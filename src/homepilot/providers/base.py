"""Base provider protocol and shared resource model."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol, runtime_checkable


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class ResourceType(str, Enum):
    """The kind of infrastructure resource."""

    DOCKER_CONTAINER = "docker_container"
    LXC_CONTAINER = "lxc_container"
    VM = "vm"
    APP = "app"  # TrueNAS Custom App


class ResourceStatus(str, Enum):
    """Runtime status of a resource."""

    RUNNING = "Running"
    STOPPED = "Stopped"
    ERROR = "Error"
    UNKNOWN = "Unknown"


class HealthStatus(str, Enum):
    """Health-check result."""

    HEALTHY = "Healthy"
    UNHEALTHY = "Unhealthy"
    UNKNOWN = "Unknown"


# ---------------------------------------------------------------------------
# Protocol detection
# ---------------------------------------------------------------------------

# Ports that are always TCP (non-HTTP) regardless of image name.
_TCP_PORTS: frozenset[int] = frozenset({
    3306,   # MySQL / MariaDB / Dolt
    5432,   # PostgreSQL
    6379,   # Redis
    6380,   # Redis TLS
    27017,  # MongoDB
    27018,  # MongoDB shard
    1433,   # MSSQL
    1521,   # Oracle DB
    5672,   # RabbitMQ AMQP
    4369,   # RabbitMQ EPMD
    2181,   # Zookeeper
    2379,   # etcd client
    2380,   # etcd peer
    9300,   # Elasticsearch transport
    7000,   # Cassandra inter-node
    7001,   # Cassandra TLS
    9042,   # Cassandra CQL
    11211,  # Memcached
})

# Image name fragments that indicate a TCP-only service.
_TCP_IMAGE_FRAGMENTS: tuple[str, ...] = (
    "mysql", "mariadb", "postgres", "redis", "mongo",
    "mssql", "sqlserver", "cassandra", "zookeeper", "memcached",
    "dolt", "cockroach", "clickhouse", "influxdb", "timescaledb",
)


def detect_protocol(port: int, image: str = "") -> str:
    """Return the likely protocol for a service: 'tcp', 'https', or 'http'.

    Detection order:
    1. Well-known TCP port  → 'tcp'
    2. Image name hint      → 'tcp'
    3. Port 443             → 'https'
    4. Default              → 'http'
    """
    if port in _TCP_PORTS:
        return "tcp"
    image_lower = image.lower()
    if any(frag in image_lower for frag in _TCP_IMAGE_FRAGMENTS):
        return "tcp"
    if port == 443:
        return "https"
    return "http"


# ---------------------------------------------------------------------------
# Resource dataclass
# ---------------------------------------------------------------------------


@dataclass
class Resource:
    """A single infrastructure resource managed by a provider."""

    id: str
    name: str
    resource_type: ResourceType
    provider_name: str
    status: ResourceStatus = ResourceStatus.UNKNOWN
    health: HealthStatus = HealthStatus.UNKNOWN
    host: str = ""
    port: int = 0
    protocol: str = "http"
    image: str = ""
    uptime: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def status_display(self) -> str:
        """Human-readable one-line summary."""
        parts = [self.name, self.status.value]
        if self.health != HealthStatus.UNKNOWN:
            parts.append(self.health.value)
        if self.port:
            parts.append(f":{self.port}")
        return " | ".join(parts)


# ---------------------------------------------------------------------------
# Provider protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class InfraProvider(Protocol):
    """Interface that every infrastructure provider must implement.

    Providers are responsible for connecting to a host, listing resources,
    and performing lifecycle actions (start, stop, restart, remove, logs).
    """

    @property
    def name(self) -> str:
        """Short identifier for this provider instance (e.g. 'truenas', 'proxmox')."""
        ...

    @property
    def host_display(self) -> str:
        """Human-readable host string (e.g. 'neil@truenas.lan')."""
        ...

    @property
    def provider_type(self) -> str:
        """Provider type string (e.g. 'truenas', 'proxmox')."""
        ...

    # -- Connection lifecycle ------------------------------------------------

    def connect(self) -> None:
        """Establish connection to the host."""
        ...

    def disconnect(self) -> None:
        """Tear down the connection."""
        ...

    def is_connected(self) -> bool:
        """Return True if the provider has an active connection."""
        ...

    # -- Resource queries ----------------------------------------------------

    def list_resources(self) -> list[Resource]:
        """Return all known resources on this host."""
        ...

    def get_resource(self, resource_id: str) -> Resource | None:
        """Return a single resource by ID, or None if not found."""
        ...

    # -- Lifecycle actions ---------------------------------------------------

    def start(self, resource_id: str) -> bool:
        """Start a resource. Returns True on success."""
        ...

    def stop(self, resource_id: str) -> bool:
        """Stop a resource. Returns True on success."""
        ...

    def restart(self, resource_id: str) -> bool:
        """Restart a resource. Returns True on success."""
        ...

    def remove(self, resource_id: str) -> bool:
        """Remove a resource. Returns True on success."""
        ...

    # -- Observability -------------------------------------------------------

    def logs(self, resource_id: str, lines: int = 50) -> str:
        """Fetch recent log output for a resource."""
        ...

    def status(self, resource_id: str) -> ResourceStatus:
        """Query the current status of a resource."""
        ...
