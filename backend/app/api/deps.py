"""Shared API dependencies: container, session, authentication, rate limits, error shape."""

from __future__ import annotations

import hmac
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.container import Container
from app.db.models import Instrument
from app.observability.metrics import RATE_LIMITED


class ApiError(HTTPException):
    def __init__(self, status: int, code: str, message: str, details: Any = None, headers: dict[str, str] | None = None) -> None:
        super().__init__(status_code=status, detail={"code": code, "message": message, "details": details}, headers=headers)


def get_container(request: Request) -> Container:
    return request.app.state.container  # type: ignore[no-any-return]


Ctx = Annotated[Container, Depends(get_container)]


async def get_session(c: Ctx) -> AsyncIterator[AsyncSession]:
    async with c.sessions() as s:
        yield s


Db = Annotated[AsyncSession, Depends(get_session)]


def get_actor(request: Request, c: Ctx) -> str:
    """Single-tenant bearer-token check. When API_AUTH_TOKEN is unset (local development) every
    caller is the local user; when set, every /api/v1 request must present it."""
    token = c.settings.api_auth_token
    if token is None or not token.get_secret_value():
        return "local"
    header = request.headers.get("authorization", "")
    supplied = header[7:] if header.lower().startswith("bearer ") else ""
    if not supplied or not hmac.compare_digest(supplied.encode(), token.get_secret_value().encode()):
        raise ApiError(401, "unauthorized", "A valid bearer token is required.", headers={"WWW-Authenticate": "Bearer"})
    return "local"


Actor = Annotated[str, Depends(get_actor)]


def rate_limit(bucket: str) -> Any:
    def dep(request: Request, c: Ctx, actor: Actor) -> None:
        limiter = c.limiters[bucket]
        key = f"{actor}:{request.client.host if request.client else 'unknown'}"
        ok, retry = limiter.allow(key)
        if not ok:
            RATE_LIMITED.labels(bucket=bucket).inc()
            raise ApiError(429, "rate_limited", f"Too many {bucket} requests. Try again in {int(retry) + 1} seconds.",
                           headers={"Retry-After": str(int(retry) + 1)})

    return Depends(dep)


async def resolve_instrument(s: AsyncSession, ref: str) -> Instrument:
    """Accepts a numeric id or EXCHANGE:SYMBOL. A bare symbol is accepted only when unambiguous."""
    ref = ref.strip()
    if ref.isdigit():
        inst = await s.get(Instrument, int(ref))
        if inst is None:
            raise ApiError(404, "instrument_not_found", f"No instrument with id {ref}.")
        return inst
    if len(ref) > 60:
        raise ApiError(422, "invalid_instrument", "Instrument reference is too long.")
    if ":" in ref:
        exch, sym = ref.split(":", 1)
        inst = await s.scalar(select(Instrument).where(Instrument.exchange == exch.upper(), Instrument.symbol == sym.upper()))
        if inst is None:
            raise ApiError(404, "instrument_not_found", f"{exch.upper()}:{sym.upper()} is not in the instrument master.")
        return inst
    matches = list((await s.scalars(select(Instrument).where(Instrument.symbol == ref.upper()))).all())
    if not matches:
        raise ApiError(404, "instrument_not_found", f"{ref.upper()} is not in the instrument master.")
    if len(matches) > 1:
        raise ApiError(409, "ambiguous_instrument", "Symbol exists on several exchanges; use EXCHANGE:SYMBOL.",
                       [f"{m.exchange}:{m.symbol}" for m in matches])
    return matches[0]


def meta(c: Container, **extra: Any) -> dict[str, Any]:
    """Envelope fields common to data responses."""
    return {"data_mode": c.settings.data_mode, "is_demo": c.settings.is_demo, "retrieved_at": c.clock.now().isoformat(), **extra}
