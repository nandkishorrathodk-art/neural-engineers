# R5 — Employee capability selection and GUI/terminal orchestration

**RESEARCH ONLY.**

## Research-paper findings

- **StateAct** (arXiv 2607.22798): channel choice is **per-subgoal**, made by the main
  agent, not a fixed property of the worker. GUI is used for 1.1% of steps.
- **CoAct-1** (arXiv 2508.03923): an **Orchestrator** decides per subtask between a GUI
  Operator and a Programmer.
- **UFO2** (arXiv 2504.14603): a **HostAgent** decomposes and routes to **AppAgents**;
  channel (API vs vision) is decided by a hybrid control-detection pipeline, not by identity.
- **HCU** (verified): computer use "belongs inside a general agent" — the interface is added
  to the agent, not baked into a separate worker loop.

**Common finding:** the interaction channel is a property of the **task/subgoal and the
available structured interface**, decided by an orchestrator. It is **not** a property of
the employee's identity.

## ANCOS today

- Employee identity is dynamic (`EmployeeFactory`, `ANC-EMP-XXXX`) with roles, skills,
  approved tools, and a persona — **decoupled** from execution mode already, which is good.
- Interaction mode is a **task** property: `execution_mode` ∈ {`terminal_only`, `gui_only`,
  `hybrid`, `authorized_tools`, `research_reasoning`}, enforced by mode contracts
  (NEURO-3). The hybrid worker sequences prepare(terminal) → gui → verify(terminal).
- So ANCOS already separates identity from channel. What it lacks is **per-subgoal adaptive
  selection**: the mode is fixed when the task is created.

## Architecture comparison

| Aspect | StateAct / CoAct-1 | ANCOS |
|---|---|---|
| Who chooses the channel | orchestrator, per subgoal | task creator, per task |
| Granularity | subgoal | task |
| Identity coupling | none | none (already good) |
| Fallback rule | structured-first, GUI when needed | mode-fixed |
| Verification | independent finish gate | `IndependentDeliverableVerifier` |

## Advantages / limitations

- **Advantage of the research direction:** per-subgoal routing lets a mostly-structured task
  touch the GUI only where it must, which is where the latency and flakiness live.
- **Limitation:** per-subgoal routing adds a decision layer that itself must be verified and
  must fail closed. A wrong routing decision (e.g. sending a destructive step to the wrong
  channel) is a safety risk.

## Hardware / infrastructure requirements

- None beyond current; it is a control-plane change.

## Applies to ANCOS?

Yes, as an **extension of the existing `execution_mode` contract**: keep identity separate,
add a routing decision that is recorded in the receipt and bounded by policy. Employee
engineering skills must remain **independent** of available channels (the brief is right on
this).

## Expected effect on latency / success

- Structured subgoals avoid pixel reconstruction (R4's cost) → plausible latency and
  reliability gain. ANCOS-specific numbers: **unmeasured**.

## Security / verification implications

- Routing must be **fail-closed and auditable**: every routing decision recorded, with the
  channel used, in the hash-chained audit trail.
- A route to a more privileged channel (e.g. semantic) requires that the task's mode permits
  it; otherwise the step is refused.
- Channel choice must never let an employee gain a capability its role forbids.

## Prototype acceptance criteria

1. A hybrid task's receipt shows, per subgoal, which channel executed it and why.
2. A forbidden route is refused (fail-closed), not silently upgraded.
3. Employee skills/identity are unchanged by the routing layer (no identity leakage).

## Evidence gaps / unresolved questions

- The cost/benefit of a routing decision layer versus ANCOS's simpler per-task mode on
  ANCOS's own task distribution: **untested**.
- How to express "deepest feasible interface" as a policy ANCOS can enforce: **open**.
