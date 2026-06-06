"""Collector helpers: tolerant API calls + region-pinned client factory."""
from __future__ import annotations

import logging
from typing import Any, Callable

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

log = logging.getLogger(__name__)


def make_client(service: str, region: str, profile: str | None = None) -> Any:
    session = boto3.Session(profile_name=profile, region_name=region)
    cfg = Config(retries={"max_attempts": 3, "mode": "standard"})
    return session.client(service, config=cfg)


def safe_call(fn: Callable[..., Any], *args, default: Any = None, **kwargs) -> Any:
    """Call AWS API, swallow AccessDenied / NotFound / ServiceUnavailable.

    Returns `default` (typically [] or {}) on any client/boto error so collectors
    degrade rather than abort the whole scan.
    """
    try:
        return fn(*args, **kwargs)
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        log.debug("safe_call(%s) suppressed %s", getattr(fn, "__name__", "?"), code)
        return default
    except BotoCoreError as e:
        log.debug("safe_call(%s) suppressed BotoCoreError %s", getattr(fn, "__name__", "?"), e)
        return default


def paginate(client: Any, op_name: str, key: str, default=None, **kwargs) -> list:
    """Run a paginator and concatenate results from `key`. Returns [] on error."""
    try:
        paginator = client.get_paginator(op_name)
        results: list = []
        for page in paginator.paginate(**kwargs):
            results.extend(page.get(key, []) or [])
        return results
    except (ClientError, BotoCoreError) as e:
        log.debug("paginate(%s) failed: %s", op_name, e)
        return default if default is not None else []
