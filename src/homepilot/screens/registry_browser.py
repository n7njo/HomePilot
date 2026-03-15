"""Registry browser screen — search Docker Hub and launch deploy config."""

from __future__ import annotations

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, DataTable, Footer, Header, Input, Label, Select, Static

from homepilot.models import HomePilotConfig
from homepilot.providers import ProviderRegistry
from homepilot.services.registry import RegistryImage, fetch_tags, search_images


class RegistryBrowserScreen(Screen):
    """Browse Docker Hub to find an image, then launch the deploy config wizard."""

    BINDINGS = [
        Binding("escape", "go_back", "Back", show=True),
        Binding("enter", "configure_selected", "Configure →", show=True),
        Binding("ctrl+t", "fetch_tags", "Fetch Tags", show=True),
    ]

    DEFAULT_CSS = """
    #browser-title {
        text-style: bold;
        color: $accent;
    }
    #search-input {
        margin: 0 2 1 2;
    }
    #results-table {
        height: 1fr;
        margin: 0 2;
    }
    #bottom-bar {
        height: 5;
        padding: 1 2 0 2;
    }
    #bottom-bar Label {
        padding: 1 1 0 0;
        width: auto;
    }
    #tag-input {
        width: 20;
        margin: 0 2 0 0;
    }
    #host-select {
        width: 30;
    }
    #create-config-btn {
        margin: 0 0 0 2;
    }
    #status {
        margin: 0 2 0 2;
        color: $text-muted;
        height: 1;
    }
    """

    def __init__(self, config: HomePilotConfig, registry: ProviderRegistry) -> None:
        super().__init__()
        self._config = config
        self._registry = registry
        self._results: list[RegistryImage] = []

    def compose(self) -> ComposeResult:
        host_options = [(k, k) for k in self._config.hosts]
        default_host = next(iter(self._config.hosts), "")

        yield Header()
        yield Vertical(
            Label("\n  Deploy from Registry\n", id="browser-title"),
            Input(placeholder="Search Docker Hub…", id="search-input"),
            Static("  Type to search for an image", id="status"),
            DataTable(id="results-table"),
            Horizontal(
                Label("Tag:"),
                Input(value="latest", id="tag-input"),
                Label("Server:"),
                Select(host_options, value=default_host, id="host-select"),
                Button("Create Config", variant="primary", id="create-config-btn"),
                id="bottom-bar",
            ),
            id="browser-body",
        )
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#results-table", DataTable)
        table.cursor_type = "row"
        table.add_column("Image", key="name", width=35)
        table.add_column("Description", key="desc")
        table.add_column("★", key="stars", width=8)
        table.add_column("", key="badge", width=12)
        self.query_one("#search-input", Input).focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "search-input":
            return
        query = event.value.strip()
        if query:
            self._search(query)
        else:
            self._clear_results()

    @work(thread=True)
    def _search(self, query: str) -> None:
        self.app.call_from_thread(
            self.query_one("#status", Static).update,
            "  Searching…",
        )
        results = search_images(query)
        self.app.call_from_thread(self._update_results, results)

    def _clear_results(self) -> None:
        self._results = []
        self.query_one("#results-table", DataTable).clear()
        self.query_one("#status", Static).update("  Type to search for an image")

    def _update_results(self, results: list[RegistryImage]) -> None:
        self._results = results
        table = self.query_one("#results-table", DataTable)
        table.clear()
        for r in results:
            badge = "⭐ Official" if r.is_official else ""
            desc = r.description[:65] + "…" if len(r.description) > 65 else r.description
            table.add_row(r.name, desc, str(r.star_count), badge)
        count = len(results)
        label = "result" if count == 1 else "results"
        self.query_one("#status", Static).update(f"  {count} {label} found — [bold]Enter[/bold] to configure")
        if results:
            table.focus()

    def _selected_image(self) -> RegistryImage | None:
        table = self.query_one("#results-table", DataTable)
        if table.cursor_row is None or not self._results:
            return None
        try:
            idx = table.cursor_row
            return self._results[idx] if idx < len(self._results) else None
        except Exception:
            return None

    def action_fetch_tags(self) -> None:
        image = self._selected_image()
        if image:
            self._load_tags(image.name)

    @work(thread=True)
    def _load_tags(self, image_name: str) -> None:
        self.app.call_from_thread(
            self.query_one("#status", Static).update,
            f"  Fetching tags for {image_name}…",
        )
        tags = fetch_tags(image_name)
        self.app.call_from_thread(self._populate_tag_input, tags, image_name)

    def _populate_tag_input(self, tags: list[str], image_name: str) -> None:
        # Put the first tag (most recent) in the tag input and hint others in status
        tag_input = self.query_one("#tag-input", Input)
        tag_input.value = tags[0] if tags else "latest"
        preview = ", ".join(tags[:8])
        self.query_one("#status", Static).update(f"  Tags for {image_name}: {preview}")

    def action_configure_selected(self) -> None:
        image = self._selected_image()
        if image is None:
            self.notify("Select an image first", severity="warning", timeout=2)
            return

        tag = self.query_one("#tag-input", Input).value.strip() or "latest"
        host_key = str(self.query_one("#host-select", Select).value)
        # Derive a sensible service name from the image (strip org prefix)
        service_name = image.name.split("/")[-1]
        image_ref = f"{image.name}:{tag}"

        from homepilot.screens.add_resource import AddResourceScreen
        self.app.push_screen(
            AddResourceScreen(
                self._config,
                self._registry,
                prefill={
                    "host": host_key,
                    "app_name": service_name,
                    "image_name": image_ref,
                    "container_name": f"{service_name}-app",
                },
            )
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "create-config-btn":
            self.action_configure_selected()

    def action_go_back(self) -> None:
        self.app.pop_screen()
