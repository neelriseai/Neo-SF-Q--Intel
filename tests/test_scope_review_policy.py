import json

from scripts.quality import check_genericity


def _policy() -> dict:
    return {"scopeReviewManifestPath": "quality/reviews/current-scope-review.json"}


def _manifest(reviewed_paths: list[str]) -> dict:
    return {
        "schemaVersion": "1.0.0",
        "reviewId": "review-1",
        "status": "APPROVED",
        "reviewedPaths": reviewed_paths,
        "reviewers": ["reviewer-a", "reviewer-b"],
        "findings": [],
        "statusDowngrades": [],
    }


def test_unrelated_review_file_cannot_satisfy_scope_review() -> None:
    findings, _ = check_genericity._scope_review_findings(
        _policy(),
        {"config/governance-policy.json"},
        {"quality/reviews/unrelated.md"},
    )

    assert [item.code for item in findings] == ["MISSING_SCOPE_REVIEW"]


def test_manifest_must_name_every_changed_controlled_path(tmp_path, monkeypatch) -> None:
    manifest_path = tmp_path / "quality" / "reviews" / "current-scope-review.json"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(
        json.dumps(_manifest(["config/another-policy.json"])), encoding="utf-8"
    )
    monkeypatch.setattr(check_genericity, "ROOT", tmp_path)

    findings, _ = check_genericity._scope_review_findings(
        _policy(),
        {"config/governance-policy.json"},
        {"quality/reviews/current-scope-review.json"},
    )

    assert "UNREVIEWED_POLICY_CHANGE" in {item.code for item in findings}


def test_approved_two_reviewer_manifest_covers_exact_change(tmp_path, monkeypatch) -> None:
    controlled_path = "config/governance-policy.json"
    manifest_path = tmp_path / "quality" / "reviews" / "current-scope-review.json"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(json.dumps(_manifest([controlled_path])), encoding="utf-8")
    monkeypatch.setattr(check_genericity, "ROOT", tmp_path)

    findings, _ = check_genericity._scope_review_findings(
        _policy(),
        {controlled_path},
        {"quality/reviews/current-scope-review.json"},
    )

    assert findings == []


def test_reviewer_string_cannot_impersonate_two_reviewers(tmp_path, monkeypatch) -> None:
    controlled_path = "config/governance-policy.json"
    manifest = _manifest([controlled_path])
    manifest["reviewers"] = "ab"
    manifest_path = tmp_path / "quality" / "reviews" / "current-scope-review.json"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(check_genericity, "ROOT", tmp_path)

    findings, _ = check_genericity._scope_review_findings(
        _policy(),
        {controlled_path},
        {"quality/reviews/current-scope-review.json"},
    )

    assert [item.code for item in findings] == ["INVALID_SCOPE_REVIEW"]
