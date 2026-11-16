"""Author the on-screen deliverable for a GUI task that asks for written content.

The deterministic planner can type a *literal* the objective already contains (a
marker, a URL, a path). It cannot invent content: an objective like "draft a
short note about ANCOS" names a deliverable that must be composed, and typing the
objective prose instead is not the deliverable. This module asks the reasoning
model to author that content, then builds a deterministic typing choreography for
it. Nothing about the content is hardcoded: the text is derived from the task's
own contract at run time.

The authored text is verified by a short, OCR-readable *marker* that must appear
verbatim inside it. A long paragraph cannot be read back reliably from pixels, so
the marker — not the whole body — is the on-screen pass condition.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from neural.logger import get_logger
from pydantic import BaseModel, field_validator

logger = get_logger("ancos.gui_intelligence.runtime.authoring")

# Only these characters can be read back from a screenshot reliably. The marker is
# upper-cased and reduced to this alphabet so OCR is not asked to distinguish
# punctuation or case in a small on-screen font.
_MARKER_ALPHABET = re.compile(r"[^A-Z0-9-]")
_MAX_MARKER_LEN = 32
_MIN_MARKER_LEN = 6

# Verbs that unambiguously produce written content. Their presence alone means the
# objective wants a composed deliverable.
_CONTENT_VERBS = (
    "write",
    "draft",
    "compose",
    "author",
    "describe",
    "summarize",
    "summarise",
    "paraphrase",
    "rephrase",
)

# General production verbs. These only imply authoring when the objective also
# names a written-content noun; "create a folder" must not trigger authoring.
_PRODUCTION_VERBS = (
    "create",
    "prepare",
    "make",
    "produce",
    "generate",
    "type",
    "enter",
    "fill",
    "add",
)

# Nouns that name a written-content deliverable.
_CONTENT_NOUNS = (
    "note",
    "paragraph",
    "description",
    "summary",
    "text",
    "message",
    "line",
    "documentation",
    "content",
    "essay",
    "caption",
    "sentence",
    "blurb",
    "memo",
    "document",
    "doc",
)

# A short clause the objective names as the content's subject, e.g.
# "about ANCOS" / "of the ANCOS platform". A trailing location phrase
# ("in the text editor") is not part of the subject, so the capture stops there.
_ABOUT_CLAUSE = re.compile(
    r"\b(?:about|on|regarding|covering|describing|of)\s+"
    r"(?P<subject>.{2,80}?)"
    r"(?=\s+(?:in|into|on|using|with|via)\s+the\b|[.,;:]|$)",
    re.IGNORECASE,
)


class AuthoredDesktopContent(BaseModel):
    """The content a GUI task must place on screen, plus its OCR-readable marker."""

    marker: str
    body: str

    @field_validator("marker")
    @classmethod
    def _normalize_marker(cls, value: str) -> str:
        cleaned = _MARKER_ALPHABET.sub("-", str(value or "").upper()).strip("-")
        cleaned = re.sub(r"-{2,}", "-", cleaned)
        if len(cleaned) < _MIN_MARKER_LEN:
            raise ValueError("marker is too short to be read back from a screenshot")
        return cleaned[:_MAX_MARKER_LEN]

    @field_validator("body")
    @classmethod
    def _validate_body(cls, value: str) -> str:
        body = str(value or "").strip().strip("'\"")
        if not body:
            raise ValueError("body must not be empty")
        return body


def is_authoring_objective(text: str) -> bool:
    """True when the objective asks for content that must be composed, not quoted.

    This is a *trigger* only: the authoring model still decides the content. It
    exists so a task that already carries a literal payload (a marker, a URL) is
    never re-authored, which would overwrite the exact string its test asserts.

    A written-content verb ("draft", "compose") is decisive on its own. A general
    production verb ("create", "add") only counts when the objective also names a
    written-content noun, so "create a folder" is not treated as authoring.
    """
    lowered = (text or "").lower()
    if any(re.search(rf"\b{verb}\b", lowered) for verb in _CONTENT_VERBS):
        return True
    has_production_verb = any(
        re.search(rf"\b{verb}\b", lowered) for verb in _PRODUCTION_VERBS
    )
    has_content_noun = any(
        re.search(rf"\b{noun}\b", lowered) for noun in _CONTENT_NOUNS
    )
    return has_production_verb and has_content_noun


def subject_hint(text: str) -> str | None:
    """Return the content subject the objective names, if any."""
    match = _ABOUT_CLAUSE.search(text or "")
    if not match:
        return None
    subject = match.group("subject").strip()
    return subject[:80] or None


def build_authoring_prompt(
    title: str,
    objective: str,
    scope: str = "",
    acceptance_criteria: str = "",
) -> str:
    """Compose the authoring prompt from the task's own contract."""
    contract = (
        f"Task title: {title}\n"
        f"Objective: {objective}\n"
        f"Scope: {scope}\n"
        f"Acceptance criteria: {acceptance_criteria}\n"
    )
    subject = subject_hint(" ".join(part for part in (objective, scope, title) if part))
    subject_line = f"The content is about: {subject}\n" if subject else ""
    return (
        "You are a graphical-desktop employee. A task will be carried out entirely by "
        "typing on a real desktop, and the result is judged by reading the screen.\n\n"
        "Author the EXACT text the desktop should type, and a short marker that proves "
        "it. Return two fields:\n"
        "- `body`: the actual deliverable text to type on screen — the content the task "
        "asks for, not a description of it. Keep it to one or two short plain-ASCII "
        "lines with no surrounding quotes.\n"
        "- `marker`: a short, distinctive token that appears VERBATIM as a substring of "
        "`body`, so a screenshot reader can confirm the text landed. Use only A-Z, 0-9 "
        "and '-', 6 to 32 characters, and make it specific to this task rather than a "
        "generic word.\n\n"
        f"{subject_line}"
        "Do not include the objective's own instructions (verbs like 'write', 'save', "
        "'open') inside `body`; write only the finished content.\n\n"
        f"{contract}"
    )


AUTHORING_SYSTEM_PROMPT = (
    "You compose concise on-screen deliverable text for desktop tasks. Return only "
    "valid JSON matching the provided schema. The marker must appear verbatim inside "
    "the body and be readable from a screenshot."
)


async def author_desktop_content(
    gateway: Any,
    *,
    title: str,
    objective: str,
    scope: str = "",
    acceptance_criteria: str = "",
    employee_id: Optional[str] = None,
    task_id: Optional[str] = None,
    project_id: Optional[str] = None,
    max_tokens: int = 2000,
    db_session: Any = None,
    budget_scope_caps: Optional[dict[str, Any]] = None,
) -> AuthoredDesktopContent | None:
    """Ask the reasoning model to author the deliverable, or return ``None``.

    Returns ``None`` (never raises for provider reasons) when authoring is
    unavailable, so the caller fails closed on the deterministic plan instead of
    surfacing a provider error as a task failure.
    """
    prompt = build_authoring_prompt(title, objective, scope, acceptance_criteria)
    try:
        content, _response = await gateway.generate_structured(
            prompt=prompt,
            response_model=AuthoredDesktopContent,
            route="reasoning",
            system_prompt=AUTHORING_SYSTEM_PROMPT,
            employee_id=employee_id,
            task_id=task_id,
            project_id=project_id,
            temperature=0.0,
            max_tokens=max_tokens,
            db_session=db_session,
            budget_scope_caps=budget_scope_caps,
        )
    except Exception as exc:  # provider/validation failure: fail closed, do not crash the plan
        logger.warning("GUI content authoring unavailable", error=str(exc))
        return None

    if content.marker not in content.body:
        # The contract is "the marker proves the body landed". A marker that is not
        # inside the body could never be observed on screen, so reject the sample
        # rather than verify against text the desktop was never told to type.
        logger.warning("Authored marker not present in body; rejecting sample")
        return None
    return content


def build_authoring_steps(
    content: AuthoredDesktopContent,
    launch_prefix: Optional[list[dict[str, Any]]] = None,
) -> list[dict[str, Any]]:
    """Build the deterministic choreography that types the authored body on screen.

    ``launch_prefix`` opens the application the objective names (for example the
    text editor through the application menu). The authored body is typed into the
    focused window; the worker clicks the focused window's body first, so keystrokes
    reach the document rather than being dropped. Newlines are typed as explicit
    Return keypresses so multi-line content does not depend on how the low-level
    typing tool treats a newline character.
    """
    steps: list[dict[str, Any]] = list(launch_prefix or [])
    if steps:
        # The application window is created asynchronously; without a settle the
        # first keystroke is delivered before the window exists and is dropped.
        steps.append({"action_type": "WAIT_SETTLE", "confidence": 0.99, "settle_ms": 2500})
    lines = content.body.split("\n")
    for index, line in enumerate(lines):
        if index:
            steps.append({"action_type": "KEY_HOTKEY", "key_code": "Return", "confidence": 0.95})
        if line:
            steps.append({"action_type": "KEY_TYPE", "text": line, "confidence": 0.95})
    return steps


__all__ = [
    "AUTHORING_SYSTEM_PROMPT",
    "AuthoredDesktopContent",
    "author_desktop_content",
    "build_authoring_prompt",
    "build_authoring_steps",
    "is_authoring_objective",
    "subject_hint",
]
