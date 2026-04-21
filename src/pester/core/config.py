"""Load and manage pester.yaml vault configuration."""

from __future__ import annotations

import copy
import logging
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)


class ConfigError(ValueError):
    """User-facing error in pester.yaml. CLI converts to a clean error message."""


# Directories to skip during vault indexing/walking (shared by chunker + indexer)
EXCLUDE_DIRS: set[str] = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    "node_modules",
    ".claude",
    ".pester",
    ".vault-index",
}

# Files to skip during indexing (relative paths from vault root)
SKIP_FILES: set[str] = {"_system/INDEX.md"}


DEFAULT_CONFIG: dict = {
    "vault": {
        "name": "My Vault",
        "language": "en",
        "owner": "",
    },
    "extraction": {
        "keywords": {
            "en": ["TODO", "action item", "deadline", "assigned to", "due by", "follow up"],
            "ru": ["нужно", "сделать", "дедлайн", "задача", "до"],
        },
        "patterns": [],
    },
    "priorities": [],
    "alerts": {
        "burn_rate_warning": 500000,
        "runway_warning": 90,
    },
    "search": {
        "model": "intfloat/multilingual-e5-base",
        "transcript_score_factor": 0.85,
        "table_full_files": [],
        "provider": "e5",
        "ollama_url": "http://localhost:11434",
        "chunk_size": 2500,
    },
    "health": {
        "journal_stale_days": 3,
        "decision_review_days": 60,
    },
    # ── Daemon + automation defaults ────────────────────────────────
    "daemon": {
        "pid_file": True,
    },
    "watcher": {
        "enabled": True,
        "debounce_seconds": 2,
        "auto_extract": {
            "enabled": True,
            "directories": ["meetings"],
            "auto_create_confidence": 0.95,
        },
        "auto_index": {
            "enabled": True,
        },
    },
    "scheduler": {
        "timezone": None,
        "morning_briefing": {
            "enabled": False,
            "time": "08:00",
        },
        "weekly_digest": {
            "enabled": False,
            "day_of_week": "friday",
            "time": "17:00",
        },
        "auto_commit": {
            "enabled": False,
            "interval_minutes": 30,
        },
        "scheduled_prompts": {},
    },
    "escalation": {
        "enabled": False,
        "rules": [],
        "default_threshold_days": 3,
        "todoist": {
            "enabled": False,
            "api_key_env": "TODOIST_API_TOKEN",
        },
    },
    "llm": {
        "enabled": False,
        "provider": "openai",
        "model": "gpt-5.4-mini",
        "api_key_env": "OPENAI_API_KEY",
        "temperature": 0.3,
        "max_tokens": 2048,
        "timeout_seconds": 30,
    },
    "bot": {
        "enabled": False,
        "provider": "openai",
        "name": "pester",
        "persona": "",
        "model": "gpt-5.4-mini",
        "api_key_env": "OPENAI_API_KEY",
        "groq_api_key_env": "GROQ_API_KEY",
        "temperature": 0.7,
        "max_tokens": 4096,
        "max_history": 20,
        "timeout_seconds": 30,
        "allowed_users": [],
        "default_mode": "auto",
    },
    "sync": {
        "drive": {"enabled": False, "folders": []},
        "telegram": {"enabled": False, "chats": []},
    },
    "notifications": {
        "telegram": {
            "enabled": False,
            "bot_token_env": "TELEGRAM_BOT_TOKEN",
            "chat_id": None,
        },
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into base, returning a new dict."""
    result = copy.deepcopy(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config(vault_path: Path) -> dict:
    """Load pester.yaml from vault root, merged with defaults.

    Returns DEFAULT_CONFIG if pester.yaml is missing.
    Raises ValueError on malformed YAML.
    """
    config_path = vault_path / "pester.yaml"
    if not config_path.is_file():
        return copy.deepcopy(DEFAULT_CONFIG)

    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ConfigError(f"Malformed pester.yaml: {e}") from e

    if not isinstance(raw, dict):
        # Empty file or non-dict YAML (e.g., just a string)
        return copy.deepcopy(DEFAULT_CONFIG)

    return _deep_merge(DEFAULT_CONFIG, raw)


def validate_config(config: dict) -> list[str]:
    """Validate config values and report warnings.

    Returns a list of warning strings. Empty list means valid config.
    """
    warnings: list[str] = []

    def _check_positive_int(path: str, value: object) -> None:
        if not isinstance(value, (int, float)) or value <= 0:
            warnings.append(f"{path}: expected positive number, got {value!r}")

    def _check_positive_number(path: str, value: object) -> None:
        if not isinstance(value, (int, float)) or value <= 0:
            warnings.append(f"{path}: expected positive number, got {value!r}")

    def _check_non_empty_string(path: str, value: object) -> None:
        if not isinstance(value, str) or not value.strip():
            warnings.append(f"{path}: expected non-empty string, got {value!r}")

    def _check_bool(path: str, value: object) -> None:
        if not isinstance(value, bool):
            warnings.append(f"{path}: expected boolean, got {value!r}")

    # Keys whose values are user-defined dicts (arbitrary sub-keys allowed)
    _FREEFORM_KEYS = {".scheduler.scheduled_prompts"}

    def _check_unknown_keys(path: str, user: dict, reference: dict) -> None:
        for key in user:
            full = f"{path}.{key}"
            if full in _FREEFORM_KEYS:
                continue  # user-defined entries, skip deep check
            if key not in reference:
                warnings.append(f"{full}: unknown key")
            elif isinstance(user[key], dict) and isinstance(reference.get(key), dict):
                _check_unknown_keys(full, user[key], reference[key])

    # Check unknown keys at all nesting levels
    _check_unknown_keys("", config, DEFAULT_CONFIG)

    # Health checks
    v = get_config_value(config, "health.journal_stale_days")
    if v is not None:
        _check_positive_int("health.journal_stale_days", v)
    v = get_config_value(config, "health.decision_review_days")
    if v is not None:
        _check_positive_int("health.decision_review_days", v)

    # Search checks
    v = get_config_value(config, "search.model")
    if v is not None:
        _check_non_empty_string("search.model", v)

    # Watcher checks
    v = get_config_value(config, "watcher.debounce_seconds")
    if v is not None:
        _check_positive_number("watcher.debounce_seconds", v)
    v = get_config_value(config, "watcher.enabled")
    if v is not None:
        _check_bool("watcher.enabled", v)

    # Scheduler checks
    tz = get_config_value(config, "scheduler.timezone")
    if tz is not None:
        try:
            from zoneinfo import ZoneInfo

            ZoneInfo(tz)
        except (KeyError, Exception):
            warnings.append(f"scheduler.timezone: invalid IANA zone {tz!r}")
    v = get_config_value(config, "scheduler.auto_commit.interval_minutes")
    if v is not None:
        _check_positive_int("scheduler.auto_commit.interval_minutes", v)

    # Escalation checks
    v = get_config_value(config, "escalation.default_threshold_days")
    if v is not None:
        _check_positive_int("escalation.default_threshold_days", v)

    # LLM checks
    v = get_config_value(config, "llm.temperature")
    if v is not None and isinstance(v, (int, float)):
        if v < 0 or v > 2:
            warnings.append(f"llm.temperature: expected 0-2, got {v}")
    v = get_config_value(config, "llm.max_tokens")
    if v is not None:
        _check_positive_int("llm.max_tokens", v)
    v = get_config_value(config, "llm.timeout_seconds")
    if v is not None:
        _check_positive_int("llm.timeout_seconds", v)

    # Bot checks
    v = get_config_value(config, "bot.allowed_users")
    if v is not None and not isinstance(v, list):
        warnings.append(f"bot.allowed_users: expected list, got {type(v).__name__}")
    v = get_config_value(config, "bot.max_history")
    if v is not None:
        _check_positive_int("bot.max_history", v)
    v = get_config_value(config, "bot.temperature")
    if v is not None and isinstance(v, (int, float)):
        if v < 0 or v > 2:
            warnings.append(f"bot.temperature: expected 0-2, got {v}")
    v = get_config_value(config, "bot.max_tokens")
    if v is not None:
        _check_positive_int("bot.max_tokens", v)

    # Log warnings
    for w in warnings:
        logger.warning("Config: %s", w)

    return warnings


def get_config_value(config: dict, dotted_key: str, default=None):
    """Safe nested access: get_config_value(cfg, 'health.journal_stale_days', 3)."""
    keys = dotted_key.split(".")
    current = config
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            return default
        current = current[key]
    return current
