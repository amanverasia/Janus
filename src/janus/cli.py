from __future__ import annotations

from pathlib import Path

import typer
import uvicorn

from janus.app import create_app
from janus.config.loader import load_config

app = typer.Typer(name="janus", help="The two-faced AI routing gateway")

TEMPLATE_YAML = """# Janus configuration
server:
  port: 20128
  host: 127.0.0.1
  require_api_key: true

providers:
  # - id: glm
  #   prefix: glm
  #   api_type: openai_compat
  #   base_url: https://open.bigmodel.cn/api/paas/v4
  #   api_key: ${GLM_API_KEY}
  #   models: [glm-4.7]
"""


@app.command()
def serve(
    port: int = typer.Option(20128, "--port", "-p", help="Port to listen on"),
    host: str = typer.Option("127.0.0.1", "--host", help="Host to bind to"),
    config: str = typer.Option(
        "~/.janus/config.yaml", "--config", "-c", help="Path to config file"
    ),
    reload: bool = typer.Option(False, "--reload", help="Enable auto-reload for development"),
) -> None:
    """Start the Janus gateway server."""
    config_path = Path(config).expanduser()
    janus_config = load_config(config_path)
    app_obj = create_app(config=janus_config)
    uvicorn.run(app_obj, host=host, port=port, reload=reload, log_level="info")


@app.command(name="config-init")
def config_init(
    path: str = typer.Option("~/.janus/config.yaml", "--path", "-p", help="Where to create config"),
) -> None:
    """Create a default config file."""
    config_path = Path(path).expanduser()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    if config_path.exists():
        typer.echo(f"Config already exists: {config_path}")
        return
    config_path.write_text(TEMPLATE_YAML)
    typer.echo(f"Config created: {config_path}")


@app.command(name="config-path")
def config_path_cmd() -> None:
    """Print the default config file path."""
    typer.echo(str(Path("~/.janus/config.yaml").expanduser()))


keys_app = typer.Typer(help="Manage API keys")
usage_app = typer.Typer(help="Usage statistics")
budgets_app = typer.Typer(help="Manage spending budgets")
pricing_app = typer.Typer(help="View model pricing")
inventory_app = typer.Typer(help="Upstream key inventory")
app.add_typer(keys_app, name="keys")
app.add_typer(usage_app, name="usage")
app.add_typer(budgets_app, name="budgets")
app.add_typer(pricing_app, name="pricing")
app.add_typer(inventory_app, name="inventory")


def _get_db_path(config: str) -> Path:
    cfg = load_config(Path(config).expanduser())
    return cfg.server.data_dir / "janus.db"


@keys_app.command("create")
def keys_create(
    name: str = typer.Option("default", "--name", "-n", help="Name for this key"),
    config: str = typer.Option("~/.janus/config.yaml", "--config", "-c"),
) -> None:
    """Create a new API key."""
    import asyncio

    from janus.storage.api_keys import create_key
    from janus.storage.database import init_db

    db_path = _get_db_path(config)
    asyncio.run(init_db(db_path))
    key, record = asyncio.run(create_key(db_path, name=name))
    typer.echo(f"API Key (save this — shown once): {key}")
    typer.echo(f"ID: {record['id']}  Name: {record['name']}")


@keys_app.command("list")
def keys_list(
    config: str = typer.Option("~/.janus/config.yaml", "--config", "-c"),
) -> None:
    """List all API keys."""
    import asyncio

    from janus.storage.api_keys import list_keys as do_list
    from janus.storage.database import init_db

    db_path = _get_db_path(config)
    asyncio.run(init_db(db_path))
    keys = asyncio.run(do_list(db_path))
    if not keys:
        typer.echo("No API keys found.")
        return
    for k in keys:
        status = "active" if k["is_active"] else "revoked"
        typer.echo(
            f"  {k['id']:>3}  {k['prefix']}...  {k['name']:<20}  {status}  {k['created_at']}"
        )


@keys_app.command("revoke")
def keys_revoke(
    key_id: int = typer.Argument(..., help="Key ID to revoke"),
    config: str = typer.Option("~/.janus/config.yaml", "--config", "-c"),
) -> None:
    """Revoke an API key."""
    import asyncio

    from janus.storage.api_keys import revoke_key
    from janus.storage.database import init_db

    db_path = _get_db_path(config)
    asyncio.run(init_db(db_path))
    asyncio.run(revoke_key(db_path, key_id))
    typer.echo(f"Revoked key {key_id}")


@usage_app.command("stats")
def usage_stats(
    config: str = typer.Option("~/.janus/config.yaml", "--config", "-c"),
) -> None:
    """Show usage statistics."""
    import asyncio

    from janus.storage.database import init_db
    from janus.storage.usage import get_usage_stats

    db_path = _get_db_path(config)
    asyncio.run(init_db(db_path))
    stats = asyncio.run(get_usage_stats(db_path))
    typer.echo(f"Total requests: {stats['total_requests']}")
    typer.echo(f"Total input tokens: {stats['total_input_tokens']}")
    typer.echo(f"Total output tokens: {stats['total_output_tokens']}")
    if stats["by_model"]:
        typer.echo("\nBy model:")
        for m in stats["by_model"]:
            typer.echo(
                f"  {m['model']:<30}  {m['requests']:>5} requests  "
                f"{m['input_tokens']:>8} in  {m['output_tokens']:>8} out"
            )


@usage_app.command("cost")
def usage_cost(
    days: int = typer.Option(30, "--days", "-d", help="Number of days to show"),
    config: str = typer.Option("~/.janus/config.yaml", "--config", "-c"),
) -> None:
    """Show cost breakdown by model."""
    import asyncio

    from janus.storage.analytics import get_breakdown
    from janus.storage.database import init_db

    db_path = _get_db_path(config)
    asyncio.run(init_db(db_path))
    rows = asyncio.run(get_breakdown(db_path, dimension="model", days=days))
    if not rows:
        typer.echo("No usage data.")
        return
    total_cost = sum(r["cost"] for r in rows)
    typer.echo(f"Cost breakdown (last {days} days):")
    typer.echo(f"{'Model':<35} {'Requests':>8} {'Cost':>12}")
    typer.echo("-" * 58)
    for r in rows:
        typer.echo(f"  {r['model'] or '—':<33} {r['requests']:>8} ${r['cost']:>10.4f}")
    typer.echo("-" * 58)
    typer.echo(f"  {'Total':<33} {'':>8} ${total_cost:>10.4f}")


@usage_app.command("by-key")
def usage_by_key(
    days: int = typer.Option(30, "--days", "-d", help="Number of days to show"),
    config: str = typer.Option("~/.janus/config.yaml", "--config", "-c"),
) -> None:
    """Show spending per client API key."""
    import asyncio

    from janus.storage.analytics import get_breakdown
    from janus.storage.database import init_db

    db_path = _get_db_path(config)
    asyncio.run(init_db(db_path))
    rows = asyncio.run(get_breakdown(db_path, dimension="client_key", days=days))
    if not rows:
        typer.echo("No per-key usage data.")
        return
    typer.echo(f"Spending per key (last {days} days):")
    typer.echo(f"{'Key':<25} {'Requests':>8} {'Cost':>12}")
    typer.echo("-" * 48)
    for r in rows:
        name = r.get("client_key") or "No key"
        typer.echo(f"  {name:<23} {r['requests']:>8} ${r['cost']:>10.4f}")


@budgets_app.command("list")
def budgets_list(
    config: str = typer.Option("~/.janus/config.yaml", "--config", "-c"),
) -> None:
    """List all active budgets."""
    import asyncio

    from janus.storage.budgets import get_budget_status, get_budgets
    from janus.storage.database import init_db

    db_path = _get_db_path(config)
    asyncio.run(init_db(db_path))
    budgets = asyncio.run(get_budgets(db_path))
    if not budgets:
        typer.echo("No budgets found.")
        return
    for b in budgets:
        status = asyncio.run(get_budget_status(db_path, key_id=b["key_id"]))
        scope = f"Key #{b['key_id']}" if b["key_id"] else "Global"
        spend_str = f"${status['today_spend']:.2f}" if status else "—"
        limit_str = f"${b['daily_limit']:.2f}"
        pct_str = f"{status['pct_used']:.0f}%" if status else "—"
        st = status["status"] if status else "—"
        typer.echo(
            f"  {b['id']:>3}  {scope:<15}  {limit_str:>10}  "
            f"spent: {spend_str:>10}  {pct_str:>6}  {st}"
        )


@budgets_app.command("set")
def budgets_set(
    daily: float = typer.Option(..., "--daily", "-d", help="Daily limit in USD"),
    key: str = typer.Option("global", "--key", "-k", help="Key name or 'global'"),
    warn: float = typer.Option(80, "--warn", "-w", help="Warn threshold percentage"),
    config: str = typer.Option("~/.janus/config.yaml", "--config", "-c"),
) -> None:
    """Create or update a budget."""
    import asyncio

    from janus.storage.api_keys import list_keys
    from janus.storage.budgets import create_or_update_budget
    from janus.storage.database import init_db

    db_path = _get_db_path(config)
    asyncio.run(init_db(db_path))

    key_id: int | None = None
    if key != "global":
        keys = asyncio.run(list_keys(db_path))
        match = next((k for k in keys if k["name"] == key), None)
        if match is None:
            typer.echo(f"Key '{key}' not found.")
            raise typer.Exit(1)
        key_id = match["id"]

    budget_id = asyncio.run(
        create_or_update_budget(db_path, key_id=key_id, daily_limit=daily, warn_pct=warn)
    )
    scope = key if key == "global" else f"key '{key}'"
    typer.echo(f"Budget {budget_id} set: {scope} daily limit = ${daily:.2f}, warn at {warn:.0f}%")


@budgets_app.command("delete")
def budgets_delete(
    budget_id: int = typer.Argument(..., help="Budget ID to delete"),
    config: str = typer.Option("~/.janus/config.yaml", "--config", "-c"),
) -> None:
    """Delete a budget."""
    import asyncio

    from janus.storage.budgets import delete_budget
    from janus.storage.database import init_db

    db_path = _get_db_path(config)
    asyncio.run(init_db(db_path))
    deleted = asyncio.run(delete_budget(db_path, budget_id))
    if deleted:
        typer.echo(f"Deleted budget {budget_id}")
    else:
        typer.echo(f"Budget {budget_id} not found")
        raise typer.Exit(1)


@pricing_app.command("list")
def pricing_list(
    config: str = typer.Option("~/.janus/config.yaml", "--config", "-c"),
) -> None:
    """List all known model pricing."""
    from janus.config.loader import load_config
    from janus.pricing.registry import PricingRegistry

    cfg = load_config(Path(config).expanduser())
    reg = PricingRegistry(cfg.pricing)
    all_pricing = reg.get_all()
    for model in sorted(all_pricing.keys()):
        p = all_pricing[model]
        typer.echo(
            f"  {model:<40}  "
            f"in: ${p.input_per_mtok:<6}  "
            f"out: ${p.output_per_mtok:<6}  "
            f"cc: ${p.cache_creation_per_mtok:<6}  "
            f"cr: ${p.cache_read_per_mtok:<6}"
        )


@pricing_app.command("show")
def pricing_show(
    model: str = typer.Argument(..., help="Model name"),
    config: str = typer.Option("~/.janus/config.yaml", "--config", "-c"),
) -> None:
    """Show pricing for a specific model."""
    from janus.config.loader import load_config
    from janus.pricing.registry import PricingRegistry

    cfg = load_config(Path(config).expanduser())
    reg = PricingRegistry(cfg.pricing)
    p = reg.get(model)
    if p is None:
        typer.echo(f"No pricing found for '{model}'")
        raise typer.Exit(1)
    typer.echo(f"Model: {model}")
    typer.echo(f"  Input:              ${p.input_per_mtok} / Mtok")
    typer.echo(f"  Output:             ${p.output_per_mtok} / Mtok")
    typer.echo(f"  Cache creation:     ${p.cache_creation_per_mtok} / Mtok")
    typer.echo(f"  Cache read:         ${p.cache_read_per_mtok} / Mtok")


@inventory_app.command("generate-encryption-key")
def inventory_generate_encryption_key() -> None:
    """Generate a Fernet key for INVENTORY_ENCRYPTION_KEY."""
    from janus.inventory.key_encryption import generate_encryption_key

    typer.echo(generate_encryption_key())


@inventory_app.command("encrypt-keys")
def inventory_encrypt_keys(
    config: str = typer.Option("~/.janus/config.yaml", "--config", "-c"),
) -> None:
    """Encrypt plaintext upstream keys at rest (requires INVENTORY_ENCRYPTION_KEY)."""
    import asyncio

    from janus.inventory.key_encryption import encryption_enabled
    from janus.storage.database import init_db
    from janus.storage.upstream_keys import (
        count_storage_encryption_state,
        reencrypt_plaintext_upstream_keys,
    )

    if not encryption_enabled():
        typer.echo("Set INVENTORY_ENCRYPTION_KEY before running encrypt-keys.")
        raise typer.Exit(1)

    db_path = _get_db_path(config)
    asyncio.run(init_db(db_path))
    before = asyncio.run(count_storage_encryption_state(db_path))
    converted = asyncio.run(reencrypt_plaintext_upstream_keys(db_path))
    after = asyncio.run(count_storage_encryption_state(db_path))
    typer.echo(f"Encrypted {converted} key(s).")
    typer.echo(
        f"Storage state: {after['encrypted']} encrypted, {after['plaintext']} plaintext "
        f"(was {before['encrypted']} encrypted, {before['plaintext']} plaintext)"
    )


@inventory_app.command("verify")
def inventory_verify(
    config: str = typer.Option("~/.janus/config.yaml", "--config", "-c"),
) -> None:
    """Summarize upstream key inventory for cutover verification."""
    import asyncio

    from janus.inventory.migrate import format_inventory_verification, verify_inventory
    from janus.storage.database import init_db

    db_path = _get_db_path(config)
    asyncio.run(init_db(db_path))
    summary = asyncio.run(verify_inventory(db_path))
    typer.echo(format_inventory_verification(summary))


@inventory_app.command("migrate")
def inventory_migrate(
    export_file: Path = typer.Argument(..., help="Dashboard export JSON path"),
    config: str = typer.Option("~/.janus/config.yaml", "--config", "-c"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Count rows without writing"),
    verify: bool = typer.Option(False, "--verify", help="Print summary after import"),
) -> None:
    """Import Dashboard_For_Apis export JSON into upstream keys."""
    import asyncio

    from janus.inventory.migrate import (
        format_inventory_verification,
        import_dashboard_export,
        verify_inventory,
    )

    db_path = _get_db_path(config)
    count = asyncio.run(import_dashboard_export(db_path, export_file, dry_run=dry_run))
    action = "Would import" if dry_run else "Imported"
    typer.echo(f"{action} {count} upstream key(s) into {db_path}")
    if verify and not dry_run:
        summary = asyncio.run(verify_inventory(db_path))
        typer.echo("")
        typer.echo(format_inventory_verification(summary))
