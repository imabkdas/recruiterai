"""Tests for llm.client — mocked LLM interactions, repair retries, backoff, and daily cap."""

from __future__ import annotations

import sqlite3
from typing import Any
from unittest.mock import MagicMock

import pytest
from pydantic import BaseModel

from jobpilot.config import AppConfig, EnvSettings, LLMConfig
from jobpilot.db import get_connection, init_schema
from jobpilot.llm.client import (
    DailyCallCapExceededError,
    GeminiClient,
    LLMError,
    LLMQuotaExhaustedError,
    LLMRateLimitError,
    LLMValidationError,
)


class DummySchema(BaseModel):
    name: str
    count: int


@pytest.fixture
def conn(tmp_path) -> sqlite3.Connection:
    db_path = tmp_path / "test.db"
    c = get_connection(db_path)
    init_schema(c)
    return c


@pytest.fixture
def base_config() -> AppConfig:
    return AppConfig(
        llm=LLMConfig(
            model_extract="gemini-2.0-flash",
            model_write="gemini-2.0-flash",
            daily_call_cap=5,
            max_retries=2,
        )
    )


@pytest.fixture
def base_env() -> EnvSettings:
    return EnvSettings(gemini_api_key="mock-key")


def make_mock_response(text: str, in_tok: int = 10, out_tok: int = 20) -> Any:
    resp = MagicMock()
    resp.text = text
    resp.usage_metadata.prompt_token_count = in_tok
    resp.usage_metadata.candidates_token_count = out_tok
    return resp


class TestGenerateJson:
    def test_successful_validation(self, conn: sqlite3.Connection, base_config: AppConfig, base_env: EnvSettings) -> None:
        mock_genai = MagicMock()
        mock_genai.models.generate_content.return_value = make_mock_response(
            '{"name": "Backend Role", "count": 3}', in_tok=50, out_tok=25
        )

        client = GeminiClient(config=base_config, env=base_env, conn=conn, genai_client=mock_genai)
        result = client.generate_json("extract this", DummySchema)

        assert isinstance(result, DummySchema)
        assert result.name == "Backend Role"
        assert result.count == 3
        assert mock_genai.models.generate_content.call_count == 1

        # Check DB usage entry
        row = conn.execute("SELECT * FROM llm_usage WHERE task = 'extract_job'").fetchone()
        assert row is not None
        assert row["input_tokens"] == 50
        assert row["output_tokens"] == 25
        assert row["cached"] == 0

    def test_repair_retry_succeeds(self, conn: sqlite3.Connection, base_config: AppConfig, base_env: EnvSettings) -> None:
        mock_genai = MagicMock()
        # 1st call: malformed JSON; 2nd call: valid JSON
        mock_genai.models.generate_content.side_effect = [
            make_mock_response("not valid json"),
            make_mock_response('{"name": "Repaired Role", "count": 7}'),
        ]

        client = GeminiClient(config=base_config, env=base_env, conn=conn, genai_client=mock_genai)
        result = client.generate_json("extract this", DummySchema)

        assert result.name == "Repaired Role"
        assert result.count == 7
        assert mock_genai.models.generate_content.call_count == 2

        # Check that repair call usage was recorded
        repair_row = conn.execute("SELECT * FROM llm_usage WHERE task = 'extract_job_repair'").fetchone()
        assert repair_row is not None

    def test_repair_retry_fails_cleanly(self, conn: sqlite3.Connection, base_config: AppConfig, base_env: EnvSettings) -> None:
        mock_genai = MagicMock()
        # Both calls return invalid json
        mock_genai.models.generate_content.side_effect = [
            make_mock_response("bad json 1"),
            make_mock_response("bad json 2"),
        ]

        client = GeminiClient(config=base_config, env=base_env, conn=conn, genai_client=mock_genai)
        with pytest.raises(LLMValidationError, match="JSON validation failed after repair retry"):
            client.generate_json("extract this", DummySchema)

        assert mock_genai.models.generate_content.call_count == 2


class TestRateLimitBackoff:
    def test_retry_on_rate_limit_then_success(self, conn: sqlite3.Connection, base_config: AppConfig, base_env: EnvSettings) -> None:
        mock_genai = MagicMock()
        rate_err = Exception("429 RESOURCE_EXHAUSTED: Rate limit exceeded")
        success_resp = make_mock_response("All good text")

        mock_genai.models.generate_content.side_effect = [rate_err, rate_err, success_resp]

        sleep_calls: list[float] = []
        client = GeminiClient(
            config=base_config,
            env=base_env,
            conn=conn,
            genai_client=mock_genai,
            sleep_fn=sleep_calls.append,
        )

        res = client.generate_text("Say hi")
        assert res == "All good text"
        assert len(sleep_calls) == 2
        assert sleep_calls == [1.0, 2.0]  # 2^0, 2^1

    def test_rate_limit_exhausted(self, conn: sqlite3.Connection, base_config: AppConfig, base_env: EnvSettings) -> None:
        mock_genai = MagicMock()
        rate_err = Exception("429 RESOURCE_EXHAUSTED")
        mock_genai.models.generate_content.side_effect = rate_err

        sleep_calls: list[float] = []
        client = GeminiClient(
            config=base_config,
            env=base_env,
            conn=conn,
            genai_client=mock_genai,
            sleep_fn=sleep_calls.append,
        )

        with pytest.raises(LLMRateLimitError, match="Rate limit exceeded after"):
            client.generate_text("Say hi")

    def test_rate_limit_uses_google_retry_delay(self, conn: sqlite3.Connection, base_config: AppConfig, base_env: EnvSettings) -> None:
        mock_genai = MagicMock()
        err_msg = "429 RESOURCE_EXHAUSTED. Please retry in 45.5s."
        mock_genai.models.generate_content.side_effect = [
            Exception(err_msg),
            make_mock_response("Success after waiting quota"),
        ]

        sleep_calls: list[float] = []
        client = GeminiClient(
            config=base_config,
            env=base_env,
            conn=conn,
            genai_client=mock_genai,
            sleep_fn=sleep_calls.append,
        )

        res = client.generate_text("Say hi")
        assert res == "Success after waiting quota"
        assert len(sleep_calls) == 1
        assert sleep_calls[0] == 46.5  # 45.5 + 1.0 buffer

    def test_suggested_delay_within_cap_is_honoured(
        self, conn: sqlite3.Connection, base_config: AppConfig, base_env: EnvSettings
    ) -> None:
        base_config.llm.max_backoff_seconds = 30.0
        mock_genai = MagicMock()
        mock_genai.models.generate_content.side_effect = [
            Exception("429 RESOURCE_EXHAUSTED. Please retry in 20s."),
            make_mock_response("Recovered"),
        ]

        sleep_calls: list[float] = []
        client = GeminiClient(
            config=base_config,
            env=base_env,
            conn=conn,
            genai_client=mock_genai,
            sleep_fn=sleep_calls.append,
        )

        assert client.generate_text("Say hi") == "Recovered"
        assert sleep_calls == [21.0]  # 20 + 1.0 buffer, under the cap

    def test_exponential_backoff_is_clamped_to_cap(
        self, conn: sqlite3.Connection, base_config: AppConfig, base_env: EnvSettings
    ) -> None:
        """Without a suggested delay, 2**attempt growth stops at max_backoff_seconds."""
        base_config.llm.max_backoff_seconds = 1.5
        mock_genai = MagicMock()
        mock_genai.models.generate_content.side_effect = [
            Exception("429 RESOURCE_EXHAUSTED"),  # no retryDelay to parse
            Exception("429 RESOURCE_EXHAUSTED"),
            make_mock_response("Recovered"),
        ]

        sleep_calls: list[float] = []
        client = GeminiClient(
            config=base_config,
            env=base_env,
            conn=conn,
            genai_client=mock_genai,
            sleep_fn=sleep_calls.append,
        )

        assert client.generate_text("Say hi") == "Recovered"
        assert sleep_calls == [1.0, 1.5]  # 2**0, then 2**1 clamped

    def test_per_day_quota_aborts_without_sleeping(
        self, conn: sqlite3.Connection, base_config: AppConfig, base_env: EnvSettings
    ) -> None:
        """A multi-hour retryDelay must abort immediately, not stall the run."""
        mock_genai = MagicMock()
        mock_genai.models.generate_content.side_effect = Exception(
            "429 RESOURCE_EXHAUSTED. Quota exceeded for metric: "
            "generate_content_free_tier_requests, limit: 20. "
            "Please retry in 16h45s. 'retryDelay': '57645s'"
        )

        sleep_calls: list[float] = []
        client = GeminiClient(
            config=base_config,
            env=base_env,
            conn=conn,
            genai_client=mock_genai,
            sleep_fn=sleep_calls.append,
        )

        with pytest.raises(LLMQuotaExhaustedError, match="quota exhausted"):
            client.generate_text("Say hi")

        assert sleep_calls == []
        assert mock_genai.models.generate_content.call_count == 1


class TestRequestThrottle:
    def test_consecutive_calls_are_spaced(
        self, conn: sqlite3.Connection, base_config: AppConfig, base_env: EnvSettings
    ) -> None:
        """min_request_interval_seconds paces calls to stay under per-minute limits."""
        base_config.llm.min_request_interval_seconds = 13.0
        mock_genai = MagicMock()
        mock_genai.models.generate_content.return_value = make_mock_response("ok")

        sleep_calls: list[float] = []
        client = GeminiClient(
            config=base_config,
            env=base_env,
            conn=conn,
            genai_client=mock_genai,
            sleep_fn=sleep_calls.append,
        )

        client.generate_text("first")
        assert sleep_calls == []  # nothing to wait for on the first call

        client.generate_text("second")
        assert len(sleep_calls) == 1
        assert 0 < sleep_calls[0] <= 13.0


class TestDailyCallCap:
    def test_cap_blocks_call(self, conn: sqlite3.Connection, base_config: AppConfig, base_env: EnvSettings) -> None:
        mock_genai = MagicMock()
        client = GeminiClient(config=base_config, env=base_env, conn=conn, genai_client=mock_genai)

        # Seed 5 calls into llm_usage today
        for _ in range(5):
            conn.execute(
                "INSERT INTO llm_usage (task, model, input_tokens, output_tokens, cached, created_at) "
                "VALUES ('test', 'gemini', 10, 10, 0, datetime('now'))"
            )
        conn.commit()

        assert client.is_cap_reached() is True

        with pytest.raises(DailyCallCapExceededError, match="Daily LLM call cap reached"):
            client.generate_text("Hello")

        # Zero API calls made
        assert mock_genai.models.generate_content.call_count == 0


class TestMissingKey:
    def test_missing_api_key_raises(self, base_config: AppConfig) -> None:
        empty_env = EnvSettings(gemini_api_key="")
        client = GeminiClient(config=base_config, env=empty_env)
        with pytest.raises(LLMError, match="GEMINI_API_KEY is not configured"):
            client.generate_text("Hello")
