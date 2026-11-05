"""Keep Docker-internal HTTP off the corporate proxy.

Host shells often export no_proxy=localhost,127.0.0.1,10.0.0.0/8 without
`qdrant` / `slither`. Compose interpolates that into the container, then
httpx sends Qdrant traffic to Squid → HTML 503.
"""

from __future__ import annotations

import os

INTERNAL_HOSTS = (
    "localhost",
    "127.0.0.1",
    "postgres",
    "redis",
    "qdrant",
    "slither",
    "backend",
    "celery-worker",
)


def apply_internal_no_proxy() -> None:
    extra = list(INTERNAL_HOSTS)
    for key in ("NO_PROXY", "no_proxy"):
        current = os.environ.get(key, "")
        parts: list[str] = []
        seen: set[str] = set()
        for item in [*current.split(","), *extra]:
            host = item.strip()
            if not host or host in seen:
                continue
            seen.add(host)
            parts.append(host)
        os.environ[key] = ",".join(parts)
