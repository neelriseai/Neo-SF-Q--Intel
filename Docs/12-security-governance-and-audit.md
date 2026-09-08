# L12 — Security, Governance and Audit

## Layer charter

| Field | Value |
|---|---|
| Version/date | 1.0 / 24 August 2026 |
| Source baseline | `salesforce-change-assurance-production-solution-design.md` v1.1 |
| Mission | Enforce identity, authorization, action policy, data protection, approvals and tamper-evident accountability across every layer |
| Owner | Security engineer |
| Scope | Cross-cutting control plane; distinct from Salesforce change-security analysis in L04 |

## Part A — Solution design

### Identity and roles

Enterprise OIDC/OAuth 2.1 with short-lived tokens. Roles: Viewer, Analyst, Operator, Approver, Admin. Optional attributes include project, environment, classification and action risk. Requester cannot self-approve privileged execution/risk acceptance by default.

### Action classes

| Action | Default |
|---|---|
| Read local Git/fixture/index; traverse graph | Auto |
| Generate isolated automation patch | Auto within repository/path/size policy |
| Compile/discover in sandbox | Allowlisted command policy |
| Read Salesforce metadata | Approved scope |
| Read-only SOQL | Query/field allowlist |
| Execute existing tests | Environment/role policy; approval as configured |
| Apply patch to feature branch | Human approval + expected-head check |
| Create test data | Sandbox-only approval |
| Deploy metadata/modify permissions | Disabled in pilot; two-person approval if enabled later |
| Production DML | Out of scope |

### Data protection

- TLS; encryption at rest and backups.
- Secrets in approved vault with rotation/revocation.
- Data minimization/redaction before model calls.
- Deny-by-default network egress.
- Project/tenant isolation and restricted evidence.
- Retention classes, deletion and legal hold defined before production onboarding.

### Generated-code sandbox

Allowlisted build commands, resource/time limits, deny-by-default network, no production credentials, bounded repository/paths/file counts and security/secret/dependency scan.

### Audit

Append actor, action, resource, policy decision, approval, correlation ID and chained event hashes. Audit is distinct from debug logs and retains security/release actions according to policy.

## Part B — Development notes

### Repository

```text
packages/policy/
packages/identity/
packages/approvals/
packages/audit/
architecture/threat-model.md
policies/security/
```

### Implementation order

1. Threat model/data classification.
2. Identity claims and role/attribute mapping.
3. Central authorization/policy decision port.
4. Approval lifecycle and separation of duties.
5. Audit chain and evidence access.
6. Sandbox/egress/secret controls.
7. Retention/deletion and incident response.

### Engineering rules

- Authorization is server-side per operation/resource.
- Never trust UI/MCP claims of role or approval.
- Downstream tokens are separately acquired/audience-bound.
- Retrieved source/record/document text is data, not instructions.
- Policy/role changes are versioned and audited.

## Part C — Testing and definition of done

### Tests

- Role/project/environment/classification authorization matrix.
- Requester self-approval and expired/replayed approval.
- OAuth issuer/audience/scope/resource binding.
- Secret/log/model-payload redaction.
- Prompt injection/tool abuse/forbidden command.
- Sandbox network/resource/path escapes.
- Audit chain tamper/gap detection.
- Retention/deletion/legal-hold behaviour.
- Credential rotation/revocation.

### Definition of done

- Every action and restricted read has an explicit policy decision.
- Privileged actions require correct scoped approval/separation of duties.
- No secrets/sensitive payloads leak to logs, artifacts or unapproved model calls.
- Generated code cannot escape the sandbox or access production credentials.
- Audit records correlate request, tools, approvals, policies and release decision.
- Threat/privacy/security review and required scans pass.
- Incident response and credential revocation are exercised.

### Integration handoff

- Publish role/action matrix, policy bundle and approval scopes.
- Provide authorization/audit test fixtures to all layers.
- Document data classifications, retention and incident contacts.
