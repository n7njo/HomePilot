"""Host bootstrap service — provisions a Proxmox (or any Debian-based) host
for HomePilot management.

Bootstrap steps
---------------
1. connect       — SSH as the configured root/admin user
2. check_os      — Verify Debian/Ubuntu and note OS version
3. install_docker — Install Docker CE if not present
4. create_user   — Create 'homepilot' system user, add to docker group
5. setup_ssh     — Copy root's authorized_keys → homepilot's
6. setup_dirs    — Create /opt/homepilot/ tree, set ownership
7. write_state   — Write initial state.yaml
8. verify        — Re-connect as homepilot and confirm docker access

After bootstrap the caller should update the host config's ssh_user
from "root" to "homepilot".
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Callable, Generator

from homepilot.models import (
    DeploymentState,
    DeployStep,
    DeployStepStatus,
)
from homepilot.services.ssh import SSHService

if TYPE_CHECKING:
    from homepilot.models import ProxmoxHostConfig, TrueNASHostConfig

logger = logging.getLogger(__name__)

BootstrapEvent = tuple[str, str, str]  # (step, status, message)
LineCallback = Callable[[str], None]

HOMEPILOT_USER = "homepilot"
HOMEPILOT_DIR = "/opt/homepilot"

_DOCKER_INSTALL_SCRIPT = """\
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq ca-certificates curl gnupg lsb-release
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/debian/gpg \
  | gpg --dearmor -o /etc/apt/keyrings/docker.gpg 2>/dev/null
chmod a+r /etc/apt/keyrings/docker.gpg
CODENAME=$(. /etc/os-release && echo "$VERSION_CODENAME")
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/debian ${CODENAME} stable" \
  > /etc/apt/sources.list.d/docker.list
apt-get update -qq
apt-get install -y -qq docker-ce docker-ce-cli containerd.io
systemctl enable docker
systemctl start docker
"""


class _SkipStep(Exception):
    pass


class BootstrapService:
    """Provision a host for HomePilot management.

    Takes the *root* (or sudoer) credentials from the host config to
    perform the one-time setup.  After bootstrap finishes successfully
    the caller should persist the updated host config (ssh_user →
    'homepilot').
    """

    def __init__(
        self,
        host_config: ProxmoxHostConfig | TrueNASHostConfig,
        *,
        root_user: str = "root",
        line_callback: LineCallback | None = None,
    ) -> None:
        self._host = host_config
        self._root_user = root_user
        self._line_cb = line_callback
        self._aborted = False
        self._ssh: SSHService | None = None
        self.state: DeploymentState | None = None

    def abort(self) -> None:
        self._aborted = True

    # ------------------------------------------------------------------
    # Public run interface
    # ------------------------------------------------------------------

    def run_sync(self) -> Generator[BootstrapEvent, None, None]:
        self.state = DeploymentState(
            app_name=f"bootstrap:{self._host.host}",
            started_at=datetime.now(timezone.utc),
        )
        steps = self._build_steps()
        self.state.steps = steps

        for step in steps:
            if self._aborted:
                step.status = DeployStepStatus.SKIPPED
                yield (step.name, "skipped", "Aborted")
                continue

            step.status = DeployStepStatus.RUNNING
            step.started_at = datetime.now(timezone.utc)
            yield (step.name, "running", step.description)

            try:
                message = self._execute_step(step.name)
                step.status = DeployStepStatus.SUCCESS
                step.message = message
                step.finished_at = datetime.now(timezone.utc)
                yield (step.name, "success", message)
            except _SkipStep as exc:
                step.status = DeployStepStatus.SKIPPED
                step.message = str(exc)
                step.finished_at = datetime.now(timezone.utc)
                yield (step.name, "skipped", str(exc))
            except Exception as exc:
                step.status = DeployStepStatus.FAILED
                step.message = str(exc)
                step.finished_at = datetime.now(timezone.utc)
                yield (step.name, "failed", str(exc))
                self._aborted = True

        self.state.finished_at = datetime.now(timezone.utc)
        self.state.aborted = self._aborted
        if self._ssh:
            self._ssh.close()

    # ------------------------------------------------------------------
    # Steps
    # ------------------------------------------------------------------

    def _build_steps(self) -> list[DeployStep]:
        return [
            DeployStep("connect", f"Connect to {self._host.host} as {self._root_user}"),
            DeployStep("check_os", "Check OS compatibility"),
            DeployStep("install_docker", "Install Docker CE (if missing)"),
            DeployStep("create_user", f"Create '{HOMEPILOT_USER}' system user"),
            DeployStep("setup_ssh", f"Authorise SSH key for '{HOMEPILOT_USER}'"),
            DeployStep("setup_dirs", f"Create {HOMEPILOT_DIR}/ directory tree"),
            DeployStep("write_state", "Write initial state file"),
            DeployStep("verify", f"Verify '{HOMEPILOT_USER}' can run docker"),
        ]

    def _execute_step(self, name: str) -> str:
        return {
            "connect": self._step_connect,
            "check_os": self._step_check_os,
            "install_docker": self._step_install_docker,
            "create_user": self._step_create_user,
            "setup_ssh": self._step_setup_ssh,
            "setup_dirs": self._step_setup_dirs,
            "write_state": self._step_write_state,
            "verify": self._step_verify,
        }[name]()

    def _step_connect(self) -> str:
        from homepilot.models import ServerConfig
        server = ServerConfig(
            host=self._host.host,
            user=self._root_user,
            ssh_key=getattr(self._host, "ssh_key", ""),
        )
        self._ssh = SSHService(server)
        self._ssh.connect()
        out, _, _ = self._run("whoami")
        return f"Connected as {out.strip()} to {self._host.host}"

    def _step_check_os(self) -> str:
        out, _, code = self._run("cat /etc/os-release")
        if code != 0:
            raise RuntimeError("Cannot read /etc/os-release — is this a Linux host?")
        lines = dict(
            line.split("=", 1) for line in out.splitlines() if "=" in line
        )
        name = lines.get("NAME", "").strip('"')
        version = lines.get("VERSION_ID", "").strip('"')
        # Check for Debian/Ubuntu (Proxmox is Debian-based)
        if "debian" not in name.lower() and "ubuntu" not in name.lower():
            raise RuntimeError(
                f"Unsupported OS: {name}. HomePilot bootstrap requires Debian or Ubuntu."
            )
        return f"OS: {name} {version}"

    def _step_install_docker(self) -> str:
        _, _, code = self._run("docker --version")
        if code == 0:
            out, _, _ = self._run("docker --version")
            raise _SkipStep(f"Docker already installed: {out.strip()}")

        self._emit("Installing Docker CE — this may take a minute…")
        _, err, code = self._run_stream(_DOCKER_INSTALL_SCRIPT, timeout=300)
        if code != 0:
            raise RuntimeError(f"Docker installation failed:\n{err}")

        out, _, _ = self._run("docker --version")
        return f"Installed: {out.strip()}"

    def _step_create_user(self) -> str:
        # Check if user already exists
        _, _, code = self._run(f"id {HOMEPILOT_USER}")
        if code == 0:
            raise _SkipStep(f"User '{HOMEPILOT_USER}' already exists")

        self._run(f"useradd -m -s /bin/bash {HOMEPILOT_USER}")
        self._run(f"usermod -aG docker {HOMEPILOT_USER}")
        return f"Created user '{HOMEPILOT_USER}' and added to docker group"

    def _step_setup_ssh(self) -> str:
        # Copy root's authorized_keys to the homepilot user
        root_keys_out, _, root_code = self._run("cat /root/.ssh/authorized_keys 2>/dev/null")
        if root_code != 0 or not root_keys_out.strip():
            raise RuntimeError(
                "No authorized_keys found in /root/.ssh/ — "
                "add your SSH public key to root first via the Proxmox web console."
            )

        cmds = [
            f"mkdir -p /home/{HOMEPILOT_USER}/.ssh",
            f"cp /root/.ssh/authorized_keys /home/{HOMEPILOT_USER}/.ssh/authorized_keys",
            f"chown -R {HOMEPILOT_USER}:{HOMEPILOT_USER} /home/{HOMEPILOT_USER}/.ssh",
            f"chmod 700 /home/{HOMEPILOT_USER}/.ssh",
            f"chmod 600 /home/{HOMEPILOT_USER}/.ssh/authorized_keys",
        ]
        for cmd in cmds:
            _, err, code = self._run(cmd)
            if code != 0:
                raise RuntimeError(f"SSH setup failed: {err}")

        key_count = len([l for l in root_keys_out.splitlines() if l.strip()])
        return f"Copied {key_count} authorized key(s) to {HOMEPILOT_USER}"

    def _step_setup_dirs(self) -> str:
        dirs = [
            HOMEPILOT_DIR,
            f"{HOMEPILOT_DIR}/apps",
            f"{HOMEPILOT_DIR}/backups",
            f"{HOMEPILOT_DIR}/logs",
        ]
        for d in dirs:
            _, err, code = self._run(f"mkdir -p {d}")
            if code != 0:
                raise RuntimeError(f"mkdir failed for {d}: {err}")

        self._run(f"chown -R {HOMEPILOT_USER}:{HOMEPILOT_USER} {HOMEPILOT_DIR}")
        return f"Created {HOMEPILOT_DIR}/ tree, owned by {HOMEPILOT_USER}"

    def _step_write_state(self) -> str:
        from homepilot.services.remote_state import RemoteStateService
        state_svc = RemoteStateService(self._ssh, host_key=self._host.host)
        state = state_svc.read()  # creates empty if missing
        state_svc.write(state)
        return f"State file written to {self._host.host}:{'/opt/homepilot/state.yaml'}"

    def _step_verify(self) -> str:
        # Re-connect as homepilot user to confirm ssh + docker access
        from homepilot.models import ServerConfig
        test_server = ServerConfig(
            host=self._host.host,
            user=HOMEPILOT_USER,
            ssh_key=getattr(self._host, "ssh_key", ""),
        )
        test_ssh = SSHService(test_server)
        try:
            test_ssh.connect()
            out, _, code = test_ssh.run_command("docker ps --format '{{.Names}}' 2>&1 | head -5")
            test_ssh.close()
            if code != 0:
                raise RuntimeError(
                    f"'{HOMEPILOT_USER}' connected via SSH but docker failed — "
                    "the docker group change may need a re-login. Try restarting the host or re-running bootstrap."
                )
            containers = out.strip() or "(none running)"
            return f"'{HOMEPILOT_USER}' can SSH and run docker. Running: {containers}"
        except Exception as exc:
            try:
                test_ssh.close()
            except Exception:
                pass
            raise RuntimeError(f"Could not connect as '{HOMEPILOT_USER}': {exc}") from exc

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _run(self, cmd: str, timeout: float = 60) -> tuple[str, str, int]:
        assert self._ssh is not None
        return self._ssh.run_command(cmd, timeout=timeout)

    def _run_stream(self, cmd: str, timeout: float = 300) -> tuple[str, str, int]:
        assert self._ssh is not None
        return self._ssh.run_command_stream(cmd, line_callback=self._line_cb, timeout=timeout)

    def _emit(self, line: str) -> None:
        if self._line_cb:
            self._line_cb(line)
