# G1 brief (verbatim)

```text
[REPO] C:\Users\neela\Documents\ChatGPT\Neo SF Q- Intel  [CHUNK] G1  [TIMEBOX] 60m (REQ-02 20m · REQ-03 20m · REQ-13 20m)
[ROLE] opus developer. Write TEST CODE ONLY. Do NOT change src/ unless a test proves a real defect —
       if it does, STOP and report it, do not fix it silently.
[RULES] CRLF forbidden, normalise \r\n -> \n on every write. No git add/commit/push.
[FILES_OWNED] tests/test_element_signature_functional.py (extend) · tests/test_locator_healing_bridge_functional.py (create)
       Do NOT touch tests/test_element_signature.py, tests/test_locator_proposal.py, tests/test_locator_healing_bridge.py, packages/**.

[WHY] A requirement matrix (Docs/29-d-series-requirement-test-matrix.md) found these proven against
 fakes only. Close them against REAL dependencies: real PostgreSQL, real source-tree metadata files.

[EXISTING_PATTERN] tests/test_element_signature_functional.py already implements the opt-in gate.
 Reuse it exactly: pytestmark skipif os.environ["NEO_SIGNATURE_FUNCTIONAL"] != "1", Settings() for
 DATABASE_URL + postgres_schema, psycopg connect with autocommit, SET search_path, migration 004
 applied from migrations/004_element_signature.sql, DELETE cleanup in fixture teardown.
 VERIFY the run with: NEO_SIGNATURE_FUNCTIONAL=1 .venv/Scripts/python -m pytest <files> -q
 Also confirm they SKIP (not fail) without that env var.

[AST_SIGNATURES]
 src/neo_sf_q_intel/element_signature.py
  [Class] ElementSignatureRepository(connection_factory: Callable[[], Connection], schema: str)
    -> save(signature: ElementSignature) -> None
    -> lookup(project_id, page_key, object_api_name, field_api_name) -> ElementSignature | None
    -> lookup_by_obligation(project_id, obligation_id) -> list[ElementSignature]
  [Class] SignatureStoreUnavailable(code: str)   codes: STORE_UNAVAILABLE · STORE_MISCONFIGURED · SIGNATURE_CORRUPT
  [Fn] build_signature(project_id, page_key, object_api_name, field_api_name, obligation_id,
       structure, attributes: dict[str,str], nearby: list[str], snapshot_root, captured_at_utc) -> ElementSignature
  [Model] ElementSignature: project_id · page_key · object_api_name · field_api_name · obligation_id
       · structure · attrs_present · attrs_hashed · nearby · snapshot_root · captured_at_utc · signature_sha256
 src/neo_sf_q_intel/locator_proposal.py
  [Fn] build_healing_context(*, obligation_id, object_api_name, field_api_name,
       dom_evidence: Mapping, field_metadata: FieldMetadata|None, signature: ElementSignature|None,
       graph_edges: Sequence[str]=(), intent_section: str|None=None) -> LocatorHealingContext
  [Class] LocatorProposalError(code)  codes: SIGNATURE_IDENTITY_MISMATCH · METADATA_IDENTITY_MISMATCH
       · DOM_EVIDENCE_INVALID · NO_CANDIDATES
 src/neo_sf_q_intel/context_feeds.py
  [Fn] metadata_lookup(salesforce_root: Path, object_api_name: str, field_api_name: str) -> FieldMetadata

[REQ-02] postgres-only; explicit unavailable; NO silent degradation to sqlite/json/cache.
 State_Given: repository built with a connection factory pointing at an unreachable/refused Postgres
 State_Action: save() and lookup()
 State_Expected: SignatureStoreUnavailable raised with code STORE_UNAVAILABLE.
   AND assert NO fallback occurred: no sqlite file, no json file, no cache dir created by the call.
   Prove the negative explicitly — snapshot the tmp/working dir before and after.
 Use a REAL psycopg connection attempt to a closed port (do not monkeypatch the driver).

[REQ-03] signature tampering detected on READ from a real row.
 State_Given: a valid signature saved to real Postgres
 State_Action: mutate ONE stored column directly via SQL so it disagrees with signature_sha256
   (do this for at least two distinct columns in separate tests, e.g. structure and attrs_hashed)
 State_Expected: lookup() raises SignatureStoreUnavailable code SIGNATURE_CORRUPT. Never returns a row.
 ALSO: mutate signature_sha256 itself and assert the same refusal.

[REQ-13] the context join refuses a mismatched field, with REAL inputs end to end.
 State_Given: signature read back from real Postgres + FieldMetadata read from the REAL configured
   source project via metadata_lookup(Settings().resolved_salesforce_root(Path.cwd()), ...)
   (skip the test if that root is absent, same style as tests/test_metadata_lookup.py)
 State_Action: build_healing_context with a signature whose field differs from field_api_name;
   separately with metadata whose field differs
 State_Expected: LocatorProposalError SIGNATURE_IDENTITY_MISMATCH / METADATA_IDENTITY_MISMATCH
 State_Action_2: matching identity -> context builds, and assert NO raw org value appears in
   json.dumps(context.model_dump(by_alias=True, mode="json"))

[ANTI_VACUOUS] a test that would pass when the operation never ran is a defect. Assert the
 precondition actually happened (row really present, mutation really applied, root really resolved)
 before asserting the outcome. Earlier in this project a probe test passed vacuously because the
 report was undefined and both branches looked identical.
[NO_OVERBUILD] no new helper framework, no fixtures beyond what these tests need.

[RETENTION] write quality/reviews/agent-exchanges/G1-brief.md (this brief verbatim, fenced) and
 quality/reviews/agent-exchanges/G1-return.md (your return JSON, fenced). Create dir if absent. LF only.

[RETURN] minified JSON only:
 {"files":[{"path","action","lines"}],"tests":{"added":N,"passed":N,"skipped":N,"failed":N},
  "redRunConfirmed":bool,"skipWithoutEnvVarConfirmed":bool,
  "defectsFound":[{"sev","req","desc","evidence"}],"deviations":[{"from","why"}],"minutesPerReq":{"REQ-02":N,"REQ-03":N,"REQ-13":N}}
```
