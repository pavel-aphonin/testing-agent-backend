"""Seed script: on first startup, create the initial admin if no users exist."""

from fastapi_users.exceptions import UserAlreadyExists
from fastapi_users_db_sqlalchemy import SQLAlchemyUserDatabase
from sqlalchemy import select

from app.auth.users import UserManager
from app.config import settings
from app.db import async_session_maker
from app.models.user import User, UserRole
from app.schemas.user import UserCreate


async def seed_initial_admin() -> None:
    """Create the first admin if the users table is empty."""
    async with async_session_maker() as session:
        result = await session.execute(select(User).limit(1))
        if result.scalar_one_or_none() is not None:
            print("[seed] Users already exist, skipping admin seed.")
            return

    async with async_session_maker() as session:
        user_db = SQLAlchemyUserDatabase(session, User)
        user_manager = UserManager(user_db)

        try:
            user = await user_manager.create(
                UserCreate(
                    email=settings.initial_admin_email,
                    password=settings.initial_admin_password,
                    is_superuser=True,
                    is_verified=True,
                    role=UserRole.ADMIN.value,
                    must_change_password=False,
                )
            )
            print(f"[seed] Created initial admin: {user.email}")
        except UserAlreadyExists:
            print(f"[seed] Admin {settings.initial_admin_email} already exists.")
