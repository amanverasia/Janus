#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from janus.inventory.migrate import import_dashboard_export


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import Dashboard_For_Apis export JSON into Janus upstream keys"
    )
    parser.add_argument("export_file", type=Path, help="Path to /api/keys/export JSON")
    parser.add_argument(
        "--db",
        type=Path,
        default=Path.home() / ".janus" / "janus.db",
        help="Path to Janus SQLite database",
    )
    parser.add_argument("--dry-run", action="store_true", help="Count rows without writing")
    args = parser.parse_args()

    count = asyncio.run(import_dashboard_export(args.db, args.export_file, dry_run=args.dry_run))
    action = "Would import" if args.dry_run else "Imported"
    print(f"{action} {count} upstream key(s) into {args.db}")


if __name__ == "__main__":
    main()
