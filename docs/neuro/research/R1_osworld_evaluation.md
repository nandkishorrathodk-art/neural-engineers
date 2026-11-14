# R1 — OSWorld 2.1 and OSWorld-Human evaluation

**RESEARCH ONLY.**

## Research-paper findings (verified)

- **OSWorld 2.0 / 2.1** (XLANG Lab, HKU; `osworld-v2.xlang.ai`): 108 long-horizon tasks,
  31 self-hosted websites, 69.6% of tasks need >1 h from a skilled human, median human
  completion ~1.6 h, ~318 tool calls (vs ~30 in OSWorld 1.0), 27.25 average scoring
  checkpoints. Best reported binary completion at 500 steps is only **20.6%** (Claude Opus
  4.8, 54.8% partial). Verified against the benchmark page.
- **OSWorld-Human** (arXiv 2506.16042): the first temporal study of computer-use agents.
  Finds "large model calls for planning, reflection, and judging account for **most** of the
  overall latency", and that each successive step can take **3× longer** than early steps.
  The brief's "75–94%" is not in the abstract — treat it as a figure from a specific table,
  not a quotable headline.
- **Epoch AI review** (`epoch.ai/benchmarks/osworld-2-1/review`, verdict *Flawed*): audited
  50 of 108 tasks, found errors in **36**; 11 affected the whole task score, 25 part; up to
  50.4% of score could be affected; 4 tasks/checkpoints impossible as written, 24 cases of
  missed credit for correct work.

## Architecture comparison vs ANCOS

ANCOS's `gui_intelligence/benchmarks/osworld_runner.py` (`OSWorldRunner`, "OSWorld 2.1
Benchmark Protocol Runner"):

- It does **not** run the real OSWorld harness. It wraps `DualBrainCognitiveController`
  over a `ScreenCaptureEngine` that, absent a live X display, uses an in-memory synthetic
  frame buffer. Task objectives are passed straight to the rule-based planner; there is no
  OSWorld task corpus, no VM snapshot/restore, no getters/checkers.
- Its reported metrics (`OSWorldTaskResult`: score, steps, average_step_latency_ms) come
  from ANCOS's own pixel-delta verifier, not OSWorld's task checkers.
- Therefore any "OSWorld 2.1" number ANCOS reports today is **not** comparable to the
  published leaderboard. This is an honesty gap, not a bug.

## Advantages / limitations

- **Advantage of adopting OSWorld 2.1:** it is the closest public proxy for ANCOS's actual
  mission (long-horizon, professional, multi-app). It would replace ANCOS's self-graded
  benchmark with an external one.
- **Limitation:** the benchmark's own grading is flawed (Epoch 36/50). Trusting its score
  blindly would violate ANCOS's "never fake progress" invariant.

## Hardware / infrastructure requirements

- Real OSWorld 2.1 needs per-task VM/container images (Ubuntu desktop, LibreOffice, Chrome,
  GIMP, VS Code, …), snapshot/restore, and the official getter/checker scripts. ANCOS's
  restricted single-image workstation is **not** sufficient as-is.
- Budget: 108 tasks × up to 500 steps × ~2.4K tokens/screenshot if run visually.

## Applies to ANCOS?

Yes, as a **target benchmark with independent grading**, not as a leaderboard chase. ANCOS
should run OSWorld tasks and grade them with *both* the official checker *and* ANCOS's
independent verifier, and report disagreements — that is the honest use.

## Expected effect on latency / success

- No direct latency effect. The value is measurement credibility: a real external success
  rate would replace the current unmeasured 4× claim (see R6).

## Security / verification implications

- OSWorld tasks include safety-sensitive executions (the benchmark ships safety reports).
  ANCOS's emergency-stop, approval gates, and zero-trust broker must wrap any real OSWorld
  run; an OSWorld task must not be allowed to escape the restricted workstation.
- Grading must be fail-closed: if the official checker and ANCOS's verifier disagree, the
  task is **not** counted as success.

## Prototype acceptance criteria

1. A single real OSWorld 2.1 task runs end-to-end inside the restricted workstation with
   the official checker producing a verdict.
2. ANCOS's independent verifier produces a second verdict from artifacts/pixels.
3. Disagreement is recorded explicitly; neither verdict is silently preferred.

## Evidence gaps / unresolved questions

- Whether the ANCOS base image can host the OSWorld task images without weakening the
  zero-trust perimeter.
- How to snapshot/restore a workstation fast enough for 108 tasks within budget.
- Whether OSWorld's own checkers can be run without giving the agent write access to them.
