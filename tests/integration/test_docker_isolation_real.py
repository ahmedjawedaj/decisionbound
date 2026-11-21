"""Does the Docker sandbox really have the isolation its docstring claims?

``test_docker_real_optional.py`` proves that a command runs inside a container.
This file probes the container from the inside, one property per test, with the
real daemon and a real image. A probe exits 0 when the property holds. The
``control`` tests run the same probe with that protection absent and expect it
to fail, so a probe that cannot tell the difference would be caught.

Run with ``DBOUND_DOCKER_TEST_IMAGE=python:3.12-slim`` after pulling the image.
Skips without Docker or an image, like the other real Docker tests. CI requires
that none of these skip.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import uuid

import pytest

from decisionbound.code_models import Workspace
from decisionbound.code_sandbox import DockerTestRunner

IMAGE = os.environ.get("DBOUND_DOCKER_TEST_IMAGE")
pytestmark = pytest.mark.skipif(not IMAGE or not shutil.which("docker"),
                                reason="Docker isolation probes need a pulled image and a local daemon")


def _workspace(path) -> Workspace:
    return Workspace(path=str(path), repository_root=str(path), base_commit="0" * 40,
                     run_id="code_" + uuid.uuid4().hex)


def _probe(tmp_path, code: str, *, timeout: int = 60, runner: DockerTestRunner | None = None):
    """Run ``python -c code`` in the sandbox and return the receipt."""
    return (runner or DockerTestRunner(IMAGE)).run(_workspace(tmp_path), ("python", "-c", code), timeout)


UNPRIVILEGED = ("--user", "65534:65534")


def _control(code: str, *flags: str) -> int:
    """Run the same probe in a container started with ``flags`` and no sandbox options.

    The sandbox runs as a non-root user in CI, so controls default to a non-root user too.
    A probe that only discriminates for root would pass vacuously there.
    """
    user = () if "--user" in flags else UNPRIVILEGED
    done = subprocess.run(["docker", "run", "--rm", "--pull", "never", *user, *flags, IMAGE, "python", "-c", code],
                          capture_output=True, timeout=120)
    return done.returncode


NETWORK_IS_LOOPBACK_ONLY = "import os, sys; sys.exit(0 if sorted(os.listdir('/sys/class/net')) == ['lo'] else 1)"
# The mount flag, not a permission error: an unprivileged user cannot write /etc even on a writable root.
ROOT_FILESYSTEM_IS_READ_ONLY = "import os, sys; sys.exit(0 if os.statvfs('/').f_flag & os.ST_RDONLY else 1)"
# Every capability set, including the bounding set. An unprivileged user has empty effective and
# permitted sets whatever the container options are, so those two alone prove nothing.
ALL_CAPABILITIES_DROPPED = (
    "import sys\n"
    "fields = dict(line.split(':', 1) for line in open('/proc/self/status').read().splitlines() if ':' in line)\n"
    "sets = ('CapInh', 'CapPrm', 'CapEff', 'CapBnd', 'CapAmb')\n"
    "sys.exit(0 if all(int(fields[k].strip(), 16) == 0 for k in sets) else 1)\n")


def test_the_container_has_no_network_interface_but_loopback(tmp_path):
    assert _probe(tmp_path, NETWORK_IS_LOOPBACK_ONLY).exit_code == 0


def test_control_a_host_networked_container_has_more_than_loopback():
    # Host networking shows the host's interfaces, so the probe must fail there.
    assert _control(NETWORK_IS_LOOPBACK_ONLY, "--network", "host") == 1


def test_the_root_filesystem_is_read_only_but_tmp_and_workspace_are_writable(tmp_path):
    assert _probe(tmp_path, ROOT_FILESYSTEM_IS_READ_ONLY).exit_code == 0
    writable = ("open('/tmp/p', 'w').write('x'); open('/workspace/from_container', 'w').write('x')")
    assert _probe(tmp_path, writable).exit_code == 0
    assert (tmp_path / "from_container").read_text() == "x"


def test_control_a_container_without_the_option_has_a_writable_root_filesystem():
    assert _control(ROOT_FILESYSTEM_IS_READ_ONLY) == 1


def test_every_capability_is_dropped_and_privileges_cannot_grow(tmp_path):
    assert _probe(tmp_path, ALL_CAPABILITIES_DROPPED).exit_code == 0
    no_new_privs = ("import sys\n"
                    "line = [x for x in open('/proc/self/status') if x.startswith('NoNewPrivs')][0]\n"
                    "sys.exit(0 if line.split()[1] == '1' else 1)\n")
    assert _probe(tmp_path, no_new_privs).exit_code == 0


def test_control_a_container_without_the_option_keeps_a_capability_bounding_set():
    assert _control(ALL_CAPABILITIES_DROPPED) == 1


@pytest.mark.skipif(not hasattr(os, "getuid") or os.getuid() == 0,
                    reason="needs a non-root caller, because the sandbox runs as the calling user")
def test_the_process_runs_as_the_calling_user_and_not_as_root(tmp_path):
    code = f"import os, sys; sys.exit(0 if (os.getuid(), os.getgid()) == ({os.getuid()}, {os.getgid()}) else 1)"
    assert _probe(tmp_path, code).exit_code == 0


SECRET_NAMES = ("DBOUND_PROBE_SECRET", "ANTHROPIC_API_KEY")
NO_SECRETS_AND_NO_DOCKER_SOCKET = (
    "import os, sys\n"
    f"leaked = [k for k in os.environ if k in {SECRET_NAMES!r}]\n"
    "sys.exit(0 if not leaked and not os.path.exists('/var/run/docker.sock') else 1)\n")


def test_host_secrets_and_the_docker_socket_are_not_visible(tmp_path, monkeypatch):
    for name in SECRET_NAMES:
        monkeypatch.setenv(name, "must-not-reach-the-container")
    assert _probe(tmp_path, NO_SECRETS_AND_NO_DOCKER_SOCKET).exit_code == 0


def test_control_an_explicit_env_value_or_socket_mount_is_seen_by_the_probe():
    assert _control(NO_SECRETS_AND_NO_DOCKER_SOCKET, "-e", "DBOUND_PROBE_SECRET=x") == 1
    assert _control(NO_SECRETS_AND_NO_DOCKER_SOCKET, "-v", "/var/run/docker.sock:/var/run/docker.sock") == 1


# Mount points the daemon always adds from its own files, and file systems that are not disk backed.
# A bind of a tmpfs-backed host directory (for example /dev/shm) looks like tmpfs here and is not detected.
# The runner's argument list is checked separately in tests/security/test_code_sandbox.py (issue 20, still open).
DAEMON_FILE_MOUNTS = ("/etc/hostname", "/etc/hosts", "/etc/resolv.conf")
NO_HOST_PATH_IS_MOUNTED_EXCEPT_THE_WORKSPACE = (
    "import sys\n"
    f"daemon = {DAEMON_FILE_MOUNTS!r}\n"
    "pseudo = {'proc', 'sysfs', 'tmpfs', 'devpts', 'mqueue', 'cgroup', 'cgroup2', 'devtmpfs', 'overlay', 'shm'}\n"
    "extra = []\n"
    "for line in open('/proc/self/mountinfo'):\n"
    "    head, tail = line.split(' - ')\n"
    "    point, fstype = head.split()[4], tail.split()[0]\n"
    "    if fstype not in pseudo and point not in ('/', '/workspace') and point not in daemon:\n"
    "        extra.append(point)\n"
    "sys.exit(0 if not extra else 1)\n")


def test_no_disk_backed_host_path_is_mounted_except_the_workspace(tmp_path):
    (tmp_path / "marker.txt").write_text("visible")
    assert _probe(tmp_path, NO_HOST_PATH_IS_MOUNTED_EXCEPT_THE_WORKSPACE).exit_code == 0
    assert _probe(tmp_path, "import sys; sys.exit(0 if open('/workspace/marker.txt').read() == 'visible' else 1)").exit_code == 0


def test_control_an_extra_bind_mount_is_seen_by_the_probe(tmp_path):
    assert _control(NO_HOST_PATH_IS_MOUNTED_EXCEPT_THE_WORKSPACE, "-v", f"{tmp_path}:/hostdir") == 1


def test_the_pids_limit_stops_a_fork_bomb(tmp_path):
    code = ("import errno, os, sys, time\n"
            "stopped = False\n"
            "for _ in range(300):\n"
            "    try:\n"
            "        if os.fork() == 0:\n"
            "            time.sleep(20)\n"
            "            os._exit(0)\n"
            "    except OSError as error:\n"
            "        stopped = error.errno == errno.EAGAIN\n"
            "        break\n"
            "sys.exit(0 if stopped else 1)\n")
    runner = DockerTestRunner(IMAGE, pids=16)
    assert _probe(tmp_path, code, runner=runner).exit_code == 0


def test_the_memory_limit_kills_a_process_that_allocates_past_it(tmp_path):
    runner = DockerTestRunner(IMAGE, memory_mb=128)
    small = "b = bytearray(32 * 1024 * 1024); b[::4096] = b'x' * len(b[::4096])"
    big = "b = bytearray(400 * 1024 * 1024); b[::4096] = b'x' * len(b[::4096])"
    assert _probe(tmp_path, small, runner=runner).exit_code == 0
    outcome = _probe(tmp_path, big, runner=runner)
    assert outcome.exit_code == 137, "allocating three times the limit must end in the kernel's OOM kill"


def test_a_command_that_runs_past_its_timeout_is_killed_and_its_container_removed(tmp_path):
    import hashlib
    workspace = _workspace(tmp_path)
    command = ("python", "-c", "import time; time.sleep(120)")
    outcome = DockerTestRunner(IMAGE).run(workspace, command, 2)
    assert outcome.timed_out is True and outcome.exit_code is None
    # The runner names the container from the run id and the command, so look for exactly that one.
    name = "dbound-" + workspace.run_id[5:] + "-" + hashlib.sha256("\0".join(command).encode()).hexdigest()[:8]
    listed = subprocess.run(["docker", "ps", "-a", "--filter", f"name={name}", "--format", "{{.Names}}"],
                            capture_output=True, text=True, timeout=30)
    assert listed.stdout.strip() == "", "the timed out container was left behind"


CPU_QUOTA = (
    "import sys\n"
    "try:\n"
    "    quota, period = open('/sys/fs/cgroup/cpu.max').read().split()\n"
    "    ratio = None if quota == 'max' else int(quota) / int(period)\n"
    "except FileNotFoundError:\n"
    "    quota = int(open('/sys/fs/cgroup/cpu/cpu.cfs_quota_us').read())\n"
    "    period = int(open('/sys/fs/cgroup/cpu/cpu.cfs_period_us').read())\n"
    "    ratio = None if quota < 0 else quota / period\n"
    "sys.exit(0 if ratio == EXPECTED else 1)\n")


def test_the_cpu_ceiling_is_applied(tmp_path):
    runner = DockerTestRunner(IMAGE, cpus=0.5)
    assert _probe(tmp_path, CPU_QUOTA.replace("EXPECTED", "0.5"), runner=runner).exit_code == 0
    # Control: the same probe expecting an unlimited container fails inside the sandbox.
    assert _probe(tmp_path, CPU_QUOTA.replace("EXPECTED", "None"), runner=runner).exit_code == 1
