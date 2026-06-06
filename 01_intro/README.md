# 01 — Intro

> Code companion for **[Data Engineering with Generative and Agentic AI on AWS](../README.md)** — *Building an AI-Augmented Data Practice for the Enterprise* by Justin J. Leto (Apress, 2026). Book site: <https://agenticdataengineering.ai>. Apress GitHub: <https://github.com/Apress/Data-Engineering-with-Generative-and-Agentic-AI-on-AWS>.

Starter material for the book. One self-contained folder: get your AI coding
tools installed before you move on to the rest of the chapters.

## Folders

### `aidlc-setup/`

Installation and authentication guide for the four terminal-based AI coding
assistants used throughout the book: **Claude Code**, **Kiro CLI**,
**Codex CLI**, and **Cursor CLI** (`cursor-agent`). Covers prerequisites
(Node, Python, AWS CLI), per-tool install and login, starter prompts, and a
per-chapter recommendation table for which tool to reach for first.

Start here if this is your first chapter.

## Default region

All deploys in this book default to **us-east-1**. Per-chapter READMEs show
the `--region` flag so you can override it; pick whatever region holds the
AWS resources you'll target.

## Cost estimate (us-east-1, light active workload)

| Project | Service | Hourly fixed | Hourly active | Notes |
|---|---|---:|---:|---|
| aidlc-setup | (none) | $0.0000 | $0.0000 | Local CLI installation only — no AWS resources. |
| **Chapter total** | | **$0.0000** | **$0.0000** | Local tooling only. |

