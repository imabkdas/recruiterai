"""Gemini LLM client wrapper using the official google-genai SDK.

Features:
- Structured JSON output with Pydantic schema validation.
- One repair retry on JSON validation failure.
- Exponential backoff on rate limits (429 / RESOURCE_EXHAUSTED).
- Daily call cap enforcement tracked in sqlite llm_usage table.
- Usage logging (tokens, task, model, cached).
- Fully mockable for testing without live API keys or network.
"""

from __future__ import annotations

import logging
import re
import sqlite3
import time
from collections.abc import Callable
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from jobpilot.config import AppConfig, EnvSettings, load_config, load_env
from jobpilot.db import get_daily_llm_call_count, record_llm_usage

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class LLMError(Exception):
    """Base exception for LLM operations."""


class DailyCallCapExceededError(LLMError):
    """Raised when the daily LLM call quota is reached."""


class LLMRateLimitError(LLMError):
    """Raised when rate limits persist after maximum retries."""


class LLMQuotaExhaustedError(LLMError):
    """Raised when the provider quota will not recover within this run.

    Signalled by a suggested retry delay longer than llm.max_backoff_seconds
    (free-tier per-day quotas report delays of many hours). Callers should stop
    making further calls rather than retrying.
    """


class LLMValidationError(LLMError):
    """Raised when structured JSON validation fails after repair retry."""


# ---------------------------------------------------------------------------
# Gemini Client
# ---------------------------------------------------------------------------

class GeminiClient:
    """Wrapper over google-genai SDK with backoff, capping, and repair retries."""

    def __init__(
        self,
        config: AppConfig | None = None,
        env: EnvSettings | None = None,
        conn: sqlite3.Connection | None = None,
        genai_client: Any | None = None,
        sleep_fn: Callable[[float], None] | None = None,
    ) -> None:
        self.config = config or load_config()
        self.env = env or load_env()
        self.conn = conn
        self._sleep_fn = sleep_fn or time.sleep
        self._genai_client = genai_client
        self._last_call_at: float | None = None

    def _get_client(self) -> Any:
        """Lazily initialize the google-genai Client."""
        if self._genai_client is not None:
            return self._genai_client

        from google import genai

        api_key = self.env.gemini_api_key
        if not api_key:
            raise LLMError("GEMINI_API_KEY is not configured in .env")

        self._genai_client = genai.Client(api_key=api_key)
        return self._genai_client

    def is_cap_reached(self) -> bool:
        """Check if today's non-cached calls reach or exceed the daily cap."""
        if not self.conn:
            return False
        current_calls = get_daily_llm_call_count(self.conn)
        return current_calls >= self.config.llm.daily_call_cap

    def _check_daily_cap(self) -> None:
        """Raise DailyCallCapExceededError if the daily call quota has been reached."""
        if self.is_cap_reached():
            current_calls = get_daily_llm_call_count(self.conn) if self.conn else 0
            cap = self.config.llm.daily_call_cap
            raise DailyCallCapExceededError(
                f"Daily LLM call cap reached ({current_calls}/{cap} calls today)."
            )

    def _is_rate_limit_error(self, exc: Exception) -> bool:
        """Determine if an exception represents a rate limit / quota error."""
        code = getattr(exc, "code", None)
        status = getattr(exc, "status", None)
        if code in (429, 503) or status in (429, 503):
            return True

        msg = str(exc).lower()
        return any(
            phrase in msg
            for phrase in (
                "429",
                "resource_exhausted",
                "rate limit",
                "quota exceeded",
                "too many requests",
            )
        )

    def _extract_retry_delay(self, exc: Exception) -> float | None:
        """Extract suggested retry delay in seconds from Gemini rate limit error."""
        msg = str(exc)
        m = re.search(r"retryDelay['\"]?\s*:\s*['\"]?(\d+(?:\.\d+)?)s?", msg, re.IGNORECASE)
        if m:
            try:
                return float(m.group(1)) + 1.0
            except ValueError:
                pass
        m_ms = re.search(r"Please retry in (\d+(?:\.\d+)?)ms", msg, re.IGNORECASE)
        if m_ms:
            try:
                return (float(m_ms.group(1)) / 1000.0) + 0.5
            except ValueError:
                pass
        m = re.search(r"Please retry in (\d+(?:\.\d+)?)s", msg, re.IGNORECASE)
        if m:
            try:
                return float(m.group(1)) + 1.0
            except ValueError:
                pass
        return None

    def _throttle(self) -> None:
        """Space consecutive calls by llm.min_request_interval_seconds.

        Free tiers cap requests per minute, so pacing avoids the 429 storm
        entirely instead of recovering from it.
        """
        interval = self.config.llm.min_request_interval_seconds
        if interval <= 0 or self._last_call_at is None:
            return
        elapsed = time.monotonic() - self._last_call_at
        if elapsed < interval:
            self._sleep_fn(interval - elapsed)

    def _call_with_retry(
        self,
        model: str,
        contents: str,
        config: Any = None,
        max_retries: int | None = None,
    ) -> Any:
        """Execute client.models.generate_content with bounded exponential backoff on 429."""
        retries = max_retries if max_retries is not None else self.config.llm.max_retries
        max_backoff = self.config.llm.max_backoff_seconds
        client = self._get_client()

        for attempt in range(retries + 1):
            try:
                self._throttle()
                if config is not None:
                    return client.models.generate_content(
                        model=model,
                        contents=contents,
                        config=config,
                    )
                return client.models.generate_content(
                    model=model,
                    contents=contents,
                )
            except Exception as exc:
                if not self._is_rate_limit_error(exc):
                    raise

                suggested_delay = self._extract_retry_delay(exc)

                # A suggested delay beyond max_backoff means a per-day quota is
                # spent; waiting it out would stall the run for hours.
                if suggested_delay is not None and suggested_delay > max_backoff:
                    raise LLMQuotaExhaustedError(
                        f"Provider quota exhausted on model {model}; it suggests retrying in "
                        f"{suggested_delay:.0f}s, beyond the {max_backoff:.0f}s backoff limit. "
                        f"Remaining work is left for the next run: {exc}"
                    ) from exc

                if attempt >= retries:
                    raise LLMRateLimitError(
                        f"Rate limit exceeded after {retries} retries on model {model}: {exc}"
                    ) from exc

                backoff = suggested_delay if suggested_delay is not None else float(2 ** attempt)
                backoff = min(backoff, max_backoff)
                logger.warning(
                    "Rate limit encountered on model %s (attempt %d/%d). Backing off %.1fs: %s",
                    model,
                    attempt + 1,
                    retries,
                    backoff,
                    exc,
                )
                self._sleep_fn(backoff)
            finally:
                self._last_call_at = time.monotonic()

    def _extract_usage(self, response: Any) -> tuple[int, int]:
        """Extract input and output token counts from response metadata."""
        usage = getattr(response, "usage_metadata", None)
        if not usage:
            return 0, 0
        input_tokens = getattr(usage, "prompt_token_count", 0) or 0
        output_tokens = getattr(usage, "candidates_token_count", 0) or 0
        return input_tokens, output_tokens

    def generate_json(
        self,
        prompt: str,
        schema_model: type[T],
        model: str | None = None,
        task: str = "extract_job",
        system_instruction: str | None = None,
    ) -> T:
        """Generate structured JSON conforming to schema_model with one repair retry."""
        self._check_daily_cap()

        model_name = model or self.config.llm.model_extract or "gemini-3.8-flash"

        from google.genai import types

        call_config = types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=schema_model,
            system_instruction=system_instruction,
            temperature=0.0,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )

        # First attempt
        response = self._call_with_retry(model=model_name, contents=prompt, config=call_config)
        in_tok, out_tok = self._extract_usage(response)
        if self.conn:
            record_llm_usage(self.conn, task=task, model=model_name, input_tokens=in_tok, output_tokens=out_tok, cached=0)

        raw_text = getattr(response, "text", "") or ""

        try:
            return schema_model.model_validate_json(raw_text)
        except (ValidationError, ValueError) as first_err:
            first_err_msg = str(first_err)
            logger.warning("JSON validation failed for %s on first attempt: %s. Attempting repair retry...", task, first_err_msg)

        # Repair attempt (strictly one retry)
        self._check_daily_cap()
        repair_prompt = (
            f"The following JSON response failed validation with error: {first_err_msg}\n\n"
            f"Please fix the output to strictly adhere to the schema and return ONLY valid JSON.\n\n"
            f"Original prompt:\n{prompt}\n\n"
            f"Invalid response:\n{raw_text}"
        )

        repair_response = self._call_with_retry(model=model_name, contents=repair_prompt, config=call_config)
        r_in_tok, r_out_tok = self._extract_usage(repair_response)
        if self.conn:
            record_llm_usage(self.conn, task=f"{task}_repair", model=model_name, input_tokens=r_in_tok, output_tokens=r_out_tok, cached=0)

        repaired_text = getattr(repair_response, "text", "") or ""
        try:
            return schema_model.model_validate_json(repaired_text)
        except (ValidationError, ValueError) as second_err:
            logger.error("JSON validation failed after repair retry for %s: %s", task, second_err)
            raise LLMValidationError(f"JSON validation failed after repair retry: {second_err}") from second_err

    def generate_text(
        self,
        prompt: str,
        model: str | None = None,
        task: str = "general",
        system_instruction: str | None = None,
    ) -> str:
        """Generate freeform text."""
        self._check_daily_cap()

        model_name = model or self.config.llm.model_write or "gemini-3.8-flash"

        from google.genai import types

        call_config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )

        response = self._call_with_retry(model=model_name, contents=prompt, config=call_config)
        in_tok, out_tok = self._extract_usage(response)
        if self.conn:
            record_llm_usage(self.conn, task=task, model=model_name, input_tokens=in_tok, output_tokens=out_tok, cached=0)

        return getattr(response, "text", "") or ""
