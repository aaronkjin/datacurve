"""Entrypoint for FastAPI ingestion service"""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from api.errors import register_error_handlers
from api.routes import traces, blobs
from db.session import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(
    title="Datacurve Ingestion API",
    version="0.1.0",
    description="High-fidelity coding telemetry pipeline for AI training data",
    lifespan=lifespan,
)

register_error_handlers(app)
app.include_router(traces.router)
app.include_router(blobs.router)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
