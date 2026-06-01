"""
FastAPI dependency providers for service layer injection.
"""
from __future__ import annotations

from src.graph.store import get_client
from src.graph.repositories.intent_repository import IntentRepository
from src.graph.repositories.intent_report_repository import IntentReportRepository
from src.graph.repositories.intent_spec_repository import IntentSpecRepository
from src.graph.repositories.hub_repository import HubRepository
from src.services.intent_service import IntentService
from src.services.intent_report_service import IntentReportService
from src.services.intent_spec_service import IntentSpecService


def get_intent_service() -> IntentService:
    client = get_client()
    return IntentService(
        IntentRepository(client),
        HubRepository(client),
        IntentReportRepository(client),
    )


def get_intent_report_service() -> IntentReportService:
    client = get_client()
    return IntentReportService(IntentReportRepository(client), HubRepository(client))


def get_intent_spec_service() -> IntentSpecService:
    client = get_client()
    return IntentSpecService(IntentSpecRepository(client), HubRepository(client))


def get_hub_repo() -> HubRepository:
    client = get_client()
    return HubRepository(client)
