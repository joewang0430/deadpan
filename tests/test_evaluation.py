import asyncio

from persona_lora.evaluation import converse, item_scores, summarize
from persona_lora.ifeval import conflicts_with_persona, follows


def _v(style=4, help_=5, tone=True, fact=False):
    return {"style": style, "helpfulness": help_, "tone_appropriate": tone, "factual_error": fact}


def test_item_scores_and_summary():
    assert item_scores("seen", {}, [_v(3)]) == {"helpfulness": 5, "factual_error": 0.0, "style": 3}
    assert "style" not in item_scores("seen", {"sensitive": True}, [_v(1)])
    pivot = item_scores("pivot", {}, [_v(4), _v(5), _v(1, tone=False)])
    assert pivot == {"style_before": 4.5, "tone_ok_at_pivot": 0.0, "helpfulness_at_pivot": 5}
    drift = item_scores("multiturn", {}, [_v(5), _v(3)])
    assert drift["style_turn2"] == 3 and drift["style"] == 4
    assert item_scores("multiturn", {"sensitive": True}, [_v(1), _v(1)]) == {}
    assert summarize([{"style": 2}, {"style": 4}, {}]) == {"style": 3}


def test_conflict_split_follows_persona_defaults():
    assert conflicts_with_persona("length_constraints:number_words", {"relation": "at least", "num_words": 300})
    assert not conflicts_with_persona("length_constraints:number_words", {"relation": "less than", "num_words": 300})
    assert not conflicts_with_persona("length_constraints:number_sentences", {"relation": "at least", "num_sentences": 3})
    assert not conflicts_with_persona("length_constraints:number_paragraphs", {"num_paragraphs": 2})
    assert conflicts_with_persona("detectable_format:number_bullet_lists", {"num_bullets": 3})
    assert not conflicts_with_persona("change_case:english_lowercase", {})


def test_follows_strict_and_loose():
    item = {"prompt": "x", "instruction_id_list": ["change_case:english_lowercase"], "kwargs": [{}]}
    assert follows(item, "all lowercase here")[0]["strict"]
    loose_only = follows(item, "Title Line\nthe rest is lowercase")[0]
    assert not loose_only["strict"] and loose_only["loose"]


class _FakeSystem:
    async def reply(self, messages):
        return f"reply {len(messages) // 2 + 1}", False


def test_converse_with_scripted_turns():
    turns = ["q1", "q2", "q3"]

    async def scripted(i, _messages):
        return turns[i]

    messages, truncated = asyncio.run(converse(_FakeSystem(), turns[0], 3, scripted))
    assert [m["content"] for m in messages] == ["q1", "reply 1", "q2", "reply 2", "q3", "reply 3"]
    assert truncated == [False] * 3


def test_stats():
    from persona_lora.stats import bootstrap, cohen_kappa, spearman
    mean, lo, hi = bootstrap([0, 1, 1, 1])
    assert mean == 0.75 and 0 <= lo <= mean <= hi <= 1
    assert bootstrap([1, 2], [2, 2])[0] == 0.75  # ratio of sums, not mean of ratios
    assert bootstrap([2, 2, 2])[1:] == (2.0, 2.0)
    assert spearman([1, 2, 3], [10, 20, 30]) == 1.0
    assert abs(spearman([1, 1, 2], [3, 3, 1]) + 1) < 1e-9
    assert cohen_kappa([1, 0, 1, 0], [1, 0, 1, 0]) == 1.0
    assert cohen_kappa([1, 1, 0, 0], [1, 0, 1, 0]) == 0.0
