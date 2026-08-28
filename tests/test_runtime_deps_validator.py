"""Tests for scripts/validate_runtime_deps.py.

The validator is a CI gate, so its own failure modes need proving: a gate that
cannot fail is worse than no gate, because it reads as evidence. Each case runs
the script against a fixture tree laid out like the repository, so the checks are
exercised through the same file-reading paths CI uses.

Mirrors tests/test_release_metadata_validator.py.
"""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys
import textwrap

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "validate_runtime_deps.py"


def _tree(
    root: Path,
    *,
    requirements: list[str] | None = None,
    hacs_floor: str = "2024.5.0b1",
    requirements_floor: str = "homeassistant~=2024.5",
    pyproject_floor: str = 'homeassistant = ">=2024.5.0"',
    source: str = "import pandas as pd\n",
) -> Path:
    """Lay out a minimal repository the validator can be pointed at."""
    component = root / "custom_components" / "adaptive_cover"
    component.mkdir(parents=True)
    (component / "coordinator.py").write_text(source, encoding="utf-8")
    (component / "manifest.json").write_text(
        json.dumps(
            {
                "domain": "adaptive_cover",
                "requirements": (
                    ["astral==2.2", "numpy", "pandas", "python-dateutil"]
                    if requirements is None
                    else requirements
                ),
                "version": "0.3.16",
            }
        ),
        encoding="utf-8",
    )
    (root / "hacs.json").write_text(
        json.dumps({"name": "x", "homeassistant": hacs_floor}), encoding="utf-8"
    )
    (root / "requirements.txt").write_text(
        f"{requirements_floor}\npandas~=2.2\n", encoding="utf-8"
    )
    (root / "pyproject.toml").write_text(
        textwrap.dedent(f"""\
            [tool.poetry]
            version = "0.3.16"

            [tool.poetry.dependencies]
            {pyproject_floor}
            python = "^3.11"
            """),
        encoding="utf-8",
    )
    scripts = root / "scripts"
    scripts.mkdir()
    shutil.copy(SCRIPT, scripts / SCRIPT.name)
    return root


def _run(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [sys.executable, str(root / "scripts" / SCRIPT.name)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_a_consistent_tree_passes(tmp_path: Path) -> None:
    """Guard the guard: the validator must not fail on a correct repository."""
    result = _run(_tree(tmp_path))

    assert result.returncode == 0, result.stderr
    assert "passed" in result.stdout


# --- undeclared runtime imports ---------------------------------------------


def test_an_undeclared_import_fails_and_names_the_package(tmp_path: Path) -> None:
    """The check that would have caught the missing numpy declaration."""
    result = _run(
        _tree(
            tmp_path,
            source="import numpy as np\nimport pandas as pd\n",
            requirements=["astral==2.2", "python-dateutil"],
        )
    )

    assert result.returncode == 1
    assert "pandas" in result.stderr
    assert "numpy" in result.stderr


def test_an_import_root_is_mapped_to_its_distribution_name(tmp_path: Path) -> None:
    """The import root is `dateutil`; the declaration is `python-dateutil`."""
    result = _run(
        _tree(
            tmp_path,
            source="from dateutil import parser\n",
            requirements=["astral==2.2"],
        )
    )

    assert result.returncode == 1
    assert "python-dateutil" in result.stderr


def test_stdlib_and_core_provided_imports_are_not_required(tmp_path: Path) -> None:
    """Core installs voluptuous and homeassistant; stdlib needs no declaring."""
    result = _run(
        _tree(
            tmp_path,
            source="import asyncio\nimport voluptuous as vol\nfrom homeassistant.core import callback\n",
            requirements=[],
        )
    )

    assert result.returncode == 0, result.stderr


# --- Home Assistant floor agreement ----------------------------------------


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"hacs_floor": "2026.7.0"}, "hacs.json declares 2026.7"),
        (
            {"requirements_floor": "homeassistant~=2026.7"},
            "requirements.txt declares 2026.7",
        ),
        (
            {"pyproject_floor": 'homeassistant = ">=2024.4.0"'},
            "pyproject.toml declares 2024.4",
        ),
    ],
)
def test_any_one_file_disagreeing_on_the_floor_fails(
    tmp_path: Path, kwargs: dict, expected: str
) -> None:
    """All three declarations must agree, whichever one drifts."""
    result = _run(_tree(tmp_path, **kwargs))

    assert result.returncode == 1
    assert "must agree" in result.stderr
    assert expected in result.stderr


def test_the_failure_names_every_file_and_its_value(tmp_path: Path) -> None:
    """A gate that says only "mismatch" makes the reader go hunting."""
    result = _run(_tree(tmp_path, pyproject_floor='homeassistant = ">=2024.4.0"'))

    for name in ("hacs.json", "requirements.txt", "pyproject.toml"):
        assert name in result.stderr


def test_a_missing_floor_declaration_fails(tmp_path: Path) -> None:
    """Deleting the line must not be a way to pass the agreement check."""
    result = _run(_tree(tmp_path, pyproject_floor='python_only = "1"'))

    assert result.returncode == 1
    assert "pyproject.toml" in result.stderr


# --- blocking file I/O ------------------------------------------------------


def test_blocking_file_io_fails_and_names_the_line(tmp_path: Path) -> None:
    """A debug open() on a hardcoded path shipped to a live install once."""
    result = _run(
        _tree(
            tmp_path,
            source='import pandas as pd\n\n\ndef f():\n    open("/config/debug.log", "a").write("x")\n',
        )
    )

    assert result.returncode == 1
    assert "blocking file I/O" in result.stderr
    assert "coordinator.py:5" in result.stderr


def test_the_real_repository_has_no_blocking_file_io() -> None:
    """Assert it about this repository, not only about a fixture."""
    result = subprocess.run(  # noqa: S603
        [sys.executable, str(SCRIPT)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
