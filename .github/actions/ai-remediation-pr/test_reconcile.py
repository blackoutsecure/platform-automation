"""Tests for structured recommendation identity and PR reconciliation helpers."""

from __future__ import annotations

import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("reconcile.py")
spec = importlib.util.spec_from_file_location("reconcile", MODULE_PATH)
assert spec and spec.loader
reconcile = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reconcile)


def test_structured_marker_round_trips_keys_with_spaces():
    key = "ps010:.github/workflows/ci.yml"
    marker = reconcile.structured_marker(key)
    indexed = reconcile.index_open_prs([{"body": marker}])
    assert indexed[key] == {"body": marker}


def test_open_pr_index_supports_legacy_marker():
    pr = {"body": reconcile.MARKER.format(key="legacy-key")}
    assert reconcile.index_open_prs([pr])["legacy-key"] == pr


def test_open_pr_index_deduplicates_by_stable_key():
    first = {"body": reconcile.structured_marker("same"), "number": 1}
    second = {"body": reconcile.structured_marker("same"), "number": 2}
    assert reconcile.index_open_prs([first, second])["same"]["number"] == 2


def test_normalize_accepts_wrapped_payload_and_defaults_patch_status():
    items = reconcile.normalize_recommendations({
        "recommendations": [{
            "finding_key": "ps010-ci",
            "recommendation": "Add permissions",
        }],
    })
    assert items[0]["patch_status"] == "unavailable"


def test_normalize_rejects_missing_stable_identity():
    try:
        reconcile.normalize_recommendations([{"recommendation": "Review"}])
    except SystemExit:
        pass
    else:
        raise AssertionError("missing finding_key must be rejected")


def test_only_validated_nonempty_patches_are_eligible():
    base = {"finding_key": "key", "recommendation": "Review"}
    assert not reconcile.has_validated_patch({**base, "patch": "diff", "patch_status": "unavailable"})
    assert not reconcile.has_validated_patch({**base, "patch_status": "validated"})
    assert reconcile.has_validated_patch({**base, "patch": "diff", "patch_status": "validated"})
