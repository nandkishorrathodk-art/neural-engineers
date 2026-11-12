"""Turn a GUI task's natural-language objective into concrete on-screen steps.

The steps reference *visible text* rather than synthetic element ids, because a
real desktop has no accessibility tree to query: the live OCR grounder locates
each target by reading pixels. This is a deterministic, bounded decomposer for
the common desktop-task shapes; a model-driven planner can replace it behind the
same interface without changing the worker.
"""

from __future__ import annotations

import re
from typing import Any

_QUOTED = re.compile(r"['\"]([^'\"]{2,80})['\"]")
_INTO_FIELD = re.compile(r"\binto\s+(?:the\s+)?(.+?)(?:\s+(?:field|box|input|area))?$", re.IGNORECASE)


def _first_quoted(text: str) -> str | None:
    match = _QUOTED.search(text)
    return match.group(1) if match else None


def _quoted_after(text: str, marker: str) -> str | None:
    """Return the first quoted value that appears after ``marker``."""
    index = text.lower().find(marker)
    if index < 0:
        return None
    return _first_quoted(text[index:])


def derive_gui_steps(title: str, objective: str, scope: str = "") -> list[dict[str, Any]]:
    """Return a list of GUI step dicts (see ``WorkerGuiStep``) for a task."""
    blob = " ".join(part for part in (title, objective, scope) if part).strip()
    lowered = blob.lower()

    steps: list[dict[str, Any]] = []

    # Click tasks: "click <label>" / "open <label>". Emitted first so a click that
    # opens a window settles before any text entry targets the new window.
    click_label = _quoted_after(blob, "click") or _quoted_after(blob, "open")
    if any(word in lowered for word in ("click", "press", "open", "launch", "select")) and click_label:
        steps.append(
            {
                "action_type": "MOUSE_CLICK",
                "target": click_label,
                "confidence": 0.95,
            }
        )

    # Text entry tasks: "type/enter <value> into the <field>". A real desktop has no
    # focus to inherit, so the field must be clicked first; without that the keyboard
    # focus gate fails closed and nothing is typed.
    typed_value = _quoted_after(blob, "type") or _quoted_after(blob, "enter") or _quoted_after(blob, "fill")
    if any(word in lowered for word in ("type", "enter", "fill", "write")) and typed_value is not None:
        field_match = _INTO_FIELD.search(blob)
        field_label = _first_quoted(field_match.group(1)) if field_match else None
        if field_label:
            steps.append(
                {
                    "action_type": "MOUSE_CLICK",
                    "target": field_label,
                    "confidence": 0.95,
                }
            )
        steps.append(
            {
                "action_type": "KEY_TYPE",
                "text": typed_value,
                "confidence": 0.95,
            }
        )
        if "press enter" in lowered or "hit enter" in lowered or "submit" in lowered:
            steps.append({"action_type": "KEY_HOTKEY", "key_code": "Return", "confidence": 0.95})

    if not steps:
        # No actionable verb we can ground: emit a settle step so the worker still
        # captures and verifies the current desktop state deterministically.
        steps.append({"action_type": "WAIT_SETTLE", "confidence": 0.99})

    return steps
