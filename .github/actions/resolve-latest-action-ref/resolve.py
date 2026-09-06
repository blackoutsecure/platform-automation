#!/usr/bin/env python3
"""Resolve the newest tag for a repository, ranking pre-releases correctly.

`GET /releases/latest` excludes pre-releases, so it cannot answer "what is
the newest version" for a repository that ships pre-releases. This module
lists every release (or every tag), filters by a SemVer-shaped pattern, and
ranks the survivors by SemVer precedence.

Ordering follows the SemVer spec: `1.0.0-rc.1 < 1.0.0`, and within a
pre-release identifier numeric segments sort below alphanumeric ones. The
`prerelease` flag on a GitHub Release therefore does NOT influence ordering
by itself; only the version string does. A repository whose newest release
is flagged pre-release still wins when its version is highest, which is
exactly the "is the pre-release the latest, or is the stable one?" question.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request

API_ROOT = "https://api.github.com"
DEFAULT_TAG_PATTERN = r"^v?\d+\.\d+\.\d+([-+][0-9A-Za-z.-]+)?$"
MAX_PAGES = 5
PER_PAGE = 100

_SEMVER_RE = re.compile(
    r"^v?(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)"
    r"(?:-(?P<pre>[0-9A-Za-z.-]+))?"
    r"(?:\+(?P<build>[0-9A-Za-z.-]+))?$"
)


def die(message: str) -> None:
    print(f"::error::resolve-latest-action-ref: {message}", file=sys.stderr)
    raise SystemExit(1)


def semver_key(tag: str) -> tuple:
    """Sort key ordering SemVer tags, with correct pre-release precedence.

    Non-SemVer tags sort below everything so they can never win a ranking.
    Build metadata is ignored for precedence, per the SemVer spec.
    """
    match = _SEMVER_RE.match((tag or "").strip())
    if match is None:
        return (0, (0, 0, 0), (0, ()))

    core = (
        int(match.group("major")),
        int(match.group("minor")),
        int(match.group("patch")),
    )
    pre = match.group("pre")
    if pre is None:
        # Absence of a pre-release outranks any pre-release of the same core.
        return (1, core, (1, ()))

    identifiers = []
    for part in pre.split("."):
        if part.isdigit():
            identifiers.append((0, int(part), ""))
        else:
            identifiers.append((1, 0, part))
    return (1, core, (0, tuple(identifiers)))


def tag_is_prerelease(tag: str) -> bool:
    """Return whether a SemVer tag contains a pre-release component."""
    match = _SEMVER_RE.match((tag or "").strip())
    return bool(match and match.group("pre") is not None)


def _request(url: str, token: str) -> object:
    request = urllib.request.Request(url)
    request.add_header("Accept", "application/vnd.github+json")
    request.add_header("X-GitHub-Api-Version", "2022-11-28")
    request.add_header("User-Agent", "bos-resolve-latest-action-ref")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:400]
        die(f"GET {url} failed with HTTP {exc.code}: {body}")
    except urllib.error.URLError as exc:
        die(f"GET {url} failed: {exc.reason}")


def _paginate(path: str, token: str) -> list:
    items: list = []
    for page in range(1, MAX_PAGES + 1):
        url = f"{API_ROOT}{path}?per_page={PER_PAGE}&page={page}"
        batch = _request(url, token)
        if not isinstance(batch, list) or not batch:
            break
        items.extend(batch)
        if len(batch) < PER_PAGE:
            break
    return items


def select_release(releases: list, pattern: re.Pattern, channel: str) -> dict | None:
    """Highest-SemVer non-draft release matching `pattern` and `channel`."""
    candidates = []
    for release in releases:
        if release.get("draft"):
            continue
        tag = release.get("tag_name") or ""
        if not pattern.match(tag):
            continue
        tag_is_pre = tag_is_prerelease(tag)
        is_pre = tag_is_pre or bool(release.get("prerelease"))
        if channel == "stable" and is_pre:
            continue
        if channel == "prerelease" and not is_pre:
            continue
        candidates.append((release, is_pre))
    if not candidates:
        return None
    if channel in {"prerelease-preferred", "pre-latest"}:
        preferred = [item for item in candidates if item[1]]
        candidates = preferred or [item for item in candidates if not item[1]]
    candidates.sort(key=lambda item: semver_key(item[0].get("tag_name") or ""))
    return candidates[-1][0]


def select_tag(tags: list, pattern: re.Pattern, channel: str) -> dict | None:
    candidates = []
    for tag in tags:
        name = tag.get("name") or ""
        if not pattern.match(name):
            continue
        is_pre = tag_is_prerelease(name)
        if channel == "stable" and is_pre:
            continue
        if channel == "prerelease" and not is_pre:
            continue
        candidates.append(tag)
    if not candidates:
        return None
    if channel in {"prerelease-preferred", "pre-latest"}:
        preferred = [tag for tag in candidates if tag_is_prerelease(tag.get("name") or "")]
        candidates = preferred or [tag for tag in candidates if not tag_is_prerelease(tag.get("name") or "")]
    candidates.sort(key=lambda item: semver_key(item.get("name") or ""))
    return candidates[-1]


def resolve_commit_sha(repo: str, tag: str, token: str) -> str:
    """Peel a tag to its commit SHA, following annotated tag objects."""
    ref = _request(f"{API_ROOT}/repos/{repo}/git/ref/tags/{tag}", token)
    obj = ref.get("object") or {}
    sha, obj_type = obj.get("sha", ""), obj.get("type", "")
    if obj_type == "tag":
        annotated = _request(f"{API_ROOT}/repos/{repo}/git/tags/{sha}", token)
        sha = (annotated.get("object") or {}).get("sha", sha)
    if not re.fullmatch(r"[0-9a-f]{40}", sha or ""):
        die(f"could not peel tag '{tag}' of {repo} to a commit SHA (got '{sha}')")
    return sha


def resolve(repo: str, channel: str, source: str, pattern_text: str, token: str) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9._-]+/[A-Za-z0-9._-]+", repo or ""):
        die(f"input 'repository' must be 'owner/name' (got '{repo}')")
    if channel not in {"auto", "stable", "prerelease", "prerelease-preferred", "pre-latest"}:
        die(
            "input 'channel' must be auto, stable, prerelease, "
            f"prerelease-preferred, or pre-latest (got '{channel}')"
        )
    if source not in {"auto", "releases", "tags"}:
        die(f"input 'source' must be auto, releases, or tags (got '{source}')")
    try:
        pattern = re.compile(pattern_text or DEFAULT_TAG_PATTERN)
    except re.error as exc:
        die(f"input 'tag_pattern' is not a valid regex: {exc}")

    effective_channel = "auto" if channel == "auto" else channel

    if source in {"auto", "releases"}:
        releases = _paginate(f"/repos/{repo}/releases", token)
        winner = select_release(releases, pattern, effective_channel)
        if winner is not None:
            tag = winner["tag_name"]
            return {
                "tag": tag,
                "sha": resolve_commit_sha(repo, tag, token),
                "is_prerelease": "true" if tag_is_prerelease(tag) or winner.get("prerelease") else "false",
                "published_at": winner.get("published_at") or "",
                "html_url": winner.get("html_url") or "",
                "source": "releases",
            }
        if source == "releases":
            die(f"{repo} has no non-draft release matching {pattern.pattern!r}")

    tags = _paginate(f"/repos/{repo}/tags", token)
    winner = select_tag(tags, pattern, effective_channel)
    if winner is None:
        die(f"{repo} has no tag matching {pattern.pattern!r}")
    tag = winner["name"]
    return {
        "tag": tag,
        "sha": resolve_commit_sha(repo, tag, token),
        "is_prerelease": "true" if tag_is_prerelease(tag) else "false",
        "published_at": "",
        "html_url": "",
        "source": "tags",
    }


def main() -> int:
    result = resolve(
        repo=os.environ.get("INPUT_REPOSITORY", "").strip(),
        channel=(os.environ.get("INPUT_CHANNEL") or "auto").strip(),
        source=(os.environ.get("INPUT_SOURCE") or "auto").strip(),
        pattern_text=(os.environ.get("INPUT_TAG_PATTERN") or "").strip(),
        token=os.environ.get("GITHUB_TOKEN", "").strip(),
    )

    output_path = os.environ.get("GITHUB_OUTPUT")
    if output_path:
        with open(output_path, "a", encoding="utf-8") as handle:
            for key, value in result.items():
                handle.write(f"{key}={value}\n")

    flag = " (pre-release)" if result["is_prerelease"] == "true" else ""
    print(
        f"{os.environ.get('INPUT_REPOSITORY')} -> {result['tag']}{flag} "
        f"@ {result['sha']} (via {result['source']})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
