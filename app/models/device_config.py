"""Admin-curated device configurations for exploration runs.

Administrators manage which device + OS version combinations are available
to testers when creating new runs. The worker reports all *physically*
available runtimes and device types on startup; the admin then picks
which ones to expose in the "New Run" dropdown.
"""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class DeviceConfig(Base):
    """A device + OS version combination approved by the admin."""

    __tablename__ = "device_configs"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )

    # "ios" or "android"
    platform: Mapped[str] = mapped_column(String(20), nullable=False)

    # Human-readable device name shown in dropdowns: "iPhone 17 Pro Max"
    device_type: Mapped[str] = mapped_column(String(200), nullable=False)

    # Machine identifier used by xcrun/avdmanager:
    # iOS:     "com.apple.CoreSimulator.SimDeviceType.iPhone-17-Pro-Max"
    # Android: "pixel_9_pro_xl"
    device_identifier: Mapped[str] = mapped_column(String(300), nullable=False)

    # Human-readable OS version: "iOS 26.2" / "Android 36"
    os_version: Mapped[str] = mapped_column(String(50), nullable=False)

    # Machine identifier:
    # iOS:     "com.apple.CoreSimulator.SimRuntime.iOS-26-2"
    # Android: "system-images;android-36;google_apis_playstore;arm64-v8a"
    os_identifier: Mapped[str] = mapped_column(String(300), nullable=False)

    # Only active configs appear in the tester's "New Run" dropdown.
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    created_by_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
