"""Tests for core.extras — optional dependency check factory."""

from unittest.mock import patch

import pytest

from pester.core.extras import make_optional_check


def _import_side_effect(available: set[str]):
    """Return a side_effect for importlib.import_module that only 'finds' packages in *available*."""

    def _side_effect(name: str):
        if name in available:
            return None  # simulate successful import
        raise ImportError(f"No module named '{name}'")

    return _side_effect


class TestMakeOptionalCheck:
    """Tests for make_optional_check."""

    @patch("pester.core.extras.importlib.import_module")
    def test_single_package_present(self, mock_import):
        mock_import.side_effect = _import_side_effect({"somepkg"})
        has, require_fn = make_optional_check("somepkg", "extra")
        assert has is True
        assert callable(require_fn)

    @patch("pester.core.extras.importlib.import_module")
    def test_single_package_missing(self, mock_import):
        mock_import.side_effect = _import_side_effect(set())
        has, require_fn = make_optional_check("somepkg", "extra")
        assert has is False
        assert callable(require_fn)

    @patch("pester.core.extras.importlib.import_module")
    def test_require_raises_systemexit(self, mock_import):
        mock_import.side_effect = _import_side_effect(set())
        _, require_fn = make_optional_check("somepkg", "search")
        with pytest.raises(SystemExit, match="Search requires: pip install pester\\[search\\]"):
            require_fn()

    @patch("pester.core.extras.importlib.import_module")
    def test_require_message_custom_label(self, mock_import):
        mock_import.side_effect = _import_side_effect(set())
        _, require_fn = make_optional_check("somepkg", "mcp", label="MCP server")
        with pytest.raises(SystemExit, match="MCP server requires: pip install pester\\[mcp\\]"):
            require_fn()

    @patch("pester.core.extras.importlib.import_module")
    def test_multi_package_all_present(self, mock_import):
        mock_import.side_effect = _import_side_effect({"pkg_a", "pkg_b"})
        has, require_fn = make_optional_check(["pkg_a", "pkg_b"], "multi")
        assert has is True

    @patch("pester.core.extras.importlib.import_module")
    def test_multi_package_first_missing(self, mock_import):
        mock_import.side_effect = _import_side_effect({"pkg_b"})
        has, require_fn = make_optional_check(["pkg_a", "pkg_b"], "multi")
        assert has is False

    @patch("pester.core.extras.importlib.import_module")
    def test_multi_package_second_missing(self, mock_import):
        mock_import.side_effect = _import_side_effect({"pkg_a"})
        has, require_fn = make_optional_check(["pkg_a", "pkg_b"], "multi")
        assert has is False

    @patch("pester.core.extras.importlib.import_module")
    def test_present_require_fn_noop(self, mock_import):
        mock_import.side_effect = _import_side_effect({"somepkg"})
        _, require_fn = make_optional_check("somepkg", "extra")
        # Should not raise
        require_fn()
