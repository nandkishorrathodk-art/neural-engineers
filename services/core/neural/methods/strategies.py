from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class StrategyType(str, Enum):
    DECOMPOSITION_PARALLEL = "decomposition_parallel"
    CHAIN_OF_VERIFICATION = "chain_of_verification"
    SPEC_DRIVEN_SYNTHESIS = "spec_driven_synthesis"
    HYPOTHESIS_DRIVEN_DEBUGGING = "hypothesis_driven_debugging"
    ADVERSARIAL_CRITIQUE = "adversarial_critique"
    ITERATIVE_REFINEMENT = "iterative_refinement"


@dataclass
class MethodStep:
    step_number: int
    name: str
    description: str
    pre_conditions: list[str] = field(default_factory=list)
    post_conditions: list[str] = field(default_factory=list)
    required_capabilities: list[str] = field(default_factory=list)
    fallback_action: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_number": self.step_number,
            "name": self.name,
            "description": self.description,
            "pre_conditions": self.pre_conditions,
            "post_conditions": self.post_conditions,
            "required_capabilities": self.required_capabilities,
            "fallback_action": self.fallback_action,
        }


def get_standard_strategy_blueprint(strategy_type: StrategyType | str) -> list[dict[str, Any]]:
    st = strategy_type.value if isinstance(strategy_type, StrategyType) else strategy_type

    if st == StrategyType.CHAIN_OF_VERIFICATION.value:
        return [
            MethodStep(
                step_number=1,
                name="GenerateBaselineResponse",
                description="Formulate initial answer or solution draft.",
                post_conditions=["Draft response exists"],
            ).to_dict(),
            MethodStep(
                step_number=2,
                name="PlanVerificationQuestions",
                description="Identify factual assertions and generate discriminating verification checks.",
                pre_conditions=["Draft response exists"],
                post_conditions=["Verification checklist compiled"],
            ).to_dict(),
            MethodStep(
                step_number=3,
                name="ExecuteVerificationChecks",
                description="Run sandboxed queries or tests to empirically confirm each fact.",
                pre_conditions=["Verification checklist compiled"],
                post_conditions=["Empirical fact receipts gathered"],
            ).to_dict(),
            MethodStep(
                step_number=4,
                name="SynthesizeVerifiedFinal",
                description="Incorporate verification results into final verified deliverable.",
                pre_conditions=["Empirical fact receipts gathered"],
                post_conditions=["Final verified answer free of hallucinations"],
            ).to_dict(),
        ]

    elif st == StrategyType.SPEC_DRIVEN_SYNTHESIS.value:
        return [
            MethodStep(
                step_number=1,
                name="FormalizeSpecification",
                description="Translate ambiguous task requirements into typed interface contracts.",
                post_conditions=["Formal specification defined"],
            ).to_dict(),
            MethodStep(
                step_number=2,
                name="GenerateAdversarialTests",
                description="Create executable test assertions covering edge cases before code synthesis.",
                pre_conditions=["Formal specification defined"],
                post_conditions=["Test harness compiled"],
            ).to_dict(),
            MethodStep(
                step_number=3,
                name="SynthesizeImplementation",
                description="Write minimal implementation fulfilling the formal spec.",
                pre_conditions=["Test harness compiled"],
                post_conditions=["Source code generated"],
            ).to_dict(),
            MethodStep(
                step_number=4,
                name="SandboxValidationLoop",
                description="Execute tests inside sandbox, iterate on failures until 100% green.",
                pre_conditions=["Source code generated"],
                post_conditions=["Cryptographic execution receipt verified"],
            ).to_dict(),
        ]

    elif st == StrategyType.HYPOTHESIS_DRIVEN_DEBUGGING.value:
        return [
            MethodStep(
                step_number=1,
                name="IngestErrorTrace",
                description="Parse stacktrace, logs, and unexpected state transitions.",
                post_conditions=["Failure signature isolated"],
            ).to_dict(),
            MethodStep(
                step_number=2,
                name="FormulateCompetingHypotheses",
                description="Generate mutually exclusive hypotheses explaining the failure mode.",
                pre_conditions=["Failure signature isolated"],
                post_conditions=["Hypotheses ranked by likelihood"],
            ).to_dict(),
            MethodStep(
                step_number=3,
                name="ExecuteDiscriminatingProbe",
                description="Run minimal reproducible probe to falsify hypotheses.",
                pre_conditions=["Hypotheses ranked by likelihood"],
                post_conditions=["Root cause uniquely determined"],
            ).to_dict(),
            MethodStep(
                step_number=4,
                name="ApplyAndVerifyPatch",
                description="Implement targeted remediation and verify regression suite passes.",
                pre_conditions=["Root cause uniquely determined"],
                post_conditions=["Zero regressions confirmed"],
            ).to_dict(),
        ]

    # Default Decomposition & Parallel Dispatch
    return [
        MethodStep(
            step_number=1,
            name="DecomposeRequirements",
            description="Divide goal into orthogonal sub-tasks with typed I/O boundaries.",
            post_conditions=["DAG task graph created"],
        ).to_dict(),
        MethodStep(
            step_number=2,
            name="DispatchParallelWorkers",
            description="Assign non-dependent tasks to available workforce workers concurrently.",
            pre_conditions=["DAG task graph created"],
            post_conditions=["Worker tasks running"],
        ).to_dict(),
        MethodStep(
            step_number=3,
            name="AggregateArtifacts",
            description="Collect worker receipts and merge artifacts.",
            pre_conditions=["Worker tasks completed"],
            post_conditions=["Aggregated deliverable ready for QA"],
        ).to_dict(),
    ]

