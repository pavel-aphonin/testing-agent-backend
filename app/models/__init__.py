"""SQLAlchemy ORM models. Importing this module registers all tables on Base.metadata."""

from app.models.agent_settings import AgentSettings
from app.models.app_package import (
    AppInstallation,
    AppPackage,
    AppPackageVersion,
    AppReview,
)
from app.models.attribute import Attribute, AttributeValue
from app.models.custom_dictionary import (
    CustomDictionary,
    CustomDictionaryItem,
    CustomDictionaryPermission,
)
from app.models.defect import DefectKind, DefectModel, DefectPriority
from app.models.device_config import DeviceConfig
from app.models.knowledge import EMBEDDING_DIM, KnowledgeChunk, KnowledgeDocument
from app.models.llm_model import LLMModel
from app.models.notification import Notification, WorkspaceInvitation
from app.models.notification_type import NotificationType, WorkspaceNotificationSetting
from app.models.reference import (
    RefActionType,
    RefDeviceType,
    RefOsVersion,
    RefPlatform,
    RefTestDataType,
    WorkspaceActionSetting,
)
from app.models.user_table_pref import UserTablePref
from app.models.role import Role
from app.models.run import Edge, Run, RunMode, RunStatus, Screen
from app.models.scenario import Scenario
from app.models.test_data import TestData
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceMember, WsRole

__all__ = [
    "AgentSettings",
    "AppInstallation",
    "AppPackage",
    "AppPackageVersion",
    "AppReview",
    "Attribute",
    "AttributeValue",
    "CustomDictionary",
    "CustomDictionaryItem",
    "CustomDictionaryPermission",
    "DefectKind",
    "DefectModel",
    "DefectPriority",
    "DeviceConfig",
    "EMBEDDING_DIM",
    "Edge",
    "KnowledgeChunk",
    "KnowledgeDocument",
    "LLMModel",
    "Notification",
    "NotificationType",
    "Role",
    "WorkspaceNotificationSetting",
    "Run",
    "RunMode",
    "RunStatus",
    "Scenario",
    "Screen",
    "TestData",
    "User",
    "Workspace",
    "WorkspaceInvitation",
    "WorkspaceMember",
    "WsRole",
]
