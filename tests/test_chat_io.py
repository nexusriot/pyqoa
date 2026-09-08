import json
import zipfile

import pytest

import chat_io
from attachments import KIND_IMAGE, save_attachments


@pytest.fixture
def filled_chat(db):
    chat_id = db.create_chat("Sorting lists")
    db.update_chat_overrides(chat_id, "gpt-4o", "be terse", 0.3)
    db.add_message(chat_id, "user", "how do I sort?")
    db.add_message(
        chat_id, "assistant", "Use `sorted()`\n\n```py\nsorted([2, 1])\n```",
        11, 22, model="gpt-4o",
    )
    return chat_id


def test_safe_filename_strips_path_characters():
    assert chat_io.safe_filename("a/b:c*d") == "a_b_c_d"
    assert chat_io.safe_filename("") == "chat"
    assert len(chat_io.safe_filename("x" * 200)) == 80


def test_json_round_trip_preserves_content_and_overrides(db, filled_chat, tmp_path):
    path = tmp_path / "chat.json"
    chat_io.export_json(db, filled_chat, path)
    new_id = chat_io.import_json(db, path)

    original = db.get_messages(filled_chat)
    restored = db.get_messages(new_id)
    assert [m["content"] for m in restored] == [m["content"] for m in original]
    assert restored[1]["prompt_tokens"] == 11
    assert restored[1]["model"] == "gpt-4o"
    assert db.get_chat(new_id)["system_prompt"] == "be terse"


def test_json_round_trip_preserves_attachments(db, chat, tmp_path):
    mid = db.add_message(chat, "user", "see this")
    save_attachments(db, mid, [
        {"kind": KIND_IMAGE, "name": "p.png", "mime": "image/png", "data": b"\x89PNG"}
    ])
    path = tmp_path / "chat.json"
    chat_io.export_json(db, chat, path)
    new_id = chat_io.import_json(db, path)
    restored = db.get_attachments(db.get_messages(new_id)[0]["id"])
    assert len(restored) == 1
    assert restored[0]["name"] == "p.png"
    assert bytes(restored[0]["data"]) == b"\x89PNG"


def test_import_rejects_a_foreign_file(db, tmp_path):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"hello": "world"}))
    with pytest.raises(ValueError, match="[Nn]ot a valid"):
        chat_io.import_json(db, path)


def test_import_skips_malformed_messages(db, tmp_path):
    path = tmp_path / "partial.json"
    path.write_text(json.dumps({
        "title": "mixed",
        "messages": [
            {"role": "user", "content": "keep me"},
            {"role": "wizard", "content": "drop me"},
            {"role": "assistant"},
            "not even a dict",
        ],
    }))
    new_id = chat_io.import_json(db, path)
    assert [m["content"] for m in db.get_messages(new_id)] == ["keep me"]


def test_markdown_export_has_role_headings(db, filled_chat, tmp_path):
    path = tmp_path / "chat.md"
    chat_io.export_markdown(db, filled_chat, path)
    text = path.read_text()
    assert text.startswith("# Sorting lists")
    assert "## You" in text and "## Assistant" in text


def test_html_export_is_self_contained_and_highlighted(db, filled_chat, tmp_path):
    path = tmp_path / "chat.html"
    chat_io.export_html(db, filled_chat, path)
    html = path.read_text()
    assert html.startswith("<!DOCTYPE html>")
    assert "codeblock" in html and "pyqoacopy" not in html
    assert "Sorting lists" in html


def test_html_export_escapes_the_title(db, tmp_path):
    chat_id = db.create_chat("<script>alert(1)</script>")
    db.add_message(chat_id, "user", "hi")
    html = chat_io.chat_to_html(db, chat_id)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_pdf_export_writes_a_pdf(qapp, db, filled_chat, tmp_path):
    path = tmp_path / "chat.pdf"
    chat_io.export_pdf(db, filled_chat, path)
    assert path.read_bytes().startswith(b"%PDF")


def test_export_all_writes_one_file_per_chat(db, filled_chat, tmp_path):
    db.create_chat("second")
    archived = db.create_chat("archived one")
    db.set_archived(archived, True)
    written = chat_io.export_all(db, tmp_path / "out", "md")
    # Archived chats are part of a full export.
    assert len(written) == 3
    assert all(p.exists() and p.suffix == ".md" for p in written)


def test_export_all_rejects_an_unknown_format(db, tmp_path):
    with pytest.raises(ValueError):
        chat_io.export_all(db, tmp_path, "docx")


def test_backup_holds_the_database_and_scrubbed_settings(
    settings, db, filled_chat, tmp_path
):
    settings.set("api_key", "sk-secret")
    settings.upsert_profile("Other", {"api_key": "sk-other"})
    path = chat_io.backup(settings, db, tmp_path / "backup.zip")
    with zipfile.ZipFile(path) as zf:
        assert set(zf.namelist()) == {"backup.json", "chats.db"}
        payload = json.loads(zf.read("backup.json"))
        assert payload["settings"]["api_key"] == ""
        assert all(p["api_key"] == "" for p in payload["settings"]["profiles"])
        assert zf.read("chats.db").startswith(b"SQLite format 3")
    assert not list(tmp_path.glob("*.dbcopy"))
