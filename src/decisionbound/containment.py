"""Worker-process containment: hard deadlines, tree termination and orphan cleanup.

A :class:`ContainedWorker` runs each task in a real child process that the host
can kill, together with every descendant that stayed in the child's process
group (POSIX) or job object (Windows). Deadlines, cancellation, leadership loss
and output floods end in a confirmed termination, or in an explicit
``ContainmentFailure`` that the journal turns into a blocked task.

What this is not: a security sandbox. Descendants that call ``setsid`` or
``setpgid`` escape a POSIX group. Windows processes that break away from the job
or start through WMI or a service escape the job. There is no filesystem,
network, credential or syscall isolation. Use the Docker coding sandbox for
that. See ``site_docs/worker-containment.md`` for the full list.

Only the Linux code paths of this module were executed by its authors. The
macOS and Windows paths follow the platform documentation and have not been
verified. A passing CI matrix would be the first evidence, not this file.
"""
from __future__ import annotations

from dataclasses import dataclass
import functools
import hashlib
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import threading
import time
from typing import Any, Mapping, Sequence
import uuid

from .storage import SQLiteRepository
from .workflows import (ExecutionControl, TaskAssignment, WorkerReceipt, WorkflowJournal,
                        WorkflowWorker)

_IS_NT = os.name == "nt"
_STRONG_LEVELS = frozenset({"posix_session", "windows_job"})
_LIMIT_NAMES = ("max_cpu_seconds", "max_memory_mib", "max_open_files", "max_processes")
_CONFIRM_SECONDS = 5.0
_JOB_NAME_PREFIX = "Local\\dbound-"
_CONTAINMENT_ID = re.compile(r"[A-Za-z0-9_]{1,64}")
_POLL_SECONDS = 0.02


class ContainmentError(RuntimeError):
    """Base class for failures of worker-process containment."""


class WorkerDeadlineExceeded(ContainmentError):
    """The worker ran past its hard deadline and its process tree was killed."""


class WorkerCancelled(ContainmentError):
    """The host cancelled the worker (revoked claim, stop, or lost leadership)."""


class WorkerCrashed(ContainmentError):
    """The worker exited abnormally, or could not be started."""


class WorkerOutputInvalid(ContainmentError):
    """The worker's output was missing, malformed or larger than allowed."""


class ContainmentFailure(ContainmentError):
    """Containment was refused, or termination of the process tree could not be confirmed."""


def _bounded_int(name: str, value: Any, low: int, high: int) -> int | None:
    """Validate an optional integer limit. Reject bools and out-of-range values."""
    if value is None:
        return None
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{name} must be an integer between {low} and {high}")
    return value


@dataclass(frozen=True)
class ResourceLimits:
    """Optional per-worker resource ceilings. ``None`` means not requested.

    ``max_output_bytes`` is always enforced by the host while reading the
    worker's single receipt line. The other limits are applied by the operating
    system. A requested limit that this host cannot enforce makes a strong
    containment request fail closed instead of silently running unlimited.
    """
    max_cpu_seconds: int | None = None
    max_memory_mib: int | None = None
    max_open_files: int | None = None
    max_processes: int | None = None
    max_output_bytes: int = 65536

    def __post_init__(self) -> None:
        _bounded_int("max_cpu_seconds", self.max_cpu_seconds, 1, 86400)
        _bounded_int("max_memory_mib", self.max_memory_mib, 16, 1048576)
        _bounded_int("max_open_files", self.max_open_files, 8, 1048576)
        _bounded_int("max_processes", self.max_processes, 1, 65535)
        if _bounded_int("max_output_bytes", self.max_output_bytes, 1, 16 * 1024 * 1024) is None:
            raise ValueError("max_output_bytes is required")

    def requested(self) -> frozenset[str]:
        """Names of the optional limits that were actually requested."""
        return frozenset(n for n in _LIMIT_NAMES if getattr(self, n) is not None)


@dataclass(frozen=True)
class ContainmentCapabilities:
    """What this host can guarantee: a containment ``level`` and enforced limit names.

    Levels are ``posix_session`` (own session and process group, killed as a
    group), ``windows_job`` (job object with kill on close) and
    ``windows_tree_best_effort`` (taskkill of the process tree, which misses
    re-parented descendants). Only the first two count as ``strong``.
    """
    level: str
    enforced_limits: frozenset[str] = frozenset()

    @property
    def strong(self) -> bool:
        """True when the level can reliably terminate the whole tree."""
        return self.level in _STRONG_LEVELS


@dataclass(frozen=True)
class ProcessIdentity:
    """A process id plus a start token that stays unique across pid reuse."""
    pid: int
    start_token: str


# ---------------------------------------------------------------------------
# Process identity
# ---------------------------------------------------------------------------
@functools.lru_cache(maxsize=1)
def _boot_id() -> str:
    """Return the Linux boot id so tokens cannot match across reboots."""
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    except OSError:
        return "unknown-boot"


def _linux_stat_fields(pid: int) -> list[str] | None:
    """Parse /proc/<pid>/stat after the command name. None when the process is gone."""
    try:
        text = Path(f"/proc/{pid}/stat").read_text()
    except (OSError, ValueError):
        return None
    return text[text.rindex(")") + 2:].split()


def _linux_identity(pid: int) -> ProcessIdentity | None:
    """Identity from stat field 22 (start time) plus boot id. Zombies count as dead."""
    fields = _linux_stat_fields(pid)
    if not fields or fields[0] in ("Z", "X"):
        return None
    return ProcessIdentity(pid, f"{_boot_id()}:{fields[19]}")


def _ps_identity(pid: int) -> ProcessIdentity | None:
    """Portable fallback using ``ps`` with a fixed locale. Zombies count as dead."""
    try:
        done = subprocess.run(["ps", "-o", "stat=", "-o", "lstart=", "-p", str(pid)],
                              capture_output=True, text=True, timeout=5,
                              env={"LC_ALL": "C", "PATH": os.environ.get("PATH", "")})
    except (OSError, subprocess.SubprocessError):
        return None
    line = done.stdout.strip()
    if done.returncode != 0 or not line or line.startswith("Z"):
        return None
    return ProcessIdentity(pid, "ps:" + " ".join(line.split()[1:]))


def _darwin_identity(pid: int) -> ProcessIdentity | None:
    """macOS start time through ``proc_pidinfo(PROC_PIDTBSDINFO)``, else ``ps``."""
    try:
        import ctypes
        import ctypes.util

        class BsdInfo(ctypes.Structure):
            _fields_ = [("pbi_flags", ctypes.c_uint32), ("pbi_status", ctypes.c_uint32),
                        ("pbi_xstatus", ctypes.c_uint32), ("pbi_pid", ctypes.c_uint32),
                        ("pbi_ppid", ctypes.c_uint32), ("pbi_uid", ctypes.c_uint32),
                        ("pbi_gid", ctypes.c_uint32), ("pbi_ruid", ctypes.c_uint32),
                        ("pbi_rgid", ctypes.c_uint32), ("pbi_svuid", ctypes.c_uint32),
                        ("pbi_svgid", ctypes.c_uint32), ("rfu_1", ctypes.c_uint32),
                        ("pbi_comm", ctypes.c_char * 16), ("pbi_name", ctypes.c_char * 32),
                        ("pbi_nfiles", ctypes.c_uint32), ("pbi_pgid", ctypes.c_uint32),
                        ("pbi_pjobc", ctypes.c_uint32), ("e_tdev", ctypes.c_uint32),
                        ("e_tpgid", ctypes.c_uint32), ("pbi_nice", ctypes.c_int32),
                        ("pbi_start_tvsec", ctypes.c_uint64), ("pbi_start_tvusec", ctypes.c_uint64)]

        libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
        info = BsdInfo()
        size = libc.proc_pidinfo(pid, 3, 0, ctypes.byref(info), ctypes.sizeof(info))
        if size == ctypes.sizeof(info) and info.pbi_pid == pid:
            if info.pbi_status == 5:  # SZOMB
                return None
            return ProcessIdentity(pid, f"darwin:{info.pbi_start_tvsec}.{info.pbi_start_tvusec}")
        if size == 0 and ctypes.get_errno() == 3:  # ESRCH
            return None
    except Exception:
        pass
    return _ps_identity(pid)


def _windows_identity(pid: int) -> ProcessIdentity | None:
    """Windows identity from the process creation FILETIME (``GetProcessTimes``)."""
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return None
    try:
        code = wintypes.DWORD()
        if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value != 259:
            return None  # exited (259 is STILL_ACTIVE)
        created, exited, kernel_time, user_time = (wintypes.FILETIME() for _ in range(4))
        if not kernel.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited),
                                      ctypes.byref(kernel_time), ctypes.byref(user_time)):
            return None
        return ProcessIdentity(pid, f"win:{(created.dwHighDateTime << 32) | created.dwLowDateTime}")
    finally:
        kernel.CloseHandle(handle)


def process_identity(pid: int) -> ProcessIdentity | None:
    """Return the live identity of ``pid``, or None if it is gone or only a zombie."""
    if type(pid) is not int or pid <= 0:
        return None
    if _IS_NT:
        return _windows_identity(pid)
    if sys.platform.startswith("linux"):
        return _linux_identity(pid)
    if sys.platform == "darwin":
        return _darwin_identity(pid)
    return _ps_identity(pid)


def _identity_matches(pid: int, token: str | None) -> bool:
    """True only when ``pid`` is alive and still carries the recorded start token."""
    live = process_identity(pid)
    return live is not None and token is not None and live.start_token == token


# ---------------------------------------------------------------------------
# POSIX process groups
# ---------------------------------------------------------------------------
def _group_alive(pgid: int, *, leader_reaped: bool) -> bool:
    """True when any non-zombie process still belongs to process group ``pgid``.

    On Linux the answer comes from /proc, because containers without an init
    process leave killed descendants as unreaped zombies that ``killpg(pgid, 0)``
    would keep reporting. Elsewhere ``killpg`` is used, and ``EPERM`` is read as
    a zombie only group once the leader has been reaped.
    """
    if sys.platform.startswith("linux"):
        try:
            names = os.listdir("/proc")
        except OSError:
            return True  # cannot prove it dead
        for name in names:
            if not name.isdigit():
                continue
            fields = _linux_stat_fields(int(name))
            if fields and fields[2] == str(pgid) and fields[0] not in ("Z", "X"):
                return True
        return False
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # macOS answers EPERM for a group that holds only zombies, but also for
        # one we may not signal. Ask ``ps`` which members can still run code.
        listing = _ps_group_states(pgid)
        if listing is not None:
            return any(not state.startswith("Z") for state in listing)
        return not leader_reaped
    return True


def _ps_group_states(pgid: int) -> list[str] | None:
    """``ps`` state of every member of ``pgid``, or None when ``ps`` cannot answer."""
    try:
        done = subprocess.run(["ps", "-A", "-o", "pgid=,stat="], capture_output=True, text=True, timeout=10,
                              env={"LC_ALL": "C", "PATH": os.environ.get("PATH", "/usr/bin:/bin")})
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    states = []
    for line in done.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] == str(pgid):
            states.append(parts[1])
    return states


def _signal_group(pgid: int, sig: int) -> bool:
    """Signal a whole process group. Returns False when the group no longer exists."""
    try:
        os.killpg(pgid, sig)
    except ProcessLookupError:
        return False
    except PermissionError:
        # macOS returns EPERM for a group that holds only zombies. On Linux an
        # EPERM is a real error and must surface (the reaper maps it to
        # ``unconfirmed``).
        if sys.platform.startswith("linux"):
            raise
        return False
    return True


# ---------------------------------------------------------------------------
# Launcher: a stdlib-only shim run as ``python -I -c`` so that resource limits
# are applied in the child itself. This avoids ``preexec_fn``, which is unsafe in
# a threaded coordinator, and avoids importing the decisionbound package.
# ---------------------------------------------------------------------------
_LAUNCHER_SOURCE = r'''
import json, os, sys
cfg = json.loads(sys.argv[1])
argv = sys.argv[3:]
if os.name == "posix":
    import resource
    def cap(which, soft, hard=None):
        try:
            resource.setrlimit(which, (soft, soft if hard is None else hard))
        except (ValueError, OSError):
            os._exit(126)
    cap(resource.RLIMIT_CORE, 0)
    if cfg.get("cpu"):
        cap(resource.RLIMIT_CPU, cfg["cpu"], cfg["cpu"] + 1)
    if cfg.get("nofile"):
        cap(resource.RLIMIT_NOFILE, cfg["nofile"])
    if cfg.get("mem_mib") and sys.platform.startswith("linux"):
        cap(resource.RLIMIT_AS, cfg["mem_mib"] * 1024 * 1024)
    try:
        os.execvp(argv[0], argv)
    except OSError:
        os._exit(127)
else:
    import subprocess
    os.read(0, 1)
    try:
        sys.exit(subprocess.call(argv))
    except OSError:
        sys.exit(127)
'''


def probe_capabilities() -> ContainmentCapabilities:
    """Report this host's containment level and which resource limits it enforces."""
    if _IS_NT:
        try:
            job = _WindowsJob.create(ResourceLimits(), name=None)
        except OSError:
            return ContainmentCapabilities("windows_tree_best_effort", frozenset())
        job.close()
        return ContainmentCapabilities(
            "windows_job", (frozenset(_LIMIT_NAMES) - {"max_open_files"}) | {"max_output_bytes"})
    enforced = {"max_cpu_seconds", "max_open_files", "max_output_bytes"}
    if sys.platform.startswith("linux"):
        enforced.add("max_memory_mib")
    return ContainmentCapabilities("posix_session", frozenset(enforced))


def _job_name(containment_id: str) -> str:
    """Name of the job object for one contained worker, in the session-local namespace."""
    return _JOB_NAME_PREFIX + containment_id


class _WindowsJob:
    """Windows job object with kill-on-close, created through ctypes.

    Written from the Win32 documentation. It could not be executed on the
    Linux machine where it was authored.
    """

    def __init__(self, handle: Any, kernel: Any):
        self._handle, self._kernel = handle, kernel

    @classmethod
    def create(cls, limits: ResourceLimits, name: str | None) -> "_WindowsJob":
        """Create the job and apply limits. Raises OSError when Windows refuses."""
        import ctypes
        from ctypes import wintypes

        class BasicLimits(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class IoCounters(ctypes.Structure):
            _fields_ = [(n, ctypes.c_uint64) for n in ("ReadOps", "WriteOps", "OtherOps",
                                                       "ReadBytes", "WriteBytes", "OtherBytes")]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", BasicLimits), ("IoInfo", IoCounters),
                        ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        handle = kernel.CreateJobObjectW(None, name)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        info = ExtendedLimits()
        flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if limits.max_processes:
            flags |= 0x8  # ACTIVE_PROCESS
            # +1: the launcher shim stays alive as the worker's parent and counts too.
            info.BasicLimitInformation.ActiveProcessLimit = limits.max_processes + 1
        if limits.max_cpu_seconds:
            flags |= 0x4  # JOB_TIME, in 100 ns units
            info.BasicLimitInformation.PerJobUserTimeLimit = limits.max_cpu_seconds * 10_000_000
        if limits.max_memory_mib:
            flags |= 0x200  # JOB_MEMORY
            info.JobMemoryLimit = limits.max_memory_mib * 1024 * 1024
        info.BasicLimitInformation.LimitFlags = flags
        if not kernel.SetInformationJobObject(handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
            error = ctypes.WinError(ctypes.get_last_error())
            kernel.CloseHandle(handle)
            raise error
        return cls(handle, kernel)

    @classmethod
    def open_existing(cls, name: str) -> "_WindowsJob | None":
        """Open a live job by name with query and terminate rights only.

        Returns None when no job has that name, which with kill-on-close means
        its last handle closed and every member was killed. Raises OSError for
        any other failure. Written from the ``OpenJobObjectW`` documentation:
        the shape is unit tested against a fake kernel32, but it has not been
        run on Windows by its author.
        """
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenJobObjectW.restype = wintypes.HANDLE
        kernel.OpenJobObjectW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
        handle = kernel.OpenJobObjectW(0x0004 | 0x0008, False, name)  # JOB_OBJECT_QUERY | JOB_OBJECT_TERMINATE
        if not handle:
            code = ctypes.get_last_error()
            if code == 2:  # ERROR_FILE_NOT_FOUND
                return None
            raise OSError(code, "OpenJobObjectW failed")
        return cls(handle, kernel)

    def assign(self, process_handle: int) -> None:
        """Place a process in the job. Raises OSError (for example ACCESS_DENIED) on failure."""
        import ctypes
        from ctypes import wintypes
        self._kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        if not self._kernel.AssignProcessToJobObject(self._handle, process_handle):
            raise ctypes.WinError(ctypes.get_last_error())

    def terminate(self) -> None:
        """Kill every process in the job at once. There is no graceful phase."""
        from ctypes import wintypes
        self._kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        self._kernel.TerminateJobObject(self._handle, 1)

    def active_processes(self) -> int:
        """Number of processes still in the job (``JobObjectBasicAccountingInformation``)."""
        import ctypes
        from ctypes import wintypes

        class Accounting(ctypes.Structure):
            _fields_ = [("TotalUserTime", ctypes.c_int64), ("TotalKernelTime", ctypes.c_int64),
                        ("ThisPeriodTotalUserTime", ctypes.c_int64), ("ThisPeriodTotalKernelTime", ctypes.c_int64),
                        ("TotalPageFaultCount", wintypes.DWORD), ("TotalProcesses", wintypes.DWORD),
                        ("ActiveProcesses", wintypes.DWORD), ("TotalTerminatedProcesses", wintypes.DWORD)]
        data = Accounting()
        self._kernel.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                                           wintypes.DWORD, ctypes.c_void_p]
        if not self._kernel.QueryInformationJobObject(self._handle, 1, ctypes.byref(data),
                                                      ctypes.sizeof(data), None):
            return 1  # cannot prove empty
        return int(data.ActiveProcesses)

    def close(self) -> None:
        """Close the handle. With kill-on-close this also kills anything still inside."""
        if self._handle:
            from ctypes import wintypes
            self._kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            self._kernel.CloseHandle(self._handle)
            self._handle = None


class ContainedProcess:
    """One launched worker process and the means to kill its whole tree.

    Create it with :meth:`spawn`. The worker receives its assignment on stdin
    (:meth:`feed`) and returns one receipt line on stdout (:meth:`start_reader`,
    :meth:`output`). stderr is discarded. The leader's exit status is observed
    without reaping it on POSIX, so the group is killed before the pid can be
    recycled.
    """

    def __init__(self, popen: subprocess.Popen, *, level: str, job: _WindowsJob | None):
        self._popen = popen
        self.pid = popen.pid
        self.pgid = popen.pid
        self.level = level
        self._job = job
        self._returncode: int | None = None
        self._confirmed: bool | None = None
        self._buffer = bytearray()
        self._limit = 0
        self._overflow = threading.Event()
        self._reader: threading.Thread | None = None
        self._writer: threading.Thread | None = None

    @classmethod
    def spawn(cls, argv: Sequence[str], *, env: Mapping[str, str], cwd: str | None = None,
              limits: ResourceLimits | None = None, containment_id: str | None = None,
              require_strong: bool = True) -> "ContainedProcess":
        """Start ``argv`` through the launcher in its own session or job object.

        Raises :class:`ContainmentFailure` when ``require_strong`` is set and the
        host can only offer best-effort containment. Raises ``OSError`` when the
        process cannot be started at all.
        """
        limits = limits or ResourceLimits()
        config = json.dumps({"cpu": limits.max_cpu_seconds, "nofile": limits.max_open_files,
                             "mem_mib": limits.max_memory_mib})
        command = [sys.executable, "-I", "-c", _LAUNCHER_SOURCE, config, "--", *argv]
        pipes = dict(stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                     env=dict(env), cwd=cwd, close_fds=True)
        if not _IS_NT:
            return cls(subprocess.Popen(command, start_new_session=True, **pipes),
                       level="posix_session", job=None)
        name = _job_name(containment_id or uuid.uuid4().hex)
        create_flags = 0x00000200 | 0x08000000  # CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
        job: _WindowsJob | None
        try:
            job = _WindowsJob.create(limits, name)
        except OSError:
            job = None
        popen = subprocess.Popen(command, creationflags=create_flags, **pipes)
        if job is not None:
            try:
                job.assign(int(popen._handle))  # type: ignore[attr-defined]
            except OSError:
                job.close()
                job = None
        if job is None:
            if require_strong:
                popen.kill()
                popen.wait()
                raise ContainmentFailure("Windows job object unavailable; refusing weak containment")
            level = "windows_tree_best_effort"
        else:
            level = "windows_job"
        try:  # release the one byte gate: no descendant can exist outside the job
            popen.stdin.write(b"\x01")  # type: ignore[union-attr]
            popen.stdin.flush()  # type: ignore[union-attr]
        except OSError:
            pass
        return cls(popen, level=level, job=job)

    @property
    def returncode(self) -> int | None:
        """Exit status once the leader has exited and been reaped, else None."""
        return self._returncode

    @property
    def confirmed(self) -> bool | None:
        """True when the whole tree was proven dead, False when not, None while running."""
        return self._confirmed

    @property
    def overflowed(self) -> bool:
        """True once the worker wrote more than the allowed output bytes."""
        return self._overflow.is_set()

    def feed(self, payload: bytes) -> None:
        """Write ``payload`` to the worker's stdin in a thread, then close stdin."""
        def write() -> None:
            try:
                self._popen.stdin.write(payload)  # type: ignore[union-attr]
                self._popen.stdin.flush()  # type: ignore[union-attr]
            except (OSError, ValueError):
                pass
            finally:
                try:
                    self._popen.stdin.close()  # type: ignore[union-attr]
                except (OSError, ValueError):
                    pass
        self._writer = threading.Thread(target=write, name="dbound-worker-stdin", daemon=True)
        self._writer.start()

    def start_reader(self, max_output_bytes: int) -> None:
        """Collect stdout in a thread, stopping and flagging overflow past the limit."""
        self._limit = max_output_bytes
        fd = self._popen.stdout.fileno()  # type: ignore[union-attr]

        def read() -> None:
            while True:
                try:
                    chunk = os.read(fd, 65536)
                except OSError:
                    return
                if not chunk:
                    return
                self._buffer.extend(chunk)
                if len(self._buffer) > self._limit:
                    self._overflow.set()
                    return
        self._reader = threading.Thread(target=read, name="dbound-worker-stdout", daemon=True)
        self._reader.start()

    def output(self) -> bytes:
        """Return collected stdout after the tree is dead (waits briefly for the reader)."""
        if self._reader is not None:
            self._reader.join(timeout=_CONFIRM_SECONDS)
        return bytes(self._buffer[: self._limit])

    def _leader_exited(self) -> bool:
        """Observe leader exit without reaping it where the platform allows."""
        if self._returncode is not None:
            return True
        if _IS_NT or not hasattr(os, "waitid"):
            return self._popen.poll() is not None
        try:
            info = os.waitid(os.P_PID, self.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
        except ChildProcessError:
            return True
        return info is not None and info.si_pid == self.pid

    def poll(self) -> int | None:
        """Return the exit status once the leader exited, else None.

        On first observing the exit, every remaining descendant is killed and
        the leader is reaped, so a worker can never leave a tree behind by
        exiting. Check :attr:`confirmed` for whether that was proven.
        """
        if self._returncode is not None:
            return self._returncode
        if not self._leader_exited():
            return None
        if _IS_NT:
            self._windows_kill()
        else:
            _signal_group(self.pgid, signal.SIGKILL)
        self._reap_and_confirm()
        return self._returncode

    def terminate_tree(self, grace_seconds: float = 3.0) -> bool:
        """Terminate the leader and all descendants. True only if proven dead.

        POSIX sends SIGTERM to the group, waits up to ``grace_seconds`` for the
        group to empty, then SIGKILL. Windows kills the job immediately (there is
        no catchable signal), or falls back to ``taskkill /T /F``.
        """
        if self._confirmed is not None:
            return self._confirmed
        if _IS_NT:
            self._windows_kill()
        else:
            _signal_group(self.pgid, signal.SIGTERM)
            end = time.monotonic() + max(0.0, grace_seconds)
            while time.monotonic() < end:
                if sys.platform.startswith("linux"):
                    if not _group_alive(self.pgid, leader_reaped=False):
                        break
                elif self._leader_exited():
                    break
                time.sleep(_POLL_SECONDS)
            _signal_group(self.pgid, signal.SIGKILL)
        return self._reap_and_confirm()

    def _windows_kill(self) -> None:
        """Kill the Windows tree through the job, or ``taskkill`` when there is no job."""
        if self._job is not None:
            self._job.terminate()
        else:
            subprocess.run(["taskkill", "/PID", str(self.pid), "/T", "/F"], capture_output=True)

    def _reap_and_confirm(self) -> bool:
        """Reap the leader, then wait (bounded) for the group or job to be empty."""
        end = time.monotonic() + _CONFIRM_SECONDS
        while self._returncode is None:
            try:
                self._returncode = self._popen.wait(timeout=0.1)
            except subprocess.TimeoutExpired:
                if time.monotonic() > end:
                    self._confirmed = False
                    return False
        while True:
            if _IS_NT:
                empty = (self._job.active_processes() == 0 if self._job is not None
                         else process_identity(self.pid) is None)
            else:
                empty = not _group_alive(self.pgid, leader_reaped=True)
            if empty:
                self._confirmed = True
                return True
            if time.monotonic() > end:
                self._confirmed = False
                return False
            time.sleep(_POLL_SECONDS)

    def close(self) -> None:
        """Release pipes, helper threads and the job handle. Safe to call repeatedly."""
        for stream in (self._popen.stdin, self._popen.stdout):
            try:
                if stream is not None:
                    stream.close()
            except (OSError, ValueError):
                pass
        if self._writer is not None:
            self._writer.join(timeout=1)
        if self._job is not None and self._confirmed:
            self._job.close()


# ---------------------------------------------------------------------------
# Process registry and orphan reaper
# ---------------------------------------------------------------------------
_REGISTRY_STATES = frozenset({"spawning", "running", "exited", "terminated", "gone",
                              "identity_mismatch", "unconfirmed", "spawn_failed"})
_LIVE_STATES = ("spawning", "running")


class ProcessRegistry:
    """Durable table of contained worker processes, in the same SQLite database.

    The table ``contained_processes`` is created lazily and additively, in the
    same way as ``local_coordinator_lease``. No existing table changes and the
    schema version stays 2. It stores process identity and claim references
    only: never argv, environment, task text, tokens or output.

    Write order is: intent row (``spawning``), spawn, then ``running`` with the
    pid and start token. Pass ``create=False`` for a read-and-reap view that must
    not create the table on installations that never used containment.
    """

    def __init__(self, store: SQLiteRepository, *, create: bool = True):
        self.store = store
        self._leader_generation = 0
        self._ready = False
        if create:
            self._ensure()

    def _ensure(self) -> None:
        """Create the table and index if needed (idempotent)."""
        with self.store._connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS contained_processes (
                    containment_id TEXT PRIMARY KEY, workflow_id TEXT NOT NULL, task_id TEXT NOT NULL,
                    attempt INTEGER NOT NULL, generation INTEGER NOT NULL,
                    pid INTEGER, pgid INTEGER, start_token TEXT,
                    owner_pid INTEGER NOT NULL, owner_start_token TEXT NOT NULL,
                    leader_generation INTEGER NOT NULL DEFAULT 0,
                    level TEXT NOT NULL, argv_sha256 TEXT NOT NULL, state TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT (datetime('now')), ended_at TEXT
                );
                CREATE INDEX IF NOT EXISTS contained_processes_state ON contained_processes(state);
            """)
        self._ready = True

    def _exists(self, db: Any) -> bool:
        """True when the table exists (it may not on never-contained installations)."""
        return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='contained_processes'").fetchone() is not None

    def bind_leader_generation(self, generation: int) -> None:
        """Stamp later rows with the coordinator's leadership generation (0 means none)."""
        if type(generation) is not int or generation < 0:
            raise ValueError("Invalid leadership generation")
        self._leader_generation = generation

    def record_intent(self, *, workflow_id: str, task_id: str, attempt: int, generation: int,
                      level: str, argv_sha256: str, owner: ProcessIdentity | None = None,
                      leader_generation: int | None = None) -> str:
        """Insert a ``spawning`` row before the process exists and return its id."""
        if not self._ready:
            self._ensure()
        if owner is None:
            mine = process_identity(os.getpid())
            if mine is None:
                raise ContainmentFailure("Cannot read this process's identity, so orphans could not be told apart")
            owner = mine
        cid = "cp_" + uuid.uuid4().hex
        with self.store._connection() as db:
            db.execute("""INSERT INTO contained_processes(containment_id,workflow_id,task_id,attempt,generation,
                          owner_pid,owner_start_token,leader_generation,level,argv_sha256,state)
                          VALUES (?,?,?,?,?,?,?,?,?,?,'spawning')""",
                       (cid, workflow_id, task_id, attempt, generation, owner.pid, owner.start_token,
                        self._leader_generation if leader_generation is None else leader_generation,
                        level, argv_sha256))
        return cid

    def record_started(self, containment_id: str, *, pid: int, pgid: int,
                       identity: ProcessIdentity | None) -> None:
        """Attach the pid, group id and start token and mark the row ``running``."""
        with self.store._connection() as db:
            db.execute("UPDATE contained_processes SET pid=?,pgid=?,start_token=?,state='running' WHERE containment_id=?",
                       (pid, pgid, identity.start_token if identity else "", containment_id))

    def mark(self, containment_id: str, state: str) -> None:
        """Move a row to a new state. Terminal states also stamp ``ended_at``."""
        if state not in _REGISTRY_STATES:
            raise ValueError("Invalid containment state")
        ended = "NULL" if state in _LIVE_STATES else "datetime('now')"
        with self.store._connection() as db:
            db.execute(f"UPDATE contained_processes SET state=?,ended_at={ended} WHERE containment_id=?",
                       (state, containment_id))

    def running(self) -> list[dict]:
        """Rows still believed alive (``spawning`` or ``running``), oldest first."""
        with self.store._connection() as db:
            if not self._exists(db):
                return []
            return [dict(r) for r in db.execute(
                "SELECT * FROM contained_processes WHERE state IN ('spawning','running') ORDER BY rowid")]


class OrphanReaper:
    """Clean up worker trees left behind by a dead or replaced coordinator.

    A row is an orphan when its owner process is dead, or when it was started
    under an older leadership generation (the owner then lost its fence). A tree
    is killed only when the live process at the recorded pid has the recorded
    start token, so a recycled pid is never signalled. If the leader is gone but
    its group is still alive, ownership cannot be verified: the row is marked
    ``unconfirmed``, nothing is killed, and the task is blocked. The journal
    remains the only authority over task state.
    """

    def __init__(self, registry: ProcessRegistry, journal: WorkflowJournal, *,
                 term_grace_seconds: float = 3.0):
        self.registry, self.journal = registry, journal
        self.term_grace_seconds = term_grace_seconds

    def reap(self, *, leader_generation: int) -> dict:
        """Examine every live row and return counts per outcome.

        The result keys are ``examined``, ``terminated``, ``gone``,
        ``identity_mismatch`` and ``unconfirmed``. Counts only, no identifiers.
        """
        report = {"examined": 0, "terminated": 0, "gone": 0, "identity_mismatch": 0, "unconfirmed": 0}
        for row in self.registry.running():
            owner_dead = not _identity_matches(row["owner_pid"], row["owner_start_token"])
            stale = bool(row["leader_generation"]) and row["leader_generation"] < leader_generation
            if not (owner_dead or stale):
                continue
            report["examined"] += 1
            outcome = self._examine(row)
            self.registry.mark(row["containment_id"], outcome)
            report[outcome] += 1
            event = {"terminated": "orphan_terminated", "gone": "orphan_gone",
                     "identity_mismatch": "orphan_identity_mismatch", "unconfirmed": "orphan_unconfirmed"}[outcome]
            try:
                self.journal.record_containment_event(row["workflow_id"], row["task_id"], event,
                                                      {"containment_id": row["containment_id"], "level": row["level"]})
                if outcome == "unconfirmed":
                    self.journal.block_uncontained(row["workflow_id"], row["task_id"], row["generation"])
            except Exception:
                pass  # evidence is best effort, the row state above is authoritative
        return report

    def _examine_windows(self, row: dict) -> str:
        """Windows orphan: kill through the job object when there is one, else by process id.

        A ``windows_job`` row is settled by the job, not by the leader. The leader may be
        gone while descendants live on inside the job, so ``terminated`` is returned only
        after the job reports no active processes. No job under that name means
        kill-on-close already ended every member. The name is built from the stored
        containment id only after it matches a plain token, so a tampered row cannot
        name another object. Rows below ``windows_job`` keep the ``taskkill`` path,
        which cannot see descendants that left the tree.

        Unit tested with fakes on every OS. The real Windows run is unverified.
        """
        if row["level"] == "windows_job":
            cid = row["containment_id"]
            if not isinstance(cid, str) or _CONTAINMENT_ID.fullmatch(cid) is None:
                return "identity_mismatch"
            try:
                job = _WindowsJob.open_existing(_job_name(cid))
            except OSError:
                return "unconfirmed"
            if job is None:
                live = process_identity(row["pid"])
                return "unconfirmed" if live is not None and live.start_token == row["start_token"] else "gone"
            try:
                job.terminate()
                end = time.monotonic() + _CONFIRM_SECONDS
                while job.active_processes() > 0:
                    if time.monotonic() > end:
                        return "unconfirmed"
                    time.sleep(_POLL_SECONDS)
                return "terminated"
            finally:
                job.close()
        live = process_identity(row["pid"])
        if live is None:
            return "unconfirmed"
        if live.start_token != row["start_token"]:
            return "identity_mismatch"
        subprocess.run(["taskkill", "/PID", str(row["pid"]), "/T", "/F"], capture_output=True)
        end = time.monotonic() + _CONFIRM_SECONDS
        while process_identity(row["pid"]) is not None and time.monotonic() < end:
            time.sleep(_POLL_SECONDS)
        return "unconfirmed" if process_identity(row["pid"]) is not None else "terminated"

    def _examine(self, row: dict) -> str:
        """Decide and carry out the action for one orphan row, then return its new state."""
        if row["pid"] is None:
            return "unconfirmed"  # crashed between intent and spawn record: cannot know
        if not _IS_NT and (type(row["pid"]) is not int or type(row["pgid"]) is not int or row["pid"] <= 1 or
                           row["pgid"] != row["pid"] or row["pid"] == os.getpid() or row["pgid"] == os.getpgrp()):
            return "identity_mismatch"  # never signal a group we did not start; a stored row is not trusted
        if _IS_NT:
            return self._examine_windows(row)
        live = process_identity(row["pid"])
        if live is None:
            return "unconfirmed" if _group_alive(row["pgid"], leader_reaped=True) else "gone"
        if live.start_token != row["start_token"]:
            return "identity_mismatch"
        try:
            if os.getpgid(row["pid"]) != row["pgid"]:
                return "identity_mismatch"
        except ProcessLookupError:
            return "gone"
        try:
            if not _signal_group(row["pgid"], signal.SIGTERM):
                return "gone"
            end = time.monotonic() + max(0.0, self.term_grace_seconds)
            while time.monotonic() < end and _group_alive(row["pgid"], leader_reaped=False):
                time.sleep(_POLL_SECONDS)
            _signal_group(row["pgid"], signal.SIGKILL)
        except PermissionError:
            return "unconfirmed"
        end = time.monotonic() + _CONFIRM_SECONDS
        while _group_alive(row["pgid"], leader_reaped=False):
            if time.monotonic() > end:
                return "unconfirmed"
            time.sleep(_POLL_SECONDS)
        return "terminated"


# ---------------------------------------------------------------------------
# The worker
# ---------------------------------------------------------------------------
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_RECEIPT_KEYS = frozenset({"status", "evidence_sha256", "repair_session_id", "nested_run_id", "cost_usd"})


def _reject_duplicate_keys(pairs: list) -> dict:
    keys = [k for k, _ in pairs]
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate key")
    return dict(pairs)


def _parse_receipt(raw: bytes) -> WorkerReceipt:
    """Parse the worker's single JSON receipt line strictly. Never echo the content."""
    try:
        return _parse_receipt_unchecked(raw)
    except (UnicodeDecodeError, ValueError, TypeError, RecursionError, OverflowError, MemoryError):
        raise WorkerOutputInvalid("Worker did not return exactly one valid receipt line") from None


def _parse_receipt_unchecked(raw: bytes) -> WorkerReceipt:
    lines = [x for x in raw.decode("utf-8").splitlines() if x.strip()]
    value = json.loads(lines[0], object_pairs_hook=_reject_duplicate_keys) if len(lines) == 1 else None
    if not isinstance(value, dict) or not value.keys() <= _RECEIPT_KEYS or not isinstance(value.get("status"), str):
        raise WorkerOutputInvalid("Worker did not return exactly one valid receipt line")
    cost = value.get("cost_usd", 0.0)
    texts = [value.get(k, "") for k in ("status", "evidence_sha256", "repair_session_id", "nested_run_id")]
    if (not 1 <= len(value["status"]) <= 64 or any(not isinstance(t, str) or len(t) > 128 for t in texts) or
            isinstance(cost, bool) or not isinstance(cost, (int, float)) or
            (isinstance(cost, int) and not 0 <= cost <= 10**9) or not math.isfinite(cost) or cost < 0):
        raise WorkerOutputInvalid("Worker receipt had invalid field values")
    return WorkerReceipt(status=value["status"], evidence_sha256=value.get("evidence_sha256", ""),
                         repair_session_id=value.get("repair_session_id", ""),
                         nested_run_id=value.get("nested_run_id", ""), cost_usd=float(cost))


class ContainedWorker(WorkflowWorker):
    """Run each task in a killable child process with a hard deadline.

    ``argv`` is the fixed command line, an operator decision made in code. The
    child gets the :class:`TaskAssignment` as one JSON object on stdin, with no
    owner token and no generation, and answers with one JSON receipt line on
    stdout. stderr is discarded. Its environment is an allowlist: ``PATH``
    (plus ``SYSTEMROOT``, ``TEMP`` and ``TMP`` on Windows) and the names in
    ``env_passthrough``. The child's working directory is the project path.

    The deadline is monotonic and starts at spawn, independent of the lease.
    Heartbeats continue until the deadline. The host discards any receipt that
    arrives after cancellation began. ``require_strong`` is always treated as
    true for ``code`` tasks. This class never grants write, model or network
    permission: the argv you choose decides what the child can do.

    Override guidance: subclass only to change how the argv is chosen. Do not
    bypass the registry, because crash recovery depends on its rows.
    """

    def __init__(self, argv: Sequence[str], *, registry: ProcessRegistry, deadline_seconds: float,
                 term_grace_seconds: float = 3, limits: ResourceLimits | None = None,
                 env_passthrough: Sequence[str] = (), require_strong: bool = True):
        if (not isinstance(argv, (list, tuple)) or not 1 <= len(argv) <= 64 or
                not all(isinstance(x, str) and x for x in argv)):
            raise ValueError("argv must be a non-empty list of non-empty strings")
        if not isinstance(registry, ProcessRegistry):
            raise TypeError("A ProcessRegistry is required")
        if (isinstance(deadline_seconds, bool) or not isinstance(deadline_seconds, (int, float)) or
                not 0 < deadline_seconds <= 86400):
            raise ValueError("deadline_seconds must be between 0 and 86400")
        if (isinstance(term_grace_seconds, bool) or not isinstance(term_grace_seconds, (int, float)) or
                not 0 <= term_grace_seconds <= 60):
            raise ValueError("term_grace_seconds must be between 0 and 60")
        if limits is not None and not isinstance(limits, ResourceLimits):
            raise TypeError("limits must be ResourceLimits")
        if not all(isinstance(x, str) and _ENV_NAME.fullmatch(x) for x in env_passthrough):
            raise ValueError("env_passthrough must contain environment variable names")
        self.argv = tuple(argv)
        self.registry = registry
        self.deadline_seconds = float(deadline_seconds)
        self.term_grace_seconds = float(term_grace_seconds)
        self.limits = limits or ResourceLimits()
        self.env_passthrough = tuple(env_passthrough)
        self.require_strong = bool(require_strong)
        self._journal = WorkflowJournal(registry.store)

    def _environment(self) -> dict[str, str]:
        """Build the child environment from an allowlist, never from the parent's full env."""
        names = ["PATH"] + (["SYSTEMROOT", "TEMP", "TMP"] if _IS_NT else []) + list(self.env_passthrough)
        return {n: os.environ[n] for n in names if n in os.environ}

    def execute(self, lease: TaskAssignment) -> WorkerReceipt:
        """Run the task with a private, never-cancelled control (used outside a supervisor)."""
        control = ExecutionControl(workflow_id=lease.workflow_id, task_id=lease.task.task_id,
                                   attempt=lease.attempt, generation=0)
        return self.execute_controlled(lease, control)

    def _refuse(self, assignment: TaskAssignment, reason: str) -> ContainmentFailure:
        """Record a refusal event and build the failure to raise. Nothing was spawned."""
        try:
            self._journal.record_containment_event(assignment.workflow_id, assignment.task.task_id,
                                                   "containment_refused", {"reason": reason})
        except Exception:
            pass
        return ContainmentFailure(reason)

    def execute_controlled(self, assignment: TaskAssignment, control: ExecutionControl) -> WorkerReceipt:
        """Spawn, supervise and tear down the child. Returns its receipt or raises.

        Raises :class:`WorkerDeadlineExceeded`, :class:`WorkerCancelled`,
        :class:`WorkerCrashed`, :class:`WorkerOutputInvalid` or
        :class:`ContainmentFailure`. Whenever it returns or raises after a spawn,
        the child's whole tree has been killed, or the failure says it could not
        be proven.
        """
        caps = probe_capabilities()
        strong_needed = self.require_strong or assignment.task.kind == "code"
        if strong_needed:
            if not caps.strong:
                raise self._refuse(assignment, "strong_containment_unavailable")
            if not self.limits.requested() <= caps.enforced_limits:
                raise self._refuse(assignment, "requested_limit_not_enforceable")
        payload = json.dumps({
            "workflow_id": assignment.workflow_id, "project_id": assignment.project_id,
            "project_path": str(assignment.project_path), "task": assignment.task.to_dict(),
            "attempt": assignment.attempt, "reserved_cost_usd": assignment.reserved_cost_usd,
            "handoffs": [{"task_id": h.task_id, "kind": h.kind, "receipt_sha256": h.receipt_sha256,
                          "verified": h.verified} for h in assignment.handoffs]}).encode("utf-8")
        cid = self.registry.record_intent(
            workflow_id=assignment.workflow_id, task_id=assignment.task.task_id,
            attempt=assignment.attempt, generation=control.generation, level=caps.level,
            argv_sha256=hashlib.sha256(json.dumps(list(self.argv)).encode()).hexdigest())
        try:
            proc = ContainedProcess.spawn(self.argv, env=self._environment(),
                                          cwd=str(assignment.project_path), limits=self.limits,
                                          containment_id=cid, require_strong=strong_needed)
        except ContainmentFailure:
            self.registry.mark(cid, "spawn_failed")
            raise
        except OSError as exc:
            self.registry.mark(cid, "spawn_failed")
            raise WorkerCrashed("Worker process could not be started") from exc
        state, outcome, deadline_at = "unconfirmed", None, time.monotonic() + self.deadline_seconds
        try:
            self.registry.record_started(cid, pid=proc.pid, pgid=proc.pgid, identity=process_identity(proc.pid))
            try:
                self._journal.record_containment_event(
                    assignment.workflow_id, assignment.task.task_id, "worker_started",
                    {"containment_id": cid, "level": proc.level, "attempt": assignment.attempt,
                     "deadline_seconds": self.deadline_seconds,
                     "enforced": sorted((caps.enforced_limits & (self.limits.requested() | {"max_output_bytes"}))),
                     "max_output_bytes": self.limits.max_output_bytes})
            except Exception:
                pass
            proc.start_reader(self.limits.max_output_bytes)
            proc.feed(payload)
            while proc.poll() is None:
                if control.cancel.is_set():
                    outcome = "cancel"
                elif proc.overflowed:
                    outcome = "overflow"
                elif time.monotonic() >= deadline_at:
                    outcome = "deadline"
                if outcome:
                    proc.terminate_tree(self.term_grace_seconds)
                    break
                control.cancel.wait(_POLL_SECONDS)
            output = proc.output()
            confirmed = bool(proc.confirmed)
            state = "unconfirmed" if not confirmed else "terminated" if outcome else "exited"
            code = proc.returncode
        except BaseException:
            proc.terminate_tree(0)  # never leave a tree behind on any host-side error
            state = "terminated" if proc.confirmed else "unconfirmed"
            raise
        finally:
            self.registry.mark(cid, state)
            proc.close()
        event = "worker_terminated" if outcome else "worker_exited"
        try:
            self._journal.record_containment_event(
                assignment.workflow_id, assignment.task.task_id, event,
                {"containment_id": cid, "outcome": outcome or "exited", "exit_code": code, "confirmed": confirmed})
        except Exception:
            pass
        if not confirmed:
            raise ContainmentFailure("Worker process tree termination could not be confirmed")
        if outcome == "cancel" or control.cancel.is_set():
            raise WorkerCancelled(control.reason or "cancelled")
        if outcome == "deadline":
            raise WorkerDeadlineExceeded(f"Worker exceeded its {self.deadline_seconds:g}s deadline")
        if outcome == "overflow" or proc.overflowed:
            raise WorkerOutputInvalid("Worker output exceeded the allowed size")
        if code != 0:
            raise WorkerCrashed(f"Worker exited with status {code}")
        return _parse_receipt(output)
