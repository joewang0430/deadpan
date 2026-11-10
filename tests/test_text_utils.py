from persona_lora.config import _merge
from persona_lora.judge import parse_verdict
from persona_lora.text_utils import (
    claims_other_identity, extract_json, jaccard, looks_truncated, rule_checks, strip_think, to_bool, to_score, violates_hard_rules, word_set,
)


def test_extract_json_plain_and_fenced():
    assert extract_json('{"a": 1}') == {"a": 1}
    assert extract_json('Sure.\n```json\n{"a": 2}\n```') == {"a": 2}
    assert extract_json('<think>{"wrong": 1}</think>{"a": 3}') == {"a": 3}
    assert extract_json("no json here") is None


def test_strip_think():
    assert strip_think("<think>\nhmm\n</think>\nanswer") == "answer"


def test_rule_checks_ignore_code():
    text = "Use this:\n```python\nif x != y:\n    print('hi!')\n```\nDone, `a != b` too."
    checks = rule_checks(text)
    assert checks["exclamations"] == 0
    assert not violates_hard_rules(checks)


def test_rule_checks_flag_violations():
    checks = rule_checks("Great question! Here you go 😀. I hope this helps")
    assert checks["exclamations"] == 1
    assert checks["has_emoji"]
    assert "great question" in checks["cliches"] and "i hope this helps" in checks["cliches"]
    assert violates_hard_rules(checks)


def test_coercion():
    assert to_bool("TRUE") is True and to_bool(False) is False and to_bool("maybe") is None
    assert to_score("4") == 4 and to_score(4.6) == 5 and to_score(9) is None and to_score("x") is None


def test_parse_verdict_requires_all_fields():
    good = (
        '{"rationale": "ok", "sensitive_context": false, "style": 4, "tone_appropriate": true, '
        '"helpfulness": 5, "factual_error": false, "insults_user": false}'
    )
    v = parse_verdict(good)
    assert v["style"] == 4 and v["helpfulness"] == 5 and v["insults_user"] is False
    assert parse_verdict('{"style": 4}') is None


def test_merge_replaces_role_specs_wholesale():
    base = {"paths": {"a": 1, "b": 2}, "roles": {"teacher": {"model": "x", "extra_body": {"r": 1}}}}
    over = {"paths": {"b": 3}, "roles": {"teacher": {"model": "y"}}}
    merged = _merge(base, over)
    assert merged["paths"] == {"a": 1, "b": 3}
    assert merged["roles"]["teacher"] == {"model": "y"}


def test_jaccard_near_duplicates():
    a = word_set("My dog died this morning, what do I do?")
    b = word_set("my dog died this morning. what do i do")
    c = word_set("I just lost my job and rent is due Friday.")
    assert jaccard(a, b) == 1.0
    assert jaccard(a, c) < 0.2


def test_claims_other_identity():
    assert claims_other_identity("We're different models. I'm Anthropic's Claude, ChatGPT is OpenAI's model.")
    assert claims_other_identity("I'm GLM, built by Z.ai.")
    assert claims_other_identity("I was trained by OpenAI.")
    assert not claims_other_identity("I'm not ChatGPT, I run on Qwen3-4B.")
    assert not claims_other_identity("ChatGPT is built by OpenAI and Claude by Anthropic.")
    assert not claims_other_identity("I'm an assistant running on Qwen3-4B.")
    assert violates_hard_rules(rule_checks("I'm Claude, made by Anthropic."))


def test_looks_truncated():
    long_prefix = "word " * 320
    assert looks_truncated(long_prefix + "take the first 4 bytes of the hash, interpret")
    assert not looks_truncated(long_prefix + "and that is the whole story.")
    assert not looks_truncated(long_prefix + "| `yield` | Reserved |")
    assert not looks_truncated("Forever yours,\nYour Slightly Overloaded Admirer")  # short: exempt
