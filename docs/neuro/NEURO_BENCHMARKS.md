# ANCOS NEURO benchmarks

## Baseline availability

No live ANCOS API, worker, verifier, or Docker engine was available during NEURO-0. Therefore no honest latency, throughput, cost-per-verified-task, or hardware utilization benchmark can be reported. Do not infer performance from in-process unit tests.

| Metric | NEURO-0 baseline | Evidence / limitation |
|---|---|---|
| Full-suite component tests | 334 passed / 31 failed / 11 skipped (376 collected) | Baseline run on 2026-11-02; not a production mission. |
| Latest full regression | 339 passed / 32 failed / 11 skipped (382 collected) | Run at source commit `c979cb2`; predates terminal worker and verifier rejection recovery. Do not present as the current commit's complete regression result. |
| Latest focused lifecycle regression | 33 passed | Pre-commit validation for the current verifier-consumer slice; authority/recovery, terminal-worker, verifier authorization, broker sandbox, and independent-verification unit suites; mocked Docker/API plus in-process DB tests, not runtime acceptance. |
| Objective-to-verified-task success rate | NOT MEASURED | Terminal worker is opt-in and unrun; no separate verifier consumer or live receipt. |
| API latency P50/P95/P99 | NOT MEASURED | Core API unavailable on local ports 12000/12003. |
| Queue wait / tasks per hour | NOT MEASURED | No running worker queue consumer. |
| GUI session utilization / safe concurrency | NOT MEASURED | Docker engine unavailable; profile capacity is not a runtime measurement. |
| Cost per task / model latency | NOT MEASURED | No live objective inference or end-to-end execution was run. |
| RAM/CPU/GPU high-water | NOT MEASURED | No isolated ANCOS runtime load test was available. |

## Benchmark protocol for future phases

Every result must include commit, hardware profile, OS/runtime versions, relevant configuration (secrets redacted), sample size, workload definition, warm-up, measured interval, raw output, and artifact hash. A task only counts as a success if the final artifact passes the independent verifier. Report retries, failures, cancellations, and verification time in total elapsed time.

At minimum collect:

- verified completion rate and quality score;
- total elapsed time and P50/P95/P99 task duration;
- queue wait, retry/failure/recovery counts, and starvation;
- model/provider latency, token use, and USD cost;
- worker and desktop CPU/RAM/VRAM high-water and utilization;
- desktop slot lease/reuse and checkpoint recovery outcomes;
- independent verification duration and rejection rate.

The 4× human-speed target remains a research metric. Compare equivalent fully completed tasks under equivalent starting conditions, and include observation, waits, retries, recovery, and independent verification in ANCOS elapsed time.
