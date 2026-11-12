"""Shared runtime errors for the GUI execution stack."""

from __future__ import annotations


class DesktopRuntimeUnavailable(RuntimeError):
    """Raised when the employee desktop or a required X binary is unavailable."""


__all__ = ["DesktopRuntimeUnavailable"]
