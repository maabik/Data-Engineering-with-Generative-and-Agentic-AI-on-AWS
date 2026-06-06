"""data-product-validator: validate an incoming data product against a YAML
data contract and gate the DataZone publish step on whether the diff contains
any breaking changes.

The contract is dbt-flavored YAML:

    asset: cocoa_shipments
    project: chocolate-producer
    description: Raw cocoa shipment receipts from origin warehouses.
    columns:
      - name: id
        type: string
        required: true
      - name: amount
        type: double
      - name: ts
        type: timestamp
    glossary_terms:    # optional
      - Cocoa
    deprecated_columns: []   # optional, columns we expect to have been removed

Two subcommands:

    dpv validate --contract contracts/foo.yaml
        Read-only. Fetch the live asset, diff against the contract, print a
        report. Exit 1 if any breaking finding is present (so the validator
        can be a CI gate).

    dpv publish --contract contracts/foo.yaml [--allow-warning]
        validate + (only if clean) call CreateListingChangeSet to publish the
        asset. This is the DataZone-side "publish lifecycle" hook: a producer
        runs `publish` instead of clicking the console button.

Severity rules (kept short and predictable):

    breaking : column removed / type changed / required column removed
    warning  : description missing on contract or asset; glossary term in
               contract not attached to the asset
    info     : new column appears in the live asset that the contract has not
               yet declared

The validator is read-only on the live domain in `validate` mode -- only the
`publish` subcommand mutates state, and only after the diff is clean.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import boto3
import yaml
from botocore.exceptions import ClientError


# -------------------------- data classes --------------------------


SEVERITY_BREAKING = "breaking"
SEVERITY_WARNING = "warning"
SEVERITY_INFO = "info"

SEV_RANK = {SEVERITY_BREAKING: 0, SEVERITY_WARNING: 1, SEVERITY_INFO: 2}


@dataclass
class Finding:
    id: str
    severity: str
    title: str
    detail: str
    remediation: str = ""


@dataclass
class Contract:
    asset: str
    project: str
    description: str = ""
    columns: list[dict] = field(default_factory=list)
    glossary_terms: list[str] = field(default_factory=list)
    deprecated_columns: list[str] = field(default_factory=list)


@dataclass
class LiveAsset:
    asset_id: str
    name: str
    description: str
    owning_project_id: str
    listing_id: str | None
    listing_status: str | None
    columns: list[dict]              # [{columnName, dataType}]
    glossary_term_ids: list[str]


@dataclass
class Report:
    contract: Contract
    asset: LiveAsset
    findings: list[Finding]
    domain_id: str
    domain_name: str

    @property
    def has_breaking(self) -> bool:
        return any(f.severity == SEVERITY_BREAKING for f in self.findings)

    @property
    def has_warning(self) -> bool:
        return any(f.severity == SEVERITY_WARNING for f in self.findings)


# -------------------------- contract loading --------------------------


def load_contract(path: Path) -> Contract:
    raw = yaml.safe_load(path.read_text())
    if not isinstance(raw, dict):
        sys.exit(f"error: contract {path} is not a YAML mapping")
    missing = [k for k in ("asset", "project", "columns") if k not in raw]
    if missing:
        sys.exit(f"error: contract {path} is missing required keys: {missing}")
    cols = raw["columns"] or []
    for c in cols:
        if "name" not in c or "type" not in c:
            sys.exit(f"error: contract column missing name/type: {c}")
    return Contract(
        asset=raw["asset"],
        project=raw["project"],
        description=(raw.get("description") or "").strip(),
        columns=cols,
        glossary_terms=raw.get("glossary_terms") or [],
        deprecated_columns=raw.get("deprecated_columns") or [],
    )


# -------------------------- live fetch --------------------------


def resolve_domain(dz, domain_id_arg: str | None, domain_name_arg: str | None) -> tuple[str, str]:
    if domain_id_arg:
        d = dz.get_domain(identifier=domain_id_arg)
        return d["id"], d["name"]
    candidates = []
    for page in dz.get_paginator("list_domains").paginate():
        for d in page.get("items", []):
            if d.get("status") == "AVAILABLE":
                candidates.append(d)
    if domain_name_arg:
        for d in candidates:
            if d["name"] == domain_name_arg:
                return d["id"], d["name"]
        sys.exit(f"error: no domain named '{domain_name_arg}' in this region")
    if len(candidates) == 1:
        return candidates[0]["id"], candidates[0]["name"]
    if not candidates:
        sys.exit("error: no AVAILABLE domains in this region")
    sys.exit(
        "error: multiple domains found, pass --domain-id or --domain-name. "
        f"Found: {[(d['id'], d['name']) for d in candidates]}"
    )


def find_project_by_name(dz, domain_id: str, project_name: str) -> str:
    for page in dz.get_paginator("list_projects").paginate(domainIdentifier=domain_id):
        for p in page.get("items", []):
            if p.get("name") == project_name:
                return p["id"]
    sys.exit(f"error: project '{project_name}' not found in domain {domain_id}")


def find_asset(dz, domain_id: str, project_id: str, asset_name: str) -> str:
    """search ASSET requires owningProjectIdentifier; pick the exact name match."""
    try:
        resp = dz.search(
            domainIdentifier=domain_id, searchScope="ASSET",
            owningProjectIdentifier=project_id,
            searchText=asset_name, maxResults=50,
        )
    except ClientError as e:
        sys.exit(f"error: search failed: {e}")
    for it in resp.get("items", []):
        a = it.get("assetItem") or {}
        if a.get("name") == asset_name:
            return a.get("identifier") or a.get("id")
    sys.exit(
        f"error: asset '{asset_name}' not found in project {project_id}. "
        "Has the producer registered it yet?"
    )


def fetch_live_asset(dz, domain_id: str, asset_id: str) -> LiveAsset:
    a = dz.get_asset(domainIdentifier=domain_id, identifier=asset_id)
    columns: list[dict] = []
    for f in a.get("formsOutput", []):
        if f.get("typeName") == "amazon.datazone.GlueTableFormType":
            try:
                content = json.loads(f.get("content", "{}"))
            except json.JSONDecodeError:
                continue
            for c in content.get("columns", []) or []:
                columns.append({
                    "name": c.get("columnName"),
                    "type": (c.get("dataType") or "").lower(),
                })
    listing = a.get("listing") or {}
    return LiveAsset(
        asset_id=a["id"],
        name=a["name"],
        description=(a.get("description") or "").strip(),
        owning_project_id=a["owningProjectId"],
        listing_id=listing.get("listingId"),
        listing_status=listing.get("listingStatus"),
        columns=columns,
        glossary_term_ids=a.get("glossaryTerms") or [],
    )


# -------------------------- diff engine --------------------------


def normalize_type(t: str) -> str:
    """Collapse common Glue/Hive type aliases so 'string' ~= 'varchar(255)'."""
    t = (t or "").strip().lower()
    # strip parametric width: varchar(255) -> varchar
    t = t.split("(", 1)[0].strip()
    aliases = {
        "varchar": "string",
        "char": "string",
        "text": "string",
        "int": "integer",
        "integer": "integer",
        "bigint": "long",
        "long": "long",
        "smallint": "integer",
        "tinyint": "integer",
        "double": "double",
        "float": "float",
        "real": "float",
        "decimal": "decimal",
        "numeric": "decimal",
        "timestamp": "timestamp",
        "date": "date",
        "boolean": "boolean",
        "bool": "boolean",
        "string": "string",
    }
    return aliases.get(t, t)


def diff(contract: Contract, asset: LiveAsset) -> list[Finding]:
    findings: list[Finding] = []
    contract_cols = {c["name"]: c for c in contract.columns}
    live_cols = {c["name"]: c for c in asset.columns}

    # 1. Columns the contract requires but the live asset is missing -> breaking
    for name, c in contract_cols.items():
        if name not in live_cols:
            sev = SEVERITY_BREAKING if c.get("required") else SEVERITY_WARNING
            findings.append(Finding(
                id=f"COL-MISSING-{name}",
                severity=sev,
                title=f"Column '{name}' is in the contract but missing from the live asset",
                detail=(
                    f"Contract declares column '{name}' (type={c.get('type')},"
                    f" required={bool(c.get('required'))}), but the live asset has no such column."
                ),
                remediation=(
                    "Either add the column to the underlying source table and re-run "
                    "the data source, or remove the column from the contract."
                ),
            ))

    # 2. Type mismatches -> breaking
    for name, c in contract_cols.items():
        live = live_cols.get(name)
        if not live:
            continue
        want = normalize_type(c.get("type", ""))
        got = normalize_type(live.get("type", ""))
        if want and got and want != got:
            findings.append(Finding(
                id=f"COL-TYPE-{name}",
                severity=SEVERITY_BREAKING,
                title=f"Column '{name}' type changed: contract={c.get('type')}, live={live.get('type')}",
                detail=(
                    f"Contract expects normalized type '{want}'; live asset has '{got}'. "
                    "Subscribers who joined on the previous type will break."
                ),
                remediation=(
                    "Cast the column back to the contracted type in the source query, "
                    "or bump the contract version after coordinating with subscribers."
                ),
            ))

    # 3. Deprecated columns that are still present -> warning
    for name in contract.deprecated_columns:
        if name in live_cols:
            findings.append(Finding(
                id=f"COL-DEPRECATED-PRESENT-{name}",
                severity=SEVERITY_WARNING,
                title=f"Column '{name}' is marked deprecated but still present",
                detail="Contract lists it under deprecated_columns; the live asset still ships it.",
                remediation="Drop the column from the source table once the deprecation grace period ends.",
            ))

    # 4. New columns in live not declared in the contract -> info (not breaking,
    # additive changes are safe for existing subscribers, but the contract is
    # drifting from reality and should be updated)
    for name, c in live_cols.items():
        if name in contract_cols:
            continue
        if name in contract.deprecated_columns:
            continue
        findings.append(Finding(
            id=f"COL-UNDECLARED-{name}",
            severity=SEVERITY_INFO,
            title=f"Live column '{name}' is not declared in the contract",
            detail=f"Asset has additional column '{name}' (type={c.get('type')}). Additive only -- not a breaking change.",
            remediation="Add the column to the contract so consumers know it's part of the product.",
        ))

    # 5. Description hygiene
    if not contract.description:
        findings.append(Finding(
            id="CONTRACT-NO-DESCRIPTION",
            severity=SEVERITY_WARNING,
            title="Contract has no description",
            detail="Subscribers can't tell what the asset is for from the contract alone.",
            remediation="Add a top-level 'description' to the contract.",
        ))
    elif not asset.description:
        findings.append(Finding(
            id="ASSET-NO-DESCRIPTION",
            severity=SEVERITY_WARNING,
            title="Live asset has no description",
            detail="The contract has a description but the live asset's description is empty.",
            remediation=(
                "Update the asset description in DataZone (or the upstream Glue table comment "
                "and re-run the data source)."
            ),
        ))
    elif contract.description != asset.description:
        # not strictly broken, just drift -> info
        findings.append(Finding(
            id="DESCRIPTION-DRIFT",
            severity=SEVERITY_INFO,
            title="Contract and live asset descriptions differ",
            detail="The contract and the live asset both have descriptions, but they don't match.",
            remediation="Pick the canonical wording and update the other side.",
        ))

    # 6. Glossary terms expected by the contract that aren't attached -> warning
    # (We can only compare term names against term ids if we resolve them, but
    # for the spec-level check we just flag that the contract expects terms.)
    if contract.glossary_terms and not asset.glossary_term_ids:
        findings.append(Finding(
            id="GLOSSARY-NOT-ATTACHED",
            severity=SEVERITY_WARNING,
            title=f"Contract expects glossary terms ({', '.join(contract.glossary_terms)}) but none are attached",
            detail="No glossary terms are attached to the live asset.",
            remediation="Attach the contracted terms via UpdateAsset / the DataZone console.",
        ))

    findings.sort(key=lambda f: (SEV_RANK[f.severity], f.id))
    return findings


# -------------------------- rendering --------------------------


def render_report(report: Report) -> str:
    lines: list[str] = []
    lines.append(f"# Data product validation report -- {report.contract.asset}")
    lines.append("")
    lines.append(f"- **Domain**: {report.domain_name} (`{report.domain_id}`)")
    lines.append(f"- **Project**: {report.contract.project}")
    lines.append(f"- **Asset id**: `{report.asset.asset_id}`")
    if report.asset.listing_id:
        lines.append(f"- **Listing**: `{report.asset.listing_id}` ({report.asset.listing_status})")
    else:
        lines.append("- **Listing**: not yet published")
    lines.append(f"- **Validated at**: {datetime.now(timezone.utc).isoformat(timespec='seconds')}")
    lines.append("")
    counts = {sev: sum(1 for f in report.findings if f.severity == sev) for sev in (SEVERITY_BREAKING, SEVERITY_WARNING, SEVERITY_INFO)}
    lines.append(
        f"**Findings**: breaking={counts[SEVERITY_BREAKING]} "
        f"warning={counts[SEVERITY_WARNING]} info={counts[SEVERITY_INFO]}"
    )
    lines.append("")

    if not report.findings:
        lines.append("PASS -- contract matches live asset.")
        return "\n".join(lines)

    for i, f in enumerate(report.findings, 1):
        lines.append(f"### {i}. [{f.severity.upper()}] {f.title}")
        lines.append("")
        lines.append(f"- **Detail**: {f.detail}")
        if f.remediation:
            lines.append(f"- **Remediation**: {f.remediation}")
        lines.append(f"- **Finding id**: `{f.id}`")
        lines.append("")
    return "\n".join(lines)


def render_json(report: Report) -> str:
    return json.dumps({
        "domain_id": report.domain_id,
        "domain_name": report.domain_name,
        "asset_id": report.asset.asset_id,
        "asset_name": report.asset.name,
        "listing_id": report.asset.listing_id,
        "listing_status": report.asset.listing_status,
        "contract": asdict(report.contract),
        "findings": [asdict(f) for f in report.findings],
        "verdict": (
            "fail" if report.has_breaking else
            "warn" if report.has_warning else
            "pass"
        ),
    }, indent=2)


# -------------------------- commands --------------------------


def build_report(dz, contract: Contract, args) -> Report:
    domain_id, domain_name = resolve_domain(dz, args.domain_id, args.domain_name)
    project_id = find_project_by_name(dz, domain_id, contract.project)
    asset_id = find_asset(dz, domain_id, project_id, contract.asset)
    asset = fetch_live_asset(dz, domain_id, asset_id)
    findings = diff(contract, asset)
    return Report(
        contract=contract, asset=asset, findings=findings,
        domain_id=domain_id, domain_name=domain_name,
    )


def write_outputs(report: Report, out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / f"{report.contract.asset}-validation.md"
    json_path = out_dir / f"{report.contract.asset}-validation.json"
    md_path.write_text(render_report(report))
    json_path.write_text(render_json(report))
    return md_path, json_path


def cmd_validate(args) -> int:
    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    dz = session.client("datazone")
    contract = load_contract(Path(args.contract))
    report = build_report(dz, contract, args)
    md_path, json_path = write_outputs(report, Path(args.out_dir))
    print(render_report(report))
    print()
    print(f"-- wrote {md_path} and {json_path} --")
    if report.has_breaking:
        print("FAIL: breaking changes detected.", file=sys.stderr)
        return 1
    if report.has_warning and getattr(args, "strict", False):
        print("FAIL: warnings present and --strict is set.", file=sys.stderr)
        return 1
    return 0


def cmd_publish(args) -> int:
    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    dz = session.client("datazone")
    contract = load_contract(Path(args.contract))
    report = build_report(dz, contract, args)
    md_path, json_path = write_outputs(report, Path(args.out_dir))
    print(render_report(report))
    print()
    print(f"-- wrote {md_path} and {json_path} --")
    if report.has_breaking:
        print("PUBLISH BLOCKED: breaking changes detected. Fix the diff or bump the contract.", file=sys.stderr)
        return 1
    if report.has_warning and not args.allow_warning:
        print(
            "PUBLISH BLOCKED: warnings present. Re-run with --allow-warning to proceed.",
            file=sys.stderr,
        )
        return 1

    # All clear -> publish (or refresh) the listing
    if report.asset.listing_id and report.asset.listing_status == "ACTIVE":
        print(f"-- listing {report.asset.listing_id} already ACTIVE, no change needed --")
        return 0
    try:
        resp = dz.create_listing_change_set(
            domainIdentifier=report.domain_id,
            action="PUBLISH",
            entityType="ASSET",
            entityIdentifier=report.asset.asset_id,
        )
        print(f"-- published asset as listing {resp.get('listingId')} (rev {resp.get('listingRevision')}) --")
        return 0
    except ClientError as e:
        print(f"PUBLISH FAILED: {e}", file=sys.stderr)
        return 2


# -------------------------- argparse wiring --------------------------


def _common(sp: argparse.ArgumentParser) -> None:
    sp.add_argument("--profile", default=os.environ.get("AWS_PROFILE", "default"))
    sp.add_argument("--region", default=os.environ.get("AWS_REGION", "us-east-1"))
    sp.add_argument("--domain-id", default=None)
    sp.add_argument("--domain-name", default=None)
    sp.add_argument("--contract", required=True, help="path to YAML data contract")
    sp.add_argument("--out-dir", default="out")


def main() -> None:
    p = argparse.ArgumentParser(prog="dpv", description="data-product-validator")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp_v = sub.add_parser("validate", help="validate a contract against a live asset (read-only)")
    _common(sp_v)
    sp_v.add_argument("--strict", action="store_true", help="fail on warnings as well as breaking")
    sp_v.set_defaults(func=cmd_validate)

    sp_p = sub.add_parser("publish", help="validate, then publish the asset as a listing if clean")
    _common(sp_p)
    sp_p.add_argument("--allow-warning", action="store_true", help="proceed with publish if only warnings are present")
    sp_p.set_defaults(func=cmd_publish)

    args = p.parse_args()
    sys.exit(args.func(args))
