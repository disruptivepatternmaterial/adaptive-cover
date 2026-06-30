#!/usr/bin/env python3
"""Validate release metadata consistency across repository surfaces."""

from __future__ import annotations

from pathlib import Path
import json
import sys


ROOT = Path(__file__).resolve().parent.parent


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _manifest_version() -> str:
    manifest_path = ROOT / "custom_components" / "adaptive_cover" / "manifest.json"
    manifest = json.loads(_read(manifest_path))
    version = manifest.get("version")
    if not isinstance(version, str) or not version:
        raise ValueError("manifest.json is missing a valid version string")
    return version


def _pyproject_version() -> str:
    pyproject_path = ROOT / "pyproject.toml"
    in_poetry_section = False
    saw_poetry_section = False
    for raw_line in _read(pyproject_path).splitlines():
        line = raw_line.strip()
        if line.startswith("[") and line.endswith("]"):
            in_poetry_section = line == "[tool.poetry]"
            saw_poetry_section = saw_poetry_section or in_poetry_section
            continue
        if not in_poetry_section:
            continue
        if not line.startswith("version"):
            continue
        key, _, value = line.partition("=")
        if key.strip() != "version":
            continue
        version = value.split("#", 1)[0].strip().strip('"').strip("'")
        if version:
            return version

    if not saw_poetry_section:
        raise ValueError("pyproject.toml is missing the [tool.poetry] section")
    else:
        raise ValueError("pyproject.toml does not contain a [tool.poetry] version")


def _check_readme(version: str) -> list[str]:
    errors: list[str] = []
    readme_path = ROOT / "README.md"
    content = _read(readme_path)
    expected = f"[v{version}]"
    if expected not in content:
        errors.append(f"README.md missing current release reference {expected}")
    return errors


def _check_changelog(version: str) -> list[str]:
    errors: list[str] = []
    changelog_path = ROOT / "CHANGELOG.md"
    content = _read(changelog_path)
    heading = f"## [{version}]"
    if heading not in content:
        errors.append(f"CHANGELOG.md missing heading '{heading}'")
    return errors


def _check_release_drafter_typos() -> list[str]:
    errors: list[str] = []
    drafter_path = ROOT / ".github" / "release-drafter.yml"
    if not drafter_path.exists():
        return errors
    content = _read(drafter_path)
    if "prereleas-identifier" in content:
        errors.append(
            ".github/release-drafter.yml contains typo 'prereleas-identifier'"
        )
    if "exlude-contributors" in content:
        errors.append(".github/release-drafter.yml contains typo 'exlude-contributors'")
    return errors


def main() -> int:
    """Run release metadata checks and return process exit code."""
    errors: list[str] = []

    try:
        manifest_version = _manifest_version()
        pyproject_version = _pyproject_version()
        if manifest_version != pyproject_version:
            errors.append(
                "Version mismatch: "
                f"manifest.json={manifest_version} vs pyproject.toml={pyproject_version}"
            )

        errors.extend(_check_readme(manifest_version))
        errors.extend(_check_changelog(manifest_version))
        errors.extend(_check_release_drafter_typos())
    except (OSError, ValueError, json.JSONDecodeError) as err:
        sys.stderr.write(f"ERROR: {err}\n")
        return 1

    if errors:
        sys.stderr.write("Release metadata validation failed:\n")
        for err in errors:
            sys.stderr.write(f"- {err}\n")
        return 1

    sys.stdout.write(
        f"Release metadata validation passed (version {manifest_version}).\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
