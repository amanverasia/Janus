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
