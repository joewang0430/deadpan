"""IFEval scoring (Zhou et al., 2023), plus the persona-conflict split used in this project.

instructions.py, instructions_registry.py and instructions_util.py are copied unchanged
(apart from the import lines) from EleutherAI/lm-evaluation-harness, lm_eval/tasks/ifeval,
on 2026-11-08. They are Google Research's original checkers, Apache-2.0 (see the headers).
`follows` below re-implements the strict and loose tests from that harness's utils.py."""
from __future__ import annotations

from . import instructions_registry

# Fixed before any results were seen (docs/design.md, section 6). An instruction conflicts
# with the persona when it asks for more length or structure than the persona's default:
# "usually two to six sentences", "plain prose by default".
LONG_WORDS = 100       # about six sentences
LONG_SENTENCES = 7     # more than six
MANY_PARAGRAPHS = 3    # two short paragraphs still fit the persona


def conflicts_with_persona(instruction_id: str, kwargs: dict) -> bool:
    kind = instruction_id.split(":")[1]
    if kind == "number_words":
        return kwargs.get("relation") == "at least" and kwargs["num_words"] >= LONG_WORDS
    if kind == "number_sentences":
        return kwargs.get("relation") == "at least" and kwargs["num_sentences"] >= LONG_SENTENCES
    if kind in ("number_paragraphs", "nth_paragraph_first_word"):
        return kwargs["num_paragraphs"] >= MANY_PARAGRAPHS
    return kind in ("number_bullet_lists", "multiple_sections", "number_highlighted_sections")


def _loose_variants(response: str) -> list[str]:
    """The loose test also accepts the response without its first and/or last line, or without '*'."""
    lines = response.split("\n")
    cut = ["\n".join(lines[1:]).strip(), "\n".join(lines[:-1]).strip(), "\n".join(lines[1:-1]).strip()]
    variants = [response, *cut]
    return variants + [v.replace("*", "") for v in variants]


def follows(item: dict, response: str) -> list[dict]:
    """Strict and loose pass/fail for every instruction of one IFEval prompt."""
    results = []
    for instruction_id, raw_kwargs in zip(item["instruction_id_list"], item["kwargs"]):
        kwargs = {k: v for k, v in raw_kwargs.items() if v}  # the harness drops empty values too
        checker = instructions_registry.INSTRUCTION_DICT[instruction_id](instruction_id)
        checker.build_description(**kwargs)
        args = checker.get_instruction_args()
        if args and "prompt" in args:
            checker.build_description(prompt=item["prompt"])
        results.append({
            "instruction_id": instruction_id,
            "conflict": conflicts_with_persona(instruction_id, kwargs),
            "strict": bool(response.strip()) and bool(checker.check_following(response)),
            "loose": any(v.strip() and checker.check_following(v) for v in _loose_variants(response)),
        })
    return results
