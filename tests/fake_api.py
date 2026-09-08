"""A tiny OpenAI-compatible HTTP server used to drive StreamWorker end to end.

Each request pops the next scripted response, so a test can say "fail with 503,
then stream these chunks" and assert on what the worker did.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def _chunk(delta: dict, model: str = "test-model") -> str:
    payload = {
        "id": "chatcmpl-test",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
    }
    return f"data: {json.dumps(payload)}\n\n"


def _usage_chunk(usage: dict, model: str = "test-model") -> str:
    payload = {
        "id": "chatcmpl-test",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": model,
        "choices": [],
        "usage": usage,
    }
    return f"data: {json.dumps(payload)}\n\n"


class FakeAPI:
    """Scripted OpenAI-compatible endpoint running on a background thread."""

    def __init__(self, script: list[dict]):
        self.script = list(script)
        self.requests: list[dict] = []
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._make_handler())
        self._server.daemon_threads = True
        self._thread = threading.Thread(
            target=self._server.serve_forever, daemon=True
        )

    @property
    def base_url(self) -> str:
        host, port = self._server.server_address
        return f"http://{host}:{port}/v1"

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *_):
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)

    def _next(self) -> dict:
        return self.script.pop(0) if self.script else {"type": "json", "content": ""}

    def _make_handler(self):
        api = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_args):
                pass  # keep the test output clean

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length) or b"{}")
                api.requests.append({"body": body, "headers": dict(self.headers)})
                step = api._next()

                if step["type"] == "status":
                    self._send_json(
                        step.get("code", 500),
                        {"error": {"message": step.get("message", "server error")}},
                    )
                    return
                if step["type"] == "reject_stream_options":
                    if "stream_options" in body:
                        # Put the step back so the client's retry (without the
                        # unsupported argument) reaches the `then` response.
                        api.script.insert(0, step)
                        self._send_json(400, {"error": {
                            "message": "Unrecognized request argument supplied: "
                                       "stream_options"
                        }})
                        return
                    step = step["then"]
                if step["type"] == "json":
                    self._send_json(200, {
                        "id": "chatcmpl-test",
                        "object": "chat.completion",
                        "created": 0,
                        "model": body.get("model", "test-model"),
                        "choices": [{
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": step.get("content", ""),
                                "tool_calls": step.get("tool_calls"),
                            },
                            "finish_reason": "stop",
                        }],
                        "usage": step.get("usage"),
                    })
                    return
                self._send_stream(step)

            def _send_json(self, code: int, payload: dict):
                raw = json.dumps(payload).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def _send_stream(self, step: dict):
                parts = []
                for text in step.get("chunks", []):
                    parts.append(_chunk({"content": text}))
                for call in step.get("tool_calls", []):
                    parts.append(_chunk({"tool_calls": [call]}))
                if step.get("usage"):
                    parts.append(_usage_chunk(step["usage"]))
                parts.append("data: [DONE]\n\n")
                raw = "".join(parts).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        return Handler
