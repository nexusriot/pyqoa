import json
import sys
import textwrap

import pytest

import tools
from mcp_client import MCPClient, MCPError
from tools import ToolRegistry, safe_eval


class _FakeSettings:
    def __init__(self, **values):
        self._values = values

    def get(self, key, default=None):
        return self._values.get(key, default)


def test_safe_eval_does_arithmetic_and_math_functions():
    assert safe_eval("(2+3)*4") == 20
    assert safe_eval("sqrt(16)") == 4
    assert safe_eval("2**10") == 1024
    assert round(safe_eval("pi"), 4) == 3.1416


def test_safe_eval_refuses_python_escapes():
    for expr in ("__import__('os')", "open('x')", "[1,2]", "'a'*3", "x + 1"):
        with pytest.raises(Exception):
            safe_eval(expr)


def test_calculate_tool_reports_errors_as_text():
    assert tools.BUILTINS["calculate"]["run"]({"expression": "1/0"}).startswith("Error")
    assert tools.BUILTINS["calculate"]["run"]({}).startswith("Error")


def test_current_time_returns_json():
    payload = json.loads(tools.BUILTINS["current_time"]["run"]({}))
    assert "local" in payload and "utc" in payload


def test_fetch_url_rejects_non_http_schemes():
    assert tools.BUILTINS["fetch_url"]["run"](
        {"url": "file:///etc/passwd"}
    ).startswith("Error")


def test_html_to_text_strips_markup_and_scripts():
    text = tools.html_to_text("<p>Hello <b>you</b></p><script>bad()</script>")
    assert "Hello" in text and "bad()" not in text and "<" not in text


def test_mcp_tool_names_are_api_safe():
    name = tools.mcp_tool_name("my server", "read/file")
    assert name == "mcp__my_server__read_file"


def test_registry_is_empty_while_tools_are_disabled():
    registry = ToolRegistry(_FakeSettings(tools_enabled=False))
    assert registry.specs() == []


def test_registry_exposes_only_the_enabled_builtins():
    registry = ToolRegistry(
        _FakeSettings(tools_enabled=True, tool_builtins=["calculate"])
    )
    names = [s["function"]["name"] for s in registry.specs()]
    assert names == ["calculate"]
    assert registry.call("calculate", '{"expression": "6*7"}') == "42"
    assert registry.call("current_time", "{}").startswith("Error: unknown tool")


def test_registry_reports_bad_arguments_instead_of_raising():
    registry = ToolRegistry(
        _FakeSettings(tools_enabled=True, tool_builtins=["calculate"])
    )
    assert registry.call("calculate", "{oops").startswith("Error")
    assert registry.call("calculate", "[1,2]").startswith("Error")


def test_registry_accepts_dict_arguments():
    registry = ToolRegistry(
        _FakeSettings(tools_enabled=True, tool_builtins=["calculate"])
    )
    assert registry.call("calculate", {"expression": "1+1"}) == "2"


FAKE_SERVER = textwrap.dedent(
    '''
    import json, sys

    def send(payload):
        sys.stdout.write(json.dumps(payload) + "\\n")
        sys.stdout.flush()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        msg = json.loads(line)
        method = msg.get("method")
        if method == "initialize":
            send({"jsonrpc": "2.0", "id": msg["id"],
                  "result": {"protocolVersion": "2024-11-05",
                             "serverInfo": {"name": "fake"}}})
        elif method == "notifications/initialized":
            send({"jsonrpc": "2.0", "method": "notifications/log",
                  "params": {"text": "ready"}})   # a notification to be skipped
        elif method == "tools/list":
            send({"jsonrpc": "2.0", "id": msg["id"], "result": {"tools": [
                {"name": "echo", "description": "Echo back",
                 "inputSchema": {"type": "object",
                                 "properties": {"text": {"type": "string"}}}},
                {"name": "boom", "description": "Always fails",
                 "inputSchema": {"type": "object"}},
            ]}})
        elif method == "tools/call":
            name = msg["params"]["name"]
            if name == "boom":
                send({"jsonrpc": "2.0", "id": msg["id"],
                      "error": {"code": -1, "message": "kaboom"}})
            else:
                text = msg["params"].get("arguments", {}).get("text", "")
                send({"jsonrpc": "2.0", "id": msg["id"], "result": {
                    "content": [{"type": "text", "text": f"echo:{text}"}]}})
        else:
            send({"jsonrpc": "2.0", "id": msg.get("id"),
                  "error": {"code": -32601, "message": "unknown"}})
    '''
)


@pytest.fixture
def fake_server(tmp_path):
    path = tmp_path / "fake_mcp.py"
    path.write_text(FAKE_SERVER)
    return path


def test_mcp_client_lists_and_calls_tools(fake_server):
    client = MCPClient("fake", sys.executable, [str(fake_server)])
    try:
        client.start()
        names = [t["name"] for t in client.list_tools()]
        assert names == ["echo", "boom"]
        assert client.call_tool("echo", {"text": "hi"}) == "echo:hi"
    finally:
        client.stop()
    assert not client.running


def test_mcp_client_surfaces_server_errors(fake_server):
    client = MCPClient("fake", sys.executable, [str(fake_server)])
    try:
        client.start()
        with pytest.raises(MCPError, match="kaboom"):
            client.call_tool("boom", {})
    finally:
        client.stop()


def test_mcp_client_reports_a_missing_command():
    client = MCPClient("nope", "/definitely/not/a/binary")
    with pytest.raises(MCPError):
        client.start()


def test_registry_namespaces_and_dispatches_mcp_tools(fake_server):
    registry = ToolRegistry(_FakeSettings(
        tools_enabled=True,
        tool_builtins=[],
        mcp_servers=[{"name": "fake", "command": sys.executable,
                      "args": [str(fake_server)], "enabled": True}],
    ))
    try:
        names = [s["function"]["name"] for s in registry.specs()]
        assert "mcp__fake__echo" in names
        assert registry.call("mcp__fake__echo", '{"text": "yo"}') == "echo:yo"
        assert registry.call("mcp__fake__boom", "{}").startswith("Error")
    finally:
        registry.close()


def test_registry_skips_disabled_servers(fake_server):
    registry = ToolRegistry(_FakeSettings(
        tools_enabled=True, tool_builtins=[],
        mcp_servers=[{"name": "fake", "command": sys.executable,
                      "args": [str(fake_server)], "enabled": False}],
    ))
    assert registry.specs() == []


def test_registry_records_an_unstartable_server_as_an_error():
    registry = ToolRegistry(_FakeSettings(
        tools_enabled=True, tool_builtins=[],
        mcp_servers=[{"name": "broken", "command": "/no/such/binary"}],
    ))
    assert registry.specs() == []
    assert registry.errors and "broken" in registry.errors[0]
