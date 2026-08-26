from __future__ import annotations

import tarfile
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parents[2]

INTERNAL_ARTIFACTS = [
    "AGENTS.md",
    "ISSUES.md",
    "STRIX-AUDIT.md",
    "todo.md",
]

INTERNAL_DIRS = [
    "dashboard-ui",
    "docs/superpowers",
    "docs/audits",
]

REQUIRED_PACKAGE_FILES = [
    "pyproject.toml",
    "README.md",
    "LICENSE",
    "src/janus/__init__.py",
    "src/janus/cli.py",
    "src/janus/app.py",
    "src/janus/dashboard/static/app/index.html",
]


def _build_sdist(tmp_path: Path) -> Path:
    pytest.importorskip("hatchling")
    from hatchling.builders.sdist import SdistBuilder

    builder = SdistBuilder(str(PROJECT_ROOT))
    artifacts = list(builder.build(directory=str(tmp_path)))
    assert artifacts, "sdist build produced no artifacts"
    return tmp_path / artifacts[0]


def _sdist_members(archive: Path) -> set[str]:
    prefix = "janus_ai-3.1.0/"
    with tarfile.open(archive, "r:gz") as tar:
        return {name[len(prefix) :] if name.startswith(prefix) else name for name in tar.getnames()}


def test_sdist_excludes_internal_artifacts(tmp_path: Path) -> None:
    members = _sdist_members(_build_sdist(tmp_path))
    for artifact in INTERNAL_ARTIFACTS:
        assert artifact not in members, f"internal artifact {artifact} leaked into sdist"


def test_sdist_excludes_internal_source_trees(tmp_path: Path) -> None:
    members = _sdist_members(_build_sdist(tmp_path))
    for directory in INTERNAL_DIRS:
        leaking = [m for m in members if m.startswith(f"{directory}/")]
        assert not leaking, f"internal tree {directory}/ leaked into sdist: {leaking[:3]}"


def test_sdist_keeps_package_source_and_bundle(tmp_path: Path) -> None:
    members = _sdist_members(_build_sdist(tmp_path))
    for required in REQUIRED_PACKAGE_FILES:
        assert required in members, f"required file {required} missing from sdist"
