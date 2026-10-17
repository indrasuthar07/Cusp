"""
ml/datasets/scenarios.py

Deterministic scenario definitions for the Cusp Phase 7 seed dataset.

Each scenario has a stable scenario_id and task_id.  Scenarios are grouped
into tasks so Task 7.5 can split the dataset by task (not by row) to prevent
train/test leakage.

Task groups
-----------
task-A-fix-auth-bug        : successful and recovery scenarios (fake)
task-B-refactor-api        : loop detection scenarios (fake)
task-C-budget-constraint   : budget and quota scenarios (fake)
task-D-failure-modes       : provider errors and egress denial (fake)
task-E-test-suite          : test-writing workflows (fake)
task-F-dep-update          : downgrade policy and classification (fake)
task-G-ci-setup            : simulated Ollama CI scenarios
task-H-db-migration        : simulated Ollama migration scenarios

Provider note
-------------
"ollama_simulated" provider_kind uses provider_profile="ollama" so that the
governance engine assigns virtual cost provenance (same as real Ollama).  The
simulation runs fully offline — no Ollama process is required.

Schema versions
---------------
Increment FEATURE_SCHEMA_VERSION when adding/removing/renaming feature fields.
Increment LABEL_SCHEMA_VERSION when changing label definitions or sentinel values.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Schema versioning
# 1.1.0 adds virtual/provider cost provenance to benchmark feature exports.
FEATURE_SCHEMA_VERSION = "1.1.0"
LABEL_SCHEMA_VERSION = "1.0.0"

# Stable task identifiers
TASK_A = "task-A-fix-auth-bug"
TASK_B = "task-B-refactor-api"
TASK_C = "task-C-budget-constraint"
TASK_D = "task-D-failure-modes"
TASK_E = "task-E-test-suite"
TASK_F = "task-F-dep-update"
TASK_G = "task-G-ci-setup"
TASK_H = "task-H-db-migration"


# Data models

@dataclass(frozen=True)
class ActionStep:
    """One action in a scenario's deterministic action sequence.

    All fields are set at scenario definition time; none depend on runtime state.

    Fields
    ------
    action_type          : model_call | tool_call | test_run | file_change | context_read
    provider_profile     : fake | ollama | gemini | groq | ...
    fingerprint          : canonical content fingerprint for exact-repeat detection
    estimated_cost_micro_usd : pre-action cost estimate in micro-USD
    actual_cost_micro_usd    : simulated post-execution cost in micro-USD (0 for failed)
    expected_verdict     : verdict PolicyResolver must return (asserted by generator)
    action_outcome       : completed | failed (simulated post-execution result)
    """

    action_type: str
    provider_profile: str
    fingerprint: str
    estimated_cost_micro_usd: int
    actual_cost_micro_usd: int
    diff_bytes: int = 0
    tests_passed: int = 0
    tests_failed: int = 0
    elapsed_ms: int = 200
    semantic_similarity: float | None = None
    embedding_available: bool = False
    provider_error_code: str | None = None
    data_classification: str = "internal"
    expected_verdict: str = "allow"
    action_outcome: str = "completed"


@dataclass
class ScenarioDefinition:
    """One reproducible scenario capturing a complete agent run.

    Fields
    ------
    scenario_id           : stable unique identifier e.g. "scn-fake-001"
    task_id               : task group for disjoint splitting e.g. "task-A-fix-auth-bug"
    provider_kind         : "fake" | "ollama_simulated"
    policy_rules          : JSON-serializable dict passed to PolicyResolver
    budget_cap_micro_usd  : run-level hard budget cap
    hosted_egress_allowed : whether the simulated agent has egress consent
    run_outcome           : verified final run state (succeeded/failed/paused/terminated)
    verified_success      : ground truth — NOT agent-declared success
    action_sequence       : ordered list of ActionStep objects
    """

    scenario_id: str
    task_id: str
    description: str
    provider_kind: str
    policy_rules: dict
    budget_cap_micro_usd: int
    hosted_egress_allowed: bool
    run_outcome: str
    verified_success: bool
    action_sequence: list[ActionStep] = field(default_factory=list)


# Helper

def _step(
    action_type: str,
    provider_profile: str,
    fingerprint: str,
    estimated_cost: int,
    actual_cost: int,
    *,
    diff_bytes: int = 0,
    tests_passed: int = 0,
    tests_failed: int = 0,
    elapsed_ms: int = 200,
    semantic_similarity: float | None = None,
    embedding_available: bool = False,
    provider_error_code: str | None = None,
    data_classification: str = "internal",
    expected_verdict: str = "allow",
    action_outcome: str = "completed",
) -> ActionStep:
    """Compact ActionStep constructor with keyword-only optional fields."""
    return ActionStep(
        action_type=action_type,
        provider_profile=provider_profile,
        fingerprint=fingerprint,
        estimated_cost_micro_usd=estimated_cost,
        actual_cost_micro_usd=actual_cost,
        diff_bytes=diff_bytes,
        tests_passed=tests_passed,
        tests_failed=tests_failed,
        elapsed_ms=elapsed_ms,
        semantic_similarity=semantic_similarity,
        embedding_available=embedding_available,
        provider_error_code=provider_error_code,
        data_classification=data_classification,
        expected_verdict=expected_verdict,
        action_outcome=action_outcome,
    )


# Shared policy templates 

_POLICY_STANDARD = {
    "default_action": "allow",
    "budgets": {
        "run_cap_micro_usd": 100_000,
        "organization_cap_micro_usd": 1_000_000,
    },
    "loop_detection": {
        "exact_repeat_limit": 3,
        "semantic_similarity_threshold": 0.97,
        "semantic_repeat_limit": 3,
    },
    "fail_safe": {
        "on_budget_check_failure": "pause",
        "on_provider_quota_failure": "pause",
    },
}

_POLICY_OLLAMA_LOCAL = {
    "default_action": "allow",
    "budgets": {
        "run_cap_micro_usd": 500_000,       # larger virtual budget for local compute
        "organization_cap_micro_usd": 5_000_000,
    },
    "loop_detection": {
        "exact_repeat_limit": 3,
        "semantic_similarity_threshold": 0.97,
        "semantic_repeat_limit": 3,
    },
    "fail_safe": {
        "on_budget_check_failure": "pause",
        "on_provider_quota_failure": "pause",
    },
}


# SCENARIOS
SCENARIOS: list[ScenarioDefinition] = [

    # Task A: fix-auth-bug 

    ScenarioDefinition(
        scenario_id="scn-fake-001",
        task_id=TASK_A,
        description=(
            "Clean success: model reads context, writes fix, tests all pass. "
            "No loops, budget comfortable."
        ),
        provider_kind="fake",
        policy_rules=_POLICY_STANDARD,
        budget_cap_micro_usd=100_000,
        hosted_egress_allowed=False,
        run_outcome="succeeded",
        verified_success=True,
        action_sequence=[
            # All actions: budget check passes, no error, no repeat → allow
            _step("model_call",  "fake", "fp-A01-read",  1000, 950,  elapsed_ms=120),
            _step("model_call",  "fake", "fp-A01-plan",  2000, 1900, diff_bytes=512,  elapsed_ms=180),
            _step("file_change", "fake", "fp-A01-impl",  0,    0,    diff_bytes=1024, elapsed_ms=50),
            _step("test_run",    "fake", "fp-A01-test1", 200,  200,  tests_passed=5,  elapsed_ms=800),
            _step("model_call",  "fake", "fp-A01-fix",   1000, 900,  diff_bytes=256,  elapsed_ms=150),
            _step("test_run",    "fake", "fp-A01-test2", 200,  200,  tests_passed=6,  elapsed_ms=750),
        ],
    ),

    ScenarioDefinition(
        scenario_id="scn-fake-002",
        task_id=TASK_A,
        description=(
            "Success with iterative test fixes: tests first fail, then pass after "
            "targeted file edits."
        ),
        provider_kind="fake",
        policy_rules=_POLICY_STANDARD,
        budget_cap_micro_usd=100_000,
        hosted_egress_allowed=False,
        run_outcome="succeeded",
        verified_success=True,
        action_sequence=[
            _step("model_call",  "fake", "fp-A02-analyze", 2000, 1800, elapsed_ms=200),
            _step("file_change", "fake", "fp-A02-impl",    0,    0,    diff_bytes=2048, elapsed_ms=40),
            _step("test_run",    "fake", "fp-A02-run1",    300,  300,
                  tests_passed=3, tests_failed=2, elapsed_ms=1200),
            _step("file_change", "fake", "fp-A02-fix1",    0,    0,    diff_bytes=512, elapsed_ms=30),
            _step("test_run",    "fake", "fp-A02-run2",    300,  300,
                  tests_passed=5, tests_failed=0, elapsed_ms=1100),
            _step("model_call",  "fake", "fp-A02-review",  1000, 950, diff_bytes=128, elapsed_ms=130),
        ],
    ),

    ScenarioDefinition(
        scenario_id="scn-fake-003",
        task_id=TASK_A,
        description=(
            "Exact-repeat loop: same fingerprint repeated with no progress. "
            "Policy pauses on the 3rd occurrence (exact_repeat_limit=3)."
        ),
        provider_kind="fake",
        policy_rules=_POLICY_STANDARD,
        budget_cap_micro_usd=100_000,
        hosted_egress_allowed=False,
        run_outcome="paused",
        verified_success=False,
        action_sequence=[
            # A0: recent=[], repeats=0, 0+1=1 < 3 → allow
            _step("model_call", "fake", "fp-A03-stuck", 1000, 950, elapsed_ms=150),
            # A1: recent=[A0(fp-A03-stuck)], repeats=1, 1+1=2 < 3 → allow
            _step("model_call", "fake", "fp-A03-stuck", 1000, 950, elapsed_ms=155),
            # A2: recent=[A0,A1], repeats=2, 2+1=3 >= 3, not made_progress → PAUSE
            _step("model_call", "fake", "fp-A03-stuck", 1000, 0, elapsed_ms=160,
                  expected_verdict="pause", action_outcome="failed"),
        ],
    ),

    # Task B: refactor-api
    ScenarioDefinition(
        scenario_id="scn-fake-004",
        task_id=TASK_B,
        description=(
            "Repeat loop broken by progress: fingerprint repeats reach limit but "
            "diff_bytes>0 on action 3 sets made_progress=True, suppressing the pause. "
            "Run recovers and succeeds."
        ),
        provider_kind="fake",
        policy_rules=_POLICY_STANDARD,
        budget_cap_micro_usd=100_000,
        hosted_egress_allowed=False,
        run_outcome="succeeded",
        verified_success=True,
        action_sequence=[
            # A0: recent=[], repeats=0, 1 < 3 → allow
            _step("model_call",  "fake", "fp-B04-try",    1000, 950),
            # A1: recent=[A0], repeats=1, 2 < 3 → allow
            _step("model_call",  "fake", "fp-B04-try",    1000, 950),
            # A2: recent=[A0,A1], repeats=2, 3>=3, BUT diff_bytes=512 → made_progress=True → SUPPRESSED → allow
            _step("file_change", "fake", "fp-B04-try",    0,    0,   diff_bytes=512),
            # A3: different fp, repeats=0 → allow
            _step("test_run",    "fake", "fp-B04-test",   200,  200, tests_passed=4),
            # A4: different fp → allow
            _step("model_call",  "fake", "fp-B04-review", 1000, 900, diff_bytes=256),
        ],
    ),

    ScenarioDefinition(
        scenario_id="scn-fake-005",
        task_id=TASK_B,
        description=(
            "Semantic loop: high cosine similarity (>=0.95) with no progress. "
            "Pauses when semantic_repeats reaches semantic_repeat_limit=2."
        ),
        provider_kind="fake",
        policy_rules={
            **_POLICY_STANDARD,
            "loop_detection": {
                "exact_repeat_limit": 10,           # exact-repeat won't fire
                "semantic_similarity_threshold": 0.95,
                "semantic_repeat_limit": 2,
            },
        },
        budget_cap_micro_usd=100_000,
        hosted_egress_allowed=False,
        run_outcome="paused",
        verified_success=False,
        action_sequence=[
            # A0: embed=False → semantic check skipped → allow
            _step("model_call", "fake", "fp-B05-s0", 1000, 950,
                  embedding_available=False),
            # A1: embed=True, sim=0.97>=0.95, not made_progress
            #   recent qualifies: 0 (A0 embed=False); current adds 1 → sem_repeats=1; 1>=2? NO → allow
            _step("model_call", "fake", "fp-B05-s1", 1000, 950,
                  semantic_similarity=0.97, embedding_available=True),
            # A2: embed=True, sim=0.98>=0.95, not made_progress
            #   recent: A1 qualifies (embed=True,sim=0.97>=0.95,not made_prog) → count=1
            #   current adds 1 → sem_repeats=2; semantic_is_loop=True; 2>=max(1,2)=2 → PAUSE
            _step("model_call", "fake", "fp-B05-s2", 1000, 0,
                  semantic_similarity=0.98, embedding_available=True,
                  expected_verdict="pause", action_outcome="failed"),
        ],
    ),

    ScenarioDefinition(
        scenario_id="scn-fake-006",
        task_id=TASK_B,
        description=(
            "Policy default_action=downgrade: every action is routed to the cheaper "
            "ollama profile. Run completes successfully with downgraded model."
        ),
        provider_kind="fake",
        policy_rules={
            "default_action": "downgrade",
            "downgrade_profile": "ollama",
            "budgets": {
                "run_cap_micro_usd": 100_000,
                "organization_cap_micro_usd": 1_000_000,
            },
            "loop_detection": {
                "exact_repeat_limit": 3,
                "semantic_similarity_threshold": 0.97,
                "semantic_repeat_limit": 3,
            },
            "fail_safe": {
                "on_budget_check_failure": "pause",
                "on_provider_quota_failure": "pause",
            },
        },
        budget_cap_micro_usd=100_000,
        hosted_egress_allowed=False,
        run_outcome="succeeded",
        verified_success=True,
        action_sequence=[
            # No other rule fires → default_action=downgrade with approved_profile=ollama
            _step("model_call", "fake", "fp-B06-task",   2000, 1900,
                  expected_verdict="downgrade"),
            _step("model_call", "fake", "fp-B06-impl",   2000, 1900, diff_bytes=1024,
                  expected_verdict="downgrade"),
            _step("test_run",   "fake", "fp-B06-test",   200,  200,  tests_passed=4,
                  expected_verdict="downgrade"),
            _step("model_call", "fake", "fp-B06-review", 1000, 950,  diff_bytes=256,
                  expected_verdict="downgrade"),
            _step("test_run",   "fake", "fp-B06-final",  200,  200,  tests_passed=5,
                  expected_verdict="downgrade"),
        ],
    ),

    # Task C: budget-constraint
    ScenarioDefinition(
        scenario_id="scn-fake-007",
        task_id=TASK_C,
        description=(
            "Run budget cap exceeded: run_cap=3000 micro-USD. "
            "Three actions complete (total spent=2850); "
            "fourth action pushes run_spent+estimated over cap → pause."
        ),
        provider_kind="fake",
        policy_rules={
            "default_action": "allow",
            "budgets": {
                "run_cap_micro_usd": 3_000,
                "organization_cap_micro_usd": 1_000_000,
            },
            "fail_safe": {"on_budget_check_failure": "pause"},
        },
        budget_cap_micro_usd=3_000,
        hosted_egress_allowed=False,
        run_outcome="paused",
        verified_success=False,
        action_sequence=[
            # 0+0+1000=1000 <= 3000 → allow; after: spent=950
            _step("model_call", "fake", "fp-C07-a0", 1000, 950),
            # 950+0+1000=1950 <= 3000 → allow; after: spent=1900
            _step("model_call", "fake", "fp-C07-a1", 1000, 950),
            # 1900+0+1000=2900 <= 3000 → allow; after: spent=2850
            _step("model_call", "fake", "fp-C07-a2", 1000, 950),
            # 2850+0+1000=3850 > 3000 → PAUSE (run_budget_cap_exceeded)
            _step("model_call", "fake", "fp-C07-a3", 1000, 0,
                  expected_verdict="pause", action_outcome="failed"),
        ],
    ),

    ScenarioDefinition(
        scenario_id="scn-fake-008",
        task_id=TASK_C,
        description=(
            "Provider quota exhausted: second action reports quota_exhausted. "
            "fail_safe.on_provider_quota_failure=pause → run stops."
        ),
        provider_kind="fake",
        policy_rules=_POLICY_STANDARD,
        budget_cap_micro_usd=100_000,
        hosted_egress_allowed=False,
        run_outcome="paused",
        verified_success=False,
        action_sequence=[
            _step("model_call", "fake", "fp-C08-a0", 1000, 950),
            # quota_exhausted ∈ {provider_quota_exhausted, quota_exhausted, rate_limited}
            # → on_provider_quota_failure=pause → PAUSE (provider_quota_exhausted)
            _step("model_call", "fake", "fp-C08-a1", 1000, 0,
                  provider_error_code="quota_exhausted",
                  expected_verdict="pause", action_outcome="failed"),
        ],
    ),

    ScenarioDefinition(
        scenario_id="scn-fake-009",
        task_id=TASK_C,
        description=(
            "Quota exhausted triggers downgrade to ollama. "
            "Run continues on the cheaper profile and succeeds."
        ),
        provider_kind="fake",
        policy_rules={
            "default_action": "allow",
            "budgets": {
                "run_cap_micro_usd": 100_000,
                "organization_cap_micro_usd": 1_000_000,
            },
            "loop_detection": {
                "exact_repeat_limit": 3,
                "semantic_similarity_threshold": 0.97,
                "semantic_repeat_limit": 3,
            },
            "fail_safe": {
                "on_budget_check_failure": "pause",
                "on_provider_quota_failure": "downgrade",   # downgrade instead of pause
            },
            "quota_downgrade_profile": "ollama",
        },
        budget_cap_micro_usd=100_000,
        hosted_egress_allowed=False,
        run_outcome="succeeded",
        verified_success=True,
        action_sequence=[
            _step("model_call", "fake",   "fp-C09-start",     1000, 950),
            # quota_exhausted → on_provider_quota_failure=downgrade → DOWNGRADE(profile=ollama)
            _step("model_call", "fake",   "fp-C09-quota-hit", 1000, 950,
                  provider_error_code="quota_exhausted",
                  expected_verdict="downgrade"),
            # Continue on ollama after downgrade
            _step("model_call", "ollama", "fp-C09-impl",      2000, 1800, diff_bytes=512),
            _step("test_run",   "ollama", "fp-C09-test",      500,  500,  tests_passed=4),
        ],
    ),

    # Task D: failure-modes
    ScenarioDefinition(
        scenario_id="scn-fake-010",
        task_id=TASK_D,
        description=(
            "Provider timeout error: not a quota error, but the generic provider_error "
            "path in the engine still uses on_provider_quota_failure=pause → run stops."
        ),
        provider_kind="fake",
        policy_rules=_POLICY_STANDARD,
        budget_cap_micro_usd=100_000,
        hosted_egress_allowed=False,
        run_outcome="paused",
        verified_success=False,
        action_sequence=[
            _step("model_call", "fake", "fp-D10-a0",      1000, 950),
            # provider_timeout not in quota set, but truthy provider_error_code
            # → generic error branch → on_provider_quota_failure=pause → PAUSE (provider_error)
            _step("model_call", "fake", "fp-D10-timeout", 1000, 0,
                  provider_error_code="provider_timeout",
                  expected_verdict="pause", action_outcome="failed"),
        ],
    ),

    ScenarioDefinition(
        scenario_id="scn-fake-011",
        task_id=TASK_D,
        description=(
            "Hosted egress consent required: policy has hosted_egress.enabled=True "
            "but the simulated agent has hosted_egress_allowed=False → immediate pause."
        ),
        provider_kind="fake",
        policy_rules={
            "default_action": "allow",
            "budgets": {
                "run_cap_micro_usd": 100_000,
                "organization_cap_micro_usd": 1_000_000,
            },
            "hosted_egress": {
                "enabled": True,
                "allowed_data_classes": ["public", "internal"],
            },
            "loop_detection": {
                "exact_repeat_limit": 3,
                "semantic_similarity_threshold": 0.97,
                "semantic_repeat_limit": 3,
            },
            "fail_safe": {
                "on_budget_check_failure": "pause",
                "on_provider_quota_failure": "pause",
            },
        },
        budget_cap_micro_usd=100_000,
        hosted_egress_allowed=False,  # agent lacks egress consent
        run_outcome="paused",
        verified_success=False,
        action_sequence=[
            # hosted_egress.enabled=True AND not hosted_egress_allowed → PAUSE (hosted_egress_consent_required)
            _step("model_call", "gemini", "fp-D11-start", 5000, 0,
                  data_classification="internal",
                  expected_verdict="pause", action_outcome="failed"),
        ],
    ),

    ScenarioDefinition(
        scenario_id="scn-fake-012",
        task_id=TASK_D,
        description=(
            "Organization budget cap exceeded: org_cap=2000 micro-USD, tight. "
            "Two actions complete (org_spent=1900); third pushes org total over cap → pause."
        ),
        provider_kind="fake",
        policy_rules={
            "default_action": "allow",
            "budgets": {
                "run_cap_micro_usd": 100_000,
                "organization_cap_micro_usd": 2_000,   # tight org cap
            },
            "fail_safe": {"on_budget_check_failure": "pause"},
        },
        budget_cap_micro_usd=100_000,
        hosted_egress_allowed=False,
        run_outcome="paused",
        verified_success=False,
        action_sequence=[
            # org: 0+0+1000=1000 <= 2000 → allow; org_spent=950
            _step("model_call", "fake", "fp-D12-a0", 1000, 950),
            # org: 950+0+1000=1950 <= 2000 → allow; org_spent=1900
            _step("model_call", "fake", "fp-D12-a1", 1000, 950),
            # org: 1900+0+1000=2900 > 2000 → PAUSE (organization_budget_cap_exceeded)
            _step("model_call", "fake", "fp-D12-a2", 1000, 0,
                  expected_verdict="pause", action_outcome="failed"),
        ],
    ),

    # Task E: test-suite
    ScenarioDefinition(
        scenario_id="scn-fake-013",
        task_id=TASK_E,
        description=(
            "Test-writing workflow: plan, implement tests, iterate until all pass. "
            "Eight actions, all allowed, verified success."
        ),
        provider_kind="fake",
        policy_rules=_POLICY_STANDARD,
        budget_cap_micro_usd=100_000,
        hosted_egress_allowed=False,
        run_outcome="succeeded",
        verified_success=True,
        action_sequence=[
            _step("model_call",  "fake", "fp-E13-plan",         2000, 1800),
            _step("file_change", "fake", "fp-E13-create-tests", 0,    0,   diff_bytes=2048),
            _step("test_run",    "fake", "fp-E13-run1",         300,  300,
                  tests_passed=0, tests_failed=5, elapsed_ms=1000),
            _step("file_change", "fake", "fp-E13-fix1",         0,    0,   diff_bytes=512),
            _step("test_run",    "fake", "fp-E13-run2",         300,  300,
                  tests_passed=3, tests_failed=2, elapsed_ms=1000),
            _step("file_change", "fake", "fp-E13-fix2",         0,    0,   diff_bytes=256),
            _step("test_run",    "fake", "fp-E13-run3",         300,  300,
                  tests_passed=5, tests_failed=0, elapsed_ms=950),
            _step("model_call",  "fake", "fp-E13-review",       1000, 950, diff_bytes=64),
        ],
    ),

    ScenarioDefinition(
        scenario_id="scn-fake-014",
        task_id=TASK_E,
        description=(
            "Multi-action with progressive test improvements: context read, "
            "model planning, implementation, three test rounds, final review. "
            "Nine actions all allowed."
        ),
        provider_kind="fake",
        policy_rules=_POLICY_STANDARD,
        budget_cap_micro_usd=100_000,
        hosted_egress_allowed=False,
        run_outcome="succeeded",
        verified_success=True,
        action_sequence=[
            _step("context_read", "fake", "fp-E14-read",   100,  100),
            _step("model_call",   "fake", "fp-E14-init",   2000, 1900),
            _step("file_change",  "fake", "fp-E14-impl",   0,    0,   diff_bytes=3072),
            _step("test_run",     "fake", "fp-E14-t1",     300,  300,
                  tests_passed=0, tests_failed=3, elapsed_ms=900),
            _step("file_change",  "fake", "fp-E14-fix1",   0,    0,   diff_bytes=256),
            _step("test_run",     "fake", "fp-E14-t2",     300,  300,
                  tests_passed=2, tests_failed=1, elapsed_ms=900),
            _step("file_change",  "fake", "fp-E14-fix2",   0,    0,   diff_bytes=128),
            _step("test_run",     "fake", "fp-E14-t3",     300,  300,
                  tests_passed=3, tests_failed=0, elapsed_ms=850),
            _step("model_call",   "fake", "fp-E14-review", 1000, 950, diff_bytes=32),
        ],
    ),

    ScenarioDefinition(
        scenario_id="scn-fake-015",
        task_id=TASK_E,
        description=(
            "Exact-repeat suppressed by made_progress: fingerprint repeats reach the "
            "limit (3>=3) on action 2, but diff_bytes>0 sets made_progress=True, "
            "suppressing the pause. Run succeeds — demonstrates a training 'near-miss'."
        ),
        provider_kind="fake",
        policy_rules=_POLICY_STANDARD,
        budget_cap_micro_usd=100_000,
        hosted_egress_allowed=False,
        run_outcome="succeeded",
        verified_success=True,
        action_sequence=[
            # A0: recent=[], repeats=0, 1 < 3 → allow
            _step("model_call",  "fake", "fp-E15-attempt", 1000, 950),
            # A1: recent=[A0(fp-E15-attempt)], repeats=1, 2 < 3 → allow
            _step("model_call",  "fake", "fp-E15-attempt", 1000, 950),
            # A2: recent=[A0,A1], repeats=2, 2+1=3 >= 3, BUT diff_bytes=1024 → made_progress=True → SUPPRESSED → allow
            _step("file_change", "fake", "fp-E15-attempt", 0,    0,   diff_bytes=1024),
            # A3: different fp → repeats=0 → allow
            _step("test_run",    "fake", "fp-E15-test",    200,  200, tests_passed=5),
            # A4: allow
            _step("model_call",  "fake", "fp-E15-review",  1000, 950, diff_bytes=256),
            # A5: allow
            _step("file_change", "fake", "fp-E15-finish",  0,    0,   diff_bytes=128),
        ],
    ),

    # Task F: dep-update
    ScenarioDefinition(
        scenario_id="scn-fake-016",
        task_id=TASK_F,
        description=(
            "Default downgrade policy with run_cap=50000: every action is downgraded "
            "to local-ollama regardless of other signals. Run completes successfully."
        ),
        provider_kind="fake",
        policy_rules={
            "default_action": "downgrade",
            "downgrade_profile": "local-ollama",
            "budgets": {
                "run_cap_micro_usd": 50_000,
                "organization_cap_micro_usd": 500_000,
            },
            "loop_detection": {
                "exact_repeat_limit": 3,
                "semantic_similarity_threshold": 0.97,
                "semantic_repeat_limit": 3,
            },
            "fail_safe": {
                "on_budget_check_failure": "pause",
                "on_provider_quota_failure": "pause",
            },
        },
        budget_cap_micro_usd=50_000,
        hosted_egress_allowed=False,
        run_outcome="succeeded",
        verified_success=True,
        action_sequence=[
            _step("model_call", "fake", "fp-F16-analyze", 2000, 1900,
                  expected_verdict="downgrade"),
            _step("model_call", "fake", "fp-F16-impl",    2000, 1900, diff_bytes=512,
                  expected_verdict="downgrade"),
            _step("test_run",   "fake", "fp-F16-test",    300,  300,  tests_passed=3,
                  expected_verdict="downgrade"),
            _step("model_call", "fake", "fp-F16-finish",  1000, 950,  diff_bytes=128,
                  expected_verdict="downgrade"),
        ],
    ),

    ScenarioDefinition(
        scenario_id="scn-fake-017",
        task_id=TASK_F,
        description=(
            "Data classification blocked: agent has hosted egress consent "
            "(hosted_egress_allowed=True) but submits 'confidential' data. "
            "Policy only allows ['public','internal'] → pause (data_classification_not_allowed)."
        ),
        provider_kind="fake",
        policy_rules={
            "default_action": "allow",
            "budgets": {
                "run_cap_micro_usd": 100_000,
                "organization_cap_micro_usd": 1_000_000,
            },
            "hosted_egress": {
                "enabled": True,
                "allowed_data_classes": ["public", "internal"],   # confidential blocked
            },
            "loop_detection": {
                "exact_repeat_limit": 3,
                "semantic_similarity_threshold": 0.97,
                "semantic_repeat_limit": 3,
            },
            "fail_safe": {
                "on_budget_check_failure": "pause",
                "on_provider_quota_failure": "pause",
            },
        },
        budget_cap_micro_usd=100_000,
        hosted_egress_allowed=True,   # has consent, but wrong data class
        run_outcome="paused",
        verified_success=False,
        action_sequence=[
            # hosted_egress_allowed=True → first check (consent) passes
            # data_classification="confidential" ∉ {"public","internal"} → PAUSE (data_classification_not_allowed)
            _step("model_call", "gemini", "fp-F17-start", 5000, 0,
                  data_classification="confidential",
                  expected_verdict="pause", action_outcome="failed"),
        ],
    ),

    # Task G: ci-setup (Ollama simulated)
    ScenarioDefinition(
        scenario_id="scn-ollama-001",
        task_id=TASK_G,
        description=(
            "Ollama local success: CI configuration is written and validated. "
            "Four actions, larger virtual budget, no loops or errors."
        ),
        provider_kind="ollama_simulated",
        policy_rules=_POLICY_OLLAMA_LOCAL,
        budget_cap_micro_usd=500_000,
        hosted_egress_allowed=False,
        run_outcome="succeeded",
        verified_success=True,
        action_sequence=[
            _step("model_call",  "ollama", "fp-G01-start",     5000, 4800, elapsed_ms=2100),
            _step("file_change", "ollama", "fp-G01-ci-config", 0,    0,    diff_bytes=1024, elapsed_ms=50),
            _step("tool_call",   "ollama", "fp-G01-validate",  1000, 1000, elapsed_ms=500),
            _step("model_call",  "ollama", "fp-G01-review",    5000, 4600, diff_bytes=256,  elapsed_ms=2000),
        ],
    ),

    ScenarioDefinition(
        scenario_id="scn-ollama-002",
        task_id=TASK_G,
        description=(
            "Ollama exact-repeat loop: local model generates identical outputs "
            "with no file change. Pauses on 3rd occurrence."
        ),
        provider_kind="ollama_simulated",
        policy_rules=_POLICY_OLLAMA_LOCAL,
        budget_cap_micro_usd=500_000,
        hosted_egress_allowed=False,
        run_outcome="paused",
        verified_success=False,
        action_sequence=[
            # A0: recent=[], repeats=0 → allow
            _step("model_call", "ollama", "fp-G02-loop", 5000, 4800, elapsed_ms=2200),
            # A1: recent=[A0], repeats=1, 2<3 → allow
            _step("model_call", "ollama", "fp-G02-loop", 5000, 4800, elapsed_ms=2250),
            # A2: recent=[A0,A1], repeats=2, 3>=3, not made_progress → PAUSE
            _step("model_call", "ollama", "fp-G02-loop", 5000, 0,    elapsed_ms=2300,
                  expected_verdict="pause", action_outcome="failed"),
        ],
    ),

    # Task H: db-migration (Ollama simulated
    ScenarioDefinition(
        scenario_id="scn-ollama-003",
        task_id=TASK_H,
        description=(
            "Ollama virtual budget limit: run_cap=15000 micro-USD (tight for local compute). "
            "Three actions complete (spent=14400); fourth pushes over cap → pause."
        ),
        provider_kind="ollama_simulated",
        policy_rules={
            "default_action": "allow",
            "budgets": {
                "run_cap_micro_usd": 15_000,   # tight virtual budget
                "organization_cap_micro_usd": 5_000_000,
            },
            "fail_safe": {"on_budget_check_failure": "pause"},
        },
        budget_cap_micro_usd=15_000,
        hosted_egress_allowed=False,
        run_outcome="paused",
        verified_success=False,
        action_sequence=[
            # 0+0+5000=5000 <= 15000 → allow; after: spent=4800
            _step("model_call", "ollama", "fp-H03-a0", 5000, 4800, elapsed_ms=2000),
            # 4800+0+5000=9800 <= 15000 → allow; after: spent=9600
            _step("model_call", "ollama", "fp-H03-a1", 5000, 4800, elapsed_ms=2100, diff_bytes=256),
            # 9600+0+5000=14600 <= 15000 → allow; after: spent=14400
            _step("model_call", "ollama", "fp-H03-a2", 5000, 4800, elapsed_ms=2000, diff_bytes=512),
            # 14400+0+5000=19400 > 15000 → PAUSE (run_budget_cap_exceeded)
            _step("model_call", "ollama", "fp-H03-a3", 5000, 0,    elapsed_ms=0,
                  expected_verdict="pause", action_outcome="failed"),
        ],
    ),

    ScenarioDefinition(
        scenario_id="scn-ollama-004",
        task_id=TASK_H,
        description=(
            "Ollama semantic loop: ONNX embeddings show high cosine similarity (>=0.95) "
            "across three responses with no code change → pause on action 2."
        ),
        provider_kind="ollama_simulated",
        policy_rules={
            **_POLICY_OLLAMA_LOCAL,
            "loop_detection": {
                "exact_repeat_limit": 10,               # exact-repeat won't fire
                "semantic_similarity_threshold": 0.95,
                "semantic_repeat_limit": 2,
            },
        },
        budget_cap_micro_usd=500_000,
        hosted_egress_allowed=False,
        run_outcome="paused",
        verified_success=False,
        action_sequence=[
            # A0: embed=False → semantic check skipped → allow
            _step("model_call", "ollama", "fp-H04-init", 5000, 4800,
                  embedding_available=False, elapsed_ms=2000),
            # A1: embed=True, sim=0.96>=0.95, not made_progress
            #   recent: A0 (embed=False) → qualifies=0; current adds 1 → sem_repeats=1; 1>=2? NO → allow
            _step("model_call", "ollama", "fp-H04-s1",   5000, 4800,
                  semantic_similarity=0.96, embedding_available=True, elapsed_ms=2100),
            # A2: embed=True, sim=0.98>=0.95, not made_progress
            #   recent: A1 qualifies → count=1; current adds 1 → sem_repeats=2; 2>=max(1,2)=2 → PAUSE
            _step("model_call", "ollama", "fp-H04-s2",   5000, 0,
                  semantic_similarity=0.98, embedding_available=True, elapsed_ms=0,
                  expected_verdict="pause", action_outcome="failed"),
        ],
    ),

    ScenarioDefinition(
        scenario_id="scn-ollama-005",
        task_id=TASK_H,
        description=(
            "Ollama success with mixed action types: model exploration (same fp twice), "
            "file migration, test run with 1 failure, targeted fix. Five actions all allowed."
        ),
        provider_kind="ollama_simulated",
        policy_rules={
            **_POLICY_OLLAMA_LOCAL,
            "loop_detection": {
                "exact_repeat_limit": 4,                # higher tolerance for local exploration
                "semantic_similarity_threshold": 0.97,
                "semantic_repeat_limit": 3,
            },
        },
        budget_cap_micro_usd=500_000,
        hosted_egress_allowed=False,
        run_outcome="succeeded",
        verified_success=True,
        action_sequence=[
            # A0: recent=[], repeats=0 → allow
            _step("model_call",  "ollama", "fp-H05-explore",   5000, 4800, elapsed_ms=2000),
            # A1: recent=[A0(fp-H05-explore)], repeats=1, 2 < 4 → allow
            _step("model_call",  "ollama", "fp-H05-explore",   5000, 4800, elapsed_ms=2100),
            # A2: same fp, diff=2048 (made_progress=True) → recent=[A0,A1], repeats=2, 3<4 → allow
            _step("file_change", "ollama", "fp-H05-migrate",   0,    0,    diff_bytes=2048, elapsed_ms=60),
            # A3: different fp → repeats=0 → allow
            _step("test_run",    "ollama", "fp-H05-test-mig",  1000, 1000,
                  tests_passed=2, tests_failed=1, elapsed_ms=1500),
            # A4: different fp → allow
            _step("file_change", "ollama", "fp-H05-fix",       0,    0,    diff_bytes=512,  elapsed_ms=40),
        ],
    ),
]


# Registry helpers
def get_scenario(scenario_id: str) -> ScenarioDefinition:
    """Return a scenario by its stable ID. Raises KeyError if not found."""
    for s in SCENARIOS:
        if s.scenario_id == scenario_id:
            return s
    raise KeyError(f"Unknown scenario_id: {scenario_id!r}")


def get_task_ids() -> list[str]:
    """Return sorted list of unique task IDs across all scenarios."""
    return sorted({s.task_id for s in SCENARIOS})


def get_scenario_ids() -> list[str]:
    """Return scenario IDs in definition order (stable)."""
    return [s.scenario_id for s in SCENARIOS]


def get_scenarios_by_task() -> dict[str, list[str]]:
    """Map task_id → [scenario_id, ...] for all tasks."""
    result: dict[str, list[str]] = {}
    for s in SCENARIOS:
        result.setdefault(s.task_id, []).append(s.scenario_id)
    return result
