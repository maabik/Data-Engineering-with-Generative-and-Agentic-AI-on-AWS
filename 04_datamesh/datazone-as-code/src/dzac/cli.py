"""datazone-as-code CLI: apply / destroy a YAML manifest of DataZone resources."""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import boto3
import yaml
from botocore.exceptions import ClientError


# ---------- IAM helpers (auto-create domain execution role) ----------

DOMAIN_EXEC_ROLE_NAME = "DZACDomainExecutionRole"
DOMAIN_EXEC_TRUST = {
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Principal": {"Service": "datazone.amazonaws.com"},
            "Action": ["sts:AssumeRole", "sts:TagSession"],
        }
    ],
}
DOMAIN_EXEC_POLICY_ARN = (
    "arn:aws:iam::aws:policy/service-role/AmazonDataZoneDomainExecutionRolePolicy"
)


def ensure_domain_execution_role(session) -> str:
    """Create or return the ARN of the DataZone domain execution role."""
    iam = session.client("iam")
    try:
        resp = iam.get_role(RoleName=DOMAIN_EXEC_ROLE_NAME)
        return resp["Role"]["Arn"]
    except ClientError as e:
        if e.response["Error"]["Code"] != "NoSuchEntity":
            raise

    print(f"  creating IAM role {DOMAIN_EXEC_ROLE_NAME}")
    resp = iam.create_role(
        RoleName=DOMAIN_EXEC_ROLE_NAME,
        AssumeRolePolicyDocument=json.dumps(DOMAIN_EXEC_TRUST),
        Description="DataZone domain execution role created by datazone-as-code",
    )
    iam.attach_role_policy(
        RoleName=DOMAIN_EXEC_ROLE_NAME, PolicyArn=DOMAIN_EXEC_POLICY_ARN
    )
    # IAM eventual consistency before DataZone tries to assume it
    time.sleep(10)
    return resp["Role"]["Arn"]


def delete_domain_execution_role(session) -> None:
    iam = session.client("iam")
    try:
        iam.detach_role_policy(
            RoleName=DOMAIN_EXEC_ROLE_NAME, PolicyArn=DOMAIN_EXEC_POLICY_ARN
        )
    except ClientError as e:
        if e.response["Error"]["Code"] not in ("NoSuchEntity",):
            raise
    try:
        iam.delete_role(RoleName=DOMAIN_EXEC_ROLE_NAME)
        print(f"  deleted IAM role {DOMAIN_EXEC_ROLE_NAME}")
    except ClientError as e:
        if e.response["Error"]["Code"] != "NoSuchEntity":
            raise


# ---------- DataZone helpers ----------


def find_domain_by_name(dz, name: str) -> dict | None:
    paginator = dz.get_paginator("list_domains")
    for page in paginator.paginate():
        for d in page.get("items", []):
            if d.get("name") == name:
                return d
    return None


def wait_domain_available(dz, domain_id: str, timeout_s: int = 600) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        d = dz.get_domain(identifier=domain_id)
        status = d.get("status")
        if status == "AVAILABLE":
            return
        if status in ("CREATION_FAILED", "DELETION_FAILED"):
            raise RuntimeError(f"domain {domain_id} entered {status}")
        time.sleep(5)
    raise TimeoutError(f"domain {domain_id} not AVAILABLE within {timeout_s}s")


def find_domain_unit_by_name(dz, domain_id: str, parent_id: str, name: str) -> str | None:
    paginator = dz.get_paginator("list_domain_units_for_parent")
    for page in paginator.paginate(
        domainIdentifier=domain_id, parentDomainUnitIdentifier=parent_id
    ):
        for u in page.get("items", []):
            if u.get("name") == name:
                return u.get("id")
    return None


def find_project_by_name(dz, domain_id: str, name: str) -> str | None:
    paginator = dz.get_paginator("list_projects")
    for page in paginator.paginate(domainIdentifier=domain_id):
        for p in page.get("items", []):
            if p.get("name") == name:
                return p.get("id")
    return None


def find_glossary_by_name(dz, domain_id: str, project_id: str, name: str) -> str | None:
    # No list_glossaries paginator; use search.
    next_token = None
    while True:
        kwargs = {
            "domainIdentifier": domain_id,
            "owningProjectIdentifier": project_id,
            "searchScope": "GLOSSARY",
        }
        if next_token:
            kwargs["nextToken"] = next_token
        resp = dz.search(**kwargs)
        for it in resp.get("items", []):
            g = it.get("glossaryItem")
            if g and g.get("name") == name:
                return g.get("id")
        next_token = resp.get("nextToken")
        if not next_token:
            return None


def find_term_by_name(dz, domain_id: str, glossary_id: str, name: str) -> str | None:
    next_token = None
    while True:
        kwargs = {
            "domainIdentifier": domain_id,
            "searchScope": "GLOSSARY_TERM",
            "filters": {
                "filter": {"attribute": "glossaryId", "value": glossary_id}
            },
        }
        if next_token:
            kwargs["nextToken"] = next_token
        resp = dz.search(**kwargs)
        for it in resp.get("items", []):
            t = it.get("glossaryTermItem")
            if t and t.get("name") == name:
                return t.get("id")
        next_token = resp.get("nextToken")
        if not next_token:
            return None


# ---------- apply / destroy ----------


def cmd_apply(args) -> int:
    manifest = yaml.safe_load(Path(args.manifest).read_text())
    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    sts = session.client("sts")
    acct = sts.get_caller_identity()["Account"]
    print(f"== applying manifest: account={acct} region={args.region} profile={args.profile} ==")

    domain_cfg = manifest["domain"]
    role_arn = domain_cfg.get("execution_role") or ensure_domain_execution_role(session)

    dz = session.client("datazone")

    # 1. Domain
    existing = find_domain_by_name(dz, domain_cfg["name"])
    if existing:
        domain_id = existing["id"]
        print(f"[domain] reuse {domain_cfg['name']} -> {domain_id}")
    else:
        print(f"[domain] create {domain_cfg['name']}")
        kwargs = {
            "name": domain_cfg["name"],
            "domainExecutionRole": role_arn,
        }
        if domain_cfg.get("description"):
            kwargs["description"] = domain_cfg["description"]
        if domain_cfg.get("version"):
            kwargs["domainVersion"] = domain_cfg["version"]
        if domain_cfg.get("service_role"):
            kwargs["serviceRole"] = domain_cfg["service_role"]
        if domain_cfg.get("tags"):
            kwargs["tags"] = {k: str(v) for k, v in domain_cfg["tags"].items()}
        resp = dz.create_domain(**kwargs)
        domain_id = resp["id"]

    wait_domain_available(dz, domain_id)
    root_unit = dz.get_domain(identifier=domain_id).get("rootDomainUnitId")
    print(f"  domain={domain_id} root_unit={root_unit}")

    # 1b. Register caller as owner of the root domain unit so subsequent
    # CreateProject / CreateGlossary calls are authorized. The caller must be
    # known to DataZone as a "user profile" first.
    caller_arn = sts.get_caller_identity()["Arn"]
    try:
        dz.create_user_profile(
            domainIdentifier=domain_id,
            userIdentifier=caller_arn,
            userType="IAM_USER" if ":user/" in caller_arn else "IAM_ROLE",
        )
        print(f"  registered user profile for {caller_arn}")
    except ClientError as e:
        # ConflictException: profile already exists in this domain
        # ValidationException("Cannot create a user profile for existing principalId"):
        #   the principal already has a profile from a prior domain in this account
        code = e.response["Error"]["Code"]
        msg = e.response["Error"].get("Message", "")
        if code == "ConflictException":
            pass
        elif code == "ValidationException" and "existing principalId" in msg:
            pass
        else:
            raise

    # The owner reference uses the DataZone user profile ID, not the IAM ARN.
    profile = dz.get_user_profile(domainIdentifier=domain_id, userIdentifier=caller_arn)
    profile_id = profile["id"]
    try:
        dz.add_entity_owner(
            domainIdentifier=domain_id,
            entityType="DOMAIN_UNIT",
            entityIdentifier=root_unit,
            owner={"user": {"userIdentifier": profile_id}},
        )
        print(f"  caller granted DOMAIN_UNIT ownership on root_unit")
    except ClientError as e:
        code = e.response["Error"]["Code"]
        msg = e.response["Error"].get("Message", "")
        if code == "ConflictException":
            pass
        elif code == "ValidationException" and "Owner already exists" in msg:
            pass
        else:
            raise

    # Owners can manage entity owners but cannot create projects without an
    # explicit policy grant. Grant the caller CREATE_PROJECT and CREATE_DOMAIN_UNIT
    # on the root unit (inherited by child units). Glossaries are scoped to a
    # project, so glossary creation is authorized via project ownership rather
    # than a domain-unit-level grant.
    for policy_type, detail_key in [
        ("CREATE_PROJECT", "createProject"),
        ("CREATE_DOMAIN_UNIT", "createDomainUnit"),
    ]:
        try:
            dz.add_policy_grant(
                domainIdentifier=domain_id,
                entityType="DOMAIN_UNIT",
                entityIdentifier=root_unit,
                policyType=policy_type,
                principal={"user": {"userIdentifier": profile_id}},
                detail={detail_key: {"includeChildDomainUnits": True}},
            )
            print(f"  granted {policy_type} on root_unit")
        except ClientError as e:
            code = e.response["Error"]["Code"]
            msg = e.response["Error"].get("Message", "")
            if code == "ConflictException" or "already exists" in msg.lower():
                pass
            else:
                raise

    # 2. Domain units
    unit_ids: dict[str, str] = {}
    for u in manifest.get("domain_units", []):
        existing_id = find_domain_unit_by_name(dz, domain_id, root_unit, u["name"])
        if existing_id:
            unit_id = existing_id
            print(f"[unit] reuse {u['name']} -> {unit_id}")
        else:
            print(f"[unit] create {u['name']}")
            resp = dz.create_domain_unit(
                domainIdentifier=domain_id,
                parentDomainUnitIdentifier=root_unit,
                name=u["name"],
            )
            unit_id = resp["id"]
        unit_ids[u["key"]] = unit_id

    # 3. Projects
    project_ids: dict[str, str] = {}
    for p in manifest.get("projects", []):
        existing_id = find_project_by_name(dz, domain_id, p["name"])
        if existing_id:
            print(f"[project] reuse {p['name']} -> {existing_id}")
            project_ids[p["key"]] = existing_id
            continue
        kwargs = {
            "domainIdentifier": domain_id,
            "name": p["name"],
        }
        if p.get("description"):
            kwargs["description"] = p["description"]
        if p.get("domain_unit"):
            kwargs["domainUnitId"] = unit_ids[p["domain_unit"]]
        print(f"[project] create {p['name']}")
        resp = dz.create_project(**kwargs)
        project_ids[p["key"]] = resp["id"]

    # 4. Glossaries + terms
    for g in manifest.get("glossaries", []):
        owning = project_ids[g["owning_project"]]
        # CreateGlossary requires a CREATE_GLOSSARY policy grant whose principal
        # is the owning PROJECT. Grant it once per (project, root_unit) pair.
        try:
            dz.add_policy_grant(
                domainIdentifier=domain_id,
                entityType="DOMAIN_UNIT",
                entityIdentifier=root_unit,
                policyType="CREATE_GLOSSARY",
                principal={
                    "project": {
                        "projectIdentifier": owning,
                        "projectDesignation": "OWNER",
                    }
                },
                detail={"createGlossary": {"includeChildDomainUnits": True}},
            )
        except ClientError as e:
            code = e.response["Error"]["Code"]
            msg = e.response["Error"].get("Message", "")
            if code == "ConflictException" or "already exists" in msg.lower():
                pass
            else:
                raise

        existing_id = find_glossary_by_name(dz, domain_id, owning, g["name"])
        if existing_id:
            glossary_id = existing_id
            print(f"[glossary] reuse {g['name']} -> {glossary_id}")
        else:
            print(f"[glossary] create {g['name']}")
            kwargs = {
                "domainIdentifier": domain_id,
                "name": g["name"],
                "owningProjectIdentifier": owning,
                "status": "ENABLED",
            }
            if g.get("description"):
                kwargs["description"] = g["description"]
            resp = dz.create_glossary(**kwargs)
            glossary_id = resp["id"]

        for t in g.get("terms", []):
            existing_term = find_term_by_name(dz, domain_id, glossary_id, t["name"])
            if existing_term:
                print(f"  [term] reuse {t['name']} -> {existing_term}")
                continue
            print(f"  [term] create {t['name']}")
            kwargs = {
                "domainIdentifier": domain_id,
                "glossaryIdentifier": glossary_id,
                "name": t["name"],
                "status": "ENABLED",
            }
            if t.get("short_description"):
                kwargs["shortDescription"] = t["short_description"]
            if t.get("long_description"):
                kwargs["longDescription"] = t["long_description"]
            dz.create_glossary_term(**kwargs)

    print()
    print("== apply complete ==")
    print(f"   domain id : {domain_id}")
    print(f"   projects  : {project_ids}")
    print(f"   units     : {unit_ids}")
    return 0


def cmd_destroy(args) -> int:
    manifest = yaml.safe_load(Path(args.manifest).read_text())
    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    sts = session.client("sts")
    acct = sts.get_caller_identity()["Account"]
    print(f"== destroying manifest: account={acct} region={args.region} profile={args.profile} ==")

    dz = session.client("datazone")
    domain_cfg = manifest["domain"]
    domain = find_domain_by_name(dz, domain_cfg["name"])
    if not domain:
        print(f"[domain] {domain_cfg['name']} not present, nothing to destroy")
    else:
        domain_id = domain["id"]
        print(f"[domain] deleting {domain_cfg['name']} ({domain_id}) -- cascade=true")
        try:
            dz.delete_domain(identifier=domain_id, skipDeletionCheck=True)
        except ClientError as e:
            if e.response["Error"]["Code"] not in ("ResourceNotFoundException",):
                raise
        # Wait for deletion. Once deletion starts, GetDomain may return
        # AccessDenied (the caller's domain-scoped permissions are revoked
        # along with the domain). Treat that the same as ResourceNotFound.
        timeout = time.time() + 600
        while time.time() < timeout:
            try:
                d = dz.get_domain(identifier=domain_id)
                status = d.get("status")
                if status in ("DELETED",):
                    break
                if status == "DELETION_FAILED":
                    raise RuntimeError(f"domain {domain_id} deletion failed")
                print(f"  domain status={status}, waiting...")
                time.sleep(10)
            except ClientError as e:
                code = e.response["Error"]["Code"]
                if code in ("ResourceNotFoundException", "AccessDeniedException"):
                    break
                raise
        print(f"  domain {domain_id} deleted")

    if not args.keep_role:
        delete_domain_execution_role(session)

    print()
    print("== destroy complete ==")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="dzac", description="datazone-as-code")
    sub = parser.add_subparsers(dest="cmd", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--manifest", required=True, help="path to YAML manifest")
    common.add_argument("--profile", default="default", help="AWS CLI profile")
    common.add_argument("--region", default="us-east-1", help="AWS region")

    p_apply = sub.add_parser("apply", parents=[common], help="create/reconcile resources")
    p_apply.set_defaults(func=cmd_apply)

    p_destroy = sub.add_parser("destroy", parents=[common], help="delete resources")
    p_destroy.add_argument(
        "--keep-role",
        action="store_true",
        help="leave the auto-created domain execution role in place",
    )
    p_destroy.set_defaults(func=cmd_destroy)

    args = parser.parse_args()
    sys.exit(args.func(args))
