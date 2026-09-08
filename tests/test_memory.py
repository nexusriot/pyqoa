import tokens as token_counter
from attachments import KIND_IMAGE, save_attachments


def _fill(db, chat, count, words=40):
    return [
        db.add_message(
            chat, "user" if i % 2 == 0 else "assistant", f"m{i} " + "word " * words
        )
        for i in range(count)
    ]


def test_short_chats_are_sent_whole(memory, db, chat):
    _fill(db, chat, 3, words=1)
    context, retrieved = memory.build_context(chat, "q")
    assert len(context) == 3 and retrieved == 0


def test_window_keeps_only_the_most_recent_messages(memory, settings, db, chat):
    settings.set("memory_window_size", 4)
    _fill(db, chat, 10, words=1)
    context, _ = memory.build_context(chat, "q")
    assert len(context) == 4
    assert context[-1]["content"].startswith("m9")


def test_disabling_memory_sends_everything(memory, settings, db, chat):
    settings.set("memory_enabled", False)
    settings.set("memory_window_size", 2)
    _fill(db, chat, 6, words=1)
    context, _ = memory.build_context(chat, "q")
    assert len(context) == 6


def test_token_budget_trims_the_oldest_messages(memory, settings, db, chat):
    settings.set("memory_window_size", 50)
    settings.set("memory_max_tokens", 300)
    _fill(db, chat, 12, words=40)
    context, _ = memory.build_context(chat, "q")
    assert token_counter.count_messages(context) <= 300
    assert context[-1]["content"].startswith("m11")


def test_token_budget_always_keeps_one_message(memory, settings, db, chat):
    settings.set("memory_max_tokens", 1)
    _fill(db, chat, 4, words=200)
    context, _ = memory.build_context(chat, "q")
    assert len(context) == 1


def test_zero_budget_means_no_trimming(memory, settings, db, chat):
    settings.set("memory_max_tokens", 0)
    _fill(db, chat, 6, words=50)
    context, _ = memory.build_context(chat, "q")
    assert len(context) == 6


def test_attachments_reach_the_context(memory, db, chat):
    mid = db.add_message(chat, "user", "look at this")
    save_attachments(db, mid, [
        {"kind": KIND_IMAGE, "name": "p.png", "mime": "image/png", "data": b"x"}
    ])
    context, _ = memory.build_context(chat, "q")
    assert isinstance(context[0]["content"], list)


def test_estimate_includes_the_draft_and_system_prompt(memory, db, chat):
    _fill(db, chat, 2, words=5)
    bare = memory.estimate_tokens(chat)
    with_draft = memory.estimate_tokens(chat, draft="a much longer draft message")
    assert with_draft > bare
    assert memory.estimate_tokens(chat, system_prompt="be terse") > bare


def test_forget_is_a_no_op_without_a_vector_store(memory, chat):
    assert memory.forget_messages(chat, []) == 0


def test_vector_layer_is_off_without_configuration(memory, settings):
    settings.set("memory_use_vector", False)
    assert memory.vector_enabled is False
