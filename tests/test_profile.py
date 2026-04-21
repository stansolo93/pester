"""Tests for pester.tracking.profile — user profile loading."""

from pathlib import Path

from pester.tracking.profile import load_profile


class TestLoadProfile:
    def test_reads_frontmatter(self, tmp_path: Path):
        profile_path = tmp_path / "_system" / "profile.md"
        profile_path.parent.mkdir(parents=True)
        profile_path.write_text(
            "---\nname: Stan\nrole: CTO\nvalues:\n  - speed\n  - quality\n---\nBio here.\n"
        )

        profile = load_profile(tmp_path, "_system/profile.md")
        assert profile["name"] == "Stan"
        assert profile["role"] == "CTO"
        assert "speed" in profile["values"]

    def test_missing_file_returns_empty(self, tmp_path: Path):
        profile = load_profile(tmp_path, "_system/profile.md")
        assert profile == {}

    def test_bad_yaml_returns_empty(self, tmp_path: Path):
        profile_path = tmp_path / "_system" / "profile.md"
        profile_path.parent.mkdir(parents=True)
        profile_path.write_text("---\n: invalid: yaml:\n---\n")

        profile = load_profile(tmp_path, "_system/profile.md")
        assert profile == {}
