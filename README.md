# Blackout Secure Automation Hub

**Copyright © 2025-2026 Blackout Secure | Apache License 2.0**

[![GitHub release](https://img.shields.io/github/v/release/blackoutsecure/bos-automation-hub)](https://github.com/blackoutsecure/bos-automation-hub/releases)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue)](LICENSE)
[![Made by BlackoutSecure](https://img.shields.io/badge/made%20by-BlackoutSecure-1f1f1f)](https://github.com/blackoutsecure)

Central reusable GitHub Actions workflows, shared composite actions, and
managed repository files for Blackout Secure projects.

## Branch model

- `dev` is the development and default branch.
- `main` is the promoted stable runtime consumed through `@main`.
- version tags (`vX.Y.Z` and floating `vX`) point at promoted runtime commits.

GitHub Actions does not allow expressions in `uses:` references. Branch
selection is therefore handled by ownership rather than generated ref strings:

- managed consumer callers use `blackoutsecure/bos-automation-hub/...@main`;
- hub-only validation uses local `./.github/actions/...` references;
- runtime branch decisions use `github.event.repository.default_branch` where
  the caller repository's branch is intended.

The reusable security and managed-file-sync workflows expose `hub_ref`, which
defaults to `auto`. Auto follows a pull request or merge-group base branch,
then the current `dev` ref, and otherwise selects `main`. The managed dev/main
callers select the matching runtime through their static `uses:` refs; other
callers can pass `hub_ref: dev` or `hub_ref: main` when they need a deliberate
override.

[`release-hub.yml`](.github/workflows/release-hub.yml) promotes shared actions,
managed templates, `LICENSE`, this README, and every workflow declaring
`workflow_call`. Event-only maintenance workflows stay on `dev` automatically.

## Universal gatekeeper

[`bos-universal-gatekeeper.yml`](.github/workflows/bos-universal-gatekeeper.yml)
is the release and deployment orchestrator. Its managed consumer caller is
[`bos-universal-gatekeeper-kicker.yml`](sync-files/workflows/bos-universal-gatekeeper-kicker.yml).

The gatekeeper can compose:

- upstream release monitoring;
- multi-architecture Docker publishing and Docker Scout scanning;
- Balena block and fleet publishing;
- GitHub Releases;
- Cloudflare Pages deployment and generated site metadata;
- security scanning;
- repository metadata updates.

The kicker is the single manual-dispatch front door for the repository. One
event kicker parses the repository config once, gives `force_run` one meaning,
and routes secrets through one trusted boundary; the reusable
[`bos-universal-gatekeeper.yml`](.github/workflows/bos-universal-gatekeeper.yml)
already splits release, deployment, and metadata concerns into independent
backend jobs with their own `needs`, permissions, outputs, and skip gates.

It triggers on `push` to both `dev` and `main`, matching the dual-branch job
graph (`resolve-target-ref` -> `sync-check-dev`/`sync-check-main` ->
`release-dev`/`release-main`). It carries no `on.push.paths` filter, because
`on:` is parsed before any job runs and cannot read a config file, so one
`file`-mode managed template cannot express a path list that suits every repo
shape. The `changed-paths` job decides relevance instead, from
`triggers.push_paths` in each consumer's own `.github/bos-universal-config.json`
(see [`sync-files/README.md`](sync-files/README.md)). It runs before
`sync-check-*` acquires `contents: write`, so an irrelevant push never reaches a
job that can commit, and an absent list means "run on every push".

`workflow_dispatch` exposes these operations, each routed to the backend that
owns it:

| Operation              | Routes to                       | Purpose                                  |
| ---------------------- | ------------------------------- | ---------------------------------------- |
| `full`                 | `bos-universal-gatekeeper.yml`  | Publish stages plus the security scan.   |
| `release_only`         | `bos-universal-gatekeeper.yml`  | Publish stages without forcing the scan. |
| `security_only`        | `bos-universal-gatekeeper.yml`  | Release-blocking security scan only.     |
| `sync_only`            | `bos-universal-sync.yml`        | Managed-file reconciliation only.        |
| `action_test`          | `bos-universal-action-test.yml` | Action smoke tests.                      |
| `metadata`             | `repo-metadata-sync.yml`        | About-box metadata only.                 |
| `marketplace_validate` | `bos-universal-marketplace.yml` | Marketplace manifest and rule checks.    |
| `marketplace_release`  | `release-promote.yml`           | Promote, tag, and publish the release.   |

Automatic triggers are unchanged: `schedule` and `push` continue to drive the
release pipeline, and routing jobs stay inert on those events. Security and
Marketplace keep their own kickers for pull-request and push events, because
those runs must fire on repository events that a dispatch front door cannot
proxy, and because their required checks are pinned in branch protection.

### Dispatch authorization

Every `workflow_dispatch` run passes through the `authorize` job before any
job holding write permission executes. The job uses the published
[`bos-workflow-gatekeeper`](https://github.com/blackoutsecure/bos-workflow-gatekeeper)
Marketplace action to resolve the actor's organization role, authorized-team
membership, and (when configured) enterprise-owner status, then fails closed
with an `Access Denied` annotation when the actor does not satisfy the policy.

The check authorizes `github.triggering_actor`, not `github.actor`. On a re-run
those differ: `actor` stays the original dispatcher while `triggering_actor` is
whoever pressed re-run, so authorizing `actor` would let any user with write
access replay a privileged dispatch under someone else's identity.

Policy is read from repository or organization variables rather than the parsed
config, so the gate never depends on a job that runs after it:

| Variable                              | Purpose                                                                |
| ------------------------------------- | ---------------------------------------------------------------------- |
| `GATEKEEPER_APP_ID`                   | GitHub App ID used to mint a short-lived authorization token.          |
| `GATEKEEPER_ENTERPRISE_SLUG`          | Enterprise slug for the owner lookup. Empty skips the check.           |
| `GATEKEEPER_REQUIRED_TEAMS`           | Comma-separated team slugs whose active members may dispatch.          |
| `GATEKEEPER_ALLOW_ORG_ADMIN`          | Set to `false` to stop treating org owners as authorized.              |
| `GATEWALL_APP_SLUG`                   | Exact Gatewall App slug trusted for protected machine handoffs.        |
| `GATEKEEPER_REQUIRE_ENTERPRISE_OWNER` | Set to `false` to relax the `high` gate-level enterprise requirement.  |
| `GATEKEEPER_HARDEN_RUNNER`            | Set to `true` to enable `step-security/harden-runner` egress auditing. |
| `GATEKEEPER_EGRESS_POLICY`            | `audit` (default) or `block` when runner hardening is enabled.         |

Credentials are split so the highest-privilege token is used for as little as
possible:

| Secret                       | Used for                                                                             | Notes                                                                                                                                                             |
| ---------------------------- | ------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `GATEKEEPER_APP_PRIVATE_KEY` | Org role and team lookups                                                            | Preferred. Paired with `GATEKEEPER_APP_ID`; the App needs `members: read`. `actions/create-github-app-token` mints an installation token that expires in an hour. |
| `GATEKEEPER_AUTHZ_PAT`       | Fallback for org/team, and the only credential that can resolve enterprise ownership | A GitHub App installation token cannot read `enterprise.ownerInfo`; that lookup needs a PAT belonging to an enterprise owner with `admin:enterprise`.             |

If neither credential is present the gate denies the dispatch, because the
default `GITHUB_TOKEN` cannot resolve org role, team membership, or enterprise
ownership.

For a guided organization-wide setup, run the loopback-only
[`Gatekeeper App setup helper`](tools/gatekeeper-app-setup/README.md). It uses
GitHub's App manifest flow, configures `GATEKEEPER_APP_ID` and
`GATEKEEPER_APP_PRIVATE_KEY` through the authenticated GitHub CLI, verifies
installation, and can re-run a failed authorization job without writing the
private key to disk.

### Gate levels

The gate level is derived from the blast radius of the requested operation and
scales the checks applied to it:

| Level       | Operations                                             | Requirement                                                     |
| ----------- | ------------------------------------------------------ | --------------------------------------------------------------- |
| `automatic` | `push`, `schedule`                                     | No human actor; authorization is skipped.                       |
| `low`       | `security_only`, `marketplace_validate`, `action_test` | Active organization membership.                                 |
| `standard`  | `sync_only`, `metadata`                                | Active organization membership.                                 |
| `high`      | `full`, `release_only`, `marketplace_release`          | Enterprise ownership, when `GATEKEEPER_ENTERPRISE_SLUG` is set. |

The `high` requirement is deliberately conditional on a configured enterprise
slug. Without one, the enterprise lookup is unverifiable and a fail-closed gate
would reject every release.

### Runner preflight

The published
[`bos-workflow-gatekeeper`](https://github.com/blackoutsecure/bos-workflow-gatekeeper)
action verifies the runner actually provides the toolchain an operation needs
before that operation starts, instead of failing halfway through with
credentials already in scope. It runs in `preflight_only` mode when
`gatekeeper.preflight` is configured:

```json
"gatekeeper": {
  "preflight": {
    "required_commands": ["docker", "jq", "python3"],
    "min_versions": { "python3": "3.11" },
    "version_args": { "docker": ["--version"] },
    "required_python_packages": ["requests>=2.31"],
    "fail_on_missing": true
  }
}
```

Set `fail_on_missing: false` to report drift as a warning during rollout rather
than blocking the run.

Consumer behavior is data-driven through `.github/bos-universal-config.json`; managed
workflow files are not edited in consumer repositories.

## Universal security

[`bos-universal-security.yml`](.github/workflows/bos-universal-security.yml) is
the reusable universal PR, protected-branch push, and merge-queue security/policy workflow. Its
managed caller is
[`bos-universal-gatekeeper-kicker.yml`](sync-files/workflows/bos-universal-gatekeeper-kicker.yml).

The required check is `security (dev) / Security summary` or
`security (main) / Security summary`, depending on which branch a run
targets. Gates are grouped by concern (see the comments in the workflow
file), though they stay one `workflow_call` surface and one required check
by design — splitting into separate workflows would force every consumer to
re-pin branch protection whenever a gate moved between groups:

- **Code quality:** workflow, Markdown, YAML, and Shell lint; optional Node
  checks (ESLint and Prettier); optional Python checks (Ruff and pytest);
  optional Shell checks (ShellCheck and Bats);
- **Security:** dependency review, code scanning (secret scan, SAST, GHAS
  posture audit), and pinned-action enforcement;
- **Compliance:** README-header and PR-title checks.

The hub itself runs
[`bos-universal-security.yml`](.github/workflows/bos-universal-security.yml)
directly. Use **Actions → Blackout Secure Universal Security → Run
workflow** on `dev` for a manual scan; it loads the current `security` section
from `.github/bos-universal-config.json`, just like the sync backend. The managed
[`bos-universal-gatekeeper-kicker.yml`](sync-files/workflows/bos-universal-gatekeeper-kicker.yml)
is retained for consumer repositories, but the hub does not install a local
kicker for this workflow.

Marketplace-specific validation is intentionally excluded. Marketplace Action
repositories add the managed
[`bos-universal-gatekeeper-kicker.yml`](sync-files/workflows/bos-universal-gatekeeper-kicker.yml),
which owns Marketplace validation, stable-branch guarding, promotion, and
opt-in post-release repository metadata synchronization.

Code-scan policy layers the same way sync policy does. Org-wide defaults live in
[sync-files/config/code-scanning-kit-global-config.json](sync-files/config/code-scanning-kit-global-config.json),
a hub-authored file the code-scan job checks out alongside the caller repo and
passes via `global_config_path`. A repository can layer its own overrides with
a `code_scanning` block in its own `.github/bos-universal-config.json`, which
`bos-code-scanning-kit` receives explicitly as its repository-tier config.

## Site generator compliance audit

The published site generators
([`bos-sitemap-generator`](https://github.com/blackoutsecure/bos-sitemap-generator),
[`bos-securitytxt-generator`](https://github.com/blackoutsecure/bos-securitytxt-generator),
[`bos-robotstxt-generator`](https://github.com/blackoutsecure/bos-robotstxt-generator),
[`bos-humanstxt-generator`](https://github.com/blackoutsecure/bos-humanstxt-generator),
[`bos-web-application-manifest-generator`](https://github.com/blackoutsecure/bos-web-application-manifest-generator))
each ship a posture audit — SEO/sitemaps.org rules for the first, RFC 9116 for
the second, RFC 9309 for the third, humanstxt.org for the fourth, and the W3C
Web App Manifest / PWA installability criteria for the fifth — resolved through
the same marketplace → global → repository config cascade the code-scanning kit
uses. The hub owns the global tier:

- [sync-files/config/sitemap-generator-global-config.json](sync-files/config/sitemap-generator-global-config.json)
- [sync-files/config/securitytxt-generator-global-config.json](sync-files/config/securitytxt-generator-global-config.json)
- [sync-files/config/robotstxt-generator-global-config.json](sync-files/config/robotstxt-generator-global-config.json)
- [sync-files/config/humanstxt-generator-global-config.json](sync-files/config/humanstxt-generator-global-config.json)
- [sync-files/config/web-manifest-generator-global-config.json](sync-files/config/web-manifest-generator-global-config.json)

All set `fail_on: never`, so a failing control is fully reported without
blocking a deploy until a repository opts up. `enable_ai_findings_summary` is
`false` org-wide, matching the code-scanning kit policy — the generators fall
back to their deterministic local summary.

[`deploy-cloudflare-pages.yml`](.github/workflows/deploy-cloudflare-pages.yml)
sparse-checks-out those policy files into `hub-generator-config/` and passes
each generator its `global_config_path`. Enable the audit through
`cloudflare.generator_audit` in `.github/bos-universal-config.json`:

```json
{
  "cloudflare": {
    "generate_sitemap": true,
    "generate_robots": true,
    "generate_security_txt": true,
    "security_contact": "security@example.com",
    "generator_audit": true,
    "generator_audit_fail_on": "never"
  }
}
```

Audit artefacts (SARIF, JSON report, recommendations) are written to
`generator-audit/` — deliberately outside `deploy_dir`, so they are collected as
the `site-compliance-reports` build artifact and never published with the site.
Each generator also appends its Markdown report to the job step summary.

> The generator `uses:` pins in `deploy-cloudflare-pages.yml` must reach the
> releases that carry these inputs before `generator_audit` has any effect.
> All four site generators are now tracked in the `action_pins` section of
> [`.github/bos-universal-config.json`](.github/bos-universal-config.json), so `sync-action-pins.yml`
> bumps them automatically once those releases land.

`humans.txt` is still emitted by an inline block in
`deploy-cloudflare-pages.yml` rather than by `bos-humanstxt-generator`, because
that action has no taggable release for the pin bumper to resolve yet. The block
now emits humanstxt.org-standard field names under the standard `TEAM`,
`THANKS`, and `SITE` banners, so swapping it for the action is a drop-in change
once a release exists.

## Managed file sync

[`bos-universal-sync.yml`](.github/workflows/bos-universal-sync.yml) is a thin
wrapper around the published
[`bos-managed-file-sync-action`](https://github.com/blackoutsecure/bos-managed-file-sync-action).
Unlike the other reusable entry points, this workflow only ever does one
thing (managed-file sync), so its display name and run name read "Blackout
Secure managed file sync" rather than "universal" — the `bos-universal-sync*`
filenames are unchanged to keep existing `uses:` references stable. It is
callable only through the managed
[`bos-universal-gatekeeper-kicker.yml`](sync-files/workflows/bos-universal-gatekeeper-kicker.yml).
That kicker owns the schedule, config-change, and manual events. Its
consumer front door resolves the target hub branch and delegates to
`bos-universal-sync.yml`, the same pattern used by the launchpad, security, and
Marketplace kickers. The reusable workflow never self-triggers and never
traverses the release, security, or Marketplace workflows. Sync defaults live in
[sync-files/config/managed-file-sync-global-config.json](sync-files/config/managed-file-sync-global-config.json),
a hub-authored file. `bos-universal-sync.yml` checks out this hub alongside
the consumer repo and passes `global_config_path` at the checked-out copy, so
the policy stays a real, editable JSON file instead of an inline blob.

## Universal action test

[`bos-universal-action-test.yml`](.github/workflows/bos-universal-action-test.yml)
is a reusable pytest matrix plus an optional live-upstream smoke test for
Actions repositories with a Python implementation. Its managed caller is
[`bos-universal-gatekeeper-kicker.yml`](sync-files/workflows/bos-universal-gatekeeper-kicker.yml).

It complements `bos-universal-security.yml`'s single-OS/Python `python-lint`
job (Ruff + pytest, part of the PR security gate) rather than replacing it:
use this workflow when a repo needs broader Python/OS matrix coverage and/or
validation against a live upstream target, driven by an `action_test` block
in `.github/bos-universal-config.json`:

```json
{
  "action_test": {
    "python_versions": ["3.10", "3.11", "3.12"],
    "os_matrix": ["ubuntu-latest", "macos-latest", "windows-latest"],
    "python_packages": ["pytest>=8.0", "ruff>=0.6", "PyYAML>=6.0"],
    "pytest_args": "-q",
    "enable_smoke_test": true,
    "smoke_trigger": "push-dev",
    "smoke_test_config": { "source": "npm", "package_name": "@actions/core" }
  }
}
```

The smoke-test job checks out the calling repo, invokes it as an action
(`uses: ./`) with the configured `source` and `package_name` inputs, and
asserts its declared `version` output is non-empty; it requires an
`action.yml` at the repo root. `smoke_trigger` defaults to
`push-dev` so live-upstream calls don't run on untrusted PR heads.

## Universal release validation

[`bos-universal-release-validation.yml`](.github/workflows/bos-universal-release-validation.yml)
is the final release-readiness gate. It runs against the exact branch, tag, or
SHA being published and is called automatically by both
[`release.yml`](.github/workflows/release.yml) and
[`release-promote.yml`](.github/workflows/release-promote.yml). The hub's own
[`release-hub.yml`](.github/workflows/release-hub.yml) applies the same action
locally before promoting the shared runtime.

The responsibilities deliberately remain separate:

- universal security and `bos-code-scanning-kit` are the required pre-merge
  security, posture, and SARIF controls;
- `bos-workflow-gatekeeper` decides whether the actor may start a privileged
  release;
- `bos-marketplace-kit` applies GitHub Marketplace-specific manifest and
  repository rules;
- universal release validation reruns the candidate's tests/build and verifies
  that generated artifacts are committed before any publication job receives
  write credentials.

Organization defaults live in
[`sync-files/config/release-validation-global-config.json`](sync-files/config/release-validation-global-config.json).
The workflow auto-detects Node and Python projects. For Node it installs from
the lockfile, runs the repository's non-mutating verification scripts, and
builds when a build script exists. For Python it installs the project and runs
configured Ruff/pytest checks. It then fails if those steps changed tracked
files, catching stale bundled Action output such as `dist/`.

Repository-only requirements stay in `.github/bos-universal-config.json`:

```json
{
  "release_validation": {
    "required_paths": ["README.md", "LICENSE", "NOTICE"],
    "version_match": "fail",
    "custom_commands": ["bash test/unit/run.sh"]
  }
}
```

Prefer a checked-in `.github/scripts/release-validation.sh` hook when a
repository has several domain-specific checks. The hook runs with read-only
repository permissions and no publishing secrets. Keep generally reusable
checks in the hub or the appropriate kit instead of copying them into hooks.

Every run emits the standard Markdown/HTML/JSON report, annotations, evidence,
and deterministic remediation guidance. Automated correction should happen in
a separate reviewed PR, never by mutating the candidate during a release. The
existing AI remediation flow may summarize or propose a validated patch, but a
missing model, low-confidence result, or Copilot review must never substitute
for deterministic checks or the required human approval.

No additional repository is needed today. The hub owns cross-repository
orchestration; Marketplace-only controls belong in `bos-marketplace-kit`, and
security controls belong in `bos-code-scanning-kit`. Split release validation
into its own Marketplace Action only if external organizations need a stable,
independently versioned public product rather than the current BOS reusable
workflow.

## Workflow boundaries

Gate and release workflows intentionally remain separate because they run at
different trust and permission boundaries:

| Layer                                             | Trigger and authority                                 | Responsibility                                                                                                                                                                |
| ------------------------------------------------- | ----------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `bos-universal-security.yml` (universal security) | Pull request / merge queue; read-mostly               | Lint, tests, dependency review, code scanning, and policy checks before merge.                                                                                                |
| `bos-universal-gatekeeper-kicker.yml`             | Push, schedule, or manual dispatch                    | Select reusable security, sync, action-test, Marketplace, upstream, metadata, or release operations.                                                                          |
| `bos-universal-gatekeeper.yml`                    | Push, schedule, or manual caller; publish permissions | Sync managed files ahead of the run, monitor upstreams, run the release-blocking security scan (or, via `operation: security_only`, just that scan), and coordinate delivery. |
| `release.yml` (artifact release)                  | Called by Universal or another trusted workflow       | Publish Docker, Balena, and GitHub Release artifacts for an already-approved version.                                                                                         |
| `bos-universal-release-validation.yml`            | Called immediately before publication; read-only      | Rerun ecosystem checks, repository extensions, and generated-artifact integrity checks against the release candidate.                                                         |
| `release-promote.yml` (Marketplace promotion)     | Operator-driven Marketplace caller                    | Promote an allowlisted source tree to the workflow-free stable branch and release it.                                                                                         |
| `release-hub.yml` (hub runtime release)           | Hub-only manual workflow                              | Promote this hub's reusable runtime from the default branch to `main` and tag it.                                                                                             |

These release orchestrators should not be merged. Artifact release does not
mutate branches; Marketplace promotion deliberately removes disallowed files;
hub promotion must publish reusable workflows that Marketplace promotion
forbids. Their common publication stage is already consolidated in
[`github-release.yml`](.github/workflows/github-release.yml), while runner
validation, release-tag resolution, and release-context logic live in shared
actions. Hub and Marketplace promotion both use
[`resolve-release-tag`](.github/actions/resolve-release-tag/action.yml),
with distinct first-release defaults (`v0.0.1` for the hub and `v0.1.0` for a
Marketplace action).

There is intentionally no standalone `bos-universal-release-kicker.yml`.
Release entry points are already owned by the workflow that has enough context
and authority to start them: Universal Launchpad calls
[`release.yml`](.github/workflows/release.yml) for artifact publication,
Marketplace repos use
[`bos-universal-gatekeeper-kicker.yml`](sync-files/workflows/bos-universal-gatekeeper-kicker.yml)
for operator-driven promotion, and this hub uses
[`release-hub.yml`](.github/workflows/release-hub.yml) for its own runtime
promotion. A generic release kicker would either duplicate those front doors
or need enough branching logic to blur their trust boundaries.

### Dev to production

For this hub, the production path is a manual dispatch of
[`release-hub.yml`](.github/workflows/release-hub.yml) from `dev`. It computes
or accepts a SemVer tag, builds the runtime allowlist, promotes that allowlist
to `main`, pushes the tag, publishes the GitHub Release, and optionally calls
[`repo-metadata-sync.yml`](.github/workflows/repo-metadata-sync.yml) against
the released tag. Consumers then use the promoted runtime from `@main` (or a
version tag).

For a Marketplace Action consumer, the production path is a manual
`operation: release` dispatch of the managed
[`bos-universal-gatekeeper-kicker.yml`](sync-files/workflows/bos-universal-gatekeeper-kicker.yml)
from the source branch. It validates trusted configuration, calls
[`release-promote.yml`](.github/workflows/release-promote.yml) to promote the
allowlist to the stable branch, publishes the GitHub Release, and optionally
calls [`repo-metadata-sync.yml`](.github/workflows/repo-metadata-sync.yml)
against the promoted tag. `operation: metadata` refreshes the same fields from
the configured stable branch without cutting another release.

For a product repository using Universal Launchpad, the launchpad owns the
artifact path: it calls [`release.yml`](.github/workflows/release.yml), which
publishes the configured Docker, Balena, and GitHub Release artifacts for an
already-approved version. It does not promote a `dev` branch to `main`.

There is deliberately no cross-workflow dependency requiring a Marketplace
release to wait for `release-hub.yml`. The hub release publishes this hub's
runtime; Marketplace promotion publishes a consumer Action's curated source
tree. Making one wait on the other would couple independent repositories,
create an unnecessary release deadlock, and would not prove that the
consumer's own validation passed. The Marketplace kicker already validates the
consumer before its release job; use protected environments or required
checks when an additional human approval gate is needed.

The Universal Launchpad retains a release-blocking scan. Scheduled and manual
releases need a fresh assessment even when no PR triggered the universal
security kicker. This is defense in depth at a different trust boundary, not a
second consumer security workflow. The kicker's `operation: security_only`
dispatch input runs just that scan (every publish/deploy stage forced off)
without removing or duplicating the PR-time security kicker — the two run at
different trust boundaries (pre-merge, read-mostly vs. release-time, publish
permissions) and neither can substitute for the other. Repositories that
enable both the security kicker's own `schedule` trigger and Launchpad's
default-on `security_scan.enable` should expect the same scan to run on both
cadences; disable one of the two schedules in `.github/bos-universal-config.json`
if that duplication isn't wanted.

The Launchpad kicker's leading `sync-check` job (managed-file sync in `commit`
mode) does not replace the standalone `bos-universal-sync-kicker.yml`: the
sync kicker is the only sync trigger for repositories without Launchpad, and
its push trigger has no branch restriction, while Launchpad's push trigger is
`main`-only. `sync-check` is skipped on `schedule` runs for this reason — the
sync kicker's own weekly cron and config-push trigger already own periodic
reconciliation, so Launchpad's 6h cron doesn't need to repeat it. On `push`/
`workflow_dispatch`, `sync-check` still runs, and only guarantees a given
Launchpad run isn't executed against a stale kicker file or config — if it
commits a change to either, this run defers to the fresh one the commit's
push retriggers.

The Marketplace kicker combines three event-scoped jobs in one managed file.
Its `pull_request_target` guard reads trusted default-branch configuration and
never executes PR-head code; its release job runs only by manual dispatch with
`contents: write`. Product-specific integration tests remain local.

## Consumer configuration

Create `.github/bos-universal-config.json` in the repository. A minimal
configuration can enable only the required stages:

```json
{
  "stages": {
    "docker": true,
    "balena": false,
    "github_release": true,
    "cloudflare_pages": false
  },
  "upstream": {
    "repo": "owner/project",
    "source": "github_release"
  },
  "docker": {
    "image_name": "project"
  },
  "gate": {
    "enable_node_lint": false,
    "enable_python_lint": false,
    "enable_shell_lint": true
  },
  "marketplace": {
    "enabled": false,
    "target_branch": "main",
    "allowlist_paths": ["action.yml", "README.md", "LICENSE"],
    "repo_metadata": {
      "enable": false,
      "homepage": "",
      "generate_topics": false
    }
  },
  "managed_file_sync": {
    "services": ["editorconfig"]
  }
}
```

The shared
[`universal-config`](.github/actions/universal-config/action.yml) action
validates and normalizes this file. Missing optional objects fall back to the
reusable workflow defaults. Marketplace `allowlist_paths`, `blocked_paths`,
`required_paths`, and `extra_sync_paths` accept JSON arrays of non-empty
strings. The normalizer converts arrays to the newline-delimited workflow API
used by the Marketplace guard and promotion workflows.

### Config sections

Launchpad, Marketplace, security, and action-test workflows read this file
through the shared
[`universal-config`](.github/actions/universal-config/action.yml)
action. Managed-file synchronization is the exception: the published
`bos-managed-file-sync-action` reads `managed_file_sync` directly. Settings can be authored as
flat top-level keys (as shown above, and required for anyone who already has
a config) or grouped under a named section per service; both layouts, and
any mix of the two, normalize identically. A flat key always wins over its
section-nested equivalent when both are present.

| Section (optional)  | Flat top-level key(s) it groups                                                                                                                           | Consumed by                                                 |
| ------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------- |
| `organization`      | `organization` (already the flat key name)                                                                                                                | every hub workflow, for runner topology and report policy   |
| `security`          | `gate`                                                                                                                                                    | `bos-universal-security.yml`                                |
| `managed_file_sync` | `managed_file_sync`                                                                                                                                       | `bos-universal-sync.yml` and `bos-managed-file-sync-action` |
| `launchpad`         | `upstream`, `stages`, `docker`, `scout`, `balena`, `companion_docker`, `release`, `platforms`, `security_scan`, `repo_metadata`, `cloudflare`, `triggers` | `bos-universal-gatekeeper.yml`                              |
| `marketplace`       | `marketplace` (already the flat key name)                                                                                                                 | `bos-universal-marketplace.yml`                             |
| `general`           | any key not owned by the shared workflow sections above (e.g. `action_test`)                                                                              | whichever workflow reads that key                           |

Unlike the other sections, `general` hoists every key it contains rather than
a fixed allowlist — it's the landing spot for a new standalone service's
config (like `bos-universal-action-test.yml`'s `action_test` block) before it
earns its own named section.

### Organization section

`organization` is the one section that is not owned by a single workflow. It
carries cross-cutting policy — runner topology, per-workflow overrides, and
report behavior — so runner labels and timeouts are data instead of a literal
repeated in every job:

```json
{
  "organization": {
    "runners": {
      "default": "ubuntu-latest",
      "x64": "ubuntu-latest",
      "arm64": "ubuntu-24.04-arm"
    },
    "reporting": {
      "enable_job_summary": true,
      "enable_annotations": true,
      "title_prefix": "Blackout Secure",
      "fail_on": "fail"
    },
    "defaults": {
      "timeout_minutes": 30
    }
  }
}
```

Config consumers can override per-workflow timeouts by adding entries under `workflows`; any unspecified workflows inherit the `defaults.timeout_minutes`. For example:

```json
{
  "organization": {
    "workflows": {
      "security": { "timeout_minutes": 20 },
      "sync": { "runs_on": ["self-hosted", "Linux"] }
    }
  }
}
```

| Key                                | Default                        | Meaning                                                                                   |
| ---------------------------------- | ------------------------------ | ----------------------------------------------------------------------------------------- |
| `runners.default`                  | `ubuntu-latest`                | Runner used by any workflow with no override.                                             |
| `runners.x64` / `runners.arm64`    | `runners.default`              | Architecture-specific labels for multi-arch build jobs.                                   |
| `reporting.enable_job_summary`     | `true`                         | `false` suppresses the `$GITHUB_STEP_SUMMARY` report.                                     |
| `reporting.enable_annotations`     | `true`                         | `false` suppresses `::error::` / `::warning::` annotations.                               |
| `reporting.enable_html`            | `true`                         | Generates the standalone HTML audit artifact.                                             |
| `reporting.enable_pdf`             | `false`                        | Attempts PDF export when Chromium or Chrome is installed; HTML remains the source report. |
| `reporting.html_path`              | `blackout-secure-report.html`  | Workspace path for the HTML report.                                                       |
| `reporting.pdf_path`               | `blackout-secure-report.pdf`   | Workspace path for an optional PDF export.                                                |
| `reporting.artifact_name`          | `blackout-secure-audit-report` | Authenticated GitHub Actions artifact name.                                               |
| `reporting.title_prefix`           | `Blackout Secure`              | Prefix applied to generated report titles.                                                |
| `reporting.fail_on`                | `fail`                         | `fail`, `warn`, or `never` — the severity tier that makes a report step exit non-zero.    |
| `defaults.timeout_minutes`         | `30`                           | Fallback job timeout.                                                                     |
| `workflows.<name>.runs_on`         | `runners.default`              | Per-workflow runner override.                                                             |
| `workflows.<name>.timeout_minutes` | `defaults.timeout_minutes`     | Per-workflow timeout override.                                                            |

Recognized workflow names are `security`, `sync`, `launchpad`, `marketplace`,
`action_test`, and `release`. Every one is always present in the normalized
`organization` output, so a workflow can read
`fromJSON(needs.resolve-config.outputs.org).workflows.<name>.runs_on`
unconditionally. A runner value may be a bare label or an array of labels;
both normalize to a value that `runs-on:` accepts directly, with no
`startsWith` guard in the workflow.

Two jobs deliberately keep a literal runner: each workflow's `resolve-config`
job (it bootstraps the runner topology) and the security workflow's `summary`
job (it is the required status check, so it must still publish a report when
config resolution itself failed).

### Run reporting

Every workflow reports through one shared surface,
[`job-report`](.github/actions/job-report/action.yml), which renders the
same audit layout used by `bos-code-scanning-kit` and
`bos-managed-file-sync-action`: a verdict, a severity-count table, recommended
actions, a `Configuration used` disclosure, and grouped findings tables — plus
matching workflow annotations. It also generates a standalone, responsive HTML
audit report with print CSS, provenance metadata, GitHub repository/run/commit
links, Blackout Secure branding, and a downloadable GitHub Actions artifact.
The HTML is the canonical rich report and can be printed to PDF from any
browser. When `reporting.enable_pdf` is enabled, the action additionally uses a
preinstalled Chromium/Chrome binary when available; a missing PDF engine is a
notice, not a failed audit.

Artifact access is authenticated by GitHub Actions and follows the permissions
of the workflow run. The report itself contains no credentials and does not
create a public URL. The generated report identifies [Blackout
Secure](https://blackoutsecure.app), the repository, ref, commit, workflow,
run, generation time, Apache License 2.0 metadata, and an open-source notice:
automated findings are operational guidance, not a security certification,
legal opinion, warranty, or substitute for qualified human review.

`job-report` is intentionally self-contained (pure Python/bash, no dependency
on any other action in this repo) and its branding, license notice, and
"Automation policy" section are configurable — `brand_name`, `brand_url`,
`license_notice`, and `enable_automation_policy` (defaults reproduce today's
output exactly, so no existing caller changes). It can also emit a
machine-readable JSON report (`enable_json`/`json_path`) alongside the
Markdown/HTML, and optionally upload the generated files as a workflow
artifact itself (`enable_artifact_upload`/`artifact_name`) instead of every
caller wiring its own `actions/upload-artifact` step.

Findings are pure data, so a workflow only builds a JSON array and the report
surface stays identical everywhere:

```json
[
  {
    "id": "SG021",
    "severity": "fail",
    "control": "Code scanning + posture audit",
    "evidence": "job result: failure",
    "remediation": "Review the SARIF findings on the Security tab.",
    "group": "security"
  }
]
```

| Severity | Report label | Meaning                                              |
| -------- | ------------ | ---------------------------------------------------- |
| `pass`   | Pass         | Control satisfied.                                   |
| `warn`   | Warning      | Advisory drift; review recommended but not blocking. |
| `fail`   | High         | Required control failed.                             |
| `skip`   | Not Assessed | Not evaluated; no verdict can be inferred.           |

A skipped gate reports as `Not Assessed` rather than a pass, so a report never
implies coverage the run did not actually provide. The action's `outcome`
output (`success`, `warn`, `failure`) reflects severity only and does not
change with `fail_on`, so a caller can gate on the verdict independently of
whether the report step itself exited non-zero.

For example, the sample above can equivalently be written grouped:

```json
{
  "launchpad": {
    "stages": { "docker": true, "balena": false, "github_release": true },
    "upstream": { "repo": "owner/project", "source": "github_release" },
    "docker": { "image_name": "project" }
  },
  "security": {
    "enable_node_lint": false,
    "enable_python_lint": false,
    "enable_shell_lint": true
  },
  "marketplace": {
    "enabled": false,
    "target_branch": "main",
    "allowlist_paths": ["action.yml", "README.md", "LICENSE"]
  },
  "managed_file_sync": {
    "services": ["prettier"]
  },
  "general": {
    "action_test": { "python_versions": ["3.11", "3.12"] }
  }
}
```

## Managed files

[`bos-universal-sync.yml`](.github/workflows/bos-universal-sync.yml) is a thin
event and commit wrapper around the published
[`bos-managed-file-sync-action`](https://github.com/blackoutsecure/bos-managed-file-sync-action).
The published action reads the `managed_file_sync` block from
[`bos-universal-config.json`](.github/bos-universal-config.json), resolves its catalog,
and reconciles the working tree. Canonical hub templates live under
[`sync-files/`](sync-files/); this repository no longer contains a local
sync engine or service registry. The global hub policy enables the
organization-wide `shellcheck`, Security kicker, and Sync kicker defaults;
repository-specific kicker definitions are available globally but must be
selected by each repository that needs them. It also sets
`take_over_managed_files: true`, allowing organization-owned managed blocks to
replace competing managed blocks from another namespace.

Service ownership modes:

- **Section:** preserves user content outside managed markers.
- **File:** continuously replaces a file with its canonical template.
- **Init-if-missing:** creates a starter once and leaves later edits alone.

### Supported sync services

The published action's default catalog currently includes `baseline`,
`codeowners`, `common`, `dependabot_actions`, `editorconfig`, `lf_line_endings`,
`license`, `markdownlint`, `notice_apache2`, `prettier`, and `shellcheck`.
Repos can extend or override the catalog with `service_definitions` or a
separate catalog file; the published action validates service conflicts before
writing anything.

See [`sync-files/README.md`](sync-files/README.md) for template ownership
and branch policy.

### Minimum sync workflow policy

GitHub requires an event-trigger workflow in each repository; a configuration
file cannot schedule a cross-repository reusable workflow by itself. Enable
the published managed-file sync action wherever managed files should be
maintained. It runs independently from release, security, and Marketplace
workflows.

- delivery repositories can use the published action independently of
  `bos_launchpad`;
- repositories without delivery use the same managed-file sync wrapper;
- this hub uses the managed Sync kicker as the event front door, which calls
  the reusable [`bos-universal-sync.yml`](.github/workflows/bos-universal-sync.yml)
  workflow.

Removing every consumer workflow would require a separate organization-wide
GitHub App or PAT-backed controller with write access to all repositories. That
larger trust boundary is intentionally not part of managed-file sync.

## Marketplace Action enrollment

All repositories should enable `bos_universal_security`. Marketplace Action
repositories additionally enable `bos_universal_marketplace`. Keep
product-specific test workflows local. Remove generic lint, Marketplace CI,
guard, and release wrappers only after the required managed kickers have been
installed from the hub's canonical templates or from an organization catalog;
the public sync action's default catalog does not include these hub-specific
workflow files.

For `blackoutsecure/bos-upstream-watcher`, retain `.github/workflows/test.yml`
because it owns the 3-OS by 3-Python matrix and live npm smoke test. Remove
`.github/workflows/lint.yml`, `.github/workflows/marketplace-ci.yml`,
`.github/workflows/marketplace-guard.yml`, and `.github/workflows/release.yml`.
Use this consumer configuration:

```json
{
  "gate": {
    "enable_lint": true,
    "enable_python_lint": true,
    "python_version": "3.12",
    "enable_shell_lint": false
  },
  "marketplace": {
    "enabled": true,
    "target_branch": "main",
    "allowlist_paths": ["action.yml", "src", "README.md", "LICENSE", "NOTICE"],
    "blocked_paths": [
      ".github/workflows/",
      ".editorconfig",
      ".gitattributes",
      ".gitignore",
      ".markdownlint.yaml",
      "pyproject.toml",
      "test/"
    ],
    "required_paths": [
      ".github/dependabot.yml",
      "action.yml",
      "src",
      "LICENSE",
      "NOTICE",
      "README.md"
    ],
    "include_dependabot_config": true,
    "include_github_metadata": false
  },
  "managed_file_sync": {
    "services": [
      "common",
      "lf_line_endings",
      "dependabot_actions",
      "editorconfig",
      "shellcheck"
    ]
  }
}
```

Marketplace validation loads the hub's strict
[`marketplace-kit-global-config.json`](sync-files/config/marketplace-kit-global-config.json)
policy and then the repository's `marketplace_kit` block. The global policy
inherits organization community-health files and defers GHAS and Security
DevOps posture rules to `bos-code-scanning-kit`. It also audits GitHub
Sponsors at `warn` (`require_sponsorship`): the SP### rules report whether
the `blackoutsecure` organization has an approved sponsors listing, whether
the inherited `blackoutsecure/.github` `FUNDING.yml` routes the Sponsor
button at it (`funding_source: inherit`), and whether the button actually
renders on each repository. Set a per-repository `marketplace_kit` field
only to override a specific policy.

For `blackoutsecure/bos-code-scanning-kit`, merge this policy into its existing
`marketplace` object to replace the repository-local post-release workflow:

```json
{
  "marketplace": {
    "repo_metadata": {
      "enable": true,
      "homepage": "https://github.com/marketplace/actions/blackout-secure-code-scanning-kit",
      "generate_topics": true,
      "topics_fallback": "github-actions code-scanning security sarif posture-audit gitleaks actionlint shellcheck composite-action devsecops github-advanced-security"
    }
  }
}
```

The source branch defaults to `github.event.repository.default_branch`; only
the stable Marketplace target remains explicitly `main`. GitHub Actions does
not support expressions in reusable-workflow `uses:` refs, so managed callers
continue to consume promoted hub runtime at `@main`.

Marketplace metadata synchronization is opt-in through
`marketplace.repo_metadata.enable`. Real writes prefer `REPO_ADMIN_PAT` and
fall back to `RELEASE_PAT`; the selected token needs `Administration: write`
and `Metadata: read` on the consumer repository. With neither secret, the
metadata stage succeeds as a documented skip so an already-published release
is not retroactively failed. Dispatch `operation: metadata` with `dry_run:
true` to preview README-derived values using the scoped `GITHUB_TOKEN` without
granting repository-administration authority.

## Workflow API

Consumer repositories use the managed
[`bos-universal-gatekeeper-kicker.yml`](sync-files/workflows/bos-universal-gatekeeper-kicker.yml)
as their single event receiver. It reads `.github/bos-universal-config.json` and
invokes the appropriate hub
entry points:

| Entry point                                                                      | Purpose                                                                      |
| -------------------------------------------------------------------------------- | ---------------------------------------------------------------------------- |
| [`bos-universal-gatekeeper.yml`](.github/workflows/bos-universal-gatekeeper.yml) | Coordinate trusted release, deployment, security, and metadata stages.       |
| [`bos-universal-security.yml`](.github/workflows/bos-universal-security.yml)     | Aggregate read-mostly PR and merge-queue validation into one required check. |
| [`bos-universal-sync.yml`](.github/workflows/bos-universal-sync.yml)             | Reconcile managed files without invoking delivery or policy workflows.       |

The following reusable workflows are stage modules, not additional files that
consumer repositories must install. Universal and the specialized promotion
workflows call them from the promoted `@main` runtime. They remain separate
because reusable jobs provide job-level permissions, outputs, matrices,
concurrency, and focused validation; inlining them would reduce file count but
would not reduce Actions jobs or runner usage.

| Workflow                                                                         | Purpose                                                                                                                            |
| -------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------- |
| [`monitor-upstream-release.yml`](.github/workflows/monitor-upstream-release.yml) | Universal stage for upstream version discovery and tracking-state updates using the config-aware `bos-upstream-watcher` composite. |
| [`release.yml`](.github/workflows/release.yml)                                   | Artifact-release composition stage; also supports direct tag-driven releases without upstream monitoring.                          |
| [`docker-build-push.yml`](.github/workflows/docker-build-push.yml)               | Release leaf for multi-architecture Docker publication.                                                                            |
| [`balena-block-publish.yml`](.github/workflows/balena-block-publish.yml)         | Release leaf for Balena block publication.                                                                                         |
| [`github-release.yml`](.github/workflows/github-release.yml)                     | Shared publisher used by artifact, Marketplace, and hub releases.                                                                  |
| [`deploy-cloudflare-pages.yml`](.github/workflows/deploy-cloudflare-pages.yml)   | Universal stage for Cloudflare Pages build and deployment.                                                                         |
| [`security-scan.yml`](.github/workflows/security-scan.yml)                       | Shared scanning stage used by trusted delivery and pre-merge validation.                                                           |
| [`repo-metadata-sync.yml`](.github/workflows/repo-metadata-sync.yml)             | Shared About-box synchronization stage used by hub, Launchpad, and Marketplace publication.                                        |
| [`bos-universal-sync.yml`](.github/workflows/bos-universal-sync.yml)             | Thin event and commit wrapper around the published managed-file sync action.                                                       |

The upstream monitor loads organization-wide watcher defaults from
[`sync-files/config/upstream-watcher-global-config.json`](sync-files/config/upstream-watcher-global-config.json)
and merges repository-specific `upstream_watcher` settings from
`.github/bos-universal-config.json` above it. Keep upstream identifiers and
tracker paths in the repository config; keep shared behavior and AI/report
defaults in the global file.

The upstream monitor pins the current watcher runtime and passes the caller's
`.github/bos-universal-config.json` through its config cascade. Add an
`upstream_watcher` section there to configure provider-specific discovery,
tracker behavior, and advisory AI settings without expanding another workflow
input map. The monitor preserves the watcher's canonical label, update type,
AI impact/status, and package metadata outputs for downstream reporting while
keeping tracker commits and downstream dispatch in the hub wrapper.

Specialized reusable entry points remain separate when their event or mutation
contract does not belong in Universal:

| Specialized workflow                                                               | Boundary                                                           |
| ---------------------------------------------------------------------------------- | ------------------------------------------------------------------ |
| [`bos-universal-marketplace.yml`](.github/workflows/bos-universal-marketplace.yml) | Marketplace validation nested by the Marketplace kicker.           |
| [`marketplace-repo-guard.yml`](.github/workflows/marketplace-repo-guard.yml)       | Trusted-target enforcement for workflow-free Marketplace branches. |
| [`release-promote.yml`](.github/workflows/release-promote.yml)                     | Allowlisted Marketplace branch promotion.                          |
| [`balena-fleet-deploy.yml`](.github/workflows/balena-fleet-deploy.yml)             | Per-fleet deployment matrix, distinct from block publication.      |
| [`nginx-config-validate.yml`](.github/workflows/nginx-config-validate.yml)         | Standalone Nginx configuration validation.                         |

## Shared actions

Reusable implementation components live under
[`.github/actions/`](.github/actions/) and include release-context and release-tag
resolution, Docker tag, build-argument, and manifest handling, Docker Scout
scanning, Balena rendering and publishing, Cloudflare project/zone helpers,
config normalization, and safe commit/push behavior.

Workflows should reuse these composites when behavior crosses more than one
workflow. Workflow-specific orchestration remains in the owning workflow.

### Balena deployment boundary

Balena publication is consolidated in
[`balena-publish`](.github/actions/balena-publish/action.yml), while the
two reusable workflows retain separate caller contracts:

- [`balena-block-publish.yml`](.github/workflows/balena-block-publish.yml)
  resolves block versions and optionally renders or commits `balena.yml`;
- [`balena-fleet-deploy.yml`](.github/workflows/balena-fleet-deploy.yml)
  validates a target set and deploys it as a per-fleet matrix.

These workflows should not be merged into a mode-driven input surface. Their
shared operation is one `balena push`; their versioning, mutation, outputs, and
concurrency contracts are different.

The official
[`balena-io/deploy-to-balena-action@v2.3.1`](https://github.com/balena-io/deploy-to-balena-action/releases/tag/v2.3.1)
supports release reuse, layer caching, custom sources and Dockerfiles, registry
secrets, custom environments, draft/final release handling, and release
outputs. It remains a Docker action containing the x64 standalone CLI, so it
cannot run reliably inside containerized self-hosted runners whose workspace
path is not visible to the host Docker daemon. The shared composite invokes the
same supported `balena push` path without a nested container, installs the
native x64 or ARM64 CLI, and tracks the official action's CLI pin (`v24.1.4`).

## Required variables and secrets

Common organization or repository variables:

- `DEFAULT_RUNNER`: runner label or JSON label array;
- `RUNNER_X64`, `RUNNER_ARM64`: optional architecture-specific runners;
- `DOCKERHUB_NAMESPACE`, `BALENA_NAMESPACE`: publishing namespaces.

Common secrets are stage-dependent:

- Docker: `DOCKERHUB_USERNAME`, `DOCKERHUB_TOKEN`;
- Balena: `BALENA_API_TOKEN`;
- private same-organization upstreams: `GATEWALL_APP_ID` + `GATEWALL_APP_PRIVATE_KEY`;
- Cloudflare: `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`, optionally
  `CLOUDFLARE_ZONE_ID` and `CLOUDFLARE_PAGES_ADMIN_TOKEN`;
- repository administration, security audit, workflow propagation, dispatch,
  release, and same-organization upstream reads: `GATEWALL_APP_ID` +
  `GATEWALL_APP_PRIVATE_KEY`.

The corresponding `*_PAT` secrets remain temporary compatibility fallbacks
for migration. Do not create new PATs for these capabilities. After each App
profile is installed and its workflow succeeds, delete the replaced PAT secret.

### Secrets pipelining strategy

Guidance for anyone who consumes, forks, or self-hosts these reusable
workflows and needs to provision credentials (GitHub, Docker Hub, Cloudflare,
Balena). It deliberately does not list this organization's own internal
secret names, App IDs, or values beyond what's already in
["Required variables and secrets"](#required-variables-and-secrets) above —
there's no benefit to publishing more than that, and it would only hand a
would-be attacker a ready-made target list. It is not synced into consumer
repositories as a separate file; those repos instead carry a short pointer
back to this section (`security_readme_pointer` under
["Supported sync services"](#supported-sync-services)).

Most public users **do not need any of this**. If you only consume a
published Marketplace action (for example `bos-humanstxt-generator` or
`bos-sitemap-generator`) in your own workflow, you only need the
secrets/inputs documented in that action's own `README.md` / `action.yml` —
usually none, or a scoped Cloudflare token if you opt into Cloudflare Pages
deployment.

#### Secret tiers, least privilege first

| Tier                                                                 | Use for                                                                                                                                                                                                  |
| -------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Repository secret**                                                | Single-repo, low-blast-radius values.                                                                                                                                                                    |
| **Environment secret** (with required reviewers)                     | Anything that can push, publish, or deploy — adds a manual-approval gate.                                                                                                                                |
| **Organization secret**, scoped to selected repositories             | A credential shared by several repos (Docker Hub, Balena) — one rotation point instead of N.                                                                                                             |
| **Enterprise secret**                                                | Only for values every org in the enterprise needs; rarely applicable here.                                                                                                                               |
| **Codespaces secret** (user or org, scoped to selected repositories) | A value a `.devcontainer` needs at development time. Entirely separate store from Actions secrets, even when the name matches — must be added again if a devcontainer needs it. No repo ships one today. |

Default to the narrowest tier that still avoids duplicate rotation work.

#### GitHub App vs. Personal Access Token

**Use a purpose-scoped GitHub App wherever the credential only needs to talk
to the GitHub API** (dispatch, contents, PR, checks, org/team reads). An App
installation token is minted per run, expires in about an hour, and no human
ever rotates it — reserve PATs strictly for capabilities a GitHub App cannot
perform (most notably enterprise-owner reads). GitHub Apps cannot
authenticate to external providers (Docker Hub, Cloudflare, Balena), so those
still need the provider's own scoped token.

Keep authorization separate from automation. The read-only Gatekeeper App
resolves organization membership before privileged manual jobs run. One
Gatewall automation App holds the repository capabilities, while every job
mints an attenuated token containing only the permissions needed for that
operation. This is the minimum two-App architecture without turning the
authorization credential itself into an organization-wide writer.

To set one up: create the App with only the permission the capability needs,
disable its webhook (it only mints tokens), generate a private key, store the
App ID as a **variable** and the private key as an **environment secret**
with required reviewers, install it only on the repositories that need it,
then mint a token in the workflow with
[`actions/create-github-app-token`](https://github.com/actions/create-github-app-token)
(wrapped here as
[`automation-app-token`](.github/actions/automation-app-token/action.yml))
in place of a PAT.

The loopback-only
[`purpose-scoped App setup helper`](tools/gatekeeper-app-setup/README.md)
automates manifest creation, organization variable/secret configuration, and
installation verification. Available profiles and minimum permissions are:

| Profile      | Credentials                                       | GitHub App permissions                                                                                                                         |
| ------------ | ------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------- |
| `gatekeeper` | `GATEKEEPER_APP_ID`, `GATEKEEPER_APP_PRIVATE_KEY` | Organization Members: read                                                                                                                     |
| `gatewall`   | `GATEWALL_APP_ID`, `GATEWALL_APP_PRIVATE_KEY`     | Actions, Administration, Contents, Pull requests, Workflows: write; secret scanning alerts and Dependabot alerts: read; Security events: write |

Keep Gatewall separate from Gatekeeper. Never add repository write permissions
to the dispatcher-authorization App.

#### Provider setup walkthroughs

**Docker Hub** — Account Settings → Security → New Access Token, scoped
**Read & Write** to the specific namespace/repository if your plan supports
it. Store as `DOCKERHUB_TOKEN` (environment secret) and `DOCKERHUB_USERNAME`
(variable). Rotate by generating a new token and deleting the old one.

**Cloudflare** — My Profile → API Tokens → Create Token → Custom Token,
scoped to only what the workflow needs (e.g. Account → Cloudflare Pages →
Edit). Store as `CLOUDFLARE_API_TOKEN`; `CLOUDFLARE_ACCOUNT_ID` and
`CLOUDFLARE_ZONE_ID` are **not secret** — store as variables. Set a token
expiry and rotate before it lapses. Cloudflare's own "Secrets Store" product
is a Workers/Pages runtime secret store, not a CI credential store, and isn't
used here.

**Balena** — balenaCloud dashboard → Preferences → Access tokens, preferring
a fleet-scoped key over an account-wide one. Store as `BALENA_API_TOKEN`
(environment secret). Rotate by minting a new key and revoking the old one.

#### Rotation summary

| Credential class                        | Rotation                                                                                               |
| --------------------------------------- | ------------------------------------------------------------------------------------------------------ |
| GitHub App installation tokens          | Automatic — minted per run, expire in ~1 hour, nobody rotates them.                                    |
| Fine-grained PATs                       | Manual; replace with a purpose-scoped GitHub App wherever the credential only talks to the GitHub API. |
| Docker Hub / Balena / Cloudflare tokens | Manual, provider-side; set the shortest TTL the provider allows and calendar-reminder before expiry.   |

### Elevated posture scanning (`GATEWALL_APP`)

The code-scanning kit's posture probes (secret-scanning enablement, Dependabot
alerts enablement, push-protection visibility, branch-protection drift) need
Administration/security read access that the default `GITHUB_TOKEN` does not
have; without it those probes report indeterminate rather than `pass`/`warn`/
`fail`.

1. Run the setup helper with `-Profile gatewall`.
2. Review the generated minimum permissions and install it for all managed
   repositories.
3. Re-run the workflow and confirm the previously indeterminate rows now show
   `pass`, `warn`, or `fail`.
4. Delete the legacy `SCANNING_PAT` after successful verification.

No consumer wiring is required beyond creating the secret:

- [`bos-universal-security.yml`](.github/workflows/bos-universal-security.yml)'s
  `code-scan` job always passes
  a short-lived App token first, then the legacy PAT or `GITHUB_TOKEN`, and both
  managed kickers already forward `secrets.SCANNING_PAT` unconditionally — the
  PAT is used automatically the moment the secret exists, with no config
  change needed.
- [`bos-universal-gatekeeper.yml`](.github/workflows/bos-universal-gatekeeper.yml)'s
  security-scan stage additionally requires
  `launchpad.security_scan.use_advanced_pat: true` (or the flat
  `security_scan.use_advanced_pat` equivalent) in
  [`bos-universal-config.json`](.github/bos-universal-config.json) — this hub ships
  that flag enabled by default. It is a documented no-op when `SCANNING_PAT`
  is absent (the kit transparently falls back to `GITHUB_TOKEN`), so enabling
  it ahead of provisioning the secret is safe.

### Workflow-file propagation (`GATEWALL_APP`)

`GITHUB_TOKEN` can never push changes to `.github/workflows/**` — this is a
hard GitHub platform restriction, not something a workflow's `permissions:`
block can grant. Without a PAT, `bos-universal-sync.yml` skips the five
managed kicker workflow files
(`bos-universal-action-test-kicker.yml`, `bos-universal-gatekeeper-kicker.yml`,
`bos-universal-marketplace-kicker.yml`, `bos-universal-security-kicker.yml`,
`bos-universal-sync-kicker.yml`) and syncs every other managed file normally.

1. Run the setup helper with `-Profile gatewall` if Gatewall is not already installed.
2. Install it for all repositories receiving managed workflow files.
3. Re-run managed sync and confirm workflow-file propagation succeeds.
4. Delete the legacy `WORKFLOW_SYNC_PAT`.

### Org-wide kicker fan-out (`GATEWALL_APP`)

[`bos-org-kicker-fanout.yml`](.github/workflows/bos-org-kicker-fanout.yml) runs a
universal kicker across every repository in the organization, and
[`bos-hub-managed-sync-propagate.yml`](.github/workflows/bos-hub-managed-sync-propagate.yml)
dispatches it automatically whenever `sync-files/**` changes. The job-scoped
`GITHUB_TOKEN` cannot dispatch workflows in other repositories, so the fan-out
fails closed with an explicit error until a credential exists.

Run the setup helper with `-Profile gatewall`, install it for all participating
repositories, and verify a dry-run fan-out. `ORG_KICK_PAT` and `DISPATCH_TOKEN`
remain temporary compatibility fallbacks only.

Targets are enumerated from the organization API rather than a hardcoded list.
Participation is opt-out, not opt-in: a repository declines by setting its own
`AUTO_HUB_SYNC` Actions variable to `false`.

#### Seeding repositories that have no kicker

The fan-out delivers by dispatching each target's _own_ copy of the kicker, so
a repository that has never received one has nothing to dispatch and can never
be reached. The `seed_missing` input (default `pr`) closes that bootstrap gap:
for each participating repository with no kicker, it opens a pull request
installing [`sync-files/workflows/`](sync-files/workflows/) into that
repository's **default branch**, then skips dispatch for that run. The next
fan-out picks the repository up once the pull request merges, and the managed
file sync (`file` mode) keeps the copy current from then on.

The default branch is the correct and only useful target: GitHub fires
`schedule` triggers, and offers `workflow_dispatch`, only for workflows present
on the default branch. Seeding never pushes directly, so branch protection
stays authoritative.

Seeding reuses `WORKFLOW_SYNC_PAT` rather than widening `ORG_KICK_PAT` — the
dispatch token has no reason to hold write access to code. That token needs
**Contents: Read and write**, **Workflows: Read and write** and **Pull
requests: Read and write** on the targets. If it is absent or under-scoped,
the run logs a warning, marks those repositories as seed failures, and
continues; nothing else in the fan-out is affected. Set `seed_missing: off` to
leave repositories without a kicker untouched.

Run with `dry_run: true` (the manual default) to preview which repositories
would be dispatched and which would be seeded, without writing anything.

## Development and validation

Run the repository contract before promotion:

```bash
python3 scripts/test_universal_config_contract.py
python3 scripts/test_sync_action_pins.py
python3 -m py_compile scripts/test_universal_config_contract.py
git diff --check
```

The contract verifies universal config and gate input forwarding, managed-service
output, branch/ref ownership, semantic runtime promotion, and internal README
links. [`lint.yml`](.github/workflows/lint.yml) runs it in CI.

### First-party action pins

Hub references to first-party Blackout Secure actions stay pinned to immutable
commit SHAs, each carrying a `# vX.Y.Z` provenance comment. `uses:` cannot
contain expressions, so "always use the latest tag" is resolved out-of-band
rather than at run time — which keeps `PS012` (`require_pinned_actions`),
Marketplace SC002 hygiene, and CodeQL `actions/unpinned-tag` satisfied.

[`sync-action-pins.yml`](.github/workflows/sync-action-pins.yml) runs daily,
resolves the newest tag for every action listed in the `action_pins` section of
[`bos-universal-config.json`](.github/bos-universal-config.json), rewrites any stale pin, and
opens a PR. Resolution ranks stable releases **and** pre-releases together by
SemVer precedence, because `GET /releases/latest` silently excludes
pre-releases and would otherwise report an older version.

Set `action_pins.channel` to `prerelease-preferred` (or the `pre-latest`
alias) to prefer the newest prerelease. If no matching prerelease exists, the
resolver falls back to the newest stable release and still records its commit
SHA. This is preferred to a literal `@pre-latest`, which GitHub treats as an
ordinary branch or tag rather than a special selector.

SHA mode is the default. A manifest entry may explicitly set
`"ref_mode": "latest"` when a floating reference is required; the pin bumper
leaves that entry unchanged, and the pinned-actions gate permits only the
literal `owner/repository@latest` when that repository is passed through its
`latest_repositories` input. This is an intentional exception to immutable
pinning and should be limited to trusted, reviewed dependencies.

```bash
python3 scripts/sync_action_pins.py --check   # report drift, exit 1 when stale
python3 scripts/sync_action_pins.py --write   # rewrite pins in place
```

Bumping requires `WORKFLOW_SYNC_PAT`, since `GITHUB_TOKEN` can never write to
`.github/workflows/**`. Without it the job reports drift and exits cleanly.
The shared
[`resolve-latest-action-ref`](.github/actions/resolve-latest-action-ref/action.yml)
composite exposes the same resolution to any workflow that needs a tag or SHA.

## License

[Apache License 2.0](LICENSE)
