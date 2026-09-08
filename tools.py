"""Tool calling: a registry of built-in tools plus tools exposed by MCP servers.

The registry produces OpenAI-style tool specs and executes calls by name. Tools
are opt-in (`tools_enabled`) and each built-in is individually switchable, since
one of them reaches the network.
"""

from __future__ import annotations

import ast
import html
import json
import math
import operator
import re
import urllib.request
from datetime import datetime, timezone

from mcp_client import MCPClient, MCPError

MCP_PREFIX = "mcp"
_NAME_SAFE = re.compile(r"[^a-zA-Z0-9_-]")

FETCH_MAX_BYTES = 200_000
FETCH_TIMEOUT = 15.0


_BIN_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod, ast.Pow: operator.pow,
}
_UNARY_OPS = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_FUNCS = {
    name: getattr(math, name)
    for name in (
        "sqrt", "log", "log2", "log10", "exp", "sin", "cos", "tan",
        "asin", "acos", "atan", "atan2", "floor", "ceil", "hypot", "fabs",
    )
}
_FUNCS.update({"abs": abs, "round": round, "min": min, "max": max})
_CONSTS = {"pi": math.pi, "e": math.e, "tau": math.tau}


def safe_eval(expr: str) -> float:
    """Evaluate an arithmetic expression without exposing the Python runtime."""
    tree = ast.parse(expr, mode="eval")

    def visit(node):
        if isinstance(node, ast.Expression):
            return visit(node.body)
        if isinstance(node, ast.Constant):
            if isinstance(node.value, (int, float)):
                return node.value
            raise ValueError("only numeric literals are allowed")
        if isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS:
            return _BIN_OPS[type(node.op)](visit(node.left), visit(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPS:
            return _UNARY_OPS[type(node.op)](visit(node.operand))
        if isinstance(node, ast.Name) and node.id in _CONSTS:
            return _CONSTS[node.id]
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in _FUNCS
            and not node.keywords
        ):
            return _FUNCS[node.func.id](*[visit(a) for a in node.args])
        raise ValueError(f"unsupported expression element: {type(node).__name__}")

    return visit(tree)


def _tool_current_time(_args: dict) -> str:
    now = datetime.now().astimezone()
    return json.dumps({
        "local": now.strftime("%Y-%m-%d %H:%M:%S %Z"),
        "utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ"),
    })


def _tool_calculate(args: dict) -> str:
    expr = str(args.get("expression", "")).strip()
    if not expr:
        return "Error: no expression given"
    try:
        return str(safe_eval(expr))
    except Exception as exc:
        return f"Error: {exc}"


_TAG_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.S | re.I)
_ANY_TAG_RE = re.compile(r"<[^>]+>")


def html_to_text(raw: str) -> str:
    text = _TAG_RE.sub(" ", raw)
    text = _ANY_TAG_RE.sub(" ", text)
    text = html.unescape(text)
    return re.sub(r"[ \t]*\n\s*", "\n", re.sub(r"[ \t]+", " ", text)).strip()


def _tool_fetch_url(args: dict) -> str:
    url = str(args.get("url", "")).strip()
    if not url.lower().startswith(("http://", "https://")):
        return "Error: only http(s) URLs are supported"
    req = urllib.request.Request(url, headers={"User-Agent": "PyQOA/0.2"})
    try:
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as resp:
            raw = resp.read(FETCH_MAX_BYTES).decode("utf-8", errors="replace")
            ctype = resp.headers.get("Content-Type", "")
    except Exception as exc:
        return f"Error: {exc}"
    if "html" in ctype.lower() or raw.lstrip().startswith("<"):
        raw = html_to_text(raw)
    return raw[:FETCH_MAX_BYTES]


BUILTINS: dict[str, dict] = {
    "current_time": {
        "run": _tool_current_time,
        "description": "Get the current local and UTC date and time.",
        "parameters": {"type": "object", "properties": {}},
        "network": False,
    },
    "calculate": {
        "run": _tool_calculate,
        "description": (
            "Evaluate an arithmetic expression. Supports + - * / // % **, "
            "parentheses, pi/e/tau and common math functions."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "description": "e.g. '(2+3)*sqrt(16)'",
                }
            },
            "required": ["expression"],
        },
        "network": False,
    },
    "fetch_url": {
        "run": _tool_fetch_url,
        "description": (
            "Fetch a public http(s) URL and return its text content "
            "(HTML is stripped to plain text)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Absolute http(s) URL"}
            },
            "required": ["url"],
        },
        "network": True,
    },
}


def mcp_tool_name(server: str, tool: str) -> str:
    """Namespaced, API-safe function name for a tool from an MCP server."""
    safe_server = _NAME_SAFE.sub("_", server)[:24]
    safe_tool = _NAME_SAFE.sub("_", tool)[:32]
    return f"{MCP_PREFIX}__{safe_server}__{safe_tool}"


class ToolRegistry:
    """Resolves the tool set for a request and dispatches calls by name."""

    def __init__(self, settings):
        self._settings = settings
        self._clients: dict[str, MCPClient] = {}
        self._mcp_tools: dict[str, tuple[str, str]] = {}  # api name -> (server, tool)
        self._specs: list[dict] = []
        self._loaded = False
        self.errors: list[str] = []

    @property
    def enabled(self) -> bool:
        return bool(self._settings.get("tools_enabled", False))

    def _builtin_names(self) -> list[str]:
        chosen = self._settings.get("tool_builtins")
        if chosen is None:
            chosen = list(BUILTINS)
        return [n for n in chosen if n in BUILTINS]

    def load(self):
        """Build the spec list (starting MCP servers on first use)."""
        if self._loaded:
            return
        self._loaded = True
        if not self.enabled:
            return
        for name in self._builtin_names():
            spec = BUILTINS[name]
            self._specs.append({
                "type": "function",
                "function": {
                    "name": name,
                    "description": spec["description"],
                    "parameters": spec["parameters"],
                },
            })
        for server in self._settings.get("mcp_servers") or []:
            if not server.get("enabled", True):
                continue
            self._load_mcp_server(server)

    def _load_mcp_server(self, server: dict):
        name = server.get("name") or "mcp"
        command = (server.get("command") or "").strip()
        if not command:
            return
        client = MCPClient(
            name, command, server.get("args") or [], server.get("env") or {}
        )
        try:
            client.start()
            tools = client.list_tools()
        except MCPError as exc:
            self.errors.append(str(exc))
            client.stop()
            return
        self._clients[name] = client
        for tool in tools:
            tool_name = tool.get("name") or ""
            if not tool_name:
                continue
            api_name = mcp_tool_name(name, tool_name)
            self._mcp_tools[api_name] = (name, tool_name)
            self._specs.append({
                "type": "function",
                "function": {
                    "name": api_name,
                    "description": (
                        tool.get("description") or f"{name} tool {tool_name}"
                    )[:1024],
                    "parameters": tool.get("inputSchema")
                    or {"type": "object", "properties": {}},
                },
            })

    def specs(self) -> list[dict]:
        self.load()
        return list(self._specs)

    def call(self, name: str, arguments: str | dict) -> str:
        """Run a tool. Never raises — the model sees errors as tool output."""
        self.load()
        if isinstance(arguments, str):
            try:
                args = json.loads(arguments) if arguments.strip() else {}
            except json.JSONDecodeError:
                return "Error: arguments were not valid JSON"
        else:
            args = arguments or {}
        if not isinstance(args, dict):
            return "Error: arguments must be a JSON object"
        if name in BUILTINS and name in self._builtin_names():
            try:
                return BUILTINS[name]["run"](args)
            except Exception as exc:
                return f"Error: {exc}"
        if name in self._mcp_tools:
            server, tool = self._mcp_tools[name]
            client = self._clients.get(server)
            if client is None:
                return f"Error: MCP server '{server}' is not available"
            try:
                return client.call_tool(tool, args)
            except MCPError as exc:
                return f"Error: {exc}"
        return f"Error: unknown tool '{name}'"

    def close(self):
        for client in self._clients.values():
            client.stop()
        self._clients.clear()
        self._mcp_tools.clear()
        self._specs.clear()
        self._loaded = False
