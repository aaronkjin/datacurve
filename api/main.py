"""FastAPI ingestion service — entrypoint."""

from fastapi import FastAPI

app = FastAPI(
    title="Datacurve Ingestion API",
    version="0.1.0",
    description="High-fidelity coding telemetry pipeline for AI training data",
)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


# Route modules will be registered here:
# from api.routes import traces, blobs
# app.include_router(traces.router)
# app.include_router(blobs.router)
