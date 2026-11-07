"""Liveness and dependency readiness endpoints for cloud orchestration."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from shilun.common.config import load_config
from shilun.common.db import get_mongo_client


router = APIRouter(tags=["operations"])


@router.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz")
def readyz() -> dict[str, str]:
    config = load_config()
    if not config.mongo_uri:
        raise HTTPException(status_code=503, detail="database_not_configured")
    client = get_mongo_client(config.mongo_uri)
    try:
        client.admin.command("ping")
    except Exception as error:
        raise HTTPException(status_code=503, detail="database_unavailable") from error
    finally:
        client.close()
    return {"status": "ready"}


__all__ = ["router"]
