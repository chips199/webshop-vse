from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse


def register_problem_handlers(app: FastAPI) -> None:
    """Registriert einheitliche Fehlerantworten nach RFC 7807."""

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
        title = exc.detail if isinstance(exc.detail, str) else "HTTP error"
        return _problem_response(request, exc.status_code, title, exc.detail)

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        return _problem_response(request, 422, "Request validation failed", exc.errors())

    @app.exception_handler(Exception)
    async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        # Interne Details werden nicht an Clients ausgegeben.
        return _problem_response(request, 500, "Internal Server Error", "An unexpected server error occurred.")


def _problem_response(request: Request, status_code: int, title: str, detail) -> JSONResponse:
    """Erzeugt eine RFC-7807-Antwort."""
    return JSONResponse(
        status_code=status_code,
        media_type="application/problem+json",
        content={
            "type": f"https://httpstatuses.com/{status_code}",
            "title": title,
            "status": status_code,
            "detail": jsonable_encoder(detail),
            "instance": str(request.url.path),
            "correlationId": getattr(request.state, "correlation_id", None),
        },
    )
