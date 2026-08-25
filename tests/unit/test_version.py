from __future__ import annotations

import json
import tomllib
from pathlib import Path

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
    version = project["project"]["version"]

    assert create_app().version == version
    assert dashboard["version"] == version
    assert bundle["version"] == version
