"""Exception handlers for the FastAPI app"""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from core.validation import TraceValidationError


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(TraceValidationError)
    async def trace_validation_error_handler(
        request: Request, exc: TraceValidationError
    ) -> JSONResponse:
        return JSONResponse(status_code=400, content=exc.to_response_body())
