#!/usr/bin/env python3
"""Extract linux/<arch>[/variant] tokens from `docker buildx imagetools
inspect --format '{{json .}}'` output. Prints CSV to stdout."""

from __future__ import annotations

import json
import sys


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("")
        return 0

    try:
        with open(argv[1], encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, json.JSONDecodeError):
        print("")
        return 0

    # Manifest lists carry entries under manifest.manifests; single-arch
    # images put one entry under manifest.config.platform.
    manifest = doc.get("manifest", {}) or {}
    entries = manifest.get("manifests") or []

    out: list[str] = []
    if entries:
        for entry in entries:
            p = (entry or {}).get("platform") or {}
            if p.get("os") and p.get("os") != "linux":
                continue
            arch = (p.get("architecture") or "").strip()
            variant = (p.get("variant") or "").strip()
            if not arch:
                continue
            token = f"linux/{arch}/{variant}" if variant else f"linux/{arch}"
            if token not in out:
                out.append(token)
    else:
        for candidate in (manifest.get("config", {}), doc):
            p = (candidate or {}).get("platform") or {}
            if not isinstance(p, dict):
                continue
            if p.get("os") and p.get("os") != "linux":
                continue
            arch = (p.get("architecture") or "").strip()
            variant = (p.get("variant") or "").strip()
            if arch:
                out.append(
                    f"linux/{arch}/{variant}" if variant else f"linux/{arch}"
                )
                break

    print(",".join(out))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
