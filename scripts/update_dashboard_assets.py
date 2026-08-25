#!/usr/bin/env python3

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src" / "janus" / "dashboard" / "static"
VENDOR = STATIC / "vendor"
TAILWIND_VERSION = "3.4.17"
TAILWIND_LINUX_X64_URL = (
    "https://github.com/tailwindlabs/tailwindcss/releases/download/"
    f"v{TAILWIND_VERSION}/tailwindcss-linux-x64"
)
TAILWIND_LINUX_X64_SHA256 = "7d24f7fa191d2193b78cd5f5a42a6093e14409521908529f42d80b11fde1f1d4"
ASSETS = {
    "htmx-2.0.4.min.js": (
        "https://cdn.jsdelivr.net/npm/htmx.org@2.0.4/dist/htmx.min.js",
        "e209dda5c8235479f3166defc7750e1dbcd5a5c1808b7792fc2e6733768fb447",
    ),
    "chart.js-4.4.1.umd.min.js": (
        "https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js",
        "d2af8974e95271638772e9e9524db5b9a6f58d6ec2d5d781400447b4a31c681e",
    ),
    "d3-7.9.0.min.js": (
        "https://cdn.jsdelivr.net/npm/d3@7.9.0/dist/d3.min.js",
        "f2094bbf6141b359722c4fe454eb6c4b0f0e42cc10cc7af921fc158fceb86539",
    ),
    "d3-sankey-0.12.3.min.js": (
        "https://cdn.jsdelivr.net/npm/d3-sankey@0.12.3/dist/d3-sankey.min.js",
        "8286db5d6aa049cc6e8a546708943b79dfb4daaefb0ccf42af674ec0ee4c86be",
    ),
    "licenses/htmx-2.0.4.LICENSE.txt": (
        "https://cdn.jsdelivr.net/npm/htmx.org@2.0.4/LICENSE",
        "d3d2456f76414f2456104660ebd65aff1c04cd7966b942bdabd63f3cdb316a38",
    ),
    "licenses/chart.js-4.4.1.LICENSE.txt": (
        "https://cdn.jsdelivr.net/npm/chart.js@4.4.1/LICENSE.md",
        "5a0877ad6d818529be4f33009d0942cdf7e2ed7656156f4aba7308459a546030",
    ),
    "licenses/d3-7.9.0.LICENSE.txt": (
        "https://cdn.jsdelivr.net/npm/d3@7.9.0/LICENSE",
        "3e6849627f74ff73c257a3ae1efb574015d94fc1035c05ec3c15805165efcbc4",
    ),
    "licenses/d3-sankey-0.12.3.LICENSE.txt": (
        "https://cdn.jsdelivr.net/npm/d3-sankey@0.12.3/LICENSE",
        "2bf785e778d67a4f5266cffcd4f2cc5bb98cde73791666e7efeb8002ba32dfa5",
    ),
    "licenses/tailwindcss-3.4.17.LICENSE.txt": (
        "https://raw.githubusercontent.com/tailwindlabs/tailwindcss/v3.4.17/LICENSE",
        "60e0b68c0f35c078eef3a5d29419d0b03ff84ec1df9c3f9d6e39a519a5ae7985",
    ),
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download(url: str, destination: Path, expected_sha256: str) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "Janus asset updater"})
    with urllib.request.urlopen(request) as response, destination.open("wb") as output:
        shutil.copyfileobj(response, output)
    actual_sha256 = sha256(destination)
    if actual_sha256 != expected_sha256:
        raise RuntimeError(
            f"Checksum mismatch for {url}: expected {expected_sha256}, got {actual_sha256}"
        )


def resolve_tailwind_cli(work_dir: Path, override: Path | None) -> Path:
    if override is not None:
        executable = override.resolve()
        if not executable.is_file():
            raise RuntimeError(f"Tailwind CLI does not exist: {executable}")
        return executable
    if platform.system() != "Linux" or platform.machine() not in {"x86_64", "amd64"}:
        raise RuntimeError("Pass --tailwind-cli with the Tailwind CSS 3.4.17 standalone CLI")
    executable = work_dir / "tailwindcss"
    download(TAILWIND_LINUX_X64_URL, executable, TAILWIND_LINUX_X64_SHA256)
    executable.chmod(0o755)
    return executable


def verify_tailwind_version(executable: Path) -> None:
    result = subprocess.run(
        [str(executable), "--help"],
        check=True,
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    version_output = result.stdout + result.stderr
    if f"tailwindcss v{TAILWIND_VERSION}" not in version_output:
        raise RuntimeError(
            f"Expected Tailwind CSS {TAILWIND_VERSION}: {version_output.splitlines()[0]}"
        )


def template_digest() -> str:
    digest = hashlib.sha256()
    templates = ROOT / "src" / "janus" / "dashboard" / "templates"
    for path in sorted(templates.glob("**/*.html")):
        digest.update(path.relative_to(ROOT).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def update(tailwind_cli: Path | None) -> None:
    VENDOR.mkdir(parents=True, exist_ok=True)
    (STATIC / "css").mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".janus-dashboard-assets-", dir=ROOT) as temp_name:
        temp_dir = Path(temp_name)
        downloaded: dict[str, Path] = {}
        for relative_path, (url, expected_sha256) in ASSETS.items():
            destination = temp_dir / relative_path
            destination.parent.mkdir(parents=True, exist_ok=True)
            download(url, destination, expected_sha256)
            downloaded[relative_path] = destination

        executable = resolve_tailwind_cli(temp_dir, tailwind_cli)
        verify_tailwind_version(executable)
        css_output = temp_dir / "dashboard.min.css"
        subprocess.run(
            [
                str(executable),
                "--input",
                str(STATIC / "css" / "tailwind.input.css"),
                "--output",
                str(css_output),
                "--config",
                str(ROOT / "scripts" / "tailwind.dashboard.config.js"),
                "--minify",
            ],
            check=True,
            cwd=ROOT,
        )

        for relative_path, source in downloaded.items():
            destination = VENDOR / relative_path
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(source, destination)
        os.replace(css_output, STATIC / "css" / "dashboard.min.css")

    files = {
        path.relative_to(STATIC).as_posix(): sha256(path)
        for path in sorted(VENDOR.glob("**/*"))
        if path.is_file() and path.name not in {"manifest.json", "README.md"}
    }
    dashboard_css = STATIC / "css" / "dashboard.min.css"
    files[dashboard_css.relative_to(STATIC).as_posix()] = sha256(dashboard_css)
    manifest = {
        "files": files,
        "tailwind": {
            "compiler_sha256": TAILWIND_LINUX_X64_SHA256,
            "compiler_url": TAILWIND_LINUX_X64_URL,
            "config_sha256": sha256(ROOT / "scripts" / "tailwind.dashboard.config.js"),
            "input_sha256": sha256(STATIC / "css" / "tailwind.input.css"),
            "templates_sha256": template_digest(),
            "version": TAILWIND_VERSION,
        },
    }
    (VENDOR / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tailwind-cli", type=Path)
    args = parser.parse_args()
    update(args.tailwind_cli)


if __name__ == "__main__":
    main()
