"""Skill alias normalization for JobPilot.

Loads a YAML alias map and provides normalize_skill() to map variant names
to canonical names. Also tracks unmatched skills for continuous improvement.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

_ALIAS_FILE = Path(__file__).parent / "skill_aliases.yaml"

# Module-level cache
_alias_map: dict[str, str] | None = None
_unmatched: set[str] = set()


def _load_aliases(alias_path: Path | None = None) -> dict[str, str]:
    """Load the alias YAML and return a lowercase-key → canonical-name dict."""
    path = alias_path or _ALIAS_FILE
    if not path.exists():
        logger.warning("Skill alias file not found: %s", path)
        return {}

    with open(path) as f:
        raw: dict[str, Any] = yaml.safe_load(f) or {}

    aliases: dict[str, str] = {}
    for key, canonical in raw.get("aliases", {}).items():
        aliases[str(key).strip().lower()] = str(canonical)
    return aliases


def get_alias_map(alias_path: Path | None = None) -> dict[str, str]:
    """Return the alias map, loading it once and caching."""
    global _alias_map  # noqa: PLW0603
    if _alias_map is None:
        _alias_map = _load_aliases(alias_path)
    return _alias_map


def reset_cache() -> None:
    """Clear the cached alias map and unmatched set (useful for testing)."""
    global _alias_map  # noqa: PLW0603
    _alias_map = None
    _unmatched.clear()


def normalize_skill(skill: str, alias_path: Path | None = None) -> str:
    """Normalize a skill name to its canonical form.

    If the skill (lowercased, stripped) is in the alias map, return the
    canonical name. Otherwise, return the original string and log it as
    unmatched.
    """
    alias_map = get_alias_map(alias_path)
    key = skill.strip().lower()

    if key in alias_map:
        return alias_map[key]

    # Not in alias map — track and return original
    if key and key not in _unmatched:
        _unmatched.add(key)
        logger.debug("Unmatched skill (not in alias map): %r", skill)

    return skill


def get_unmatched_skills() -> list[str]:
    """Return sorted list of skill names seen but not found in the alias map."""
    return sorted(_unmatched)


def clear_unmatched() -> None:
    """Reset the unmatched skill tracker."""
    _unmatched.clear()
