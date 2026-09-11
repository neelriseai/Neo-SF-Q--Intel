"""No-org regressions for bounded metadata work and per-dispatch identity integrity."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from threading import Barrier, Event, Lock

import pytest

import neo_sf_q_intel.live_read_evidence as live_read
import tests.test_live_read_evidence as support
from neo_sf_q_intel.live_read_evidence import CliCompleted, LiveReadCode, LiveReadError
from neo_sf_q_intel.live_target_plan import TargetPartition


class _MetadataRunner(support._Runner):
    def __init__(self):
        super().__init__()
        self.metadata_callback = None
        self.metadata_calls = []
        self.stages = {}
        self.lock = Lock()

    def run(self, invocation):
        if invocation.arguments[:3] != ("project", "retrieve", "start"):
            return super().run(invocation)
        arguments = invocation.arguments
        member = arguments[arguments.index("--metadata") + 1].split(":", 1)[1]
        stage = Path(arguments[arguments.index("--target-metadata-dir") + 1])
        with self.lock:
            self.invocations.append(invocation)
            self.metadata_calls.append(member)
            self.stages[member] = stage
        if self.metadata_callback is not None:
            return self.metadata_callback(member, stage)
        return self.response(member, stage)

    @staticmethod
    def response(member, stage):
        component = stage / f"unpackaged/classes/{member}.cls"
        component.parent.mkdir(parents=True)
        component.write_text(f"public class {member} {{}}", encoding="utf-8")
        (stage / "unpackaged/package.xml").write_text(
            '<Package xmlns="http://soap.sforce.com/2006/04/metadata"><types>'
            f"<members>{member}</members><name>ApexClass</name>"
            "</types><version>67.0</version></Package>",
            encoding="utf-8",
        )
        return support._completed(
            {
                "status": 0,
                "result": {
                    "done": True,
                    "success": True,
                    "status": "Succeeded",
                    "fileProperties": [
                        {
                            "type": "ApexClass",
                            "fullName": member,
                            "fileName": f"unpackaged/classes/{member}.cls",
                        },
                        {
                            "type": "Package",
                            "fullName": "package",
                            "fileName": "unpackaged/package.xml",
                        },
                    ],
                },
            }
        )


def _parallel_fixture(tmp_path, monkeypatch, *, workers=4, count=8):
    source_profile = support._source_profile

    def expanded_profile(scope):
        profile = source_profile(scope)
        prototype = profile["metadata"][0]
        profile["metadata"] = [
            {**prototype, "member": f"IndependentMetadata{index}"} for index in range(count)
        ]
        return profile

    monkeypatch.setattr(support, "_source_profile", expanded_profile)
    runner = _MetadataRunner()
    executor, port, _, ledger = support._fixture(tmp_path, runner=runner)
    executor = replace(
        executor,
        config=executor.config.model_copy(update={"maximum_parallel_metadata_reads": workers}),
    )
    targets = tuple(
        target
        for target in port.capture_value.plan.targets
        if target.partition is TargetPartition.METADATA
    )
    return executor, port, runner, ledger, targets


def _assert_no_operation_receipts(ledger):
    for gate in ("SF-L03", "SF-L04", "SF-L05"):
        assert ledger.replay(campaign_id="campaign-live-read", gate_id=gate) == ()


@pytest.mark.parametrize("cached_display", [False, True])
def test_transient_identity_aba_between_members_cannot_issue_evidence(tmp_path, cached_display):
    executor, _, runner, ledger = support._fixture(tmp_path, record_count=3)
    original = runner.run
    state = {"other_org": False, "reads": 0}

    def switch(invocation):
        result = original(invocation)
        if support._is_business_rest(invocation):
            state["reads"] += 1
            if state["reads"] == 1:
                state["other_org"] = True
            elif state["reads"] == 3:
                # The vulnerable partition-only guard never saw the intermediate identity.
                state["other_org"] = False
        elif state["other_org"]:
            if not cached_display and invocation.arguments[:2] == ("org", "display"):
                document = json.loads(result.stdout)
                document["result"]["id"] = "00D000000000002AAA"
                return support._completed(document)
            if invocation.arguments[:4] == ("api", "request", "rest", "/services/oauth2/userinfo"):
                document = json.loads(result.stdout)
                document["result"]["body"]["organization_id"] = "00D000000000002AAA"
                return support._completed(document)
        return result

    runner.run = switch
    with pytest.raises(LiveReadError, match="^LIVE_READ_IDENTITY_MISMATCH$"):
        executor.execute()
    assert state["reads"] == 1
    _assert_no_operation_receipts(ledger)


def test_parallel_cold_staging_root_creation_has_no_check_then_create_race(tmp_path, monkeypatch):
    support._fixture(tmp_path)
    (tmp_path / ".runtime").mkdir(exist_ok=True)
    root = tmp_path / ".runtime/parallel-first-start"
    simultaneous_create = Barrier(4)
    original_mkdir = Path.mkdir

    def raced_mkdir(path, *args, **kwargs):
        if path == root:
            simultaneous_create.wait(timeout=10)
        return original_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", raced_mkdir)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = tuple(
            pool.map(lambda _: live_read._prepare_staging_root(tmp_path, root), range(4))
        )
    assert results == (None,) * 4
    live_read._checked_identity(root, directory=True)


@pytest.mark.parametrize("workers", [1, 2, 4])
def test_metadata_parallel_cap_and_source_order_survive_reverse_completion(
    tmp_path, monkeypatch, workers
):
    executor, _, runner, ledger, targets = _parallel_fixture(
        tmp_path, monkeypatch, workers=workers, count=workers * 2
    )
    order = [target.specification["member"] for target in targets]
    rank = {member: index for index, member in enumerate(order)}
    entered = Barrier(workers)
    finished = [Event() for _ in order]
    lock = Lock()
    state = {"active": 0, "maximum": 0}
    completions = []

    def reverse_completion(member, stage):
        index = rank[member]
        with lock:
            state["active"] += 1
            state["maximum"] = max(state["maximum"], state["active"])
        try:
            result = runner.response(member, stage)
            entered.wait(timeout=10)
            if (index + 1) % workers:
                assert finished[index + 1].wait(timeout=10)
            with lock:
                completions.append(member)
            finished[index].set()
            return result
        finally:
            with lock:
                state["active"] -= 1

    runner.metadata_callback = reverse_completion
    result = executor.execute()
    assert state == {"active": 0, "maximum": workers}
    assert completions == [
        member
        for offset in range(0, len(order), workers)
        for member in reversed(order[offset : offset + workers])
    ]
    assert [
        observation.target_sha256
        for observation in result.observations
        if observation.partition is TargetPartition.METADATA
    ] == [target.target_sha256 for target in targets]
    assert len(set(runner.stages.values())) == len(order)
    assert not list((tmp_path / executor.config.staging_root).iterdir())
    assert len(ledger.replay(campaign_id="campaign-live-read", gate_id="SF-L05")) == 1


@pytest.mark.parametrize("quiescent", [True, False])
def test_failed_metadata_wave_stops_later_waves_and_cleans_only_quiescent_stages(
    tmp_path, monkeypatch, quiescent
):
    executor, _, runner, ledger, targets = _parallel_fixture(tmp_path, monkeypatch)
    first_wave = {target.specification["member"] for target in targets[:4]}
    failing_member = targets[0].specification["member"]
    entered = Barrier(4)

    def fail_one(member, stage):
        result = runner.response(member, stage)
        entered.wait(timeout=10)
        if member == failing_member:
            return CliCompleted(1, b"secret-error-canary", quiescent=quiescent)
        return result

    runner.metadata_callback = fail_one
    error = "LIVE_READ_CLI_FAILED" if quiescent else "LIVE_READ_PROCESS_NOT_QUIESCENT"
    with pytest.raises(LiveReadError, match=f"^{error}$"):
        executor.execute()
    assert set(runner.metadata_calls) == first_wave
    _assert_no_operation_receipts(ledger)
    remaining = tuple((tmp_path / executor.config.staging_root).iterdir())
    if quiescent:
        assert remaining == ()
    else:
        assert remaining == (runner.stages[failing_member],)
        assert (remaining[0] / "unpackaged/package.xml").is_file()


@pytest.mark.parametrize("change", ["expiry", "revocation"])
def test_metadata_authority_loss_during_wave_blocks_receipts_and_later_dispatch(
    tmp_path, monkeypatch, change
):
    executor, port, runner, ledger, targets = _parallel_fixture(tmp_path, monkeypatch)
    entered = Barrier(4)
    changed = Barrier(4)
    revoked = Event()
    current = [executor.clock()]

    def revalidate():
        if revoked.is_set():
            raise LiveReadError(LiveReadCode.AUTHORITY_INVALID)

    executor = replace(executor, clock=lambda: current[0], authority_revalidator=revalidate)

    def expire_after_dispatch(member, stage):
        result = runner.response(member, stage)
        if entered.wait(timeout=10) == 0:
            if change == "expiry":
                current[0] = live_read._parse_time(port.capture_value.plan.valid_until) + timedelta(
                    seconds=1
                )
            else:
                revoked.set()
        changed.wait(timeout=10)
        return result

    runner.metadata_callback = expire_after_dispatch
    with pytest.raises(LiveReadError, match="^LIVE_READ_AUTHORITY_INVALID$"):
        executor.execute()
    assert set(runner.metadata_calls) == {target.specification["member"] for target in targets[:4]}
    _assert_no_operation_receipts(ledger)
    assert not list((tmp_path / executor.config.staging_root).iterdir())
