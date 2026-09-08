"""Minimal MCP (Model Context Protocol) client over the stdio transport.

Speaks newline-delimited JSON-RPC 2.0 to a locally spawned server process,
exposing just enough of the protocol for tool use: `initialize`, `tools/list`
and `tools/call`. Everything is synchronous — the caller is already on a worker
thread — and every failure raises `MCPError` for the tool layer to turn into a
message the model can read.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time


class MCPError(RuntimeError):
    pass


PROTOCOL_VERSION = "2024-11-05"


class MCPClient:
    """One spawned MCP server process."""

    def __init__(
        self,
        name: str,
        command: str,
        args: list[str] | None = None,
        env: dict | None = None,
        timeout: float = 30.0,
    ):
        self.name = name
        self._command = command
        self._args = list(args or [])
        self._env = dict(env or {})
        self._timeout = timeout
        self._proc: subprocess.Popen | None = None
        self._next_id = 0
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self):
        if self.running:
            return
        env = dict(os.environ)
        env.update({str(k): str(v) for k, v in self._env.items()})
        try:
            self._proc = subprocess.Popen(
                [self._command, *self._args],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                env=env,
                text=True,
                encoding="utf-8",
                bufsize=1,
            )
        except OSError as exc:
            raise MCPError(f"cannot start MCP server '{self.name}': {exc}") from exc
        self._request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "PyQOA", "version": "0.2.0"},
            },
        )
        self._notify("notifications/initialized", {})

    def stop(self):
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            if proc.stdin:
                proc.stdin.close()
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    def _send(self, payload: dict):
        if not self.running or self._proc.stdin is None:
            raise MCPError(f"MCP server '{self.name}' is not running")
        try:
            self._proc.stdin.write(json.dumps(payload) + "\n")
            self._proc.stdin.flush()
        except (BrokenPipeError, ValueError) as exc:
            raise MCPError(f"MCP server '{self.name}' closed its input") from exc

    def _notify(self, method: str, params: dict):
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def _request(self, method: str, params: dict) -> dict:
        with self._lock:
            self._next_id += 1
            req_id = self._next_id
            self._send(
                {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
            )
            return self._read_response(req_id)

    def _read_response(self, req_id: int) -> dict:
        """Read lines until the reply with our id arrives (notifications skipped)."""
        deadline = time.monotonic() + self._timeout
        while True:
            if time.monotonic() > deadline:
                raise MCPError(f"MCP server '{self.name}' timed out on request")
            if not self.running:
                raise MCPError(f"MCP server '{self.name}' exited unexpectedly")
            line = self._proc.stdout.readline() if self._proc.stdout else ""
            if not line:
                raise MCPError(f"MCP server '{self.name}' closed its output")
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue  # servers sometimes log to stdout; ignore non-JSON
            if msg.get("id") != req_id:
                continue
            if "error" in msg:
                err = msg["error"] or {}
                raise MCPError(
                    f"{self.name}: {err.get('message', 'unknown error')}"
                )
            return msg.get("result") or {}

    def list_tools(self) -> list[dict]:
        result = self._request("tools/list", {})
        return list(result.get("tools") or [])

    def call_tool(self, tool_name: str, arguments: dict) -> str:
        result = self._request(
            "tools/call", {"name": tool_name, "arguments": arguments or {}}
        )
        return self._flatten_content(result)

    @staticmethod
    def _flatten_content(result: dict) -> str:
        """Reduce an MCP tool result to the plain text the model will see."""
        parts = []
        for item in result.get("content") or []:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "text":
                parts.append(item.get("text", ""))
            elif item.get("type") == "resource":
                res = item.get("resource") or {}
                parts.append(res.get("text") or res.get("uri") or "")
            else:
                parts.append(f"[{item.get('type', 'content')}]")
        text = "\n".join(p for p in parts if p)
        if result.get("isError"):
            return f"Error: {text or 'tool reported an error'}"
        return text or "(no output)"
