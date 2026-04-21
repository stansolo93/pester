"""Translation parity tests for vault coaching prompts.

Catches two classes of regression at CI time:

1. **File set drift** — a prompt added to one locale but not the other (e.g.
   ``en/morning_focus.md`` exists but ``ru/morning_focus.md`` is missing).
2. **Template variable drift** — a translation accidentally drops or renames
   a placeholder (e.g. RU keeps ``{must_tasks}`` but EN renames it to
   ``{musts}``), which would surface as a silent KeyError fallback at
   runtime via ``coaching/runner.py``'s ``_SafeDict``.
"""

from __future__ import annotations

import re
from pathlib import Path

PROMPTS_ROOT = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "pester"
    / "templates"
    / "vault"
    / "_system"
    / "prompts"
)

LOCALES = ("en", "ru")

_VAR_RE = re.compile(r"\{([a-z_][a-z0-9_]*)\}")


def _files_for(locale: str) -> set[str]:
    locale_dir = PROMPTS_ROOT / locale
    return {p.name for p in locale_dir.glob("*.md")}


def _vars_in(path: Path) -> set[str]:
    return set(_VAR_RE.findall(path.read_text(encoding="utf-8")))


def test_locale_subdirs_exist():
    for locale in LOCALES:
        assert (PROMPTS_ROOT / locale).is_dir(), f"Missing locale subdir: _system/prompts/{locale}/"


def test_file_sets_match_across_locales():
    en_files = _files_for("en")
    ru_files = _files_for("ru")
    assert en_files, "Expected EN prompts; found none"
    only_en = en_files - ru_files
    only_ru = ru_files - en_files
    assert not only_en, f"Prompts present in en/ but missing from ru/: {sorted(only_en)}"
    assert not only_ru, f"Prompts present in ru/ but missing from en/: {sorted(only_ru)}"


def test_template_variables_match_per_file():
    en_files = _files_for("en")
    mismatches: list[str] = []
    for name in sorted(en_files):
        en_vars = _vars_in(PROMPTS_ROOT / "en" / name)
        ru_vars = _vars_in(PROMPTS_ROOT / "ru" / name)
        if en_vars != ru_vars:
            mismatches.append(
                f"{name}: en={sorted(en_vars)} ru={sorted(ru_vars)} "
                f"diff={sorted(en_vars ^ ru_vars)}"
            )
    assert not mismatches, "Template variable drift between locales:\n  " + "\n  ".join(mismatches)


def test_required_prompts_exist():
    """The 12 prompts the bot and scheduler load must exist in EN."""
    expected = {
        "copilot.md",
        "provocateur.md",
        "morning_focus.md",
        "evening_review.md",
        "daily_reflection.md",
        "daily_context.md",
        "weekly_analysis.md",
        "weekend_morning.md",
        "weekend_evening.md",
        "weekend_planning.md",
        "monthly_review.md",
        "quarterly_strategy.md",
    }
    missing = expected - _files_for("en")
    assert not missing, f"Missing EN prompts: {sorted(missing)}"
