"""Ask whether a dependency is the real package or a conftest stand-in.

``pytest.importorskip`` cannot answer this. ``tests/conftest.py`` registers its
stand-ins in ``sys.modules``, so the stub *is* the module and importorskip
reports success. A test that guards with importorskip therefore runs against a
``MagicMock`` and passes for the wrong reason -- which is how the solar-grid
tests came to assert nothing at all.

Guard with :func:`requires_real` instead.
"""

from __future__ import annotations

import sys

import pytest

# Set by tests/conftest.py on every module object it fabricates. Kept as a
# literal in both places rather than imported: conftest is loaded by pytest
# under a name that varies with import mode, and importing it from here would
# re-run the stubbing. tests/test_harness_integrity.py pins the two together.
STUB_MARKER = "__adaptive_cover_stub__"


def is_stub(name: str) -> bool:
    """Return True when ``name`` is registered but is a conftest stand-in."""
    module = sys.modules.get(name)
    return module is not None and getattr(module, STUB_MARKER, False) is True


def is_real(name: str) -> bool:
    """Return True when ``name`` is importable and is not a stand-in."""
    if is_stub(name):
        return False
    try:
        __import__(name)
    except ImportError:
        return False
    return True


def requires_real(*names: str) -> None:
    """Skip the calling test unless every named package is genuinely installed."""
    for name in names:
        if is_stub(name):
            pytest.skip(f"{name} is a tests/conftest.py stub, not the real package")
        try:
            __import__(name)
        except ImportError:
            pytest.skip(f"{name} is not installed")
