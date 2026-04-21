"""Server-side handler for the 'defect.created' event.

Phase 2 invokes webhook_url from settings. This Python handler file is
kept as a reference for future in-process execution (Phase 3+) when we
lock down sandboxing. Right now the Markov backend does NOT import this
file automatically — it's part of the bundle for readability.
"""


def handle(event: str, payload: dict) -> None:
    """Example handler. Would be called by the host if in-process
    execution were enabled."""
    print(f"[hello-world] received {event}: {payload.get('title')}")
