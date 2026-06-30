"""Tests for release metadata validation script."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VALIDATOR_PATH = ROOT / "scripts" / "validate_release_metadata.py"


def _load_validator_module():
    spec = importlib.util.spec_from_file_location(
        "validate_release_metadata",
        VALIDATOR_PATH,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load release metadata validator module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_repo_surface(
    base: Path,
    *,
    manifest_version: str = "0.3.8",
    pyproject_version: str = "0.3.8",
    include_readme_ref: bool = True,
    include_changelog_ref: bool = True,
    release_drafter_body: str = "prerelease-identifier: beta\n",
) -> None:
    (base / "custom_components" / "adaptive_cover").mkdir(parents=True)
    (base / ".github").mkdir(parents=True)

    (base / "custom_components" / "adaptive_cover" / "manifest.json").write_text(
        json.dumps({"version": manifest_version}),
        encoding="utf-8",
    )
    # Include another section with a version first to ensure parser is section-aware.
    (base / "pyproject.toml").write_text(
        "\n".join(
            [
                "[tool.bumpversion]",
                'version = "999.0.0"',
                "",
                "[tool.poetry]",
                'name = "adaptive_cover"',
                f'version = "{pyproject_version}"',
            ]
        ),
        encoding="utf-8",
    )
    (base / "README.md").write_text(
        (
            f"Current release: [v{manifest_version}]"
            if include_readme_ref
            else "Current release: [v0.0.0]"
        ),
        encoding="utf-8",
    )
    (base / "CHANGELOG.md").write_text(
        (f"## [{manifest_version}]" if include_changelog_ref else "## [0.0.0]"),
        encoding="utf-8",
    )
    (base / ".github" / "release-drafter.yml").write_text(
        release_drafter_body,
        encoding="utf-8",
    )


def test_validator_passes_with_matching_versions_and_refs(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    """Validator should pass when release surfaces are consistent."""
    _write_repo_surface(tmp_path)
    module = _load_validator_module()
    monkeypatch.setattr(module, "ROOT", tmp_path)

    assert module.main() == 0
    out, err = capsys.readouterr()
    assert "validation passed" in out.lower()
    assert err == ""


def test_validator_fails_on_version_mismatch(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    """Validator should fail when manifest and pyproject versions diverge."""
    _write_repo_surface(tmp_path, pyproject_version="0.3.9")
    module = _load_validator_module()
    monkeypatch.setattr(module, "ROOT", tmp_path)

    assert module.main() == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert "version mismatch" in err.lower()


def test_validator_fails_on_release_drafter_typos(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    """Validator should fail when known release-drafter typo keys are present."""
    _write_repo_surface(
        tmp_path,
        release_drafter_body="\n".join(
            [
                "prereleas-identifier: beta",
                "exlude-contributors:",
            ]
        ),
    )
    module = _load_validator_module()
    monkeypatch.setattr(module, "ROOT", tmp_path)

    assert module.main() == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert "prereleas-identifier" in err
    assert "exlude-contributors" in err


def test_validator_fails_on_missing_readme_release_ref(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    """Validator should fail when README release reference is missing."""
    _write_repo_surface(tmp_path, include_readme_ref=False)
    module = _load_validator_module()
    monkeypatch.setattr(module, "ROOT", tmp_path)

    assert module.main() == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert "readme.md missing current release reference" in err.lower()


def test_validator_fails_on_missing_changelog_heading(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    """Validator should fail when CHANGELOG release heading is missing."""
    _write_repo_surface(tmp_path, include_changelog_ref=False)
    module = _load_validator_module()
    monkeypatch.setattr(module, "ROOT", tmp_path)

    assert module.main() == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert "changelog.md missing heading" in err.lower()


def test_validator_fails_cleanly_when_manifest_is_missing(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    """Validator should return a clean error when a required file is absent."""
    _write_repo_surface(tmp_path)
    (tmp_path / "custom_components" / "adaptive_cover" / "manifest.json").unlink()
    module = _load_validator_module()
    monkeypatch.setattr(module, "ROOT", tmp_path)

    assert module.main() == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert "manifest.json" in err


def test_validator_parses_pyproject_version_with_inline_comment(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    """Version parsing should ignore inline comments on the pyproject version."""
    _write_repo_surface(tmp_path)
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        pyproject.read_text(encoding="utf-8").replace(
            'version = "0.3.8"',
            'version = "0.3.8"  # comment',
        ),
        encoding="utf-8",
    )
    module = _load_validator_module()
    monkeypatch.setattr(module, "ROOT", tmp_path)

    assert module.main() == 0
    out, err = capsys.readouterr()
    assert "validation passed" in out.lower()
    assert err == ""
