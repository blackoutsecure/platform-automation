#!/usr/bin/env python3
"""Resolve a new v-prefixed SemVer tag from explicit input or repository tags."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path


SEMVER = re.compile(r"^v(\d+)\.(\d+)\.(\d+)(?:-([A-Za-z0-9.-]+))?$")
STABLE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")


def fail(message: str) -> None:
    raise SystemExit(f"ERROR: {message}")


def repository_tags() -> list[str]:
    result = subprocess.run(
        ["git", "tag", "--list"],
        check=True,
        capture_output=True,
        text=True,
    )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def stable_key(tag: str) -> tuple[int, int, int]:
    match = STABLE.fullmatch(tag)
    if match is None:
        fail(f"internal stable-tag parse failed for {tag!r}")
    return tuple(map(int, match.groups()))


def write_output(name: str, value: str) -> None:
    with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
        output.write(f"{name}={value}\n")


def append_summary(tag: str, previous: str, bump: str, automatic: bool) -> None:
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    with Path(summary_path).open("a", encoding="utf-8") as summary:
        summary.write(
            "## Tag resolution\n\n"
            "| Field | Value |\n"
            "| --- | --- |\n"
            f"| tag_name | `{tag}` |\n"
            f"| previous_tag | `{previous or '(none)'}` |\n"
            f"| bump | `{bump}` |\n"
            f"| auto_resolved | `{str(automatic).lower()}` |\n"
        )


def main() -> None:
    explicit = os.environ.get("INPUT_TAG_NAME", "").strip()
    bump = os.environ.get("INPUT_BUMP", "patch").strip().lower() or "patch"
    first_tag = os.environ.get("INPUT_FIRST_TAG", "v0.0.1").strip()

    if bump not in {"patch", "minor", "major", "none"}:
        fail(f"bump must be patch, minor, major, or none (got {bump!r})")
    if STABLE.fullmatch(first_tag) is None:
        fail(f"first_tag must be stable v-prefixed SemVer (got {first_tag!r})")

    tags = repository_tags()
    stable_tags = sorted(
        (tag for tag in tags if STABLE.fullmatch(tag)),
        key=stable_key,
    )
    previous = stable_tags[-1] if stable_tags else ""

    if explicit:
        tag = explicit
        effective_bump = "none"
        automatic = False
    else:
        if bump == "none":
            fail("tag_name is required when bump is none")
        automatic = True
        effective_bump = bump
        if not previous:
            tag = first_tag
        else:
            major, minor, patch = stable_key(previous)
            if bump == "patch":
                patch += 1
            elif bump == "minor":
                minor += 1
                patch = 0
            else:
                major += 1
                minor = 0
                patch = 0
            tag = f"v{major}.{minor}.{patch}"

    if SEMVER.fullmatch(tag) is None:
        fail(f"tag_name must be v-prefixed SemVer (got {tag!r})")
    if tag in tags:
        fail(f"tag {tag} already exists")

    write_output("tag_name", tag)
    write_output("previous_tag", previous)
    write_output("auto_resolved", json.dumps(automatic))
    write_output("bump", effective_bump)
    append_summary(tag, previous, effective_bump, automatic)


if __name__ == "__main__":
    main()
