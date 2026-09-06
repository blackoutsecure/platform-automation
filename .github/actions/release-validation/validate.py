#!/usr/bin/env python3
"""Deterministic release-readiness checks for hub-managed repositories."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path.cwd()
SEMVER = re.compile(r"^v?\d+\.\d+\.\d+(?:-[0-9A-Za-z.+-]+)?$")


def finding(
    rule_id: str,
    severity: str,
    control: str,
    evidence: str,
    remediation: str,
    group: str,
) -> dict[str, str]:
    return {
        "id": rule_id,
        "severity": severity,
        "control": control,
        "evidence": evidence,
        "remediation": remediation,
        "group": group,
    }


def run(command: list[str] | str, label: str) -> tuple[bool, str]:
    shell = isinstance(command, str)
    if not shell and command:
        resolved = shutil.which(command[0])
        if resolved:
            command = [resolved, *command[1:]]
    display = command if shell else " ".join(command)
    print(f"::group::{label}: {display}")
    result = subprocess.run(
        command,
        cwd=ROOT,
        shell=shell,
        executable="/bin/bash" if shell and os.name != "nt" else None,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    output = result.stdout or ""
    print(output, end="" if output.endswith("\n") else "\n")
    print("::endgroup::")
    tail = output[-3000:].strip()
    return result.returncode == 0, tail or ("" if result.returncode == 0 else f"exit code {result.returncode}")


def bool_value(config: dict[str, Any], key: str, default: bool) -> bool:
    value = config.get(key, default)
    return value if isinstance(value, bool) else default


def parse_version(path: Path) -> str:
    if path.name == "package.json":
        try:
            value = json.loads(path.read_text(encoding="utf-8")).get("version", "")
            return value if isinstance(value, str) else ""
        except (OSError, json.JSONDecodeError):
            return ""
    match = re.search(
        r"(?m)^version\s*=\s*[\"']([^\"']+)[\"']",
        path.read_text(encoding="utf-8"),
    )
    return match.group(1) if match else ""


def main() -> int:
    try:
        config = json.loads(os.environ.get("RELEASE_VALIDATION_CONFIG", "{}"))
    except json.JSONDecodeError as exc:
        print(f"::error::Invalid release validation config: {exc}", file=sys.stderr)
        return 2
    if not isinstance(config, dict):
        print("::error::Release validation config must be a JSON object", file=sys.stderr)
        return 2

    expected_tag = os.environ.get("RELEASE_VALIDATION_EXPECTED_TAG", "").strip()
    release_kind = os.environ.get("RELEASE_VALIDATION_KIND", "artifact").strip()
    findings: list[dict[str, str]] = []
    commands: list[str] = []

    if config.get("enabled") is False:
        findings.append(finding(
            "RV000", "skip", "Release validation enabled",
            "release_validation.enabled is false", "Enable the release gate before publishing.",
            "policy",
        ))
    else:
        if expected_tag and not SEMVER.fullmatch(expected_tag):
            findings.append(finding(
                "RV001", "fail", "Release tag is SemVer", expected_tag,
                "Use vX.Y.Z or vX.Y.Z-suffix.", "metadata",
            ))
        else:
            findings.append(finding(
                "RV001", "pass", "Release tag is SemVer",
                expected_tag or "Resolved by the publishing workflow",
                "No action needed.", "metadata",
            ))

        required = config.get("required_paths", [])
        if not isinstance(required, list) or any(not isinstance(item, str) for item in required):
            findings.append(finding(
                "RV002", "fail", "Required path policy is valid",
                "required_paths must be an array of strings",
                "Correct release_validation.required_paths.", "policy",
            ))
        else:
            if release_kind == "marketplace" and "action.yml" not in required:
                required = [*required, "action.yml"]
            missing = [item for item in required if not (ROOT / item).exists()]
            findings.append(finding(
                "RV002", "fail" if missing else "pass", "Required release files exist",
                ", ".join(missing) if missing else f"{len(required)} required paths present",
                "Add the missing release files or explicitly adjust the repository policy."
                if missing else "No action needed.",
                "metadata",
            ))

        if expected_tag:
            expected_version = expected_tag.removeprefix("v")
            versions = {
                str(path): parse_version(path)
                for path in (ROOT / "package.json", ROOT / "pyproject.toml")
                if path.exists()
            }
            mismatches = [f"{path}={version}" for path, version in versions.items() if version and version != expected_version]
            mode = str(config.get("version_match", "warn")).lower()
            severity = (
                "pass" if not mismatches
                else "fail" if mode == "fail"
                else "not_applicable" if mode == "skip"
                else "warn"
            )
            findings.append(finding(
                "RV003", severity, "Declared version matches release tag",
                ", ".join(mismatches) if mismatches else "No version mismatch detected",
                "Update package metadata or set version_match to warn/skip when tags are intentionally independent."
                if mismatches else "No action needed.",
                "metadata",
            ))

        package_path = ROOT / "package.json"
        if package_path.exists() and bool_value(config, "run_node", True):
            try:
                package = json.loads(package_path.read_text(encoding="utf-8"))
                scripts = package.get("scripts", {})
                scripts = scripts if isinstance(scripts, dict) else {}
            except json.JSONDecodeError as exc:
                findings.append(finding(
                    "RV010", "fail", "Node release checks", f"Invalid package.json: {exc}",
                    "Correct package.json before release.", "tests",
                ))
                scripts = {}
            else:
                node_commands: list[list[str]] = []
                lockfile = ROOT / "package-lock.json"
                if bool_value(config, "require_lockfile", True) and not lockfile.exists():
                    findings.append(finding(
                        "RV010", "fail", "Node release checks", "package-lock.json is missing",
                        "Commit a lockfile or set require_lockfile to false for this repository.", "tests",
                    ))
                else:
                    node_commands.append(["npm", "ci"] if lockfile.exists() else ["npm", "install", "--ignore-scripts"])
                    if "verify" in scripts:
                        node_commands.append(["npm", "run", "verify"])
                    elif "check" in scripts:
                        node_commands.append(["npm", "run", "check"])
                    else:
                        for name in ("lint:check", "lint", "format:check", "test"):
                            script = str(scripts.get(name, ""))
                            if script and "--fix" not in script and "--write" not in script:
                                node_commands.append(["npm", "run", name])
                    if "build" in scripts:
                        node_commands.append(["npm", "run", "build"])

                    failure = ""
                    for command in node_commands:
                        commands.append(" ".join(command))
                        ok, output = run(command, "Node validation")
                        if not ok:
                            failure = f"{' '.join(command)} failed\n{output}"
                            break
                    findings.append(finding(
                        "RV010", "fail" if failure else "pass", "Node release checks",
                        failure or f"{len(node_commands)} commands passed",
                        "Run the failing command locally and commit its fixes." if failure else "No action needed.",
                        "tests",
                    ))

        pyproject_path = ROOT / "pyproject.toml"
        if pyproject_path.exists() and bool_value(config, "run_python", True):
            text = pyproject_path.read_text(encoding="utf-8")
            install_target = ".[dev]" if re.search(r"(?m)^dev\s*=\s*\[", text) else "."
            python_commands: list[list[str]] = [
                [sys.executable, "-m", "pip", "install", "-e", install_target],
            ]
            if "ruff" in text.lower():
                python_commands.append([sys.executable, "-m", "ruff", "check", "."])
            if (ROOT / "test").is_dir() or (ROOT / "tests").is_dir():
                python_commands.append([sys.executable, "-m", "pytest", "-q"])
            failure = ""
            for command in python_commands:
                commands.append(" ".join(command))
                ok, output = run(command, "Python validation")
                if not ok:
                    failure = f"{' '.join(command)} failed\n{output}"
                    break
            findings.append(finding(
                "RV011", "fail" if failure else "pass", "Python release checks",
                failure or f"{len(python_commands)} commands passed",
                "Run the failing command locally and commit its fixes." if failure else "No action needed.",
                "tests",
            ))

        if bool_value(config, "run_custom", True):
            custom: list[str] = []
            configured = config.get("custom_commands", [])
            if isinstance(configured, list):
                custom.extend(item for item in configured if isinstance(item, str) and item.strip())
            hook = str(config.get("custom_hook", ".github/scripts/release-validation.sh")).strip()
            if hook and (ROOT / hook).is_file():
                custom.append(f"bash {json.dumps(hook)}")
            failure = ""
            for command in custom:
                commands.append(command)
                ok, output = run(command, "Repository-specific validation")
                if not ok:
                    failure = f"{command} failed\n{output}"
                    break
            findings.append(finding(
                "RV020", "fail" if failure else ("pass" if custom else "not_applicable"),
                "Repository-specific release checks",
                failure or (f"{len(custom)} commands passed" if custom else "No custom checks configured"),
                "Correct the custom validation failure." if failure else "Add a hook only for checks unique to this repository.",
                "extensions",
            ))

        if bool_value(config, "verify_clean_tree", True):
            commands.append("git status --porcelain --untracked-files=no")
            ok, output = run(["git", "status", "--porcelain", "--untracked-files=no"], "Artifact integrity")
            dirty = output if ok else "Unable to inspect the worktree"
            findings.append(finding(
                "RV030", "fail" if dirty else "pass", "Generated artifacts are committed",
                dirty or "Build and validation left tracked files unchanged",
                "Run the build locally and commit regenerated artifacts." if dirty else "No action needed.",
                "artifacts",
            ))

    passed = not any(item["severity"] == "fail" for item in findings)
    output_path = os.environ.get("GITHUB_OUTPUT")
    if output_path:
        with open(output_path, "a", encoding="utf-8") as handle:
            handle.write("findings<<__BOS_RELEASE_FINDINGS__\n")
            handle.write(json.dumps(findings, separators=(",", ":")) + "\n")
            handle.write("__BOS_RELEASE_FINDINGS__\n")
            handle.write(f"commands={json.dumps(commands, separators=(',', ':'))}\n")
            handle.write(f"passed={str(passed).lower()}\n")
    else:
        print(json.dumps({"findings": findings, "commands": commands, "passed": passed}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
