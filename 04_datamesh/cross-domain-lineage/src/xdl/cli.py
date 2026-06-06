"""cross-domain-lineage: read DataZone subscriptions and Glue lineage events
across one or more domains and produce a domain-to-domain dependency graph.

Two subcommands:
  - lineage : collect signals + render Mermaid + interactive HTML
  - seed    : create demo assets, a listing, a subscription, and post a
              couple of OpenLineage events so 'lineage' has something to draw

The graph nodes are *domain units* (each unit is a bounded context). Edges
come from two sources:
  1. DataZone subscription requests / grants -- consumer project's owning
     unit -> producer listing's owning unit
  2. Glue (OpenLineage) lineage events posted to DataZone -- the upstream
     dataset's owning unit -> the downstream dataset's owning unit
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import boto3
from botocore.exceptions import ClientError


# -------------------------- data classes --------------------------


@dataclass
class Edge:
    src_unit: str          # producing domain-unit id
    dst_unit: str          # consuming domain-unit id
    kind: str              # "subscription" | "lineage"
    label: str             # short human-readable label
    detail: str = ""       # extra metadata (asset, listing, status)


@dataclass
class Graph:
    domain_id: str
    domain_name: str
    units: dict[str, dict] = field(default_factory=dict)     # unit_id -> {name, project_count}
    projects: dict[str, dict] = field(default_factory=dict)  # project_id -> {name, unit_id}
    edges: list[Edge] = field(default_factory=list)


# -------------------------- signal collection --------------------------


def _paginate(client, op: str, key: str, **kwargs) -> Iterable[dict]:
    paginator = client.get_paginator(op)
    for page in paginator.paginate(**kwargs):
        for it in page.get(key, []):
            yield it


def collect_graph(dz, domain_id: str) -> Graph:
    domain = dz.get_domain(identifier=domain_id)
    g = Graph(domain_id=domain_id, domain_name=domain["name"])
    root_unit = domain.get("rootDomainUnitId", "")

    # Domain units (root + first-level children -- enough for assessment-grade graph)
    g.units[root_unit] = {"name": "root", "project_count": 0, "is_root": True}
    for u in _paginate(
        dz, "list_domain_units_for_parent", "items",
        domainIdentifier=domain_id, parentDomainUnitIdentifier=root_unit,
    ):
        g.units[u["id"]] = {"name": u.get("name", u["id"]), "project_count": 0, "is_root": False}

    # Projects -> attach to their owning unit
    for p in _paginate(dz, "list_projects", "items", domainIdentifier=domain_id):
        unit_id = p.get("domainUnitId") or root_unit
        g.projects[p["id"]] = {"name": p.get("name", p["id"]), "unit_id": unit_id}
        if unit_id in g.units:
            g.units[unit_id]["project_count"] += 1

    # Subscription edges: each subscription request links a subscribing project
    # to a listing owned by another project. The subscribed listing already
    # includes ownerProjectId, so no extra index is needed.
    for sr in _list_subscription_requests(dz, domain_id, list(g.projects.keys())):
        sub_principals = sr.get("subscribedPrincipals", []) or [{}]
        sub_project_id = sub_principals[0].get("project", {}).get("id")
        status = sr.get("status", "?")
        for sl in sr.get("subscribedListings", []):
            listing_id = sl.get("id", "")
            owning_project_id = sl.get("ownerProjectId")
            if not (sub_project_id and owning_project_id):
                continue
            src = g.projects.get(owning_project_id, {}).get("unit_id")
            dst = g.projects.get(sub_project_id, {}).get("unit_id")
            if src and dst and src != dst:
                # The list response item only carries entityType/forms in the
                # nested asset listing; fetch the listing for its display name.
                listing_name = listing_id
                try:
                    detail = dz.get_listing(
                        domainIdentifier=domain_id, identifier=listing_id
                    )
                    listing_name = detail.get("name") or listing_name
                except ClientError:
                    pass
                g.edges.append(
                    Edge(
                        src_unit=src, dst_unit=dst, kind="subscription",
                        label=listing_name,
                        detail=f"status={status} listing={listing_id[:8]}",
                    )
                )

    # Lineage edges: walk OpenLineage events posted to the domain. Each event
    # has inputs[] and outputs[] datasets. Resolve each dataset to its owning
    # asset (and thus owning project + unit), and emit cross-unit edges.
    for ev in _list_lineage_events(dz, domain_id):
        try_add_lineage_edges(dz, domain_id, ev, g)

    return g


def _list_subscription_requests(dz, domain_id: str, project_ids: list[str]) -> list[dict]:
    """list_subscription_requests requires a filter. We query each project as
    BOTH the requester (owningProjectId) and the listing owner (approverProjectId)
    so we capture cross-project subscriptions in either direction."""
    out: list[dict] = []
    seen: set[str] = set()
    statuses = ("PENDING", "ACCEPTED", "REJECTED")
    for pid in project_ids:
        for filter_key in ("owningProjectId", "approverProjectId"):
            for status in statuses:
                next_token: str | None = None
                while True:
                    kwargs: dict[str, Any] = {
                        "domainIdentifier": domain_id,
                        "status": status,
                        filter_key: pid,
                    }
                    if next_token:
                        kwargs["nextToken"] = next_token
                    try:
                        resp = dz.list_subscription_requests(**kwargs)
                    except ClientError:
                        break
                    for it in resp.get("items", []):
                        if it.get("id") in seen:
                            continue
                        seen.add(it["id"])
                        out.append(it)
                    next_token = resp.get("nextToken")
                    if not next_token:
                        break
    return out


def _list_lineage_events(dz, domain_id: str) -> list[dict]:
    """ListLineageEvents already returns the parsed OpenLineage summary in
    items[].eventSummary.openLineageRunEventSummary -- no need to fetch the
    full payload via GetLineageEvent."""
    out: list[dict] = []
    next_token: str | None = None
    while True:
        kwargs: dict[str, Any] = {"domainIdentifier": domain_id}
        if next_token:
            kwargs["nextToken"] = next_token
        try:
            resp = dz.list_lineage_events(**kwargs)
        except ClientError:
            return out
        for it in resp.get("items", []):
            ol = (it.get("eventSummary") or {}).get("openLineageRunEventSummary")
            if ol:
                out.append(ol)
        next_token = resp.get("nextToken")
        if not next_token:
            return out


def try_add_lineage_edges(dz, domain_id: str, event: dict, g: Graph) -> None:
    inputs = event.get("inputs") or []
    outputs = event.get("outputs") or []
    if not inputs or not outputs:
        return
    in_units = [_resolve_dataset_unit(dz, domain_id, ds, g) for ds in inputs]
    out_units = [_resolve_dataset_unit(dz, domain_id, ds, g) for ds in outputs]
    in_units = [u for u in in_units if u]
    out_units = [u for u in out_units if u]
    for src_unit, src_label in in_units:
        for dst_unit, dst_label in out_units:
            if src_unit == dst_unit:
                continue
            g.edges.append(
                Edge(
                    src_unit=src_unit, dst_unit=dst_unit, kind="lineage",
                    label=f"{src_label} → {dst_label}",
                    detail=f"job={event.get('job', {}).get('name', '?')}",
                )
            )


def _resolve_dataset_unit(dz, domain_id: str, dataset: dict, g: Graph) -> tuple[str, str] | None:
    """Resolve an OpenLineage dataset to (unit_id, asset_name).

    OpenLineage names are typically "<db>.<table>" (or sometimes a path). The
    DataZone asset is registered with just the table name, so we search for
    the last dotted/slashed segment and prefer an exact name match.
    """
    full = dataset.get("name") or ""
    if not full:
        return None
    leaf = full.replace("/", ".").rsplit(".", 1)[-1]
    # Search with searchScope=ASSET requires owningProjectId, so fan out
    # across all known projects and pick the best match.
    exact = None
    fallback = None
    for pid in g.projects.keys():
        try:
            resp = dz.search(
                domainIdentifier=domain_id, searchScope="ASSET",
                owningProjectIdentifier=pid,
                searchText=leaf, maxResults=20,
            )
        except ClientError:
            continue
        for it in resp.get("items", []):
            a = it.get("assetItem") or {}
            if a.get("name") == leaf:
                exact = (g.projects[pid]["unit_id"], leaf)
                return exact
            if fallback is None:
                fallback = (g.projects[pid]["unit_id"], a.get("name", leaf))
    return exact or fallback


# -------------------------- rendering --------------------------


def render_mermaid(g: Graph) -> str:
    lines: list[str] = ["```mermaid", "graph LR"]
    # Nodes: one per domain unit, with project count
    for uid, u in g.units.items():
        if u.get("is_root") and u["project_count"] == 0 and not _has_edges_to_root(g, uid):
            continue
        node_id = _safe_id(uid)
        label = f"{u['name']}<br/><i>({u['project_count']} project{'s' if u['project_count']!=1 else ''})</i>"
        lines.append(f'  {node_id}["{label}"]')
    # Edges (deduplicate by triple)
    seen: set[tuple[str, str, str]] = set()
    for e in g.edges:
        key = (e.src_unit, e.dst_unit, e.kind)
        if key in seen:
            continue
        seen.add(key)
        arrow = "-->" if e.kind == "lineage" else "-.->"
        edge_label = f"{e.kind}: {e.label}"
        lines.append(
            f'  {_safe_id(e.src_unit)} {arrow}|"{edge_label}"| {_safe_id(e.dst_unit)}'
        )
    lines.append("```")
    return "\n".join(lines)


def _has_edges_to_root(g: Graph, uid: str) -> bool:
    return any(e.src_unit == uid or e.dst_unit == uid for e in g.edges)


def _safe_id(uid: str) -> str:
    return "u_" + uid.replace("-", "_")[:24]


def render_html(g: Graph) -> str:
    """Self-contained HTML page with mermaid.js (CDN) for interactive viewing."""
    mermaid_block = render_mermaid(g).replace("```mermaid\n", "").replace("\n```", "")
    edge_rows = "\n".join(
        f"<tr><td>{e.kind}</td><td>{g.units.get(e.src_unit, {}).get('name', e.src_unit)}</td>"
        f"<td>{g.units.get(e.dst_unit, {}).get('name', e.dst_unit)}</td>"
        f"<td>{e.label}</td><td>{e.detail}</td></tr>"
        for e in g.edges
    ) or "<tr><td colspan='5'><i>no cross-domain edges detected</i></td></tr>"
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Cross-Domain Lineage -- {g.domain_name}</title>
<script type="module">
import mermaid from "https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs";
mermaid.initialize({{ startOnLoad: true, securityLevel: "loose", theme: "default" }});
</script>
<style>
  body {{ font-family: -apple-system, sans-serif; margin: 24px; color: #222; }}
  h1 {{ font-size: 1.4em; }} h2 {{ font-size: 1.1em; margin-top: 28px; }}
  .meta {{ color: #666; font-size: 0.9em; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 0.9em; }}
  th, td {{ border: 1px solid #ddd; padding: 6px 10px; text-align: left; }}
  th {{ background: #f7f7f7; }}
  .mermaid {{ background: #fafafa; padding: 16px; border: 1px solid #eee; border-radius: 6px; }}
</style></head>
<body>
<h1>Cross-Domain Lineage -- {g.domain_name}</h1>
<div class="meta">Domain id: <code>{g.domain_id}</code> &middot;
  units: {len(g.units)} &middot; projects: {len(g.projects)} &middot;
  edges: {len(g.edges)} &middot; rendered {datetime.now(timezone.utc).isoformat(timespec='seconds')}</div>

<h2>Dependency graph</h2>
<div class="mermaid">
{mermaid_block}
</div>

<h2>Edges</h2>
<table>
  <thead><tr><th>Kind</th><th>Producer unit</th><th>Consumer unit</th><th>Label</th><th>Detail</th></tr></thead>
  <tbody>
{edge_rows}
  </tbody>
</table>
<p class="meta">Solid arrows = OpenLineage events. Dashed arrows = DataZone subscriptions.</p>
</body></html>
"""


# -------------------------- seed (demo data) --------------------------


def seed_demo(dz, glue, sts, domain_id: str, region: str) -> None:
    """Create a Glue database + two tables, register them as DataZone assets in
    the producer project, publish one as a listing, file a subscription request
    from the consumer project, and post a couple of OpenLineage events.

    Idempotent: re-running re-uses existing resources where possible.
    """
    print(f"== seeding demo data into domain {domain_id} ==")
    domain = dz.get_domain(identifier=domain_id)
    root_unit = domain.get("rootDomainUnitId", "")
    account = sts.get_caller_identity()["Account"]

    # Find producer + consumer projects by name (matches example.yaml)
    projects = list(_paginate(dz, "list_projects", "items", domainIdentifier=domain_id))
    by_name = {p["name"]: p for p in projects}
    producer = by_name.get("chocolate-producer")
    consumer = by_name.get("chocolate-consumer")
    if not producer or not consumer:
        sys.exit(
            "error: expected projects 'chocolate-producer' and 'chocolate-consumer' "
            "(deploy datazone-as-code/manifests/example.yaml first)"
        )
    print(f"  producer={producer['id']}  consumer={consumer['id']}")

    # 1. Glue database + tables
    db_name = "xdl_demo"
    try:
        glue.create_database(DatabaseInput={"Name": db_name})
        print(f"  glue: created database {db_name}")
    except glue.exceptions.AlreadyExistsException:
        print(f"  glue: reuse database {db_name}")

    for tname in ("cocoa_shipments", "monthly_revenue", "revenue_forecast"):
        try:
            glue.create_table(
                DatabaseName=db_name,
                TableInput={
                    "Name": tname,
                    "TableType": "EXTERNAL_TABLE",
                    "StorageDescriptor": {
                        "Columns": [
                            {"Name": "id", "Type": "string"},
                            {"Name": "amount", "Type": "double"},
                            {"Name": "ts", "Type": "timestamp"},
                        ],
                        "Location": f"s3://example-bucket/xdl/{tname}/",
                        "InputFormat": "org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat",
                        "OutputFormat": "org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat",
                    },
                },
            )
            print(f"  glue: created table {db_name}.{tname}")
        except glue.exceptions.AlreadyExistsException:
            print(f"  glue: reuse table {db_name}.{tname}")

    # 2. DataZone GlueTable assets. Producer owns the upstream tables;
    # consumer owns its own derived asset so we get a real cross-domain edge.
    asset_ids: dict[str, str] = {}
    asset_owner: dict[str, str] = {
        "cocoa_shipments": producer["id"],
        "monthly_revenue": producer["id"],
        "revenue_forecast": consumer["id"],
    }
    for tname, owner in asset_owner.items():
        existing = _find_asset(dz, domain_id, owner, tname)
        if existing:
            print(f"  datazone: reuse asset {tname} = {existing}")
            asset_ids[tname] = existing
            continue
        try:
            arn = f"arn:aws:glue:{region}:{account}:table/{db_name}/{tname}"
            resp = dz.create_asset(
                domainIdentifier=domain_id,
                owningProjectIdentifier=owner,
                name=tname,
                description=f"Demo asset for {tname} (cross-domain-lineage seed)",
                typeIdentifier="amazon.datazone.GlueTableAssetType",
                externalIdentifier=arn,
                formsInput=[{
                    "formName": "GlueTableForm",
                    "typeIdentifier": "amazon.datazone.GlueTableFormType",
                    "content": json.dumps({
                        "tableArn": arn,
                        "tableName": tname,
                        "sourceClassification": "Glue",
                        "region": region,
                        "catalogId": account,
                        "columns": [
                            {"columnName": "id", "dataType": "string"},
                            {"columnName": "amount", "dataType": "double"},
                            {"columnName": "ts", "dataType": "timestamp"},
                        ],
                    }),
                }],
            )
            asset_ids[tname] = resp["id"]
            print(f"  datazone: created asset {tname} = {resp['id']}")
        except dz.exceptions.ConflictException:
            print(f"  datazone: reuse asset {tname} (conflict on re-run)")
        except ClientError as e:
            print(f"  datazone: WARN could not create asset {tname}: {e}")

    # 3. Publish one asset as a listing
    listing_id = None
    if "cocoa_shipments" in asset_ids:
        try:
            resp = dz.create_listing_change_set(
                domainIdentifier=domain_id,
                action="PUBLISH",
                entityType="ASSET",
                entityIdentifier=asset_ids["cocoa_shipments"],
            )
            listing_id = resp.get("listingId")
            print(f"  datazone: published listing {listing_id}")
        except ClientError as e:
            print(f"  datazone: WARN listing publish failed: {e}")

    # 4. Subscription request from consumer project
    if listing_id:
        try:
            existing_sub = any(
                listing_id in [sl.get("id") for sl in sr.get("subscribedListings", [])]
                for sr in _list_subscription_requests(dz, domain_id)
            )
            if existing_sub:
                print(f"  datazone: reuse subscription request for listing {listing_id}")
            else:
                sr = dz.create_subscription_request(
                    domainIdentifier=domain_id,
                    subscribedListings=[{"identifier": listing_id}],
                    subscribedPrincipals=[{
                        "project": {"identifier": consumer["id"]},
                    }],
                    requestReason="cross-domain-lineage demo",
                )
                print(f"  datazone: created subscription request {sr['id']}")
        except ClientError as e:
            print(f"  datazone: WARN subscription request failed: {e}")

    # 5. OpenLineage events: one inside the producer (raw -> curated) and one
    # crossing producer -> consumer (curated -> revenue model)
    import uuid
    ns = f"glue:{region}:{account}"
    def _ol_event(job_name: str, ins: list[str], outs: list[str]) -> dict:
        return {
            "eventType": "COMPLETE",
            "eventTime": datetime.now(timezone.utc).isoformat(),
            "producer": "https://github.com/cross-domain-lineage/seed",
            "schemaURL": "https://openlineage.io/spec/2-0-2/OpenLineage.json#/$defs/RunEvent",
            "run": {"runId": str(uuid.uuid4())},
            "job": {"namespace": "demo", "name": job_name},
            "inputs": [{"namespace": ns, "name": f"{db_name}.{n}"} for n in ins],
            "outputs": [{"namespace": ns, "name": f"{db_name}.{n}"} for n in outs],
        }
    events = [
        # intra-domain (production -> production)
        _ol_event("build_monthly_revenue", ["cocoa_shipments"], ["monthly_revenue"]),
        # cross-domain (production -> analytics): revenue model consumes producer's monthly_revenue
        _ol_event("forecast_revenue", ["monthly_revenue"], ["revenue_forecast"]),
    ]
    for ev in events:
        try:
            dz.post_lineage_event(domainIdentifier=domain_id, event=json.dumps(ev).encode("utf-8"))
            print(f"  datazone: posted lineage event for {ev['job']['name']}")
        except ClientError as e:
            print(f"  datazone: WARN lineage post failed: {e}")

    print("== seed complete ==")


def _find_asset(dz, domain_id: str, project_id: str, name: str) -> str | None:
    try:
        resp = dz.search(
            domainIdentifier=domain_id, searchScope="ASSET",
            owningProjectIdentifier=project_id,
            searchText=name, maxResults=20,
        )
    except ClientError:
        return None
    for it in resp.get("items", []):
        a = it.get("assetItem") or {}
        if a.get("name") == name:
            return a.get("identifier") or a.get("id")
    return None


# -------------------------- CLI --------------------------


def resolve_domain(dz, domain_arg: str | None, domain_name: str | None) -> str:
    if domain_arg:
        return domain_arg
    candidates = []
    for page in dz.get_paginator("list_domains").paginate():
        for d in page.get("items", []):
            if d.get("status") == "AVAILABLE":
                candidates.append(d)
    if domain_name:
        for d in candidates:
            if d["name"] == domain_name:
                return d["id"]
        sys.exit(f"error: no domain named '{domain_name}' in this region")
    if len(candidates) == 1:
        return candidates[0]["id"]
    if not candidates:
        sys.exit("error: no AVAILABLE domains in this region")
    sys.exit(
        "error: multiple domains found, pass --domain-id or --domain-name. "
        f"Found: {[(d['id'], d['name']) for d in candidates]}"
    )


def cmd_lineage(args) -> None:
    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    dz = session.client("datazone")
    domain_id = resolve_domain(dz, args.domain_id, args.domain_name)
    print(f"== cross-domain-lineage: domain {domain_id} (region={args.region}) ==")
    g = collect_graph(dz, domain_id)
    print(
        f"  collected: units={len(g.units)} projects={len(g.projects)} edges={len(g.edges)}"
    )

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    md_path = out / f"{g.domain_name}-lineage.md"
    html_path = out / f"{g.domain_name}-lineage.html"
    json_path = out / f"{g.domain_name}-lineage.json"

    md = "# Cross-Domain Lineage -- " + g.domain_name + "\n\n" + render_mermaid(g) + "\n"
    md_path.write_text(md)
    html_path.write_text(render_html(g))
    json_path.write_text(json.dumps({
        "domain_id": g.domain_id, "domain_name": g.domain_name,
        "units": g.units, "projects": g.projects,
        "edges": [vars(e) for e in g.edges],
    }, indent=2))
    print(f"== wrote {md_path} / {html_path} / {json_path} ==")
    print()
    print(md)


def cmd_seed(args) -> None:
    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    dz = session.client("datazone")
    glue = session.client("glue")
    sts = session.client("sts")
    domain_id = resolve_domain(dz, args.domain_id, args.domain_name)
    seed_demo(dz, glue, sts, domain_id, args.region)


def main() -> None:
    p = argparse.ArgumentParser(prog="xdl", description="cross-domain-lineage")
    sub = p.add_subparsers(dest="cmd", required=True)

    common_args = lambda sp: (
        sp.add_argument("--profile", default=os.environ.get("AWS_PROFILE", "default")),
        sp.add_argument("--region", default=os.environ.get("AWS_REGION", "us-east-1")),
        sp.add_argument("--domain-id", default=None),
        sp.add_argument("--domain-name", default=None),
    )

    sp_l = sub.add_parser("lineage", help="build dependency graph")
    common_args(sp_l)
    sp_l.add_argument("--out-dir", default="out")
    sp_l.set_defaults(func=cmd_lineage)

    sp_s = sub.add_parser("seed", help="seed demo data (Glue + DataZone)")
    common_args(sp_s)
    sp_s.set_defaults(func=cmd_seed)

    args = p.parse_args()
    args.func(args)
