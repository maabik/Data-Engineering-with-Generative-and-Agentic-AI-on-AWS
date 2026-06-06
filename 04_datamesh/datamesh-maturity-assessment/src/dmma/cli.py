"""datamesh-maturity-assessment: programmatic scoring of an AWS DataZone
deployment against Zhamak Dehghani's four data-mesh principles:

  1. Domain ownership          (decentralized teams own their data)
  2. Data as a product         (assets are discoverable, addressable, trustworthy)
  3. Self-serve data platform  (consumers can subscribe + provision without tickets)
  4. Federated computational governance (policies + glossary applied uniformly)

The tool reads a live DataZone domain via boto3, collects signals, and emits
a maturity score per principle plus a prioritized remediation list.
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
from botocore.exceptions import ClientError


# -------------------------- data classes --------------------------


@dataclass
class Finding:
    """A single observation contributing to a principle's score."""
    id: str
    principle: int
    severity: str          # "info" | "low" | "medium" | "high"
    title: str
    detail: str
    remediation: str
    points_lost: float     # 0.0 = no impact, larger = bigger gap

    def priority(self) -> int:
        return {"high": 0, "medium": 1, "low": 2, "info": 3}[self.severity]


@dataclass
class PrincipleScore:
    number: int
    name: str
    score: float           # 0.0 - 100.0
    findings: list[Finding] = field(default_factory=list)


@dataclass
class Signals:
    """Raw API observations -- everything the scoring rules consume."""
    domain_id: str
    domain_name: str
    domain_status: str
    domain_version: str
    root_unit_id: str
    units: list[dict] = field(default_factory=list)
    projects: list[dict] = field(default_factory=list)
    project_members: dict[str, list[dict]] = field(default_factory=dict)  # project_id -> members
    glossaries: list[dict] = field(default_factory=list)                  # via search
    glossary_terms: list[dict] = field(default_factory=list)              # via search
    assets: list[dict] = field(default_factory=list)                      # via search
    listings: list[dict] = field(default_factory=list)                    # published assets
    data_sources: dict[str, list[dict]] = field(default_factory=dict)     # project_id -> data sources
    environments: dict[str, list[dict]] = field(default_factory=dict)     # project_id -> envs
    policy_grants_root: list[dict] = field(default_factory=list)          # grants on root unit
    root_unit_owners: list[dict] = field(default_factory=list)


# -------------------------- signal collection --------------------------


def collect_signals(dz, domain_id: str) -> Signals:
    domain = dz.get_domain(identifier=domain_id)
    s = Signals(
        domain_id=domain_id,
        domain_name=domain["name"],
        domain_status=domain.get("status", "UNKNOWN"),
        domain_version=domain.get("domainVersion", "V1"),
        root_unit_id=domain.get("rootDomainUnitId", ""),
    )

    # Domain units (recursive walk -- only one level deep is fine for assessment)
    paginator = dz.get_paginator("list_domain_units_for_parent")
    for page in paginator.paginate(
        domainIdentifier=domain_id, parentDomainUnitIdentifier=s.root_unit_id
    ):
        s.units.extend(page.get("items", []))

    # Root unit owners
    paginator = dz.get_paginator("list_entity_owners")
    for page in paginator.paginate(
        domainIdentifier=domain_id,
        entityType="DOMAIN_UNIT",
        entityIdentifier=s.root_unit_id,
    ):
        s.root_unit_owners.extend(page.get("owners", []))

    # Policy grants on root unit (covers the inheritable grants)
    for policy_type in ("CREATE_PROJECT", "CREATE_DOMAIN_UNIT", "CREATE_GLOSSARY"):
        try:
            paginator = dz.get_paginator("list_policy_grants")
            for page in paginator.paginate(
                domainIdentifier=domain_id,
                entityType="DOMAIN_UNIT",
                entityIdentifier=s.root_unit_id,
                policyType=policy_type,
            ):
                for g in page.get("grantList", []):
                    g["_policyType"] = policy_type
                    s.policy_grants_root.append(g)
        except ClientError as e:
            # Some policy types may not exist on this domain version
            if e.response["Error"]["Code"] not in ("ValidationException",):
                raise

    # Projects
    paginator = dz.get_paginator("list_projects")
    for page in paginator.paginate(domainIdentifier=domain_id):
        s.projects.extend(page.get("items", []))

    for p in s.projects:
        pid = p["id"]
        members: list[dict] = []
        try:
            paginator = dz.get_paginator("list_project_memberships")
            for page in paginator.paginate(domainIdentifier=domain_id, projectIdentifier=pid):
                members.extend(page.get("members", []))
        except ClientError:
            pass
        s.project_members[pid] = members

        envs: list[dict] = []
        try:
            paginator = dz.get_paginator("list_environments")
            for page in paginator.paginate(domainIdentifier=domain_id, projectIdentifier=pid):
                envs.extend(page.get("items", []))
        except ClientError:
            pass
        s.environments[pid] = envs

        sources: list[dict] = []
        try:
            paginator = dz.get_paginator("list_data_sources")
            for page in paginator.paginate(domainIdentifier=domain_id, projectIdentifier=pid):
                sources.extend(page.get("items", []))
        except ClientError:
            pass
        s.data_sources[pid] = sources

    # Search-based collections (glossaries, glossary terms, assets, listings)
    s.glossaries = _search_all(dz, domain_id, "GLOSSARY", "glossaryItem")
    s.glossary_terms = _search_all(dz, domain_id, "GLOSSARY_TERM", "glossaryTermItem")
    s.assets = _search_all(dz, domain_id, "ASSET", "assetItem")

    # Published listings (data products available for subscription)
    next_token: str | None = None
    while True:
        kwargs: dict[str, Any] = {"domainIdentifier": domain_id}
        if next_token:
            kwargs["nextToken"] = next_token
        try:
            resp = dz.search_listings(**kwargs)
        except ClientError:
            break
        for it in resp.get("items", []):
            s.listings.append(it)
        next_token = resp.get("nextToken")
        if not next_token:
            break

    return s


def _search_all(dz, domain_id: str, scope: str, item_key: str) -> list[dict]:
    out: list[dict] = []
    next_token: str | None = None
    while True:
        kwargs: dict[str, Any] = {"domainIdentifier": domain_id, "searchScope": scope}
        if next_token:
            kwargs["nextToken"] = next_token
        try:
            resp = dz.search(**kwargs)
        except ClientError:
            return out
        for it in resp.get("items", []):
            obj = it.get(item_key)
            if obj:
                out.append(obj)
        next_token = resp.get("nextToken")
        if not next_token:
            return out


# -------------------------- scoring rules --------------------------


PRINCIPLES = {
    1: "Domain ownership",
    2: "Data as a product",
    3: "Self-serve data platform",
    4: "Federated computational governance",
}


def score_principle_1(s: Signals) -> PrincipleScore:
    """Domain ownership: are there multiple bounded contexts (units), each with
    distinct owners, and projects parented to those units rather than root?"""
    findings: list[Finding] = []
    score = 100.0

    if len(s.units) == 0:
        f = Finding(
            id="P1-NO-UNITS",
            principle=1,
            severity="high",
            title="No domain units defined",
            detail=(
                "A data mesh needs explicit bounded contexts. With zero domain "
                "units, every project lives at the root, which is the same "
                "anti-pattern as a centralized data lake under a different name."
            ),
            remediation=(
                "Create at least 2-3 DataZone domain units that map to your "
                "organization's bounded contexts (e.g. 'sales', 'logistics', "
                "'marketing') and parent projects under them."
            ),
            points_lost=40,
        )
        findings.append(f)
        score -= f.points_lost
    elif len(s.units) == 1:
        f = Finding(
            id="P1-ONE-UNIT",
            principle=1,
            severity="medium",
            title="Only one domain unit",
            detail=(
                f"Domain has 1 unit ({s.units[0].get('name')}). A single unit "
                "doesn't reflect organizational decomposition -- the value of "
                "domain ownership comes from multiple teams owning their slice."
            ),
            remediation=(
                "Add at least one more domain unit so projects can be parented "
                "to genuinely distinct bounded contexts."
            ),
            points_lost=15,
        )
        findings.append(f)
        score -= f.points_lost

    # Projects per unit distribution
    by_unit: dict[str, int] = {}
    for p in s.projects:
        unit = p.get("domainUnitId") or s.root_unit_id
        by_unit[unit] = by_unit.get(unit, 0) + 1

    projects_at_root = by_unit.get(s.root_unit_id, 0)
    if s.projects and projects_at_root == len(s.projects):
        f = Finding(
            id="P1-PROJECTS-AT-ROOT",
            principle=1,
            severity="high",
            title="All projects parented to root domain unit",
            detail=(
                f"All {len(s.projects)} projects are at the root unit. Domain "
                "ownership requires projects to be owned by the unit that "
                "represents the producing team."
            ),
            remediation=(
                "Re-parent each project under the appropriate domain unit "
                "using DataZone's MoveProject (or recreate if needed)."
            ),
            points_lost=25,
        )
        findings.append(f)
        score -= f.points_lost
    elif projects_at_root > 0 and s.projects:
        ratio = projects_at_root / len(s.projects)
        if ratio > 0.5:
            f = Finding(
                id="P1-MOSTLY-ROOT",
                principle=1,
                severity="medium",
                title=f"{projects_at_root}/{len(s.projects)} projects still at root unit",
                detail="Most projects are not yet parented under a domain unit.",
                remediation="Move remaining projects under their owning domain units.",
                points_lost=10,
            )
            findings.append(f)
            score -= f.points_lost

    # Distinct owners per unit (we can only check root_unit_owners cheaply)
    if not s.root_unit_owners:
        f = Finding(
            id="P1-NO-ROOT-OWNERS",
            principle=1,
            severity="medium",
            title="Root domain unit has no explicit owners",
            detail="Without owners, accountability for the mesh as a whole is ambiguous.",
            remediation="Assign at least one user or group as owner of the root unit.",
            points_lost=10,
        )
        findings.append(f)
        score -= f.points_lost

    if not s.projects:
        f = Finding(
            id="P1-NO-PROJECTS",
            principle=1,
            severity="high",
            title="No projects in the domain",
            detail="A mesh with no projects has no producers and no consumers.",
            remediation="Stand up at least one producer project and one consumer project.",
            points_lost=20,
        )
        findings.append(f)
        score -= f.points_lost

    return PrincipleScore(1, PRINCIPLES[1], max(score, 0.0), findings)


def score_principle_2(s: Signals) -> PrincipleScore:
    """Data as a product: assets exist, are described, and are published as
    listings so consumers can discover and subscribe to them."""
    findings: list[Finding] = []
    score = 100.0

    if not s.assets:
        f = Finding(
            id="P2-NO-ASSETS",
            principle=2,
            severity="high",
            title="No assets registered in the domain",
            detail="Without assets, there is nothing to treat as a product.",
            remediation=(
                "Create a DataZone data source on a producer project (Glue, "
                "Redshift, or S3) and run it to register inventory assets."
            ),
            points_lost=40,
        )
        findings.append(f)
        score -= f.points_lost
        return PrincipleScore(2, PRINCIPLES[2], max(score, 0.0), findings)

    # Listings: percentage of assets that are published
    n_assets = len(s.assets)
    n_listings = len(s.listings)
    publish_ratio = n_listings / n_assets if n_assets else 0.0
    if publish_ratio == 0:
        f = Finding(
            id="P2-NO-LISTINGS",
            principle=2,
            severity="high",
            title=f"None of the {n_assets} assets are published as listings",
            detail=(
                "Assets that aren't published cannot be discovered or "
                "subscribed to -- they are inventory, not products."
            ),
            remediation=(
                "From each producer project, publish the curated assets as "
                "listings so consumers can subscribe to them."
            ),
            points_lost=30,
        )
        findings.append(f)
        score -= f.points_lost
    elif publish_ratio < 0.5:
        f = Finding(
            id="P2-LOW-PUBLISH-RATIO",
            principle=2,
            severity="medium",
            title=f"Only {n_listings}/{n_assets} assets are published",
            detail="Most assets remain inventory rather than discoverable products.",
            remediation="Promote curated assets to listings; archive the rest.",
            points_lost=15,
        )
        findings.append(f)
        score -= f.points_lost

    # Descriptions on assets
    described = sum(1 for a in s.assets if (a.get("description") or "").strip())
    described_ratio = described / n_assets if n_assets else 0.0
    if described_ratio < 0.5:
        f = Finding(
            id="P2-MISSING-DESCRIPTIONS",
            principle=2,
            severity="medium",
            title=f"Only {described}/{n_assets} assets have descriptions",
            detail=(
                "A data product without a description fails the discoverability "
                "and self-describing pillars of 'data as a product'."
            ),
            remediation=(
                "Backfill asset descriptions -- consider running the "
                "auto-cataloger (Bedrock-powered) over the source Glue catalog."
            ),
            points_lost=15,
        )
        findings.append(f)
        score -= f.points_lost

    return PrincipleScore(2, PRINCIPLES[2], max(score, 0.0), findings)


def score_principle_3(s: Signals) -> PrincipleScore:
    """Self-serve data platform: producer projects have a data source +
    environment; consumer projects have an environment to subscribe from."""
    findings: list[Finding] = []
    score = 100.0

    if not s.projects:
        f = Finding(
            id="P3-NO-PROJECTS",
            principle=3,
            severity="high",
            title="No projects -- self-serve has nothing to serve",
            detail="The platform is unused.",
            remediation="Provision producer + consumer projects.",
            points_lost=40,
        )
        findings.append(f)
        score -= f.points_lost
        return PrincipleScore(3, PRINCIPLES[3], max(score, 0.0), findings)

    projects_with_env = sum(1 for p in s.projects if s.environments.get(p["id"]))
    projects_with_source = sum(1 for p in s.projects if s.data_sources.get(p["id"]))

    env_ratio = projects_with_env / len(s.projects)
    src_ratio = projects_with_source / len(s.projects)

    if env_ratio == 0:
        f = Finding(
            id="P3-NO-ENVIRONMENTS",
            principle=3,
            severity="high",
            title="No project has a provisioned environment",
            detail=(
                "Without an environment, a project cannot run analytics or "
                "subscribe to data. Self-serve provisioning is the entire "
                "promise of principle 3."
            ),
            remediation=(
                "Create at least one environment per active project (Athena, "
                "Redshift, or SageMaker blueprint)."
            ),
            points_lost=30,
        )
        findings.append(f)
        score -= f.points_lost
    elif env_ratio < 1.0:
        missing = len(s.projects) - projects_with_env
        f = Finding(
            id="P3-PARTIAL-ENVIRONMENTS",
            principle=3,
            severity="medium",
            title=f"{missing} project(s) have no environment",
            detail="Some projects cannot consume or produce data products.",
            remediation="Provision environments for the remaining projects.",
            points_lost=12,
        )
        findings.append(f)
        score -= f.points_lost

    if src_ratio == 0 and s.assets:
        # Have assets but no project owns a data source -- weird state
        f = Finding(
            id="P3-NO-DATASOURCES",
            principle=3,
            severity="medium",
            title="Assets exist but no project owns a data source",
            detail="Assets were likely created manually rather than provisioned via a self-serve data source.",
            remediation="Define DataZone data sources on producer projects so asset publication is repeatable.",
            points_lost=12,
        )
        findings.append(f)
        score -= f.points_lost

    # Project memberships: a single-member project (just the creator) signals
    # that the platform isn't actually shared.
    solo = [p for p in s.projects if len(s.project_members.get(p["id"], [])) <= 1]
    if solo and len(solo) == len(s.projects):
        f = Finding(
            id="P3-SOLO-PROJECTS",
            principle=3,
            severity="low",
            title="Every project has exactly one member",
            detail=(
                "Projects are personal sandboxes rather than shared team "
                "workspaces. The platform isn't being used as a collaboration surface."
            ),
            remediation="Add team members to each project; assign group ownership.",
            points_lost=8,
        )
        findings.append(f)
        score -= f.points_lost

    return PrincipleScore(3, PRINCIPLES[3], max(score, 0.0), findings)


def score_principle_4(s: Signals) -> PrincipleScore:
    """Federated computational governance: glossary exists, terms are defined,
    and policy grants are wired so creation/governance is delegated."""
    findings: list[Finding] = []
    score = 100.0

    if not s.glossaries:
        f = Finding(
            id="P4-NO-GLOSSARY",
            principle=4,
            severity="high",
            title="No business glossary defined",
            detail=(
                "Federated governance requires a shared semantic layer. Without "
                "a glossary, every project will reinvent terminology."
            ),
            remediation="Create at least one glossary (suggest: BusinessTerms, owned by a steward project) and seed it with terms.",
            points_lost=30,
        )
        findings.append(f)
        score -= f.points_lost
    else:
        if not s.glossary_terms:
            f = Finding(
                id="P4-EMPTY-GLOSSARY",
                principle=4,
                severity="medium",
                title=f"{len(s.glossaries)} glossary/glossaries exist but contain no terms",
                detail="An empty glossary is a placeholder, not governance.",
                remediation="Add terms for the most-used business concepts (Revenue, Customer, Cohort, etc).",
                points_lost=15,
            )
            findings.append(f)
            score -= f.points_lost
        elif len(s.glossary_terms) < 5:
            f = Finding(
                id="P4-FEW-TERMS",
                principle=4,
                severity="low",
                title=f"Only {len(s.glossary_terms)} glossary term(s) defined",
                detail="A glossary with very few terms suggests early adoption.",
                remediation="Continue seeding terms; aim for >= 1 term per producer domain.",
                points_lost=5,
            )
            findings.append(f)
            score -= f.points_lost

    # Policy grants on root unit -- federation depends on delegated creation
    grant_types = {g.get("_policyType") for g in s.policy_grants_root}
    if "CREATE_PROJECT" not in grant_types:
        f = Finding(
            id="P4-NO-CREATE-PROJECT-GRANT",
            principle=4,
            severity="medium",
            title="No CREATE_PROJECT policy grant on root unit",
            detail=(
                "Without an inheritable CREATE_PROJECT grant, only the domain "
                "creator can stand up new projects. That's centralization, not federation."
            ),
            remediation=(
                "Grant CREATE_PROJECT (with includeChildDomainUnits=true) to "
                "trusted user(s) or group(s)."
            ),
            points_lost=15,
        )
        findings.append(f)
        score -= f.points_lost

    if "CREATE_GLOSSARY" not in grant_types and s.glossaries:
        # If glossaries exist but no grant, glossary creation was console-driven
        f = Finding(
            id="P4-NO-CREATE-GLOSSARY-GRANT",
            principle=4,
            severity="low",
            title="No CREATE_GLOSSARY grant on root unit",
            detail="Future glossary creation cannot be self-served by stewards.",
            remediation="Grant CREATE_GLOSSARY to the steward project as a project principal.",
            points_lost=5,
        )
        findings.append(f)
        score -= f.points_lost

    # Glossary terms attached to assets? (proxy for actual federation)
    if s.assets and s.glossary_terms:
        annotated = sum(1 for a in s.assets if a.get("glossaryTerms"))
        ratio = annotated / len(s.assets)
        if ratio == 0:
            f = Finding(
                id="P4-TERMS-NOT-ATTACHED",
                principle=4,
                severity="medium",
                title="Glossary terms exist but are not attached to any asset",
                detail=(
                    "Governance only takes effect when shared terminology is "
                    "actually applied to assets."
                ),
                remediation="Tag each published asset with at least one relevant glossary term.",
                points_lost=12,
            )
            findings.append(f)
            score -= f.points_lost

    return PrincipleScore(4, PRINCIPLES[4], max(score, 0.0), findings)


def score_all(s: Signals) -> list[PrincipleScore]:
    return [
        score_principle_1(s),
        score_principle_2(s),
        score_principle_3(s),
        score_principle_4(s),
    ]


# -------------------------- rendering --------------------------


def stage_label(score: float) -> str:
    if score >= 80:
        return "Mature"
    if score >= 60:
        return "Operational"
    if score >= 40:
        return "Emerging"
    if score >= 20:
        return "Initial"
    return "Pre-mesh"


def render_markdown(s: Signals, scores: list[PrincipleScore]) -> str:
    overall = sum(p.score for p in scores) / len(scores)
    lines: list[str] = []
    lines.append(f"# Data Mesh Maturity Assessment -- {s.domain_name}")
    lines.append("")
    lines.append(f"- **Domain ID**: `{s.domain_id}`")
    lines.append(f"- **Domain version**: {s.domain_version}")
    lines.append(f"- **Assessed at**: {datetime.now(timezone.utc).isoformat(timespec='seconds')}")
    lines.append(f"- **Overall score**: **{overall:.1f} / 100**  -- {stage_label(overall)}")
    lines.append("")
    lines.append("## Inventory")
    lines.append("")
    lines.append(f"| Resource | Count |")
    lines.append(f"|---|---:|")
    lines.append(f"| Domain units | {len(s.units)} |")
    lines.append(f"| Projects | {len(s.projects)} |")
    lines.append(f"| Glossaries | {len(s.glossaries)} |")
    lines.append(f"| Glossary terms | {len(s.glossary_terms)} |")
    lines.append(f"| Assets (inventory) | {len(s.assets)} |")
    lines.append(f"| Listings (published) | {len(s.listings)} |")
    lines.append(f"| Root-unit policy grants | {len(s.policy_grants_root)} |")
    lines.append("")

    lines.append("## Scores by principle")
    lines.append("")
    lines.append("| # | Principle | Score | Stage |")
    lines.append("|---|---|---:|---|")
    for p in scores:
        lines.append(f"| {p.number} | {p.name} | {p.score:.1f} | {stage_label(p.score)} |")
    lines.append("")

    # Prioritized remediation list (sorted by severity then points lost)
    all_findings: list[Finding] = []
    for p in scores:
        all_findings.extend(p.findings)
    all_findings.sort(key=lambda f: (f.priority(), -f.points_lost))

    if all_findings:
        lines.append("## Prioritized remediation list")
        lines.append("")
        for i, f in enumerate(all_findings, 1):
            lines.append(f"### {i}. [{f.severity.upper()}] (P{f.principle}) {f.title}")
            lines.append("")
            lines.append(f"- **Detail**: {f.detail}")
            lines.append(f"- **Remediation**: {f.remediation}")
            lines.append(f"- **Score impact**: -{f.points_lost:.0f} points on principle {f.principle}")
            lines.append("")
    else:
        lines.append("## Prioritized remediation list")
        lines.append("")
        lines.append("_No findings -- the deployment scored cleanly across all principles._")
        lines.append("")

    return "\n".join(lines)


def render_json(s: Signals, scores: list[PrincipleScore]) -> str:
    overall = sum(p.score for p in scores) / len(scores)
    payload = {
        "domain_id": s.domain_id,
        "domain_name": s.domain_name,
        "assessed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_score": round(overall, 1),
        "overall_stage": stage_label(overall),
        "principles": [
            {
                "number": p.number,
                "name": p.name,
                "score": round(p.score, 1),
                "stage": stage_label(p.score),
                "findings": [asdict(f) for f in p.findings],
            }
            for p in scores
        ],
        "inventory": {
            "units": len(s.units),
            "projects": len(s.projects),
            "glossaries": len(s.glossaries),
            "glossary_terms": len(s.glossary_terms),
            "assets": len(s.assets),
            "listings": len(s.listings),
            "policy_grants_root": len(s.policy_grants_root),
        },
    }
    return json.dumps(payload, indent=2)


# -------------------------- CLI --------------------------


def resolve_domain(dz, domain_arg: str | None, domain_name: str | None) -> str:
    if domain_arg:
        return domain_arg
    paginator = dz.get_paginator("list_domains")
    candidates = []
    for page in paginator.paginate():
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


def main() -> None:
    parser = argparse.ArgumentParser(prog="dmma", description="Data mesh maturity assessment")
    parser.add_argument("--profile", default=os.environ.get("AWS_PROFILE", "default"))
    parser.add_argument("--region", default=os.environ.get("AWS_REGION", "us-east-1"))
    parser.add_argument("--domain-id", default=None, help="explicit DataZone domain ID (overrides --domain-name)")
    parser.add_argument("--domain-name", default=None, help="DataZone domain name to assess")
    parser.add_argument("--out-dir", default="out", help="directory for JSON + Markdown report")
    args = parser.parse_args()

    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    dz = session.client("datazone")

    domain_id = resolve_domain(dz, args.domain_id, args.domain_name)
    print(f"== assessing domain {domain_id} (region={args.region}, profile={args.profile}) ==")
    signals = collect_signals(dz, domain_id)
    print(
        f"  collected: units={len(signals.units)} projects={len(signals.projects)} "
        f"glossaries={len(signals.glossaries)} terms={len(signals.glossary_terms)} "
        f"assets={len(signals.assets)} listings={len(signals.listings)}"
    )
    scores = score_all(signals)

    md = render_markdown(signals, scores)
    js = render_json(signals, scores)

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    md_path = out / f"{signals.domain_name}-assessment.md"
    json_path = out / f"{signals.domain_name}-assessment.json"
    md_path.write_text(md)
    json_path.write_text(js)

    print()
    print(md)
    print()
    print(f"== wrote {md_path} and {json_path} ==")
