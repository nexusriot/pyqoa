import utils


def test_markdown_renders_code_with_a_copy_link():
    html, codes = utils.render_markdown("text\n\n```py\nprint(1)\n```")
    assert codes == ["print(1)\n"]
    assert "pyqoacopy:0" in html


def test_export_rendering_drops_the_copy_link():
    html, _ = utils.render_markdown("```py\nprint(1)\n```", for_export=True)
    assert "pyqoacopy" not in html
    assert "codeblock" in html


def test_escape_html_handles_none():
    assert utils.escape_html(None) == ""
    assert utils.escape_html("<b>") == "&lt;b&gt;"


def test_open_code_fence_detection():
    assert utils.in_open_code_fence("a\n```py\nx")
    assert not utils.in_open_code_fence("a\n```py\nx\n```")
    assert not utils.in_open_code_fence("")


def test_stable_prefix_splits_on_the_last_blank_line():
    assert utils.stable_prefix("one\n\ntwo") == ("one\n\n", "two")


def test_stable_prefix_never_splits_inside_a_code_fence():
    head, tail = utils.stable_prefix("intro\n\n```py\nprint(1)\n\nprint(2)")
    assert head == "intro\n\n"
    assert tail.startswith("```py")


def test_stable_prefix_of_a_single_paragraph_is_all_tail():
    assert utils.stable_prefix("just typing") == ("", "just typing")


def test_stable_prefix_of_empty_text():
    assert utils.stable_prefix("") == ("", "")


def test_fallback_renderer_handles_fences(monkeypatch):
    monkeypatch.setattr(utils, "_MARKDOWN", False)
    html, codes = utils.render_markdown("hi\n\n```py\nprint(1)\n```")
    assert codes == ["print(1)\n"]
    assert "print" in html
