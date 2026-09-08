import httpx
import pytest
from openai import (
    APITimeoutError, AuthenticationError, NotFoundError,
    RateLimitError,
)

import api_client
from api_client import (
    StreamWorker, TitleWorker, _merge_tool_call_deltas, _tool_calls_payload,
    build_client, clean_title, fallback_title, friendly_error, is_local_url,
    is_reasoning_model, preflight,
)
from fake_api import FakeAPI
from settings import Settings
from tools import ToolRegistry


@pytest.fixture
def cfg(data_dir) -> Settings:
    s = Settings(data_dir)
    s.set("max_retries", 2)
    s.set("retry_base_delay", 0.05)
    s.set("timeout", 10)
    return s


def _http_error(cls, status: int):
    """Build an SDK status error without a live request."""
    request = httpx.Request("POST", "http://x/v1/chat/completions")
    response = httpx.Response(status, request=request, json={"error": {}})
    return cls("boom", response=response, body=None)


def test_reasoning_models_are_detected_behind_a_provider_prefix():
    assert is_reasoning_model("o1")
    assert is_reasoning_model("openai/o3-mini")
    assert not is_reasoning_model("gpt-4o")
    assert not is_reasoning_model("")


def test_local_urls_do_not_need_a_key():
    assert is_local_url("http://localhost:11434/v1")
    assert is_local_url("http://127.0.0.1:8080/v1")
    assert not is_local_url("https://api.openai.com/v1")


def test_clean_title_strips_quotes_and_caps_length():
    assert clean_title('"A nice title."') == "A nice title"
    assert clean_title("line one\nline two") == "line one"
    assert len(clean_title("x" * 200)) == 60
    assert clean_title("") == ""


def test_fallback_title_collapses_whitespace_and_truncates():
    assert fallback_title("  hello   world ") == "hello world"
    assert fallback_title("x" * 60).endswith("…")
    assert fallback_title("") == "New Chat"


def test_tool_call_deltas_are_reassembled_in_order():
    acc: dict = {}

    class _Fn:
        def __init__(self, name=None, arguments=None):
            self.name, self.arguments = name, arguments

    class _Delta:
        def __init__(self, index, id=None, function=None):
            self.index, self.id, self.function = index, id, function

    _merge_tool_call_deltas(acc, [_Delta(0, "call_1", _Fn("calc", '{"a"'))])
    _merge_tool_call_deltas(acc, [_Delta(0, None, _Fn(None, ": 1}"))])
    assert acc[0] == {"id": "call_1", "name": "calc", "arguments": '{"a": 1}'}


def test_tool_calls_payload_synthesises_missing_ids():
    payload = _tool_calls_payload([{"name": "t", "arguments": ""}])
    assert payload[0]["id"] == "call_0"
    assert payload[0]["function"] == {"name": "t", "arguments": "{}"}


def test_preflight_accepts_a_complete_configuration(cfg):
    cfg.set("api_key", "sk-test")
    assert preflight(cfg) is None


def test_preflight_flags_a_missing_key_on_a_remote_endpoint(cfg):
    cfg.set("api_key", "")
    assert "API key" in preflight(cfg)


def test_preflight_allows_a_keyless_local_endpoint(cfg):
    cfg.set("api_key", "")
    cfg.set("api_url", "http://localhost:11434/v1")
    assert preflight(cfg) is None


def test_preflight_rejects_a_malformed_url(cfg):
    cfg.set("api_url", "not-a-url")
    assert "not a valid" in preflight(cfg)


def test_preflight_requires_a_model(cfg):
    cfg.set("api_key", "sk-test")
    cfg.set("model", "")
    assert "model" in preflight(cfg)


def test_preflight_honours_a_per_chat_model_override(cfg):
    cfg.set("api_key", "sk-test")
    cfg.set("model", "")
    assert preflight(cfg, {"model": "gpt-4o"}) is None


@pytest.mark.parametrize("exc,needle", [
    (_http_error(AuthenticationError, 401), "key"),
    (_http_error(NotFoundError, 404), "not found"),
    (_http_error(RateLimitError, 429), "Rate limit"),
    (APITimeoutError(request=None), "timed out"),
])
def test_friendly_error_explains_common_failures(exc, needle):
    assert needle.lower() in friendly_error(exc).lower()


def test_friendly_error_falls_back_to_the_raw_message():
    assert friendly_error(ValueError("weird")) == "weird"


def test_retryable_classification():
    assert api_client._is_retryable(_http_error(RateLimitError, 429))
    assert api_client._is_retryable(_http_error(api_client.APIStatusError, 503))
    assert not api_client._is_retryable(_http_error(AuthenticationError, 401))


def test_build_client_sends_custom_headers(cfg):
    cfg.set("request_headers", {"X-Title": "PyQOA"})
    client = build_client(cfg)
    assert client.default_headers["X-Title"] == "PyQOA"
    assert "User-Agent" in client.default_headers


def test_build_client_ignores_malformed_headers(cfg):
    cfg.set("request_headers", "oops")
    assert build_client(cfg) is not None


def _run(qtbot, worker, timeout=15000):
    with qtbot.waitSignal(worker.completed, timeout=timeout) as blocker:
        worker.start()
    worker.wait(timeout)
    return blocker.args[0]


def test_streaming_reply_is_assembled_and_usage_reported(qtbot, cfg):
    script = [{"type": "stream", "chunks": ["Hello", " world"],
               "usage": {"prompt_tokens": 7, "completion_tokens": 3}}]
    with FakeAPI(script) as api:
        cfg.set("api_url", api.base_url)
        cfg.set("model", "test-model")
        worker = StreamWorker(cfg, messages=[{"role": "user", "content": "hi"}])
        chunks = []
        usage = []
        worker.chunk_received.connect(chunks.append)
        worker.usage_received.connect(lambda p, c: usage.append((p, c)))
        assert _run(qtbot, worker) == "Hello world"
        assert chunks == ["Hello", " world"]
        assert usage == [(7, 3)]


def test_non_streaming_request(qtbot, cfg):
    with FakeAPI([{"type": "json", "content": "one shot",
                   "usage": {"prompt_tokens": 1, "completion_tokens": 2}}]) as api:
        cfg.set("api_url", api.base_url)
        cfg.set("stream", False)
        worker = StreamWorker(cfg, messages=[{"role": "user", "content": "hi"}])
        assert _run(qtbot, worker) == "one shot"


def test_transient_failure_is_retried_with_a_signal(qtbot, cfg):
    script = [{"type": "status", "code": 503},
              {"type": "stream", "chunks": ["recovered"]}]
    with FakeAPI(script) as api:
        cfg.set("api_url", api.base_url)
        worker = StreamWorker(cfg, messages=[{"role": "user", "content": "hi"}])
        retries = []
        worker.retrying.connect(lambda a, b: retries.append((a, b)))
        assert _run(qtbot, worker) == "recovered"
        assert retries == [(1, 2)]


def test_authentication_failure_is_not_retried(qtbot, cfg):
    with FakeAPI([{"type": "status", "code": 401}]) as api:
        cfg.set("api_url", api.base_url)
        worker = StreamWorker(cfg, messages=[{"role": "user", "content": "hi"}])
        with qtbot.waitSignal(worker.error, timeout=15000) as blocker:
            worker.start()
        worker.wait(15000)
        assert "key" in blocker.args[0].lower()
        assert len(api.requests) == 1


def test_stream_options_rejection_falls_back_to_a_plain_stream(qtbot, cfg):
    script = [{"type": "reject_stream_options",
               "then": {"type": "stream", "chunks": ["plain"]}}]
    with FakeAPI(script) as api:
        cfg.set("api_url", api.base_url)
        worker = StreamWorker(cfg, messages=[{"role": "user", "content": "hi"}])
        assert _run(qtbot, worker) == "plain"
        assert len(api.requests) == 2
        assert "stream_options" not in api.requests[1]["body"]


def test_reasoning_models_swap_the_token_parameter(qtbot, cfg):
    with FakeAPI([{"type": "stream", "chunks": ["ok"]}]) as api:
        cfg.set("api_url", api.base_url)
        cfg.set("model", "o3-mini")
        cfg.set("temperature", 0.9)
        worker = StreamWorker(cfg, messages=[{"role": "user", "content": "hi"}])
        _run(qtbot, worker)
        body = api.requests[0]["body"]
        assert "max_completion_tokens" in body and "max_tokens" not in body
        assert "temperature" not in body


def test_custom_headers_reach_the_server(qtbot, cfg):
    with FakeAPI([{"type": "stream", "chunks": ["ok"]}]) as api:
        cfg.set("api_url", api.base_url)
        cfg.set("request_headers", {"X-Title": "PyQOA"})
        worker = StreamWorker(cfg, messages=[{"role": "user", "content": "hi"}])
        _run(qtbot, worker)
        assert api.requests[0]["headers"]["X-Title"] == "PyQOA"


def test_tool_call_round_trip(qtbot, cfg):
    script = [
        {"type": "stream", "tool_calls": [{
            "index": 0, "id": "call_1", "type": "function",
            "function": {"name": "calculate", "arguments": '{"expression": "6*7"}'},
        }]},
        {"type": "stream", "chunks": ["The answer is 42"]},
    ]
    with FakeAPI(script) as api:
        cfg.set("api_url", api.base_url)
        cfg.set("tools_enabled", True)
        cfg.set("tool_builtins", ["calculate"])
        registry = ToolRegistry(cfg)
        worker = StreamWorker(
            cfg, messages=[{"role": "user", "content": "6*7?"}], registry=registry
        )
        assert _run(qtbot, worker) == "The answer is 42"
        assert worker.tool_log == ["calculate"]
        follow_up = api.requests[1]["body"]["messages"]
        assert follow_up[-1]["role"] == "tool"
        assert follow_up[-1]["content"] == "42"
        assert follow_up[-2]["tool_calls"][0]["id"] == "call_1"
        registry.close()


def test_tool_rounds_are_capped(qtbot, cfg):
    call = {"type": "stream", "tool_calls": [{
        "index": 0, "id": "c", "type": "function",
        "function": {"name": "calculate", "arguments": '{"expression": "1"}'},
    }]}
    with FakeAPI([dict(call) for _ in range(10)]) as api:
        cfg.set("api_url", api.base_url)
        cfg.set("tools_enabled", True)
        cfg.set("tool_builtins", ["calculate"])
        cfg.set("tool_max_rounds", 2)
        registry = ToolRegistry(cfg)
        worker = StreamWorker(
            cfg, messages=[{"role": "user", "content": "loop"}], registry=registry
        )
        notes = []
        worker.tool_activity.connect(notes.append)
        _run(qtbot, worker)
        assert len(api.requests) == 3  # 2 tool rounds + the final attempt
        assert any("tool_max_rounds" in n for n in notes)
        registry.close()


def test_tools_are_not_offered_when_disabled(qtbot, cfg):
    with FakeAPI([{"type": "stream", "chunks": ["ok"]}]) as api:
        cfg.set("api_url", api.base_url)
        cfg.set("tools_enabled", False)
        worker = StreamWorker(
            cfg, messages=[{"role": "user", "content": "hi"}],
            registry=ToolRegistry(cfg),
        )
        _run(qtbot, worker)
        assert "tools" not in api.requests[0]["body"]


def test_cancelled_worker_still_reports_completion(qtbot, cfg):
    with FakeAPI([{"type": "stream", "chunks": ["a", "b"]}]) as api:
        cfg.set("api_url", api.base_url)
        worker = StreamWorker(cfg, messages=[{"role": "user", "content": "hi"}])
        worker.cancel()
        # Even a worker cancelled before it starts must emit `completed`, or the
        # composer would stay disabled forever.
        assert _run(qtbot, worker) == ""


def test_title_worker_returns_a_clean_title(qtbot, cfg):
    with FakeAPI([{"type": "json", "content": '"Sorting Lists In Python."'}]) as api:
        cfg.set("api_url", api.base_url)
        worker = TitleWorker(cfg, 7, "how do I sort a list?")
        with qtbot.waitSignal(worker.title_ready, timeout=15000) as blocker:
            worker.start()
        worker.wait(15000)
        assert blocker.args == [7, "Sorting Lists In Python"]


def test_title_worker_stays_silent_on_failure(qtbot, cfg):
    with FakeAPI([{"type": "status", "code": 500}]) as api:
        cfg.set("api_url", api.base_url)
        worker = TitleWorker(cfg, 7, "anything")
        worker.start()
        worker.wait(15000)
        assert not worker.isRunning()
