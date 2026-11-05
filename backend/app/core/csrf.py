"""Reject cookie-authenticated mutating requests without X-Requested-With."""

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.auth_cookies import ACCESS_COOKIE, REFRESH_COOKIE

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})
CSRF_HEADER = "x-requested-with"
# Allow account switch / first login even if stale cookies are present.
PUBLIC_MUTATIONS = frozenset(
    {
        "/api/v1/auth/register",
        "/api/v1/auth/login",
        "/api/v1/auth/google",
    }
)


class CsrfCookieMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        if request.method in SAFE_METHODS:
            return await call_next(request)

        if request.url.path in PUBLIC_MUTATIONS:
            return await call_next(request)

        auth = request.headers.get("authorization") or ""
        if auth.lower().startswith("bearer "):
            return await call_next(request)

        has_session_cookie = bool(
            request.cookies.get(ACCESS_COOKIE) or request.cookies.get(REFRESH_COOKIE)
        )
        if not has_session_cookie:
            return await call_next(request)

        if not request.headers.get(CSRF_HEADER):
            return JSONResponse(
                status_code=403,
                content={"detail": "Missing X-Requested-With header"},
            )
        return await call_next(request)
