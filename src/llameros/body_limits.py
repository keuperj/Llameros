"""Bound actual ASGI body bytes before framework JSON parsing."""

import asyncio
import json

from starlette.responses import JSONResponse


class BodyLimits:
    def __init__(self, app, inference_bytes, timeout=30):
        self.app, self.inference_bytes, self.timeout = app, inference_bytes, timeout

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope["path"]
        limit = (
            self.inference_bytes
            if path == "/inference" or path.startswith("/inference/")
            else 131072
        )
        raw = bytearray()
        try:
            lengths = [v for k, v in scope["headers"] if k.lower() == b"content-length"]
            if lengths and (len(lengths) != 1 or not lengths[0].isdigit()):
                raise ValueError("Invalid content length")
            if lengths and int(lengths[0]) > limit:
                return await JSONResponse({"detail": "Request too large"}, 413)(
                    scope, receive, send
                )
            async with asyncio.timeout(self.timeout):
                while True:
                    message = await receive()
                    if message["type"] == "http.disconnect":
                        return
                    chunk = message.get("body", b"")
                    if len(raw) + len(chunk) > limit:
                        return await JSONResponse({"detail": "Request too large"}, 413)(
                            scope, receive, send
                        )
                    raw.extend(chunk)
                    if not message.get("more_body", False):
                        break
            # A lexical depth check avoids recursive parser/validator failures.
            depth, quoted, escaped = 0, False, False
            for byte in raw:
                if quoted:
                    if escaped:
                        escaped = False
                    elif byte == 92:
                        escaped = True
                    elif byte == 34:
                        quoted = False
                elif byte == 34:
                    quoted = True
                elif byte in (91, 123):
                    depth += 1
                    if depth > 64:
                        raise ValueError("JSON nesting too deep")
                elif byte in (93, 125):
                    depth -= 1
            if raw:
                json.loads(raw)
        except TimeoutError:
            return await JSONResponse({"detail": "Request body timed out"}, 408)(
                scope, receive, send
            )
        except (ValueError, RecursionError):
            return await JSONResponse({"detail": "Invalid or excessively nested JSON body"}, 400)(
                scope, receive, send
            )

        delivered = False

        async def replay():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": bytes(raw), "more_body": False}
            return await receive()

        await self.app(scope, replay, send)
