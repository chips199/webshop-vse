from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse


def register_problem_handlers(app: FastAPI) -> None:
    """Ersetzt FastAPIs Standard-Fehlerantworten durch RFC 7807 Problem Details.

    Damit liefern alle Fehler (bewusste HTTPException, Validierungsfehler,
    unerwartete Exceptions) dasselbe einheitliche JSON-Format
    (media type application/problem+json) statt drei unterschiedlicher
    FastAPI-Default-Formate. Wird einmal beim App-Start in main.py
    aufgerufen.
    """

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
        # Faengt alle bewusst geworfenen HTTPException (404, 409, ...) ab.
        title = exc.detail if isinstance(exc.detail, str) else "HTTP error"
        return _problem_response(request, exc.status_code, title, exc.detail)

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Faengt Pydantic-Validierungsfehler ab (falsches Request-Body-Format).
        return _problem_response(request, 422, "Request validation failed", exc.errors())

    @app.exception_handler(Exception)
    async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        # Auffangnetz fuer alles Unerwartete (z.B. eine nicht abgefangene
        # PaymentFacadeError) - liefert bewusst keine internen Details nach
        # aussen, nur einen generischen 500er.
        return _problem_response(request, 500, "Internal Server Error", "An unexpected server error occurred.")


def _problem_response(request: Request, status_code: int, title: str, detail) -> JSONResponse:
    """Baut die eigentliche RFC-7807-JSON-Antwort (von allen Handlern oben genutzt)."""
    return JSONResponse(
        status_code=status_code,
        media_type="application/problem+json",
        content={
            "type": f"https://httpstatuses.com/{status_code}",
            "title": title,
            "status": status_code,
            "detail": jsonable_encoder(detail),
            "instance": str(request.url.path),
            # Aus dem correlation_id_middleware in main.py - erlaubt es, einen
            # Fehler im Log anhand derselben correlationId wiederzufinden.
            "correlationId": getattr(request.state, "correlation_id", None),
        },
    )
