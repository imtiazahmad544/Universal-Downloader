"""FastAPI application factory for the Universal Downloader API."""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api import health
from app.api.v1.router import router as v1_router
from app.core.database import init_db
from app.services.state_machine import InvalidTransitionError


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="Universal Downloader API", version="1.1.0", lifespan=lifespan)

    @app.exception_handler(InvalidTransitionError)
    async def invalid_transition_handler(
        _request: Request, exc: InvalidTransitionError
    ) -> JSONResponse:
        # InvalidTransitionError subclasses ValueError; this narrow handler
        # only maps our domain error to 409. All other ValueErrors (and any
        # unexpected error) keep FastAPI's default behavior.
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    app.include_router(health.router)
    app.include_router(v1_router)
    return app
