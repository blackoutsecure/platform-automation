#!/usr/bin/env python3
"""Render a balena block/application manifest (balena.yml) from env vars
via PyYAML safe_dump. See sibling action.yml for the input contract."""

from __future__ import annotations

import os
import sys

import yaml


def fail(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


# First entry of the first resolved arch becomes the auto-derived
# defaultDeviceType when the caller didn't pass one. Mainstream devices
# only; deprecated/alpha types are intentionally omitted.
ARCH_TO_DEVICES: dict[str, list[str]] = {
    "aarch64": [
        "raspberrypi4-64",
        "raspberrypi5",
        "raspberrypi3-64",
        "raspberrypi400-64",
        "raspberrypizero2w-64",
        "raspberrypi-cm4-ioboard",
        "generic-aarch64",
    ],
    "amd64": [
        "genericx86-64-ext",
        "intel-nuc",
        "generic-amd64",
    ],
    "armv7hf": [
        "raspberrypi3",
        "raspberrypi4",
        "raspberrypi400",
        "fincm3",
        "generic-armv7ahf",
    ],
    "rpi": [
        "raspberry-pi2",
        "raspberry-pi",
        "raspberrypi-zero",
        "raspberrypi-zero-w",
    ],
    "i386": [
        "generic-i386",
    ],
}

DOCKER_TO_BALENA: dict[str, str] = {
    "amd64": "amd64",
    "x86_64": "amd64",
    "arm64": "aarch64",
    "arm64/v8": "aarch64",
    "aarch64": "aarch64",
    "arm/v7": "armv7hf",
    "arm/v6": "rpi",
    "386": "i386",
    "i386": "i386",
}


def _split_list(raw: str) -> list[str]:
    """Split newline-/comma-separated string, trim, dedupe, preserve order."""
    out: list[str] = []
    seen: set[str] = set()
    for chunk in (raw or "").splitlines():
        for s in chunk.split(","):
            s = s.strip()
            if s and s not in seen:
                seen.add(s)
                out.append(s)
    return out


def _docker_token_to_balena(token: str) -> str | None:
    t = token.strip().lower()
    if not t:
        return None
    if "/" in t:
        head, _, rest = t.partition("/")
        if head == "linux":
            t = rest
        elif head in ("amd64", "arm64", "arm", "386"):
            pass  # bare arch/variant
        else:
            return None  # windows/, freebsd/, ...
    return DOCKER_TO_BALENA.get(t)


def _expand_arches(arches: list[str], source_label: str) -> list[str]:
    unknown = [a for a in arches if a not in ARCH_TO_DEVICES]
    if unknown:
        fail(
            f"{source_label}: unknown balena arch(es) {unknown!r} — "
            f"known: {sorted(ARCH_TO_DEVICES)}"
        )
    out: list[str] = []
    seen: set[str] = set()
    for a in arches:
        for d in ARCH_TO_DEVICES[a]:
            if d not in seen:
                seen.add(d)
                out.append(d)
    return out


def main() -> int:
    out_path = os.environ["OUT_PATH"].strip()
    if not out_path or out_path.startswith("/") or ".." in out_path.split("/"):
        fail(f"out_path must be a non-empty repo-relative path: {out_path!r}")

    name = os.environ["BLOCK_NAME"].strip()
    if not name:
        fail("name is empty")

    version = os.environ["BLOCK_VERSION"].strip()
    if not version:
        fail("version is empty")

    block_type = os.environ.get("BLOCK_TYPE", "").strip() or "sw.block"

    # Device-type resolution waterfall: supported_device_types -> image_ref
    # sniff -> architectures -> platforms. First non-empty wins.
    supported = _split_list(os.environ.get("SUPPORTED_DEVICE_TYPES", ""))
    resolution_source = "supported_device_types"

    if not supported:
        sniffed = os.environ.get("SNIFFED_PLATFORMS", "").strip()
        if sniffed:
            arches: list[str] = []
            seen_a: set[str] = set()
            for token in sniffed.split(","):
                bal = _docker_token_to_balena(token)
                if bal and bal not in seen_a:
                    seen_a.add(bal)
                    arches.append(bal)
            if not arches:
                fail(
                    f"image_ref sniff returned platforms {sniffed!r} "
                    "but none mapped to a balena arch"
                )
            supported = _expand_arches(arches, "image_ref")
            resolution_source = f"image_ref (-> arches: {','.join(arches)})"

    if not supported:
        arches = _split_list(os.environ.get("ARCHITECTURES", ""))
        if arches:
            supported = _expand_arches(arches, "architectures")
            resolution_source = f"architectures ({','.join(arches)})"

    if not supported:
        platforms = _split_list(os.environ.get("PLATFORMS", ""))
        if platforms:
            arches = []
            seen_a = set()
            for token in platforms:
                bal = _docker_token_to_balena(token)
                if bal is None:
                    fail(
                        f"platforms entry {token!r} does not map to a "
                        f"balena arch (known: {sorted(DOCKER_TO_BALENA)})"
                    )
                if bal not in seen_a:
                    seen_a.add(bal)
                    arches.append(bal)
            supported = _expand_arches(arches, "platforms")
            resolution_source = (
                f"platforms ({','.join(platforms)} -> arches: "
                f"{','.join(arches)})"
            )

    if not supported:
        fail(
            "no supported device types could be resolved — set one of "
            "supported_device_types / image_ref / architectures / platforms"
        )

    print(f"Resolved supportedDeviceTypes via {resolution_source}")
    print(f"  -> {supported}")

    default_dt = os.environ.get("DEFAULT_DEVICE_TYPE", "").strip()
    if not default_dt:
        default_dt = supported[0]
        print(f"Derived defaultDeviceType from first entry: {default_dt}")
    if default_dt not in supported:
        fail(
            f"default_device_type {default_dt!r} must appear in "
            f"supportedDeviceTypes {supported!r}"
        )

    repo_url = os.environ.get("ASSETS_REPO_URL", "").strip()
    logo_url = os.environ.get("ASSETS_LOGO_URL", "").strip()

    emit_assets_raw = os.environ.get("EMIT_ASSETS", "true").strip().lower()
    if emit_assets_raw not in ("true", "false"):
        fail(f"emit_assets must be 'true' or 'false' (got {emit_assets_raw!r})")
    emit_assets = emit_assets_raw == "true"

    if emit_assets and not repo_url:
        repo_url = (
            f"{os.environ['GITHUB_SERVER_URL']}/"
            f"{os.environ['GITHUB_REPOSITORY']}"
        )
    if repo_url and not repo_url.startswith("https://"):
        fail(f"assets_repository_url must start with 'https://': {repo_url!r}")
    if logo_url and not logo_url.startswith("https://"):
        fail(f"assets_logo_url must start with 'https://': {logo_url!r}")

    description = os.environ.get("BLOCK_DESCRIPTION", "").strip()
    post_prov = os.environ.get("BLOCK_POST_PROVISIONING", "")

    # Canonical field order per balena docs: name, [description],
    # [post-provisioning], version, type, [assets], data.
    doc: dict = {"name": name}
    if description:
        doc["description"] = description
    if post_prov.strip():
        doc["post-provisioning"] = post_prov
    doc["version"] = version
    doc["type"] = block_type

    if emit_assets:
        assets: dict = {
            "repository": {"type": "blob.asset", "data": {"url": repo_url}},
        }
        if logo_url:
            assets["logo"] = {"type": "blob.asset", "data": {"url": logo_url}}
        doc["assets"] = assets

    doc["data"] = {
        "defaultDeviceType": default_dt,
        "supportedDeviceTypes": supported,
    }

    # safe_dump quotes/escapes specials; caller values can't break out.
    with open(out_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(
            doc,
            f,
            sort_keys=False,
            default_flow_style=False,
            allow_unicode=True,
            width=1000,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
