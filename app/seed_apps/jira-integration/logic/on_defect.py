"""Jira integration — defect.created handler.

This file documents the behavior; actual execution happens server-side
in app/services/app_builtins.py::jira_create_issue because we don't yet
sandbox arbitrary Python.
"""


def handle(event: str, payload: dict) -> None:
    """Called via the built-in dispatcher when a defect is created."""
    print(f"[jira-integration] {event}: {payload.get('title')}")
