"""FinV API application (modular monolith)."""
from __future__ import annotations

import logging

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .api import accounting, assistant, audit, auth, companies, firm, gst
from .db import init_engine
from .security import require_csrf_header

log = logging.getLogger("finv")


def create_app(database_url: str | None = None, create_tables: bool = False) -> FastAPI:
    engine = init_engine(database_url)
    if create_tables:
        from .db import Base
        from . import models  # noqa: F401
        Base.metadata.create_all(engine)

    app = FastAPI(title="FinV API", version="0.1.0", dependencies=[Depends(require_csrf_header)],
                  docs_url="/api/docs", openapi_url="/api/openapi.json", redoc_url=None)
    for r in (auth.router, companies.router, accounting.router, audit.router, assistant.router, gst.router, gst.links, firm.router):
        app.include_router(r)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        resp = await call_next(request)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        if request.url.path.startswith("/api/") and "Cache-Control" not in resp.headers:
            resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError):
        errs = [{"field": ".".join(str(p) for p in e["loc"][1:]), "message": e["msg"]} for e in exc.errors()]
        return JSONResponse(status_code=422, content={"detail": "Some fields are missing or invalid.", "errors": errs})

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        # Never leak stack traces, SQL or provider responses to users; log the type only (no record data).
        log.error("Unhandled error on %s %s: %s", request.method, request.url.path, type(exc).__name__)
        return JSONResponse(status_code=500, content={"detail": "Something went wrong on our side. Nothing was changed. Try again."})

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    return app

