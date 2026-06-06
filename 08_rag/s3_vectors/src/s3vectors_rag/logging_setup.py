from __future__ import annotations

import logging
import os
import sys


_CONFIGURED = False


def configure(level: str = "INFO") -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    root = logging.getLogger("s3vectors_rag")
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    handler = logging.StreamHandler(sys.stderr)
    fmt = "%(asctime)s %(levelname)s %(name)s | %(message)s"
    handler.setFormatter(logging.Formatter(fmt))
    root.addHandler(handler)
    root.propagate = False
    # Quiet noisy AWS SDK chatter unless caller explicitly raised the level.
    for noisy in ("botocore", "urllib3", "boto3"):
        logging.getLogger(noisy).setLevel(os.environ.get("AWS_SDK_LOG_LEVEL", "WARNING"))
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"s3vectors_rag.{name}")
