import tokens


def test_empty_text_costs_nothing():
    assert tokens.count_tokens("") == 0


def test_counting_grows_with_length():
    short = tokens.count_tokens("hello")
    long = tokens.count_tokens("hello " * 200)
    assert 0 < short < long


def test_message_includes_framing_overhead():
    assert tokens.count_message({"role": "user", "content": "hi"}) > \
        tokens.count_tokens("hi")


def test_multipart_content_charges_images():
    text_only = tokens.count_message({"role": "user", "content": "hi"})
    with_image = tokens.count_message({
        "role": "user",
        "content": [{"type": "text", "text": "hi"},
                    {"type": "image_url", "image_url": {"url": "data:…"}}],
    })
    assert with_image > text_only + 500


def test_heuristic_fallback_when_tiktoken_is_missing(monkeypatch):
    monkeypatch.setattr(tokens, "_TIKTOKEN_IMPORTED", False)
    assert tokens.count_tokens("a" * 40) == 10


def test_unknown_model_still_counts(monkeypatch):
    assert tokens.count_tokens("hello world", "some-local-model") > 0


def test_count_messages_sums_the_list():
    msgs = [{"role": "user", "content": "one"},
            {"role": "assistant", "content": "two"}]
    assert tokens.count_messages(msgs) >= sum(
        tokens.count_message(m) for m in msgs
    )
