#!/usr/bin/env python3
"""Synchronize and bump BuildAnchor versions across all packages.

Updates:
- pyproject.toml
- uv.lock (via `uv lock`)
- sdk/node/package.json
- sdk/java/pom.xml
- Formula/buildanchor.rb
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT_PATH = ROOT / "pyproject.toml"
POM_PATH = ROOT / "sdk" / "java" / "pom.xml"
NODE_PACKAGE_PATH = ROOT / "sdk" / "node" / "package.json"
FORMULA_PATH = ROOT / "Formula" / "buildanchor.rb"


def read_current_version() -> str:
    content = PYPROJECT_PATH.read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', content, re.MULTILINE)
    if not match:
        raise ValueError(f"Could not find version string in {PYPROJECT_PATH}")
    return match.group(1)


def parse_semver(v: str) -> tuple[int, int, int]:
    # Match standard major.minor.patch
    m = re.match(r"^(\d+)\.(\d+)\.(\d+)", v)
    if not m:
        raise ValueError(f"Invalid semver version: {v}")
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def calculate_bump(current: str, bump_type: str) -> str:
    major, minor, patch = parse_semver(current)
    if bump_type == "major":
        return f"{major + 1}.0.0"
    elif bump_type == "minor":
        return f"{major}.{minor + 1}.0"
    elif bump_type == "patch":
        return f"{major}.{minor}.{patch + 1}"
    raise ValueError(f"Unknown bump type: {bump_type}")


def update_pyproject(new_version: str) -> None:
    content = PYPROJECT_PATH.read_text(encoding="utf-8")
    updated = re.sub(
        r'^version\s*=\s*"[^"]+"',
        f'version = "{new_version}"',
        content,
        count=1,
        flags=re.MULTILINE,
    )
    PYPROJECT_PATH.write_text(updated, encoding="utf-8")
    print(f"✓ Updated {PYPROJECT_PATH.relative_to(ROOT)} -> {new_version}")


def update_uv_lock() -> None:
    try:
        res = subprocess.run(["uv", "lock"], cwd=ROOT, capture_output=True, text=True, check=True)
        print("✓ Updated uv.lock")
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        print(f"! Warning: could not run 'uv lock': {e}", file=sys.stderr)


def update_pom(new_version: str) -> None:
    if not POM_PATH.exists():
        return
    content = POM_PATH.read_text(encoding="utf-8")
    # Match version inside <project> root after artifactId
    updated = re.sub(
        r'(<artifactId>buildanchor-sdk</artifactId>\s*<version>)[^<]+(</version>)',
        rf"\g<1>{new_version}\g<2>",
        content,
        count=1,
    )
    POM_PATH.write_text(updated, encoding="utf-8")
    print(f"✓ Updated {POM_PATH.relative_to(ROOT)} -> {new_version}")


def update_node_package(new_version: str) -> None:
    if not NODE_PACKAGE_PATH.exists():
        return
    package = json.loads(NODE_PACKAGE_PATH.read_text(encoding="utf-8"))
    package["version"] = new_version
    NODE_PACKAGE_PATH.write_text(
        json.dumps(package, indent=2) + "\n", encoding="utf-8"
    )
    print(f"✓ Updated {NODE_PACKAGE_PATH.relative_to(ROOT)} -> {new_version}")


def update_formula(new_version: str) -> None:
    if not FORMULA_PATH.exists():
        return
    content = FORMULA_PATH.read_text(encoding="utf-8")
    updated = re.sub(
        r'archive/refs/tags/[^"]+\.tar\.gz',
        f"archive/refs/tags/v{new_version}.tar.gz",
        content,
    )
    updated = re.sub(
        r'version\s+"[^"]+"',
        f'version "{new_version}"',
        updated,
        count=1,
    )
    FORMULA_PATH.write_text(updated, encoding="utf-8")
    print(f"✓ Updated {FORMULA_PATH.relative_to(ROOT)} -> {new_version}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Synchronize BuildAnchor project versions.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("version", nargs="?", help="Explicit new version (e.g. 0.3.3)")
    group.add_argument("--patch", action="store_true", help="Bump patch version (e.g. 0.3.2 -> 0.3.3)")
    group.add_argument("--minor", action="store_true", help="Bump minor version (e.g. 0.3.2 -> 0.4.0)")
    group.add_argument("--major", action="store_true", help="Bump major version (e.g. 0.3.2 -> 1.0.0)")

    args = parser.parse_args()
    current_version = read_current_version()
    print(f"Current version: {current_version}")

    if args.patch:
        new_version = calculate_bump(current_version, "patch")
    elif args.minor:
        new_version = calculate_bump(current_version, "minor")
    elif args.major:
        new_version = calculate_bump(current_version, "major")
    else:
        new_version = args.version.lstrip("v")
        parse_semver(new_version)

    print(f"Bumping to:      {new_version}\n")

    update_pyproject(new_version)
    update_uv_lock()
    update_node_package(new_version)
    update_pom(new_version)
    update_formula(new_version)

    print(f"\nAll version markers successfully synchronized to {new_version}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
