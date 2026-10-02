"""Tests for jobpilot.profile.skills — alias normalization."""

from __future__ import annotations

import pytest

from jobpilot.profile.skills import (
    clear_unmatched,
    get_unmatched_skills,
    normalize_skill,
    reset_cache,
)


@pytest.fixture(autouse=True)
def _reset() -> None:
    """Reset the alias cache and unmatched tracker before each test."""
    reset_cache()
    clear_unmatched()


# ---------------------------------------------------------------------------
# Table-driven alias normalization tests
# ---------------------------------------------------------------------------

_ALIAS_CASES = [
    # (input, expected canonical)
    # Core backend
    ("spring boot", "Spring Boot"),
    ("SpringBoot", "Spring Boot"),
    ("spring-boot", "Spring Boot"),
    ("Spring Framework", "Spring Boot"),
    ("java", "Java"),
    ("JAVA", "Java"),
    ("core java", "Java"),
    ("Java EE", "Java"),
    ("j2ee", "Java"),
    ("microservice", "Microservices"),
    ("micro-services", "Microservices"),
    ("rest", "REST APIs"),
    ("restful", "REST APIs"),
    ("RESTful APIs", "REST APIs"),
    ("kafka", "Kafka"),
    ("apache kafka", "Kafka"),
    ("event-driven", "Event-driven architecture"),
    ("event driven architecture", "Event-driven architecture"),
    # Cloud
    ("k8s", "Kubernetes"),
    ("kubernetes", "Kubernetes"),
    ("docker", "Docker"),
    ("pcf", "Cloud Foundry"),
    ("cloud foundry", "Cloud Foundry"),
    ("aws", "AWS"),
    ("amazon web services", "AWS"),
    ("aws ecs", "AWS ECS"),
    ("ecs", "AWS ECS"),
    ("fargate", "AWS Fargate"),
    ("ecr", "AWS ECR"),
    # CI/CD
    ("ci/cd", "CI/CD"),
    ("cicd", "CI/CD"),
    ("continuous integration", "CI/CD"),
    ("jenkins", "Jenkins"),
    ("git", "Git"),
    ("github", "GitHub"),
    ("gitlab", "GitLab"),
    ("bitbucket", "Bitbucket"),
    # Security
    ("oauth", "OAuth 2.0"),
    ("oauth2", "OAuth 2.0"),
    ("oauth 2.0", "OAuth 2.0"),
    ("jwt", "JWT"),
    ("json web token", "JWT"),
    ("azure ad", "Azure AD"),
    ("entra id", "Azure AD"),
    ("sso", "SSO"),
    ("single sign-on", "SSO"),
    # Databases
    ("postgres", "PostgreSQL"),
    ("postgresql", "PostgreSQL"),
    ("mysql", "MySQL"),
    ("pgvector", "pgvector"),
    # Frontend
    ("react", "React"),
    ("reactjs", "React"),
    ("typescript", "TypeScript"),
    ("ts", "TypeScript"),
    ("javascript", "JavaScript"),
    ("js", "JavaScript"),
    # Monitoring
    ("new relic", "New Relic"),
    ("newrelic", "New Relic"),
    # Testing
    ("junit", "JUnit"),
    ("mockito", "Mockito"),
    # Process
    ("agile", "Agile"),
    ("scrum", "Agile"),
    ("jira", "Jira"),
    # AI
    ("openai", "OpenAI APIs"),
    ("fastapi", "FastAPI"),
    # Mobile
    ("kotlin", "Kotlin"),
    ("android", "Android"),
    # Extras
    ("redis", "Redis"),
    ("rabbitmq", "RabbitMQ"),
    ("graphql", "GraphQL"),
    ("terraform", "Terraform"),
    ("python", "Python"),
    ("golang", "Go"),
    ("azure", "Azure"),
    ("helm", "Helm"),
    ("hibernate", "Hibernate"),
    ("jpa", "JPA"),
]


@pytest.mark.parametrize("input_skill,expected", _ALIAS_CASES)
def test_alias_normalization(input_skill: str, expected: str) -> None:
    assert normalize_skill(input_skill) == expected


# ---------------------------------------------------------------------------
# Unmatched skill tracking
# ---------------------------------------------------------------------------

class TestUnmatchedTracking:
    def test_unknown_skill_tracked(self) -> None:
        result = normalize_skill("some_obscure_framework_xyz")
        assert result == "some_obscure_framework_xyz"
        assert "some_obscure_framework_xyz" in get_unmatched_skills()

    def test_known_skill_not_tracked(self) -> None:
        normalize_skill("java")
        assert "java" not in get_unmatched_skills()

    def test_clear_unmatched(self) -> None:
        normalize_skill("unknown_thing")
        assert len(get_unmatched_skills()) > 0
        clear_unmatched()
        assert len(get_unmatched_skills()) == 0


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_whitespace_stripped(self) -> None:
        assert normalize_skill("  java  ") == "Java"

    def test_case_insensitive(self) -> None:
        assert normalize_skill("KUBERNETES") == "Kubernetes"

    def test_empty_string(self) -> None:
        result = normalize_skill("")
        assert result == ""

    def test_passthrough_for_already_canonical(self) -> None:
        # "Java" lowercase → matches "java" alias → returns "Java"
        assert normalize_skill("Java") == "Java"
