"""Backward-compatibility wrapper — re-exports AddResourceScreen as AddAppScreen."""

from homepilot.screens.add_resource import AddResourceScreen as AddAppScreen

__all__ = ["AddAppScreen"]
