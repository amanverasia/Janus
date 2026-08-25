#!/usr/bin/env python3

from __future__ import annotations

import argparse
import filecmp
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UI_ROOT = ROOT / "dashboard-ui"
BUILD_ROOT = UI_ROOT / "build"
STATIC_ROOT = ROOT / "src" / "janus" / "dashboard" / "static"
APP_ROOT = STATIC_ROOT / "app"


def _run(command: list[str]) -> None:
    subprocess.run(command, cwd=UI_ROOT, check=True)


def _same_tree(left: Path, right: Path) -> bool:
    comparison = filecmp.dircmp(left, right)
    if comparison.left_only or comparison.right_only or comparison.funny_files:
        return False
    if comparison.diff_files:
        return False
    return all(_same_tree(left / name, right / name) for name in comparison.common_dirs)


def _build() -> None:
    _run(["npm", "ci"])
    _run(["npm", "run", "format:check"])
    _run(["npm", "run", "check"])
    _run(["npm", "run", "build"])
    if not (BUILD_ROOT / "index.html").is_file():
        raise RuntimeError("SvelteKit build did not produce build/index.html")


def _replace_bundle() -> None:
    STATIC_ROOT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".janus-dashboard-ui-", dir=STATIC_ROOT) as name:
        staged = Path(name) / "app"
        shutil.copytree(BUILD_ROOT, staged)
        previous = STATIC_ROOT / ".app-previous"
        if previous.exists():
            shutil.rmtree(previous)
        if APP_ROOT.exists():
            os.replace(APP_ROOT, previous)
        try:
            os.replace(staged, APP_ROOT)
        except Exception:
            if previous.exists():
                os.replace(previous, APP_ROOT)
            raise
        if previous.exists():
            shutil.rmtree(previous)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    _build()
    if args.check:
        if not APP_ROOT.is_dir() or not _same_tree(BUILD_ROOT, APP_ROOT):
            raise SystemExit("Committed dashboard bundle is out of date")
        return
    _replace_bundle()


if __name__ == "__main__":
    main()
