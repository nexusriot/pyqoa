import sqlite3

import pytest

from database import Database, _fts_query


def test_fts_query_quotes_tokens_and_prefixes_the_last():
    assert _fts_query("hello world") == '"hello" AND "world"*'
    assert _fts_query('say "hi"') == '"say" AND """hi"""*'
    assert _fts_query("   ") == ""


def test_messages_are_ordered_by_id_not_timestamp(db, chat):
    ids = [db.add_message(chat, "user", f"m{i}") for i in range(5)]
    assert [r["id"] for r in db.get_messages(chat)] == ids


def test_delete_chat_cascades_to_messages(db, chat):
    db.add_message(chat, "user", "hi")
    db.delete_chat(chat)
    assert db.get_messages(chat) == []


def test_delete_chats_removes_several(db):
    ids = [db.create_chat(f"c{i}") for i in range(3)]
    db.delete_chats(ids[:2])
    assert [c["id"] for c in db.get_chats()] == [ids[2]]


def test_search_finds_message_bodies(db, chat):
    db.add_message(chat, "user", "how do I sort a list in python")
    hits = db.search_messages("sort")
    assert len(hits) == 1
    assert hits[0]["chat_id"] == chat
    assert "sort" in hits[0]["snippet"].lower()


def test_search_is_prefix_matching_on_the_last_token(db, chat):
    db.add_message(chat, "assistant", "serialisation explained")
    assert db.search_messages("serialis")


def test_search_skips_archived_unless_asked(db, chat):
    db.add_message(chat, "user", "archived content here")
    db.set_archived(chat, True)
    assert db.search_messages("archived") == []
    assert db.search_messages("archived", include_archived=True)


def test_search_ignores_inactive_variants(db, chat):
    mid = db.add_message(chat, "assistant", "unique-variant-text")
    db.begin_variant(mid)
    assert db.search_messages("unique-variant-text") == []


def test_search_survives_fts_syntax_characters(db, chat):
    db.add_message(chat, "user", "a query with (parens) and *stars*")
    assert db.search_messages("(parens)")


def test_deleted_messages_leave_the_index(db, chat):
    mid = db.add_message(chat, "user", "ephemeral phrase")
    db.delete_messages_from(chat, mid)
    assert db.search_messages("ephemeral") == []


def test_like_fallback_when_fts_is_unavailable(db, chat):
    db.add_message(chat, "user", "fallback path text")
    db.fts_enabled = False
    hits = db.search_messages("fallback")
    assert hits and hits[0]["chat_id"] == chat


def test_pin_sorts_a_chat_to_the_top(db):
    first = db.create_chat("first")
    second = db.create_chat("second")
    db.set_pinned(first, True)
    assert [c["id"] for c in db.get_chats()][0] == first
    assert second in [c["id"] for c in db.get_chats()]


def test_archived_chats_are_hidden_by_default(db):
    chat_id = db.create_chat("gone")
    db.set_archived(chat_id, True)
    assert db.get_chats() == []
    assert len(db.get_chats(include_archived=True)) == 1


def test_folders_list_is_deduplicated_and_sorted(db):
    a, b, c = (db.create_chat(f"c{i}") for i in range(3))
    db.set_folder(a, "Work")
    db.set_folder(b, "work-notes")
    db.set_folder(c, "Work")
    assert db.folders() == ["Work", "work-notes"]


def test_blank_folder_clears_the_assignment(db, chat):
    db.set_folder(chat, "Temp")
    db.set_folder(chat, "")
    assert db.get_chat(chat)["folder"] is None


def test_variants_hide_the_previous_reply(db, chat):
    db.add_message(chat, "user", "q")
    first = db.add_message(chat, "assistant", "answer one")
    group = db.begin_variant(first)
    second = db.add_message(chat, "assistant", "answer two", variant_group=group)
    assert [m["content"] for m in db.get_messages(chat)] == ["q", "answer two"]
    assert [v["id"] for v in db.get_variants(second)] == [first, second]


def test_switching_variant_swaps_which_is_visible(db, chat):
    first = db.add_message(chat, "assistant", "one")
    group = db.begin_variant(first)
    second = db.add_message(chat, "assistant", "two", variant_group=group)
    assert db.set_active_variant(first)
    assert [m["id"] for m in db.get_messages(chat)] == [first]
    assert db.get_message(second)["active"] == 0


def test_variant_group_is_stable_across_regenerations(db, chat):
    first = db.add_message(chat, "assistant", "one")
    group = db.begin_variant(first)
    second = db.add_message(chat, "assistant", "two", variant_group=group)
    assert db.begin_variant(second) == group


def test_set_active_variant_needs_a_group(db, chat):
    plain = db.add_message(chat, "assistant", "solo")
    assert db.set_active_variant(plain) is False
    assert db.get_variants(plain) == []


def test_deleting_a_variant_removes_its_siblings(db, chat):
    db.add_message(chat, "user", "q")
    first = db.add_message(chat, "assistant", "one")
    group = db.begin_variant(first)
    second = db.add_message(chat, "assistant", "two", variant_group=group)
    removed = db.delete_messages_from(chat, second)
    assert set(removed) == {first, second}
    assert len(db.get_messages(chat)) == 1


def test_truncate_after_keeps_the_anchor(db, chat):
    ids = [db.add_message(chat, "user", f"m{i}") for i in range(4)]
    removed = db.truncate_after(chat, ids[1])
    assert removed == ids[2:]
    assert [m["id"] for m in db.get_messages(chat)] == ids[:2]


def test_branch_copies_history_up_to_a_message(db, chat):
    db.update_chat_overrides(chat, "gpt-4o", "be terse", 0.2)
    a = db.add_message(chat, "user", "one")
    b = db.add_message(chat, "assistant", "two")
    db.add_message(chat, "user", "three")
    new_id = db.branch_chat(chat, b)
    assert [m["content"] for m in db.get_messages(new_id)] == ["one", "two"]
    assert db.get_chat(new_id)["system_prompt"] == "be terse"
    assert db.get_chat(new_id)["title"].endswith("(branch)")
    # The original is untouched — that is the whole point of branching.
    assert len(db.get_messages(chat)) == 3
    assert a in [m["id"] for m in db.get_messages(chat)]


def test_branch_copies_attachments(db, chat):
    mid = db.add_message(chat, "user", "look")
    db.add_attachment(mid, "image", "a.png", "image/png", b"\x89PNG")
    new_id = db.branch_chat(chat, mid)
    copied = db.get_attachments(db.get_messages(new_id)[0]["id"])
    assert len(copied) == 1 and copied[0]["name"] == "a.png"


def test_branch_of_missing_chat_raises(db):
    with pytest.raises(ValueError):
        db.branch_chat(999, 1)


def test_token_totals_ignore_inactive_variants(db, chat):
    first = db.add_message(chat, "assistant", "one", 10, 20)
    db.begin_variant(first)
    db.add_message(chat, "assistant", "two", 1, 2,
                   variant_group=db.get_message(first)["variant_group"])
    assert db.get_chat_token_totals(chat) == (1, 2)


def test_usage_groups_by_model(db, chat):
    db.add_message(chat, "assistant", "a", 10, 5, model="gpt-4o")
    db.add_message(chat, "assistant", "b", 1, 2, model="gpt-4o")
    db.add_message(chat, "assistant", "c", 3, 4, model="llama3")
    rows = {r["model"]: r for r in db.usage_by_model()}
    assert rows["gpt-4o"]["prompt_tokens"] == 11
    assert rows["llama3"]["messages"] == 1
    assert db.usage_totals() == (3, 14, 11)


def test_usage_by_day_covers_todays_messages(db, chat):
    db.add_message(chat, "assistant", "a", 5, 5, model="gpt-4o")
    rows = db.usage_by_day(7)
    assert rows and rows[-1]["prompt_tokens"] == 5


def test_schema_migrates_an_old_database(tmp_path):
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE chats (id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " title TEXT NOT NULL DEFAULT 'New Chat',"
        " created_at TEXT NOT NULL DEFAULT (datetime('now')),"
        " updated_at TEXT NOT NULL DEFAULT (datetime('now')));"
        "CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " chat_id INTEGER NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL,"
        " created_at TEXT NOT NULL DEFAULT (datetime('now')));"
        "INSERT INTO chats (title) VALUES ('legacy');"
        "INSERT INTO messages (chat_id, role, content) VALUES (1, 'user', 'old text');"
    )
    conn.commit()
    conn.close()

    upgraded = Database(path)
    try:
        row = upgraded.get_messages(1)[0]
        assert row["content"] == "old text"
        assert row["active"] == 1 and row["model"] is None
        assert upgraded.get_chat(1)["pinned"] == 0
        # Rows written before the index existed are backfilled into it.
        assert upgraded.search_messages("old")
    finally:
        upgraded.close()
