"""Mode system — copilot (directive) vs provocateur (reflective) coaching modes."""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_lock = threading.Lock()


def get_mode(config: dict, user_override: str | None = None, timezone: str | None = None) -> str:
    """Determine coaching mode: 'copilot' or 'provocateur'.

    Priority:
    1. Explicit user override (/copilot, /coach commands)
    2. Config ``bot.default_mode`` if not "auto"
    3. Auto: weekday 08-18 → copilot, else → provocateur
    """
    if user_override:
        return user_override

    default_mode = config.get("bot", {}).get("default_mode", "auto")
    if default_mode != "auto":
        return default_mode

    # Auto-detect from time of day and day of week
    tz = None
    if timezone:
        try:
            from zoneinfo import ZoneInfo

            tz = ZoneInfo(timezone)
        except (ImportError, KeyError):
            pass

    now = datetime.now(tz)
    # Weekend → provocateur
    if now.weekday() >= 5:
        return "provocateur"
    # Evening (after 18:00) or early morning (before 08:00) → provocateur
    if now.hour >= 18 or now.hour < 8:
        return "provocateur"
    return "copilot"


def load_mode_overrides(state_dir: Path) -> dict[int, str]:
    """Load per-user mode overrides from state_dir/mode_overrides.json."""
    path = state_dir / "mode_overrides.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {int(k): v["mode"] for k, v in data.items() if isinstance(v, dict)}
    except (json.JSONDecodeError, OSError, KeyError, ValueError) as e:
        logger.warning("Cannot load mode overrides: %s", e)
        return {}


def save_mode_override(state_dir: Path, user_id: int, mode: str) -> None:
    """Persist a mode override for user_id."""
    path = state_dir / "mode_overrides.json"
    with _lock:
        data: dict[str, Any] = {}
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        data[str(user_id)] = {"mode": mode, "set_at": datetime.now(timezone.utc).isoformat()}
        try:
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as e:
            logger.warning("Cannot save mode override: %s", e)


def clear_mode_override(state_dir: Path, user_id: int) -> None:
    """Remove override, returning to auto mode."""
    path = state_dir / "mode_overrides.json"
    with _lock:
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            data.pop(str(user_id), None)
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Cannot clear mode override: %s", e)


def load_language_preferences(state_dir: Path) -> dict[int, str]:
    """Load per-user language preferences from state_dir/language_preferences.json.

    Keys are user_id (int), values are language code ('ru', 'en', ...).
    """
    path = state_dir / "language_preferences.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {int(k): v["language"] for k, v in data.items() if isinstance(v, dict)}
    except (json.JSONDecodeError, OSError, KeyError, ValueError) as e:
        logger.warning("Cannot load language preferences: %s", e)
        return {}


def save_language_preference(state_dir: Path, user_id: int, language: str) -> None:
    """Persist a language preference for user_id ('ru' or 'en')."""
    path = state_dir / "language_preferences.json"
    with _lock:
        data: dict[str, Any] = {}
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        data[str(user_id)] = {
            "language": language,
            "set_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as e:
            logger.warning("Cannot save language preference: %s", e)


def load_prompt_template(vault_path: Path, prompt_path: str, lang: str | None = None) -> str | None:
    """Read a prompt template from vault. Returns None if no candidate file is found.

    With ``lang=None``, performs the legacy lookup at ``vault_path / prompt_path``.

    With ``lang`` set, tries a 3-level fallback chain:

      1. ``{parent}/{lang}/{filename}`` — locale subdir
      2. ``{parent}/en/{filename}``     — English fallback (skipped if lang == "en")
      3. ``vault_path / prompt_path``   — legacy flat layout

    Unknown languages (e.g. "de", "fr", "mixed") fall through to the English
    subdir, NOT the legacy file, so adopted Russian-content vaults don't
    accidentally surface Russian text to non-Russian users.
    """
    candidates: list[Path] = []
    p = Path(prompt_path)
    parent = p.parent
    filename = p.name

    if lang:
        candidates.append(vault_path / parent / lang / filename)
        if lang != "en":
            candidates.append(vault_path / parent / "en" / filename)
    candidates.append(vault_path / prompt_path)

    for path in candidates:
        if not path.exists():
            continue
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            continue
    return None
