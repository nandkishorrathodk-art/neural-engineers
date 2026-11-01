from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from neural.constants import MemoryCategory, TaskStatus
from neural.db.repositories import EmployeeRepository, EvolutionRepository, TaskRepository
from neural.logger import get_logger
from neural.memory.store import OrganizationalMemoryStore
from neural.methods.benchmarks import BenchmarkUnavailableError
from neural.methods.lab import MethodLabEngine
from neural.tools.engine import ToolsmithEngine

logger = get_logger("ancos.evolution.meta_learning")


@dataclass
class FailurePattern:
    pattern_id: str
    pattern_type: str
    frequency: int
    severity: str  # low, medium, high, critical
    sample_task_ids: list[str] = field(default_factory=list)
    root_cause: str = ""
    proposed_remediation: str = ""
    remediation_type: str = "prompt"  # "tool", "method", "prompt"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class FailurePatternAnalyzer:
    """
    Empirical failure pattern analyzer.
    Extracts, clusters, and diagnoses operational and verification failures from task logs.
    """

    def __init__(self, task_repo: TaskRepository | None = None) -> None:
        self.task_repo = task_repo

    async def analyze_failure_patterns(
        self,
        manual_failure_logs: list[dict[str, Any]] | None = None,
    ) -> list[FailurePattern]:
        logger.info("Analyzing operational failure logs for recurring anti-patterns")
        logs = list(manual_failure_logs or [])

        # Fetch failed tasks from database if task_repo available
        if self.task_repo:
            failed_tasks = await self.task_repo.list_tasks(status=TaskStatus.FAILED, limit=50)
            for t in failed_tasks:
                verification_note = ""
                if t.verification_result:
                    if isinstance(t.verification_result, dict):
                        verification_note = t.verification_result.get("notes", "") or t.verification_result.get("error", "")
                    elif isinstance(t.verification_result, str):
                        verification_note = t.verification_result
                logs.append({
                    "task_id": t.task_id,
                    "title": t.title,
                    "objective": t.objective,
                    "verification_notes": verification_note,
                    "retry_count": t.retry_count,
                })

        patterns_map: dict[str, FailurePattern] = {}

        for log in logs:
            task_id = log.get("task_id", f"TASK-{uuid.uuid4().hex[:6].upper()}")
            notes = str(log.get("verification_notes", "")).lower()
            title = str(log.get("title", "")).lower()
            error = str(log.get("error", "")).lower()
            combined = f"{notes} {title} {error}"

            if "async" in combined or "session" in combined or "await" in combined:
                ptype = "ASYNC_SESSION_MISMANAGEMENT"
                if ptype not in patterns_map:
                    patterns_map[ptype] = FailurePattern(
                        pattern_id=f"PAT-{uuid.uuid4().hex[:6].upper()}",
                        pattern_type=ptype,
                        frequency=0,
                        severity="high",
                        root_cause="Improper lifecycle handling of async database sessions and missing await statements.",
                        proposed_remediation="Add strict async context manager guidelines and synthesis rules in prompts.",
                        remediation_type="prompt",
                    )
                patterns_map[ptype].frequency += 1
                patterns_map[ptype].sample_task_ids.append(task_id)

            elif "timeout" in combined or "deadline" in combined or "latency" in combined:
                ptype = "TIMEOUT_EXCEEDED"
                if ptype not in patterns_map:
                    patterns_map[ptype] = FailurePattern(
                        pattern_id=f"PAT-{uuid.uuid4().hex[:6].upper()}",
                        pattern_type=ptype,
                        frequency=0,
                        severity="medium",
                        root_cause="Sequential long-running synchronous execution blocking worker loops.",
                        proposed_remediation="Decompose workload into concurrent parallel DAG tasks via Method Lab.",
                        remediation_type="method",
                    )
                patterns_map[ptype].frequency += 1
                patterns_map[ptype].sample_task_ids.append(task_id)

            elif "ast" in combined or "safety" in combined or "restricted" in combined:
                ptype = "SAFETY_BOUNDARY_VIOLATION"
                if ptype not in patterns_map:
                    patterns_map[ptype] = FailurePattern(
                        pattern_id=f"PAT-{uuid.uuid4().hex[:6].upper()}",
                        pattern_type=ptype,
                        frequency=0,
                        severity="critical",
                        root_cause="Attempted use of restricted builtins or low-level os commands violating safety boundary.",
                        proposed_remediation="Author verified specialized utility tool to satisfy capability without direct os calls.",
                        remediation_type="tool",
                    )
                patterns_map[ptype].frequency += 1
                patterns_map[ptype].sample_task_ids.append(task_id)

            elif "assert" in combined or "test" in combined or "fail" in combined:
                ptype = "TEST_ASSERTION_FAILURE"
                if ptype not in patterns_map:
                    patterns_map[ptype] = FailurePattern(
                        pattern_id=f"PAT-{uuid.uuid4().hex[:6].upper()}",
                        pattern_type=ptype,
                        frequency=0,
                        severity="high",
                        root_cause="Edge case logic discrepancy between implementation and verification test suite.",
                        proposed_remediation="Adopt Spec-Driven Code Synthesis method with pre-implementation test generation.",
                        remediation_type="method",
                    )
                patterns_map[ptype].frequency += 1
                patterns_map[ptype].sample_task_ids.append(task_id)

            else:
                ptype = "GENERIC_EXECUTION_DISCREPANCY"
                if ptype not in patterns_map:
                    patterns_map[ptype] = FailurePattern(
                        pattern_id=f"PAT-{uuid.uuid4().hex[:6].upper()}",
                        pattern_type=ptype,
                        frequency=0,
                        severity="low",
                        root_cause="General discrepancy between task objectives and output artifacts.",
                        proposed_remediation="Inject Chain-of-Verification self-critique steps into task reasoning.",
                        remediation_type="prompt",
                    )
                patterns_map[ptype].frequency += 1
                patterns_map[ptype].sample_task_ids.append(task_id)

        # If no failures present, provide proactive pattern for continuous learning
        if not patterns_map:
            proactive_pat = FailurePattern(
                pattern_id=f"PAT-{uuid.uuid4().hex[:6].upper()}",
                pattern_type="PROACTIVE_CAPABILITY_EXPANSION",
                frequency=1,
                severity="low",
                sample_task_ids=["SYS-PROACTIVE-01"],
                root_cause="Zero active failures detected. Proactively optimizing JSON schema serialization and verification.",
                proposed_remediation="Synthesize specialized JSON sanitizer tool and optimize verification prompts.",
                remediation_type="tool",
            )
            patterns_map[proactive_pat.pattern_type] = proactive_pat

        return list(patterns_map.values())


class PromptOptimizer:
    """
    Autonomous prompt optimizer.
    Formulates targeted prompt enhancements from failure patterns and evaluates improvements.
    """

    @staticmethod
    def optimize_prompt(
        current_prompt: str,
        failure_pattern: FailurePattern,
        target_role: str = "Senior Engineer",
    ) -> dict[str, Any]:
        logger.info("Formulating autonomous prompt optimization", role=target_role, pattern=failure_pattern.pattern_type)

        additions: list[str] = []
        if failure_pattern.pattern_type == "ASYNC_SESSION_MISMANAGEMENT":
            additions.append(
                "CRITICAL MANDATE: All database interactions MUST use 'async with session.begin():' or explicitly manage commit/rollback. Never leak un-awaited coroutines."
            )
        elif failure_pattern.pattern_type == "SAFETY_BOUNDARY_VIOLATION":
            additions.append(
                "STRICT SAFETY POLICY: Direct calls to os.system, subprocess, eval, or hardcoded API keys are forbidden. Use authorized sandboxed tools exclusively."
            )
        elif failure_pattern.pattern_type == "TEST_ASSERTION_FAILURE":
            additions.append(
                "TEST-FIRST VERIFICATION: Write and verify unit test assertions before synthesizing implementation code. Ensure all edge cases (empty inputs, None, bounds) are tested."
            )
        else:
            additions.append(
                f"RECURSIVE LEARNING DIRECTIVE: Address recurring bottleneck [{failure_pattern.root_cause}]. Follow empirical falsification before claiming milestone completion."
            )

        optimized_prompt = current_prompt.strip() + "\n\n" + "\n".join(additions)

        return {
            "target_role": target_role,
            "pattern_type": failure_pattern.pattern_type,
            "candidate_status": "not_evaluated",
            "baseline_prompt_length": len(current_prompt),
            "optimized_prompt_length": len(optimized_prompt),
            "rules_injected": additions,
            "baseline_quality_score": None,
            "optimized_quality_score": None,
            "improvement_pct": None,
            "optimized_prompt": optimized_prompt,
        }


class RecursiveSelfImprovementEngine:
    """
    Recursive Self-Improvement & Continuous Meta-Learning Engine.
    Closed-loop autonomous capability evolution:
    1. Harvest operational execution receipts & failure patterns.
    2. Formulate targeted remediation (tools, methods, prompt adjustments).
    3. Synthesize & test new tools via Toolsmith.
    4. Formulate & benchmark novel problem-solving methods via Method Lab.
    5. Optimize workforce prompts and commit learnings to 4-Tier Memory.
    6. Record immutable evolution records for organizational tracking.
    """

    def __init__(
        self,
        evolution_repo: EvolutionRepository | None = None,
        task_repo: TaskRepository | None = None,
        employee_repo: EmployeeRepository | None = None,
        memory_store: OrganizationalMemoryStore | None = None,
        toolsmith: ToolsmithEngine | None = None,
        method_lab: MethodLabEngine | None = None,
    ) -> None:
        self.evolution_repo = evolution_repo
        self.task_repo = task_repo
        self.employee_repo = employee_repo
        self.memory_store = memory_store or OrganizationalMemoryStore()
        self.toolsmith = toolsmith or ToolsmithEngine()
        self.method_lab = method_lab or MethodLabEngine()
        self.pattern_analyzer = FailurePatternAnalyzer(task_repo=task_repo)
        self.prompt_optimizer = PromptOptimizer()

    async def run_self_improvement_cycle(
        self,
        trigger_reason: str = "scheduled_periodic",
        auto_apply: bool = False,
        sample_failures: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        cycle_id = f"SELF-EVO-{uuid.uuid4().hex[:8].upper()}"
        start_time = datetime.now(UTC)
        logger.info("Triggering recursive self-improvement cycle", cycle_id=cycle_id, trigger=trigger_reason)

        # 1. Analyze failure patterns
        patterns = await self.pattern_analyzer.analyze_failure_patterns(manual_failure_logs=sample_failures)

        actions_taken = []
        tools_authored = []
        methods_synthesized = []
        prompts_optimized = []
        memories_stored = []

        # 2. Process identified patterns
        for pat in patterns[:3]:
            # Toolsmith remediation
            if pat.remediation_type == "tool" or pat.pattern_type in ("SAFETY_BOUNDARY_VIOLATION", "PROACTIVE_CAPABILITY_EXPANSION"):
                tool_name = f"safe_sanitizer_{uuid.uuid4().hex[:4]}"
                code = """
import json

def sanitize_payload(raw_data: str) -> dict:
    \"\"\"Safely parse and sanitize incoming payload without eval or unsafe calls.\"\"\"
    try:
        data = json.loads(raw_data)
        if not isinstance(data, dict):
            return {"valid": False, "sanitized": {}}
        clean = {str(k): v for k, v in data.items() if not str(k).startswith("_")}
        return {"valid": True, "sanitized": clean}
    except Exception as e:
        return {"valid": False, "error": str(e)}
"""
                test_code = """
from tool import sanitize_payload

res1 = sanitize_payload('{"user": "alice", "_secret": "123"}')
assert res1["valid"] is True
assert "_secret" not in res1["sanitized"]
assert res1["sanitized"]["user"] == "alice"

res2 = sanitize_payload('invalid json')
assert res2["valid"] is False
print("ALL_SANITIZER_TESTS_PASSED")
"""
                tool_rec, receipt = await self.toolsmith.author_tool(
                    name=tool_name,
                    code=code,
                    test_code=test_code,
                    description=f"Automated tool created to remediate {pat.pattern_type}",
                    category="security",
                    author_id="ToolsmithRecursiveAI",
                    auto_test=True,
                )
                tools_authored.append({
                    "tool_id": tool_rec.tool_id,
                    "name": tool_rec.name,
                    "status": tool_rec.status,
                    "receipt_id": receipt.receipt_id if receipt else None,
                })
                actions_taken.append(f"Authored and verified tool: {tool_name}")

            # Method Lab remediation
            elif pat.remediation_type == "method" or pat.pattern_type in ("TIMEOUT_EXCEEDED", "TEST_ASSERTION_FAILURE"):
                strat = "spec_driven_synthesis" if "test" in pat.pattern_type.lower() else "decomposition_parallel"
                m_name = f"adaptive_{strat}_{uuid.uuid4().hex[:4]}"
                method_rec = await self.method_lab.synthesize_method(
                    name=m_name,
                    objective=f"Autonomous strategy remediating {pat.pattern_type}",
                    strategy_type=strat,
                    domain="engineering",
                    author_id="MethodLabRecursiveAI",
                )
                # Benchmark method
                try:
                    _, scorecard = await self.method_lab.benchmark_method(method_rec.method_id)
                except BenchmarkUnavailableError as err:
                    methods_synthesized.append({
                        "method_id": method_rec.method_id,
                        "name": method_rec.name,
                        "status": "not_evaluated",
                        "evaluation_reason": str(err),
                    })
                    actions_taken.append(f"Staged unevaluated method candidate: {m_name}")
                else:
                    # Benchmarking can nominate a candidate, but this loop has
                    # no approval receipt and therefore cannot activate it.
                    if scorecard.recommendation == "promote":
                        methods_synthesized.append({
                            "method_id": method_rec.method_id,
                            "name": method_rec.name,
                            "status": "awaiting_human_approval",
                            "composite_score": scorecard.composite_score,
                            "accuracy": scorecard.accuracy,
                            "relative_improvement_pct": scorecard.relative_improvement_pct,
                            "auto_apply_blocked": auto_apply,
                        })
                        actions_taken.append(f"Synthesized and benchmarked method candidate for human review: {m_name}")

            # Prompt optimization remediation
            if pat.remediation_type == "prompt" or pat.pattern_type == "ASYNC_SESSION_MISMANAGEMENT":
                base_prompt = "You are a Senior Engineer at ANCOS. Write robust, maintainable code."
                opt_res = self.prompt_optimizer.optimize_prompt(
                    current_prompt=base_prompt,
                    failure_pattern=pat,
                    target_role="Senior Backend Engineer",
                )
                prompts_optimized.append(opt_res)
                actions_taken.append(
                    f"Generated unevaluated prompt candidate for Senior Backend Engineer regarding {pat.pattern_type}"
                )

            # 3. Store in 4-Tier Memory
            mem_entry = await self.memory_store.store_entry(
                category=MemoryCategory.LESSON,
                source=f"meta_learning:{cycle_id}",
                content=f"Failure pattern remediated: {pat.pattern_type}. Root cause: {pat.root_cause}. Remediation: {pat.proposed_remediation}",
                metadata_json={"pattern": pat.to_dict(), "cycle_id": cycle_id},
            )
            memories_stored.append(mem_entry.id)

        # 4. Commit EvolutionRecord
        saved_record_id: str | None = None
        if self.evolution_repo:
            rec = await self.evolution_repo.create_record(
                record_type="meta_learning_cycle",
                hypothesis=f"Autonomous recursive self-improvement cycle {cycle_id} addressing {len(patterns)} failure patterns",
                experiment_details={
                    "cycle_id": cycle_id,
                    "patterns": [p.to_dict() for p in patterns],
                    "tools_authored": tools_authored,
                    "methods_synthesized": methods_synthesized,
                    "prompts_optimized": prompts_optimized,
                },
                baseline_metrics={"active_failure_patterns": len(patterns)},
                experiment_metrics={
                    "tools_count": len(tools_authored),
                    "methods_count": len(methods_synthesized),
                    "prompts_count": len(prompts_optimized),
                    "memories_created": len(memories_stored),
                },
                status="completed",
                version="1.2.0",
            )
            saved_record_id = rec.id

        duration = round((datetime.now(UTC) - start_time).total_seconds(), 3)
        logger.info("Completed recursive self-improvement cycle", cycle_id=cycle_id, duration_s=duration)

        return {
            "cycle_id": cycle_id,
            "status": "completed",
            "trigger_reason": trigger_reason,
            "duration_seconds": duration,
            "failure_patterns_detected": [p.to_dict() for p in patterns],
            "actions_taken": actions_taken,
            "tools_authored": tools_authored,
            "methods_synthesized": methods_synthesized,
            "prompts_optimized": prompts_optimized,
            "memories_stored_count": len(memories_stored),
            "evolution_record_id": saved_record_id,
            "timestamp": datetime.now(UTC).isoformat(),
        }
