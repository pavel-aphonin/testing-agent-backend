"""Scenario steps_json schema, version 2 (graph model).

Scenarios used to be a flat list of steps:

    {"steps": [{"screen_name": "...", "action": "tap", ...}, ...]}

PER-79 expands them into a directed graph with explicit nodes and
edges so the worker can support branching, loops, decision points,
explicit screen-checks, etc. The on-disk JSON shape:

    {
      "version": 2,
      "nodes": [
        {"id": "start", "type": "start", "position": {"x": 0, "y": 0}},
        {"id": "n1", "type": "action",
         "data": {"action": "tap", "element_label": "...", ...},
         "position": {"x": 100, "y": 100}},
        {"id": "end", "type": "end", "position": {...}}
      ],
      "edges": [
        {"id": "e1", "source": "start", "target": "n1"},
        {"id": "e2", "source": "n1", "target": "end"}
      ]
    }

Backward compatibility: ``normalize()`` accepts either v1 (legacy
``{"steps": [...]}``) or v2 and always returns a valid v2 graph.
Callers that read the DB should run everything through ``normalize()``
once. The frontend only ever sees v2.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ---------------------------------------------------------------- nodes


NodeType = Literal[
    "start",
    "end",
    "action",
    "decision",
    "wait",
    "screen_check",
    "loop_back",
    "sub_scenario",
    "group",
    # PER-110: high-level "цель" node — natural-language instruction
    # ("Авторизуйся с этими данными", "Переведи 100 на счёт X"). The
    # worker runs a mini LLM-loop on this node: look at the screen,
    # decide one action, execute, repeat until the LLM reports the
    # goal as done or ``max_steps`` is exhausted.
    #
    # data fields:
    #   description: str           — what the agent must accomplish
    #   expected_outcome: str?     — optional screen-state to verify
    #                                 once the LLM declares done
    #   max_steps: int = 15        — safety cap on the inner loop
    "goal",
]


class NodePosition(BaseModel):
    model_config = ConfigDict(extra="ignore")
    x: float = 0.0
    y: float = 0.0


class ScenarioNode(BaseModel):
    """A vertex in the scenario graph.

    The ``data`` payload is intentionally loose — its shape depends on
    ``type``. We don't strictly validate it here because the worker is
    the source of truth for execution semantics, and the schema would
    just lag the worker's needs. The frontend cooperates by sending
    well-formed payloads for each type.
    """

    model_config = ConfigDict(extra="ignore")

    id: str = Field(min_length=1, max_length=64)
    type: NodeType
    position: NodePosition = Field(default_factory=NodePosition)
    # Type-specific payload; see the per-type docs for fields.
    #   action: {action, element_label, value?, expected_result?,
    #            screen_name?, screen_description?}
    #   decision: {label?}  (conditions live on outgoing edges)
    #   wait: {ms}
    #   screen_check: {screen_description}
    #   loop_back: {max_iterations}
    #   sub_scenario: {linked_scenario_id, linked_scenario_title?}
    #   group: {label?} — purely visual container
    #   goal: {description, expected_outcome?, max_steps?}
    data: dict[str, Any] = Field(default_factory=dict)
    # Group support — when set, the node renders inside the group with
    # this id. The worker ignores groups entirely; they're a layout
    # device. ``None`` means top-level.
    parentId: str | None = None
    # Optional explicit dimensions, mostly used by group bounding boxes
    # whose size the user picks. Regular shape nodes auto-size.
    width: float | None = None
    height: float | None = None


# ---------------------------------------------------------------- edges


class ScenarioEdge(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = Field(min_length=1, max_length=64)
    source: str = Field(min_length=1, max_length=64)
    target: str = Field(min_length=1, max_length=64)
    # Edge metadata; condition expressions land here in PER-83, loop
    # marker in PER-84.
    #   {condition?: str, label?: str, branch?: "true"|"false"|"default",
    #    loop?: bool}
    data: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------- graph


class ScenarioGraphV2(BaseModel):
    """Top-level v2 payload. Always serialized with ``version=2``."""

    model_config = ConfigDict(extra="ignore")

    version: Literal[2] = 2
    nodes: list[ScenarioNode] = Field(default_factory=list)
    edges: list[ScenarioEdge] = Field(default_factory=list)

    @field_validator("nodes")
    @classmethod
    def _at_most_one_start_end(cls, v: list[ScenarioNode]) -> list[ScenarioNode]:
        starts = sum(1 for n in v if n.type == "start")
        ends = sum(1 for n in v if n.type == "end")
        # ``0`` is allowed because an empty draft scenario is valid (we
        # don't want to block save while the user is mid-edit). But the
        # worker will refuse to run a graph that has no start or end —
        # that's enforced at execution time, not here.
        if starts > 1:
            raise ValueError("graph has more than one start node")
        if ends > 1:
            raise ValueError("graph has more than one end node")
        return v


# ---------------------------------------------------------------- normalize


def normalize(steps_json: dict | None) -> dict:
    """Return a valid v2 dict for any input.

    * ``None`` / empty / missing keys → empty graph (just start + end).
    * v1 (``{"steps": [...]}``) → linear graph start → s0 → s1 → ... → end.
      Step fields are passed through as ``data`` on action nodes; the
      legacy ``screen_name`` is preserved for the runner's old code path.
    * v2 (already has ``nodes`` + ``edges``) → validated and returned
      verbatim (with ``version`` filled in if missing).
    """
    raw = steps_json or {}

    # v2: explicit graph
    if isinstance(raw.get("nodes"), list) and isinstance(raw.get("edges"), list):
        return ScenarioGraphV2.model_validate({**raw, "version": 2}).model_dump()

    # v1: flat steps list (or completely empty)
    steps = raw.get("steps") if isinstance(raw.get("steps"), list) else []
    nodes: list[dict[str, Any]] = [
        {"id": "start", "type": "start", "position": {"x": 0, "y": 0}, "data": {}},
    ]
    edges: list[dict[str, Any]] = []

    prev_id = "start"
    for idx, step in enumerate(steps):
        node_id = f"n{idx}"
        nodes.append(
            {
                "id": node_id,
                "type": "action",
                "position": {"x": 0, "y": (idx + 1) * 120},
                "data": dict(step) if isinstance(step, dict) else {},
            }
        )
        edges.append(
            {
                "id": f"e_{prev_id}_{node_id}",
                "source": prev_id,
                "target": node_id,
                "data": {},
            }
        )
        prev_id = node_id

    end_id = "end"
    nodes.append(
        {
            "id": end_id,
            "type": "end",
            "position": {"x": 0, "y": (len(steps) + 1) * 120},
            "data": {},
        }
    )
    edges.append(
        {
            "id": f"e_{prev_id}_{end_id}",
            "source": prev_id,
            "target": end_id,
            "data": {},
        }
    )

    return ScenarioGraphV2.model_validate(
        {"version": 2, "nodes": nodes, "edges": edges}
    ).model_dump()


def is_v2(steps_json: dict | None) -> bool:
    """Sniff whether the payload is already in v2 shape.

    Used by callers that want to skip normalization on the hot path
    (e.g. the worker). Anything that has ``nodes`` and ``edges`` keys
    counts as v2.
    """
    if not isinstance(steps_json, dict):
        return False
    return (
        isinstance(steps_json.get("nodes"), list)
        and isinstance(steps_json.get("edges"), list)
    )
