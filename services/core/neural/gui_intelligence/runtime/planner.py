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
_INTO_FIELD = re.compile(
    r"\binto\s+(?:the\s+)?(.+?)(?:\s+(?:field|box|input|area))?$", re.IGNORECASE
)

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

# Application-launch objectives name a *concept* ("text editor", "browser") while
# the desktop only exposes *menu labels* ("Mousepad", "Web Browser"). The live
# grounder matches pixels, so a concept that never appears on screen can never be
# clicked. This catalog translates a concept to the concrete (category, label)
# the desktop actually renders; the label lives inside its category submenu, so
# the category must be opened first. Keep entries aligned with the base image's
# application menu.
_APP_CATALOG: tuple[tuple[tuple[str, ...], str | None, str], ...] = (
    (("text editor", "notepad", "mousepad", "gedit"), "Accessories", "Mousepad"),
    (("file manager", "thunar"), None, "File Manager"),
    (("terminal emulator", "terminal", "console"), None, "Terminal Emulator"),
    (("web browser", "browser", "firefox", "chromium", "chrome"), None, "Web Browser"),
    (("mail reader", "email", "thunderbird"), None, "Mail Reader"),
)

# Verbs that signal the objective is launching an application rather than using
# one that is already on screen. Routing an app concept through the launcher is
# only correct when the objective actually asks to open/launch it.
_APP_LAUNCH_VERBS = ("open", "launch", "start", "run", "load")

# Text-entry value extraction. Real objectives write the payload unquoted
# ("type the marker GUI_LIFECYCLE_42 into the document"); a quoted value is
# preferred, but the unquoted payload between the verb and its "into/in/to"
# target is captured when no quotes are present.
_TYPED_SEGMENT = re.compile(
    r"\b(?:type|enter|fill|write|input|append|add|insert)\b\s+(?:the\s+|a\s+|an\s+)?"
    r"(?P<value>.+?)\s+(?:into|in|to)\b",
    re.IGNORECASE,
)
_TYPED_FILLER_LEAD = re.compile(
    r"^(?:(?:the|a|an)\s+)?"
    r"(?:(?:exact|literal|precise|specified|following)\s+)?"
    r"(?:line|text|marker|string|value|word|following|this|these|message|content|phrase)\s+",
    re.IGNORECASE,
)
_TYPED_VERB = re.compile(r"\b(?:type|enter|fill|write|input|append|add|insert)\b", re.IGNORECASE)

# A keyboard payload is a literal token (a marker, a URL, a path, KEY=value, or a
# short quoted phrase). Narrative prose that merely *describes* what to author
# ("write a short documentation note about ANCOS") is not a payload: typing the
# sentence verbatim on the desktop is nonsense and no deliverable contains it.
# These signals separate the two so a description is never sent to the keyboard.
_PROSE_SENTENCE_BREAK = re.compile(r"[.!?;]\s+\S")
_PROSE_AUTHORING_PHRASE = re.compile(
    r"\b(?:note|notes|document|documentation|paragraph|essay|summary|description|report|content)\b"
    r"\s+(?:about|on|of|describing|covering|regarding|that|which)\b",
    re.IGNORECASE,
)
# Prose often continues the sentence with a conjunction or a second imperative
# ("Type and save a brief description of ANCOS ..."), which is a description of
# the work, never a literal to type.
_PROSE_LEAD_CONJUNCTION = re.compile(r"^(?:and|then|also|plus|as well as)\b", re.IGNORECASE)
_PROSE_ACTION_VERB = re.compile(
    r"\b(?:save|open|navigate|click|create|write|type|edit|delete|append|insert"
    r"|launch|draft|persist|verify|ensure)\b",
    re.IGNORECASE,
)
_MAX_LITERAL_PAYLOAD_WORDS = 8


def _looks_like_prose(value: str) -> bool:
    """True when a candidate typed value is descriptive prose, not a literal payload."""
    words = value.split()
    if _PROSE_SENTENCE_BREAK.search(value):
        return True
    if _PROSE_AUTHORING_PHRASE.search(value):
        return True
    if _PROSE_LEAD_CONJUNCTION.search(value):
        return True
    # A short payload ("save", "hello world") is a literal; an action verb only
    # signals a work description once the phrase is long enough to be a clause.
    if len(words) >= 3 and _PROSE_ACTION_VERB.search(value):
        return True
    return len(words) > _MAX_LITERAL_PAYLOAD_WORDS


# A document file named in an edit objective ("open handoff.txt ... and save").
_DOCUMENT_FILE = re.compile(
    r"\b([A-Za-z0-9_][A-Za-z0-9_.-]*\.(?:txt|md|markdown|rst|py|json|csv|log|cfg|ini|conf|sh))\b"
)
# Verbs that mean "modify an existing document", as opposed to launching an app.
_DOCUMENT_EDIT_VERBS = ("append", "add", "edit", "insert", "modify", "update", "write")

# The employee workspace is the only writable directory inside the desktop
# container, so a bare document name resolves here when a path is typed into a
# file dialog.
_EMPLOYEE_WORKSPACE = "/home/employee/workspace"
# A concrete payload token such as GUI_EDIT=HYBRID_GUI_OK or DONE.
_EDIT_PAYLOAD = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*=[A-Za-z0-9_./-]+\b")

# A negation within this window before an application alias means the objective
# names that application as an exclusion ("without a terminal"), not a target.
_NEGATION = re.compile(r"\b(?:no|not|without|non|avoid|skip|except|other than)\b", re.IGNORECASE)

# A web objective names where the browser must go. The desktop exposes only
# pixels, so the destination has to be typed into the omnibox as a literal URL.
# An explicit URL is preferred; a well-known site *name* ("Google Docs") maps to
# the host the user actually means.
_URL = re.compile(
    r"\b((?:https?://)?(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+"
    r"(?:com|org|net|io|dev|edu|gov|co|in|ai|app|xyz)(?:/[^\s,;]*)?)",
    re.IGNORECASE,
)
# A bare filename ("handoff.txt") must never be read as a web host.
_FILE_EXTENSION = re.compile(
    r"\.(?:txt|md|markdown|rst|py|json|csv|log|cfg|ini|conf|sh|yaml|yml|toml|xml|html?)$",
    re.IGNORECASE,
)
_SITE_ALIASES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("google docs", "google document", "google doc", "docs.google"), "docs.google.com"),
    (("google drive", "drive.google"), "drive.google.com"),
    (("gmail", "gmail.com"), "mail.google.com"),
    (("youtube",), "www.youtube.com"),
    (("wikipedia",), "www.wikipedia.org"),
    (("github",), "github.com"),
    (("google",), "www.google.com"),
)
_WEB_VERBS = ("navigate", "go to", "visit", "browse", "open the url", "load the page")


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
            label = _clean_label(text[index + len(verb) :])
            if label:
                return label
            start = index + len(verb)
    return None


def _application_menu_label(text: str) -> str | None:
    lowered = text.lower()
    if any(hint in lowered for hint in _MENU_HINTS):
        return _MENU_LABEL
    return None


def _looks_like_app_launch(lowered: str) -> bool:
    """True when the objective is launching an application by concept name."""
    return any(re.search(rf"\b{verb}\b", lowered) for verb in _APP_LAUNCH_VERBS)


def _web_destination(text: str) -> str | None:
    """Return the URL a web objective must load, or ``None``.

    An explicit URL wins; a recognised site name ("Google Docs") maps to its host.
    A bare document filename is never treated as a host, so a file-edit objective
    cannot be misread as a navigation.
    """
    lowered = text.lower()
    has_url = "http://" in lowered or "https://" in lowered
    if not any(verb in lowered for verb in _WEB_VERBS) and not has_url:
        return None
    for match in _URL.finditer(text):
        candidate = match.group(1).rstrip(".")
        if _FILE_EXTENSION.search(candidate):
            continue
        return candidate
    for aliases, host in _SITE_ALIASES:
        for alias in aliases:
            if re.search(rf"(?<![\w-]){re.escape(alias)}\b", lowered):
                return host
    return None


def _match_application(lowered: str) -> tuple[str | None, str] | None:
    """Map an app-launch objective to its concrete (category, label) on screen.

    The leftmost non-negated alias wins, so "open the web browser without a
    terminal" resolves to the browser rather than the terminal named later as an
    exclusion. An alias preceded by a negation word ("no terminal", "without
    using a browser") names what to avoid, not what to launch.
    """
    candidates: list[tuple[int, str | None, str]] = []
    for aliases, category, label in _APP_CATALOG:
        for alias in aliases:
            match = re.search(rf"(?<![\w-]){re.escape(alias)}\b", lowered)
            if match is None:
                continue
            prefix = lowered[max(0, match.start() - 24) : match.start()]
            if _NEGATION.search(prefix):
                continue
            candidates.append((match.start(), category, label))
    if not candidates:
        return None
    candidates.sort()
    _, category, label = candidates[0]
    return category, label


def _typed_value(text: str) -> str | None:
    """Return the literal text a type/enter/fill objective wants entered.

    A quoted payload wins; otherwise the noun phrase between the verb and its
    destination is used, with leading filler words ("the marker", "the text")
    stripped so the value that reaches the keyboard is the payload itself.
    """
    quoted = (
        _quoted_after(text, "type") or _quoted_after(text, "enter") or _quoted_after(text, "fill")
    )
    if quoted is not None:
        return quoted
    segment = _TYPED_SEGMENT.search(text)
    if segment:
        value = _TYPED_FILLER_LEAD.sub("", segment.group("value").strip()).strip()
        if value and not _looks_like_prose(value):
            return value[:200]
        return None
    # No explicit destination ("type GUI_OK"): take the remainder after the verb.
    verb = _TYPED_VERB.search(text)
    if verb:
        value = _TYPED_FILLER_LEAD.sub("", text[verb.end() :].strip()).strip()
        value = re.split(r"[.,;:!?()\n]", value, maxsplit=1)[0].strip()
        if value and not _looks_like_prose(value):
            return value[:200]
        return None
    return None


def _select_edit_payload(
    objective: str, blob: str, acceptance_criteria: str = ""
) -> "re.Match[str] | None":
    """Choose which ``KEY=value`` token the GUI edit should type.

    A hybrid objective names one payload for its terminal phase (file setup) and
    a different one for its graphical phase. The graphical phase is the one this
    choreography types, so the selection prefers, in order: a token whose key
    names the GUI itself (``GUI_EDIT=...``); a token introduced near a GUI hint
    ("GUI editor", "graphical desktop") whose key does not name a terminal phase;
    the first token that is not terminal-named; and finally the first token.

    Key names outrank proximity because a hybrid title ("hybrid terminal+GUI
    workflow") puts a GUI hint within the proximity window of the terminal payload
    too, so proximity alone would type the terminal-side line on the desktop.

    The signed acceptance criteria is searched as well as the objective, because
    the Boss planner paraphrases free-form objectives and can drop the literal
    graphical payload from the task objective while preserving it in the
    acceptance criteria (the authoritative pass condition).
    """
    combined = f"{blob} {acceptance_criteria}".strip()
    tokens = list(_EDIT_PAYLOAD.finditer(combined))
    if not tokens:
        return None
    gui_hints = ("gui", "graphical", "desktop", "editor")
    terminal_hints = ("terminal", "prepare", "verify", "hash")

    def key_of(token: "re.Match[str]") -> str:
        return token.group(0).split("=", 1)[0].lower()

    for token in tokens:
        if any(hint in key_of(token) for hint in gui_hints):
            return token
    for token in tokens:
        if any(hint in key_of(token) for hint in terminal_hints):
            continue
        window = combined[max(0, token.start() - 80) : token.start()].lower()
        if any(hint in window for hint in gui_hints):
            return token
    for token in tokens:
        if not any(hint in key_of(token) for hint in terminal_hints):
            return token
    return tokens[0]


def derive_document_edit_steps(
    title: str, objective: str, scope: str = "", acceptance_criteria: str = ""
) -> list[dict[str, Any]] | None:
    """Plan a GUI edit of an existing workspace document, or return ``None``.

    Objectives like "open handoff.txt in the text editor and add the line
    GUI_EDIT=HYBRID_GUI_OK, then save" need a real open→edit→save choreography on
    a desktop that exposes only pixels. The deterministic route is: launch the
    editor, use its Open dialog with the GTK location bar (Ctrl+O then Ctrl+L,
    typing the absolute path straight into the focused bar), move to the end of
    the document, append the payload, and save with Ctrl+S.

    ``acceptance_criteria`` is consulted when the objective prose has been
    paraphrased and no longer names the file or payload literally; it is the
    signed pass condition and therefore authoritative.
    """
    blob = " ".join(part for part in (title, objective, scope) if part).strip()
    contract_blob = f"{blob} {acceptance_criteria}".strip()
    lowered = contract_blob.lower()
    file_match = _DOCUMENT_FILE.search(blob) or _DOCUMENT_FILE.search(acceptance_criteria)
    if file_match is None:
        return None
    # Only an edit verb turns a named file into an edit choreography; a bare
    # "open handoff.txt" stays a click task.
    if not any(re.search(rf"\b{verb}\b", lowered) for verb in _DOCUMENT_EDIT_VERBS):
        return None
    # The open/close must go through the graphical desktop. A hybrid task also
    # names a terminal phase, so a bare "terminal" mention must not disqualify
    # it; require a positive GUI signal instead of rejecting on the word alone.
    if not any(hint in lowered for hint in ("gui", "graphical", "desktop", "editor")):
        return None
    payload = _select_edit_payload(objective, blob, acceptance_criteria)
    if payload is None:
        return None
    document_name = file_match.group(1)
    path = f"{_EMPLOYEE_WORKSPACE}/{document_name}"
    steps: list[dict[str, Any]] = [
        {"action_type": "MOUSE_CLICK", "target": _MENU_LABEL, "confidence": 0.95},
        {"action_type": "MOUSE_CLICK", "target": "Accessories", "confidence": 0.95},
        {"action_type": "MOUSE_CLICK", "target": "Mousepad", "confidence": 0.95},
        # The editor window is created asynchronously. Without a settle the next
        # keystroke (Ctrl+O) is delivered before the window exists and is silently
        # dropped, so the Open dialog never appears and the edit never happens.
        {"action_type": "WAIT_SETTLE", "confidence": 0.99, "settle_ms": 2500},
        {"action_type": "KEY_HOTKEY", "key_code": "ctrl+o", "confidence": 0.95},
        {"action_type": "WAIT_SETTLE", "confidence": 0.99, "settle_ms": 1200},
        {"action_type": "KEY_HOTKEY", "key_code": "ctrl+l", "confidence": 0.95},
        {
            "action_type": "KEY_TYPE",
            "text": path,
            "confidence": 0.95,
            "type_into_active_focus": True,
        },
        {"action_type": "KEY_HOTKEY", "key_code": "Return", "confidence": 0.95},
        # The dialog must finish closing and the editor must take focus back
        # before the next keystroke, otherwise Ctrl+End lands in the dialog.
        {"action_type": "WAIT_SETTLE", "confidence": 0.99, "settle_ms": 1500},
        {"action_type": "KEY_HOTKEY", "key_code": "ctrl+End", "confidence": 0.95},
        # Type the payload directly after Ctrl+End. The prepare phase guarantees
        # the artifact ends with exactly one newline, so Ctrl+End lands on a fresh
        # empty final line and typing fills it as the next line. Pressing Return
        # first would insert a *blank* line and yield an extra empty element.
        {
            "action_type": "KEY_TYPE",
            "text": payload.group(0),
            "confidence": 0.95,
            "type_into_active_focus": True,
        },
    ]
    # A document-edit choreography always persists the change: the verify phase
    # reads the finalized file, so an unsaved edit fails closed. The paraphrased
    # objective may no longer name the save verb, so save unconditionally here
    # rather than keying off a verb that planning can drop.
    steps.append({"action_type": "KEY_HOTKEY", "key_code": "ctrl+s", "confidence": 0.95})
    # Ctrl+S is fire-and-forget: it proves a keystroke was sent, not that the file
    # was written. A silently lost edit (the save landed in a modal dialog or the
    # editor never had focus) then reports success and the verify phase fails with
    # no explanation. Confirm the save from the editor's own state — a saved
    # document drops the '*' modified marker from its window title — so a lost
    # edit fails here, at the point of the save, instead of silently later.
    steps.append(
        {
            "action_type": "CONFIRM_SAVED",
            "confidence": 0.95,
            "expected_text": document_name,
            "settle_ms": 4000,
        }
    )
    return steps


def derive_gui_steps(
    title: str, objective: str, scope: str = "", acceptance_criteria: str = ""
) -> list[dict[str, Any]]:
    """Return a list of GUI step dicts (see ``WorkerGuiStep``) for a task."""
    blob = " ".join(part for part in (title, objective, scope) if part).strip()
    lowered = blob.lower()
    # App-launch routing keys off the instruction text, not the short task title:
    # a title like "Open the editor" is not enough to distinguish launching a
    # desktop application from clicking a label that is already on screen.
    instruction_lowered = " ".join(part for part in (objective, scope) if part).strip().lower()

    # A named-document edit ("open handoff.txt ... and save") is a full open→edit
    # →save choreography, not a single click; plan it before generic click routing.
    document_steps = derive_document_edit_steps(title, objective, scope, acceptance_criteria)
    if document_steps is not None:
        return document_steps

    steps: list[dict[str, Any]] = []

    # Click tasks: "click <label>" / "open <label>". Emitted first so a click that
    # opens a window settles before any text entry targets the new window.
    click_label = _quoted_after(blob, "click") or _quoted_after(blob, "open")
    if click_label is None:
        click_label = _label_after_verb(blob, _CLICK_VERBS)
    menu_label = _application_menu_label(blob)
    app_match = (
        _match_application(instruction_lowered)
        if _looks_like_app_launch(instruction_lowered)
        else None
    )
    if app_match is not None:
        # The concept name ("text editor") is not a pixel label; route through the
        # launcher to the concrete menu label ("Mousepad") the desktop renders,
        # opening the category submenu first when the entry lives inside one.
        category, app_label = app_match
        click_label = None
        steps.append({"action_type": "MOUSE_CLICK", "target": _MENU_LABEL, "confidence": 0.95})
        if category and category.lower() not in lowered:
            steps.append({"action_type": "MOUSE_CLICK", "target": category, "confidence": 0.95})
        steps.append({"action_type": "MOUSE_CLICK", "target": app_label, "confidence": 0.95})
        menu_label = None
        # A browser launch is only the first half of a web objective; the page the
        # objective names still has to be loaded. Drive the omnibox (Ctrl+L) so the
        # destination is typed as a real URL rather than landing in the page body.
        if app_label == "Web Browser":
            destination = _web_destination(blob)
            if destination:
                steps.append({"action_type": "WAIT_SETTLE", "confidence": 0.99, "settle_ms": 4000})
                steps.append({"action_type": "KEY_HOTKEY", "key_code": "ctrl+l", "confidence": 0.95})
                steps.append({"action_type": "WAIT_SETTLE", "confidence": 0.99, "settle_ms": 600})
                steps.append(
                    {
                        "action_type": "KEY_TYPE",
                        "text": destination,
                        "confidence": 0.95,
                        "type_into_active_focus": True,
                    }
                )
                steps.append({"action_type": "KEY_HOTKEY", "key_code": "Return", "confidence": 0.95})
                steps.append({"action_type": "WAIT_SETTLE", "confidence": 0.99, "settle_ms": 5000})
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
    typed_value = _typed_value(
        " ".join(part for part in (objective, scope) if part).strip() or blob
    )
    if (
        any(word in lowered for word in ("type", "enter", "fill", "write", "input"))
        and typed_value is not None
    ):
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


def expected_onscreen_text(steps: list[dict[str, Any]]) -> str | None:
    """Return the literal deliverable text a plan types onto the screen.

    A plan that types a marker (``KEY_TYPE``) declares that marker as its visible
    deliverable. Verification must check for *that* text rather than a generic
    whole-screen change, because typing a short marker alters very few pixels.
    """
    for step in reversed(steps):
        if step.get("action_type") == "KEY_TYPE" and step.get("text"):
            return str(step["text"])
    return None
