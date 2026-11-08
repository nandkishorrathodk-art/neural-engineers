import pytest
from neural.exceptions import (
    DiskQuotaExceededError,
    NetworkPerimeterViolation,
    SyscallSecurityViolation,
)
from neural.sandboxes.workstation import SandboxConfig, WorkstationSandbox
from neural.security.network import NetworkSecurityPerimeter
from neural.security.profiles import (
    APPARMOR_WORKSTATION_PROFILE,
    SecurityProfileValidator,
    generate_seccomp_profile,
)
from neural.security.resources import ResourceGovernor


def test_seccomp_profile_generation():
    """Validates that OCI seccomp JSON profile blocks unauthorized dangerous syscalls."""
    profile = generate_seccomp_profile()
    assert profile["defaultAction"] == "SCMP_ACT_ERRNO"
    assert "SCMP_ARCH_X86_64" in profile["architectures"]

    rules = {r["action"]: r["names"] for r in profile["syscalls"]}
    assert "SCMP_ACT_ALLOW" in rules
    assert "SCMP_ACT_ERRNO" in rules

    # Verify dangerous syscalls are explicitly prohibited
    errno_syscalls = set(rules["SCMP_ACT_ERRNO"])
    assert "ptrace" in errno_syscalls
    assert "bpf" in errno_syscalls
    assert "clone3" in errno_syscalls
    assert "mount" in errno_syscalls
    assert "kexec_load" in errno_syscalls
    assert "reboot" in errno_syscalls

    # Verify standard application syscalls are permitted
    allowed_syscalls = set(rules["SCMP_ACT_ALLOW"])
    assert "read" in allowed_syscalls
    assert "write" in allowed_syscalls
    assert "openat" in allowed_syscalls
    assert "socket" in allowed_syscalls


def test_apparmor_profile_content():
    """Validates AppArmor profile denies raw network sockets and secret file reads."""
    profile = APPARMOR_WORKSTATION_PROFILE
    assert "profile ancos-workstation" in profile
    assert "deny network raw," in profile
    assert "deny capability sys_admin," in profile
    assert "deny capability sys_ptrace," in profile
    assert "deny /etc/shadow r," in profile
    assert "deny /proc/kcore rw," in profile


def test_security_profile_validator_container_spec():
    """Validates container spec security boundary checks."""
    insecure_spec = {
        "privileged": True,
        "user": "root",
        "security_opt": [],
        "cap_add": ["SYS_ADMIN", "SYS_PTRACE"],
    }
    valid, violations = SecurityProfileValidator.validate_container_spec(insecure_spec)
    assert valid is False
    assert len(violations) >= 3
    assert any("privileged" in v.lower() for v in violations)
    assert any("non-root" in v.lower() for v in violations)
    assert any("sys_admin" in v.lower() for v in violations)

    secure_spec = {
        "privileged": False,
        "user": "1000:1000",
        "security_opt": ["no-new-privileges:true", "apparmor=ancos-workstation"],
        "cap_add": ["CHOWN"],
    }
    valid_sec, violations_sec = SecurityProfileValidator.validate_container_spec(secure_spec)
    assert valid_sec is True
    assert len(violations_sec) == 0


def test_security_profile_validator_enforce_container_spec():
    """Tests automatic container spec hardening."""
    raw_spec = {"privileged": True, "user": "0"}
    hardened = SecurityProfileValidator.enforce_container_spec(raw_spec)
    assert hardened["privileged"] is False
    assert hardened["user"] == "1000:1000"
    assert "no-new-privileges:true" in hardened["security_opt"]
    assert "apparmor=ancos-workstation" in hardened["security_opt"]
    assert hardened["cap_drop"] == ["ALL"]


def test_security_profile_validator_blocks_syscall_patterns():
    """Tests detection of dangerous system calls and OS privilege boundary breaks."""
    dangerous_code = "import ctypes\nctypes.CDLL(None).ptrace(0, 0, 0, 0)\n"
    valid, violations = SecurityProfileValidator.validate_syscall_attempt(dangerous_code)
    assert valid is False
    assert any("ptrace" in v for v in violations)

    with pytest.raises(SyscallSecurityViolation):
        SecurityProfileValidator.enforce_syscall_safety(dangerous_code)


def test_network_security_perimeter_blocks_cloud_metadata():
    """Verifies that Zero-Trust network perimeter intercepts all cloud metadata accesses."""
    perimeter = NetworkSecurityPerimeter()

    # Blocked metadata endpoints
    metadata_targets = [
        "169.254.169.254",
        "http://169.254.169.254/latest/meta-data/",
        "http://metadata.google.internal/computeMetadata/v1/",
        "169.254.170.2",
        "100.100.100.200",
        "http://instance-data/latest/meta-data/",
    ]

    for target in metadata_targets:
        blocked, reason = perimeter.is_blocked_target(target)
        assert blocked is True, f"Expected {target} to be blocked"
        with pytest.raises(NetworkPerimeterViolation):
            perimeter.validate_outbound_request(target)


def test_network_security_perimeter_blocks_private_subnets_unless_allowlisted():
    """Verifies private RFC1918 subnets are blocked unless explicitly allowlisted."""
    perimeter = NetworkSecurityPerimeter(allow_internal_networks=False)

    blocked, _ = perimeter.is_blocked_target("10.0.1.5")
    assert blocked is True

    blocked, _ = perimeter.is_blocked_target("192.168.1.1")
    assert blocked is True

    # Localhost and explicitly allowed hosts pass
    assert perimeter.is_blocked_target("127.0.0.1")[0] is False
    assert perimeter.is_blocked_target("localhost")[0] is False

    # Add custom allowlist
    perimeter.allow_target("api.internal.company")
    assert perimeter.is_blocked_target("api.internal.company")[0] is False


def test_network_security_perimeter_scan_code():
    """Tests code scanner for hardcoded metadata IP/URLs."""
    perimeter = NetworkSecurityPerimeter()
    malicious_code = (
        "import urllib.request\n"
        "req = urllib.request.urlopen('http://169.254.169.254/latest/meta-data/iam/security-credentials/')\n"
    )
    safe, violations = perimeter.scan_code_for_network_violations(malicious_code)
    assert safe is False
    assert len(violations) > 0


def test_resource_governor_cgroup_and_memory_parsing():
    """Tests memory string parsing and cgroups v2 configuration generation."""
    assert ResourceGovernor.parse_memory_to_bytes("512m") == 512 * 1024 * 1024
    assert ResourceGovernor.parse_memory_to_bytes("4g") == 4 * 1024 * 1024 * 1024
    assert ResourceGovernor.parse_memory_to_bytes("1024k") == 1024 * 1024

    spec = ResourceGovernor.get_cgroup_spec(cpu_limit=1.5, mem_limit="2g", pids_limit=150, disk_quota_mb=200)
    assert spec["nano_cpus"] == int(1.5 * 1e9)
    assert spec["mem_limit_bytes"] == 2 * 1024 * 1024 * 1024
    assert spec["pids_limit"] == 150
    assert spec["disk_quota_bytes"] == 200 * 1024 * 1024
    assert spec["cgroup_v2"]["cpu.max"] == "150000 100000"


def test_resource_governor_disk_quota_enforcement(tmp_path):
    """Tests directory size tracking and disk quota enforcement."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    # Create 100 KB test file
    sample_file = workspace / "data.bin"
    sample_file.write_bytes(b"A" * 100 * 1024)

    # Quota check with 200 KB limit -> Pass
    exceeded, used, msg = ResourceGovernor.check_disk_quota(str(workspace), max_bytes=200 * 1024)
    assert exceeded is False
    assert used >= 100 * 1024

    # Quota check with 50 KB limit -> Exceeded
    exceeded_fail, used_fail, msg_fail = ResourceGovernor.check_disk_quota(str(workspace), max_bytes=50 * 1024)
    assert exceeded_fail is True
    assert "exceeded" in msg_fail.lower()

    # Enforce write
    with pytest.raises(DiskQuotaExceededError):
        ResourceGovernor.enforce_disk_write(str(workspace), incoming_bytes=150 * 1024, max_bytes=200 * 1024)


@pytest.mark.asyncio
async def test_workstation_sandbox_integration_with_security_perimeter(tmp_path):
    """
    Tests WorkstationSandbox intercepting and blocking attempts to access
    cloud metadata service or execute prohibited syscalls.
    """
    cfg = SandboxConfig(
        timeout_seconds=2.0,
        enforce_network_perimeter=True,
        enforce_syscall_filtering=True,
    )
    sandbox = WorkstationSandbox(config=cfg, custom_workdir=str(tmp_path / "sbx"))
    await sandbox.initialize()

    # Attempt metadata access inside sandbox
    metadata_probe_code = (
        "import urllib.request\n"
        "resp = urllib.request.urlopen('http://169.254.169.254/latest/meta-data/')\n"
    )
    receipt = await sandbox.execute_python_code(metadata_probe_code)
    assert receipt.passed_safety is False
    assert receipt.exit_code == -1
    assert any("metadata" in v.lower() for v in receipt.safety_violations)
    assert len(receipt.signature) == 64

    # Attempt prohibited syscall pattern inside sandbox
    syscall_probe_code = "import ctypes\nctypes.CDLL(None).bpf(0, 0, 0)\n"
    receipt_sys = await sandbox.execute_python_code(syscall_probe_code)
    assert receipt_sys.passed_safety is False
    assert any("bpf" in v.lower() for v in receipt_sys.safety_violations)

    await sandbox.cleanup()


def test_network_security_perimeter_ipv6_and_hex_metadata_edge_cases():
    """Validates IPv6 parsing and encoded metadata/private-network edge cases."""
    perimeter = NetworkSecurityPerimeter()

    # Bracketed IPv6 metadata endpoint
    blocked, reason = perimeter.is_blocked_target("[fd00:ec2::254]")
    assert blocked is True
    assert "metadata" in reason.lower()

    # Bracketed IPv6 with port
    blocked, reason = perimeter.is_blocked_target("[fd00:ec2::254]:80")
    assert blocked is True

    # Raw IPv6 was previously truncated at its first colon by host:port parsing.
    assert perimeter.is_blocked_target("fd00:ec2::254")[0] is True
    assert perimeter.is_blocked_target("fd00::1")[0] is True
    assert perimeter.is_blocked_target("http://[::1]:8080/")[0] is True
    assert perimeter.is_blocked_target("::ffff:169.254.169.254")[0] is True

    # Hex representation of 169.254.169.254 (0xA9FEA9FE)
    blocked, reason = perimeter.is_blocked_target("0xA9FEA9FE")
    assert blocked is True

    # Decimal integer representation of 169.254.169.254 (2852039166)
    blocked, reason = perimeter.is_blocked_target("2852039166")
    assert blocked is True


def test_network_security_perimeter_scans_ipv6_literals_but_marks_them_static_only():
    perimeter = NetworkSecurityPerimeter()
    safe, violations = perimeter.scan_code_for_network_violations(
        "url = 'http://[fd00::1]/internal'"
    )

    assert safe is False
    assert any("private" in violation.lower() or "internal" in violation.lower() for violation in violations)


def test_resource_governor_iec_memory_parsing_edge_cases():
    """Validates IEC units (Gi, Mi, Ki, TiB) and edge cases in memory parsing."""
    assert ResourceGovernor.parse_memory_to_bytes("4Gi") == 4 * 1024 * 1024 * 1024
    assert ResourceGovernor.parse_memory_to_bytes("512MiB") == 512 * 1024 * 1024
    assert ResourceGovernor.parse_memory_to_bytes("1024KiB") == 1024 * 1024
    assert ResourceGovernor.parse_memory_to_bytes("1TiB") == 1024 * 1024 * 1024 * 1024
    assert ResourceGovernor.parse_memory_to_bytes("2048b") == 2048
    # Fallback on invalid format
    assert ResourceGovernor.parse_memory_to_bytes("invalid_mem") == 512 * 1024 * 1024
