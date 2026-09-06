# Composite actions

Reusable composite actions consumed by the workflows in this repo and by
downstream callers that pin to this hub. Layout:

- `.github/actions/<name>/` — reusable action modules used by multiple
  workflows and pinned directly from this hub.
- `<name>/` — used by one workflow in this repo only.

The reusable `resolve-hub-ref` and `universal-config` actions are direct
infrastructure actions used by the managed kickers. They stay in their
canonical action directories so the callers keep simple, explicit `uses:`
references and a single source of truth.

The `resolve-hub-ref` action centralizes the small amount of branch routing
required by managed kickers. Kicker files should keep only event triggers,
resolver inputs, static `@dev`/`@main` jobs, and secret inheritance;
configuration and execution belong in the reusable backend workflow.

The `release-validation` action owns the deterministic release-readiness
engine used by artifact, Marketplace, and hub runtime releases. It emits
structured findings for `job-report`; it does not publish, push, or repair the
candidate in place. Repository-specific extensions are supplied through the
universal `release_validation` config or the conventional
`.github/scripts/release-validation.sh` hook.

## Published orchestration actions

Repository About-box sync (description, homepage, topics, and best-effort
sidebar widget preferences) is no longer a hub-local composite — it now
consumes the published
[`blackoutsecure/bos-repo-about-sync-action`](https://github.com/blackoutsecure/bos-repo-about-sync-action)
Marketplace action directly. Prefer the reusable
[`repo-metadata-sync.yml`](../workflows/repo-metadata-sync.yml) workflow for
normal consumers; it adds released-ref checkout, token fallback, concurrency,
soft skip behavior, and reusable outputs around the action.

The reusable workflow keeps credentials purpose-specific: the selected
Administration PAT is used only for repository PATCH/PUT calls, while the
job-scoped `GITHUB_TOKEN` with `models: read` is used for optional inference.

## Rules

1. **Inputs go through `env:`, never `${{ … }}` in `run:` bodies.** Bash
   reads the input as `"${VAR}"`. Template expansion inside `run:` is a
   shell-injection bug.
2. **Every bash `run:` starts with `set -euo pipefail`.**
3. **Validation helpers (`die`, `validate_tag`, `check_singleline`) stay
   inlined per action.** Total duplication is ~30 lines and keeping each
   `action.yml` self-contained is worth more than the saving.
4. **Python > ~20 lines moves to a sibling `.py` file**, invoked as
   `python3 "${GITHUB_ACTION_PATH}/script.py"`. `${GITHUB_ACTION_PATH}`
   resolves correctly cross-repo. Inputs still go through `env:`.
5. **Third-party actions are SHA-pinned** with a trailing version comment.
   Dependabot bumps both.
6. **`persist-credentials: false` on every `actions/checkout`** unless the
   step needs to push back.

## Lint

`actionlint` + `shellcheck` run on every PR via
[`.github/workflows/lint.yml`](../workflows/lint.yml). Locally:

```bash
brew install actionlint shellcheck
actionlint
```
