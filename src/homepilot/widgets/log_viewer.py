"""Scrollable log viewer widget."""

from __future__ import annotations

from textual.widgets import RichLog


class LogViewer(RichLog):
    """A scrollable log viewer that auto-scrolls to the bottom."""

    DEFAULT_CSS = """
    LogViewer {
        height: 1fr;
        border: solid $surface-lighten-2;
        background: $surface;
        padding: 0 1;
    }
    """

    def append_line(self, line: str) -> None:
        """Add a line and scroll to the bottom."""
        self.write(line)
