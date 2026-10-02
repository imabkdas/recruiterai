"""Tests for llm.redact — verifying removal of PII and personal contact details."""

from __future__ import annotations

import pytest

from jobpilot.llm.redact import (
    redact_address,
    redact_email,
    redact_phone,
    redact_text,
    redact_url,
)


class TestRedactEmail:
    @pytest.mark.parametrize(
        ("input_text", "expected"),
        [
            ("Contact me at john.doe@example.com for info.", "Contact me at [REDACTED_EMAIL] for info."),
            ("My emails are a@b.co and dev+alerts@test.org.", "My emails are [REDACTED_EMAIL] and [REDACTED_EMAIL]."),
            ("No email here in this sentence.", "No email here in this sentence."),
            ("", ""),
        ],
    )
    def test_email_redaction(self, input_text: str, expected: str) -> None:
        assert redact_email(input_text) == expected


class TestRedactPhone:
    @pytest.mark.parametrize(
        ("input_text", "expected"),
        [
            ("Reach me at +1-555-123-4567 today.", "Reach me at [REDACTED_PHONE] today."),
            ("Mobile: (555) 123-4567 or 555.987.6543.", "Mobile: [REDACTED_PHONE] or [REDACTED_PHONE]."),
            ("India phone: +91 98765 43210 please.", "India phone: [REDACTED_PHONE] please."),
            ("Call me at 9876543210 immediately.", "Call me at [REDACTED_PHONE] immediately."),
            ("Phone: +44 20 7946 0991", "Phone: [REDACTED_PHONE]"),
        ],
    )
    def test_phone_redaction(self, input_text: str, expected: str) -> None:
        assert redact_phone(input_text) == expected

    @pytest.mark.parametrize(
        "safe_text",
        [
            "Candidate has 4 years of experience.",
            "Salary expectation is $120,000 per year.",
            "Completed project in 2024 using Java 17.",
            "Runs on port 8080 with 500 connections.",
        ],
    )
    def test_non_phone_numbers_preserved(self, safe_text: str) -> None:
        assert redact_phone(safe_text) == safe_text


class TestRedactUrl:
    @pytest.mark.parametrize(
        ("input_text", "expected"),
        [
            ("Profile at https://www.linkedin.com/in/anand-dev/ here.", "Profile at [REDACTED_URL] here."),
            ("Code at https://github.com/anandkumar and review.", "Code at [REDACTED_URL] and review."),
            ("Follow me https://x.com/anand_codes on Twitter.", "Follow me [REDACTED_URL] on Twitter."),
            ("My site is https://anand.github.io/.", "My site is [REDACTED_URL]."),
        ],
    )
    def test_personal_url_redaction(self, input_text: str, expected: str) -> None:
        assert redact_url(input_text) == expected

    def test_general_urls_preserved(self) -> None:
        text = "Check the docs at https://spring.io/projects/spring-boot and https://github.com/features"
        assert redact_url(text) == text


class TestRedactAddress:
    @pytest.mark.parametrize(
        ("input_text", "expected"),
        [
            ("Living at 123 Main Street, Apt 4B, City.", "Living at [REDACTED_ADDRESS], City."),
            ("Office at 456 Elm Ave, Suite 200.", "Office at [REDACTED_ADDRESS]."),
            ("Address: Flat 201, Green Garden Road, Bangalore.", "Address: [REDACTED_ADDRESS], Bangalore."),
            ("Please send to PIN: 560001.", "Please send to [REDACTED_ADDRESS]."),
            ("Zip Code: 94107", "[REDACTED_ADDRESS]"),
        ],
    )
    def test_address_redaction(self, input_text: str, expected: str) -> None:
        assert redact_address(input_text) == expected


class TestRedactTextCombined:
    def test_all_identifiers_redacted(self) -> None:
        text = (
            "Name: Anand\n"
            "Email: anand@example.com\n"
            "Phone: +91 98765 43210\n"
            "Address: 123 Tech Park Road, Apt 10\n"
            "LinkedIn: https://linkedin.com/in/anand-dev\n"
            "Skills: Java, Spring Boot, Microservices, 4 years experience."
        )
        redacted = redact_text(text)
        assert "anand@example.com" not in redacted
        assert "98765" not in redacted
        assert "123 Tech Park Road" not in redacted
        assert "linkedin.com/in/anand-dev" not in redacted

        assert "[REDACTED_EMAIL]" in redacted
        assert "[REDACTED_PHONE]" in redacted
        assert "[REDACTED_ADDRESS]" in redacted
        assert "[REDACTED_URL]" in redacted
        assert "Java, Spring Boot, Microservices, 4 years experience." in redacted
