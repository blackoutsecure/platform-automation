# Managed files

This directory contains canonical hub templates published with the repository.
Managed-file synchronization is provided by
[`bos-managed-file-sync-action`](https://github.com/blackoutsecure/bos-managed-file-sync-action).
Consumer repositories select its services in the `managed_file_sync` section
of `.github/bos-universal-config.json`. The global policy enables the single
organization-wide `bos_universal_gatekeeper_kicker` receiver alongside generic
maintenance services. It also sets `take_over_managed_files: true` so
organization-owned blocks can replace competing managed blocks.
Small changes to inherited Marketplace-managed files use the ordered
`managed_file_sync.file_patches` setting rather than redefining the complete
service. The global policy uses this to replace the Marketplace `.vscode/*`
exception with `.vscode/` while retaining the rest of the shared `.gitignore`
baseline.
This repository's own sync wrappers use `.github/bos-universal-config.json` as
the repo layer and check out the shared global policy at
`sync-files/config/managed-file-sync-global-config.json`.
The upstream monitor also loads
`sync-files/config/upstream-watcher-global-config.json` and merges any
repository-specific `upstream_watcher` section from the universal config.
The Cloudflare deploy workflow loads
`sync-files/config/sitemap-generator-global-config.json`,
`sync-files/config/securitytxt-generator-global-config.json`,
`sync-files/config/robotstxt-generator-global-config.json`,
`sync-files/config/humanstxt-generator-global-config.json`, and
`sync-files/config/web-manifest-generator-global-config.json` as the global
tier for the site generators' built-in compliance audits.
Settings may be authored as flat top-level keys or grouped under a named
section per service (`launchpad`, `marketplace`, `security`, plus a
`general` catch-all for anything else) — see the ["Config sections"](../README.md#config-sections)
table in the hub README for the full key mapping; this file doesn't repeat it.

## Active templates

Canonical workflow templates live under [`workflows/`](workflows/). These
files remain the normal authoring and review surface for hub-specific release
content. Generic dotfile services (`shellcheck`, `yamllint`, `markdownlint`,
`editorconfig`, `common`, `lf_line_endings`, `prettier`) are defined inline
(`content_lines`) in `bos-managed-file-sync-action`'s own bundled marketplace
config — there is no hub-local dotfile source directory to maintain.

- [`bos-universal-gatekeeper-kicker.yml`](workflows/bos-universal-gatekeeper-kicker.yml)
  is the sole managed receiver and manual-dispatch front door. It delegates to
  the promoted hub runtime, whose selected operation runs the appropriate
  security, sync, action-test, Marketplace, upstream, metadata, or release
  behavior.

These workflows are file-owned managed. Consumer repositories must not edit
them directly. A repository opts into the callers it needs, for example:

```json
{
  "managed_file_sync": {
    "services": ["shellcheck"]
  }
}
```

Enable the published managed-file sync action alongside whichever other
managed callers the repository needs. GitHub still requires one event-trigger
workflow per repository; `.github/bos-universal-config.json` controls sync behavior
through `managed_file_sync`. The `editorconfig` service, like the other
generic dotfile services, is provided by the published action's default
catalog — the hub only defines its own workflow and community-health/
github-meta/org-profile content here.

## Ownership modes

The sync engine supports three ownership modes:

- **Section:** replaces only content between managed markers.
- **Whole-file:** continuously overwrites the complete target file.
  Consumers install these thin managed callers, not the hub's stage-level
  reusable workflows. The callers invoke the promoted Universal and pre-merge
  gate entry points at `@main`; the hub keeps their internal stage composition
  centralized and independently testable.
- **Init-if-missing:** creates a starter file once and never overwrites it.

The published action's catalog is authoritative for generic services. Hub-only
workflow templates are maintained here and are not part of the public sync
catalog.

## Organization defaults

[`community-health/`](community-health/), [`github-meta/`](github-meta/), and
[`org-profile/`](org-profile/) are canonical sources for the dedicated
`blackoutsecure/.github` repository. Enable the whole-file `org_defaults`
service there and set this in the JSON config:

```json
{
  "general": {
    "target_repo_role": "org-default-repo"
  }
}
```

The role check prevents these files, especially `profile/README.md`, from
being copied into normal product repositories. Product repositories use the
default `target_repo_role: consumer` and inherit community-health files and
templates from GitHub's organization repository.

The organization-default service is selected only by the dedicated
`blackoutsecure/.github` repository's `.github/bos-universal-config.json`.
Its targets are the repository root files
`CODE_OF_CONDUCT.md`, `CONTRIBUTING.md`, `SECURITY.md`, and `SUPPORT.md`;
`.github/FUNDING.yml`; `.github/PULL_REQUEST_TEMPLATE.md`;
`.github/ISSUE_TEMPLATE/*`; and `profile/README.md`.
Unlike the other community-health files, `FUNDING.yml` is only read from the
`.github` folder — a root or `docs/` copy is silently ignored, so the Sponsor
button would not inherit.
These files are not copied into product repositories because GitHub already
uses the organization repository's community-health and template files as
defaults there. Product repositories should retain only repository-specific
metadata such as `CODEOWNERS`, dependabot policy, workflows, and universal
configuration.

## Security & secrets pointer service

The full secrets guide (secret tiers, GitHub App vs. PAT guidance, GitHub App
setup walkthrough, and per-provider setup walkthroughs for Docker Hub,
Cloudflare, and Balena) lives in one place: the hub README's
["Secrets pipelining strategy"](../README.md#secrets-pipelining-strategy)
section. It is not duplicated into every consumer repository — most
repositories only ever need the secrets/inputs already documented in their
own `README.md` / `action.yml`, so shipping the full generic guide everywhere
would mostly be noise.

Instead, the `security_readme_pointer` service (enabled by default alongside
`license_service`) appends a short block to each consumer repository's
`README.md` linking back to that section, for the minority of forks/operators
who do need to provision their own credentials.

A repository that doesn't want the pointer block must disable the service:

```json
{
  "managed_file_sync": {
    "disabled_services": ["security_readme_pointer"]
  }
}
```

## License service

GitHub's organization `.github` repository cannot provide a default license;
each repository must carry its own `LICENSE` file for GitHub license detection,
source archives, clones, and package downloads. The hub therefore standardizes
repository licensing through the managed-file sync service named
`license_service`.

The global policy enables `license_service` by default and owns `LICENSE` in
whole-file mode from [`legal/LICENSE`](legal/LICENSE). Repositories that need a
different approved license must opt out explicitly and provide their own
`LICENSE`:

```json
{
  "managed_file_sync": {
    "disabled_services": ["license_service"]
  }
}
```

The service name is intentionally license-agnostic. If the organization-wide
standard changes later, keep `license_service` selected and update only the
service definition/template in this hub.

### Proprietary license service

Internal and commercial repositories that are not published as a reusable
Action, package, or library carry the Blackout Secure Proprietary License
instead. `proprietary_license_service` owns `LICENSE` in whole-file mode from
[`legal/LICENSE-PROPRIETARY`](legal/LICENSE-PROPRIETARY), so those repositories
stay byte-identical to one standard the same way Apache-2.0 repositories do.

It is opt-in and deliberately absent from the default service list. Both
services own `LICENSE` in `file` mode, and the sync engine rejects two
non-`block` services claiming one path, so a repository selecting this one must
disable `license_service` in the same config or the run fails with an
ambiguous-ownership error:

```json
{
  "managed_file_sync": {
    "services": ["proprietary_license_service"],
    "disabled_services": ["license_service"]
  }
}
```

Repositories on the launchpad express the same thing under `sync_files` in
`bos-launchpad-config.json`.

Two constraints apply before selecting it. A repository listed on the GitHub
Marketplace must carry an OSI-approved license, and `bos-marketplace-kit`
enforces `allowed_licenses: ["Apache-2.0"]` with `require_license_audit: fail`,
so a Marketplace Action can never use this service. A repository that
redistributes a copyleft dependency cannot use it either; its license is
inherited, not chosen.

Third-party components are not covered by the proprietary grant. Any repository
using this service that bundles, vendors, or loads a third-party component must
record that component's license in its own `NOTICE` or `NOTICE.md`.

## Kicker push triggers

`bos_universal_gatekeeper_kicker` fires on `push` to both `dev` and `main`,
and carries no `on.push.paths` filter. GitHub parses the `on:` block before
any job runs and cannot evaluate expressions there, so a single `file`-mode
managed template cannot express a path list that fits a Docker image repo, a
Node Action, and a static site at the same time.

Relevance is decided instead by the kicker's `changed-paths` job, which reads
an allowlist from the consumer's own `.github/bos-universal-config.json`:

```json
{
  "triggers": {
    "push_paths": ["action.yml", "src/**", "dist/**", "test/**"]
  }
}
```

The key is accepted at top level or under `launchpad`, matching how
`universal-config` hoists the other launchpad subkeys. Patterns are shell
globs matched against `git diff --name-only` between the push's before and
after commits.

Behaviour worth knowing:

- An absent, empty, or unreadable list means "run on every push", so a repo
  that has not opted in keeps working.
- `.github/workflows/bos-universal-gatekeeper-kicker.yml` and
  `.github/bos-universal-config.json` are always in scope, so a managed-file
  sync commit to either can still re-trigger the pipeline.
- A branch creation (all-zero before-SHA) or a force-push whose base is no
  longer reachable fails open and runs, because neither yields a usable diff.
- `schedule` and `workflow_dispatch` bypass the filter entirely.

The job runs before `resolve-target-ref`, and therefore before
`sync-check-dev`/`sync-check-main` hold `contents: write`, so an irrelevant
push never reaches a job that can commit.

## Branch policy

`dev` is the hub development branch; `main` is the promoted stable runtime.
GitHub Actions does not allow expressions in `uses:` references, so branch
targeting is resolved ahead of time and encoded as static per-branch jobs:

- the security and Marketplace kickers resolve which branch (`dev` or `main`)
  a run targets, then dispatch to same-named jobs whose `uses:` refs are pinned
  to `@dev` and `@main`; the sync kicker invokes the published action directly
  and does not depend on a hub runtime branch;
- the gatekeeper kicker only ever fires on `main` pushes, so it has no `dev`
  variant and always calls `@main`;
- `bos-universal-sync.yml` is the hub's callable-only reusable workflow for
  the published sync action; `bos-universal-sync-kicker.yml` owns all events;
- `release-hub.yml` cannot reference `@main` without breaking
  self-validation, so it uses local `./.github/...` references instead;
- the hub uses the published action for generic managed-file synchronization;
- runtime branch decisions inside actions use the caller repository's
  `github.event.repository.default_branch` where appropriate.

The hub promotion workflow publishes shared actions, this directory, core
documentation/license files, and workflows declaring `workflow_call`. Event-only
hub maintenance workflows remain on `dev`.
