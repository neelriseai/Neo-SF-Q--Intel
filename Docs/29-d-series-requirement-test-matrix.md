# 29. D-Series Requirement-to-Test Traceability Matrix

Scope: LLM locator-healing D-series (REQ-HEAL-01..18). Source test files:

- `tests/test_element_signature.py`
- `tests/test_element_signature_functional.py`
- `tests/test_metadata_lookup.py`
- `tests/test_locator_proposal.py`
- `tests/test_locator_healing_bridge.py`
- `tests/test_locator_healing_bridge_functional.py`
- `tests/test_mcp_server.py` (metadata_lookup tests only)
- `packages/browser/tests/healing-probe.spec.ts`
- `packages/browser/tests/browser-worker.spec.ts` (locator probe tests only)
- `packages/browser/tests/live-healing-cli.spec.ts`

Classification: **unit** = fakes/stubs/tmp_path fixtures. **functional** = real PostgreSQL, real
Playwright page/browser, real source-project files on disk, or a real cross-language digest
computed in the other runtime. VERDICT = COVERED (>=1 unit AND >=1 functional) | UNIT_ONLY |
FUNCTIONAL_ONLY | NOT_IMPLEMENTED (no code, no tests) | GAP.

## Matrix

| REQ | Unit tests (file::name) | Functional tests (file::name) | VERDICT |
|---|---|---|---|
| REQ-HEAL-01 | `test_element_signature.py::test_build_signature_hashes_values_and_never_retains_raw`, `::test_raw_value_absent_from_model_dump_json`, `::test_salesforce_like_identifier_is_stored_only_as_digest`, `::test_save_then_lookup_round_trip`, `::test_save_upsert_keeps_latest_capture`, `::test_lookup_miss_returns_none`, `::test_lookup_by_obligation_returns_matches`, `::test_lookup_by_obligation_miss_returns_empty` | `test_element_signature_functional.py::test_signature_round_trips_through_postgresql`, `::test_latest_capture_wins_on_the_same_key`, `::test_secondary_obligation_index_returns_every_element`, `::test_missing_key_returns_none` | COVERED |
| REQ-HEAL-02 | `test_element_signature.py::test_driver_exception_raises_store_unavailable`, `::test_connection_factory_failure_raises_store_unavailable`, `::test_store_unavailable_is_not_a_signature_corrupt`, `::test_both_failure_types_are_element_signature_errors`, `::test_unreachable_store_raises_store_unavailable_and_never_signature_corrupt` | `test_element_signature_functional.py::test_save_against_an_unreachable_database_is_reported_not_degraded`, `::test_lookup_against_an_unreachable_database_is_reported_not_degraded`, `::test_no_sqlite_json_or_cache_artefact_is_written_when_the_store_is_down` (real psycopg connect to a released loopback port; asserts `error.value.__cause__` is `psycopg.OperationalError`; a `tmp_path` snapshot taken before and after every call proves no sqlite/json/cache fallback artefact was written) | COVERED |
| REQ-HEAL-03 | `test_element_signature.py::test_attrs_hashed_subset_rule_is_rejected`, `::test_attrs_hashed_rejects_text_shaped_value`, `::test_signature_sha256_mismatch_is_rejected`, `::test_signature_corrupt_is_not_a_store_unavailable`, `::test_both_failure_types_are_element_signature_errors`, `::test_a_short_row_is_refused_as_signature_corrupt_not_store_unavailable` | `test_element_signature_functional.py::test_a_tampered_structure_column_is_refused_on_read`, `::test_a_tampered_attrs_hashed_column_is_refused_on_read`, `::test_a_tampered_signature_digest_is_refused_on_read`, `::test_a_tampered_row_is_also_refused_through_the_obligation_index` (4 tamper tests against real PostgreSQL rows — structure, attrs_hashed, signature_sha256, and the same tamper reached via `lookup_by_obligation` — each now asserting the class `SignatureCorrupt`, not the base `ElementSignatureError`) | COVERED |
| REQ-HEAL-04 | `test_metadata_lookup.py::test_lookup_field_reports_identity_and_reference`, `::test_picklist_values_are_returned_with_labels_and_default`, `::test_absent_required_element_reads_as_not_required`, `::test_declared_required_is_preserved`, `::test_standard_field_without_metadata_is_reported_not_found`, `::test_unknown_object_is_reported_not_found`, `::test_unparseable_metadata_is_reported_rather_than_raised_raw`, `::test_wrong_root_element_is_rejected`, `::test_global_value_set_reports_the_name_without_inventing_values`; `test_mcp_server.py::test_metadata_lookup_tool_reads_the_configured_source_project`; `test_locator_healing_bridge.py::test_picklist_values_reach_the_model_as_the_field_type_detail` | `test_metadata_lookup.py::test_real_lookup_field_is_read_from_the_source_project`, `::test_real_picklist_field_returns_its_declared_values`, `::test_every_declared_field_in_the_source_project_parses` (all skipped unless the real Salesforce source project is checked out) | COVERED |
| REQ-HEAL-05 | `test_metadata_lookup.py::test_object_names_outside_the_api_name_grammar_are_rejected`, `::test_field_names_outside_the_api_name_grammar_are_rejected`, `::test_full_name_disagreeing_with_the_file_name_is_rejected`, `::test_oversized_metadata_is_refused`, `::test_missing_salesforce_root_is_reported`, `::test_a_declared_field_is_read_without_invoking_any_org_transport`, `::test_an_absent_field_is_reported_not_found_rather_than_described_from_the_org`, `::test_the_forbidden_transport_guard_fails_the_test_when_a_transport_is_invoked`; `test_mcp_server.py::test_metadata_lookup_tool_reports_codes_without_leaking_any_path`, `::test_metadata_lookup_tool_reports_an_unconfigured_source_project`, `::test_metadata_lookup_tool_answers_without_invoking_any_org_transport`, `::test_metadata_lookup_tool_reports_an_absent_field_without_describing_the_org` | `test_metadata_lookup.py::test_real_lookup_field_is_read_from_the_source_project`, `::test_every_declared_field_in_the_source_project_parses`, `::test_the_real_source_project_is_read_without_invoking_any_org_transport` (all skipped unless real source project present) | COVERED |
| REQ-HEAL-06 | `test_locator_proposal.py::test_context_without_candidates_is_refused_before_any_provider_call`, `::test_candidate_count_beyond_the_bound_is_refused`; `test_locator_healing_bridge.py::test_evidence_without_candidates_is_refused`; `live-healing-cli.spec.ts::"projects dom evidence through when the probe captured it"`, `::"leaves the metadata-tier projection shape unchanged"` | `healing-probe.spec.ts::"withholds dom evidence when the deterministic tier resolved the obligation"`, `::"withholds dom evidence when candidate capture is not requested"`, `::"captures a bounded sanitized candidate list when the locator is not found"`, `::"truncates a large candidate set without throwing"`, `::"captures dom evidence for an ambiguous deterministic candidate"`; `browser-worker.spec.ts::"locator probe pushes DOM evidence when the worker is asked to capture candidates"`, `::"locator probe withholds DOM evidence when candidate capture is not requested"` | COVERED |
| REQ-HEAL-07 | `test_locator_proposal.py::test_prompt_envelope_carries_no_record_value_or_person_name`, `::test_record_serializes_without_any_raw_org_value`; `test_locator_healing_bridge.py::test_no_raw_org_value_survives_the_join`, `::test_a_candidate_carrying_a_non_digest_attribute_value_is_refused` | `healing-probe.spec.ts::"never emits a raw attribute value anywhere in the report"`, `::"never emits accessible-name text and digests it to sixteen hex characters"`, `::"emits attribute names in clear text, sorted and deduplicated"`, `::"emits a structure skeleton free of ids, classes and text"`; `browser-worker.spec.ts::"pushed DOM evidence carries digests, never the record value or the person name"`; `test_locator_healing_bridge_functional.py::test_no_raw_org_value_survives_the_real_join` (real PostgreSQL signature joined to real source-project metadata; serialized context checked for the absence of the raw record id / person name and the presence of both digests) | COVERED |
| REQ-HEAL-08 | `test_element_signature.py::test_attribute_digests_match_the_typescript_worker_byte_for_byte`; `test_locator_healing_bridge.py::test_the_two_languages_agree_so_signature_and_candidate_digests_match` | `browser-worker.spec.ts::"pushed DOM evidence carries digests, never the record value or the person name"` (asserts the TS-computed digest equals the Python-pinned `RECORD_ID_DIGEST`) | COVERED |
| REQ-HEAL-09 | `test_locator_proposal.py::test_well_formed_proposal_is_accepted`, `::test_non_json_response_is_rejected`, `::test_ordinal_outside_the_pushed_candidate_set_is_rejected`, `::test_unexpected_response_key_is_rejected` (rejects a `selector` key), `::test_duplicate_json_keys_are_rejected`, `::test_the_prompt_handed_to_the_provider_contains_the_candidate_ordinals` | none | UNIT_ONLY |
| REQ-HEAL-10 | `test_locator_proposal.py::test_citation_that_was_never_supplied_is_rejected`, `::test_proposal_without_any_citation_is_rejected` | none | UNIT_ONLY |
| REQ-HEAL-11 | `test_locator_proposal.py::test_context_carrying_a_machine_path_is_refused` | none | UNIT_ONLY |
| REQ-HEAL-12 | `test_locator_proposal.py::test_rejected_proposal_is_recorded_rather_than_raised` | none | UNIT_ONLY |
| REQ-HEAL-13 | `test_locator_healing_bridge.py::test_context_joins_worker_candidates_with_the_stored_signature`, `::test_a_signature_for_a_different_field_is_refused`, `::test_metadata_for_a_different_field_is_refused` | `test_locator_healing_bridge_functional.py::test_a_stored_signature_for_another_field_is_refused_against_real_metadata`, `::test_real_metadata_for_another_field_is_refused_against_the_stored_signature` (signature read back from real PostgreSQL joined against `FieldMetadata` parsed from the real configured Salesforce source project; both mismatch directions — stored signature vs. mismatched metadata, and real metadata vs. mismatched stored signature — isolated and each asserted against its own error code, `SIGNATURE_IDENTITY_MISMATCH` / `METADATA_IDENTITY_MISMATCH`) | COVERED |
| REQ-HEAL-14 | `live-healing-cli.spec.ts::"projects dom evidence through when the probe captured it"` (tiers=["metadata","llm"] -> `modelDiscoveryRequested: true`), `::"leaves the metadata-tier projection shape unchanged"` (tiers=["metadata"] -> `modelDiscoveryRequested: false`) | `browser-worker.spec.ts::"gate open: deterministic abstention on a missing element pushes DOM evidence"`, `::"gate shut: the deterministic tier resolving the element withholds DOM evidence"` (core case: the deterministic tier genuinely resolves the obligation against a real Playwright page and `domEvidence` stays absent even though `captureCandidates=true`), `::"gate shut: a resolved element with capture disabled still withholds DOM evidence"`, `::"gate open: an ambiguous semantic identity abstains and pushes DOM evidence"` (4 real-Playwright tests; every negative "evidence absent" assertion is preceded by a liveness assertion that the probe actually ran, so an unrun probe cannot be mistaken for a withheld-evidence pass) | COVERED |
| REQ-HEAL-15 | `live-healing-cli.spec.ts::"leaves the metadata-tier projection shape unchanged"` | `healing-probe.spec.ts::"default invocation reproduces the pre-change report shape exactly"` | COVERED |
| REQ-HEAL-16 | none | none | NOT_IMPLEMENTED |
| REQ-HEAL-17 | none | none | NOT_IMPLEMENTED |
| REQ-HEAL-18 | none | none | NOT_IMPLEMENTED |

Totals: 18 requirements. COVERED 11. UNIT_ONLY 4. FUNCTIONAL_ONLY 0. NOT_IMPLEMENTED 3. GAP 0.

New requirements this pass (REQ-HEAL-16..18), no code and no tests yet:

- REQ-HEAL-16 — a `change_delta` of `DELETE` on an entity must refuse healing; the failing test
  stays failing rather than being healed over.
- REQ-HEAL-17 — the agent pulls context via tools at the point it needs it; there is no fixed
  pre-assembled payload handed to it up front.
- REQ-HEAL-18 — the evidence graph (git-diff derived) is the source of change information for
  healing; the Salesforce application knowledge graph (`knowledge/application-graph.json`) is
  retired as a healing input.

## Open gaps

REQ-HEAL-01..15 all carry at least unit coverage; four (09, 10, 11, 12) remain UNIT_ONLY. Each
blocker below is the real reason no functional test exists, not a restatement of "more tests
needed":

- REQ-HEAL-09 (model cannot express a selector; ordinal only) — blocked by the agent-loop
  redesign (REQ-HEAL-17: the agent will pull context via tools instead of receiving one
  pre-assembled payload). The ordinal-only response contract itself survives that redesign
  unchanged; what blocks a functional test is that today's `_Provider` call site is being
  replaced, not that the contract is wrong.
- REQ-HEAL-10 (citation must resolve to supplied evidence) — blocked: `available_references()`
  currently exposes positional `edge:{index}` references. Once evidence is pulled by the agent
  tool-by-tool rather than assembled into one ordered payload, there is no fixed ordering left to
  index into — references must become stable entity ids before a functional citation test can be
  written against the real pull path.
- REQ-HEAL-11 (context screened for secret/machine-path text before the provider call) —
  blocked: screening today is one sweep over a pre-assembled payload. It must move to a per-tool
  boundary (each pulled tool result screened as it arrives) before a functional test can prove
  screening happens ahead of an actual outbound call under the tool-pull model.
- REQ-HEAL-12 (rejected proposal recorded with INVALID_RESPONSE, never raised) — blocked by
  credentials, not by design: `OPENAI_API_KEY` in this environment returns 401, so no real
  provider rejection can currently be exercised end-to-end. This is a credentials gap, not a
  design blocker — the moment a live key is available the existing fake-provider test converts to
  a functional test unchanged.

No requirement is FUNCTIONAL_ONLY. REQ-HEAL-16, 17 and 18 are NOT_IMPLEMENTED — tracked above,
not listed here as gaps, because there is no implementation yet for a test to be a gap against.

## Orphan tests (no REQ-HEAL-01..18 mapping)

35 test functions in the sourced files exercise behavior outside the 18 listed requirements:

`tests/test_element_signature.py` (2):
`test_invalid_attribute_names_are_dropped_not_raised`, `test_signature_is_deterministic`.

`tests/test_element_signature_functional.py` (0): every functional test in this file is now
mapped to REQ-HEAL-01, 02 or 03 above.

`tests/test_metadata_lookup.py` (1): `test_serialized_output_uses_camel_case_aliases`.

`tests/test_locator_proposal.py` (6): `test_prompt_envelope_digest_is_deterministic_for_an_identical_context`,
`test_prompt_envelope_changes_when_the_candidate_set_changes`,
`test_rationale_containing_a_machine_path_is_rejected` (screens the model's *response* text; REQ-HEAL-11 covers only pre-call *context* screening),
`test_accepted_proposal_is_recorded_with_a_success_receipt`,
`test_confidence_below_the_floor_abstains`,
`test_provider_outage_abstains_without_a_response_digest`.

`tests/test_locator_healing_bridge.py` (2): `test_malformed_evidence_is_refused_rather_than_partially_read`,
`test_absent_metadata_and_signature_still_produce_a_usable_context`.

`tests/test_locator_healing_bridge_functional.py` (1): `test_matching_identity_joins_postgres_and_the_source_project`
— proves the real-PostgreSQL / real-source-project happy path (context builds and both digests
line up), which underlies REQ-HEAL-13's guard but does not itself test a refusal, so it is not
counted as that requirement's functional evidence.

`packages/browser/tests/healing-probe.spec.ts` (19) — the whole obligation-probe engine predating
the D3/T-DOM candidate-push section (baseline pass/fail, stale-hook rediscovery, decoy-hook
rejection, object-scoped action identity, disabled-action rejection, rerun stability, fail-closed
ambiguity, selector-injection/editability-claim rejection, the 5 "rejects extra `{level}` keys"
tests, the 2 "rejects duplicate `{identity}`" tests, malformed-container rejection, obligation-cap
parity, key-order/rename stability, and browser-failure propagation). None of these test the
signature store, metadata feed, or LLM proposal/gating machinery that REQ-HEAL-01..18 describe.

`packages/browser/tests/browser-worker.spec.ts` and `live-healing-cli.spec.ts`: no orphans among
the tests sourced (the rest of `browser-worker.spec.ts` — enrollment, session handoff, business
action — was out of scope per the task's "locator probe tests only" instruction and was not
evaluated).

## Requirements with zero tests

REQ-HEAL-16, REQ-HEAL-17 and REQ-HEAL-18 — expected: all three are NOT_IMPLEMENTED, added this
pass ahead of any code. Every REQ-HEAL-01..15 has at least one unit test in the sourced files.
