"""Click-based CLI entrypoint for `dla`."""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import boto3
import click
import yaml
from rich.console import Console
from rich.table import Table

from .dimensions import DIMENSION_TITLES
from .reporting import render_json, render_markdown
from .runner import run_scan


_LEVEL_BADGE = {1: "L1 🔴", 2: "L2 ⚠", 3: "L3 ✓", 4: "L4 ✓✓", 5: "L5 ★", 0: "?"}


@click.group()
@click.version_option(package_name="datalake-maturity-assessment")
@click.option("-v", "--verbose", is_flag=True, help="Enable debug logging.")
def main(verbose: bool):
    """Data Lake Maturity Assessment — score an AWS data lake on 8 dimensions."""
    level = logging.DEBUG if verbose else logging.WARNING
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s %(message)s")


@main.command()
@click.option("--region", default=None, help="AWS region to scan (default: from profile).")
@click.option("--profile", default=None, help="AWS profile to use.")
@click.option("--preset", default=None, help="Industry weights preset (pe_portco, financial_services, healthcare).")
@click.option("--target-level", default=4, type=int, help="Target maturity level — items at/above this are not in remediation backlog.")
@click.option("--output", "-o", default="report.md", type=click.Path(), help="Markdown report output path.")
@click.option("--json-output", default=None, type=click.Path(), help="JSON sidecar output path.")
@click.option("--include-snapshot", is_flag=True, help="Include the raw snapshot in the JSON output (large).")
@click.option("--quick-wins", is_flag=True, help="Filter remediation to quick wins (effort=S, impact≥3).")
@click.option("--min-score", default=None, type=float, help="Exit non-zero if weighted score < this value (CI mode).")
def scan(region, profile, preset, target_level, output, json_output, include_snapshot, quick_wins, min_score):
    """Run a one-shot scan against the active AWS account."""
    console = Console()

    # Resolve region from session if not given
    if not region:
        sess = boto3.Session(profile_name=profile)
        region = sess.region_name or "us-east-1"

    # Resolve account id
    sts = boto3.Session(profile_name=profile, region_name=region).client("sts")
    try:
        account_id = sts.get_caller_identity()["Account"]
    except Exception as e:
        click.echo(f"ERROR: could not call STS GetCallerIdentity ({e})", err=True)
        sys.exit(2)

    console.rule(f"[bold]Data Lake Maturity Assessment[/bold] — account [cyan]{account_id}[/cyan] / region [cyan]{region}[/cyan]")
    if preset:
        console.print(f"  weights preset: [yellow]{preset}[/yellow]")
    console.print()

    result = run_scan(region=region, profile=profile, preset=preset, target_level=target_level, console=console)
    summary = result["summary"]
    backlog = result["backlog"]
    if quick_wins:
        backlog = [it for it in backlog if it["effort"] == "S" and it["impact"] >= 3]

    # Console scorecard
    console.print()
    table = Table(title=f"Scorecard — weighted {summary['weighted_score']} / 5.0 ({summary['weighted_label']})", show_header=True)
    table.add_column("#", justify="right", width=3)
    table.add_column("Dimension")
    table.add_column("Level", width=8)
    table.add_column("Top gap", overflow="fold")
    for i, (key, title) in enumerate(DIMENSION_TITLES.items(), 1):
        s = summary["scores_by_dimension"].get(key, {"level": 0, "top_gap": "—"})
        table.add_row(str(i), title, _LEVEL_BADGE.get(s["level"], "?"), s["top_gap"])
    console.print(table)
    console.print()
    console.print(f"  Weakest link: [bold]L{summary['weakest_level']}[/bold] ({', '.join(summary['weakest_dimensions']) or '—'})")
    console.print(f"  Remediation items: [bold]{len(backlog)}[/bold]")
    console.print()

    # Write reports
    md = render_markdown(account_id, region, summary, backlog)
    Path(output).write_text(md)
    console.print(f"  ✔ Markdown report: [green]{output}[/green]")
    if json_output:
        js = render_json(account_id, region, summary, backlog, snapshot=result["snapshot"] if include_snapshot else None)
        Path(json_output).write_text(js)
        console.print(f"  ✔ JSON report:     [green]{json_output}[/green]")

    # Always emit a remediation.yaml next to the markdown
    rem_path = Path(output).with_name("remediation.yaml")
    rem_path.write_text(yaml.safe_dump({"remediation": backlog}, sort_keys=False))
    console.print(f"  ✔ Remediation YAML: [green]{rem_path}[/green]")

    if min_score is not None and summary["weighted_score"] < min_score:
        console.print(f"[red]FAIL[/red]: weighted score {summary['weighted_score']} < min {min_score}")
        sys.exit(3)


@main.command()
def list_presets():
    """Print available industry weight presets."""
    here = Path(__file__).resolve().parents[2]
    preset_dir = here / "config" / "industry_presets"
    for p in sorted(preset_dir.glob("*.yaml")):
        click.echo(p.stem)


if __name__ == "__main__":  # pragma: no cover
    main()
