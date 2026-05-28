from contextvars import ContextVar
from uuid import uuid4

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request


REQUEST_ID_HEADER = "X-Request-ID"
request_id_ctx_var: ContextVar[str] = ContextVar("request_id", default="-")


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get(REQUEST_ID_HEADER) or str(uuid4())
        token = request_id_ctx_var.set(request_id)
        try:
            response = await call_next(request)
        finally:
            request_id_ctx_var.reset(token)
        response.headers[REQUEST_ID_HEADER] = request_id
        return response
