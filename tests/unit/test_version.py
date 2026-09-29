from __future__ import annotations

import json
import tomllib
from importlib.metadata import version as package_version
from pathlib import Path

from janus import __version__
from janus.app import create_app

PROJECT_ROOT = Path(__file__).parents[2]


def test_release_versions_stay_synchronized() -> None:
    project = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dashboard = json.loads(
        (PROJECT_ROOT / "dashboard-ui" / "package.json").read_text(encoding="utf-8")
    )
    bundle = json.loads(
        (
            PROJECT_ROOT
            / "src"
            / "janus"
            / "dashboard"
            / "static"
            / "app"
            / "_app"
            / "version.json"
        ).read_text(encoding="utf-8")
    )
    version = __version__

    assert project["project"].get("version") is None
    assert project["project"]["dynamic"] == ["version"]
    assert project["tool"]["hatch"]["version"]["path"] == "src/janus/__init__.py"
    assert dashboard["version"] == version
    assert package_version("janus-ai") == version
    assert create_app().version == version
    assert bundle["version"] == version
