"""Every error is JSON: {"error": <code>, "detail": ...}. Never a stack trace."""
import logging

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("loyalty.api")


class ApiError(Exception):
    """A business error. Raising it inside a transaction rolls the transaction back."""

    def __init__(self, status: int, error: str, detail):
        super().__init__(detail)
        self.status, self.error, self.detail = status, error, detail


def not_found(what: str, key: str) -> ApiError:
    return ApiError(404, "NOT_FOUND", f"{what} {key} not found")


def install_handlers(app: FastAPI):
    @app.exception_handler(ApiError)
    async def api_error(_: Request, exc: ApiError):
        return JSONResponse(status_code=exc.status, content={"error": exc.error, "detail": exc.detail})

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_: Request, exc: RequestValidationError):
        errors = [{"field": ".".join(str(p) for p in e["loc"]), "message": e["msg"]} for e in exc.errors()]
        return JSONResponse(status_code=400, content={"error": "INVALID_REQUEST", "detail": jsonable_encoder(errors)})

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_: Request, exc: StarletteHTTPException):
        code = "NOT_FOUND" if exc.status_code == 404 else "HTTP_ERROR"
        return JSONResponse(status_code=exc.status_code, content={"error": code, "detail": exc.detail})

    @app.exception_handler(Exception)
    async def unexpected(_: Request, exc: Exception):
        log.exception("unhandled error", exc_info=exc)
        return JSONResponse(status_code=500, content={"error": "INTERNAL_ERROR", "detail": "internal error"})
