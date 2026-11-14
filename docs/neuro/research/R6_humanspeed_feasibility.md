# R6 — Human-versus-ANCOS benchmark and 4× feasibility

**RESEARCH ONLY.** This workstream recommends **freezing the 4× claim**.

## Where the 4× claim lives

- `gui_intelligence/benchmarks/human_speed.py` — `HumanSpeedComparator.SPEEDUP_TARGET = 4.0`;
  computes `Speedup = T_human / T_ancos` and **disqualifies to 0.0** if verification fails
  or safety is violated. The disqualification rule is correct and should be kept.
- Surfaced at `POST /gui/human-speed/evaluate` and asserted in tests
  (`test_neuro_phase7_live_acceptance.py`, `test_phase7_gui_intelligence.py`,
  `test_phase7_gui_workflow.py`).
- `docs/neuro/NEURO_BENCHMARKS.md` already states: *"The 4× human-speed target remains a
  research metric."* — good, but the comparator and route still present it as a target.

## Why 4× is not currently measurable

1. **No human baseline exists in-repo.** `HumanSpeedComparator` takes
   `human_time_seconds` as an **input**. Nothing in ANCOS produces a verified human median;
   the tests pass hand-chosen values. So any 4× figure is only as real as the number someone
   typed in.
2. **The disqualification rule means real speedup is usually 0.0.** A failed task counts as
   0×. With NEURO-12A measuring 90% GUI / 90% hybrid success, ~10% of tasks score 0×, which
   dominates the mean.
3. **ANCOS's per-task time includes verification and retries**, and its reliability is not
   100%, so a headline speedup is dominated by reliability, not speed.

## Research-paper context (verified)

- The strongest comparable result in the literature is HCU's **21% mean-time reduction**,
  not 4× (see R2/R4). Even the best published computer-use systems do not claim 4× against
  humans; OSWorld 2.0's best binary completion is **20.6%**, and OSWorld-Human found agents
  take **2.7–4.3× more steps than necessary** — i.e. *slower*, not 4× faster, on accuracy
  and efficiency alike.

## Recommendation

1. **Freeze the 4× claim.** Do not advertise, and do not use as a primary KPI, until a real
   human baseline exists for the specific task set.
2. **Replace it with two honest primary metrics:**
   - **Verified success rate** (task counts as success only if the independent verifier
     passes) — already ANCOS's philosophy.
   - **Cost per verified task** (USD, from `CostTracker`) — already instrumented.
3. **Keep the disqualification rule** unchanged — it is the honest part of the current design.
4. If a human baseline is ever built, define it rigorously: same task set, same start state,
   median of ≥ N humans, include observation/waits/retries/verification in ANCOS time.

## Advantages / limitations

- **Advantage:** removing an unbacked 4× claim aligns with ANCOS's "zero sugar-coating"
  invariant.
- **Limitation:** success rate alone hides latency; keep latency as a secondary metric with
  per-step attribution (R4), not as a speedup ratio against an unmeasured baseline.

## Hardware / infrastructure requirements

- Building a real human baseline requires human annotators on the same task set — a
  **process** cost, not hardware.

## Applies to ANCOS?

Yes. This is the most immediately actionable conclusion: it is a **documentation and KPI
change**, not a code change.

## Expected effect on latency / success

- None on runtime; large on honesty and on steering effort away from chasing a speed number
  toward reliability and structured interaction.

## Security / verification implications

- Unchanged; independent verification stays mandatory and is what makes "verified success
  rate" meaningful.

## Prototype acceptance criteria

1. ANCOS docs/reports state 4× as **unmeasured/frozen**, with the NEURO-12A measured
   reliability (90%/90%) cited instead.
2. A human-speed comparison is only published when `human_time_seconds` comes from a
   documented, verifiable human baseline — otherwise it is reported as a sensitivity
   analysis, clearly labelled.

## Evidence gaps / unresolved questions

- No human median exists for any ANCOS task set.
- No published computer-use system demonstrates a credible 4× human speedup on a comparable
  benchmark; the closest verified result is 21% mean-time reduction.
