"""System branding — product name + logo customization.

Singleton table: exactly one row, lazily created on first read with
defaults. The row exists so updates can be SQL ``UPDATE`` rather than
upsert-gymnastics, and so the updated_at/by columns work cleanly.

Defaults (empty row) mean "use the built-in Markov branding": the
animated flip-logo component on the frontend plus the string «Марков».
Any field left NULL in the row also falls back to the default for that
field — so an admin can change just the product name without having to
upload a logo, or vice versa.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


# Well-known id for the singleton row. Generated once, hardcoded here —
# simpler than a dedicated "is the singleton" column or a trigger.
BRANDING_SINGLETON_ID = uuid.UUID("00000000-0000-0000-0000-0000000b4a47")


class SystemBranding(Base):
    __tablename__ = "system_branding"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=BRANDING_SINGLETON_ID,
    )

    # Full product name shown in the sidebar header and <title>.
    # When NULL — frontend renders «Марков».
    product_name: Mapped[str | None] = mapped_column(String(80), nullable=True)

    # Shorter variant for tight spots (browser tab, favicon tooltips).
    # When NULL — frontend reuses ``product_name`` or «Марков».
    short_name: Mapped[str | None] = mapped_column(String(40), nullable=True)

    # Path under ``app_uploads_dir`` to a raster / SVG logo. When NULL,
    # the frontend uses the built-in animated flip logo. 120×120 is a
    # sensible ceiling — the component renders at 32–64px.
    logo_path: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Optional second image. If provided alongside ``logo_path``, the
    # frontend animates a 3D flip between the two, matching the
    # Markov/Alfa style. Keeps the product feeling alive for companies
    # that want to show their own brand+accent mark.
    logo_back_path: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Browser tab icon. Separate from the logo on purpose — favicons
    # are typically square raster / ICO with tight pixel budgets, while
    # the sidebar logo is happy with any aspect and likely SVG. When
    # NULL the frontend falls back to the default Vite favicon.
    favicon_path: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Full set of theme tokens — shape mirrors Ant Design's ``theme.token``
    # so the frontend can feed it straight into ``ConfigProvider``. Stored
    # as one JSONB blob because the set grows over time (more colors,
    # border radius, font family...) and a JSON column doesn't require a
    # migration per field.
    #
    # Canonical shape (all keys optional):
    #   {
    #     "light": {
    #       "colorPrimary": "#EE3424", "colorSuccess": "#52c41a",
    #       "colorWarning": "#faad14", "colorError":   "#ff4d4f",
    #       "colorInfo":    "#1677ff", "colorLink":    null
    #     },
    #     "dark":  { ... same keys ... },
    #     "borderRadius": 6,
    #     "fontFamily":   null
    #   }
    #
    # Missing keys → frontend falls back to Markov defaults.
    theme_tokens: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
