"""PER-183: cross-cutting observability glue.

Currently houses Sentry init. Logging setup lives in
``app/logging_config.py`` because it has to run before any other
import; once that grows additional concerns it can move here too.
"""

from app.observability.sentry import init_sentry

__all__ = ["init_sentry"]
