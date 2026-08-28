#!/usr/bin/env python3
"""Validate that manifest.json requirements cover every runtime import.

This is the machine check that would have caught the missing numpy and
dateutil declarations before they shipped. Run by scripts/check.
"""

from __future__ import annotations

import ast
import json
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
COMPONENT = REPO / "custom_components" / "adaptive_cover"
MANIFEST = COMPONENT / "manifest.json"

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

    if undeclared:
        sys.stderr.write(  # noqa: T201
            "FAIL: imported at runtime but missing from manifest.json requirements: "
            + ", ".join(undeclared)
            + "\n"
        )
        return 1

    sys.stdout.write(  # noqa: T201
        f"Runtime dependency check passed ({len(declared)} declared).\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
