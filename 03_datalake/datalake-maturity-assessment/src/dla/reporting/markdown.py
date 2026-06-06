"""Markdown report renderer."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..dimensions import DIMENSION_BOOK_REFS, DIMENSION_TITLES


_LEVEL_BADGE = {1: "L1 🔴", 2: "L2 ⚠", 3: "L3 ✓", 4: "L4 ✓✓", 5: "L5 ★"}


def _effort_summary(backlog: list[dict[str, Any]]) -> tuple[int, int, int]:
    weeks_by_effort = {"S": 1, "M": 3, "L": 6, "XL": 12}
    weeks = [weeks_by_effort.get(it["effort"], 3) for it in backlog]
    return (sum(weeks), len(backlog), len([it for it in backlog if it["effort"] == "S" and it["impact"] >= 3]))


def render_markdown(account_id: str, region: str, summary: dict[str, Any], backlog: list[dict[str, Any]]) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    scores = summary["scores_by_dimension"]
    out: list[str] = []
    out.append(f"# Data Lake Maturity Assessment\n")
    out.append(f"**Account**: `{account_id}` &nbsp;&nbsp; **Region**: `{region}` &nbsp;&nbsp; **Scanned**: {ts}\n")
    out.append("")
    out.append(f"**Weighted Score**: **{summary['weighted_score']} / 5.0** — {summary['weighted_label']}  ")
    weakest_titles = ", ".join(DIMENSION_TITLES.get(n, n) for n in summary["weakest_dimensions"])
    out.append(f"**Weakest Link**: L{summary['weakest_level']} ({weakest_titles})  ")
    total_weeks, total_items, quick_wins = _effort_summary(backlog)
    out.append(f"**Remediation backlog**: {total_items} items, ≈{total_weeks} person-weeks, **{quick_wins} quick wins**.")
    out.append("")
    out.append("---")
    out.append("")
    out.append("## Per-dimension scorecard")
    out.append("")
    out.append("| # | Dimension | Score | Top Gap |")
    out.append("|---|-----------|-------|---------|")
    for i, (key, title) in enumerate(DIMENSION_TITLES.items(), 1):
        s = scores.get(key, {"level": 0, "top_gap": "n/a"})
        out.append(f"| {i} | **{title}** | {_LEVEL_BADGE.get(s['level'], '?')} | {s['top_gap']} |")
    out.append("")

    out.append("## Evidence by dimension")
    out.append("")
    for key, title in DIMENSION_TITLES.items():
        s = scores.get(key)
        if not s:
            continue
        out.append(f"### {title} — {_LEVEL_BADGE.get(s['level'], '?')}")
        out.append("")
        for line in s.get("evidence", []):
            out.append(f"- {line}")
        out.append("")
        out.append(f"_Top gap_: {s.get('top_gap', '—')}")
        out.append("")
        out.append(f"_Book reference_: {DIMENSION_BOOK_REFS.get(key, '')}")
        out.append("")

    out.append("## Remediation backlog")
    out.append("")
    if not backlog:
        out.append("_No remediation items — every dimension is at or above target level._")
    else:
        out.append("Sorted by `priority_score = impact / effort_weeks`. Items where effort=S and impact≥3 are tagged `quick win`.")
        out.append("")
        for it in backlog:
            tag = "  `quick win`" if it["effort"] == "S" and it["impact"] >= 3 else ""
            out.append(f"### {it['id']} — {it['title']}{tag}")
            out.append("")
            out.append(
                f"**Dimension**: {DIMENSION_TITLES.get(it['dimension'], it['dimension'])} "
                f"(L{it['current_level']} → L{it['target_level']}) &nbsp; "
                f"**Impact**: {it['impact']}/5 &nbsp; "
                f"**Effort**: {it['effort']} (≈{it['effort_weeks_estimate']} wk) &nbsp; "
                f"**Priority**: {it['priority_score']}"
            )
            out.append("")
            if it.get("aws_services"):
                out.append(f"**AWS services**: {', '.join(it['aws_services'])}")
            if it.get("book_reference"):
                out.append(f"**Book**: {it['book_reference']}")
            out.append("")
            if it.get("why_it_matters"):
                out.append("**Why it matters**")
                out.append("")
                out.append(it["why_it_matters"].strip())
                out.append("")
            if it.get("steps"):
                out.append("**Steps**")
                for s in it["steps"]:
                    out.append(f"- {s}")
                out.append("")
            if it.get("acceptance_criteria"):
                out.append("**Acceptance criteria**")
                for s in it["acceptance_criteria"]:
                    out.append(f"- {s}")
                out.append("")
            if it.get("reference_code"):
                out.append(f"**Reference code**: `{it['reference_code']}`")
                out.append("")
            out.append("---")
            out.append("")

    out.append("## Recommended reading order")
    out.append("")
    out.append("Start with the chapters that address the lowest-scoring dimensions first.")
    out.append("")
    sorted_dims = sorted(scores.items(), key=lambda kv: kv[1]["level"])
    seen = set()
    rank = 1
    for key, s in sorted_dims:
        ref = DIMENSION_BOOK_REFS.get(key)
        if not ref or ref in seen:
            continue
        seen.add(ref)
        out.append(f"{rank}. {ref} — closes the gap on **{DIMENSION_TITLES.get(key, key)}** (currently L{s['level']}).")
        rank += 1
        if rank > 3:
            break
    out.append("")
    out.append("---")
    out.append("")
    out.append(
        "_Generated by `datalake-maturity-assessment` — companion to "
        "*Data Engineering with Generative and Agentic AI on AWS* (Apress, 2026)._"
    )
    return "\n".join(out)
