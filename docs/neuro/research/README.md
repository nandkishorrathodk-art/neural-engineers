# NEURO-13: Advanced Computer-Use Intelligence Research

**Status: RESEARCH ONLY. No implementation.** This directory contains research, not
code changes. Nothing here alters ANCOS runtime behaviour.

**Date:** 2026-11-14. **Branch:** `main`. **Code state studied:** post-`0ebcf68`
(NEURO-12D GUI save reliability).

## Why this exists

The premise handed to this phase was that ANCOS's GUI decision loop costs **5–10 s per
step** because "the architecture asks the AI to think too often". That premise is
**wrong for the production worker path**, and the correction changes the recommendation.
See [`00_NEURO13_CHARTER.md`](00_NEURO13_CHARTER.md) §1 and
[`R4_inference_latency.md`](R4_inference_latency.md). In short:

- The **production** GUI path (`GuiWorkerEngine` ← `/internal/worker/tasks/{id}/gui/plan`
  → `derive_gui_steps`) is **deterministic and LLM-free**. There is no per-step model call
  to remove. Its latency is OCR + waits + pixel I/O, ~30–60 s per *task* (10–15 steps), not
  per step.
- The **5–10 s/step** number matches a *different, non-production* surface:
  `DualBrainCognitiveController` + `OSWorldRunner`, reachable only through the `/gui/*`
  benchmark routes. There the per-step cost is not LLM inference (the System-2 planner's
  `llm_gateway` is never actually called) but **Tesseract OCR at 2 upscales per step**.
- The **4× human-speed claim** is unmeasured against a real human baseline; the live
  measured reliability is 90% GUI / 90% hybrid (NEURO-12A), and speedup is `NOT_MEASURED`.

## Citation verification (done before writing)

Every external claim was checked against the live source. Verdicts:

| Claim | Verdict | Evidence |
|---|---|---|
| Microsoft HCU paper + all table numbers | **VERIFIED exactly** | `microsoft.github.io/Hybrid-Computer-Use` + PDF: Partial 72.37 vs 68.82, Binary 32.4 vs 31.5, tool calls 78.4 vs 111.7, steps 85 vs 94, mean 38.25 vs 48.42 min, median 32.95 vs 36.80, "21.00% less mean Time", batches up to 64, AX/GUI MCP tools, AT-SPI/AXUIElement/UI Automation |
| OSWorld 2.0/2.1 facts (108 tasks, 31 sites, 69.6% >1h, ~1.6 h median, 27.25 checkpoints) | **VERIFIED exactly** | `osworld-v2.xlang.ai` |
| Epoch AI review: errors in 36 of 50 audited tasks | **VERIFIED exactly** | `epoch.ai/benchmarks/osworld-2-1/review` ("audited 50 of its 108 tasks and found errors in 36") |
| StateAct: GUI subagent for 28 of 108 tasks = 1.1% of main-agent steps | **VERIFIED exactly** | arXiv 2607.22798 abstract |
| ASIL: structured JSON state + semantic actions, 15 applications | **VERIFIED** (task count 300 single + 80 multi = 380, not "380 single-app") | arXiv 2608.26991 abstract |
| OSWorld-Human: planning/reflection/judging = 75–94% of latency | **PARTIALLY VERIFIED** — the abstract says such calls account for "most of the overall latency" and that steps can take 3× longer over a trajectory; the literal "75–94%" range is not in the abstract and should be attributed to a specific figure/table, not quoted as a headline | arXiv 2506.16042 abstract |
| UFO2 (Microsoft, UIA + vision, HostAgent + AppAgent) | **VERIFIED** | arXiv 2504.14603 "UFO2: The Desktop AgentOS" |
| CoAct-1 (Orchestrator + GUI Operator + Programmer) | **VERIFIED** | arXiv 2508.03923 |

The user's framing was accurate. The only correction is the 75–94% figure and the ASIL
task split.

## Workstreams

| File | Workstream | Core finding |
|---|---|---|
| [`R1_osworld_evaluation.md`](R1_osworld_evaluation.md) | OSWorld 2.1 / OSWorld-Human evaluation | ANCOS's `OSWorldRunner` is a protocol stub over a synthetic frame buffer; it does not run the real benchmark. Epoch's 36/50 grading-error finding means independent verification is mandatory, which ANCOS already does. |
| [`R2_semantic_interaction.md`](R2_semantic_interaction.md) | HCU, AX trees, AT-SPI, semantic interaction | The highest-value channel for ANCOS, but it **collides head-on with the strict GUI-only contract**. Recommendation: a *separate, policy-gated* semantic channel, not a rewrite of the visual loop. |
| [`R3_architectures.md`](R3_architectures.md) | ASIL, StateAct, UFO2, CoAct-1 | ANCOS already has the programmatic + visual halves of these systems. The missing piece is a *structured intermediate representation* and a *channel selector*. |
| [`R4_inference_latency.md`](R4_inference_latency.md) | Multi-step planning, batching, latency | The real GUI cost is OCR (2× tesseract per step), not LLM calls. Cheapest wins: OCR caching/targeted regions, not architecture replacement. |
| [`R5_capability_selection.md`](R5_capability_selection.md) | Employee capability selection & orchestration | Channel choice should be a *policy decision* decoupled from employee identity; ANCOS already has `execution_mode` plumbing to extend. |
| [`R6_humanspeed_feasibility.md`](R6_humanspeed_feasibility.md) | Human-vs-ANCOS benchmark & 4× feasibility | 4× is not currently measurable and is a poor target. Freeze it; replace with verified-success-rate and cost-per-verified-task. |

## Non-negotiable framing

1. This is research. The recommendation is **staged**, not "replace the GUI engine".
2. Semantic/AX interaction is **not** the same as the prohibited "bypass the desktop via
   shell". It operates the *real* UI controls through the OS accessibility layer. The
   distinction and its policy implications are spelled out in `R2`.
3. Nothing here should be read as endorsing dropping ANCOS's strict GUI-only contract
   silently. Any change to that contract is a human-sovereignty decision.
