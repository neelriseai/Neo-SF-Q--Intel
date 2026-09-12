# G4 return

```json
{"files":[{"path":"tests/test_metadata_lookup.py","action":"append","lines":66},{"path":"tests/test_mcp_server.py","action":"append","lines":56}],"tests":{"added":6,"passed":49,"failed":0},"redRunConfirmed":true,"guardSelfCheckAdded":true,"transportsPatched":["subprocess.run","neo_sf_q_intel.salesforce_cli.SalesforceCLI.org_status","neo_sf_q_intel.salesforce_cli.SalesforceCLI.rest_get"],"defectsFound":[],"deviations":[]}
```

## Notes

- `tests/test_metadata_lookup.py` gains `_forbid_org_transports` plus four tests: declared field
  read with transports forbidden, absent `Opportunity.Amount` reported `FIELD_METADATA_NOT_FOUND`
  with transports forbidden, the real configured source project read with transports forbidden
  (`requires_source_project`), and the guard self-check.
- `tests/test_mcp_server.py` gains its own `_forbid_org_transports` plus the two MCP-level tests,
  shaped after `test_salesforce_inspection_is_unavailable_without_any_transport`.
- Every test also asserts the real outcome (returned metadata / raised code), so no test passes
  merely because a stub was never reached.
- Red run (scratchpad, discarded): with the patch removed the self-check assertion fails, and a
  simulated fallback implementation that calls `subprocess.run` is caught by the guard.
- The real source project is present in this checkout, so the `requires_source_project` test ran
  rather than skipping.
