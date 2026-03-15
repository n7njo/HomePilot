"""Theme picker screen."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import ModalScreen
from textual.widgets import Label, ListView, ListItem


class ThemePickerScreen(ModalScreen[str | None]):
    """Modal screen for selecting a Textual theme."""

    BINDINGS = [
        Binding("escape", "dismiss(None)", "Cancel", show=True),
    ]

    CSS = """
    ThemePickerScreen {
        align: center middle;
    }

    #theme-picker-container {
        width: 40;
        height: auto;
        max-height: 24;
        border: thick $accent;
        background: $surface;
        padding: 1 2;
    }

    #theme-picker-title {
        text-style: bold;
        color: $accent;
        text-align: center;
        padding: 0 0 1 0;
    }

    ListView {
        height: auto;
        max-height: 18;
        border: none;
    }
    """

    def compose(self) -> ComposeResult:
        themes = sorted(self.app.available_themes.keys())
        current = self.app.theme

        with __import__("textual.containers", fromlist=["Vertical"]).Vertical(id="theme-picker-container"):
            yield Label("Select Theme", id="theme-picker-title")
            items = [
                ListItem(
                    Label(f"{'▶ ' if name == current else '  '}{name}"),
                    id=f"theme-{name.replace('-', '_')}",
                )
                for name in themes
            ]
            lv = ListView(*items)
            yield lv

    def on_mount(self) -> None:
        lv = self.query_one(ListView)
        themes = sorted(self.app.available_themes.keys())
        current = self.app.theme
        if current in themes:
            lv.index = themes.index(current)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        # Extract theme name from the item id (reverse the id mangling)
        item_id = event.item.id or ""
        theme_name = item_id.removeprefix("theme-").replace("_", "-")
        self.dismiss(theme_name)
