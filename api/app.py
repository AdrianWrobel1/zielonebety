"""
Zielone Bety REST API Application Entry Point
"""

from typing import Optional
from api.routes import APIRouter
from api.services import PlatformAPIService


def create_api_app(service: Optional[PlatformAPIService] = None) -> APIRouter:
    """Factory creating configured APIRouter instance."""
    app_service = service or PlatformAPIService()
    return APIRouter(service=app_service)
