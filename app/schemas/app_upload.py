"""Pydantic schemas for the app upload endpoint."""

from pydantic import BaseModel


class AppUploadResponse(BaseModel):
    """Returned after a successful .app.zip / .ipa / .apk upload."""

    upload_id: str
    bundle_id: str
    app_name: str
    platform: str  # "ios" | "android"
