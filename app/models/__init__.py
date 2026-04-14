"""SQLAlchemy ORM models. Importing this module registers all tables on Base.metadata."""

from app.models.agent_settings import AgentSettings
from app.models.defect import DefectKind, DefectModel, DefectPriority
from app.models.device_config import DeviceConfig
from app.models.knowledge import EMBEDDING_DIM, KnowledgeChunk, KnowledgeDocument
from app.models.llm_model import LLMModel
from app.models.run import Edge, Run, RunMode, RunStatus, Screen
from app.models.scenario import Scenario
from app.models.test_data import TestData
from app.models.user import User, UserRole

__all__ = [
    "AgentSettings",
    "DefectKind",
    "DefectModel",
    "DefectPriority",
    "DeviceConfig",
    "EMBEDDING_DIM",
    "Edge",
    "KnowledgeChunk",
    "KnowledgeDocument",
    "LLMModel",
    "Run",
    "RunMode",
    "RunStatus",
    "Scenario",
    "Screen",
    "TestData",
    "User",
    "UserRole",
]
