"""Small authentication boundary for self-hosted and cloud deployments."""

from __future__ import annotations

import base64
import secrets
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response


PUBLIC_PATHS = frozenset({"/healthz", "/readyz"})


def _credentials(authorization: str) -> tuple[str, str] | None:
    scheme, _, encoded = authorization.partition(" ")
    if scheme.lower() != "basic" or not encoded:
        return None
    try:
        decoded = base64.b64decode(encoded, validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None
    username, separator, password = decoded.partition(":")
    return (username, password) if separator else None


def install_basic_auth(app: FastAPI, config: Any) -> None:
    """Protect the entire workbench when cloud authentication is enabled.

    Basic Auth is intentionally the first-deployment boundary.  It must run
    behind HTTPS and can later be replaced by Alibaba Cloud SSO or an identity
    aware proxy without changing the review APIs.
    """

    if not bool(config.auth_required):
        return
    expected_username = str(config.auth_username or "")
    expected_password = str(config.auth_password or "")
    if not expected_username or not expected_password:
        raise RuntimeError(
            "SHILUN_AUTH_REQUIRED=true 时必须同时配置 "
            "SHILUN_AUTH_USERNAME 与 SHILUN_AUTH_PASSWORD"
        )

    @app.middleware("http")
    async def require_basic_auth(request: Request, call_next: Any) -> Response:
        if request.url.path in PUBLIC_PATHS:
            return await call_next(request)
        supplied = _credentials(request.headers.get("authorization", ""))
        valid = bool(
            supplied
            and secrets.compare_digest(supplied[0], expected_username)
            and secrets.compare_digest(supplied[1], expected_password)
        )
        if valid:
            return await call_next(request)
        return JSONResponse(
            status_code=401,
            content={"detail": "需要登录后访问 VC-Astock。"},
            headers={"WWW-Authenticate": 'Basic realm="VC-Astock", charset="UTF-8"'},
        )


__all__ = ["install_basic_auth"]
