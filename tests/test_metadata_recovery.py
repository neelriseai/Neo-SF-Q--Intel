from __future__ import annotations

import hashlib
import io
import json
import os
import stat
import subprocess
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from neo_sf_q_intel.live_read_evidence import CliCompleted, SubprocessCliRunner
from neo_sf_q_intel.live_target_plan import BrowserMetadataDrift
from neo_sf_q_intel.metadata_recovery import (
    HostMetadataRecoveryAdapter,
    MetadataPropertyIntent,
    MetadataRecoveryCode,
    MetadataRecoveryConfig,
    MetadataRecoveryError,
    MetadataRecoveryLease,
    _read_metadata_archive,
    _root,
    edit_single_property,
    metadata_execution_binding,
    metadata_state_sha256,
)

NS = "http://soap.sforce.com/2006/04/metadata"
NOW = datetime(2026, 9, 11, tzinfo=UTC)
KEY = b"offline-private-journal-key-not-production" * 2
MEMBER = "FixtureSurface"
LOCATOR = f"unpackaged/flexipages/{MEMBER}.flexipage"
PREIMAGE = {
    "unpackaged/package.xml": (
        f'<Package xmlns="{NS}"><types><members>{MEMBER}</members>'
        "<name>FlexiPage</name></types><version>67.0</version></Package>"
    ).encode(),
    LOCATOR: (
        f'<?xml version="1.0" encoding="UTF-8"?>\n<FlexiPage xmlns="{NS}">\n'
        "  <flexiPageRegions><name>main</name><itemInstances><componentInstance>"
        "<componentInstanceProperties><name>displayVariant</name><value>original</value>"
        "</componentInstanceProperties><componentName>c:FixturePanel</componentName>"
        "<identifier>fixturePanel</identifier></componentInstance></itemInstances></flexiPageRegions>\n"
        "  <description>Unrelated property must remain byte-identical</description>\n"
        "</FlexiPage>\n"
    ).encode(),
}
CANDIDATE = {**PREIMAGE, LOCATOR: PREIMAGE[LOCATOR].replace(b">original<", b">Replacement<")}


def h(value):
    return hashlib.sha256(value.encode()).hexdigest()


def intent(**changes):
    value = MetadataPropertyIntent(
        scope_sha256=h("scope"),
        source_contract_sha256=h("source"),
        source_property_declaration_sha256=h("declaration"),
        target_plan_sha256=h("plan"),
        drift=BrowserMetadataDrift(
            metadataType="FlexiPage",
            member=MEMBER,
            componentName="c:FixturePanel",
            componentIdentifier="fixturePanel",
            propertyName="displayVariant",
            operation="SET_SCALAR",
            baselineValue="original",
            alternateValue="Replacement",
            restoration="PREIMAGE_EXACT",
            allowedPreStates=({"state": "ABSENT"}, {"state": "PRESENT", "value": "original"}),
            maximumFiles=1,
            maximumBytes=1048576,
        ),
        expected_preimage_sha256=metadata_state_sha256(PREIMAGE),
        expected_candidate_sha256=metadata_state_sha256(CANDIDATE),
        api_version="67.0",
        test_level="NoTestRun",
        test_classes=(),
        tests=(),
        expected_test_count=0,
    )
    return value.model_copy(update=changes)


def lease(source=None):
    return MetadataRecoveryLease(
        intent_sha256=_root(source or intent()),
        execution_binding_sha256=_root(
            metadata_execution_binding(
                MetadataRecoveryConfig(alias="test-alias", expected_cli_version="2.148.3"), "67.0"
            )
        ),
        org_fingerprint_sha256=h("org"),
        actor_fingerprint_sha256=h("actor"),
        classification_receipt_sha256=h("classification"),
        mutation_authority_receipt_sha256=h("authority"),
        recovery_authority_receipt_sha256=h("recovery"),
        restore_rehearsal_receipt_sha256=h("rehearsal"),
        one_use_claim_sha256=h("claim"),
        issued_at=NOW,
        expires_at=NOW + timedelta(minutes=15),
        recovery_deadline=NOW + timedelta(minutes=30),
    )


@pytest.fixture
def repo(tmp_path):
    subprocess.run(("git", "init", "--quiet", str(tmp_path)), check=True, capture_output=True)
    (tmp_path / ".gitignore").write_text(".runtime/\n", encoding="utf-8")
    return tmp_path


class Authority:
    def __init__(self):
        self.calls = []
        self.reject = None
        self.dispatches = {}
        self.process_quiescent = True

    def verify(self, source, permit, binding, operation, package, now):
        self.calls.append((operation, package))
        assert permit.intent_sha256 == _root(source)
        assert permit.execution_binding_sha256 == _root(binding)
        assert binding.alias == "test-alias" and binding.expected_cli_version == "2.148.3"
        if operation == self.reject:
            raise RuntimeError("sensitive authority payload must not leak")

    def claim_dispatch_once(self, source, permit, operation, package, now):
        key = (_root(source), operation)
        if key in self.dispatches:
            raise RuntimeError("durable dispatch replay")
        root = h(_root(source) + operation + package)
        self.dispatches[key] = root
        return root

    def read_dispatch_fence(self, source, permit, operation):
        return self.dispatches.get((_root(source), operation))

    def note_process_state(self, source, permit, operation, quiescent):
        self.process_quiescent = quiescent

    def require_recovery_quiescence(self, source, permit):
        if self.process_quiescent is not True:
            raise RuntimeError("process quiescence not independently proven")


class SalesforceFake:
    """Exact local test transport only; never invokes sf or another process."""

    def __init__(self):
        self.state = dict(PREIMAGE)
        self.baseline = dict(PREIMAGE)
        self.unrelated = {"another-member": b"unchanged"}
        self.calls = []
        self.jobs = {}
        self.deployments = []
        self.fail_check = False
        self.unknown_forward = False
        self.fail_report_once = False
        self.candidate_mismatch = False
        self.restore_mismatch = False
        self.restore_unknown = False
        self.nonquiescent = False
        self.extra_file = False
        self.before_call = lambda args: None
        self.after_retrieve = lambda directory: None
        self.authority = Authority()

    @staticmethod
    def output(value, status=0):
        return CliCompleted(status, json.dumps({"status": status, "result": value}).encode())

    def run(self, invocation):
        args = invocation.arguments
        self.calls.append(args)
        self.before_call(args)
        assert "--target-org" in args and args[args.index("--target-org") + 1] == "test-alias"
        assert 0 < invocation.timeout_seconds <= 30
        if args[:3] == ("project", "retrieve", "start"):
            assert args[args.index("--metadata") + 1] == f"FlexiPage:{MEMBER}"
            directory = Path(args[args.index("--target-metadata-dir") + 1])
            assert "--unzip" not in args
            with zipfile.ZipFile(directory / "capture.zip", "w") as archive:
                for locator, content in self.state.items():
                    archive.writestr(locator, content)
                if self.extra_file:
                    archive.writestr("unpackaged/unrelated.xml", b"unrelated")
            self.after_retrieve(directory)
            return self.output(
                {
                    "done": True,
                    "success": True,
                    "status": "Succeeded",
                    "fileProperties": [
                        {
                            "fileName": "unpackaged/package.xml",
                            "type": "Package",
                            "fullName": "package",
                        },
                        {"fileName": LOCATOR, "type": "FlexiPage", "fullName": MEMBER},
                    ],
                }
            )
        if args[:3] == ("project", "deploy", "start"):
            directory = Path(args[args.index("--metadata-dir") + 1])
            assert (
                directory.name == "unpackaged" and "--single-package" in args and "--async" in args
            )
            assert "--source-dir" not in args and "--ignore-warnings" not in args
            journal = directory.parent.parent
            records = [
                json.loads(path.read_bytes())["payload"] for path in journal.glob("event-*.json")
            ]
            assert any(value["operation"] == "PREIMAGE_DURABLE" for value in records)
            files = {
                path.relative_to(directory.parent).as_posix(): path.read_bytes()
                for path in directory.rglob("*")
                if path.is_file()
            }
            assert set(files) == set(PREIMAGE)
            check = "--dry-run" in args
            restoring = not check and files == self.baseline
            if not check:
                self.deployments.append(files)
                self.state = dict(files)
                if self.candidate_mismatch and not restoring:
                    self.state[LOCATOR] = self.state[LOCATOR].replace(b"Replacement", b"Wrong")
                if self.restore_mismatch and restoring:
                    self.state[LOCATOR] = self.state[LOCATOR].replace(b"original", b"Not-restored")
                if self.nonquiescent:
                    return CliCompleted(-1, b"", quiescent=False)
                if (self.unknown_forward and not restoring) or (self.restore_unknown and restoring):
                    return CliCompleted(-1, b"", timed_out=True)
            job = str(len(self.jobs) + 1).rjust(15, "0")
            self.jobs[job] = {"check": check, "restore": restoring, "canceled": False}
            return self.output({"id": job, "done": False, "status": "Queued", "files": []})
        job = args[args.index("--job-id") + 1]
        state = self.jobs[job]
        if args[:3] == ("project", "deploy", "cancel"):
            state["canceled"] = True
            return self.output({"id": job, "done": True, "status": "Canceled"})
        assert args[:3] == ("project", "deploy", "report")
        if self.fail_report_once and not state["check"] and not state["restore"]:
            if "reported" not in state:
                state["reported"] = True
                return CliCompleted(-1, b"", timed_out=True)
            if not state["canceled"]:
                return self.output({"id": job, "done": False, "status": "InProgress"})
        failing = self.fail_check and state["check"]
        canceled = state["canceled"]
        return self.output(
            {
                "id": job,
                "checkOnly": state["check"],
                "done": True,
                "success": not failing and not canceled,
                "status": "Failed" if failing else "Canceled" if canceled else "Succeeded",
                "rollbackOnError": True,
                "numberComponentErrors": int(failing),
                "numberComponentsDeployed": 0 if failing else 1,
                "numberComponentsTotal": 1,
                "numberTestErrors": 0,
                "numberTestsCompleted": 0,
                "numberTestsTotal": 0,
                "details": {
                    "componentFailures": [],
                    "componentSuccesses": [
                        {
                            "componentType": "FlexiPage",
                            "fullName": MEMBER,
                            "success": True,
                            "created": False,
                            "deleted": False,
                        },
                        {"componentType": "", "fullName": "package.xml", "success": True},
                    ],
                },
            },
            status=int(failing),
        )


def adapter(repo, runner=None, authority=None, enabled=True, now=lambda: NOW):
    runner = runner or SalesforceFake()
    return HostMetadataRecoveryAdapter(
        repo,
        MetadataRecoveryConfig(
            alias="test-alias", expected_cli_version="2.148.3", mutation_enabled=enabled
        ),
        authority or runner.authority,
        runner=runner,
        journal_authentication_key=KEY,
        now=now,
    )


def test_default_disabled_and_reuses_direct_shell_false_runner(repo):
    service = HostMetadataRecoveryAdapter(
        repo,
        MetadataRecoveryConfig(alias="test-alias", expected_cli_version="2.148.3"),
        Authority(),
        journal_authentication_key=KEY,
    )
    assert isinstance(service.runner, SubprocessCliRunner)
    report = service.run(intent(), lease())
    assert report.status == "NOT_RUN" and report.gap_codes == (MetadataRecoveryCode.DISABLED,)
    assert not (repo / ".runtime").exists()


def test_success_check_only_once_deploy_once_exact_preimage_restore_and_no_unrelated_changes(repo):
    runner = SalesforceFake()
    seen = []
    report = adapter(repo, runner).run(intent(), lease(), candidate_window=seen.append)
    assert report.status == "RESTORED", report
    assert report.candidate_reconciled and report.restored_exactly and report.zero_scoped_residue
    assert not report.acceptance_credit and not report.release_eligible
    assert runner.deployments == [CANDIDATE, PREIMAGE]
    assert seen == [metadata_state_sha256(CANDIDATE)]
    assert runner.state == PREIMAGE and runner.unrelated == {"another-member": b"unchanged"}
    assert sum("--dry-run" in args for args in runner.calls) == 1
    assert len(list((repo / ".runtime/metadata-recovery").glob("active-*.json"))) == 0
    text = report.model_dump_json()
    assert MEMBER not in text and "Original" not in text and "test-alias" not in text
    assert str(repo) not in text


def test_check_only_failure_never_dispatches_candidate_or_restore(repo):
    runner = SalesforceFake()
    runner.fail_check = True
    report = adapter(repo, runner).run(intent(), lease())
    assert report.status == "FAILED_BEFORE_DEPLOY"
    assert MetadataRecoveryCode.CHECK_ONLY_FAILED in report.gap_codes
    assert not runner.deployments and runner.state == PREIMAGE


def test_unknown_job_after_dispatch_quarantines_without_any_further_transport(repo):
    runner = SalesforceFake()
    runner.unknown_forward = True
    service = adapter(repo, runner)
    report = service.run(intent(), lease())
    assert report.status == "QUARANTINED"
    assert len(runner.deployments) == 1 and runner.state == CANDIDATE
    assert runner.calls[-1][:3] == ("project", "deploy", "start")
    assert list((repo / ".runtime/metadata-recovery").glob("active-*.json"))
    count = len(runner.calls)
    another = intent(scope_sha256=h("new-intent"))
    blocked = service.run(another, lease(another))
    assert MetadataRecoveryCode.JOURNAL_REPLAY in blocked.gap_codes
    assert len(runner.calls) == count


def test_known_job_timeout_cancels_waits_for_terminal_then_restores_once(repo):
    runner = SalesforceFake()
    runner.fail_report_once = True
    report = adapter(repo, runner).run(intent(), lease())
    assert report.status == "FAILED_RESTORED", report
    assert runner.deployments == [CANDIDATE, PREIMAGE]
    assert sum(args[:3] == ("project", "deploy", "cancel") for args in runner.calls) == 1


def test_candidate_mismatch_quarantines_unowned_state_instead_of_overwriting_it(repo):
    runner = SalesforceFake()
    runner.candidate_mismatch = True
    report = adapter(repo, runner).run(intent(), lease())
    assert report.status == "QUARANTINED"
    assert MetadataRecoveryCode.CANDIDATE_MISMATCH in report.gap_codes
    assert len(runner.deployments) == 1


def test_restore_mismatch_quarantines_and_keeps_org_claim(repo):
    runner = SalesforceFake()
    runner.restore_mismatch = True
    report = adapter(repo, runner).run(intent(), lease())
    assert report.status == "QUARANTINED" and not report.restored_exactly
    assert len(runner.deployments) == 2
    assert list((repo / ".runtime/metadata-recovery").glob("active-*.json"))


def test_nonquiescent_process_does_not_start_another_cli_process(repo):
    runner = SalesforceFake()
    runner.nonquiescent = True
    report = adapter(repo, runner).run(intent(), lease())
    assert report.status == "QUARANTINED"
    assert MetadataRecoveryCode.PROCESS_NOT_QUIESCENT in report.gap_codes
    assert runner.calls[-1][:3] == ("project", "deploy", "start")


def test_browser_candidate_window_failure_cannot_skip_restore_or_leak_error(repo):
    def fail(_):
        raise RuntimeError("PRIVATE-DOM-SESSION-CANARY")

    runner = SalesforceFake()
    report = adapter(repo, runner).run(intent(), lease(), candidate_window=fail)
    assert report.status == "FAILED_RESTORED" and runner.state == PREIMAGE
    assert "PRIVATE-DOM-SESSION-CANARY" not in report.model_dump_json()


def test_preimage_drift_before_deploy_blocks_without_reverting_someone_elses_change(repo):
    runner = SalesforceFake()
    retrieve_calls = 0

    def concurrent(args):
        nonlocal retrieve_calls
        if args[:3] == ("project", "retrieve", "start"):
            retrieve_calls += 1
            if retrieve_calls == 2:
                runner.state = {
                    **PREIMAGE,
                    LOCATOR: PREIMAGE[LOCATOR].replace(b"original", b"Changed"),
                }

    runner.before_call = concurrent
    report = adapter(repo, runner).run(intent(), lease())
    assert MetadataRecoveryCode.CONCURRENT_CHANGE in report.gap_codes
    assert not runner.deployments and runner.state != PREIMAGE


def test_authority_rejected_deploy_never_activates_recovery_write(repo):
    runner, authority = SalesforceFake(), Authority()
    authority.reject = "DEPLOY"
    report = adapter(repo, runner, authority).run(intent(), lease())
    assert report.status == "FAILED_BEFORE_DEPLOY" and not runner.deployments
    assert not any(operation == "RESTORE" for operation, _ in authority.calls)


def test_restart_recovery_uses_authenticated_journal_and_never_retries_restore(repo):
    runner = SalesforceFake()
    runner.restore_unknown = True
    service = adapter(repo, runner)
    assert service.run(intent(), lease()).status == "QUARANTINED"
    previous = len(runner.deployments)
    fresh_service = adapter(repo, runner)
    report = fresh_service.recover(intent(), lease())
    assert report.status == "QUARANTINED"
    assert len(runner.deployments) == previous


def test_journal_tampering_cannot_produce_compensation_authority(repo):
    runner = SalesforceFake()
    runner.unknown_forward = True
    service = adapter(repo, runner)
    assert service.run(intent(), lease()).status == "QUARANTINED"
    event = next((repo / ".runtime/metadata-recovery" / _root(intent())).glob("event-0000.json"))
    payload = json.loads(event.read_bytes())
    payload["payload"]["intent_sha256"] = h("forged")
    event.write_text(json.dumps(payload), encoding="utf-8")
    count = len(runner.calls)
    assert adapter(repo, runner).recover(intent(), lease()).status == "QUARANTINED"
    assert len(runner.calls) == count


@pytest.mark.parametrize(
    "path", ["../elsewhere", "/root", ".runtime/../escape", ".runtime\\escape", "outside/runtime"]
)
def test_journal_path_attacks_rejected(path):
    with pytest.raises(ValidationError):
        MetadataRecoveryConfig(alias="test", journal_root=path)


def test_unrelated_retrieved_component_is_rejected_not_dropped(repo):
    runner = SalesforceFake()
    runner.extra_file = True
    report = adapter(repo, runner).run(intent(), lease())
    assert report.status == "FAILED_BEFORE_DEPLOY" and not runner.deployments


def test_hardlinked_component_rejected_before_candidate_effect(repo):
    runner = SalesforceFake()

    def hardlink(directory):
        current = directory / "capture.zip"
        original = directory / "original.private"
        current.rename(original)
        os.link(original, current)

    runner.after_retrieve = hardlink
    report = adapter(repo, runner).run(intent(), lease())
    assert report.status == "FAILED_BEFORE_DEPLOY" and not runner.deployments


def test_parent_symlink_rejected_before_any_salesforce_transport(repo, tmp_path):
    runtime = repo / ".runtime"
    runtime.mkdir()
    destination = repo / "outside"
    destination.mkdir()
    try:
        (runtime / "metadata-recovery").symlink_to(destination, target_is_directory=True)
    except OSError:
        pytest.skip("Symlink creation unavailable to this Windows test account")
    runner = SalesforceFake()
    report = adapter(repo, runner).run(intent(), lease())
    assert report.status == "FAILED_BEFORE_DEPLOY" and not runner.calls


def test_property_editor_preserves_other_bytes_and_escapes_only_value():
    source = intent()
    edited = edit_single_property(PREIMAGE[LOCATOR], source)
    assert edited == PREIMAGE[LOCATOR].replace(b">original<", b">Replacement<")
    assert b"Unrelated property must remain byte-identical" in edited


@pytest.mark.parametrize(
    "content",
    [
        PREIMAGE[LOCATOR].replace(b"<value>", b"<value/><value>"),
        PREIMAGE[LOCATOR].replace(b">original<", b"><![CDATA[original]]><"),
        b'<!DOCTYPE x [<!ENTITY secret "unsafe">]>' + PREIMAGE[LOCATOR],
    ],
)
def test_ambiguous_or_non_roundtrippable_xml_is_blocked(content):
    with pytest.raises(MetadataRecoveryError):
        edit_single_property(content, intent())


@pytest.mark.parametrize("timestamp", ["2026-09-11T00:00:00", "2026-09-11T00:00:00-00:00"])
def test_naive_unknown_timezone_rejected(timestamp):
    payload = lease().model_dump(mode="json")
    payload["issued_at"] = timestamp
    with pytest.raises(ValidationError):
        MetadataRecoveryLease.model_validate(payload)


def test_offset_equivalence_has_identical_authority_digest():
    payload = lease().model_dump(mode="json")
    payload.update(
        {
            "issued_at": "2026-09-11T05:30:00+05:30",
            "expires_at": "2026-09-11T05:45:00+05:30",
            "recovery_deadline": "2026-09-11T06:00:00+05:30",
        }
    )
    assert _root(MetadataRecoveryLease.model_validate(payload)) == _root(lease())


def test_short_recovery_window_blocks_before_cli(repo):
    runner = SalesforceFake()
    permit = lease().model_copy(update={"recovery_deadline": NOW + timedelta(minutes=16)})
    report = adapter(repo, runner).run(intent(), permit)
    assert MetadataRecoveryCode.AUTHORITY_REJECTED in report.gap_codes
    assert not runner.calls


def test_restart_can_restore_after_unavailable_recovery_authority_without_redeploying_candidate(
    repo,
):
    runner, authority = SalesforceFake(), Authority()
    authority.reject = "RESTORE"
    assert adapter(repo, runner, authority).run(intent(), lease()).status == "QUARANTINED"
    assert runner.deployments == [CANDIDATE]
    authority.reject = None
    recovered = adapter(repo, runner, authority).recover(intent(), lease())
    assert recovered.status == "RESTORED", recovered
    assert runner.deployments == [CANDIDATE, PREIMAGE]
    assert runner.state == PREIMAGE


def test_restart_rejects_raw_preimage_tampering_even_when_xml_semantics_are_unchanged(repo):
    runner = SalesforceFake()
    runner.unknown_forward = True
    assert adapter(repo, runner).run(intent(), lease()).status == "QUARANTINED"
    preimage = repo / ".runtime/metadata-recovery" / _root(intent()) / "preimage" / LOCATOR
    preimage.write_bytes(
        preimage.read_bytes().replace(b"<description>", b"<!--tamper--><description>")
    )
    previous = len(runner.calls)
    assert adapter(repo, runner).recover(intent(), lease()).status == "QUARANTINED"
    assert len(runner.calls) == previous


@pytest.mark.parametrize(
    "attack", ["traversal", "absolute", "duplicate", "symlink", "bomb", "omitted"]
)
def test_archive_attacks_are_rejected_without_extracting_any_file(attack):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in PREIMAGE.items():
            if attack == "omitted" and name == LOCATOR:
                continue
            archive.writestr(name, data)
        if attack == "traversal":
            archive.writestr("../escape.xml", b"escape")
        elif attack == "absolute":
            archive.writestr("/outside.xml", b"escape")
        elif attack == "duplicate":
            with pytest.warns(UserWarning):
                archive.writestr(LOCATOR, b"duplicate")
        elif attack == "symlink":
            item = zipfile.ZipInfo("unpackaged/link")
            item.create_system = 3
            item.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(item, b"outside")
        elif attack == "bomb":
            archive.writestr("unpackaged/bomb", b"x" * 20000)
    with pytest.raises(MetadataRecoveryError):
        _read_metadata_archive(output.getvalue(), set(PREIMAGE), 16000)


def test_exact_component_identity_preserves_other_repeated_instances():
    original = PREIMAGE[LOCATOR]
    begin, end = (
        original.index(b"<itemInstances>"),
        original.index(b"</itemInstances>") + len(b"</itemInstances>"),
    )
    sibling = original[begin:end].replace(b"fixturePanel", b"anotherPanel")
    content = original[:end] + sibling + original[end:]
    assert edit_single_property(content, intent()) == content.replace(
        b">original<", b">Replacement<", 1
    )


def test_counter_booleans_are_not_numeric_check_only_evidence(repo):
    class BooleanCounterFake(SalesforceFake):
        def run(self, invocation):
            result = super().run(invocation)
            if invocation.arguments[:3] == ("project", "deploy", "report"):
                body = json.loads(result.stdout)
                body["result"]["numberComponentErrors"] = False
                return CliCompleted(result.returncode, json.dumps(body).encode())
            return result

    runner = BooleanCounterFake()
    report = adapter(repo, runner).run(intent(), lease())
    assert report.status == "FAILED_BEFORE_DEPLOY" and not runner.deployments


def test_windows_package_files_are_write_locked_during_final_authority_refresh(repo):
    if os.name != "nt":
        pytest.skip("Native Windows file-sharing lock contract")

    class MutatingAuthority(Authority):
        def verify(self, source, permit, binding, operation, package, now):
            super().verify(source, permit, binding, operation, package, now)
            if (
                operation == "CHECK_ONLY"
                and len([item for item in self.calls if item[0] == operation]) == 3
            ):
                path = repo / ".runtime/metadata-recovery" / _root(source) / "candidate" / LOCATOR
                path.write_bytes(b"wrong candidate")

    runner = SalesforceFake()
    report = adapter(repo, runner, MutatingAuthority()).run(intent(), lease())
    assert MetadataRecoveryCode.AUTHORITY_REJECTED in report.gap_codes
    assert not any(args[:3] == ("project", "deploy", "start") for args in runner.calls)


def truncate_journal_before(repo, source, operation):
    """Attacker removes an authenticated suffix, never forges a signature."""
    paths = sorted((repo / ".runtime/metadata-recovery" / _root(source)).glob("event-*.json"))
    first = next(
        index
        for index, path in enumerate(paths)
        if json.loads(path.read_bytes())["payload"]["operation"] == operation
    )
    for path in paths[first:]:
        path.unlink()


def test_active_candidate_window_excludes_even_same_process_recovery(repo):
    runner = SalesforceFake()
    nested = []

    def competing_recovery(_):
        count = len(runner.calls)
        nested.append(adapter(repo, runner).recover(intent(), lease()))
        assert len(runner.calls) == count
        assert runner.state == CANDIDATE

    report = adapter(repo, runner).run(intent(), lease(), candidate_window=competing_recovery)
    assert report.status == "RESTORED", report
    assert nested[0].gap_codes == (MetadataRecoveryCode.TRANSACTION_BUSY,)
    assert runner.deployments == [CANDIDATE, PREIMAGE]


def test_concurrent_unrelated_member_property_change_is_never_overwritten(repo):
    runner = SalesforceFake()

    def someone_elses_edit(_):
        runner.state[LOCATOR] = runner.state[LOCATOR].replace(
            b"Unrelated property must remain byte-identical", b"Concurrent independent edit"
        )

    report = adapter(repo, runner).run(intent(), lease(), candidate_window=someone_elses_edit)
    assert report.status == "QUARANTINED" and not report.restored_exactly
    assert MetadataRecoveryCode.CONCURRENT_CHANGE in report.gap_codes
    assert runner.deployments == [CANDIDATE]
    assert b"Concurrent independent edit" in runner.state[LOCATOR]


def test_restore_dispatch_fence_survives_authenticated_journal_suffix_truncation(repo):
    runner = SalesforceFake()
    runner.restore_unknown = True
    assert adapter(repo, runner).run(intent(), lease()).status == "QUARANTINED"
    assert len(runner.deployments) == 2
    truncate_journal_before(repo, intent(), "RESTORE_INTENT")
    restored = adapter(repo, runner).recover(intent(), lease())
    assert restored.status == "QUARANTINED"
    assert MetadataRecoveryCode.JOB_UNKNOWN in restored.gap_codes
    assert len(runner.deployments) == 2


@pytest.mark.parametrize("truncate_terminal", [False, True])
def test_restart_never_resumes_nonquiescent_process_without_independent_proof(
    repo, truncate_terminal
):
    runner = SalesforceFake()
    runner.nonquiescent = True
    assert adapter(repo, runner).run(intent(), lease()).status == "QUARANTINED"
    if truncate_terminal:
        truncate_journal_before(repo, intent(), "QUARANTINED")
    count = len(runner.calls)
    report = adapter(repo, runner).recover(intent(), lease())
    assert report.status == "QUARANTINED"
    assert MetadataRecoveryCode.PROCESS_NOT_QUIESCENT in report.gap_codes
    assert len(runner.calls) == count


def test_nonquiescent_report_and_rolled_back_terminal_still_blocks_restart(repo):
    class ReportProcessUnknown(SalesforceFake):
        def run(self, invocation):
            result = super().run(invocation)
            if invocation.arguments[:3] == ("project", "deploy", "report"):
                job = invocation.arguments[invocation.arguments.index("--job-id") + 1]
                if not self.jobs[job]["check"]:
                    return CliCompleted(-1, b"", quiescent=False)
            return result

    runner = ReportProcessUnknown()
    assert adapter(repo, runner).run(intent(), lease()).status == "QUARANTINED"
    truncate_journal_before(repo, intent(), "QUARANTINED")
    count = len(runner.calls)
    assert adapter(repo, runner).recover(intent(), lease()).status == "QUARANTINED"
    assert len(runner.calls) == count


@pytest.mark.parametrize("blocked_at", ["dispatch_fence", "process_fence"])
def test_expiry_during_durable_fence_never_dispatches_or_compensates(repo, blocked_at):
    current = [NOW]

    class SlowFenceAuthority(Authority):
        def claim_dispatch_once(self, source, permit, operation, package, now):
            result = super().claim_dispatch_once(source, permit, operation, package, now)
            if operation == "DEPLOY" and blocked_at == "dispatch_fence":
                current[0] = permit.expires_at
            return result

        def note_process_state(self, source, permit, operation, quiescent):
            super().note_process_state(source, permit, operation, quiescent)
            if operation == "DEPLOY" and quiescent is None and blocked_at == "process_fence":
                current[0] = permit.expires_at

    runner, authority = SalesforceFake(), SlowFenceAuthority()
    report = adapter(repo, runner, authority, now=lambda: current[0]).run(intent(), lease())
    assert report.status == "QUARANTINED", report
    assert MetadataRecoveryCode.AUTHORITY_REJECTED in report.gap_codes
    assert not runner.deployments and runner.state == PREIMAGE
    assert not any(operation == "RESTORE" for operation, _ in authority.calls)
    count = len(runner.calls)
    assert (
        adapter(repo, runner, authority, now=lambda: current[0]).recover(intent(), lease()).status
        == "QUARANTINED"
    )
    assert len(runner.calls) == count


@pytest.mark.parametrize(
    "field,value", [("alias", "different-alias"), ("expected_cli_version", "2.148.4")]
)
def test_actual_transport_binding_mismatch_blocks_before_cli(repo, field, value):
    runner = SalesforceFake()
    service = adapter(repo, runner)
    service.config = service.config.model_copy(update={field: value})
    report = service.run(intent(), lease())
    assert MetadataRecoveryCode.AUTHORITY_REJECTED in report.gap_codes
    assert not runner.calls


def test_actual_transport_binding_change_during_fence_cannot_dispatch(repo):
    class ChangingAuthority(Authority):
        def claim_dispatch_once(self, source, permit, operation, package, now):
            result = super().claim_dispatch_once(source, permit, operation, package, now)
            if operation == "DEPLOY":
                service.config = service.config.model_copy(update={"alias": "different-alias"})
            return result

    runner = SalesforceFake()
    service = adapter(repo, runner, ChangingAuthority())
    report = service.run(intent(), lease())
    assert report.status == "QUARANTINED"
    assert MetadataRecoveryCode.AUTHORITY_REJECTED in report.gap_codes
    assert not runner.deployments


def absent_preimage():
    return {
        **PREIMAGE,
        LOCATOR: PREIMAGE[LOCATOR].replace(
            b"<componentInstanceProperties><name>displayVariant</name><value>original</value>"
            b"</componentInstanceProperties>",
            b"",
        ),
    }


def test_absent_scalar_set_preserves_other_bytes_and_restores_original_absence(repo):
    baseline = absent_preimage()
    changed = {**baseline, LOCATOR: edit_single_property(baseline[LOCATOR], intent())}
    source = intent(
        expected_preimage_sha256=metadata_state_sha256(baseline),
        expected_candidate_sha256=metadata_state_sha256(changed),
    )
    runner = SalesforceFake()
    runner.baseline, runner.state = baseline, dict(baseline)
    report = adapter(repo, runner).run(source, lease(source))
    assert report.status == "RESTORED", report
    assert runner.deployments == [changed, baseline]
    assert runner.state[LOCATOR] == baseline[LOCATOR]
    assert b"displayVariant" not in runner.state[LOCATOR]
    assert b"Unrelated property must remain byte-identical" in changed[LOCATOR]


@pytest.mark.parametrize("absent", [False, True])
def test_scalar_set_cannot_exceed_source_member_byte_cap(absent):
    original = absent_preimage()[LOCATOR] if absent else PREIMAGE[LOCATOR]
    source = intent(drift=intent().drift.model_copy(update={"maximum_bytes": len(original)}))
    with pytest.raises(MetadataRecoveryError) as error:
        edit_single_property(original, source)
    assert error.value.code == MetadataRecoveryCode.SOURCE_BINDING_INVALID


@pytest.mark.parametrize("field,value", [("property_path", []), ("relative_file", "elsewhere.xml")])
def test_caller_cannot_override_compiled_property_or_member_path(field, value):
    with pytest.raises(ValidationError):
        MetadataPropertyIntent.model_validate({**intent().model_dump(), field: value})


def test_absent_requires_explicit_source_allowed_state_and_exact_existing_component():
    source = intent()
    present_only = source.model_copy(
        update={
            "drift": source.drift.model_copy(
                update={
                    "allowed_pre_states": tuple(
                        item for item in source.drift.allowed_pre_states if item.state == "PRESENT"
                    )
                }
            )
        }
    )
    with pytest.raises(MetadataRecoveryError):
        edit_single_property(absent_preimage()[LOCATOR], present_only)
    with pytest.raises(MetadataRecoveryError):
        edit_single_property(
            absent_preimage()[LOCATOR].replace(b"fixturePanel", b"otherPanel"), source
        )


def specified_test_intent():
    return intent(
        test_level="RunSpecifiedTests",
        test_classes=("FixtureTests",),
        tests=("FixtureTests.first", "FixtureTests.second"),
        expected_test_count=2,
    )


class SpecifiedClassTestsFake(SalesforceFake):
    def run(self, invocation):
        args = invocation.arguments
        if args[:3] == ("project", "deploy", "start"):
            selected = tuple(
                args[index + 1] for index, value in enumerate(args) if value == "--tests"
            )
            assert selected == ("FixtureTests",)  # MDAPI takes classes, never Class.method.
        result = super().run(invocation)
        if args[:3] == ("project", "deploy", "report"):
            body = json.loads(result.stdout)
            body["result"].update(numberTestsTotal=2, numberTestsCompleted=2)
            body["result"]["details"]["runTestResult"] = {
                "failures": [],
                "successes": [
                    {"name": "FixtureTests", "methodName": method} for method in ("first", "second")
                ],
            }
            return CliCompleted(result.returncode, json.dumps(body).encode())
        return result


def test_deploy_specifies_classes_but_independently_accounts_for_every_method(repo):
    source = specified_test_intent()
    runner = SpecifiedClassTestsFake()
    report = adapter(repo, runner).run(source, lease(source))
    assert report.status == "RESTORED", report
    assert runner.deployments == [CANDIDATE, PREIMAGE]


def test_missing_method_cannot_pass_merely_because_selected_class_and_count_match(repo):
    class MissingMethodFake(SpecifiedClassTestsFake):
        def run(self, invocation):
            result = super().run(invocation)
            if invocation.arguments[:3] == ("project", "deploy", "report"):
                body = json.loads(result.stdout)
                body["result"]["details"]["runTestResult"]["successes"].pop()
                return CliCompleted(result.returncode, json.dumps(body).encode())
            return result

    source, runner = specified_test_intent(), MissingMethodFake()
    report = adapter(repo, runner).run(source, lease(source))
    assert report.status == "FAILED_BEFORE_DEPLOY" and not runner.deployments
    assert MetadataRecoveryCode.CHECK_ONLY_FAILED in report.gap_codes


@pytest.mark.parametrize(
    "changes",
    [
        {"test_classes": ("FixtureTests.first",)},
        {"test_classes": ("UnrelatedTests",)},
        {"test_classes": ("FixtureTests", "UnrelatedTests")},
        {"test_classes": ()},
        {"expected_test_count": 1},
    ],
)
def test_deployment_class_selection_is_not_a_method_or_incomplete_inventory(changes):
    with pytest.raises(ValidationError):
        MetadataPropertyIntent.model_validate({**specified_test_intent().model_dump(), **changes})


def test_nonquiescent_recovery_report_cannot_fall_through_into_cancel_or_restore(repo):
    class RecoveryReportUnknown(SalesforceFake):
        def __init__(self):
            super().__init__()
            self.forward_reports = 0

        def run(self, invocation):
            result = super().run(invocation)
            args = invocation.arguments
            if args[:3] == ("project", "deploy", "report"):
                job = args[args.index("--job-id") + 1]
                if not self.jobs[job]["check"]:
                    self.forward_reports += 1
                    return CliCompleted(
                        -1, b"", timed_out=True, quiescent=self.forward_reports == 1
                    )
            return result

    runner = RecoveryReportUnknown()
    report = adapter(repo, runner).run(intent(), lease())
    assert report.status == "QUARANTINED"
    assert MetadataRecoveryCode.PROCESS_NOT_QUIESCENT in report.gap_codes
    assert runner.forward_reports == 2
    assert runner.calls[-1][:3] == ("project", "deploy", "report")
    assert not any(args[:3] == ("project", "deploy", "cancel") for args in runner.calls)
    assert runner.deployments == [CANDIDATE]
