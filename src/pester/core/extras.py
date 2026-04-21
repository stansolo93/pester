"""Factory for optional-extra availability checks."""

from __future__ import annotations

import importlib
from typing import Callable


def make_optional_check(
    package_name: str | list[str],
    extra_name: str,
    *,
    label: str | None = None,
) -> tuple[bool, Callable[[], None]]:
    """Return (has_package, require_fn) for an optional extra.

    If any package in *package_name* is missing, has_package is False
    and require_fn() raises SystemExit with an install hint.
    """
    packages = [package_name] if isinstance(package_name, str) else package_name
    display = label or extra_name.capitalize()

    for pkg in packages:
        try:
            importlib.import_module(pkg)
        except ImportError:

            def _require(_display=display, _extra=extra_name) -> None:
                raise SystemExit(f"{_display} requires: pip install pester[{_extra}]")

            return False, _require

    return True, lambda: None
