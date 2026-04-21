"""ScheduledPrompt dataclass — defines a coaching prompt that runs on a schedule."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


@dataclass
class ScheduledPrompt:
    """Definition of a coaching prompt that fires on a schedule.

    Attributes:
        name:  Identifier, e.g. "morning_focus".
        schedule:  Time string for ``schedule`` library, e.g. "09:00".
        prompt_path:  Relative to vault root, e.g. "_system/prompts/morning_focus.md".
        data_fn:  Callable(vault_path, config) -> dict of template variables.
        mode:  "copilot" or "provocateur".
        fallback_template:  Used when prompt_path file is missing.
        days:  Day-of-week filter, e.g. ["monday", "tuesday", ...].
                Empty list means every day.
    """

    name: str
    schedule: str
    prompt_path: str
    data_fn: Callable[[Path, dict[str, Any]], dict[str, str]]
    mode: str = "copilot"
    fallback_template: str = ""
    days: list[str] = field(default_factory=list)
