import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QMessageBox

import theme
from attachments import KIND_IMAGE
from fake_api import FakeAPI
from tools import ToolRegistry
from ui.chat_list import KIND_CHAT, KIND_HEADER, KIND_MESSAGE, ROLE_KIND, ChatList
from ui.chat_view import ChatView
from ui.main_window import MainWindow
from ui.message_widget import MessageWidget
from ui.settings_dialog import (
    PromptLibraryDialog, SettingsDialog, headers_to_text, text_to_headers,
)
from ui.usage_dialog import UsageDialog


@pytest.fixture(autouse=True)
def _clean_theme():
    theme.apply("dark")
    theme.set_font_scale(1.0)
    yield
    theme.apply("dark")
    theme.set_font_scale(1.0)


@pytest.fixture
def view(qtbot, settings, db, memory):
    widget = ChatView(settings, db, memory, registry=ToolRegistry(settings))
    qtbot.addWidget(widget)
    return widget


@pytest.fixture
def window(qtbot, settings, db, memory):
    win = MainWindow(settings, db, memory)
    qtbot.addWidget(win)
    return win


def _kinds(chat_list) -> list[str]:
    return [
        chat_list.list_widget.item(i).data(ROLE_KIND)
        for i in range(chat_list.list_widget.count())
    ]


def test_message_widget_renders_markdown(qtbot):
    w = MessageWidget("assistant", "**bold**", message_id=1)
    qtbot.addWidget(w)
    assert "bold" in w.browser.toPlainText()
    assert w.get_text() == "**bold**"


def test_actions_are_hidden_until_the_message_is_saved(qtbot):
    w = MessageWidget("assistant", streaming=True)
    qtbot.addWidget(w)
    assert not w._copy_btn.isVisible()
    w.finalize()
    w.set_message_id(5)
    w.show()
    assert w._copy_btn.isVisible()


def test_branch_button_emits_the_message_id(qtbot):
    w = MessageWidget("user", "hi", message_id=9)
    qtbot.addWidget(w)
    with qtbot.waitSignal(w.branch_requested) as blocker:
        w._branch_btn.click()
    assert blocker.args == [9]


def test_variant_switcher_appears_only_with_alternates(qtbot):
    w = MessageWidget("assistant", "one", message_id=2)
    qtbot.addWidget(w)
    w.show()
    w.set_variants([2], 2)
    assert not w._variant_label.isVisible()
    w.set_variants([2, 3], 2)
    assert w._variant_label.text() == "1/2"
    assert w._variant_prev.isEnabled() is False
    with qtbot.waitSignal(w.variant_requested) as blocker:
        w._variant_next.click()
    assert blocker.args == [3]


def test_live_rendering_keeps_the_unfinished_tail_as_text(qtbot):
    w = MessageWidget("assistant", streaming=True, live_markdown=True)
    qtbot.addWidget(w)
    w.append_chunk("# Heading\n\nstill typ")
    w._live_render()
    text = w.browser.toPlainText()
    assert "Heading" in text and "still typ" in text
    assert w.get_text() == "# Heading\n\nstill typ"


def test_live_rendering_gives_up_on_very_long_replies(qtbot):
    w = MessageWidget("assistant", streaming=True, live_markdown=True)
    qtbot.addWidget(w)
    w.append_chunk("x" * 30_000)
    assert w._live_markdown is False
    assert w.browser.toPlainText().startswith("xxx")


def test_plain_streaming_appends_without_rendering(qtbot):
    w = MessageWidget("assistant", streaming=True, live_markdown=False)
    qtbot.addWidget(w)
    w.append_chunk("abc")
    w.append_chunk("def")
    assert w.browser.toPlainText() == "abcdef"


def test_token_footer_shows_cost_for_known_models(qtbot):
    w = MessageWidget("assistant", "hi", model="gpt-4o", message_id=1)
    qtbot.addWidget(w)
    w.set_token_info(1_000_000, 0)
    assert "$2.50" in w._token_label.text()


def test_attachment_chips_are_rebuilt_each_time(qtbot, db, chat):
    mid = db.add_message(chat, "user", "look")
    db.add_attachment(mid, KIND_IMAGE, "p.png", "image/png", b"notapng")
    w = MessageWidget("user", "look", message_id=mid)
    qtbot.addWidget(w)
    w.set_attachments(db.get_attachments(mid))
    assert w._attach_wrap.isVisibleTo(w)
    assert w._attach_row.count() >= 1
    w.set_attachments([])
    assert not w._attach_wrap.isVisibleTo(w)


def test_chat_list_groups_pinned_and_folders(qtbot, db, settings):
    a = db.create_chat("alpha")
    b = db.create_chat("beta")
    db.set_pinned(a, True)
    db.set_folder(b, "Work")
    widget = ChatList(db, settings)
    qtbot.addWidget(widget)
    widget.refresh()
    assert _kinds(widget).count(KIND_HEADER) == 2
    assert _kinds(widget).count(KIND_CHAT) == 2


def test_chat_list_hides_archived_until_toggled(qtbot, db, settings):
    chat_id = db.create_chat("hidden")
    db.set_archived(chat_id, True)
    widget = ChatList(db, settings)
    qtbot.addWidget(widget)
    widget.refresh()
    assert _kinds(widget).count(KIND_CHAT) == 0
    widget.archive_btn.setChecked(True)
    assert _kinds(widget).count(KIND_CHAT) == 1
    assert settings.get("show_archived") is True


def test_chat_list_shows_message_hits(qtbot, db, settings):
    chat_id = db.create_chat("Some chat")
    db.add_message(chat_id, "assistant", "the mitochondrion is the powerhouse")
    widget = ChatList(db, settings)
    qtbot.addWidget(widget)
    widget.search_edit.setText("mitochondrion")
    widget._search_timer.stop()
    widget._filter()
    assert KIND_MESSAGE in _kinds(widget)


def test_selecting_a_hit_emits_chat_and_message(qtbot, db, settings):
    chat_id = db.create_chat("Some chat")
    mid = db.add_message(chat_id, "assistant", "unique needle here")
    widget = ChatList(db, settings)
    qtbot.addWidget(widget)
    widget.search_edit.setText("needle")
    widget._search_timer.stop()
    widget._filter()
    hit = next(
        widget.list_widget.item(i)
        for i in range(widget.list_widget.count())
        if widget.list_widget.item(i).data(ROLE_KIND) == KIND_MESSAGE
    )
    with qtbot.waitSignal(widget.message_selected) as blocker:
        widget.list_widget.setCurrentItem(hit)
    assert blocker.args == [chat_id, mid]


def test_headers_are_not_selectable(qtbot, db, settings):
    a = db.create_chat("alpha")
    db.set_pinned(a, True)
    db.create_chat("beta")
    widget = ChatList(db, settings)
    qtbot.addWidget(widget)
    widget.refresh()
    for i in range(widget.list_widget.count()):
        item = widget.list_widget.item(i)
        if item.data(ROLE_KIND) == KIND_HEADER:
            assert Qt.ItemFlag.ItemIsSelectable not in item.flags()


def test_deleting_selected_chats_emits_all_ids(qtbot, db, settings, monkeypatch):
    ids = [db.create_chat(f"c{i}") for i in range(3)]
    widget = ChatList(db, settings)
    qtbot.addWidget(widget)
    widget.refresh()
    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes
    )
    with qtbot.waitSignal(widget.chats_deleted) as blocker:
        widget._delete(ids[:2])
    assert blocker.args == [ids[:2]]
    assert [c["id"] for c in db.get_chats()] == [ids[2]]


def test_loading_a_chat_builds_one_widget_per_message(view, db, chat):
    db.add_message(chat, "user", "hi")
    db.add_message(chat, "assistant", "hello", 1, 2, model="gpt-4o")
    view.load_chat(chat)
    assert len(view._message_widgets) == 2
    assert view._token_total_label.text().startswith("Chat: 3 tokens")


def test_send_is_blocked_by_preflight(view, settings, db, chat):
    settings.set("api_key", "")
    settings.set("api_url", "https://api.openai.com/v1")
    view.load_chat(chat)
    view.input_edit.setPlainText("hello")
    view._send()
    assert view.error_banner.isVisibleTo(view)
    assert "API key" in view._error_label.text()
    assert db.get_messages(chat) == []


def test_full_send_round_trip(qtbot, view, settings, db):
    chat = None
    script = [{"type": "stream", "chunks": ["Hi ", "there"],
               "usage": {"prompt_tokens": 4, "completion_tokens": 2}}]
    with FakeAPI(script) as api:
        settings.set("api_url", api.base_url)
        settings.set("model", "test-model")
        settings.set("auto_title", False)
        chat = db.create_chat()          # an untitled chat gets a derived title
        view.load_chat(chat)
        view.input_edit.setPlainText("hey")
        with qtbot.waitSignal(view.chat_updated, timeout=15000):
            view._send()
    rows = db.get_messages(chat)
    assert [r["role"] for r in rows] == ["user", "assistant"]
    assert rows[1]["content"] == "Hi there"
    assert rows[1]["prompt_tokens"] == 4
    assert rows[1]["model"] == "test-model"
    assert db.get_chat(chat)["title"] == "hey"   # fallback title


def test_attachments_are_saved_with_the_message(qtbot, view, settings, db, chat):
    with FakeAPI([{"type": "stream", "chunks": ["ok"]}]) as api:
        settings.set("api_url", api.base_url)
        settings.set("auto_title", False)
        view.load_chat(chat)
        view._pending_attachments.append(
            {"kind": KIND_IMAGE, "name": "p.png", "mime": "image/png", "data": b"x"}
        )
        view.input_edit.setPlainText("what is this")
        with qtbot.waitSignal(view.chat_updated, timeout=15000):
            view._send()
    user_msg = db.get_messages(chat)[0]
    assert db.get_attachments(user_msg["id"])[0]["name"] == "p.png"
    assert view._pending_attachments == []


def test_regenerate_keeps_the_previous_reply_as_a_variant(
    qtbot, view, settings, db, chat
):
    db.add_message(chat, "user", "question")
    first = db.add_message(chat, "assistant", "first answer", model="test-model")
    with FakeAPI([{"type": "stream", "chunks": ["second answer"]}]) as api:
        settings.set("api_url", api.base_url)
        settings.set("auto_title", False)
        view.load_chat(chat)
        with qtbot.waitSignal(view.chat_updated, timeout=15000):
            view._regenerate(first)
    visible = [m["content"] for m in db.get_messages(chat)]
    assert visible == ["question", "second answer"]
    assert len(db.get_variants(db.get_messages(chat)[1]["id"])) == 2


def test_partial_reply_is_persisted_when_stopped(view, settings, db, chat):
    view.load_chat(chat)
    db.add_message(chat, "user", "question")
    view._stream_widget = view._add_widget("assistant", streaming=True)
    view._on_completed("half a reply")
    assert db.get_messages(chat)[-1]["content"] == "half a reply"
    assert view.send_btn.isEnabled()
    assert not view.stop_btn.isVisibleTo(view)


def test_error_finishes_the_stream_and_re_enables_sending(view, db, chat):
    view.load_chat(chat)
    view._stream_widget = view._add_widget("assistant", streaming=True)
    view.send_btn.setEnabled(False)
    view._on_error("Could not reach the API endpoint.")
    assert view.send_btn.isEnabled()
    assert view.error_banner.isVisibleTo(view)


def test_editing_truncates_and_refills_the_composer(view, db, chat):
    view.load_chat(chat)
    first = db.add_message(chat, "user", "original text")
    db.add_message(chat, "assistant", "reply")
    view.load_chat(chat, force=True)
    view._edit_from(first)
    assert db.get_messages(chat) == []
    assert view.input_edit.toPlainText() == "original text"


def test_branching_emits_the_new_chat(qtbot, view, db, chat):
    mid = db.add_message(chat, "user", "keep me")
    db.add_message(chat, "assistant", "drop me")
    view.load_chat(chat)
    with qtbot.waitSignal(view.chat_branched) as blocker:
        view._branch(mid)
    new_id = blocker.args[0]
    assert [m["content"] for m in db.get_messages(new_id)] == ["keep me"]
    assert len(db.get_messages(chat)) == 2


def test_switching_variant_reloads_the_view(view, db, chat):
    first = db.add_message(chat, "assistant", "one")
    group = db.begin_variant(first)
    second = db.add_message(chat, "assistant", "two", variant_group=group)
    view.load_chat(chat)
    assert view._message_widgets[-1].get_text() == "two"
    view._switch_variant(first)
    assert view._message_widgets[-1].get_text() == "one"
    assert second not in [w.message_id for w in view._message_widgets]


def test_find_reports_match_counts(view, db, chat):
    db.add_message(chat, "user", "the quick brown fox")
    db.add_message(chat, "assistant", "the lazy dog")
    view.load_chat(chat)
    view.toggle_find()
    view.find_edit.setText("the")
    assert view._find_status.text() == "1/2"
    view._step_find(1)
    assert view._find_status.text() == "2/2"
    view.find_edit.setText("zebra")
    assert view._find_status.text() == "no matches"


def test_token_meter_counts_the_draft(view, settings, db, chat):
    view.load_chat(chat)
    view._update_token_meter()
    empty = view._meter_label.text()
    view.input_edit.setPlainText("a fairly long draft message goes here")
    view._update_token_meter()
    assert view._meter_label.text() != empty
    assert "tokens in context" in view._meter_label.text()


def test_token_meter_warns_past_the_budget(view, settings, db, chat):
    settings.set("memory_max_tokens", 5)
    view.load_chat(chat)
    view.input_edit.setPlainText("word " * 100)
    view._update_token_meter()
    assert "older messages will be dropped" in view._meter_label.text()


def test_inserting_a_prompt_appends_to_the_draft(view, chat):
    view.load_chat(chat)
    view.input_edit.setPlainText("first")
    view._insert_prompt("second")
    assert view.input_edit.toPlainText() == "first\n\nsecond"


def test_header_text_round_trip():
    assert text_to_headers("X-A: 1\n\n# comment\nX-B: two") == {
        "X-A": "1", "X-B": "two"
    }
    assert headers_to_text({"X-A": "1"}) == "X-A: 1"
    assert text_to_headers("") == {}


def test_settings_dialog_saves_every_tab(qtbot, settings):
    dlg = SettingsDialog(settings)
    qtbot.addWidget(dlg)
    dlg.url_edit.setText("http://localhost:11434/v1")
    dlg.model_combo.setCurrentText("llama3")
    dlg.headers_edit.setPlainText("X-Title: PyQOA")
    dlg.tools_check.setChecked(True)
    dlg.memory_budget_spin.setValue(4096)
    dlg._save()
    assert settings.get("model") == "llama3"
    assert settings.get("request_headers") == {"X-Title": "PyQOA"}
    assert settings.get("tools_enabled") is True
    assert settings.get("memory_max_tokens") == 4096


def test_settings_dialog_blocks_an_invalid_endpoint(qtbot, settings):
    dlg = SettingsDialog(settings)
    qtbot.addWidget(dlg)
    dlg.url_edit.setText("ftp://nope")
    dlg._save()
    assert dlg._error_label.isVisibleTo(dlg)
    assert settings.get("api_url") != "ftp://nope"


def test_settings_dialog_switches_profiles(qtbot, settings):
    settings.upsert_profile("Local", {"api_url": "http://localhost:11434/v1",
                                      "model": "llama3"})
    dlg = SettingsDialog(settings)
    qtbot.addWidget(dlg)
    dlg.profile_combo.setCurrentText("Local")
    assert dlg.model_combo.currentText() == "llama3"
    dlg._save()
    assert settings.active_profile == "Local"
    assert settings.get("model") == "llama3"


def test_mcp_servers_survive_a_save(qtbot, settings):
    settings.set("api_url", "http://localhost:11434/v1")
    dlg = SettingsDialog(settings)
    qtbot.addWidget(dlg)
    dlg._mcp_servers.append(
        {"name": "fs", "command": "npx", "args": ["-y", "x"], "enabled": True}
    )
    dlg._refresh_mcp_list()
    dlg._save()
    assert settings.get("mcp_servers")[0]["name"] == "fs"
    assert dlg.mcp_list.count() == 1


def test_prompt_library_persists_entries(qtbot, settings):
    dlg = PromptLibraryDialog(settings)
    qtbot.addWidget(dlg)
    dlg._prompts.append({"name": "Review", "text": "Review this code"})
    dlg._refresh()
    dlg._save_and_close()
    assert settings.get("prompts") == [
        {"name": "Review", "text": "Review this code"}
    ]


def test_usage_dialog_summarises_tokens(qtbot, db, chat):
    db.add_message(chat, "assistant", "a", 1_000_000, 0, model="gpt-4o")
    dlg = UsageDialog(db)
    qtbot.addWidget(dlg)
    html = dlg._build_html()
    assert "1,000,000" in html and "gpt-4o" in html and "$2.50" in html


def test_main_window_opens_on_a_chat(window, db):
    window._new_chat()
    assert window.chat_view.current_chat_id is not None
    assert window.windowTitle().startswith("PyQOA")


def test_font_scale_action_rebuilds_and_persists(window, settings):
    window.change_font_scale(0.2)
    assert theme.FONT_SCALE == pytest.approx(1.2)
    assert settings.get("font_scale") == pytest.approx(1.2)
    window.change_font_scale(None)
    assert theme.FONT_SCALE == 1.0


def test_theme_switch_keeps_the_open_chat(window, db):
    chat_id = db.create_chat("keep me")
    db.add_message(chat_id, "user", "hi")
    window.chat_list.refresh(select_id=chat_id)
    window.chat_view.load_chat(chat_id)
    window.apply_theme("light")
    assert window.chat_view.current_chat_id == chat_id
    assert theme.NAME == "light"


def test_profile_menu_switch_updates_the_title(window, settings):
    settings.upsert_profile("Local", {"api_url": "http://localhost:11434/v1",
                                      "model": "llama3"})
    window._rebuild_profile_menu()
    window._switch_profile("Local")
    assert settings.active_profile == "Local"
    assert "Local" in window.windowTitle()


def test_deleting_every_chat_starts_a_fresh_one(window, db):
    chat_id = db.create_chat("only one")
    window._on_chats_deleted([chat_id])
    db.delete_chat(chat_id)
    window._on_chats_deleted([chat_id])
    assert db.get_chats()


def test_search_hit_opens_and_focuses_the_message(window, db):
    chat_id = db.create_chat("target")
    db.add_message(chat_id, "user", "first")
    mid = db.add_message(chat_id, "assistant", "second")
    window._on_message_selected(chat_id, mid)
    assert window.chat_view.current_chat_id == chat_id
