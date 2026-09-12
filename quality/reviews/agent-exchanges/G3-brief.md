# G3 brief (verbatim)

```
[REPO] C:\Users\neela\Documents\ChatGPT\Neo SF Q- Intel  [CHUNK] G3  [TIMEBOX] 25m
[ROLE] opus developer. [RULES] CRLF forbidden, normalise \r\n -> \n on every write. No git add/commit/push.
[FILES_OWNED] src/neo_sf_q_intel/element_signature.py · tests/test_element_signature.py · tests/test_element_signature_functional.py
 Do NOT touch packages/**, Docs/**, knowledge/**, any other src module.

[DEFECT] verified, from a functional test against real PostgreSQL.
 element_signature.py:65   class SignatureStoreUnavailable(ElementSignatureError)  code STORE_UNAVAILABLE
 element_signature.py:241,252,257,261,276  raise ElementSignatureError(..., code="SIGNATURE_CORRUPT")
 element_signature.py:370-372  `with self._session(): row = ...` then `return _signature_from_row(row)` OUTSIDE the session
 element_signature.py:386-388  same shape on lookup_by_obligation
 EFFECT: a tampered row surfaces as the BASE ElementSignatureError. A caller catching
 SignatureStoreUnavailable (the documented store failure type, and a SUBCLASS of the base) never
 catches it. Unit tests asserted only the `code` string, never the class, so they missed it.

[REJECTED_FIX] do NOT move the decode inside _session() to make corruption raise
 SignatureStoreUnavailable. Corruption and unavailability require OPPOSITE handling: unavailability
 is transient and may be retried or reported as degraded; corruption is a tamper signal that must
 never be retried and must never be silently healed. Filing a tamper event under a retryable type
 is worse than the current bug.

[REQUIRED_FIX]
 1 add `class SignatureCorrupt(ElementSignatureError)` with `code: str = "SIGNATURE_CORRUPT"`,
   a SIBLING of SignatureStoreUnavailable, not a subclass of it. Docstring must say why it is
   distinct: tamper is not transient and must not be retried.
 2 every SIGNATURE_CORRUPT raise site now raises SignatureCorrupt. The `code` string stays exactly
   "SIGNATURE_CORRUPT" — it is asserted by existing tests and by a cross-language contract.
 3 leave the decode OUTSIDE _session(). That placement is correct: a decode failure is not a
   store-session failure.
 4 module docstring / class docstrings updated so the catchable contract is explicit:
   callers that must not proceed catch ElementSignatureError (the base);
   callers distinguishing transient from tamper catch SignatureStoreUnavailable vs SignatureCorrupt.

[STATE_TRANSITIONS] tests to add or tighten. EVERY ONE asserts the CLASS, not only the code.
 1 tampered `structure` column      -> lookup() raises SignatureCorrupt · code SIGNATURE_CORRUPT
 2 tampered `attrs_hashed` column   -> same
 3 tampered `signature_sha256`      -> same
 4 tamper reached via lookup_by_obligation -> same (proves the secondary read path)
 5 SignatureCorrupt is NOT an instance of SignatureStoreUnavailable   <- the regression guard
 6 SignatureStoreUnavailable is NOT an instance of SignatureCorrupt
 7 both ARE instances of ElementSignatureError
 8 unreachable store -> SignatureStoreUnavailable, and NOT SignatureCorrupt
 Rows 1-4 belong in the FUNCTIONAL file (real PostgreSQL, existing opt-in gate
 NEO_SIGNATURE_FUNCTIONAL=1). Rows 5-8 may be unit tests.
 G1 already wrote functional tamper tests asserting ElementSignatureError — TIGHTEN those to
 SignatureCorrupt rather than duplicating them.

[ANTI_VACUOUS] before asserting a tamper refusal, assert the row was really written AND the
 mutation really applied (row count / changed value read back). A test that passes when the
 mutation silently no-opped is a defect.

[VERIFY]
 .venv/Scripts/python -m pytest tests/test_element_signature.py -q
 NEO_SIGNATURE_FUNCTIONAL=1 .venv/Scripts/python -m pytest tests/test_element_signature_functional.py -q
 .venv/Scripts/python -m pytest tests/test_locator_healing_bridge.py tests/test_locator_proposal.py tests/test_metadata_lookup.py -q   # no regression
 .venv/Scripts/python -m ruff check src tests ; .venv/Scripts/python -m ruff format --check src/neo_sf_q_intel/element_signature.py
 Confirm the functional file still SKIPS without the env var.
 Grep the repo for existing `except SignatureStoreUnavailable` / `except ElementSignatureError`
 call sites and report whether any of them change behaviour under this fix.

[RETENTION] write quality/reviews/agent-exchanges/G3-brief.md (this brief verbatim, fenced) and
 G3-return.md (your return JSON, fenced). LF only.

[RETURN] minified JSON only:
 {"files":[{"path","action","lines"}],"tests":{"added":N,"tightened":N,"passed":N,"skipped":N,"failed":N},
  "redRunConfirmed":bool,"callSitesAffected":[{"file":"","line":N,"behaviourChange":""}],
  "defectsFound":[{"sev","desc","evidence"}],"deviations":[{"from","why"}]}
```
