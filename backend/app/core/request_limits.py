"""Request size limits applied before FastAPI parses multipart bodies."""

from starlette.responses import JSONResponse

from app.core.upload_limits import MAX_UPLOAD_REQUEST_BYTES

UPLOAD_PATH = "/api/v1/contracts/analyze/upload"


class UploadRequestSizeLimitMiddleware:
    """Bound upload request bodies before form parsing can spool them."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if (
            scope["type"] != "http"
            or scope.get("method") != "POST"
            or scope.get("path") != UPLOAD_PATH
        ):
            await self.app(scope, receive, send)
            return

        for name, value in scope.get("headers", []):
            if name.lower() != b"content-length" or not value.isdigit():
                continue
            # Compare normalized digits so arbitrarily long Content-Length
            # values do not need to be converted to Python integers.
            declared_size = value.lstrip(b"0") or b"0"
            limit = str(MAX_UPLOAD_REQUEST_BYTES).encode("ascii")
            if len(declared_size) > len(limit) or (
                len(declared_size) == len(limit) and declared_size > limit
            ):
                await _request_too_large(scope, receive, send)
                return

        received_bytes = 0
        overflowed = False

        async def limited_receive():
            nonlocal received_bytes, overflowed
            if overflowed:
                return {"type": "http.request", "body": b"", "more_body": False}

            message = await receive()
            if message["type"] == "http.request":
                received_bytes += len(message.get("body", b""))
                if received_bytes > MAX_UPLOAD_REQUEST_BYTES:
                    overflowed = True
                    return {"type": "http.request", "body": b"", "more_body": False}
            return message

        async def limited_send(message):
            # Multipart parsing may convert premature EOF into a 400 response.
            # Suppress that response so the outer limit can return the intended 413.
            if not overflowed:
                await send(message)

        try:
            await self.app(scope, limited_receive, limited_send)
        except Exception:
            if not overflowed:
                raise

        if overflowed:
            await _request_too_large(scope, receive, send)


async def _request_too_large(scope, receive, send) -> None:
    response = JSONResponse(
        {"detail": "Upload request exceeds the 10 MiB file limit plus multipart overhead"},
        status_code=413,
    )
    await response(scope, receive, send)
