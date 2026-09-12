# Delegated development roles

```
[ROLE_MATRIX]
 opus-5       : design · review · challenge · triage decisions · prompt authoring · ZERO code
 opus-4.6/7/8 : all feature code and all test code
 sonnet-4.6   : tool runs · test runs · lint · status checks · polling · documentation
[LOOP] split -> tests first (unit AND functional) -> requirement matrix -> build -> collect ALL
       issues -> severity -> priority -> per-bug time slot by complexity -> fix in order -> log
[COVERAGE] coverage means the requirement matrix, never the test count
[ACCEPT] no chunk accepted on "tests pass"; opus-5 challenges thin-layer, over-engineering,
         missing functional cover, unmapped requirement
[LOG] quality/reviews/ai-communication-compliance-log.md updated at EVERY chunk boundary;
      every number tagged MEAS or EST
```
