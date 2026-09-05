#!/usr/bin/env python3
"""Validate hub runtime, managed caller, branch, and documentation contracts."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parent.parent


def workflow_input_names(body: str) -> set[str]:
    inputs = body.split("    inputs:\n", 1)[1].split("    secrets:\n", 1)[0]
    return set(re.findall(r"^      ([a-z][a-z0-9_]+):", inputs, re.MULTILINE))


def caller_input_names(body: str, workflow_name: str) -> set[str]:
    call_pattern = re.compile(
        r"^    uses: (?:\./|blackoutsecure/bos-automation-hub/)"
        rf"\.github/workflows/{re.escape(workflow_name)}(?:@\w+)?$",
        re.MULTILINE,
    )
    match = call_pattern.search(body)
    assert match is not None, workflow_name
    call = body[match.end() :]
    # Stop at the job-level `secrets:` key, whether it's an inline value
    # (`secrets: inherit`, the common case) or a nested mapping — both start
    # a line with exactly 4 spaces of indent then `secrets:`. Without this,
    # a caller with more than one job invoking the same reusable workflow
    # (e.g. a dev/main split) would bleed into the next job's `permissions:`
    # block, since a plain `"    secrets:\n"` literal never matches
    # `secrets: inherit`.
    inputs = re.split(r"\n    secrets:", call.split("    with:\n", 1)[1], maxsplit=1)[0]
    return set(re.findall(r"^      ([a-z][a-z0-9_]+):", inputs, re.MULTILINE))


def assert_first_party_pin(body: str, action: str) -> None:
    """First-party actions must be pinned to a SHA carrying its version tag.

    The SHA itself is deliberately not asserted: `sync-action-pins.yml`
    rewrites it whenever upstream publishes a newer tag. What matters to the
    contract is the shape — an immutable 40-hex commit plus the `# vX.Y.Z`
    provenance comment — so a bump never silently degrades to a branch ref.
    """
    pattern = re.escape(f"uses: {action}@") + r"[0-9a-f]{40} # v\d+\.\d+\.\d+"
    assert re.search(pattern, body), action


def assert_markdown_links_exist(path: Path) -> None:
    missing = set()
    for raw_target in re.findall(r"\[[^]]+\]\(([^)]+)\)", path.read_text()):
        target = unquote(raw_target.split("#", 1)[0].strip())
        if not target or "://" in target or target.startswith(("#", "mailto:")):
            continue
        if not (path.parent / target).resolve().exists():
            missing.add(raw_target)
    assert not missing, {str(path.relative_to(ROOT)): sorted(missing)}


def run_universal_config(config: object) -> subprocess.CompletedProcess[str]:
    return run_universal_config_raw(json.dumps(config))


def run_universal_config_raw(raw_text: str) -> subprocess.CompletedProcess[str]:
    action = (
        ROOT / ".github/actions/universal-config/action.yml"
    ).read_text(encoding="utf-8")
    script = action.split("        python3 - <<'PY'\n", 1)[1].split(
        "\n        PY", 1
    )[0]
    with tempfile.TemporaryDirectory() as temp_dir:
        temp = Path(temp_dir)
        config_path = temp / ".github" / "bos-universal-config.json"
        config_path.parent.mkdir()
        config_path.write_text(raw_text)
        env = os.environ | {
            "CONFIG_PATH": ".github/bos-universal-config.json",
            "ALLOW_MISSING": "false",
            "GITHUB_OUTPUT": str(temp / "output"),
            "GITHUB_STEP_SUMMARY": str(temp / "summary"),
            "SUMMARY_CONTEXT": "Universal config contract test",
        }
        result = subprocess.run(
            [sys.executable, "-c", textwrap.dedent(script)],
            cwd=temp,
            env=env,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        if result.returncode == 0:
            result.stdout += (temp / "output").read_text(encoding="utf-8")
        return result


def main() -> None:
    normalized = run_universal_config(
        {
            "marketplace": {
                "allowlist_paths": ["action.yml", "README.md"],
                "blocked_paths": [".github/workflows/", "test/"],
                "required_paths": [],
                "extra_sync_paths": ["NOTICE"],
                "repo_metadata": {
                    "enable": True,
                    "topics_fallback": "github-actions security",
                },
            }
        }
    )
    assert normalized.returncode == 0, normalized.stderr
    cfg_output = normalized.stdout.split("cfg<<__BOS_EOF__\n", 1)[1].split(
        "\n__BOS_EOF__", 1
    )[0]
    normalized_cfg = json.loads(cfg_output)
    assert normalized_cfg["security_scan"] == {
        "enable": True,
        "fail_on": "fail",
        "blocks_release": True,
        "enable_kit_composite": True,
        "enable_posture": True,
        "enable_scanners": True,
        "enable_upload": True,
        "codeql_languages": "",
        "codeql_queries": "security-and-quality",
        "codeql_runs_on": "",
        "use_advanced_pat": True,
    }
    security_disabled = run_universal_config(
        {"security_scan": {"enable": False, "enable_upload": False, "use_advanced_pat": False}}
    )
    assert security_disabled.returncode == 0, security_disabled.stderr
    disabled_cfg = json.loads(
        security_disabled.stdout.split("cfg<<__BOS_EOF__\n", 1)[1].split(
            "\n__BOS_EOF__", 1
        )[0]
    )
    assert disabled_cfg["security_scan"]["enable"] is False
    assert disabled_cfg["security_scan"]["enable_upload"] is False
    assert disabled_cfg["security_scan"]["use_advanced_pat"] is False
    marketplace = json.loads(cfg_output)["marketplace"]
    assert marketplace["allowlist_paths"] == "action.yml\nREADME.md"
    assert marketplace["blocked_paths"] == ".github/workflows/\ntest/"
    assert marketplace["required_paths"] == ""
    assert marketplace["extra_sync_paths"] == "NOTICE"
    assert marketplace["repo_metadata"] == {
        "enable": True,
        "topics_fallback": "github-actions security",
    }
    malformed = run_universal_config(
        {"marketplace": {"allowlist_paths": ["action.yml", 3]}}
    )
    assert malformed.returncode == 1
    assert "marketplace.allowlist_paths[1] must be a non-empty string" in malformed.stderr
    legacy_paths = run_universal_config(
        {"marketplace": {"allowlist_paths": "action.yml\nREADME.md"}}
    )
    assert legacy_paths.returncode == 1
    assert "marketplace.allowlist_paths must be an array of strings" in legacy_paths.stderr

    # Invalid JSON syntax must fail cleanly with a line/column-annotated
    # error, not a raw Python traceback.
    invalid_json = run_universal_config_raw('{"gate": {"enable_lint": true,}}')
    assert invalid_json.returncode == 1
    assert "Invalid JSON" in invalid_json.stderr
    assert "line" in invalid_json.stderr and "column" in invalid_json.stderr
    assert "Traceback (most recent call last)" not in invalid_json.stderr

    def cfg_from(result: subprocess.CompletedProcess[str]) -> dict:
        assert result.returncode == 0, result.stderr
        return json.loads(
            result.stdout.split("cfg<<__BOS_EOF__\n", 1)[1].split(
                "\n__BOS_EOF__", 1
            )[0]
        )

    action_test_defaults = run_universal_config({})
    assert action_test_defaults.returncode == 0, action_test_defaults.stderr
    action_test_output = action_test_defaults.stdout.split("action_test=", 1)[1].split(
        "\n", 1
    )[0]
    assert json.loads(action_test_output) == {
        "python_versions": ["3.11"],
        "os_matrix": ["ubuntu-latest"],
        "python_packages": ["pytest>=8.0", "ruff>=0.6", "PyYAML>=6.0"],
        "pytest_args": "-q",
        "enable_smoke_test": False,
        "smoke_trigger": "push-dev",
        "smoke_test_config": {},
        "smoke_test_output_name": "version",
        "timeout_pytest": 10,
        "timeout_smoke": 5,
        "enable_ai_failure_summary": True,
        "ai_provider": "auto",
        "ai_model": "auto",
    }

    release_validation_output = action_test_defaults.stdout.split(
        "release_validation=", 1
    )[1].split("\n", 1)[0]
    assert json.loads(release_validation_output) == {
        "enabled": True,
        "enforce": "fail",
        "run_node": True,
        "run_python": True,
        "run_custom": True,
        "verify_clean_tree": True,
        "require_lockfile": True,
        "version_match": "warn",
        "required_paths": ["README.md", "LICENSE"],
        "custom_hook": ".github/scripts/release-validation.sh",
        "custom_commands": [],
    }
    bad_release_policy = run_universal_config(
        {"release_validation": {"enforce": "sometimes"}}
    )
    assert bad_release_policy.returncode == 1
    assert "release_validation.enforce must be" in bad_release_policy.stderr

    # ── organization section ──────────────────────────────────────
    # Runner topology and report policy are data, so an empty config
    # must still yield a complete, directly usable organization block.
    def org_from(result: subprocess.CompletedProcess[str]) -> dict:
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout.split("organization=", 1)[1].split("\n", 1)[0])

    org_defaults = org_from(run_universal_config({}))
    assert org_defaults["runners"] == {
        "default": "ubuntu-latest",
        "x64": "ubuntu-latest",
        "arm64": "ubuntu-latest",
    }
    assert org_defaults["reporting"] == {
        "enable_job_summary": True,
        "enable_annotations": True,
        "enable_html": True,
        "enable_pdf": False,
        "html_path": "blackout-secure-report.html",
        "pdf_path": "blackout-secure-report.pdf",
        "artifact_name": "blackout-secure-audit-report",
        "title_prefix": "Blackout Secure",
        "fail_on": "fail",
    }
    assert org_defaults["defaults"] == {"timeout_minutes": 30}
    assert set(org_defaults["workflows"]) == {
        "security", "sync", "launchpad", "marketplace", "action_test", "release",
    }
    assert all(
        entry == {"runs_on": "ubuntu-latest", "timeout_minutes": 30}
        for entry in org_defaults["workflows"].values()
    )

    # The summary only lists fields relevant to enabled stages. Configured
    # values are visibly marked as passing, while active stages cannot hide
    # mandatory unset values behind a neutral placeholder.
    summary = (ROOT / ".github/actions/universal-config/action.yml").read_text()
    assert "Required applicable configuration is unset" in summary
    assert "✅ `" in summary
    assert "❌ **unset (required)**" in summary
    assert "summary_context" in summary
    assert "Universal config snapshot\" + (f\" - {summary_context}\"" in summary

    missing_cloudflare_project = run_universal_config(
        {"stages": {"cloudflare_pages": True}}
    )
    assert missing_cloudflare_project.returncode == 1
    assert "Required applicable configuration is unset: cloudflare.project_name" in missing_cloudflare_project.stderr

    configured_cloudflare = run_universal_config(
        {"stages": {"cloudflare_pages": True}, "cloudflare": {"project_name": "site"}}
    )
    assert configured_cloudflare.returncode == 0, configured_cloudflare.stderr
    assert "cloudflare_project: site" in configured_cloudflare.stdout

    # A per-workflow override wins over the org default; unset workflows
    # keep inheriting it.
    org_override = org_from(
        run_universal_config(
            {
                "organization": {
                    "runners": {"default": "ubuntu-24.04", "arm64": "ubuntu-24.04-arm"},
                    "defaults": {"timeout_minutes": 15},
                    "reporting": {"fail_on": "never", "enable_annotations": False},
                    "workflows": {"security": {"runs_on": ["self-hosted", "Linux"], "timeout_minutes": 45}},
                }
            }
        )
    )
    assert org_override["runners"]["arm64"] == "ubuntu-24.04-arm"
    assert org_override["runners"]["x64"] == "ubuntu-24.04"
    assert org_override["reporting"]["fail_on"] == "never"
    assert org_override["reporting"]["enable_annotations"] is False
    assert org_override["workflows"]["security"] == {
        "runs_on": ["self-hosted", "Linux"],
        "timeout_minutes": 45,
    }
    assert org_override["workflows"]["sync"] == {
        "runs_on": "ubuntu-24.04",
        "timeout_minutes": 15,
    }

    # A JSON-array label string resolves to a real array so callers can
    # feed the value straight into `runs-on:` without a startsWith guard.
    org_json_labels = org_from(
        run_universal_config(
            {"organization": {"runners": {"default": '["self-hosted","X64"]'}}}
        )
    )
    assert org_json_labels["runners"]["default"] == ["self-hosted", "X64"]

    bad_fail_on = run_universal_config(
        {"organization": {"reporting": {"fail_on": "sometimes"}}}
    )
    assert bad_fail_on.returncode == 1
    assert "organization.reporting.fail_on must be" in bad_fail_on.stderr

    # Grouped-section authoring layout hoists to the flat keys every
    # downstream kicker/normalizer reads — both layouts must resolve
    # identically, and a flat key always wins over its section alias.
    grouped = cfg_from(
        run_universal_config(
            {
                "security": {"enable_lint": True, "enable_shell_lint": True},
                "launchpad": {
                    "upstream": {"repo": "owner/grouped"},
                    "docker": {"image_name": "grouped-image"},
                },
                "marketplace": {"enabled": True},
            }
        )
    )
    assert grouped["gate"] == {
        "enable_lint": True,
        "enable_shell_lint": True,
    }
    assert grouped["upstream"]["repo"] == "owner/grouped"
    assert grouped["docker"]["image_name"] == "grouped-image"
    assert grouped["marketplace"]["enabled"] is True

    flat_wins = cfg_from(
        run_universal_config(
            {
                "gate": {"enable_lint": False},
                "security": {"enable_lint": True},
            }
        )
    )
    assert flat_wins["gate"] == {"enable_lint": False}

    # "general" is a catch-all for keys owned by neither of the four named
    # services — every key it holds is hoisted as-is (no fixed allowlist),
    # so a brand-new standalone service's block lands there first.
    general = cfg_from(
        run_universal_config(
            {
                "general": {
                    "action_test": {"python_versions": ["3.12"]},
                    "upstream": {"repo": "should-not-win"},
                },
                "upstream": {"repo": "owner/flat-wins"},
            }
        )
    )
    assert general["action_test"] == {"python_versions": ["3.12"]}
    assert general["upstream"]["repo"] == "owner/flat-wins"

    workflow = (ROOT / ".github/workflows/bos-universal-gatekeeper.yml").read_text()
    action_test_workflow = (
        ROOT / ".github/workflows/bos-universal-action-test.yml"
    ).read_text()
    assert "source: ${{ fromJSON(needs.resolve-config.outputs.test).smoke_test_config.source || '' }}" in action_test_workflow
    assert "package_name: ${{ fromJSON(needs.resolve-config.outputs.test).smoke_test_config.package_name || '' }}" in action_test_workflow
    kicker = (
        ROOT / "sync-files/workflows/bos-universal-gatekeeper-kicker.yml"
    ).read_text()

    declared = workflow_input_names(workflow)
    forwarded = caller_input_names(kicker, "bos-universal-gatekeeper.yml")
    assert declared == forwarded, {
        "missing": sorted(declared - forwarded),
        "unknown": sorted(forwarded - declared),
    }
    assert "config_path: .github/bos-universal-config.json" in kicker
    assert "trusted_app_slugs: ${{ vars.GATEWALL_APP_SLUG }}" in kicker
    assert "config_path: .github/bos-universal-config.json" in action_test_workflow

    monitor_workflow = (ROOT / ".github/workflows/monitor-upstream-release.yml").read_text()
    assert_first_party_pin(monitor_workflow, "blackoutsecure/bos-upstream-watcher")
    assert "config_path: .github/bos-universal-config.json" in monitor_workflow
    assert "global_config_path: hub-config/sync-files/config/upstream-watcher-global-config.json" in monitor_workflow
    assert re.search(r'use_global_config:\s+["\']auto["\']', monitor_workflow)
    assert "upstream_update_type:" in monitor_workflow
    assert "upstream_ai_status:" in monitor_workflow

    assert "managed-files-guard:" not in kicker
    assert "bos-universal-sync.yml@main" not in workflow
    assert kicker.count(
        "enable_security_scan: ${{ needs.parse-config.outputs.run_security == 'true'"
    ) == 2, "managed Universal callers must enable security for full and security-only routes"
    assert kicker.count("security_scan_enable_kit_composite: ${{ toJSON(") == 2
    assert kicker.count("security_scan_use_advanced_pat: ${{ toJSON(") == 2

    # Push trigger covers both long-lived branches: the job graph is already
    # dual-branch (`release-dev` / `release-main` gate on `github.ref_name`),
    # so a `main`-only trigger would leave the dev route unreachable on push.
    assert "branches: [dev, main]" in kicker, (
        "managed kicker must trigger on both dev and main"
    )
    # `on:` is parsed before any job runs and cannot read a config file, so a
    # single file-mode template cannot carry a path list that fits every repo
    # shape. Relevance is decided by the `changed-paths` job instead.
    assert not re.search(r"^    paths:$", kicker, re.MULTILINE), (
        "managed kicker must not reintroduce a static push path filter; "
        "per-repo relevance belongs in triggers.push_paths"
    )
    assert "\n  changed-paths:\n" in kicker
    assert "needs: [authorize, changed-paths]" in kicker, (
        "the path gate must run before sync-check acquires contents: write"
    )
    assert "if: needs.changed-paths.outputs.should_run == 'true'" in kicker
    assert "push_paths" in kicker
    # Absent config, absent key, and an unusable diff base must all fail open.
    assert kicker.count('should_run=true" >> "${GITHUB_OUTPUT}"') >= 4

    promote = (ROOT / ".github/workflows/release-promote.yml").read_text()
    dependabot_input = promote.split("      include_dependabot_config:\n", 1)[
        1
    ].split("      include_github_metadata:\n", 1)[0]
    assert "        default: true\n" in dependabot_input


    gate_workflow = (ROOT / ".github/workflows/bos-universal-security.yml").read_text()
    gate_declared = workflow_input_names(gate_workflow)
    assert "  workflow_dispatch:" not in gate_workflow
    assert not (ROOT / ".github/workflows/bos-universal-security-kicker.yml").exists()
    assert "[RUNTIME] Blackout Secure Universal Security" in gate_workflow
    assert '"python_packages": ["ruff>=0.6", "pytest>=8.0", "PyYAML>=6.0"]' in gate_workflow
    assert "name: Security summary" in gate_workflow
    assert "kit_version:" not in gate_workflow
    assert "code_scanning_kit_version:" not in gate_workflow
    assert "marketplace-action-ci.yml@main" not in gate_workflow
    assert "bos-universal-marketplace.yml@main" not in gate_workflow
    assert "enable_marketplace_ci:" not in gate_workflow
    assert "enable_baseline:" not in gate_workflow
    assert "needs.resolve-config.outputs.gate" in gate_workflow
    assert "uses: ./hub-runtime/.github/actions/universal-config" in gate_workflow
    assert "inputs.hub_ref != 'auto' && inputs.hub_ref" in gate_workflow
    assert "github.event_name == 'pull_request' && github.base_ref" in gate_workflow
    assert "code_scan_fail_on:" in gate_workflow
    assert "code_scan_http_timeout:" in gate_workflow
    assert (
        "fail_on: ${{ fromJSON(needs.resolve-config.outputs.gate).code_scan_fail_on }}"
        in gate_workflow
    )
    assert (
        "http_timeout: ${{ fromJSON(needs.resolve-config.outputs.gate).code_scan_http_timeout }}"
        in gate_workflow
    )
    assert "steps.security-app.outputs.token || secrets.SCANNING_PAT" in gate_workflow
    assert "vars.GATEWALL_APP_ID" in gate_workflow

    readme = (ROOT / "README.md").read_text()
    readme_header_action = (
        ROOT / ".github/actions/check-readme-header/action.yml"
    ).read_text()
    assert "enable_baseline" not in readme
    assert "## Managed file sync" in readme
    assert "The reusable workflow never self-triggers" in readme
    assert "### Elevated posture scanning (`GATEWALL_APP`)" in readme
    assert "security_scan.use_advanced_pat" in readme
    assert "bos-launchpad-release.yml" not in readme_header_action
    assert "bos-universal-gatekeeper-kicker.yml" in readme_header_action
    assert "single manual-dispatch front door" in readme
    assert "### Dispatch authorization" in readme
    assert "name: Validate routing outputs" in kicker
    assert "github.ref_name == 'dev'" in kicker
    assert "github.ref_name == 'main'" in kicker
    assert "inputs.operation == 'metadata'" in kicker
    assert "::notice title=Dispatch route::" in kicker
    assert "::notice title=Dispatch route deferred::" in kicker
    assert "SYNC_DEV_CHANGED:" in kicker
    assert kicker.count("enable_security_scan: ${{ needs.parse-config.outputs.run_security == 'true'") == 2
    assert kicker.count("always() && !cancelled() && needs.parse-config.result == 'success'") >= 4
    assert kicker.count("&& needs.parse-config.result == 'success'") >= 6
    assert "# Blackout Secure README Header Audit" in readme_header_action
    assert "outcome=${outcome}" in readme_header_action
    assert "RH001" in readme_header_action and "RH030" in readme_header_action

    for managed_template in (
        ROOT / "sync-files/workflows"
    ).glob("*.yml"):
        assert "\non:\n" not in managed_template.read_text()
        assert "\n\"on\":\n" in managed_template.read_text()
    managed_templates = sorted(
        path.name for path in (ROOT / "sync-files/workflows").glob("*.yml")
    )
    assert managed_templates == ["bos-universal-gatekeeper-kicker.yml"]
    assert not (ROOT / ".github/workflows/bos-launchpad-marketplace.yml").exists()
    assert not (
        ROOT / "sync-files/workflows/bos-launchpad-marketplace.yml"
    ).exists()

    marketplace_ruleset_path = (
        ROOT / "scripts/marketplace-repo/main-protection-ruleset.json"
    )
    marketplace_ruleset = json.loads(marketplace_ruleset_path.read_text())
    assert marketplace_ruleset["name"] == "marketplace-action-default-branch-guard"
    assert marketplace_ruleset["target"] == "branch"
    assert marketplace_ruleset["enforcement"] == "active"
    assert marketplace_ruleset["bypass_actors"] == [{
        "actor_id": "BYPASS_ACTOR_ID_PLACEHOLDER",
        "actor_type": "Integration",
        "bypass_mode": "always",
    }]
    assert marketplace_ruleset["conditions"]["ref_name"]["include"] == [
        "~DEFAULT_BRANCH"
    ]
    ruleset_types = {rule["type"] for rule in marketplace_ruleset["rules"]}
    assert {"deletion", "non_fast_forward", "pull_request", "file_path_restriction"} <= ruleset_types
    marketplace_ruleset_readme = (
        ROOT / "scripts/marketplace-repo/README.md"
    ).read_text()
    assert "does not satisfy `PS020` on its own" in marketplace_ruleset_readme

    marketplace_workflow = (
        ROOT / ".github/workflows/bos-universal-marketplace.yml"
    ).read_text(encoding="utf-8")
    assert 'use_global_config: "true"' in marketplace_workflow or "use_global_config: 'true'" in marketplace_workflow
    assert (
        "global_config_path: hub-config/sync-files/config/marketplace-kit-global-config.json" in marketplace_workflow
        or "global_config_path:      hub-config/sync-files/config/marketplace-kit-global-config.json" in marketplace_workflow
    )
    assert "config_path: .github/bos-universal-config.json" in marketplace_workflow or "config_path:             .github/bos-universal-config.json" in marketplace_workflow
    assert_first_party_pin(
        marketplace_workflow,
        "blackoutsecure/bos-marketplace-kit/.github/actions/check",
    )

    assert "concurrency:" in kicker
    assert "github.repository" in kicker
    assert "cancel-in-progress:" in kicker

    resolver = (ROOT / ".github/actions/resolve-hub-ref/action.yml").read_text()
    assert "name: Resolve hub ref" in resolver
    assert 'echo "ref=${ref}"' in resolver


    repo_metadata_workflow = (
        ROOT / ".github/workflows/repo-metadata-sync.yml"
    ).read_text()
    assert "  workflow_call:" in repo_metadata_workflow
    assert "\n  release:\n" not in repo_metadata_workflow
    assert repo_metadata_workflow.count(
        "uses: blackoutsecure/bos-repo-about-sync-action@"
    ) == 1
    assert_first_party_pin(
        repo_metadata_workflow, "blackoutsecure/bos-repo-about-sync-action"
    )
    assert ".github/actions/repo-metadata@main" not in repo_metadata_workflow
    assert not (ROOT / ".github/actions/repo-metadata").exists()
    assert "steps.repo-admin-app.outputs.token || secrets.REPO_ADMIN_PAT" in repo_metadata_workflow
    assert "vars.GATEWALL_APP_ID" in repo_metadata_workflow
    assert "group: repo-metadata-${{ github.repository }}" in repo_metadata_workflow
    assert "inputs.checkout_ref || github.sha" in repo_metadata_workflow
    assert workflow.count("uses: ./.github/workflows/repo-metadata-sync.yml") == 1
    assert ".github/actions/repo-metadata@main" not in workflow

    artifact_release = (ROOT / ".github/workflows/release.yml").read_text()
    marketplace_promote = (
        ROOT / ".github/workflows/release-promote.yml"
    ).read_text()
    assert artifact_release.startswith(
        "# Tag-driven release **pipeline**."
    )
    assert "name: Artifact Release" in artifact_release
    assert "name: Marketplace Promotion Release" in marketplace_promote
    assert "release.yml@main" in workflow
    assert "release-promote.yml" not in artifact_release
    assert "bos-universal-release-validation.yml@main" in artifact_release
    assert "bos-universal-release-validation.yml@main" in marketplace_promote
    assert ".github/workflows/release.yml@main" not in marketplace_promote
    assert marketplace_promote.count(
        "uses: blackoutsecure/bos-automation-hub/"
        ".github/actions/resolve-release-tag@main"
    ) == 1
    promote_hub_refs = re.findall(
        r"uses: blackoutsecure/bos-automation-hub/[^\s]+@(\w+)",
        marketplace_promote,
    )
    assert promote_hub_refs and set(promote_hub_refs) == {"main"}, promote_hub_refs
    assert marketplace_promote.count(
        "uses: blackoutsecure/bos-automation-hub/"
        ".github/actions/shared/preflight-runner-config@main"
    ) == 1
    assert "LATEST=\"$(git tag --list" not in marketplace_promote
    publisher_call = (
        "uses: blackoutsecure/bos-automation-hub/"
        ".github/workflows/github-release.yml@main"
    )
    assert len(
        re.findall(
            rf"^\s+{re.escape(publisher_call)}$", artifact_release, re.MULTILINE
        )
    ) == 1
    assert len(
        re.findall(
            rf"^\s+{re.escape(publisher_call)}$", marketplace_promote, re.MULTILINE
        )
    ) == 1

    workflows = sorted((ROOT / ".github/workflows").glob("*.yml"))
    lint_workflow = (ROOT / ".github/workflows/lint.yml").read_text()
    assert "branches: [main, dev]" in lint_workflow
    reusable = {
        path.name for path in workflows if "\n  workflow_call:\n" in path.read_text()
    }
    event_only = {path.name for path in workflows} - reusable
    assert event_only == {
        "lint.yml",
        "openwrt-readsb-wiedehopf-bump.yml",
        "release-hub.yml",
        "sync-action-pins.yml",
        "bos-org-kicker-fanout.yml",
        "bos-hub-managed-sync-propagate.yml",
        "bos-hub-gatekeeper-kicker.yml",
        "gatewall-smoke-test.yml",
        "osi-license-catalogue-refresh.yml",
    }

    release_hub = (ROOT / ".github/workflows/release-hub.yml").read_text()
    assert "name: Hub runtime release" in release_hub
    assert "grep -lE '^  workflow_call:'" in release_hub
    assert "DENYLIST=(" not in release_hub
    assert "${{ github.event.repository.default_branch }}" in release_hub
    assert not re.search(r"(?:ref:|source_branch:)\s+dev\b", release_hub)
    assert "origin/dev" not in release_hub
    assert "refs/heads/dev" not in release_hub
    assert "release-promote.yml@main" not in release_hub
    assert "uses: ./.github/workflows/github-release.yml" in release_hub
    assert release_hub.count(
        "uses: ./.github/actions/resolve-release-tag"
    ) == 1
    assert release_hub.count(
        "uses: ./.github/actions/universal-config"
    ) == 2
    assert release_hub.count(
        "uses: ./.github/workflows/repo-metadata-sync.yml"
    ) == 1
    assert "checkout_ref: ${{ needs.compute-tag.outputs.tag_name }}" in release_hub
    assert "needs.release.result == 'success'" in release_hub
    assert "inputs.release_draft != true" in release_hub
    assert "REPO_ADMIN_PAT: ${{ secrets.REPO_ADMIN_PAT }}" in release_hub
    assert "GATEWALL_APP_PRIVATE_KEY: ${{ secrets.GATEWALL_APP_PRIVATE_KEY }}" in release_hub
    assert "RELEASE_PAT: ${{ secrets.RELEASE_PAT }}" in release_hub
    assert "vars.GATEWALL_APP_ID" in release_hub
    assert "LATEST=\"$(git tag --list" not in release_hub

    balena_block = (
        ROOT / ".github/workflows/balena-block-publish.yml"
    ).read_text()
    balena_fleet = (
        ROOT / ".github/workflows/balena-fleet-deploy.yml"
    ).read_text()
    balena_publisher = (
        ROOT / ".github/actions/balena-publish/action.yml"
    ).read_text()
    shared_balena_action = (
        "uses: blackoutsecure/bos-automation-hub/"
        ".github/actions/balena-publish@main"
    )
    for balena_workflow in (balena_block, balena_fleet):
        assert balena_workflow.count(shared_balena_action) == 1
        assert "balena-io/deploy-to-balena-action@" not in balena_workflow
        assert "require_runner_x64: 'true'" not in balena_workflow
        runs_on = re.findall(r"^\s+runs-on:.*$", balena_workflow, re.MULTILINE)
        assert runs_on and all("RUNNER_X64" not in line for line in runs_on)
    assert "default: 'v24.1.4'" in balena_publisher
    assert "${plat}-${arch}-standalone.tar.gz" in balena_publisher
    assert "sync-balena-yml:" in balena_block
    assert "matrix: ${{ fromJSON(needs.plan.outputs.matrix) }}" in balena_fleet

    docker_workflow = (
        ROOT / ".github/workflows/docker-build-push.yml"
    ).read_text()
    compose_build_args = (
        "uses: blackoutsecure/bos-automation-hub/"
        ".github/actions/compose-docker-build-args@main"
    )
    assert docker_workflow.count(compose_build_args) == 2
    assert "echo \"build_args<<__EOF__\"" not in docker_workflow

    # The sole managed receiver resolves static @dev and @main refs per run.
    refs = re.findall(r"uses: blackoutsecure/bos-automation-hub/[^\s]+@(\w+)", kicker)
    assert refs and set(refs) == {"main", "dev"}, refs

    sync_backend = (ROOT / ".github/workflows/bos-universal-sync.yml").read_text()
    assert "[RUNTIME] Blackout Secure Managed File Sync" in sync_backend
    assert "uses: ./hub-runtime/.github/actions/universal-config" in sync_backend
    assert "inputs.hub_ref != 'auto' && inputs.hub_ref" in sync_backend
    assert "github.event_name == 'merge_group'" in sync_backend

    # ── standardized reporting ────────────────────────────────────
    # One shared audit-report surface, driven by findings data, so every
    # workflow reports status the same way instead of hand-rolling a
    # summary block per job.
    job_report = (ROOT / ".github/actions/job-report/action.yml").read_text()
    assert "name: Job report" in job_report
    for token in ("outcome", "verdict", "passes", "warns", "fails", "skips", "total"):
        assert f"{token}:" in job_report
    assert "Provided by [{brand_name}]({brand_url})" in job_report
    assert "brand_name" in job_report and "brand_url" in job_report
    assert "## Recommended Actions" in job_report
    assert "## Detailed Findings" in job_report
    assert '"⚪ Not Assessed"' in job_report

    report_refs = {
        "uses: ./hub-runtime/.github/actions/job-report",
        "uses: ./hub-source/.github/actions/job-report",
    }
    for reporting_workflow in (gate_workflow, sync_backend):
        assert any(ref in reporting_workflow for ref in report_refs)
        # Report policy is read through step outputs, never a bare
        # `fromJSON` of a possibly-empty needs output, so the report
        # still renders when config resolution failed.
        assert "fail_on: ${{ steps.findings.outputs.fail_on }}" in reporting_workflow
        assert (
            "enable_summary: ${{ steps.findings.outputs.enable_summary }}"
            in reporting_workflow
        )
        assert (
            "enable_annotations: ${{ steps.findings.outputs.enable_annotations }}"
            in reporting_workflow
        )

    # Runner topology comes from the organization block, never a literal.
    assert "org: ${{ steps.config.outputs.organization }}" in gate_workflow
    assert "config: ${{ steps.config.outputs.cfg }}" in gate_workflow
    assert "title: ${{ steps.findings.outputs.title_prefix }} Security Gate Report" in gate_workflow
    assert "org: ${{ steps.config.outputs.organization }}" in sync_backend
    assert "config: ${{ steps.config.outputs.cfg }}" in sync_backend
    assert "title: ${{ steps.findings.outputs.title_prefix }} Managed File Sync Report" in sync_backend
    assert (
        "runs-on: ${{ fromJSON(needs.resolve-config.outputs.org)"
        ".workflows.security.runs_on }}"
    ) in gate_workflow
    assert "workflows.sync.runs_on }}" in sync_backend
    # Two literal runners survive by design: `resolve-config` bootstraps
    # the runner topology, and the aggregated summary must still run when
    # that bootstrap failed.
    assert gate_workflow.count("runs-on: ubuntu-latest") == 2
    assert sync_backend.count("runs-on: ubuntu-latest") == 1
    assert "vars.DEFAULT_RUNNER" not in sync_backend
    assert 'elif counts["skip"]' in job_report
    assert "Coverage incomplete" in job_report
    assert 'set_value(row.get("value"))' in job_report
    assert 'for key in LABELS if counts[key]' in job_report

    hub_config_raw = json.loads((ROOT / ".github/bos-universal-config.json").read_text())
    assert set(hub_config_raw) == {
        "action_pins", "gate", "launchpad", "managed_file_sync", "organization", "remediation",
    }
    # The pin bumper reads this section instead of a standalone manifest, so
    # the standalone file must stay gone and the section must stay usable.
    assert not (ROOT / ".github/action-pins.json").exists()
    hub_pins = hub_config_raw["action_pins"]
    assert hub_pins["channel"] in {"auto", "stable", "prerelease", "prerelease-preferred", "pre-latest"}
    assert hub_pins["scan_globs"]
    assert hub_pins["repositories"]
    assert all("repository" in entry for entry in hub_pins["repositories"])
    assert all(entry.get("ref_mode", "sha") in {"sha", "latest"} for entry in hub_pins["repositories"])
    assert hub_config_raw["gate"] == {
        "node_lint_mode": "auto",
        "python_lint_mode": "auto",
        "shell_lint_mode": "auto",
    }
    assert hub_config_raw["remediation"] == {
        "mode": "notify",
        "min_confidence": "high",
        "allow_workflow_changes": False,
        "allow_security_control_changes": False,
        "allow_auto_merge": False,
    }
    hub_org = hub_config_raw["organization"]
    assert hub_org["runners"]["default"] == "ubuntu-latest"
    assert hub_org["reporting"]["fail_on"] == "fail"
    assert hub_org["defaults"]["timeout_minutes"] == 30
    # Hub config has no workflow overrides; they're just examples for consumers.
    assert "workflows" not in hub_org

    global_code_scan_config = json.loads(
        (ROOT / "sync-files/config/code-scanning-kit-global-config.json").read_text(encoding="utf-8")
    )
    assert global_code_scan_config["code_scanning"]["posture"]["workflows"] == {
        "require_permissions_block": "fail",
        "forbid_write_all": "fail",
        "require_pinned_actions": "warn",
        "allow_tag_pin": ["blackoutsecure/bos-automation-hub"],
    }
    assert global_code_scan_config["code_scanning"]["posture"]["branches"] == {
        "main": {
            "require_conversation_resolution": True,
            "severity": "fail",
        },
        "dev": {},
    }
    assert global_code_scan_config["code_scanning"]["remediation"] == {"enable_ai_findings_summary": False}

    global_marketplace_config = json.loads(
        (ROOT / "sync-files/config/marketplace-kit-global-config.json").read_text(encoding="utf-8")
    )
    assert global_marketplace_config["marketplace_kit"] == {
        "profile": "strict",
        "org_health_repo": "blackoutsecure/.github",
        "check_org_health": True,
        "community_health_source": "inherit",
        "enable_security_scan": True,
        "defer_to_code_scanning_kit": True,
        "require_sponsorship": "warn",
        "funding_source": "inherit",
        "enable_ai_findings_summary": False,
        "version": "auto",
        "license": "auto",
        "require_license_audit": "fail",
        "allowed_licenses": ["Apache-2.0"],
        "denied_licenses": [],
        "license_catalogue_max_age_days": 400,
    }

    # ── Site-generator audit policy ────────────────────────────────────
    # The sitemap and security.txt generators resolve config in
    # marketplace -> global -> repository order. The hub owns the global
    # tier for both; `fail_on: never` keeps a failing control visible in
    # the report without blocking a deploy.
    global_sitemap_config = json.loads(
        (ROOT / "sync-files/config/sitemap-generator-global-config.json").read_text()
    )
    sitemap_audit = global_sitemap_config["sitemap"]["audit"]
    assert sitemap_audit["enable"] is True
    assert sitemap_audit["fail_on"] == "never"
    assert sitemap_audit["rules"]["require_https"] == "fail"
    assert sitemap_audit["rules"]["require_same_origin"] == "fail"
    assert global_sitemap_config["sitemap"]["remediation"] == {
        "enable_ai_findings_summary": False
    }

    global_securitytxt_config = json.loads(
        (ROOT / "sync-files/config/securitytxt-generator-global-config.json").read_text()
    )
    securitytxt_audit = global_securitytxt_config["security_txt"]["audit"]
    assert securitytxt_audit["enable"] is True
    assert securitytxt_audit["fail_on"] == "never"
    assert securitytxt_audit["expires_max_days"] == 365
    for required_rule in (
        "require_contact",
        "require_expires",
        "expires_not_expired",
        "valid_contact_uri",
        "require_https_uris",
        "well_known_location",
    ):
        assert securitytxt_audit["rules"][required_rule] == "fail", required_rule
    assert global_securitytxt_config["security_txt"]["remediation"] == {
        "enable_ai_findings_summary": False
    }

    cloudflare_workflow = (ROOT / ".github/workflows/deploy-cloudflare-pages.yml").read_text()
    assert "generator_audit:" in cloudflare_workflow
    assert "generator_audit_fail_on:" in cloudflare_workflow
    assert (
        "global_config_path: hub-generator-config/sync-files/config/"
        "sitemap-generator-global-config.json" in cloudflare_workflow
    )
    assert (
        "global_config_path: hub-generator-config/sync-files/config/"
        "securitytxt-generator-global-config.json" in cloudflare_workflow
    )
    assert (
        "global_config_path: hub-generator-config/sync-files/config/"
        "robotstxt-generator-global-config.json" in cloudflare_workflow
    )
    assert (
        "global_config_path: hub-generator-config/sync-files/config/"
        "web-manifest-generator-global-config.json" in cloudflare_workflow
    )
    # Audit artefacts land outside `deploy_dir` so they are never published.
    assert "path: generator-audit/" in cloudflare_workflow

    global_humanstxt_config = json.loads(
        (ROOT / "sync-files/config/humanstxt-generator-global-config.json").read_text()
    )
    humanstxt_audit = global_humanstxt_config["humans_txt"]["audit"]
    assert humanstxt_audit["enable"] is True
    assert humanstxt_audit["fail_on"] == "never"
    for required_rule in (
        "require_team_section",
        "require_site_section",
        "require_https_urls",
        "valid_section_syntax",
    ):
        assert humanstxt_audit["rules"][required_rule] == "fail", required_rule
    assert global_humanstxt_config["humans_txt"]["remediation"] == {
        "enable_ai_findings_summary": False
    }

    global_robotstxt_config = json.loads(
        (ROOT / "sync-files/config/robotstxt-generator-global-config.json").read_text()
    )
    robotstxt_audit = global_robotstxt_config["robots_txt"]["audit"]
    assert robotstxt_audit["enable"] is True
    assert robotstxt_audit["fail_on"] == "never"
    assert robotstxt_audit["max_size_kb"] == 500
    for required_rule in (
        "require_user_agent",
        "require_sitemap",
        "forbid_disallow_all",
        "valid_directives",
        "forbid_html_content",
    ):
        assert robotstxt_audit["rules"][required_rule] == "fail", required_rule
    assert global_robotstxt_config["robots_txt"]["remediation"] == {
        "enable_ai_findings_summary": False
    }

    global_web_manifest_config = json.loads(
        (ROOT / "sync-files/config/web-manifest-generator-global-config.json").read_text()
    )
    web_manifest_audit = global_web_manifest_config["web_manifest"]["audit"]
    assert web_manifest_audit["enable"] is True
    assert web_manifest_audit["fail_on"] == "never"
    assert web_manifest_audit["max_size_kb"] == 128
    for required_rule in (
        "require_name",
        "require_icons",
        "require_start_url",
        "require_display",
        "require_192_icon",
        "require_512_icon",
        "valid_json",
    ):
        assert web_manifest_audit["rules"][required_rule] == "fail", required_rule
    assert global_web_manifest_config["web_manifest"]["remediation"] == {
        "enable_ai_findings_summary": False
    }

    # The inline humans.txt block stands in for `bos-humanstxt-generator`
    # until that action publishes a taggable release, so it must still emit
    # humanstxt.org-standard banners and field names.
    assert "/* HUMANS.TXT */" in cloudflare_workflow
    assert "Last update: ${last_update}" in cloudflare_workflow
    assert "/* NOTES */" not in cloudflare_workflow

    # Every first-party action pinned in this repo must be tracked by the
    # pin bumper, otherwise its SHA silently goes stale.
    action_pins = json.loads(
        (ROOT / ".github/bos-universal-config.json").read_text()
    )["action_pins"]
    tracked = {entry["repository"] for entry in action_pins["repositories"]}
    for generator in (
        "blackoutsecure/bos-sitemap-generator",
        "blackoutsecure/bos-securitytxt-generator",
        "blackoutsecure/bos-robotstxt-generator",
        "blackoutsecure/bos-web-application-manifest-generator",
    ):
        assert generator in tracked, generator

    gatekeeper_workflow = (ROOT / ".github/workflows/bos-universal-gatekeeper.yml").read_text()
    assert cloudflare_workflow.count("generator_audit_artifact_name") >= 2
    assert gatekeeper_workflow.count("cloudflare_generator_audit:") >= 1
    assert gatekeeper_workflow.count("generator_audit_fail_on:") >= 3

    gatekeeper_kicker = (
        ROOT / "sync-files/workflows/bos-universal-gatekeeper-kicker.yml"
    ).read_text()
    assert gatekeeper_kicker.count("cloudflare_generator_audit:") == 2
    assert gatekeeper_kicker.count("cloudflare_generator_audit_fail_on:") == 2

    universal_config_action = (
        ROOT / ".github/actions/universal-config/action.yml"
    ).read_text()
    assert '"generator_audit": cloudflare_raw.get("generator_audit") is True' in universal_config_action
    assert '"generator_audit_fail_on"' in universal_config_action

    assert (
        "global_config_path: hub-config/sync-files/config/code-scanning-kit-global-config.json"
        in gate_workflow
    )
    assert re.search(r'use_global_config:\s+["\']true["\']', gate_workflow)
    assert "config: .github/bos-universal-config.json" in gate_workflow
    assert_first_party_pin(gate_workflow, "blackoutsecure/bos-code-scanning-kit")
    standalone_scan_workflow = (
        ROOT / ".github/workflows/security-scan.yml"
    ).read_text()
    assert "sparse-checkout: sync-files/config/code-scanning-kit-global-config.json" in standalone_scan_workflow
    assert re.search(r'use_global_config:\s+["\']true["\']', standalone_scan_workflow)
    assert "config: .github/bos-universal-config.json" in standalone_scan_workflow
    assert_first_party_pin(
        standalone_scan_workflow, "blackoutsecure/bos-code-scanning-kit"
    )
    global_sync_config = json.loads(
        (ROOT / "sync-files/config/managed-file-sync-global-config.json").read_text()
    )
    sync_policy = global_sync_config["managed_file_sync"]
    assert "exclude_services" not in sync_policy
    assert "exclude_sevices" not in sync_policy
    assert sync_policy["take_over_managed_files"] is True
    # The hub is checked out into `sync-files/`; content_file paths are
    # explicit beneath that root, including the workflows/ subdirectory.
    assert sync_policy["managed_files_path"] == "sync-files"
    assert sync_policy["services"] == [
        "shellcheck",
        "yamllint",
        "coverage_artifacts",
        "license_service",
        "security_readme_pointer",
        "bos_universal_gatekeeper_kicker",
    ]
    assert sync_policy["service_definitions"]["bos_universal_gatekeeper_kicker"] == {
        "mode": "file",
        "files": [
            {
                "path": ".github/workflows/bos-universal-gatekeeper-kicker.yml",
                "content_file": "workflows/bos-universal-gatekeeper-kicker.yml",
            }
        ],
    }
    assert {
        name for name in sync_policy["service_definitions"]
        if name.startswith("bos_universal_")
    } == {"bos_universal_gatekeeper_kicker"}
    assert all(
        definition["mode"] == "file"
        for name, definition in sync_policy["service_definitions"].items()
        if name.startswith("bos_universal_")
    )
    assert "variables" not in sync_policy
    hub_config = cfg_from(
        run_universal_config_raw((ROOT / ".github/bos-universal-config.json").read_text())
    )
    assert hub_config["gate"] == {
        "node_lint_mode": "auto",
        "python_lint_mode": "auto",
        "shell_lint_mode": "auto",
    }
    assert hub_config["repo_metadata"] == {
        "enable": True,
        "homepage": "https://github.com/blackoutsecure/bos-automation-hub",
        "ai_model": "auto",
        "description_mode": "auto",
        "description_fallback": "",
        "use_existing_readme": True,
        "generate_readme": False,
        "generate_topics": True,
        "topics_fallback": (
            "github-actions automation reusable-workflows composite-actions "
            "devops ci-cd workflow-automation"
        ),
    }
    assert hub_config["security_scan"]["enable"] is True
    assert hub_config["security_scan"]["enable_kit_composite"] is True
    assert hub_config["security_scan"]["enable_posture"] is True
    assert hub_config["security_scan"]["enable_scanners"] is True
    assert hub_config["security_scan"]["enable_upload"] is True
    assert hub_config["security_scan"]["use_advanced_pat"] is True
    assert not (ROOT / ".github/workflows/sync-managed-config.yml").exists()
    assert "  workflow_call:" in sync_backend
    assert "  schedule:" not in sync_backend
    assert "  workflow_dispatch:" not in sync_backend
    assert "workflow-sync App token or legacy PAT available" in sync_backend
    assert '"severity": "warn" if mode == "check" else "pass"' in sync_backend
    assert "workflow-file state is unverified" in sync_backend
    assert "Drift detected; rerun in `commit` mode" in sync_backend
    # Every reusable (`workflow_call:`) workflow must be callable ONLY by
    # another workflow. Any additional trigger on a runtime workflow would let
    # it be started outside the gatekeeper's authorization path, so the
    # trigger surface is asserted for all of them rather than a hand-listed
    # subset that new workflows can silently escape.
    for runtime_name in sorted(reusable):
        runtime_body = (ROOT / ".github/workflows" / runtime_name).read_text()
        assert "\n  workflow_call:\n" in runtime_body, runtime_name
        for forbidden in (
            "workflow_dispatch",
            "schedule",
            "push",
            "pull_request",
            "pull_request_target",
            "merge_group",
            "repository_dispatch",
            "issues",
        ):
            # Anchored to a real top-level trigger key so commented-out
            # examples inside the header docs don't count as triggers.
            assert not re.search(
                rf"^  {forbidden}:\s*$", runtime_body, re.MULTILINE
            ), (runtime_name, forbidden)
    gatekeeper_runtime = (
        ROOT / ".github/workflows/bos-universal-gatekeeper.yml"
    ).read_text()
    assert "  workflow_call:" in gatekeeper_runtime
    assert "  workflow_dispatch:" not in gatekeeper_runtime
    hub_gatekeeper_kicker = (
        ROOT / ".github/workflows/bos-hub-gatekeeper-kicker.yml"
    ).read_text()
    assert "name: '[KICKER] Blackout Secure Hub Gatekeeper'" in hub_gatekeeper_kicker
    assert "  schedule:" in hub_gatekeeper_kicker
    assert "  workflow_dispatch:" in hub_gatekeeper_kicker
    assert "paths:" in hub_gatekeeper_kicker
    assert "bos-hub-gatekeeper-kicker.yml" not in hub_gatekeeper_kicker.split(
        "      - '", 1
    )[0]
    assert "global_config_json" not in sync_backend
    assert (
        "global_config_path: hub-source/sync-files/config/managed-file-sync-global-config.json"
        in sync_backend
    )
    assert "Ensure managed-file-sync global config is available" in sync_backend
    assert (
        'git -C hub-source show "HEAD:sync-files/config/managed-file-sync-global-config.json"'
        in sync_backend
    )
    assert "managed_files_path: hub-source/sync-files" in sync_backend
    assert "inputs.hub_ref != 'auto' && inputs.hub_ref" in sync_backend
    assert "config_path: .github/bos-universal-config.json" not in sync_backend
    assert "dry_run: ${{ (inputs.mode || 'commit') == 'check' }}" in sync_backend
    assert re.search(r'use_global_config:\s+["\']auto["\']', sync_backend)
    assert_first_party_pin(
        sync_backend, "blackoutsecure/bos-managed-file-sync-action"
    )
    assert "uses: ./hub-source/.github/actions/commit-and-push" in sync_backend
    assert "permission-workflows: write" in sync_backend
    assert "workflow_sync_pat:" in sync_backend
    assert "steps.workflow-app.outputs.token || secrets.WORKFLOW_SYNC_PAT" in sync_backend
    assert "vars.GATEWALL_APP_ID" in sync_backend
    assert "secrets.WORKFLOW_SYNC_PAT != '' && 'true' || 'false'" in sync_backend
    assert "disabled_services" in sync_backend

    gatekeeper_workflow = (
        ROOT / ".github/workflows/bos-universal-gatekeeper.yml"
    ).read_text()
    assert "secrets: inherit" in kicker
    assert "REPO_ADMIN_PAT: ${{ secrets.REPO_ADMIN_PAT }}" not in kicker
    assert (
        "REPO_ADMIN_PAT: ${{ secrets.REPO_ADMIN_PAT || secrets.RELEASE_PAT }}"
        in gatekeeper_workflow
    )
    assert "RELEASE_PAT:\n        description:" in gatekeeper_workflow
    assert "GATEWALL_APP_PRIVATE_KEY:" in gatekeeper_workflow

    assert_markdown_links_exist(ROOT / "README.md")
    assert_markdown_links_exist(ROOT / "sync-files/README.md")

    print(
        f"repository contract valid: {len(declared)} launchpad inputs, "
        f"{len(gate_declared)} gate inputs, {len(reusable)} runtime workflows"
    )


if __name__ == "__main__":
    main()
