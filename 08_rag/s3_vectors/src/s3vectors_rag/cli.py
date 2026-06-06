from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import Config
from .embeddings import TitanEmbedder
from .ingest import ingest_paths
from .logging_setup import configure, get_logger
from .store import S3VectorStore


log = get_logger("cli")


def _parse_filters(items: Optional[List[str]]) -> Dict[str, Any]:
    """
    Parse `--filter key=value` pairs into the s3vectors `Filter` shape.

    Multiple filters AND together. Equality only — keep the surface small;
    callers wanting richer filters should call the SDK directly.
    """
    if not items:
        return {}
    clauses = []
    for item in items:
        if "=" not in item:
            raise SystemExit(f"--filter must be key=value, got: {item}")
        k, v = item.split("=", 1)
        clauses.append({k.strip(): v.strip()})
    if len(clauses) == 1:
        return clauses[0]
    return {"$and": clauses}


def cmd_ingest(args: argparse.Namespace, cfg: Config) -> int:
    cfg.source_tag = args.source_tag
    paths = [Path(p) for p in args.paths]
    written = ingest_paths(paths, cfg)
    print(f"ingested {written} vector(s) into {cfg.bucket_name}/{cfg.index_name}")
    return 0


def cmd_query(args: argparse.Namespace, cfg: Config) -> int:
    store = S3VectorStore(cfg)
    embedder = TitanEmbedder(cfg)
    qvec = embedder.embed_one(args.text)
    flt = _parse_filters(args.filter)
    hits = store.query(qvec, top_k=args.top_k, metadata_filter=flt or None)
    if args.json:
        print(json.dumps([h.__dict__ for h in hits], indent=2, default=str))
        return 0
    if not hits:
        print("(no results)")
        return 0
    for i, h in enumerate(hits, 1):
        src = h.metadata.get("source", "?")
        loc = h.metadata.get("locator", "")
        preview = (h.metadata.get("text") or "").replace("\n", " ")
        if len(preview) > 200:
            preview = preview[:197] + "..."
        print(f"{i}. score={h.score:.3f} | {src} {loc}")
        print(f"   {preview}")
    return 0


def cmd_stats(args: argparse.Namespace, cfg: Config) -> int:
    store = S3VectorStore(cfg)
    info = store.stats()
    print(json.dumps(info, indent=2, default=str))
    return 0


def cmd_clean(args: argparse.Namespace, cfg: Config) -> int:
    if not args.yes:
        print(f"This will delete index '{cfg.index_name}' and bucket '{cfg.bucket_name}'.")
        print("Re-run with --yes to confirm.")
        return 1
    store = S3VectorStore(cfg)
    store.teardown()
    print("done")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="s3v-rag", description="S3 Vectors RAG demo CLI")
    p.add_argument("--region", help="AWS region (overrides AWS_REGION/S3V_REGION)")
    p.add_argument("--bucket", help="Vector bucket name (overrides S3V_BUCKET)")
    p.add_argument("--index", help="Index name (overrides S3V_INDEX)")
    p.add_argument("--log-level", default=None, help="DEBUG|INFO|WARNING|ERROR")

    sub = p.add_subparsers(dest="cmd", required=True)

    pi = sub.add_parser("ingest", help="Load + chunk + embed + upsert documents")
    pi.add_argument("paths", nargs="+", help="One or more files or directories")
    pi.add_argument("--source-tag", default=None, help="Override metadata.source for all chunks")
    pi.set_defaults(func=cmd_ingest)

    pq = sub.add_parser("query", help="Embed query text and run similarity search")
    pq.add_argument("text", help="Query text")
    pq.add_argument("--top-k", type=int, default=5)
    pq.add_argument("--filter", action="append", default=[], help="key=value (repeatable)")
    pq.add_argument("--json", action="store_true")
    pq.set_defaults(func=cmd_query)

    ps = sub.add_parser("stats", help="Show index info")
    ps.set_defaults(func=cmd_stats)

    pc = sub.add_parser("clean", help="Delete the index and bucket")
    pc.add_argument("--yes", action="store_true", help="Required to actually delete")
    pc.set_defaults(func=cmd_clean)

    return p


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    cfg = Config()
    if args.region:
        cfg.region = args.region
    if args.bucket:
        cfg.bucket_name = args.bucket
    if args.index:
        cfg.index_name = args.index
    if args.log_level:
        cfg.log_level = args.log_level

    configure(cfg.log_level)

    return args.func(args, cfg)


if __name__ == "__main__":
    sys.exit(main())
