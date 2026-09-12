# G4 brief (verbatim)

```
[REPO] C:\Users\neela\Documents\ChatGPT\Neo SF Q- Intel  [CHUNK] G4  [TIMEBOX] 20m
[ROLE] opus developer. TEST CODE ONLY. Do NOT modify src/**. If a test proves a real src defect,
       STOP and report it in the return JSON — do not fix it silently.
[RULES] CRLF forbidden, normalise \r\n -> \n on every write. No git add/commit/push.
[FILES_OWNED] tests/test_metadata_lookup.py · tests/test_mcp_server.py (append only)
       Do NOT touch any other file, including Docs/**.

[DEFECT] Docs/29 marks REQ-HEAL-05 as "COVERED (see note 1)" where note 1 admits:
 the "no live org call" clause has NO dedicated assertion.
 A verdict must not be softened by a parenthetical. Either the clause is asserted, or the row is
 not COVERED. Close it by asserting it.

[REQ-HEAL-05] metadata lookup is repo-confined; api-name grammar only; no traversal;
              AND NO LIVE ORG CALL.
 The first three clauses already have tests. Only the last is unasserted.

[EXISTING_PATTERN] tests/test_mcp_server.py::test_salesforce_inspection_is_unavailable_without_any_transport
 already does exactly this for another tool. READ IT FIRST and follow its shape:
   monkeypatch.setattr("subprocess.run", forbidden)
   monkeypatch.setattr("neo_sf_q_intel.salesforce_cli.SalesforceCLI.org_status", forbidden)
   monkeypatch.setattr("neo_sf_q_intel.salesforce_cli.SalesforceCLI.rest_get", forbidden)
   where `forbidden` calls pytest.fail(...)
 Reuse that idiom. Do not invent a new mocking approach.

[AST_SIGNATURES]
 [Fn] neo_sf_q_intel.context_feeds.metadata_lookup(salesforce_root: Path, object_api_name: str,
      field_api_name: str) -> FieldMetadata
 [Fn] neo_sf_q_intel.mcp_server.build_mcp(settings: Settings, service: AssuranceService,
      repository_root: Path | None = None) -> MCPServer     tool name: "metadata_lookup"
 [Class] ContextFeedError(code)  codes: OBJECT_NAME_INVALID · FIELD_NAME_INVALID ·
      FIELD_METADATA_NOT_FOUND · FIELD_METADATA_INVALID · FIELD_METADATA_UNREADABLE ·
      FIELD_METADATA_TOO_LARGE · SALESFORCE_ROOT_UNAVAILABLE

[STATE_TRANSITIONS] add these tests.
 1 State: a valid field exists in a tmp source tree · every org transport monkeypatched to fail the test
   -> Action: metadata_lookup(...)
   -> Expected: returns the FieldMetadata, and NO transport was invoked
 2 State: same, but the field is ABSENT (the standard-field case, e.g. Opportunity.Amount)
   -> Expected: raises ContextFeedError FIELD_METADATA_NOT_FOUND, and NO transport was invoked.
      THIS IS THE IMPORTANT ONE — "not found in source" must never trigger a fallback org describe.
 3 State: same transports patched · the REAL configured source project (skip if absent, follow the
   existing `requires_source_project` marker already in tests/test_metadata_lookup.py)
   -> Action: metadata_lookup on Opportunity.Regional_VP_Approver__c
   -> Expected: real metadata returned, NO transport invoked
 4 MCP level, in tests/test_mcp_server.py, following the existing forbidden-transport test's shape:
   call the "metadata_lookup" tool with every org transport patched to fail
   -> Expected: tool returns the payload, no transport invoked
 5 MCP level: call it for an absent field with transports patched
   -> Expected: ToolError ending FIELD_METADATA_NOT_FOUND, no transport invoked

[ANTI_VACUOUS] MANDATORY. A `forbidden` stub that is never reached proves nothing on its own, so
 each test must ALSO assert the real outcome (the returned metadata / the raised code). Additionally
 write ONE self-check test proving the guard actually bites: patch the transports the same way and
 assert that calling the patched `subprocess.run` directly DOES fail the guard — so a future
 refactor that silently stops patching cannot make these tests vacuous.

[NO_OVERBUILD] no shared fixture module, no new conftest. Reuse what the two files already define.

[VERIFY]
 .venv/Scripts/python -m pytest tests/test_metadata_lookup.py tests/test_mcp_server.py -q
 .venv/Scripts/python -m ruff check src tests
 .venv/Scripts/python -m ruff format --check tests/test_metadata_lookup.py tests/test_mcp_server.py
 Confirm no other suite regressed:
 .venv/Scripts/python -m pytest tests/test_context_feeds.py tests/test_locator_proposal.py -q

[RETENTION] write quality/reviews/agent-exchanges/G4-brief.md (this brief verbatim, fenced) and
 G4-return.md (your return JSON, fenced). LF only.

[RETURN] minified JSON only:
 {"files":[{"path","action","lines"}],"tests":{"added":N,"passed":N,"failed":N},
  "redRunConfirmed":bool,"guardSelfCheckAdded":bool,
  "transportsPatched":[],"defectsFound":[{"sev","desc","evidence"}],"deviations":[{"from","why"}]}
```
