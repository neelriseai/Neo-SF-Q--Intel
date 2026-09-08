from __future__ import annotations

from neo_sf_q_intel.governance_policy import GovernancePolicy


def main() -> None:
    policy = GovernancePolicy.load()
    print(
        f"Governance policy {policy.schema_version} is valid: "
        f"{len(policy.metrics)} metrics, {len(policy.controls)} controls"
    )


if __name__ == "__main__":
    main()
