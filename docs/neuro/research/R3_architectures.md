# R3 — ASIL, StateAct, UFO2, CoAct-1 architectures

**RESEARCH ONLY.**

## Research-paper findings (verified)

- **ASIL** (arXiv 2608.26991, EMNLP Findings 2026): "Agent-Software Interaction Layer" —
  exposes software through **structured JSON observations and code-executable semantic
  actions**, choosing the *deepest feasible access path* per application. Instantiated
  across **15 applications** and a benchmark of **300 single-app + 80 multi-app tasks**
  (the brief's "380 tasks" is this sum). Abstract numbers: ASIL >80 with closed models at
  <5 actions/task; under a repaired runtime and 50-step screenshot budget, screenshot-and-
  click yields 6.6 / 26.6 strict success, rising to 15.0 / 53.3 on an easier band; small-
  scale SFT raises Qwen3.5-2B 58.0→72.1 and 9B 66.6→80.4.
- **StateAct** (arXiv 2607.22798): **code-first, multi-agent** harness. The main agent works
  directly with program state (files, app backends, DOM) via code; a **dedicated GUI
  subagent** handles screenshot-and-click only for the few subgoals that need it — **28 of
  108 tasks, 1.1% of main-agent steps**. Adds an **independent finish gate** that checks for
  structural failures (missing/unsaved/wrong-path output). On OSWorld 2.0: lifts Claude
  Opus 4.8 from 20.6%→26.9% binary and 54.8%→61.6% partial at ~9× lower cost. A code-only
  variant *without* the GUI subagent scores only 45.9% partial (worse than the 54.8%
  screenshot baseline) — so the visual fallback is still needed.
- **UFO2** (arXiv 2504.14603): "The Desktop AgentOS" for Windows — a **HostAgent** for
  decomposition + **AppAgent**s specialised per application, a **unified GUI–API action
  layer**, and a **hybrid control detection** pipeline fusing **Windows UI Automation (UIA)**
  with vision.
- **CoAct-1** (arXiv 2508.03923): an **Orchestrator** dynamically delegates each subtask to
  either a **GUI Operator** or a **Programmer** agent that writes/executes Python or Bash —
  "coding as an enhanced action".

## The common pattern

All four converge on the same thesis, and it is the brief's thesis: **prefer the deepest
structured interface; fall back to pixels only when necessary; keep the code/programmatic
path primary for state manipulation.** StateAct is the strongest evidence that the best
GUI agent spends *little* time in the GUI.

## Architecture comparison vs ANCOS

ANCOS already has two of the three pieces these systems propose:

| System | Programmatic | Semantic | Visual | Selector | Independent state check |
|---|---|---|---|---|---|
| ASIL | code actions | JSON state | screenshots | per-app deepest path | yes |
| StateAct | code-first main agent | — | GUI subagent (1.1% steps) | subgoal routing | **finish gate** |
| UFO2 | app APIs | UIA | vision | HostAgent | — |
| CoAct-1 | Programmer | — | GUI Operator | Orchestrator | — |
| **ANCOS** | **terminal worker** | **none** | **GUI worker** | **`execution_mode` contract (static)** | **`IndependentDeliverableVerifier`** |

What ANCOS **lacks** relative to these systems:
1. a **structured state/observation representation** shared across channels (ANCOS uses
   pixels + OCR regions; there is no common "program state" object);
2. an **adaptive channel selector** — ANCOS's mode is fixed at task creation
   (`terminal_only` / `gui_only` / `hybrid` / …), not chosen per subgoal;
3. app-specialised knowledge (UFO2's AppAgents).

## Advantages / limitations

- **Advantage:** ANCOS's hybrid worker is already CoAct-1/StateAct-shaped (terminal
  prepare → GUI interaction → terminal verify). The gap is adaptive routing, not a rewrite.
- **Limitation:** the visual fallback is *proven necessary* (StateAct's code-only variant
  underperforms). Any plan that removes the visual channel is contradicted by the evidence.

## Hardware / infrastructure requirements

- No new hardware. Requires an internal representation layer and a router; both are
  software-only. UFO2-style app agents would need per-app knowledge bases.

## Applies to ANCOS?

Yes, in stages: the **channel selector** and a **shared structured-state object** are the
directly applicable ideas. UFO2's per-app agents are a later, optional stage.

## Expected effect on latency / success

- Routing easy/structured subgoals to the programmatic path (which ANCOS already does in
  hybrid) should cut both latency and flakiness; StateAct's ~9× cost reduction and 1.1%
  GUI-step share suggest large upside. Exact ANCOS numbers: **unmeasured**.

## Security / verification implications

- A selector must be **fail-closed**: unknown → visual (the most constrained) or refuse,
  never silently escalate privileges.
- StateAct's **independent finish gate** maps exactly onto ANCOS's verifier; keep it
  independent of the channel that produced the result.

## Prototype acceptance criteria

1. A task with mixed subgoals routes each subgoal to a channel by policy, recorded in the
   receipt.
2. The same independent verification runs regardless of channel.
3. A structured-state object round-trips: the verifier can inspect it without trusting the
   executor's claims.

## Evidence gaps / unresolved questions

- How to represent "program state" uniformly across a terminal file, a GUI document, and a
  web app: **open design question**.
- Whether per-subgoal routing beats ANCOS's per-task mode on real tasks: **untested**.
