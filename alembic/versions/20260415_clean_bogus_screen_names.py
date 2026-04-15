"""Clean up screen rows whose `name` is actually the vision-LLM prompt.

An earlier version of the vision-naming pipeline shipped without output
validation. When the model echoed our own prompt back instead of
producing a short label, we wrote that echo into `screens.name`. The
live DB still carries rows like:

    "2. Вопрос: Посмотри на скриншот мобильного экрана и придумай дл…"

This rewrites any such row to `"Экран <short-hash>"` so graphs, lists,
and logs stop leaking prompt fragments at QA/dev users.

Detection uses the same banned-keyword set as explorer/llm_loop.py's
validator — if it would reject the string today, it gets rewritten here.
Case-insensitive; triggers on ANY single keyword. This is aggressive,
but a real screen has no business containing "скриншот" or "посмотри".

Revision ID: 20260415_cleansn
Revises: 20260415_title
Create Date: 2026-04-15
"""

from alembic import op

revision = "20260415_cleansn"
down_revision = "20260415_title"
branch_labels = None
depends_on = None


# Must stay in sync with explorer/llm_loop.py's `banned` tuple. If you add
# keywords there, add them here too — otherwise fresh bad rows written by
# old worker builds won't be caught on the next `alembic upgrade head`.
_BANNED_KEYWORDS = (
    "посмотри",
    "придумай",
    "вопрос:",
    "скриншот",
    "ответь",
    "название",
    "мобильного",
    "экрана:",
)


def upgrade() -> None:
    # Build one OR'd ILIKE predicate — a single UPDATE is cheaper than
    # one-per-keyword on large screens tables.
    or_clauses = " OR ".join([f"LOWER(name) LIKE '%{kw}%'" for kw in _BANNED_KEYWORDS])

    # Length/word-count thresholds mirror the validator too: legitimate
    # names are <= 40 chars and <= 6 words. This catches bogus rows that
    # slipped past the keyword filter (e.g. "Posm. skrin. mobil'n...").
    sql = f"""
        UPDATE screens
        SET name = 'Экран ' || substr(screen_id_hash, 1, 8)
        WHERE
            name IS NOT NULL
            AND (
                ({or_clauses})
                OR length(name) > 40
                OR (array_length(regexp_split_to_array(trim(name), '\\s+'), 1) > 6)
            )
    """
    op.execute(sql)


def downgrade() -> None:
    # No-op: we can't reconstruct the original bogus strings, and nobody
    # wants them back anyway. Keep the downgrade hook so alembic stays
    # linear but don't touch the data.
    pass
