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

# Verbs that name a clickable on-screen target in a real objective.
_CLICK_VERBS = ("click", "press", "open", "launch", "select")

# A label ends at the first word that describes *where* the label is rather than
# what it says ("the File Manager icon" -> "File Manager").
_LABEL_BOUNDARY = re.compile(
    r"\b(?:icon|button|menu|app|application|entry|item|window|dialog|field|box|input|"
    r"area|from|into|to|and|then|on|in)\b",
    re.IGNORECASE,
)

# Phrases that name the desktop's top-level application launcher. The real label
# is read from pixels, so this maps the intent to the label the grounder matches.
_MENU_HINTS = ("application menu", "applications menu", "start menu", "whisker menu")
_MENU_LABEL = "Applications"


def _first_quoted(text: str) -> str | None:
    match = _QUOTED.search(text)
    return match.group(1) if match else None


def _quoted_after(text: str, marker: str) -> str | None:
    """Return the first quoted value that appears after ``marker``."""
    index = text.lower().find(marker)
    if index < 0:
        return None
    return _first_quoted(text[index:])


def _clean_label(fragment: str) -> str | None:
    """Reduce a verb's trailing words to the on-screen label it names."""
    fragment = fragment.strip()
    fragment = re.sub(r"^(?:on\s+)?(?:the|a|an)\s+", "", fragment, flags=re.IGNORECASE)
    fragment = re.split(r"[.,;:!?()\n]", fragment, maxsplit=1)[0]
    boundary = _LABEL_BOUNDARY.search(fragment)
    if boundary:
        fragment = fragment[: boundary.start()]
    fragment = fragment.strip(" \t'\"")
    return fragment[:80] if len(fragment) >= 2 else None


def _label_after_verb(text: str, verbs: tuple[str, ...]) -> str | None:
    """Return the first groundable label named after any of ``verbs``.

    Real objectives write ``click the File Manager icon`` far more often than they
    quote the label, so the unquoted noun phrase is the primary signal; quotes are
    only a disambiguation aid.
    """
    lowered = text.lower()
    for verb in verbs:
        start = 0
        while True:
            index = lowered.find(verb, start)
            if index < 0:
                break
            label = _clean_label(text[index + len(verb):])
            if label:
                return label
            start = index + len(verb)
    return None


def _application_menu_label(text: str) -> str | None:
    lowered = text.lower()
    if any(hint in lowered for hint in _MENU_HINTS):
        return _MENU_LABEL
    return None


def derive_gui_steps(title: str, objective: str, scope: str = "") -> list[dict[str, Any]]:
    """Return a list of GUI step dicts (see ``WorkerGuiStep``) for a task."""
    blob = " ".join(part for part in (title, objective, scope) if part).strip()
    lowered = blob.lower()

    steps: list[dict[str, Any]] = []

    # Click tasks: "click <label>" / "open <label>". Emitted first so a click that
    # opens a window settles before any text entry targets the new window.
    click_label = _quoted_after(blob, "click") or _quoted_after(blob, "open")
    if click_label is None:
        click_label = _label_after_verb(blob, _CLICK_VERBS)
    menu_label = _application_menu_label(blob)
    if menu_label and (click_label is None or menu_label.lower() != click_label.lower()):
        # Opening the application menu is the first real step when the objective
        # routes through it; the launcher must be open before its entries can be
        # grounded on the following frame.
        steps.append({"action_type": "MOUSE_CLICK", "target": menu_label, "confidence": 0.95})
    if click_label and any(word in lowered for word in _CLICK_VERBS):
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
