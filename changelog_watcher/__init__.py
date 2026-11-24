"""Changelog Watcher – keeps track of which applications need updates."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("changelog-watcher")  # single source: pyproject.toml (PEP 440, no "v")
except PackageNotFoundError:  # running from a source tree without `pip install`
    __version__ = "0.0.0"
