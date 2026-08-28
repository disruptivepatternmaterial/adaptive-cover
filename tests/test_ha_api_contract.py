"""Check the integration against real Home Assistant and its own manifest.

The rest of the suite runs against the stubs installed by ``tests/conftest.py``,
which replace every ``homeassistant.*`` module wholesale. Those stubs cannot
tell us whether an API we import still exists in Core, so an import of a helper
Core has not shipped yet passes the suite and then fails at runtime for every
user. The checks here deliberately bypass the stubs: the Home Assistant ones
run in a subprocess with no pytest and therefore no conftest, and the manifest
ones read source text rather than importing anything.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
COMPONENT_DIR = REPO_ROOT / "custom_components" / "adaptive_cover"
MANIFEST = COMPONENT_DIR / "manifest.json"

# Distributions Home Assistant Core installs for us, so the integration may
# import them without declaring them. Everything else has to be in
# manifest.json: relying on a transitive dependency means a version bump
# elsewhere can uninstall it.
CORE_PROVIDED = frozenset({"homeassistant", "voluptuous"})

# Import root -> distribution name to declare in manifest.json.
DISTRIBUTION_NAMES = {"dateutil": "python-dateutil"}


def _run_without_stubs(snippet: str) -> subprocess.CompletedProcess[str]:
    """Run a snippet in a fresh interpreter, uncontaminated by conftest stubs."""
    return subprocess.run(  # noqa: S603
        [sys.executable, "-c", textwrap.dedent(snippet)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def _require_real_home_assistant() -> None:
    probe = _run_without_stubs("import homeassistant.core")
    if probe.returncode != 0:
        pytest.skip("Home Assistant is not installed in this environment")


def _module_level_ha_imports() -> dict[str, set[str]]:
    """Map each unguarded ``homeassistant`` module to the names imported from it.

    Only column 0 imports are collected. An indented ``from homeassistant...``
    sits inside a ``try``/``except ImportError`` or a function, which is how the
    integration handles APIs that legitimately differ across Core versions, and
    those must not be required to resolve.
    """
    imports: dict[str, set[str]] = {}
    for path in sorted(COMPONENT_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or node.col_offset != 0:
                continue
            if node.level or not node.module:
                continue
            if node.module.split(".")[0] != "homeassistant":
                continue
            imports.setdefault(node.module, set()).update(
                alias.name for alias in node.names
            )
    return imports


def _third_party_import_roots() -> set[str]:
    """Collect every non-stdlib import root the integration uses."""
    stdlib = set(sys.stdlib_module_names)
    roots: set[str] = set()
    for path in sorted(COMPONENT_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
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


def test_every_unguarded_ha_import_exists_in_installed_core() -> None:
    """Each module-scope ``from homeassistant... import X`` must resolve."""
    _require_real_home_assistant()
    targets = _module_level_ha_imports()
    assert targets, "found no homeassistant imports to check"

    result = _run_without_stubs(f"""
        import importlib

        targets = {targets!r}
        missing = []
        for module_name, names in sorted(targets.items()):
            try:
                module = importlib.import_module(module_name)
            except ImportError as err:
                missing.append(f"{{module_name}} (module): {{err}}")
                continue
            for name in sorted(names):
                if not hasattr(module, name):
                    missing.append(f"{{module_name}}.{{name}}")

        if missing:
            print("MISSING: " + ", ".join(missing))
            raise SystemExit(1)
        print("OK")
        """)

    assert result.returncode == 0, (
        "integration imports Home Assistant APIs the installed Core does not "
        f"provide.\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )


def test_sun_module_builds_an_observer_on_installed_core() -> None:
    """``SunData`` must get a usable Observer whether or not Core ships the helper.

    ``get_astral_observer`` only exists from Core 2026.7. Below that the
    fallback in ``sun.py`` has to produce an equivalent Observer, so this
    asserts the coordinates land in the right fields on either path.
    """
    _require_real_home_assistant()

    result = _run_without_stubs("""
        import importlib.util
        import pathlib

        spec = importlib.util.spec_from_file_location(
            "_adaptive_cover_sun",
            pathlib.Path("custom_components/adaptive_cover/sun.py"),
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        class _Config:
            latitude = 52.5
            longitude = 5.25
            elevation = 12.0
            time_zone = "Europe/Amsterdam"

        class _Hass:
            config = _Config()

        observer = module.SunData("Europe/Amsterdam", _Hass()).observer
        assert observer.latitude == 52.5, observer
        assert observer.longitude == 5.25, observer
        assert observer.elevation == 12.0, observer
        print("OK")
        """)

    assert result.returncode == 0, (
        f"sun.py failed against real Home Assistant.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


def test_manifest_declares_every_third_party_import() -> None:
    """Runtime imports must be declared, not inherited from another package."""
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    declared = {
        requirement.split("==")[0].split(">=")[0].split("~=")[0].strip().lower()
        for requirement in manifest["requirements"]
    }

    undeclared = sorted(
        DISTRIBUTION_NAMES.get(root, root)
        for root in _third_party_import_roots()
        if root not in CORE_PROVIDED
        and DISTRIBUTION_NAMES.get(root, root).lower() not in declared
    )

    assert not undeclared, (
        f"imported at runtime but missing from manifest.json requirements: {undeclared}"
    )
