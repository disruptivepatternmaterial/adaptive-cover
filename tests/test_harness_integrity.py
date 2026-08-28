"""Check that the test harness is not lying about its own dependencies.

tests/conftest.py stubs third-party packages so the suite runs without them.
That is fine when a package is genuinely absent and a silent failure when it is
not: an installed pandas replaced by a stub whose ``date_range`` returns ``[]``
turns every solar-grid assertion into a tautology, and no amount of
``pytest.importorskip`` notices, because the stub is what ``sys.modules`` holds.

The check here is the one that would have caught it. For each package the
harness knows how to stub, probe importability in a subprocess with no pytest
and therefore no conftest, and fail if the package is really installed but the
in-process module is a stand-in.
"""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys

import pytest

from tests.harness import STUB_MARKER, is_stub

REPO_ROOT = Path(__file__).resolve().parent.parent

# Every package tests/conftest.py has an `except ImportError` stub for.
STUBBABLE = ("numpy", "pandas", "dateutil", "voluptuous")


def _installed_outside_pytest(name: str) -> bool:
    """Report whether ``name`` imports in an interpreter conftest never touched."""
    probe = subprocess.run(  # noqa: S603
        [sys.executable, "-c", f"import {name}"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return probe.returncode == 0


@pytest.mark.parametrize("name", STUBBABLE)
def test_an_installed_package_is_never_replaced_by_a_stub(name: str) -> None:
    """A stub may stand in for an absent package, never for a present one."""
    if not _installed_outside_pytest(name):
        pytest.skip(f"{name} is genuinely absent, so stubbing it is correct")
    assert not is_stub(name), (
        f"{name} is installed, but tests/conftest.py replaced it with a stub. "
        "Any test guarded by pytest.importorskip is now asserting against a "
        "MagicMock. Find what poisoned sys.modules before conftest imported it."
    )


def test_the_marker_conftest_writes_is_the_marker_harness_reads() -> None:
    """Pin the two literals together; they are declared independently."""
    homeassistant = sys.modules.get("homeassistant")
    assert homeassistant is not None, "conftest always stubs homeassistant"
    assert getattr(homeassistant, STUB_MARKER, False) is True
    assert is_stub("homeassistant")


def test_a_reused_module_is_not_branded_a_stub() -> None:
    """_mod() populates the real dateutil in place; it must stay unmarked.

    conftest reaches `_mod("dateutil")` only when `dateutil.parser` fails to
    import. If dateutil itself is installed, that call decorates the real
    package, and marking it would make requires_real() skip against real code.
    """
    if not _installed_outside_pytest("dateutil"):
        pytest.skip("dateutil is genuinely absent")
    assert not is_stub("dateutil")


def test_pytz_is_not_stubbed() -> None:
    """The pytz stub broke the pandas import; it must not come back.

    Regression for the defect this module exists to prevent: stubbing pytz made
    pandas raise "Can't determine version for pytz", which sent conftest down
    its pandas-stub branch on a machine where pandas was installed.
    """
    assert not is_stub("pytz")
