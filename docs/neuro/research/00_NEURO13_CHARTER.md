# NEURO-13 Charter: Advanced Computer-Use Intelligence Research

**RESEARCH ONLY — no implementation.** Deliverable of workstreams R1–R6 in this directory.

## 1. The premise, corrected

The brief argued ANCOS is slow because *"the architecture asks the AI to think too
often"* — a screenshot→LLM→decision→input loop at 5–10 s per decision, i.e. 6.3 minutes
for 50 decisions.

Measured against the actual code:

| Path | Model calls per step? | What actually costs time | Reachable from |
|---|---|---|---|
| **Worker GUI (production)** | **No** — `derive_gui_steps` is a deterministic rule-based decomposer (`runtime/planner.py`); `GuiWorkerEngine` never imports an LLM | Tesseract OCR per ground/verify, `WAIT_SETTLE` durations, `xdotool`/`scrot` round-trips via `docker exec` | `/internal/worker/tasks/{id}/gui/plan` + `/gui/execute` |
| **Dual-brain / OSWorld (`/gui/*`)** | **No** — `System2TaskPlanner.llm_gateway` is stored but never called; planning is rule-based | **Tesseract at 2 upscales per step**, `VisualTransitionTracker`, per-step `IndependentVisualVerifier` | `/gui/plan`, `/gui/osworld/run` (benchmark/owner routes) |

So the 5–10 s figure is **plausible as an OCR-bound per-step number on the benchmark
surface**, but it is **not** an LLM-cost problem, and it is **not** the production worker's
per-step cost (production runs are ~30–60 s per *task*, per NEURO-12A). Removing "LLM
thinking per click" would optimise a call that, in the GUI path, does not exist.

This correction does not kill the brief's thesis — it sharpens it. The real version of the
thesis is: **ANCOS reconstructs application state from pixels (OCR) at nearly every step,
when a structured channel would often be cheaper, more reliable, and less fragile.** That
is exactly the HCU / ASIL / StateAct finding, and it is the correct thing to research.

## 2. What each workstream concludes

- **R1 (OSWorld).** ANCOS's OSWorld integration is a protocol stub, not the real benchmark.
  Adopting OSWorld 2.1 as a target is worthwhile, but only with independent grading
  (Epoch found errors in 36/50 audited tasks) — which ANCOS's verifier philosophy already
  requires. Do not chase leaderboard scores.
- **R2 (Semantic/AX).** The single highest-leverage research direction. HCU's real gains
  (72.37% vs 68.82% partial; 78.4 vs 111.7 tool calls; 21% less mean time) come from adding
  an AX channel **beside** the visual one, not from replacing it. For ANCOS this is blocked
  by the strict GUI-only contract; the recommendation is a *new, policy-gated* semantic
  channel, never a silent weakening of the visual contract.
- **R3 (Architectures).** ANCOS already implements the programmatic and visual halves of
  StateAct/CoAct-1 (terminal worker + GUI worker + hybrid coordinator). The missing pieces
  are (a) a structured state/observation representation shared across channels and (b) an
  explicit channel selector. UFO2's app-specialised agents are an optional later stage.
- **R4 (Latency).** Cheapest real wins are in OCR and observation policy (cache by frame
  hash — already partially present — targeted regions, observe-on-uncertainty), not in a
  new architecture. Action batching must stay risk-gated.
- **R5 (Capability selection).** Interaction channel is a *task/policy* property, not an
  employee-identity property. ANCOS already has `execution_mode` contracts to extend.
- **R6 (HumanSpeed).** 4× is currently unmeasurable and is the wrong primary target.
  Freeze it; report verified-success-rate and cost-per-verified-task instead.

## 3. Staged recommendation (do NOT do this all at once)

**Stage 0 — research (this phase).** No code. Done.

**Stage 1 — instrument before you change (low risk).**
Add per-step latency + channel attribution to the worker receipt (OCR ms, wait ms,
transport ms, verification ms). You cannot optimise what you have not measured, and today
the 5–10 s number is unverified for the worker path. Acceptance: a receipt field carrying
measured per-step phase timings on a real run.

**Stage 2 — cheap observation/OCR wins (low risk, no contract change).**
Targeted-region OCR, stronger frame-hash caching, and an observe-on-uncertainty policy.
Acceptance: measurable per-step latency reduction on the benchmark surface with **no
regression** in verified success rate.

**Stage 3 — prototype a policy-gated semantic channel (medium risk, needs approval).**
Add an AT-SPI/AX observation + semantic-action channel behind an explicit
`semantic` capability that is **off by default** and **forbidden unless the task's mode
allows it**. This is the only stage that touches the GUI-only contract, and it requires
explicit human sign-off. Acceptance: semantic actions on a real app succeed with AX-tree
evidence, and GUI-only tasks remain provably visual (contract test still passes).

**Stage 4 — channel selector + structured state (higher risk, research-gated).**
Only after Stage 3 shows a real, verified benefit. Acceptance: an objective is routed to
programmatic/semantic/visual by policy, with the same independent verification on every
channel.

## 4. What NOT to do

- Do **not** copy HCU wholesale. ANCOS's visual loop, verification model, and safety
  contract are different by design (zero-trust, independent verification, human approval).
- Do **not** replace `GUIExecutionLoop` / `ContainerDesktopDriver` in one move.
- Do **not** treat semantic AX as a licence to bypass the desktop. AX drives the *real* UI;
  a shell shortcut does not.
- Do **not** keep advertising 4× until a real human baseline exists.

## 5. Open questions / evidence gaps

- No measured per-step latency breakdown for the production worker path exists today.
- AT-SPI availability and reliability on the ANCOS base desktop image is **unverified**.
- The security review for an AX channel (can a malicious app forge AX names?) is not done.
- Whether OSWorld 2.1 tasks can be graded independently by ANCOS's verifier is untested.
