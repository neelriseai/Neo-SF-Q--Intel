"""P0 containment regressions: candidate Node tests cannot mint local authority."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

import neo_sf_q_intel.local_validation_runner as runner_module
from neo_sf_q_intel.local_validation_runner import (
    LocalValidationRunError,
    NodeInvocation,
    SubprocessNodeRunner,
    _node_invocation,
)
from tests.test_local_validation_runner import _compiled, _scope, _service


@pytest.mark.parametrize(
    "candidate",
    [
        b"// No tests registered.\n",
        b"process.exit(0);",
        b"process.exitCode=0;",
        b"process.stdout.write(JSON.stringify({complete:true,tests:1,pass:1}));",
        b"process.stdout.write(JSON.stringify({document:'e30=',mac:'0'.repeat(64)}));",
        b"import {serialize} from 'node:v8';"
        b"process.stdout.write(serialize({type:'test:pass',data:{name:'forged'}}));",
        b"process.stdout.write=()=>true;",
        b"import fs from 'node:fs';fs.writeSync=()=>1;",
        b"import crypto from 'node:crypto';crypto.createHmac=()=>({});",
        b"import {spawnSync} from 'node:child_process';"
        b"spawnSync(process.execPath,['-e','process.exit(0)']);",
    ],
    ids=[
        "zero-tests",
        "process-exit",
        "exit-code",
        "unsigned-summary",
        "signed-shape",
        "serialized-event-frame",
        "stdout-monkeypatch",
        "fs-monkeypatch",
        "crypto-monkeypatch",
        "child-spawn",
    ],
)
def test_candidate_test_programs_are_blocked_before_any_process_or_output(
    tmp_path: Path, monkeypatch, capsys, candidate: bytes
) -> None:
    test = tmp_path / "candidate.test.mjs"
    test.write_bytes(candidate)
    invocation = _node_invocation(
        tmp_path,
        SimpleNamespace(working_directory=".", runner_kind="NODE_TEST"),
        test.name,
        node_test_driver=tmp_path / "host-driver.mjs",
        timeout_seconds=5,
        maximum_output_bytes=4096,
    )

    def no_dispatch(*args, **kwargs):
        pytest.fail("Untrusted NODE_TEST reached executable resolution or process creation")

    monkeypatch.setattr(runner_module.shutil, "which", no_dispatch)
    monkeypatch.setattr(runner_module.subprocess, "Popen", no_dispatch)
    with pytest.raises(LocalValidationRunError) as refusal:
        SubprocessNodeRunner().run(invocation)
    assert refusal.value.code == "LOCAL_NODE_TEST_ATTESTATION_UNTRUSTED"
    assert "--allow-child-process" not in invocation.arguments
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("permission", ["--allow-child-process", "--allow-child-process=true"])
def test_real_runner_never_grants_candidate_child_process_authority(
    tmp_path: Path, monkeypatch, permission: str
) -> None:
    def no_dispatch(*args, **kwargs):
        pytest.fail("Forbidden child-process authority reached dispatch")

    monkeypatch.setattr(runner_module.subprocess, "Popen", no_dispatch)
    with pytest.raises(LocalValidationRunError) as refusal:
        SubprocessNodeRunner().run(NodeInvocation((permission,), tmp_path, 5, 4096))
    assert refusal.value.code == "LOCAL_CHILD_PROCESS_PERMISSION_FORBIDDEN"


def test_real_blocked_candidate_cannot_leave_a_signed_or_durable_pass(
    tmp_path, monkeypatch
) -> None:
    repository, compilation = _compiled(tmp_path)
    node = SubprocessNodeRunner()
    # Version discovery is not the authority boundary.  Pin a permitted version
    # and keep the actual production dispatch method, not a fake result runner.
    monkeypatch.setattr(SubprocessNodeRunner, "version", lambda self, **kwargs: "26.5.0")
    service, ledger, artifacts, _ = _service(tmp_path, repository, node)
    scope = _scope(compilation)

    def no_store(*args, **kwargs):
        pytest.fail("Blocked candidate attempted to append trusted evidence")

    monkeypatch.setattr(artifacts, "append", no_store)
    with pytest.raises(LocalValidationRunError) as refusal:
        service.run(compilation, scope=scope)
    assert refusal.value.code == "LOCAL_NODE_TEST_ATTESTATION_UNTRUSTED"
    assert ledger.replay(campaign_id=scope.campaign_id) == ()


@pytest.mark.skipif(shutil.which("node") is None, reason="Node runtime unavailable")
def test_real_node_version_probe_remains_available_without_candidate_execution(tmp_path) -> None:
    version = SubprocessNodeRunner().version(timeout_seconds=5)
    assert re.fullmatch(r"\d+\.\d+\.\d+", version)
    with pytest.raises(LocalValidationRunError) as refusal:
        SubprocessNodeRunner().run(NodeInvocation(("--version",), tmp_path, 5, 4096, b"x" * 32))
    assert refusal.value.code == "LOCAL_NODE_TEST_ATTESTATION_UNTRUSTED"


@pytest.mark.skipif(shutil.which("node") is None, reason="Node runtime unavailable")
def test_same_isolate_lexical_buffer_secret_is_not_an_authority_boundary(tmp_path) -> None:
    # This probe emits booleans only, never random bytes, heap contents, or an
    # exploit document.  A private lexical variable is not process isolation.
    program = "".join(
        [
            "const v8=require('node:v8');",
            "let supported=false,visible=false;",
            "try{const key=require('node:crypto').randomBytes(32);",
            "const rows=v8.queryObjects(Buffer,{format:'summary'});",
            "supported=Array.isArray(rows);",
            "const marker=key.subarray(0,8).toString('hex').match(/../g).join(' ');",
            "visible=rows.some(value=>String(value).includes(marker));}catch{}",
            "process.stdout.write(JSON.stringify({supported,visible}));",
        ]
    )
    completed = SubprocessNodeRunner().run(
        NodeInvocation(("--permission", "-e", program), tmp_path, 10, 4096)
    )
    assert completed.returncode == 0 and completed.quiescent
    assert not completed.timed_out and not completed.output_exceeded
    observed = json.loads(completed.stdout)
    if not observed["supported"]:
        pytest.skip("Installed Node lacks the heap introspection API; no isolation claim follows")
    assert observed == {"supported": True, "visible": True}
