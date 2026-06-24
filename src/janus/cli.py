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
  require_api_key: false

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
app.add_typer(keys_app, name="keys")
app.add_typer(usage_app, name="usage")


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
