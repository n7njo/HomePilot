# Adding New Providers

HomePilot uses a pluggable provider architecture. To add support for a new infrastructure backend (e.g. Unraid, Docker Compose on a remote host, etc.), implement the `InfraProvider` protocol and register it in the provider factory.

## Step 1: Define a Host Config

Add a new dataclass in `src/homepilot/models.py`:

```python
@dataclass
class MyHostConfig(HostConfig):
    type: str = "myhost"
    api_url: str = ""
    api_key: str = ""
```

## Step 2: Implement the Provider

Create `src/homepilot/providers/myhost.py` and implement every method in the `InfraProvider` protocol defined in `providers/base.py`:

```python
class MyHostProvider:
    def __init__(self, host_key: str, config: MyHostConfig) -> None:
        self._host_key = host_key
        self._config = config

    @property
    def name(self) -> str:
        return self._host_key

    @property
    def host_display(self) -> str:
        return self._config.api_url

    @property
    def provider_type(self) -> str:
        return "myhost"

    def connect(self) -> None: ...
    def disconnect(self) -> None: ...
    def is_connected(self) -> bool: ...
    def list_resources(self) -> list[Resource]: ...
    def get_resource(self, resource_id: str) -> Resource | None: ...
    def start(self, resource_id: str) -> bool: ...
    def stop(self, resource_id: str) -> bool: ...
    def restart(self, resource_id: str) -> bool: ...
    def remove(self, resource_id: str) -> bool: ...
    def logs(self, resource_id: str, lines: int = 50) -> str: ...
    def status(self, resource_id: str) -> ResourceStatus: ...
```

## Step 3: Register in the Factory

In `src/homepilot/providers/__init__.py`, add the new type to `ProviderRegistry._build_provider()`:

```python
elif host_type == "myhost":
    from homepilot.providers.myhost import MyHostProvider
    return MyHostProvider(host_key, config)
```

## Step 4: Update Config Parsing

In `src/homepilot/config.py`, handle the new type in `_parse_host()` and `_host_to_dict()`:

```python
elif host_type == "myhost":
    return MyHostConfig(
        type="myhost",
        host=data.get("host", ""),
        api_url=data.get("api_url", ""),
        api_key=data.get("api_key", ""),
    )
```

## Step 5: Write Tests

Add `tests/test_myhost.py` mirroring the pattern in `tests/test_proxmox.py` — mock the API calls and verify that `list_resources`, lifecycle actions, and error handling work correctly.

## Step 6: Document

Add a wiki page (e.g. `wiki/MyHost-Provider.md`) and update the `_Sidebar.md`.
