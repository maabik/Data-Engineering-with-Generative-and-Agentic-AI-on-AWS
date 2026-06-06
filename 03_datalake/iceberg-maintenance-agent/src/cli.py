#!/usr/bin/env python3
"""
Iceberg Maintenance Agent CLI

Sub-commands:
  validate    Validate a configuration file
  once        Run rule-based analysis (and optionally execute maintenance)
  continuous  Run rule-based monitoring on a loop
  agent       Run the Claude-powered AI maintenance agent
"""
from __future__ import annotations

import logging
import sys

import click

from monitor.config import ConfigManager


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        level=level,
    )


@click.group()
@click.option("--config", "-c", required=True, help="Path to YAML configuration file")
@click.option("--verbose", "-v", is_flag=True, default=False)
@click.pass_context
def main(ctx: click.Context, config: str, verbose: bool) -> None:
    _setup_logging(verbose)
    ctx.ensure_object(dict)
    ctx.obj["config_path"] = config
    ctx.obj["verbose"] = verbose


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------
@main.command()
@click.pass_context
def validate(ctx: click.Context) -> None:
    """Validate the configuration file."""
    cfg = ConfigManager.load_from_file(ctx.obj["config_path"])
    errors = ConfigManager.validate_config(cfg)
    if errors:
        click.echo("Configuration errors:")
        for e in errors:
            click.echo(f"  - {e}")
        sys.exit(1)
    click.echo("Configuration is valid.")
    click.echo(f"Tables configured: {len(cfg.tables)}")
    for t in cfg.tables:
        click.echo(f"  {t}")


# ---------------------------------------------------------------------------
# once (rule-based)
# ---------------------------------------------------------------------------
@main.command()
@click.option("--execute", is_flag=True, default=False, help="Execute recommended maintenance operations")
@click.option("--dry-run", is_flag=True, default=False, help="Simulate operations without making changes")
@click.pass_context
def once(ctx: click.Context, execute: bool, dry_run: bool) -> None:
    """Run a single analysis pass (rule-based, no AI)."""
    from monitor.engine import MonitorEngine

    cfg = ConfigManager.load_from_file(ctx.obj["config_path"])
    if dry_run:
        cfg.dry_run = True

    engine = MonitorEngine(cfg)
    summary = engine.run_once(execute=execute)
    click.echo(summary)


# ---------------------------------------------------------------------------
# continuous (rule-based)
# ---------------------------------------------------------------------------
@main.command()
@click.option("--execute", is_flag=True, default=False)
@click.option("--dry-run", is_flag=True, default=False)
@click.pass_context
def continuous(ctx: click.Context, execute: bool, dry_run: bool) -> None:
    """Run continuous rule-based monitoring on a loop."""
    from monitor.engine import MonitorEngine

    cfg = ConfigManager.load_from_file(ctx.obj["config_path"])
    if dry_run:
        cfg.dry_run = True

    engine = MonitorEngine(cfg)
    engine.run_continuous(execute=execute)


# ---------------------------------------------------------------------------
# agent (Claude-powered)
# ---------------------------------------------------------------------------
@main.command()
@click.option(
    "--tables",
    "-t",
    multiple=True,
    help="Override tables from config (can be specified multiple times)",
)
@click.option("--dry-run", is_flag=True, default=False, help="Simulate operations without making changes")
@click.option("--model", default=None, help="Override Claude model (e.g. claude-opus-4-7)")
@click.pass_context
def agent(ctx: click.Context, tables: tuple[str, ...], dry_run: bool, model: str | None) -> None:
    """Run the Claude-powered AI maintenance agent."""
    from agent.maintenance_agent import IcebergMaintenanceAgent

    cfg = ConfigManager.load_from_file(ctx.obj["config_path"])
    if dry_run:
        cfg.dry_run = True
    if model:
        cfg.anthropic_model = model

    table_list = list(tables) if tables else cfg.tables
    if not table_list:
        click.echo("Error: no tables specified in config or via --tables", err=True)
        sys.exit(1)

    click.echo(f"Starting AI maintenance agent for {len(table_list)} table(s)...")
    if dry_run:
        click.echo("[DRY RUN mode — no changes will be made]")

    ai_agent = IcebergMaintenanceAgent(cfg)
    result = ai_agent.run(table_list)
    click.echo(result)


if __name__ == "__main__":
    main()
