from __future__ import annotations

import ipaddress
import re
from typing import Any, Optional
from urllib.parse import urlparse

from neural.exceptions import NetworkPerimeterViolation
from neural.logger import get_logger

logger = get_logger("ancos.security.network")


class NetworkSecurityPerimeter:
    """
    Zero-Trust Network Perimeter for sandboxed execution and containerized workstations.
    Enforces strict egress filtering:
    - Blocks all cloud instance metadata endpoints (AWS, GCP, Azure, OpenStack, Alibaba).
    - Blocks access to internal RFC1918 private subnets unless explicitly allowlisted.
    - Intercepts and audits prohibited egress attempts.
    """

    # Cloud metadata endpoints and hostnames
    CLOUD_METADATA_HOSTS: set[str] = {
        "169.254.169.254",
        "169.254.170.2",
        "100.100.100.200",
        "metadata.google.internal",
        "metadata.goog",
        "instance-data",
        "fd00:ec2::254",
    }

    # Private IP networks (RFC1918 + loopback)
    PRIVATE_NETWORKS = [
        ipaddress.ip_network("10.0.0.0/8"),
        ipaddress.ip_network("172.16.0.0/12"),
        ipaddress.ip_network("192.168.0.0/16"),
        ipaddress.ip_network("169.254.0.0/16"),  # Link-local / Cloud metadata
    ]

    def __init__(
        self,
        allow_internal_networks: bool = False,
        allowlist: Optional[set[str]] = None,
        block_metadata: bool = True,
    ):
        self.allow_internal_networks = allow_internal_networks
        self.block_metadata = block_metadata
        self.allowlist: set[str] = set(allowlist) if allowlist else {"localhost", "127.0.0.1"}
        self._blocked_events: list[dict[str, Any]] = []

    def allow_target(self, host_or_ip: str) -> None:
        """Explicitly allowlists a specific hostname or IP."""
        self.allowlist.add(host_or_ip.strip().lower())

    def _extract_host(self, target: str) -> str:
        """Extracts hostname or IP string from URL or raw address."""
        raw = target.strip()
        if "://" in raw:
            parsed = urlparse(raw)
            return (parsed.hostname or "").lower()

        # Handle bracketed IPv6 addresses, e.g. [fd00:ec2::254] or [::1]:8080
        if raw.startswith("["):
            bracket_end = raw.find("]")
            if bracket_end != -1:
                return raw[1:bracket_end].strip().lower()

        # Handle host:port
        if ":" in raw:
            parts = raw.split(":")
            return parts[0].strip().lower()

        host = raw.strip().lower()

        # Handle integer/hex IP addresses (e.g. 0xA9FEA9FE or 2852039166 for 169.254.169.254)
        try:
            if host.startswith("0x"):
                int_val = int(host, 16)
                if 0 <= int_val <= 0xFFFFFFFF:
                    return str(ipaddress.IPv4Address(int_val)).lower()
            elif host.isdigit():
                int_val = int(host)
                if 0 <= int_val <= 0xFFFFFFFF:
                    return str(ipaddress.IPv4Address(int_val)).lower()
        except Exception:
            pass

        return host

    def is_blocked_target(self, target: str) -> tuple[bool, str]:
        """
        Evaluates whether an endpoint target is prohibited under zero-trust policy.
        Returns (is_blocked: bool, reason: str).
        """
        host = self._extract_host(target)
        if not host:
            return False, ""

        # Allowlist takes precedence for safe internal development services
        if host in self.allowlist:
            return False, ""

        # 1. Cloud Metadata Hard Block
        if self.block_metadata:
            if host in self.CLOUD_METADATA_HOSTS or any(meta in host for meta in ["169.254.169.254", "metadata.google"]):
                return True, f"Prohibited cloud metadata access attempt blocked: '{host}'"

        # 2. Check if host parses as an IP address
        try:
            ip_obj = ipaddress.ip_address(host)
            # Link-local is always blocked
            if ip_obj.is_link_local:
                return True, f"Link-local network access blocked: '{host}'"

            # Check metadata IP explicitly
            if str(ip_obj) == "169.254.169.254":
                return True, f"Cloud metadata service IP (169.254.169.254) blocked: '{host}'"

            # Check RFC1918 private subnets
            if not self.allow_internal_networks:
                for net in self.PRIVATE_NETWORKS:
                    if ip_obj in net:
                        return True, f"Private internal RFC1918 network access blocked: '{host}' in {net}"
        except ValueError:
            # Not an IP address, validate hostname patterns
            pass

        return False, ""

    def validate_outbound_request(self, target: str) -> None:
        """
        Validates an outbound network target.
        Raises NetworkPerimeterViolation if prohibited.
        """
        blocked, reason = self.is_blocked_target(target)
        if blocked:
            event = {
                "target": target,
                "reason": reason,
                "action": "blocked",
            }
            self._blocked_events.append(event)
            logger.warning("Zero-Trust network perimeter blocked request", target=target, reason=reason)
            raise NetworkPerimeterViolation(reason)

    def scan_code_for_network_violations(self, code: str) -> tuple[bool, list[str]]:
        """
        Scans code or script content for prohibited URL or IP literal access.
        """
        violations: list[str] = []

        # Check for metadata URLs/IPs in string literals
        for meta in self.CLOUD_METADATA_HOSTS:
            if meta in code:
                violations.append(f"Hardcoded cloud metadata endpoint reference detected: {meta}")

        # Scan for IPv4 addresses in code
        ip_matches = re.findall(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", code)
        for ip_str in ip_matches:
            blocked, reason = self.is_blocked_target(ip_str)
            if blocked:
                violations.append(reason)

        return len(violations) == 0, violations

