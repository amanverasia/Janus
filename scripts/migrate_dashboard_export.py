#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from janus.inventory.migrate import (
    format_inventory_verification,
    import_dashboard_export,
    verify_inventory,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import Dashboard_For_Apis export JSON into Janus upstream keys"
    )
    parser.add_argument(
        "export_file",
        type=Path,
        nargs="?",
        help="Path to /api/keys/export JSON (omit with --verify-only)",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=Path.home() / ".janus" / "janus.db",
        help="Path to Janus SQLite database",
    )
    parser.add_argument("--dry-run", action="store_true", help="Count rows without writing")
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Print inventory summary after import",
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="Print inventory summary without importing",
    )
    args = parser.parse_args()

    if args.verify_only:
        summary = asyncio.run(verify_inventory(args.db))
        print(format_inventory_verification(summary))
        return

    if args.export_file is None:
        parser.error("export_file is required unless --verify-only is set")

    count = asyncio.run(import_dashboard_export(args.db, args.export_file, dry_run=args.dry_run))
    action = "Would import" if args.dry_run else "Imported"
    print(f"{action} {count} upstream key(s) into {args.db}")

    if args.verify and not args.dry_run:
        summary = asyncio.run(verify_inventory(args.db))
        print()
        print(format_inventory_verification(summary))


if __name__ == "__main__":
    main()
