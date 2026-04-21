"""Shared fixtures and pytest configuration for pester tests."""

from __future__ import annotations

import shutil
from datetime import date, timedelta
from pathlib import Path

import pytest
import yaml

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def tmp_vault(tmp_path: Path) -> Path:
    """Create a temporary vault with sample structure and files."""
    vault = tmp_path / "vault"
    vault.mkdir()

    # Copy fixtures into the temp vault
    if FIXTURES_DIR.is_dir():
        for item in FIXTURES_DIR.iterdir():
            dest = vault / item.name
            if item.is_dir():
                shutil.copytree(item, dest)
            else:
                shutil.copy2(item, dest)

    # Ensure standard dirs exist (for T3 tracking tests)
    for d in ["actions", "decisions", "journal", "meetings", "people", "projects", "reference"]:
        (vault / d).mkdir(exist_ok=True)

    return vault


@pytest.fixture
def empty_vault(tmp_path: Path) -> Path:
    """Create a vault with pester.yaml but no content files."""
    vault = tmp_path / "empty-vault"
    vault.mkdir()
    (vault / "pester.yaml").write_text(
        "vault:\n  name: Empty Test Vault\n  language: en\n",
        encoding="utf-8",
    )
    return vault


@pytest.fixture
def sample_config() -> dict:
    """Return a config dict matching DEFAULT_CONFIG structure."""
    from pester.core.config import DEFAULT_CONFIG

    return DEFAULT_CONFIG.copy()


# ── T3: Tracking + Health fixtures ───────────────────────────────────────────


@pytest.fixture
def tmp_vault_ru(tmp_vault: Path) -> Path:
    """Temporary vault configured for Russian language."""
    config = yaml.safe_load((tmp_vault / "pester.yaml").read_text())
    config["vault"]["language"] = "ru"
    (tmp_vault / "pester.yaml").write_text(yaml.dump(config, allow_unicode=True))
    return tmp_vault


@pytest.fixture
def sample_action_file(tmp_vault: Path) -> Path:
    """Create a sample open action file."""
    content = "---\nowner: stan\nstatus: open\ndue: {due}\ncreated: {created}\ncompleted: null\nsource: manual\npriority: Should\n---\n\n# Test action item\n".format(
        due=(date.today() + timedelta(days=5)).isoformat(),
        created=date.today().isoformat(),
    )
    action_path = tmp_vault / "actions" / "stan-test-action.md"
    action_path.write_text(content)
    return action_path


@pytest.fixture
def overdue_action_file(tmp_vault: Path) -> Path:
    """Create an overdue action file."""
    content = "---\nowner: jalba\nstatus: open\ndue: {due}\ncreated: {created}\ncompleted: null\nsource: meeting\npriority: Must\n---\n\n# Overdue task\n".format(
        due=(date.today() - timedelta(days=3)).isoformat(),
        created=(date.today() - timedelta(days=10)).isoformat(),
    )
    action_path = tmp_vault / "actions" / "jalba-overdue-task.md"
    action_path.write_text(content)
    return action_path


@pytest.fixture
def done_action_file(tmp_vault: Path) -> Path:
    """Create a completed action file."""
    content = "---\nowner: stan\nstatus: done\ndue: {due}\ncreated: {created}\ncompleted: {completed}\nsource: manual\npriority: Should\n---\n\n# Completed task\n".format(
        due=(date.today() - timedelta(days=1)).isoformat(),
        created=(date.today() - timedelta(days=7)).isoformat(),
        completed=date.today().isoformat(),
    )
    action_path = tmp_vault / "actions" / "stan-completed-task.md"
    action_path.write_text(content)
    return action_path


@pytest.fixture
def meeting_en(tmp_vault: Path) -> Path:
    """Create an English meeting notes file with action items."""
    src = Path(__file__).parent / "fixtures" / "meeting-en.md"
    dest = tmp_vault / "meetings" / "meeting-en.md"
    dest.write_text(src.read_text())
    return dest


@pytest.fixture
def meeting_ru(tmp_vault: Path) -> Path:
    """Create a Russian meeting notes file with action items."""
    src = Path(__file__).parent / "fixtures" / "meeting-ru.md"
    dest = tmp_vault / "meetings" / "meeting-ru.md"
    dest.write_text(src.read_text())
    return dest


@pytest.fixture
def meeting_mixed(tmp_vault: Path) -> Path:
    """Create a mixed en/ru meeting notes file."""
    src = Path(__file__).parent / "fixtures" / "meeting-mixed.md"
    dest = tmp_vault / "meetings" / "meeting-mixed.md"
    dest.write_text(src.read_text())
    return dest


@pytest.fixture
def vault_with_wikilinks(tmp_vault: Path) -> Path:
    """Create a vault with various wikilink patterns."""
    (tmp_vault / "people" / "stan.md").write_text(
        "# Stan\nCEO of [[projects/matching-v2|Matching Engine]].\nWorks with [[jalba]].\n"
    )
    (tmp_vault / "people" / "jalba.md").write_text("# Jalba Loredana\nCo-founder. See [[stan]].\n")
    (tmp_vault / "projects" / "matching-v2.md").write_text(
        "# Matching Engine v2\nOwner: [[stan]]\nReviewer: [[jalba]]\n"
    )
    (tmp_vault / "meetings" / "standup.md").write_text(
        "# Standup Notes\nDiscussed [[matching-v2]] with [[jalba-loredanna]].\n"
        "Also see [[nonexistent-doc]].\n"
    )
    return tmp_vault


@pytest.fixture
def vault_with_journals(tmp_vault: Path) -> Path:
    """Create a vault with journal entries (some with gaps)."""
    today = date.today()
    for i in range(7):
        d = today - timedelta(days=i)
        if d.weekday() >= 5:
            continue
        if i in (1, 2):
            continue
        (tmp_vault / "journal" / f"{d.isoformat()}.md").write_text(
            f"# Journal {d.isoformat()}\nNotes for the day.\n"
        )
    return tmp_vault


@pytest.fixture
def vault_with_decisions(tmp_vault: Path) -> Path:
    """Create a vault with decision files (some stale)."""
    today = date.today()
    (tmp_vault / "decisions" / "recent.md").write_text(
        "---\ncreated: {}\ntitle: Recent Decision\n---\n# Recent\n".format(
            (today - timedelta(days=10)).isoformat()
        )
    )
    (tmp_vault / "decisions" / "stale.md").write_text(
        "---\ncreated: {}\ntitle: Stale Decision\n---\n# Stale\n".format(
            (today - timedelta(days=90)).isoformat()
        )
    )
    return tmp_vault
