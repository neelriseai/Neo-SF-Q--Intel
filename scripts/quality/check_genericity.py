from __future__ import annotations

import argparse
import ast
import fnmatch
import hashlib
import json
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

ROOT = Path(__file__).resolve().parents[2]
POLICY_PATH = ROOT / "config" / "quality-policy.json"
SCOPE_PATH = ROOT / "config" / "capability-scope.json"
REQUIREMENTS_PATH = ROOT / "config" / "requirement-registry.json"
LIVE_SALESFORCE_PROFILE_PATH = ROOT / "config" / "live-salesforce-acceptance-profile.json"
PROTECTED_SCOPE_PATHS = {
    "config/quality-policy.json",
    "scripts/quality/check_genericity.py",
}
PROTECTED_SCOPE_ROOTS = {"scripts/quality", ".githooks"}


def _strict_json_loads(body: str) -> dict:
    def no_duplicates(pairs: list[tuple[str, object]]) -> dict:
        result: dict = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    value = json.loads(body, object_pairs_hook=no_duplicates)
    if not isinstance(value, dict):
        raise ValueError("top-level JSON value must be an object")
    return value


def _canonical_sha256(value: object) -> str:
    body = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Finding:
    code: str
    path: str
    message: str


class ReviewFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=3)
    severity: Literal["P0", "P1", "P2"]
    resolution: str = Field(min_length=10)


class StatusDowngrade(BaseModel):
    model_config = ConfigDict(extra="forbid")

    capabilityId: str = Field(min_length=3)
    from_: Literal["IMPLEMENTED", "FOUNDATION", "NEXT", "DEFERRED"] = Field(alias="from")
    to: Literal["IMPLEMENTED", "FOUNDATION", "NEXT", "DEFERRED"]
    reason: str = Field(min_length=20)


class ScopeReviewManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schemaVersion: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    reviewId: str = Field(min_length=3)
    status: Literal["IN_PROGRESS", "APPROVED"]
    reviewedPaths: list[str] = Field(min_length=1)
    reviewers: list[str] = Field(min_length=2)
    findings: list[ReviewFinding]
    statusDowngrades: list[StatusDowngrade]

    @model_validator(mode="after")
    def validate_distinct_reviewers(self) -> ScopeReviewManifest:
        if any(not item.strip() for item in self.reviewers):
            raise ValueError("reviewer IDs cannot be blank")
        if len(set(self.reviewers)) != len(self.reviewers):
            raise ValueError("reviewer IDs must be distinct")
        return self


def _repo_files() -> list[str]:
    completed = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        shell=False,
    )
    return sorted(path.replace("\\", "/") for path in completed.stdout.splitlines())


def _is_allowed(path: str, prefixes: list[str]) -> bool:
    return any(
        path == prefix.rstrip("/") or path.startswith(prefix.rstrip("/") + "/")
        for prefix in prefixes
    )


def _imports(path: Path) -> tuple[set[str], str | None]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError) as exc:
        return set(), str(exc)
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names, None


def check_vector_storage_policy(path: str, body: str, policy: dict) -> list[Finding]:
    findings: list[Finding] = []
    basename = Path(path).name
    is_manifest = any(
        fnmatch.fnmatch(basename, pattern) for pattern in policy["dependencyManifestPatterns"]
    )
    if is_manifest or path.startswith(("src/", "apps/", "packages/")):
        approved = {
            package.casefold().replace("_", "-")
            for package in policy["approvedPersistentVectorPackages"]
        }
        for package in policy["knownPersistentVectorPackages"]:
            pattern = rf"(?i)(?<![a-z0-9_-]){re.escape(package)}(?![a-z0-9_-])"
            if re.search(pattern, body) and package.casefold().replace("_", "-") not in approved:
                findings.append(
                    Finding(
                        "UNAPPROVED_VECTOR_BACKEND",
                        path,
                        f"Persistent vector backend {package} is not approved",
                    )
                )
    if path.startswith("migrations/") or _is_allowed(path, policy["runtimeRoots"]):
        for pattern in policy["postgresVectorPatterns"]:
            if re.search(pattern, body):
                findings.append(
                    Finding(
                        "POSTGRES_VECTOR_STORAGE",
                        path,
                        "PostgreSQL may not store vector columns or extensions",
                    )
                )
    return findings


def _head_scope() -> dict[str, dict] | None:
    completed = subprocess.run(
        ["git", "show", "HEAD:config/capability-scope.json"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        shell=False,
    )
    if completed.returncode != 0:
        return None
    body = json.loads(completed.stdout)
    return {item["id"]: item for item in body.get("capabilities", [])}


def _head_quality_policy() -> dict | None:
    completed = subprocess.run(
        ["git", "show", "HEAD:config/quality-policy.json"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        shell=False,
    )
    if completed.returncode != 0:
        return None
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError:
        return None


def _controlled_changed_paths(
    changed: set[str], current_policy: dict, previous_policy: dict | None
) -> set[str]:
    policies = [current_policy]
    if previous_policy:
        policies.append(previous_policy)
    exact = set(PROTECTED_SCOPE_PATHS)
    roots = set(PROTECTED_SCOPE_ROOTS)
    for candidate in policies:
        exact.update(candidate.get("scopeControlledPaths", []))
        roots.update(candidate.get("scopeControlledRoots", []))
    return {path for path in changed if path in exact or _is_allowed(path, sorted(roots))}


def _changed_paths() -> set[str]:
    completed = subprocess.run(
        ["git", "diff", "--name-only", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        shell=False,
    )
    tracked = {item.replace("\\", "/") for item in completed.stdout.splitlines()}
    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        shell=False,
    )
    return tracked | {item.replace("\\", "/") for item in untracked.stdout.splitlines()}


def _scope_review_findings(
    policy: dict, controlled_changed: set[str], changed: set[str]
) -> tuple[list[Finding], dict]:
    if not controlled_changed:
        return [], {}
    manifest_path = policy["scopeReviewManifestPath"]
    findings: list[Finding] = []
    if manifest_path not in changed:
        return [
            Finding(
                "MISSING_SCOPE_REVIEW",
                ", ".join(sorted(controlled_changed)),
                "Controlled policy changes require the structured scope-review manifest",
            )
        ], {}
    try:
        raw_manifest = json.loads((ROOT / manifest_path).read_text(encoding="utf-8"))
        manifest = ScopeReviewManifest.model_validate(raw_manifest).model_dump(by_alias=True)
    except (OSError, json.JSONDecodeError, ValidationError):
        return [Finding("INVALID_SCOPE_REVIEW", manifest_path, "Manifest is not valid JSON")], {}
    reviewed_paths = set(manifest.get("reviewedPaths", []))
    uncovered = controlled_changed - reviewed_paths
    if uncovered:
        findings.append(
            Finding(
                "UNREVIEWED_POLICY_CHANGE",
                ", ".join(sorted(uncovered)),
                "Each changed controlled path must be named in reviewedPaths",
            )
        )
    if manifest.get("status") != "APPROVED":
        findings.append(
            Finding(
                "SCOPE_REVIEW_NOT_APPROVED",
                manifest_path,
                "Scope review must be APPROVED before commit",
            )
        )
    return findings, manifest


def _requirement_registry_findings(registry: dict, capability_ids: set[str]) -> list[Finding]:
    findings: list[Finding] = []
    requirements = registry.get("requirements", [])
    requirement_ids = [item.get("requirementId") for item in requirements]
    if len(requirement_ids) != len(set(requirement_ids)):
        findings.append(
            Finding(
                "DUPLICATE_REQUIREMENT",
                "config/requirement-registry.json",
                "Requirement IDs must be unique",
            )
        )
    mapped: list[str] = []
    valid_levels = {"UC", "F", "B", "L", "G"}
    valid_statuses = {"SATISFIED", "PARTIAL", "TRACEABILITY_GAP", "NOT_RUN", "BLOCKED"}
    for item in requirements:
        requirement_id = item.get("requirementId", "")
        if not re.fullmatch(r"REQ-[A-Z]+-\d{3}", requirement_id):
            findings.append(
                Finding(
                    "INVALID_REQUIREMENT_ID",
                    "config/requirement-registry.json",
                    "Requirement IDs must use REQ-AREA-NNN",
                )
            )
        mapped.extend(item.get("capabilityIds", []))
        unknown = set(item.get("capabilityIds", [])) - capability_ids
        if unknown:
            findings.append(
                Finding(
                    "UNKNOWN_REQUIREMENT_CAPABILITY",
                    "config/requirement-registry.json",
                    f"{requirement_id} references unknown capability IDs",
                )
            )
        levels = set(item.get("acceptanceClasses", []))
        if not levels or not levels <= valid_levels:
            findings.append(
                Finding(
                    "INVALID_ACCEPTANCE_CLASS",
                    "config/requirement-registry.json",
                    f"{requirement_id} has invalid acceptance classes",
                )
            )
        test_nodes = item.get("exactTestNodeIds", [])
        receipt_ids = item.get("currentReceiptIds", [])
        if len(test_nodes) != len(set(test_nodes)) or len(receipt_ids) != len(set(receipt_ids)):
            findings.append(
                Finding(
                    "DUPLICATE_REQUIREMENT_EVIDENCE",
                    "config/requirement-registry.json",
                    f"{requirement_id} repeats a test node or receipt ID",
                )
            )
        if any("::" not in node for node in test_nodes):
            findings.append(
                Finding(
                    "NONEXACT_TEST_NODE",
                    "config/requirement-registry.json",
                    f"{requirement_id} contains a file-level test reference",
                )
            )
        status = item.get("status")
        if status not in valid_statuses:
            findings.append(
                Finding(
                    "INVALID_REQUIREMENT_STATUS",
                    "config/requirement-registry.json",
                    f"{requirement_id} has an invalid status",
                )
            )
        if status == "SATISFIED" and (not test_nodes or not item.get("currentReceiptIds")):
            findings.append(
                Finding(
                    "UNRECEIPTED_REQUIREMENT",
                    "config/requirement-registry.json",
                    f"{requirement_id} cannot be SATISFIED without exact tests "
                    "and current receipts",
                )
            )
        if status == "SATISFIED":
            findings.append(
                Finding(
                    "SATISFIED_WITHOUT_RECEIPT_VALIDATOR",
                    "config/requirement-registry.json",
                    f"{requirement_id} cannot be SATISFIED until the durable "
                    "receipt validator exists",
                )
            )
            if item.get("gaps"):
                findings.append(
                    Finding(
                        "SATISFIED_REQUIREMENT_HAS_GAPS",
                        "config/requirement-registry.json",
                        f"{requirement_id} cannot be SATISFIED with open gaps",
                    )
                )
        if status != "SATISFIED" and not item.get("gaps"):
            findings.append(
                Finding(
                    "MISSING_REQUIREMENT_GAP",
                    "config/requirement-registry.json",
                    f"{requirement_id} must explain incomplete acceptance",
                )
            )
    mapped_set = set(mapped)
    if mapped_set != capability_ids or len(mapped) != len(mapped_set):
        findings.append(
            Finding(
                "INCOMPLETE_REQUIREMENT_COVERAGE",
                "config/requirement-registry.json",
                "Every capability must map to exactly one top-level requirement",
            )
        )
    return findings


def _live_salesforce_profile_findings(
    profile: dict, requirement_capabilities: dict[str, set[str]]
) -> list[Finding]:
    path = "config/live-salesforce-acceptance-profile.json"
    findings: list[Finding] = []
    if _canonical_sha256(profile) != (
        "5e4a91adbf78ffbfa6a9d973c00c8ed857e875339118324d0a590b3af1c54bb4"
    ):
        findings.append(
            Finding(
                "UNREVIEWED_LIVE_PROFILE_ROTATION",
                path,
                "The complete acceptance definition changed without validator migration",
            )
        )
    expected: dict[str, tuple[str, str, str, str, str, list[str], list[str]]] = {
        "SF-L01": (
            "HOST_NONPRODUCTION_ENROLLMENT",
            "HOST_ENROLLMENT_RECEIPT",
            "HOST_CONTROL_PLANE",
            "HOST_OPERATOR_ENROLLMENT",
            "NONE",
            [],
            ["LIVE_BASELINE"],
        ),
        "SF-L02": (
            "MACHINE_LOCAL_CLI_AUTHENTICATION",
            "CLI_AUTHENTICATION_RECEIPT",
            "CLASSIFICATION_ONLY_READ",
            "HOST_ENROLLMENT",
            "NONE",
            ["SF-L01"],
            ["LIVE_BASELINE"],
        ),
        "SF-L03": (
            "MCP_STANDARD_REST_ASSERTIONS",
            "MCP_LIVE_REST_RECEIPT",
            "READ_ONLY",
            "HOST_READ_POLICY",
            "LIVE_TARGET_PLAN",
            ["SF-L02"],
            ["LIVE_BASELINE"],
        ),
        "SF-L04": (
            "MCP_SOURCE_CONTRACT_CUSTOM_API",
            "MCP_LIVE_CUSTOM_API_RECEIPT",
            "READ_ONLY",
            "HOST_READ_POLICY",
            "LIVE_TARGET_PLAN",
            ["SF-L02"],
            ["LIVE_BASELINE"],
        ),
        "SF-L05": (
            "SCOPED_METADATA_RETRIEVAL",
            "LIVE_METADATA_RECEIPT",
            "READ_ONLY",
            "HOST_METADATA_READ_POLICY",
            "LIVE_TARGET_PLAN",
            ["SF-L02"],
            ["LIVE_BASELINE"],
        ),
        "SF-L06": (
            "EPHEMERAL_PLAYWRIGHT_SESSION",
            "BROWSER_SESSION_RECEIPT",
            "SESSION_HANDOFF",
            "HOST_BROWSER_POLICY",
            "LIVE_TARGET_PLAN",
            ["SF-L01", "SF-L02"],
            ["LIVE_BASELINE"],
        ),
        "SF-L07": (
            "LIVE_LIGHTNING_ASSERTIONS",
            "LIGHTNING_ASSERTION_RECEIPT",
            "READ_ONLY",
            "HOST_BROWSER_POLICY",
            "LIVE_TARGET_PLAN",
            ["SF-L05", "SF-L06"],
            ["LIVE_BASELINE"],
        ),
        "SF-L08": (
            "SELECTED_LIVE_APEX_TESTS",
            "LIVE_TEST_RECEIPT",
            "SIDE_EFFECTING_TEST",
            "CURRENT_TASK_TEST_AUTHORITY",
            "LIVE_TARGET_PLAN",
            ["SF-L02", "SF-L05"],
            ["LIVE_BASELINE"],
        ),
        "SF-L09": (
            "AUTHORIZED_BROWSER_RECOVERY",
            "BROWSER_RECOVERY_RECEIPT",
            "NON_DESTRUCTIVE_BROWSER_ACTION",
            "EXPLICIT_BROWSER_APPROVAL",
            "LIVE_TARGET_PLAN",
            ["SF-L07"],
            ["LIVE_BASELINE"],
        ),
        "SF-C01": (
            "CANDIDATE_PRESTATE_RESTORE_READINESS",
            "RESTORE_READINESS_RECEIPT",
            "READ_ONLY_RESTORE_READINESS_VALIDATION",
            "HOST_READ_POLICY",
            "CANDIDATE_CAMPAIGN_PLAN",
            ["SF-L05"],
            ["LIVE_BASELINE"],
        ),
        "SF-C02": (
            "EXACT_CANDIDATE_CHECK_ONLY",
            "CHECK_ONLY_RECEIPT",
            "SIDE_EFFECTING_TEST",
            "CURRENT_TASK_TEST_AUTHORITY",
            "CANDIDATE_CAMPAIGN_PLAN",
            ["SF-C01"],
            ["CANDIDATE_CHECK_ONLY"],
        ),
        "SF-C03": (
            "AUTHORIZED_EXACT_DEPLOYMENT",
            "DEPLOYMENT_RECEIPT",
            "METADATA_MUTATION",
            "CURRENT_TASK_MUTATION_AUTHORITY",
            "CANDIDATE_CAMPAIGN_PLAN",
            ["SF-C02"],
            ["DEPLOYED_CANDIDATE"],
        ),
        "SF-C04": (
            "POST_DEPLOY_CANDIDATE_RECONCILIATION",
            "CANDIDATE_RECONCILIATION_RECEIPT",
            "READ_ONLY",
            "HOST_METADATA_READ_POLICY",
            "CANDIDATE_CAMPAIGN_PLAN",
            ["SF-C03"],
            ["DEPLOYED_CANDIDATE"],
        ),
        "SF-C05": (
            "DEPLOYED_CANDIDATE_ASSERTIONS",
            "CANDIDATE_ASSERTION_RECEIPT",
            "READ_AND_SIDE_EFFECTING_TEST",
            "CURRENT_TASK_TEST_AND_BROWSER_AUTHORITY",
            "CANDIDATE_CAMPAIGN_PLAN",
            ["SF-C04"],
            ["DEPLOYED_CANDIDATE"],
        ),
        "SF-C06": (
            "RESTORE_AND_RESIDUE_RECONCILIATION",
            "RESTORE_RECONCILIATION_RECEIPT",
            "RESTORE_MUTATION",
            "PREAUTHORIZED_CAMPAIGN_RECOVERY",
            "CANDIDATE_CAMPAIGN_PLAN",
            [],
            ["RESTORED_BASELINE"],
        ),
    }
    expected_inputs = {
        "SF-L01": [],
        "SF-L02": ["HOST_ENROLLMENT_RECEIPT"],
        "SF-L03": [
            "HOST_ENROLLMENT_RECEIPT",
            "LIVE_DATASET_SCOPE_RECEIPT",
            "LIVE_TARGET_PLAN_RECEIPT",
            "EXPECTED_EXECUTION_CONTRACT_RECEIPT",
        ],
        "SF-L04": [
            "HOST_ENROLLMENT_RECEIPT",
            "LIVE_DATASET_SCOPE_RECEIPT",
            "LIVE_TARGET_PLAN_RECEIPT",
            "EXPECTED_EXECUTION_CONTRACT_RECEIPT",
        ],
        "SF-L05": [
            "HOST_ENROLLMENT_RECEIPT",
            "LIVE_TARGET_PLAN_RECEIPT",
            "EXPECTED_EXECUTION_CONTRACT_RECEIPT",
        ],
        "SF-L06": ["HOST_ENROLLMENT_RECEIPT", "LIVE_TARGET_PLAN_RECEIPT"],
        "SF-L07": [
            "BROWSER_SESSION_RECEIPT",
            "LIVE_DATASET_SCOPE_RECEIPT",
            "LIVE_METADATA_RECEIPT",
            "LIVE_TARGET_PLAN_RECEIPT",
            "EXPECTED_EXECUTION_CONTRACT_RECEIPT",
        ],
        "SF-L08": [
            "CURRENT_TEST_AUTHORITY_RECEIPT",
            "LIVE_METADATA_RECEIPT",
            "LIVE_TARGET_PLAN_RECEIPT",
            "EXPECTED_EXECUTION_CONTRACT_RECEIPT",
        ],
        "SF-L09": ["CURRENT_BROWSER_APPROVAL_RECEIPT", "LIGHTNING_ASSERTION_RECEIPT"],
        "SF-C01": [
            "HOST_ENROLLMENT_RECEIPT",
            "LIVE_METADATA_RECEIPT",
            "INDEPENDENT_RESTORE_REHEARSAL_RECEIPT",
            "CANDIDATE_CAMPAIGN_PLAN_RECEIPT",
        ],
        "SF-C02": [
            "CURRENT_TEST_AUTHORITY_RECEIPT",
            "RESTORE_READINESS_RECEIPT",
            "CANDIDATE_CAMPAIGN_PLAN_RECEIPT",
        ],
        "SF-C03": [
            "INDEPENDENT_MUTATION_AUTHORIZATION_RECEIPT",
            "CAMPAIGN_RECOVERY_PERMIT_RECEIPT",
            "CHECK_ONLY_RECEIPT",
            "RESTORE_READINESS_RECEIPT",
            "CANDIDATE_CAMPAIGN_PLAN_RECEIPT",
        ],
        "SF-C04": ["DEPLOYMENT_RECEIPT", "CANDIDATE_CAMPAIGN_PLAN_RECEIPT"],
        "SF-C05": [
            "DEPLOYMENT_RECEIPT",
            "CANDIDATE_RECONCILIATION_RECEIPT",
            "MCP_LIVE_REST_RECEIPT",
            "MCP_LIVE_CUSTOM_API_RECEIPT",
            "LIVE_METADATA_RECEIPT",
            "LIVE_DATASET_SCOPE_RECEIPT",
            "BROWSER_SESSION_RECEIPT",
            "LIGHTNING_ASSERTION_RECEIPT",
            "LIVE_TEST_RECEIPT",
            "BROWSER_RECOVERY_RECEIPT",
            "CURRENT_TEST_AUTHORITY_RECEIPT",
            "CURRENT_BROWSER_APPROVAL_RECEIPT",
            "CANDIDATE_CAMPAIGN_PLAN_RECEIPT",
        ],
        "SF-C06": [
            "DEPLOYMENT_DISPATCH_RECEIPT",
            "CAMPAIGN_RECOVERY_PERMIT_RECEIPT",
            "RESTORE_READINESS_RECEIPT",
        ],
    }
    expected_capabilities = {
        "SF-L01": ["runtime.salesforce-live-evidence"],
        "SF-L02": ["runtime.salesforce-live-evidence"],
        "SF-L03": ["tools.mcp", "runtime.salesforce-live-evidence"],
        "SF-L04": ["tools.mcp", "runtime.salesforce-live-evidence"],
        "SF-L05": ["runtime.salesforce-live-evidence"],
        "SF-L06": ["automation.browser-worker"],
        "SF-L07": ["automation.browser-worker", "automation.locator-healing"],
        "SF-L08": ["quality.live-test-execution"],
        "SF-L09": ["automation.browser-worker", "automation.locator-healing"],
        "SF-C01": ["runtime.salesforce-live-evidence"],
        "SF-C02": ["runtime.salesforce-live-evidence", "quality.live-test-execution"],
        "SF-C03": ["runtime.salesforce-live-evidence"],
        "SF-C04": ["runtime.salesforce-live-evidence"],
        "SF-C05": [
            "runtime.salesforce-live-evidence",
            "quality.live-test-execution",
            "automation.browser-worker",
            "automation.locator-healing",
        ],
        "SF-C06": ["runtime.salesforce-live-evidence"],
    }
    expected_requirements = {
        gate_id: ["REQ-UIA-001"] if gate_id in {"SF-L06", "SF-L07", "SF-L09"} else ["REQ-SF-001"]
        for gate_id in expected
    }
    expected_requirements["SF-C05"] = ["REQ-SF-001", "REQ-UIA-001"]
    all_gates = [*profile.get("gates", []), *profile.get("candidateGates", [])]
    gate_ids = [item.get("gateId") for item in all_gates]
    required_gate_ids = profile.get("requiredGateIds", [])

    top_level_expected = {
        "schemaVersion": "1.0.0",
        "profileId": "live-salesforce-campaign-v1",
        "definitionOnly": True,
        "acceptanceProfileDigestRequiredInEveryReceipt": True,
        "missingReceiptResult": "NOT_RUN",
        "completionRule": "ALL_REQUIRED_GATES_PASSED",
        "fixtureSubstitutionPolicy": "PROHIBITED_FOR_LIVE_GATES",
        "releaseAuthorityEnabled": False,
    }
    for field, value in top_level_expected.items():
        if profile.get(field) != value:
            findings.append(
                Finding("INVALID_LIVE_PROFILE_INVARIANT", path, f"{field} must remain {value!r}")
            )
    expected_top_keys = {
        *top_level_expected,
        "evidencePhases",
        "dependencyReceiptRule",
        "liveTargetPlan",
        "authorityReceiptRules",
        "orgClassificationPolicy",
        "metadataRetrievalPolicy",
        "candidatePhaseReceiptRules",
        "compensationAuthorizationRules",
        "browserActionPolicy",
        "requiredGateIds",
        "gates",
        "candidateGates",
        "candidateCampaign",
        "nonEvidence",
    }
    if set(profile) != expected_top_keys:
        findings.append(
            Finding(
                "UNKNOWN_LIVE_PROFILE_FIELD",
                path,
                "Profile fields must match the reviewed definition",
            )
        )
    if profile.get("dependencyReceiptRule") != (
        "EACH_DEPENDENCY_PASSED_RECEIPT_IS_AN_IMPLICIT_EXACT_CURRENT_SAME_CAMPAIGN_INPUT_ROOT"
    ):
        findings.append(
            Finding(
                "WEAK_LIVE_DEPENDENCY_BINDING",
                path,
                "Dependencies must bind current same-campaign passed receipts",
            )
        )
    if set(profile.get("evidencePhases", [])) != {
        "LIVE_BASELINE",
        "CANDIDATE_CHECK_ONLY",
        "DEPLOYED_CANDIDATE",
        "RESTORED_BASELINE",
    }:
        findings.append(
            Finding("INCOMPLETE_LIVE_PHASES", path, "All four live evidence phases are required")
        )
    if set(gate_ids) != set(expected) or len(gate_ids) != len(set(gate_ids)):
        findings.append(
            Finding(
                "INCOMPLETE_LIVE_GATE_SET",
                path,
                "The exact nine live and six candidate gates are required",
            )
        )
    if set(required_gate_ids) != set(expected) or len(required_gate_ids) != len(
        set(required_gate_ids)
    ):
        findings.append(
            Finding(
                "OPTIONAL_LIVE_GATE", path, "Every unique live and candidate gate must be required"
            )
        )

    for gate in all_gates:
        gate_id = gate.get("gateId", "unknown")
        invariant = expected.get(gate_id)
        if invariant is None:
            continue
        kind, receipt, effect, authority, target_plan, dependencies, phases = invariant
        exact_fields = {
            "kind": kind,
            "receiptType": receipt,
            "effectClass": effect,
            "authorityClass": authority,
            "targetPlanInput": target_plan,
            "dependsOn": dependencies,
            "acceptedEvidencePhases": phases,
        }
        if any(gate.get(field) != value for field, value in exact_fields.items()):
            findings.append(
                Finding(
                    "WEAKENED_LIVE_GATE_INVARIANT",
                    path,
                    f"{gate_id} kind, receipt, effect, authority, target or phase changed",
                )
            )
        expected_gate_keys = {
            "gateId",
            "kind",
            "receiptType",
            "effectClass",
            "authorityClass",
            "targetPlanInput",
            "requirementIds",
            "capabilityIds",
            "dependsOn",
            "acceptedEvidencePhases",
            "requiredInputReceiptRoles",
            "requiredForCompletion",
            "fixtureMaySatisfy",
            "evidenceRequirements",
        }
        if gate_id == "SF-C06":
            expected_gate_keys.update(
                {
                    "activationRule",
                    "readbackBeforeRecovery",
                    "successPathAdditionalInputReceiptRoles",
                    "failurePathAdditionalInputReceiptRoles",
                    "unknownExternalStateRule",
                }
            )
        if set(gate) != expected_gate_keys:
            findings.append(
                Finding(
                    "UNKNOWN_LIVE_GATE_FIELD",
                    path,
                    f"{gate_id} fields must match the reviewed definition",
                )
            )
        if gate.get("requiredInputReceiptRoles") != expected_inputs[gate_id]:
            findings.append(
                Finding(
                    "MISSING_LIVE_AUTHORITY_INPUT",
                    path,
                    f"{gate_id} required receipt roles changed",
                )
            )
        if (
            gate.get("capabilityIds") != expected_capabilities[gate_id]
            or gate.get("requirementIds") != expected_requirements[gate_id]
        ):
            findings.append(
                Finding(
                    "WEAK_LIVE_TRACEABILITY",
                    path,
                    f"{gate_id} requirement or capability mapping changed",
                )
            )
        if gate_id == "SF-C06" and (
            gate.get("activationRule")
            != "ON_DEPLOYMENT_DISPATCH_ATTEMPT_REGARDLESS_OF_C03_C04_C05_OUTCOME"
            or gate.get("readbackBeforeRecovery") is not True
            or gate.get("successPathAdditionalInputReceiptRoles") != ["CANDIDATE_ASSERTION_RECEIPT"]
            or gate.get("failurePathAdditionalInputReceiptRoles") != []
            or gate.get("unknownExternalStateRule") != "READBACK_THEN_RESTORE_OR_QUARANTINE"
        ):
            findings.append(
                Finding(
                    "SKIPPABLE_LIVE_RECOVERY",
                    path,
                    "SF-C06 dispatch activation or branch recovery was weakened",
                )
            )
        if "status" in gate:
            findings.append(
                Finding(
                    "MUTABLE_LIVE_GATE_STATUS",
                    path,
                    f"{gate_id} definition may not store runtime status",
                )
            )
        if (
            gate.get("requiredForCompletion") is not True
            or gate.get("fixtureMaySatisfy") is not False
        ):
            findings.append(
                Finding(
                    "SUBSTITUTABLE_LIVE_GATE",
                    path,
                    f"{gate_id} must be required and non-substitutable",
                )
            )
        requirement_ids = gate.get("requirementIds", [])
        covered = set().union(
            *(requirement_capabilities.get(item, set()) for item in requirement_ids)
        )
        if not requirement_ids or any(
            item not in requirement_capabilities for item in requirement_ids
        ):
            findings.append(
                Finding(
                    "UNKNOWN_LIVE_REQUIREMENT", path, f"{gate_id} references an unknown requirement"
                )
            )
        if not gate.get("capabilityIds") or set(gate.get("capabilityIds", [])) - covered:
            findings.append(
                Finding(
                    "UNCOVERED_LIVE_CAPABILITY",
                    path,
                    f"{gate_id} capability is not covered by its requirements",
                )
            )
        if len(gate.get("evidenceRequirements", [])) < 3:
            findings.append(
                Finding("THIN_LIVE_GATE", path, f"{gate_id} lacks concrete evidence requirements")
            )

    target_plan = profile.get("liveTargetPlan", {})
    if (
        _canonical_sha256(target_plan)
        != "94343362f822335821bc0173d9525da681cd2e7668678f62535cc54890df7f71"
    ):
        findings.append(
            Finding(
                "WEAK_LIVE_TARGET_PLAN",
                path,
                "Target derivation must be host-owned, source-bound and fail closed",
            )
        )
    protected_section_hashes = {
        "orgClassificationPolicy": (
            "774c2d51647d82b5bf4b22099491ac9547fbc9633e0cf8ea44a79d22bf7d1ac0"
        ),
        "metadataRetrievalPolicy": (
            "8ec801bb430877864a6baaaee70d8da0e4f5853a516092991c1423d7d21cddae"
        ),
        "browserActionPolicy": "a881fd5ec9bb7cfc582ed45cb4bcf2faba53445711bdaadcb0a159b76be13b1f",
    }
    for section, expected_sha256 in protected_section_hashes.items():
        if _canonical_sha256(profile.get(section, {})) != expected_sha256:
            findings.append(
                Finding(
                    "WEAK_LIVE_SAFETY_POLICY",
                    path,
                    f"{section} changed without reviewed validator migration",
                )
            )
    authority_rules = profile.get("authorityReceiptRules", {})
    if authority_rules != {
        "independentIssuerRequired": True,
        "executionReceiptMayAuthorizeItself": False,
        "currentExpiryRequired": True,
        "exactCampaignOrgSourceBuildManifestAndOperationMatchRequired": True,
        "scopeWideningAllowed": False,
    }:
        findings.append(
            Finding(
                "WEAK_LIVE_AUTHORITY_RULE",
                path,
                "Authority receipts must be independent, current, exact and non-widening",
            )
        )
    candidate_phase_rules = profile.get("candidatePhaseReceiptRules", {})
    if candidate_phase_rules != {
        "allRequiredRolesDistinct": True,
        "sameOrgCampaignCandidateDeploymentRootRequired": True,
        "currentUnexpiredReceiptsRequired": True,
        "acceptedPhase": "DEPLOYED_CANDIDATE",
        "receiptReuseAcrossBaselineAndCandidateForbidden": True,
        "gapsOrUnknownExternalStateMayPass": False,
    }:
        findings.append(
            Finding(
                "WEAK_CANDIDATE_PHASE_RULE",
                path,
                "Candidate receipts must be distinct, current and share one "
                "deployed-candidate root",
            )
        )
    compensation_rules = profile.get("compensationAuthorizationRules", {})
    if compensation_rules != {
        "recoveryPermitIssuedBeforeDeploymentDispatch": True,
        "exactCompensationManifestAndSyntheticDataScopeRequired": True,
        "remainingValidityMustCoverWorstCaseCampaignAndRestore": True,
        "recoveryPermitActivatedByDeploymentDispatch": True,
        "issuerAvailabilityAfterDispatchRequired": False,
        "narrowerTamperedOrExpiredPermitResult": "DEPLOYMENT_BLOCKED",
    }:
        findings.append(
            Finding(
                "WEAK_COMPENSATION_AUTHORITY",
                path,
                "Recovery must be exactly pre-authorized before deployment",
            )
        )
    campaign = profile.get("candidateCampaign", {})
    campaign_expected = {
        "requiredForLiveSalesforceCampaignCompletion": True,
        "baselineEvidenceMaySatisfy": False,
        "checkOnlyEvidenceMaySatisfy": False,
        "orderedGateIds": [f"SF-C{index:02d}" for index in range(1, 7)],
        "preDispatchFailureRule": "STOP_AND_RECORD_BLOCKED_OR_NOT_RUN",
        "postDispatchFailureRule": "ALWAYS_ATTEMPT_SF_C06_BEFORE_TERMINATION",
        "manualRecoveryStatus": "RESTORE_REQUIRED_MANUAL_INTERVENTION",
        "quarantineOnUnresolvedRestore": True,
        "deploymentRetryWhileQuarantined": False,
    }
    if any(campaign.get(field) != value for field, value in campaign_expected.items()):
        findings.append(
            Finding(
                "WEAK_CANDIDATE_RECOVERY_POLICY",
                path,
                "Candidate ordering, substitution or recovery policy was weakened",
            )
        )
    return findings


def check_repository() -> list[Finding]:
    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    scope = json.loads(SCOPE_PATH.read_text(encoding="utf-8"))
    requirement_registry = json.loads(REQUIREMENTS_PATH.read_text(encoding="utf-8"))
    live_profile_error: str | None = None
    try:
        live_salesforce_profile = _strict_json_loads(
            LIVE_SALESFORCE_PROFILE_PATH.read_text(encoding="utf-8")
        )
    except (json.JSONDecodeError, ValueError) as exc:
        live_salesforce_profile = {}
        live_profile_error = str(exc)
    paths = _repo_files()
    findings: list[Finding] = []
    if live_profile_error:
        findings.append(
            Finding(
                "INVALID_LIVE_PROFILE_JSON",
                "config/live-salesforce-acceptance-profile.json",
                live_profile_error,
            )
        )
    runtime_roots = policy["runtimeRoots"]
    allowed = policy["scenarioAllowedPaths"]
    exempt = policy["scanExemptPaths"]

    for path in paths:
        if path in exempt:
            continue
        file_path = ROOT / path
        try:
            body = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if path not in policy.get("absolutePathScanExemptPaths", []) and _is_allowed(
            path, policy.get("absolutePathScanRoots", [])
        ):
            for pattern in policy["absolutePathPatterns"]:
                if re.search(pattern, body):
                    findings.append(
                        Finding("ABSOLUTE_PATH", path, "Matched a forbidden machine path pattern")
                    )
        for pattern in policy["secretPatterns"]:
            if re.search(pattern, body):
                findings.append(
                    Finding("POSSIBLE_SECRET", path, "Matched a forbidden credential pattern")
                )
        findings.extend(check_vector_storage_policy(path, body, policy))
        if _is_allowed(path, allowed) or not _is_allowed(path, runtime_roots):
            continue
        for pattern in policy["scenarioLiteralPatterns"]:
            if re.search(pattern, body):
                findings.append(
                    Finding(
                        "SCENARIO_LITERAL",
                        path,
                        f"Runtime code matched scenario-only pattern {pattern}",
                    )
                )

    example_values: dict[str, str] = {}
    for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        example_values[key.strip()] = value.strip()
    for key in policy["blankExampleSecrets"]:
        if example_values.get(key):
            findings.append(
                Finding("POPULATED_EXAMPLE_SECRET", ".env.example", f"{key} must be blank")
            )
    for ignored_path in policy.get("requiredIgnoredPaths", []):
        completed = subprocess.run(
            ["git", "check-ignore", "-q", "--", ignored_path],
            cwd=ROOT,
            check=False,
            shell=False,
        )
        if completed.returncode != 0:
            findings.append(
                Finding(
                    "UNIGNORED_RUNTIME_STATE",
                    ".gitignore",
                    f"Runtime state path {ignored_path} must be ignored",
                )
            )
    if example_values.get("ALLOW_SALESFORCE_WRITES", "").casefold() != "false":
        findings.append(
            Finding(
                "UNSAFE_EXAMPLE_DEFAULT",
                ".env.example",
                "ALLOW_SALESFORCE_WRITES must default to false",
            )
        )

    for path, forbidden in policy["layerImportRules"].items():
        actual, parse_error = _imports(ROOT / path)
        if parse_error:
            findings.append(Finding("UNPARSEABLE_LAYER", path, parse_error))
            continue
        for dependency in sorted(actual & set(forbidden)):
            findings.append(
                Finding(
                    "LAYER_IMPORT",
                    path,
                    f"Layer may not import {dependency}",
                )
            )

    capabilities = scope.get("capabilities", [])
    identifiers = [item.get("id") for item in capabilities]
    findings.extend(_requirement_registry_findings(requirement_registry, set(identifiers)))
    findings.extend(
        _live_salesforce_profile_findings(
            live_salesforce_profile,
            {
                item.get("requirementId"): set(item.get("capabilityIds", []))
                for item in requirement_registry.get("requirements", [])
            },
        )
    )
    if len(identifiers) != len(set(identifiers)):
        findings.append(
            Finding("DUPLICATE_CAPABILITY", "config/capability-scope.json", "IDs must be unique")
        )
    valid_statuses = {"IMPLEMENTED", "FOUNDATION", "NEXT", "DEFERRED"}
    missing_capabilities = set(policy["requiredCapabilityIds"]) - set(identifiers)
    for identifier in sorted(missing_capabilities):
        findings.append(
            Finding(
                "MISSING_CAPABILITY",
                "config/capability-scope.json",
                f"Required capability {identifier} was removed",
            )
        )
    for item in capabilities:
        if item.get("status") not in valid_statuses:
            findings.append(
                Finding(
                    "INVALID_CAPABILITY_STATUS",
                    "config/capability-scope.json",
                    f"{item.get('id')} has an invalid status",
                )
            )
        if not item.get("genericity"):
            findings.append(
                Finding(
                    "MISSING_GENERICITY_PROOF",
                    "config/capability-scope.json",
                    f"{item.get('id')} lacks a genericity statement",
                )
            )
        if item.get("status") == "IMPLEMENTED" and not item.get("verification"):
            findings.append(
                Finding(
                    "UNVERIFIED_IMPLEMENTATION",
                    "config/capability-scope.json",
                    f"{item.get('id')} is IMPLEMENTED without verification",
                )
            )
        if item.get("status") == "IMPLEMENTED":
            cases = item.get("verificationCases", {})
            for case_class in ("positive", "negative", "failure"):
                if not cases.get(case_class):
                    findings.append(
                        Finding(
                            "INCOMPLETE_VERIFICATION_MATRIX",
                            "config/capability-scope.json",
                            f"{item.get('id')} lacks {case_class} verification",
                        )
                    )
        referenced_paths = [*item.get("implementation", []), *item.get("verification", [])]
        for referenced_path in referenced_paths:
            if not (ROOT / referenced_path).is_file():
                findings.append(
                    Finding(
                        "MISSING_CAPABILITY_EVIDENCE",
                        "config/capability-scope.json",
                        f"{item.get('id')} references missing {referenced_path}",
                    )
                )

    changed = _changed_paths()
    controlled_changed = _controlled_changed_paths(changed, policy, _head_quality_policy())
    scope_review_findings, review_manifest = _scope_review_findings(
        policy, controlled_changed, changed
    )
    findings.extend(scope_review_findings)

    head_scope = _head_scope()
    current_scope = {item["id"]: item for item in capabilities if item.get("id")}
    if head_scope is not None:
        rank = {"DEFERRED": 0, "NEXT": 1, "FOUNDATION": 2, "IMPLEMENTED": 3}
        for identifier, previous in head_scope.items():
            current = current_scope.get(identifier)
            if current is None:
                findings.append(
                    Finding(
                        "CAPABILITY_REMOVED",
                        "config/capability-scope.json",
                        f"Capability {identifier} exists on HEAD and may not be deleted",
                    )
                )
            elif rank[current["status"]] < rank[previous["status"]]:
                downgrade = next(
                    (
                        item
                        for item in review_manifest.get("statusDowngrades", [])
                        if item.get("capabilityId") == identifier
                        and item.get("from") == previous["status"]
                        and item.get("to") == current["status"]
                        and len(item.get("reason", "")) >= 20
                    ),
                    None,
                )
                if downgrade is None:
                    findings.append(
                        Finding(
                            "UNREVIEWED_CAPABILITY_REGRESSION",
                            "config/capability-scope.json",
                            f"Capability {identifier} regressed from {previous['status']} "
                            "without an exact reviewed downgrade",
                        )
                    )
    return findings


def _knowledge_check() -> Finding | None:
    completed = subprocess.run(
        [sys.executable, "scripts/catalog/build_project_index.py", "--check"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        shell=False,
    )
    if completed.returncode == 0:
        return None
    detail = (completed.stdout or completed.stderr).strip()
    return Finding("STALE_KNOWLEDGE", "knowledge/", detail)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--skip-knowledge", action="store_true")
    args = parser.parse_args()
    findings = check_repository()
    if not args.skip_knowledge:
        knowledge_finding = _knowledge_check()
        if knowledge_finding:
            findings.append(knowledge_finding)
    if args.json:
        print(json.dumps([asdict(finding) for finding in findings], indent=2))
    elif findings:
        for finding in findings:
            print(f"{finding.code}: {finding.path}: {finding.message}")
    else:
        print("Genericity, layer boundaries, scope manifest and knowledge freshness passed")
    if findings:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
