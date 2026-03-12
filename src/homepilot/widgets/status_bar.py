"""Bottom status bar widget for HomePilot."""

from __future__ import annotations

from datetime import datetime

from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Static


class StatusBar(Static):
    """Displays host connection states and current time in the footer area."""

    hosts_display: reactive[str] = reactive("No hosts")

    def render(self) -> str:
        now = datetime.now().strftime("%H:%M:%S")
        return f" {self.hosts_display}  │  {now}"
