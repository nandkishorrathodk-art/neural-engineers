# R2 — HCU, AX trees, AT-SPI, and semantic interaction

**RESEARCH ONLY.** Highest-priority workstream.

## Research-paper findings (verified)

**Microsoft HCU** (`microsoft.github.io/Hybrid-Computer-Use`, Oct 2026; paper PDF fetched
and read): a unified **programmatic + semantic (AX) + visual** interface, shipped as a
two-tool MCP server (AX and GUI) beside the agent's own shell/file tools.

Mechanisms (verified from the paper):
- Compact **AX working trees** with short refs `aN → wN → rN` bound to one window.
- **Batches of up to 64 actions** with in-call waits and a post-action **AX diff**.
- **Guards** that check target and layout *before* sending desktop input.
- One Go core, native providers for Linux (**AT-SPI**), macOS (**AXUIElement**), Windows
  (**UI Automation**).

Matched GPT-6 Astra / Codex / OSWorld 2.0 full set (verified exact from the PDF, Table 9):

| Metric | CUA Driver (screenshot) | HCU | Δ |
|---|---|---|---|
| Partial | 68.82% | 72.37% | +3.55 pp |
| Binary | 31.5% | 32.4% | +0.90 pp |
| Tool calls | 111.7 | 78.4 | −29.81% |
| Steps | 94 | 85 | −9.57% |
| Mean time | 48.42 min | 38.25 min | **−21.00%** |
| Median time | 36.80 min | 32.95 min | − |
| Cost | $10.05 | $10.03 | ~equal |

Key caveat stated by the paper itself: **"Gains are over CUA Driver with the same model,
harness, and tasks."** The gain is **~21% mean time**, not 4×. A Claude Opus 5 variant was
actually *slower* per task (113.95 min) and the paper reports cost reductions, not speed
multiples.

## The collision with ANCOS's contract

ANCOS's strict GUI-only contract is explicit. `ocr_runtime.py` states: *"The ANCOS strict
GUI contract forbids DOM / accessibility-tree inspection. Text on a real desktop must
therefore be recovered from raster pixels."* `GUIActionPolicy.validate_gui_contract` blocks
DOM/CDP shortcuts (`document.querySelector`, `page.evaluate`, `cdp.send`).

So HCU's central idea is currently **prohibited by design** in ANCOS. This is the crux of
the whole research phase and it must not be glossed over.

**The critical distinction** (from the brief, and it is correct): semantic AX interaction
**drives the real application's UI controls** through the OS accessibility layer. It is
**not** the same as bypassing the desktop with an unrestricted shell command. ANCOS's
prohibition exists to stop *cheating* (reading the answer instead of doing the task); AX
does the task, just via a more structured input channel. But this is a *policy* judgement,
not a technical one, and it is the human owner's call.

## Architecture comparison vs ANCOS

| Capability | HCU | ANCOS today |
|---|---|---|
| Programmatic channel | shell/file tools | terminal worker |
| Semantic channel | AX tree (AT-SPI/UIA/AXUIElement) | **none** (prohibited) |
| Visual channel | GUI tool, screenshot + input | `GUIExecutionLoop` + `ContainerDesktopDriver` + OCR |
| Structured state | AX refs + diffs | pixels + OCR regions + `VisualBeliefStateManager` |
| Batching | up to 64, guarded | `ActionBatcher` (small chunks) |
| Pre-input guard | target + layout check | `GUIRiskGuard` (policy, not layout) |

## Advantages / limitations

- **Advantages:** fewer pixels to interpret, semantic target identity instead of coordinate
  guessing (kills coordinate drift), reliable post-action diffs, likely big OCR-latency and
  reliability wins on Linux apps that expose AT-SPI.
- **Limitations:** AT-SPI coverage is uneven (GTK good, some apps poor, canvas apps none);
  AX can be spoofed by a hostile app (a security concern); it does not remove the need for
  the visual channel; and for ANCOS it requires changing a deliberate safety contract.

## Hardware / infrastructure requirements

- Linux: `at-spi2-core` + `pyatspi`/`dbus` in the desktop image; a running AT-SPI bus.
- The AX channel must run **inside** the restricted container, never as a host capability.

## Applies to ANCOS?

Partially, and only via a **new, policy-gated channel** (Stage 3 in the charter). It does
**not** apply as a silent replacement of the visual loop.

## Expected effect on latency / success

- If AT-SPI works on the base image: fewer full-screen OCR passes per step (OCR is the
  measured GUI cost — see R4), so a plausible per-step latency reduction and a reduction in
  coordinate-drift failures. Exact numbers **unmeasured for ANCOS**.

## Security / verification implications

- An AX channel is an *observation and action* channel; it must be subject to the same
  policy engine, approval gates, and independent verification as the visual channel.
- A malicious or compromised app could expose misleading AX names; verification must still
  confirm the real artifact/UI state, not trust AX text alone.
- The channel must be **off by default** and enabled per task mode, not globally.

## Prototype acceptance criteria

1. A GTK app (e.g. Mousepad) exposes a readable AX tree inside the restricted container.
2. A semantic action (`click` by AX ref) performs the same real UI change a visual click
   does, verified by ANCOS's independent verifier.
3. GUI-only tasks remain provably visual: the existing contract test still passes and no AX
   call is made when the mode forbids it.

## Evidence gaps / unresolved questions

- AT-SPI availability/latency on the ANCOS base image: **untested**.
- How to make AX spoofing-safe: **unresolved**.
- Whether HCU's ~21% time gain transfers to ANCOS's simpler task shapes: **unknown**.
