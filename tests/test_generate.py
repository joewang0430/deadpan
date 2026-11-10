from persona_lora.generate import base_id, retry_items


def _row(id_, keep, **extra):
    return {"id": id_, "kind": "single", "topic": "other", "sensitive": False, "keep": keep,
            "messages": [{"role": "user", "content": "q " + id_.split("#")[0]}], **extra}


def test_retry_items_only_for_prompts_with_no_kept_attempt():
    judged = [
        _row("a", True),
        _row("b", False),
        _row("c", False), _row("c#r1", True, base_id="c"),
        _row("d", False), _row("d#r1", False, base_id="d"), _row("d#r2", False, base_id="d"),
    ]
    items = retry_items(judged, max_retries=2)
    assert [i["id"] for i in items] == ["b#r1"]
    assert items[0]["base_id"] == "b" and items[0]["text"] == "q b"


def test_retry_items_numbers_attempts():
    judged = [_row("e", False), _row("e#r1", False, base_id="e")]
    assert [i["id"] for i in retry_items(judged, max_retries=2)] == ["e#r2"]
    assert base_id(judged[1]) == "e" and base_id(judged[0]) == "e"
