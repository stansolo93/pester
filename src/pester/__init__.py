"""pester — Accountability engine for founders."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("pester")
except PackageNotFoundError:
    # Fallback for development installs without metadata
    __version__ = "0.0.0-dev"
