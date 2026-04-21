"""AlfaGen integration handler stub. Actual logic is in
app/services/app_builtins.py::alfagen_send_defect."""


def handle(event: str, payload: dict) -> None:
    print(f"[alfagen-integration] {event}: {payload.get('title')}")
