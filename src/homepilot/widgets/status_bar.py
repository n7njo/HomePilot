"""Bottom status bar widget for HomePilot."""

from __future__ import annotations

from datetime import datetime

from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Static


class StatusBar(Static):
    """Displays server connection state and current time in the footer area."""

    server_host: reactive[str] = reactive("—")
    ssh_connected: reactive[bool] = reactive(False)

    def render(self) -> str:
        conn = "● Connected" if self.ssh_connected else "○ Disconnected"
        now = datetime.now().strftime("%H:%M:%S")
        return f" 🖥  {self.server_host}  │  {conn}  │  {now}"
