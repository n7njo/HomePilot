"""Tests for Proxmox API client and ProxmoxProvider."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from homepilot.models import ProxmoxHostConfig
from homepilot.providers.base import ResourceStatus, ResourceType
from homepilot.providers.proxmox import ProxmoxProvider
from homepilot.services.proxmox_api import (
    PVEToken,
    ProxmoxAPI,
    resolve_token,
    _load_from_tokens_file,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_TOKEN = PVEToken(token_id="test@pve!tok", token_secret="secret-uuid")


@pytest.fixture
def api():
    """ProxmoxAPI with a mocked httpx.Client."""
    a = ProxmoxAPI("192.168.0.199", SAMPLE_TOKEN)
    a._client = MagicMock(spec=httpx.Client)
    return a


@pytest.fixture
def host_config():
    return ProxmoxHostConfig(
        host="192.168.0.199",
        token_id="test@pve!tok",
        token_secret="secret-uuid",
        token_source="inline",
        verify_ssl=False,
    )


@pytest.fixture
def provider(host_config):
    """ProxmoxProvider with a mocked API."""
    p = ProxmoxProvider("proxmox-lab", host_config)
    mock_api = MagicMock(spec=ProxmoxAPI)
    p._api = mock_api
    return p


# ---------------------------------------------------------------------------
# PVEToken
# ---------------------------------------------------------------------------


class TestPVEToken:
    def test_header_value(self):
        token = PVEToken(token_id="user@pve!my-token", token_secret="abc-123")
        assert token.header_value == "PVEAPIToken=user@pve!my-token=abc-123"


# ---------------------------------------------------------------------------
# resolve_token
# ---------------------------------------------------------------------------


class TestResolveToken:
    def test_inline(self):
        tok = resolve_token("user@pve!tok", "secret", token_source="inline")
        assert tok.token_id == "user@pve!tok"
        assert tok.token_secret == "secret"

    def test_env_vars(self, monkeypatch):
        monkeypatch.setenv("PROXMOX_API_TOKEN_ID", "env-id")
        monkeypatch.setenv("PROXMOX_API_TOKEN_SECRET", "env-secret")
        # Patch platform so keychain is skipped
        with patch("homepilot.services.proxmox_api.platform.system", return_value="Linux"):
            tok = resolve_token("ignored", "", token_source="env")
        assert tok.token_id == "env-id"
        assert tok.token_secret == "env-secret"

    def test_tokens_file(self, tmp_path, monkeypatch):
        tokens_file = tmp_path / ".homelab-tokens"
        tokens_file.write_text(
            'export PROXMOX_API_TOKEN_ID="file-id"\n'
            'export PROXMOX_API_TOKEN_SECRET="file-secret"\n'
        )
        # Clear env vars so file is used
        monkeypatch.delenv("PROXMOX_API_TOKEN_ID", raising=False)
        monkeypatch.delenv("PROXMOX_API_TOKEN_SECRET", raising=False)
        with (
            patch("homepilot.services.proxmox_api.platform.system", return_value="Linux"),
            patch("homepilot.services.proxmox_api.os.path.isfile", return_value=True),
            patch("homepilot.services.proxmox_api.os.path.expanduser", return_value=str(tokens_file)),
            patch("homepilot.services.proxmox_api._load_from_tokens_file") as mock_load,
        ):
            mock_load.return_value = PVEToken(token_id="file-id", token_secret="file-secret")
            tok = resolve_token("fallback", "", token_source="env")
        assert tok.token_id == "file-id"
        assert tok.token_secret == "file-secret"

    def test_no_token_raises(self, monkeypatch):
        monkeypatch.delenv("PROXMOX_API_TOKEN_ID", raising=False)
        monkeypatch.delenv("PROXMOX_API_TOKEN_SECRET", raising=False)
        with (
            patch("homepilot.services.proxmox_api.platform.system", return_value="Linux"),
            patch("homepilot.services.proxmox_api.os.path.isfile", return_value=False),
        ):
            with pytest.raises(RuntimeError, match="No Proxmox API token"):
                resolve_token("", "", token_source="env")


# ---------------------------------------------------------------------------
# _load_from_tokens_file
# ---------------------------------------------------------------------------


class TestLoadFromTokensFile:
    def test_valid_file(self, tmp_path):
        f = tmp_path / "tokens"
        f.write_text(
            'export PROXMOX_API_TOKEN_ID="user@pve!tok"\n'
            'export PROXMOX_API_TOKEN_SECRET="abc-def"\n'
        )
        tok = _load_from_tokens_file(str(f))
        assert tok is not None
        assert tok.token_id == "user@pve!tok"
        assert tok.token_secret == "abc-def"

    def test_missing_file(self, tmp_path):
        result = _load_from_tokens_file(str(tmp_path / "no-such-file"))
        assert result is None

    def test_incomplete_file(self, tmp_path):
        f = tmp_path / "tokens"
        f.write_text('export PROXMOX_API_TOKEN_ID="only-id"\n')
        result = _load_from_tokens_file(str(f))
        assert result is None


# ---------------------------------------------------------------------------
# ProxmoxAPI
# ---------------------------------------------------------------------------


def _mock_response(data):
    """Create a mock httpx.Response with the PVE JSON envelope."""
    resp = MagicMock(spec=httpx.Response)
    resp.json.return_value = {"data": data}
    resp.raise_for_status = MagicMock()
    return resp


class TestProxmoxAPI:
    def test_base_url(self, api):
        assert api.base_url == "https://192.168.0.199:8006/api2/json"

    def test_is_connected(self, api):
        assert api.is_connected() is True
        api._client = None
        assert api.is_connected() is False

    def test_get_version(self, api):
        api._client.get.return_value = _mock_response({"version": "8.2.4"})
        info = api.get_version()
        assert info["version"] == "8.2.4"
        api._client.get.assert_called_once_with("/version")

    def test_get_nodes(self, api):
        api._client.get.return_value = _mock_response([
            {"node": "pve", "status": "online"},
        ])
        nodes = api.get_nodes()
        assert len(nodes) == 1
        assert nodes[0]["node"] == "pve"

    def test_get_vms(self, api):
        api._client.get.return_value = _mock_response([
            {"vmid": 100, "name": "ubuntu", "status": "running"},
            {"vmid": 101, "name": "debian", "status": "stopped"},
        ])
        vms = api.get_vms("pve")
        assert len(vms) == 2
        assert vms[0]["name"] == "ubuntu"

    def test_get_vm_status(self, api):
        api._client.get.return_value = _mock_response(
            {"status": "running", "uptime": 3600, "mem": 1073741824, "maxmem": 2147483648, "cpus": 2}
        )
        status = api.get_vm_status("pve", 100)
        assert status["status"] == "running"
        assert status["cpus"] == 2

    def test_start_vm(self, api):
        api._client.post.return_value = _mock_response("UPID:pve:123")
        result = api.start_vm("pve", 100)
        api._client.post.assert_called_once_with("/nodes/pve/qemu/100/status/start", data=None)
        assert result == "UPID:pve:123"

    def test_get_containers(self, api):
        api._client.get.return_value = _mock_response([
            {"vmid": 200, "name": "pihole", "status": "running"},
        ])
        cts = api.get_containers("pve")
        assert len(cts) == 1
        assert cts[0]["vmid"] == 200

    def test_start_container(self, api):
        api._client.post.return_value = _mock_response("UPID:pve:456")
        api.start_container("pve", 200)
        api._client.post.assert_called_once_with("/nodes/pve/lxc/200/status/start", data=None)

    def test_get_cluster_resources(self, api):
        api._client.get.return_value = _mock_response([
            {"type": "qemu", "vmid": 100, "node": "pve", "name": "vm1", "status": "running"},
            {"type": "lxc", "vmid": 200, "node": "pve", "name": "ct1", "status": "stopped"},
        ])
        resources = api.get_cluster_resources("vm")
        assert len(resources) == 2
        api._client.get.assert_called_once_with("/cluster/resources?type=vm")

    def test_test_connection_success(self, api):
        api._client.get.return_value = _mock_response({"version": "8.2.4"})
        assert api.test_connection() is True

    def test_test_connection_failure(self, api):
        api._client.get.side_effect = httpx.HTTPStatusError(
            "401", request=MagicMock(), response=MagicMock()
        )
        assert api.test_connection() is False

    def test_connect_disconnect(self):
        a = ProxmoxAPI("10.0.0.1", SAMPLE_TOKEN)
        assert a.is_connected() is False
        with patch("homepilot.services.proxmox_api.httpx.Client") as MockClient:
            a.connect()
            assert a.is_connected() is True
            MockClient.assert_called_once()
        a.disconnect()
        assert a.is_connected() is False


# ---------------------------------------------------------------------------
# ProxmoxProvider
# ---------------------------------------------------------------------------


CLUSTER_RESOURCES = [
    {
        "type": "qemu", "vmid": 100, "node": "pve", "name": "ubuntu-vm",
        "status": "running", "uptime": 90061, "maxmem": 4294967296, "maxcpu": 4, "template": 0,
    },
    {
        "type": "lxc", "vmid": 200, "node": "pve", "name": "pihole-ct",
        "status": "stopped", "uptime": 0, "maxmem": 536870912, "maxcpu": 1, "template": 0,
    },
    {
        "type": "storage", "storage": "local", "node": "pve",
    },
]


class TestProxmoxProvider:
    def test_properties(self, provider, host_config):
        assert provider.name == "proxmox-lab"
        assert provider.host_display == "192.168.0.199"
        assert provider.provider_type == "proxmox"

    def test_is_connected(self, provider):
        provider._api.is_connected.return_value = True
        assert provider.is_connected() is True

    def test_list_resources(self, provider):
        provider._api.is_connected.return_value = True
        provider._api.get_cluster_resources.return_value = CLUSTER_RESOURCES

        resources = provider.list_resources()
        assert len(resources) == 2  # storage entry filtered out

        vm = resources[0]
        assert vm.name == "ubuntu-vm"
        assert vm.resource_type == ResourceType.VM
        assert vm.status == ResourceStatus.RUNNING
        assert vm.id == "qemu/pve/100"
        assert "1d" in vm.uptime  # 90061s ≈ 1d 1h

        lxc = resources[1]
        assert lxc.name == "pihole-ct"
        assert lxc.resource_type == ResourceType.LXC_CONTAINER
        assert lxc.status == ResourceStatus.STOPPED
        assert lxc.id == "lxc/pve/200"

    def test_get_resource(self, provider):
        provider._api.is_connected.return_value = True
        provider._api.get_cluster_resources.return_value = CLUSTER_RESOURCES

        r = provider.get_resource("qemu/pve/100")
        assert r is not None
        assert r.name == "ubuntu-vm"

        assert provider.get_resource("qemu/pve/999") is None

    def test_start_vm(self, provider):
        provider._api.is_connected.return_value = True
        provider._api.start_vm.return_value = {"data": "UPID"}
        assert provider.start("qemu/pve/100") is True
        provider._api.start_vm.assert_called_once_with("pve", 100)

    def test_start_lxc(self, provider):
        provider._api.is_connected.return_value = True
        provider._api.start_container.return_value = {"data": "UPID"}
        assert provider.start("lxc/pve/200") is True
        provider._api.start_container.assert_called_once_with("pve", 200)

    def test_stop_vm(self, provider):
        provider._api.is_connected.return_value = True
        provider._api.shutdown_vm.return_value = {}
        assert provider.stop("qemu/pve/100") is True
        provider._api.shutdown_vm.assert_called_once_with("pve", 100)

    def test_restart_lxc(self, provider):
        provider._api.is_connected.return_value = True
        provider._api.reboot_container.return_value = {}
        assert provider.restart("lxc/pve/200") is True
        provider._api.reboot_container.assert_called_once_with("pve", 200)

    def test_remove_returns_false(self, provider):
        assert provider.remove("qemu/pve/100") is False

    def test_status_running(self, provider):
        provider._api.is_connected.return_value = True
        provider._api.get_vm_status.return_value = {"status": "running"}
        assert provider.status("qemu/pve/100") == ResourceStatus.RUNNING

    def test_status_stopped_lxc(self, provider):
        provider._api.is_connected.return_value = True
        provider._api.get_container_status.return_value = {"status": "stopped"}
        assert provider.status("lxc/pve/200") == ResourceStatus.STOPPED

    def test_status_error_returns_unknown(self, provider):
        provider._api.is_connected.return_value = True
        provider._api.get_vm_status.side_effect = Exception("connection lost")
        assert provider.status("qemu/pve/100") == ResourceStatus.UNKNOWN

    def test_logs_vm(self, provider):
        provider._api.is_connected.return_value = True
        provider._api.get_vm_status.return_value = {
            "status": "running", "uptime": 7200, "mem": 536870912, "maxmem": 1073741824, "cpus": 2,
        }
        log_output = provider.logs("qemu/pve/100")
        assert "VM 100 on pve" in log_output
        assert "running" in log_output.lower()

    def test_logs_lxc(self, provider):
        provider._api.is_connected.return_value = True
        provider._api.get_container_status.return_value = {
            "status": "running", "uptime": 3600, "mem": 134217728, "maxmem": 536870912, "cpus": 1,
        }
        log_output = provider.logs("lxc/pve/200")
        assert "LXC 200 on pve" in log_output

    def test_start_failure_returns_false(self, provider):
        provider._api.is_connected.return_value = True
        provider._api.start_vm.side_effect = Exception("timeout")
        assert provider.start("qemu/pve/100") is False

    def test_invalid_resource_id(self, provider):
        provider._api.is_connected.return_value = True
        # ValueError is caught by the broad except in start() → returns False
        assert provider.start("bad-id") is False

    def test_uptime_display(self, provider):
        assert provider._uptime_display(0) == ""
        assert provider._uptime_display(90061) == "1d 1h 1m"
        assert provider._uptime_display(3661) == "1h 1m"
        assert provider._uptime_display(120) == "2m"


class TestProxmoxProviderProtocol:
    """Verify ProxmoxProvider satisfies InfraProvider protocol."""

    def test_isinstance_check(self, host_config):
        from homepilot.providers.base import InfraProvider
        p = ProxmoxProvider("test", host_config)
        assert isinstance(p, InfraProvider)
