from __future__ import annotations

import re
from typing import Any

from neural.exceptions import SyscallSecurityViolation
from neural.logger import get_logger

logger = get_logger("ancos.security.profiles")

# High-risk Linux system calls prohibited under Zero-Trust Workstation bounds
SECCOMP_RESTRICTED_SYSCALLS: list[str] = [
    "ptrace",
    "bpf",
    "clone3",
    "kexec_load",
    "kexec_file_load",
    "reboot",
    "swapon",
    "swapoff",
    "sysfs",
    "syslog",
    "process_vm_readv",
    "process_vm_writev",
    "userfaultfd",
    "mount",
    "umount2",
    "pivot_root",
    "setns",
    "unshare",
    "acct",
    "add_key",
    "keyctl",
    "request_key",
    "iopl",
    "ioperm",
    "create_module",
    "init_module",
    "finit_module",
    "delete_module",
    "lookup_dcookie",
]

# Standard application syscalls permitted for development & test execution
SECCOMP_ALLOWED_SYSCALLS: list[str] = [
    "read",
    "write",
    "open",
    "openat",
    "close",
    "stat",
    "fstat",
    "lstat",
    "poll",
    "lseek",
    "mmap",
    "mprotect",
    "munmap",
    "brk",
    "rt_sigaction",
    "rt_sigprocmask",
    "rt_sigreturn",
    "ioctl",
    "access",
    "pipe",
    "pipe2",
    "select",
    "sched_yield",
    "mremap",
    "msync",
    "mincore",
    "madvise",
    "shmget",
    "shmat",
    "shmctl",
    "dup",
    "dup2",
    "dup3",
    "pause",
    "nanosleep",
    "getitimer",
    "alarm",
    "setitimer",
    "getpid",
    "sendfile",
    "socket",
    "connect",
    "accept",
    "accept4",
    "sendto",
    "recvfrom",
    "sendmsg",
    "recvmsg",
    "shutdown",
    "bind",
    "listen",
    "getsockname",
    "getpeername",
    "socketpair",
    "setsockopt",
    "getsockopt",
    "clone",
    "fork",
    "vfork",
    "execve",
    "exit",
    "wait4",
    "kill",
    "uname",
    "fcntl",
    "flock",
    "fsync",
    "fdatasync",
    "truncate",
    "ftruncate",
    "getdents",
    "getdents64",
    "getcwd",
    "chdir",
    "fchdir",
    "rename",
    "renameat",
    "mkdir",
    "mkdirat",
    "rmdir",
    "creat",
    "link",
    "unlink",
    "unlinkat",
    "readlink",
    "readlinkat",
    "chmod",
    "fchmod",
    "fchmodat",
    "chown",
    "fchown",
    "lchown",
    "fchownat",
    "umask",
    "gettimeofday",
    "getrlimit",
    "getrusage",
    "sysinfo",
    "times",
    "getuid",
    "getgid",
    "geteuid",
    "getegid",
    "setuid",
    "setgid",
    "getgroups",
    "setgroups",
    "sigaltstack",
    "utime",
    "futimesat",
    "epoll_create",
    "epoll_create1",
    "epoll_ctl",
    "epoll_wait",
    "futex",
    "exit_group",
    "clock_gettime",
    "clock_getres",
    "clock_nanosleep",
]

APPARMOR_WORKSTATION_PROFILE: str = """#include <tunables/global>

profile ancos-workstation flags=(attach_disconnected,mediate_deleted) {
  #include <abstractions/base>
  #include <abstractions/nameservice>

  # Deny raw sockets and unauthorized network packet craft
  deny network raw,
  deny network packet,

  # Deny privileged administrative capabilities
  deny capability sys_admin,
  deny capability sys_rawio,
  deny capability sys_ptrace,
  deny capability sys_module,
  deny capability sys_boot,

  # Prohibit ptrace debugging of outside processes
  deny ptrace,

  # Prohibit access to sensitive host secrets and kernel introspection
  deny /etc/shadow r,
  deny /etc/gshadow r,
  deny /etc/sudoers r,
  deny /proc/kcore rw,
  deny /proc/kallsyms rw,
  deny /sys/firmware/** rwklx,
  deny /sys/kernel/security/** rwklx,

  # Workspace confines
  /home/employee/workspace/** rwklx,
  /tmp/** rwklx,
}
"""


def generate_seccomp_profile(default_action: str = "SCMP_ACT_ERRNO") -> dict[str, Any]:
    """Generates standard OCI-compliant seccomp profile JSON dict."""
    return {
        "defaultAction": default_action,
        "architectures": [
            "SCMP_ARCH_X86_64",
            "SCMP_ARCH_X86",
            "SCMP_ARCH_AARCH64",
            "SCMP_ARCH_ARM",
        ],
        "syscalls": [
            {
                "names": SECCOMP_ALLOWED_SYSCALLS,
                "action": "SCMP_ACT_ALLOW",
            },
            {
                "names": SECCOMP_RESTRICTED_SYSCALLS,
                "action": "SCMP_ACT_ERRNO",
            },
        ],
    }


class SecurityProfileValidator:
    """
    Validates and enforces OS boundary profiles (seccomp, AppArmor, non-root, capability drop)
    for workstations and sandboxed execution environments.
    """

    FORBIDDEN_COMMAND_PATTERNS = [
        "ptrace",
        "bpf",
        "kexec",
        "insmod",
        "rmmod",
        "modprobe",
        "mount",
        "umount",
        "pivot_root",
        "setns",
        "unshare",
        "process_vm_readv",
        "process_vm_writev",
        "userfaultfd",
        "SYS_ptrace",
        "SYS_bpf",
        "SYS_mount",
        "SYS_clone3",
        "SYS_reboot",
        "ctypes.CDLL(None)",
        "libc.ptrace",
        "libc.mount",
        "os.chroot",
    ]

    @classmethod
    def validate_container_spec(cls, spec: dict[str, Any]) -> tuple[bool, list[str]]:
        """Validates that a container specification enforces Zero-Trust bounds."""
        violations: list[str] = []

        if spec.get("privileged", False) is True:
            violations.append("Container must NOT run in privileged mode")

        user = str(spec.get("user", ""))
        if user in ("0", "root", "0:0"):
            violations.append("Container must run as non-root user (e.g. 1000:1000)")

        sec_opts = spec.get("security_opt", [])
        if "no-new-privileges:true" not in sec_opts:
            violations.append("Container must enable 'no-new-privileges:true'")

        cap_add = spec.get("cap_add", [])
        dangerous_caps = {"SYS_ADMIN", "SYS_PTRACE", "NET_RAW", "SYS_RAWIO", "SYS_MODULE"}
        present_dangerous = dangerous_caps.intersection(set(c.upper() for c in cap_add))
        if present_dangerous:
            violations.append(f"Forbidden capabilities granted: {sorted(present_dangerous)}")

        return len(violations) == 0, violations

    @classmethod
    def enforce_container_spec(cls, spec: dict[str, Any]) -> dict[str, Any]:
        """Hardens and patches a container launch specification to satisfy security policy."""
        hardened = dict(spec)
        hardened["privileged"] = False
        if not hardened.get("user") or hardened.get("user") in ("0", "root", "0:0"):
            hardened["user"] = "1000:1000"

        opts = set(hardened.get("security_opt", []))
        opts.add("no-new-privileges:true")
        opts.add("apparmor=ancos-workstation")
        hardened["security_opt"] = sorted(list(opts))

        # Explicitly drop ALL capabilities and whitelist minimal required
        hardened["cap_drop"] = ["ALL"]
        hardened["cap_add"] = ["CHOWN", "DAC_OVERRIDE", "FOWNER", "SETGID", "SETUID"]
        return hardened

    @classmethod
    def validate_syscall_attempt(cls, code_or_command: str) -> tuple[bool, list[str]]:
        """Inspects code or commands for attempts to invoke prohibited system calls."""
        violations: list[str] = []
        for pattern in cls.FORBIDDEN_COMMAND_PATTERNS:
            if pattern.isalnum() or ("_" in pattern and "." not in pattern and "(" not in pattern):
                if re.search(rf"\b{re.escape(pattern)}\b", code_or_command):
                    violations.append(f"Blocked dangerous system call or capability pattern: {pattern}")
            else:
                if pattern in code_or_command:
                    violations.append(f"Blocked dangerous system call or capability pattern: {pattern}")
        return len(violations) == 0, violations


    @classmethod
    def enforce_syscall_safety(cls, code_or_command: str) -> None:
        """Raises SyscallSecurityViolation if any prohibited pattern is detected."""
        valid, violations = cls.validate_syscall_attempt(code_or_command)
        if not valid:
            logger.warning("Syscall boundary violation detected", violations=violations)
            raise SyscallSecurityViolation(f"Prohibited syscall invocation: {', '.join(violations)}")
