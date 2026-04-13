"""llama-swap config generator.

llama-swap (https://github.com/mostlygeek/llama-swap) is a thin proxy that
sits in front of multiple llama-server processes and routes OpenAI-compatible
requests to the right one based on the ``model`` field. It reads its config
from a YAML file and, with ``-watch-config``, picks up changes without a
restart.

This module is the bridge between Postgres (the source of truth for what
models exist) and that YAML file. Whenever the admin creates, updates, or
deletes an LLMModel — or whenever the seed runs on first startup — we
regenerate the YAML and write it atomically to ``settings.llm_swap_config_path``
(which lives inside the shared bind-mount, so the llm container sees it
instantly).

The shape of the YAML follows the upstream example::

    listen: ":8080"
    healthCheckTimeout: 60
    models:
      gemma-4-e4b:
        cmd: llama-server -m /var/lib/llm-models/gemma-4-e4b-it-Q4_K_M.gguf
                          --ctx-size 131072 --port 8180 --jinja
        proxy: http://localhost:8180
        checkEndpoint: /health
        ttl: 600

We deliberately keep this generator a pure function over the LLMModel rows
so it's trivial to unit-test without a Postgres or filesystem stand-in.
"""

from __future__ import annotations

import os
import shlex
import tempfile
from typing import Iterable

import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.llm_model import LLMModel


# Sentinels for the generated YAML. Tweaking these is fine — they only
# affect the *generated* file, never anything checked into the repo.
DEFAULT_TTL_SECONDS = 600  # unload an idle llama-server after 10 minutes
DEFAULT_HEALTH_TIMEOUT = 90  # how long to wait for /health on startup


def _build_model_entry(model: LLMModel, port: int) -> dict:
    """Render one (state, cmd, proxy) entry for a single LLMModel row.

    The cmd line is built with ``shlex.join`` so paths with spaces survive,
    even though we don't expect any. ``--jinja`` enables the Jinja chat
    template that ships in modern GGUF files (Gemma 4, Qwen 3.5, etc.).
    """
    cmd_parts: list[str] = [
        "llama-server",
        "-m",
        model.gguf_path,
        "--ctx-size",
        str(model.context_length),
        "--port",
        str(port),
        "--jinja",
    ]
    if model.mmproj_path:
        cmd_parts += ["--mmproj", model.mmproj_path]

    return {
        "cmd": shlex.join(cmd_parts),
        "proxy": f"http://localhost:{port}",
        "checkEndpoint": "/health",
        "ttl": DEFAULT_TTL_SECONDS,
    }


def build_swap_yaml(models: Iterable[LLMModel], base_port: int) -> str:
    """Pure function: list of LLMModel → YAML text.

    Stable ordering: sort by name so byte-for-byte regenerations don't
    appear "changed" if the database row order shifts. Inactive rows are
    skipped — admin must explicitly flip ``is_active=True`` for a model
    to be servable.
    """
    sorted_models = sorted(
        (m for m in models if m.is_active),
        key=lambda m: m.name,
    )

    config: dict = {
        "healthCheckTimeout": DEFAULT_HEALTH_TIMEOUT,
        "models": {},
    }

    for idx, model in enumerate(sorted_models):
        port = base_port + idx
        config["models"][model.name] = _build_model_entry(model, port)

    # `default_flow_style=False` for human-readable block syntax. The yaml
    # module sorts dict keys lexicographically by default, which is fine.
    return yaml.safe_dump(config, default_flow_style=False, sort_keys=True)


def write_atomically(path: str, content: str) -> None:
    """Write to <path>.tmp then os.replace(<path>.tmp, <path>).

    llama-swap's file watcher reacts to inotify rename events, so an atomic
    rename triggers exactly one reload. A naive write would briefly leave
    the file empty and could trigger a reload mid-write.
    """
    target_dir = os.path.dirname(path) or "."
    os.makedirs(target_dir, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix=".llama-swap.", suffix=".yaml.tmp", dir=target_dir
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.replace(tmp_path, path)
    except Exception:
        # Best-effort cleanup of the tmp file if rename failed.
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


async def regenerate_swap_config(session: AsyncSession) -> None:
    """Read all LLMModel rows from `session` and rewrite the swap YAML.

    Called from CRUD endpoints in ``app/api/llm_models.py`` after every
    successful change, and from ``seed_initial_models`` after the seed
    runs. Idempotent — calling it twice in a row produces the same file.
    """
    result = await session.execute(select(LLMModel))
    models = list(result.scalars().all())
    yaml_text = build_swap_yaml(models, base_port=settings.llm_swap_base_port)
    write_atomically(settings.llm_swap_config_path, yaml_text)
