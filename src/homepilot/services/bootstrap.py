"""Host bootstrap service — provisions a host for HomePilot management.

Two implementations:
- BootstrapService        — Proxmox / Debian-based hosts
- TrueNASBootstrapService — TrueNAS SCALE hosts (Docker built-in, midclt user mgmt)

Use make_bootstrap_service(host_cfg, ...) to get the right one.

Proxmox bootstrap steps
-----------------------
1. connect       — SSH as the configured root/admin user
2. check_os      — Verify Debian/Ubuntu and note OS version
3. install_docker — Install Docker CE if not present
4. create_user   — Create 'homepilot' system user, add to docker group
5. setup_ssh     — Copy root's authorized_keys → homepilot's
6. setup_dirs    — Create /opt/homepilot/ tree, set ownership
7. write_state   — Write initial state.yaml
8. verify        — Re-connect as homepilot and confirm docker access

TrueNAS bootstrap steps
-----------------------
1. connect        — SSH as current admin user
2. create_user    — Create 'homepilot' user via midclt user.create
3. setup_ssh      — Authorise admin SSH key via midclt user.update
4. setup_sudoers  — Grant passwordless sudo for docker via /etc/sudoers.d/homepilot
5. setup_dirs     — Create /mnt/tank/homepilot/ state directory
6. write_state    — Write initial state.yaml
7. verify         — Re-connect as homepilot, confirm sudo docker ps

After bootstrap the caller should update the host config's user/ssh_user
to 'homepilot'.
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
TRUENAS_HOMEPILOT_DIR = "/mnt/tank/homepilot"

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


# ---------------------------------------------------------------------------
# TrueNAS SCALE bootstrap
# ---------------------------------------------------------------------------

class TrueNASBootstrapService:
    """Provision a TrueNAS SCALE host for HomePilot management.

    TrueNAS SCALE ships with Docker built-in, so there is no Docker install
    step.  User creation goes through ``midclt call user.create`` rather than
    ``useradd``.  The state directory lives at /mnt/tank/homepilot/ instead
    of /opt/homepilot/.
    """

    def __init__(
        self,
        host_config: "TrueNASHostConfig",
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
    # Public run interface (mirrors BootstrapService)
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
            DeployStep("create_user", f"Create '{HOMEPILOT_USER}' user via midclt"),
            DeployStep("setup_ssh", f"Authorise SSH key for '{HOMEPILOT_USER}'"),
            DeployStep("setup_sudoers", "Grant passwordless sudo for docker"),
            DeployStep("setup_dirs", f"Create {TRUENAS_HOMEPILOT_DIR}/ directory"),
            DeployStep("write_state", "Write initial state file"),
            DeployStep("verify", f"Verify '{HOMEPILOT_USER}' can run sudo docker"),
        ]

    def _execute_step(self, name: str) -> str:
        return {
            "connect": self._step_connect,
            "create_user": self._step_create_user,
            "setup_ssh": self._step_setup_ssh,
            "setup_sudoers": self._step_setup_sudoers,
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

    def _step_create_user(self) -> str:
        import json
        # Check if user already exists (Unix level)
        _, _, code = self._run(f"id {HOMEPILOT_USER}")
        if code == 0:
            raise _SkipStep(f"User '{HOMEPILOT_USER}' already exists")

        midclt = self._host.midclt_cmd
        # TrueNAS rules:
        # - password_disabled=true requires the 'password' field to be omitted entirely
        # - home must start with /mnt or be /var/empty (homepilot is a service account)
        # home is the *parent* directory — TrueNAS creates home/<username> inside it.
        # The parent must already exist; /mnt/tank/home is a safe conventional location.
        _, mkdir_err, mkdir_code = self._run("sudo mkdir -p /mnt/tank/home && sudo chmod 755 /mnt/tank/home")
        if mkdir_code != 0:
            raise RuntimeError(f"Failed to create /mnt/tank/home: {mkdir_err}")
        payload = json.dumps({
            "username": HOMEPILOT_USER,
            "full_name": "HomePilot Management",
            "password_disabled": True,
            "smb": False,  # must be False to allow password_disabled when SMB is enabled
            "shell": "/usr/bin/bash",
            "home": "/mnt/tank/home",
            "home_create": True,
            "group_create": True,
        })
        # Single-quote the JSON payload — safe as JSON values contain no single quotes here
        out, err, code = self._run(f"{midclt} user.create '{payload}'")
        if code != 0:
            raise RuntimeError(f"midclt user.create failed: {err or out}")
        uid = out.strip()
        return f"Created user '{HOMEPILOT_USER}' (TrueNAS id: {uid})"

    def _step_setup_ssh(self) -> str:
        import json
        import subprocess

        # Use the same key HomePilot already uses to connect:
        # 1. If an ssh_key file is configured, derive the public key from it.
        # 2. Otherwise, scan ~/.ssh/*.pub (same files Paramiko auto-discovers).
        from pathlib import Path

        first_key = None
        ssh_key_path = getattr(self._host, "ssh_key", "")
        if ssh_key_path:
            result = subprocess.run(
                ["ssh-keygen", "-y", "-f", ssh_key_path],
                capture_output=True, text=True,
            )
            if result.returncode == 0 and result.stdout.strip():
                first_key = result.stdout.strip().splitlines()[0].strip()

        if not first_key:
            ssh_dir = Path.home() / ".ssh"
            for pub_file in sorted(ssh_dir.glob("*.pub")):
                try:
                    for line in pub_file.read_text().splitlines():
                        if line.strip() and not line.startswith("#"):
                            first_key = line.strip()
                            break
                except OSError:
                    continue
                if first_key:
                    break

        if not first_key:
            raise RuntimeError(
                "Could not find an SSH public key in ~/.ssh/*.pub — "
                "ensure an ssh_key is configured for this host or that "
                "a public key exists in ~/.ssh/."
            )

        # Look up the TrueNAS-internal user ID for homepilot.
        # Pass the filter as a separate shell argument (not wrapped in an outer array).
        midclt = self._host.midclt_cmd
        filters = f'[["username", "=", "{HOMEPILOT_USER}"]]'
        query_out, _, query_code = self._run(f"{midclt} user.query '{filters}'")
        if query_code != 0:
            raise RuntimeError(f"midclt user.query failed: {query_out}")
        try:
            users = json.loads(query_out)
            if not users:
                raise ValueError(f"no user named '{HOMEPILOT_USER}' found")
            truenas_id = users[0].get("id")
            if truenas_id is None:
                raise ValueError("no 'id' in response")
        except (json.JSONDecodeError, ValueError) as exc:
            raise RuntimeError(
                f"Failed to parse midclt user.query response: {query_out[:200]}"
            ) from exc

        # Ensure homepilot has a writable home (needed for sshpubkey to write authorized_keys).
        # If user was created with /var/empty, migrate to /mnt/tank/home first.
        users = json.loads(query_out)
        current_home = users[0].get("home", "")
        if not current_home or current_home == "/var/empty":
            _, mkdir_err, mkdir_code = self._run("sudo mkdir -p /mnt/tank/home && sudo chmod 755 /mnt/tank/home")
            if mkdir_code != 0:
                raise RuntimeError(f"Failed to create /mnt/tank/home: {mkdir_err}")
            home_payload = json.dumps({"home": "/mnt/tank/home", "home_create": True})
            _, home_err, home_code = self._run(
                f"{midclt} user.update '{truenas_id}' '{home_payload}'"
            )
            if home_code != 0:
                raise RuntimeError(f"midclt user.update (home) failed: {home_err}")

        # Update the user with the SSH public key.
        # midclt call user.update takes two separate positional args: id and data dict.
        data_payload = json.dumps({"sshpubkey": first_key})
        _, err, code = self._run(f"{midclt} user.update '{truenas_id}' '{data_payload}'")
        if code != 0:
            raise RuntimeError(f"midclt user.update (sshpubkey) failed: {err}")

        return f"SSH key authorised for '{HOMEPILOT_USER}' (TrueNAS id: {truenas_id})"

    def _step_setup_sudoers(self) -> str:
        # Find the docker binary path
        docker_path_out, _, code = self._run("which docker || command -v docker")
        docker_path = docker_path_out.strip() or "/usr/bin/docker"

        sudoers_line = f"{HOMEPILOT_USER} ALL=(ALL) NOPASSWD: {docker_path}"
        sudoers_file = "/etc/sudoers.d/homepilot"

        # Write the sudoers drop-in (chmod 440 required for sudo to accept it)
        escaped = sudoers_line.replace("'", "'\"'\"'")
        _, err, code = self._run(
            f"printf '%s\\n' '{escaped}' > {sudoers_file} && chmod 440 {sudoers_file}"
        )
        if code != 0:
            raise RuntimeError(f"Failed to write {sudoers_file}: {err}")

        # Validate with visudo -c
        _, err, code = self._run(f"visudo -c -f {sudoers_file}")
        if code != 0:
            self._run(f"rm -f {sudoers_file}")
            raise RuntimeError(f"sudoers syntax check failed: {err}")

        return f"Wrote {sudoers_file}: {HOMEPILOT_USER} may sudo {docker_path} without password"

    def _step_setup_dirs(self) -> str:
        _, err, code = self._run(f"mkdir -p {TRUENAS_HOMEPILOT_DIR}")
        if code != 0:
            raise RuntimeError(f"mkdir failed for {TRUENAS_HOMEPILOT_DIR}: {err}")
        self._run(f"chown {HOMEPILOT_USER} {TRUENAS_HOMEPILOT_DIR}")
        return f"Created {TRUENAS_HOMEPILOT_DIR}/, owned by {HOMEPILOT_USER}"

    def _step_write_state(self) -> str:
        from homepilot.services.remote_state import RemoteStateService
        state_path = f"{TRUENAS_HOMEPILOT_DIR}/state.yaml"
        state_svc = RemoteStateService(self._ssh, host_key=self._host.host, state_path=state_path)
        state = state_svc.read()
        state_svc.write(state)
        return f"State file written to {self._host.host}:{state_path}"

    def _step_verify(self) -> str:
        from homepilot.models import ServerConfig
        test_server = ServerConfig(
            host=self._host.host,
            user=HOMEPILOT_USER,
            ssh_key=getattr(self._host, "ssh_key", ""),
            docker_cmd=self._host.docker_cmd,
        )
        test_ssh = SSHService(test_server)
        try:
            test_ssh.connect()
            # TrueNAS docker runs as sudo docker
            docker_cmd = self._host.docker_cmd
            out, err, code = test_ssh.run_command(f"{docker_cmd} ps --format '{{{{.Names}}}}' 2>&1 | head -5")
            test_ssh.close()
            if code != 0:
                raise RuntimeError(
                    f"'{HOMEPILOT_USER}' connected via SSH but '{docker_cmd} ps' failed — "
                    f"check sudoers configuration. Error: {err or out}"
                )
            containers = out.strip() or "(none running)"
            return f"'{HOMEPILOT_USER}' can SSH and run {docker_cmd}. Running: {containers}"
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

    def _emit(self, line: str) -> None:
        if self._line_cb:
            self._line_cb(line)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def make_bootstrap_service(
    host_config: "ProxmoxHostConfig | TrueNASHostConfig",
    *,
    root_user: str = "root",
    line_callback: LineCallback | None = None,
) -> "BootstrapService | TrueNASBootstrapService":
    """Return the correct bootstrap service for the given host type."""
    from homepilot.models import TrueNASHostConfig
    if isinstance(host_config, TrueNASHostConfig):
        # Default root_user to the configured user (e.g. 'neil') rather than 'root'
        effective_user = root_user if root_user != "root" else host_config.user
        return TrueNASBootstrapService(
            host_config, root_user=effective_user, line_callback=line_callback
        )
    return BootstrapService(host_config, root_user=root_user, line_callback=line_callback)
