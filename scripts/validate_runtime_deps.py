#!/usr/bin/env python3
"""Validate the integration's dependency declarations against its own source.

Three checks, each of which would have caught a defect that shipped:

* every third-party import is declared in manifest.json (missing numpy and
  python-dateutil);
* the declared Home Assistant floor agrees across hacs.json, requirements.txt
  and pyproject.toml (they disagreed three ways);
* nothing in the integration performs blocking file I/O in the event loop.

Run by scripts/check.
"""

from __future__ import annotations

import ast
import json
import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
COMPONENT = REPO / "custom_components" / "adaptive_cover"
MANIFEST = COMPONENT / "manifest.json"
HACS = REPO / "hacs.json"
REQUIREMENTS = REPO / "requirements.txt"
PYPROJECT = REPO / "pyproject.toml"

# Callables that hit the filesystem synchronously. Home Assistant's own
# blocking-call detector trips on these inside the event loop, and a debug
# `open()` on a hardcoded path did exactly that on a live install.
BLOCKING_IO_CALLS = frozenset({"open"})

# Packages Home Assistant Core installs for us — the integration may import
# them without declaring them in manifest.json.
CORE_PROVIDED = frozenset({"homeassistant", "voluptuous"})

# Import root → PyPI distribution name (when they differ).
DIST_NAMES: dict[str, str] = {"dateutil": "python-dateutil"}


def _third_party_roots() -> set[str]:
    stdlib = set(sys.stdlib_module_names)
    roots: set[str] = set()
    for path in sorted(COMPONENT.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                candidates = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
                candidates = [node.module]
            else:
                continue
            for name in candidates:
                root = name.split(".")[0]
                if root not in stdlib:
                    roots.add(root)
    return roots


def _minor_floor(spec: str) -> tuple[int, int] | None:
    """Reduce a version spec to the (year, month) floor it declares."""
    match = re.search(r"(\d{4})\.(\d{1,2})", spec)
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2))


def _declared_ha_floors() -> dict[str, tuple[int, int] | None]:
    """Read the Home Assistant floor each metadata file declares."""
    floors: dict[str, tuple[int, int] | None] = {}

    hacs = json.loads(HACS.read_text(encoding="utf-8"))
    floors["hacs.json"] = _minor_floor(str(hacs.get("homeassistant", "")))

    for line in REQUIREMENTS.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("homeassistant"):
            floors["requirements.txt"] = _minor_floor(line)
            break
    else:
        floors["requirements.txt"] = None

    for line in PYPROJECT.read_text(encoding="utf-8").splitlines():
        if re.match(r"\s*homeassistant\s*=", line):
            floors["pyproject.toml"] = _minor_floor(line)
            break
    else:
        floors["pyproject.toml"] = None

    return floors


def _ha_floor_disagreements() -> list[str]:
    """Report the files whose declared HA floor differs from the others."""
    floors = _declared_ha_floors()

    missing = sorted(name for name, floor in floors.items() if floor is None)
    if missing:
        return [f"no Home Assistant floor found in {', '.join(missing)}"]

    distinct = set(floors.values())
    if len(distinct) == 1:
        return []

    detail = ", ".join(
        f"{name} declares {floor[0]}.{floor[1]}"
        for name, floor in sorted(floors.items())
    )
    return [
        "the declared Home Assistant floor must agree across hacs.json, "
        f"requirements.txt and pyproject.toml, but {detail}"
    ]


def _blocking_io_sites() -> list[str]:
    """Report synchronous file I/O calls inside the integration."""
    sites: list[str] = []
    for path in sorted(COMPONENT.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in BLOCKING_IO_CALLS
            ):
                sites.append(
                    f"{path.relative_to(REPO)}:{node.lineno} calls {node.func.id}()"
                )
    return sites


def main() -> int:  # noqa: D103
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    declared = {
        r.split("==")[0].split(">=")[0].split("~=")[0].strip().lower()
        for r in manifest.get("requirements", [])
    }

    undeclared = sorted(
        DIST_NAMES.get(root, root)
        for root in _third_party_roots()
        if root not in CORE_PROVIDED
        and DIST_NAMES.get(root, root).lower() not in declared
    )

    failures: list[str] = []
    if undeclared:
        failures.append(
            "imported at runtime but missing from manifest.json requirements: "
            + ", ".join(undeclared)
        )
    failures.extend(_ha_floor_disagreements())
    failures.extend(
        f"blocking file I/O in the event loop: {site}" for site in _blocking_io_sites()
    )

    if failures:
        for failure in failures:
            sys.stderr.write(f"FAIL: {failure}\n")
        return 1

    sys.stdout.write(  # noqa: T201
        f"Runtime dependency check passed ({len(declared)} declared, "
        "HA floor consistent, no blocking I/O).\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
