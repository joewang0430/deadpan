"""Step 11 (evaluation): result tables with 95% bootstrap CIs, judge agreement and figures.

Reads the generations (step 08), the judged conversations (step 09) and the IFEval scores
(step 10). Writes results/eval/report.md, results/eval/summary.json and three figures.

Every system answered the same items, so a difference between two systems is computed per
item and its CI resamples items (a paired bootstrap). A difference whose 95% CI excludes 0
is marked with *. Rates (tone_ok, factual_error, IFEval) are shown in percent.

  uv run python scripts/11_eval_report.py"""
from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from persona_lora.config import ROOT, load_config  # noqa: E402
from persona_lora.evaluation import ALL_SETS, JUDGED_SETS, OPTIONAL_SETS, eval_dir, item_scores  # noqa: E402
from persona_lora.io_utils import read_jsonl, utf8_console  # noqa: E402
from persona_lora.stats import bootstrap, cohen_kappa, spearman  # noqa: E402

LABEL = {"base": "Base", "prompt": "Prompt-only", "lora": "LoRA"}
# Slots 1-3 of the dataviz skill's reference palette (validated all-pairs for colour-vision
# deficiency). Each system also has its own marker and a text label, so colour is never the
# only way to tell them apart.
COLOR = {"base": "#2a78d6", "prompt": "#eb6834", "lora": "#1baf7a"}
MARKER = {"base": "o", "prompt": "s", "lora": "^"}
INK, INK2, GRID, SURFACE = "#0b0b0b", "#52514e", "#e4e3df", "#ffffff"
PAIRS = [("lora", "prompt"), ("lora", "base")]
RATES = {"factual_error", "tone_ok", "tone_ok_at_pivot"}


def table_metrics(turns: int) -> dict[str, list[str]]:
    return {
        "seen": ["style", "helpfulness", "factual_error"],
        "unseen": ["style", "helpfulness", "factual_error"],
        "sensitive": ["tone_ok", "helpfulness"],
        "pivot": ["style_before", "tone_ok_at_pivot", "helpfulness_at_pivot"],
        "multiturn": ["style", "style_turn1", f"style_turn{turns}"],
    }


def ci(est: tuple[float, float, float], n: int) -> dict:
    return {"mean": est[0], "lo": est[1], "hi": est[2], "n": n}


def fmt(c: dict | None, rate: bool, diff: bool = False) -> str:
    if c is None:
        return "-"
    k, unit = (100, "") if rate else (1, "")
    sign = "+" if diff else ""
    star = "*" if diff and (c["lo"] > 0 or c["hi"] < 0) else ""
    digits = 0 if rate else 2
    return f"{c['mean'] * k:{sign}.{digits}f}{unit} [{c['lo'] * k:.{digits}f}, {c['hi'] * k:.{digits}f}]{star}"


# ---------- judged sets ----------

def load_scores(cfg: dict, role: str, systems: list[str]) -> dict:
    """scores[system][set][item id] = that item's metrics (evaluation.item_scores)."""
    return {name: {s: {r["id"]: item_scores(s, r, r["verdicts"])
                       for r in read_jsonl(eval_dir(cfg) / "judged" / role / name / f"{s}.jsonl")}
                   for s in JUDGED_SETS} for name in systems}


def metric_ci(items: dict, metric: str) -> dict | None:
    vals = [m[metric] for m in items.values() if metric in m]
    return ci(bootstrap(vals), len(vals)) if vals else None


def paired_ci(a: dict, b: dict, metric: str) -> dict | None:
    ids = [i for i in a if i in b and metric in a[i] and metric in b[i]]
    return ci(bootstrap([a[i][metric] - b[i][metric] for i in ids]), len(ids)) if ids else None


def judge_table(scores: dict, systems: list[str], metrics: dict) -> tuple[list[str], dict]:
    pairs = [p for p in PAIRS if p[0] in systems and p[1] in systems]
    head = ["set", "metric", "n"] + [LABEL[s] for s in systems] + [f"{LABEL[a]} − {LABEL[b]}" for a, b in pairs]
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    out = {}
    for s, names in metrics.items():
        for metric in names:
            cells = {name: metric_ci(scores[name][s], metric) for name in systems}
            diffs = {f"{a}-{b}": paired_ci(scores[a][s], scores[b][s], metric) for a, b in pairs}
            if not any(cells.values()):
                continue
            n = max((c["n"] for c in cells.values() if c), default=0)
            rate = metric in RATES
            lines.append("| " + " | ".join([s, metric, str(n)] + [fmt(cells[x], rate) for x in systems]
                                           + [fmt(d, rate, diff=True) for d in diffs.values()]) + " |")
            out[f"{s}/{metric}"] = {**cells, **diffs}
    return lines, out


def agreement(cfg: dict, roles: list[str], systems: list[str]) -> dict | None:
    """Turn-level agreement between the first two judges over every judged assistant turn."""
    if len(roles) < 2:
        return None
    pairs = []
    for name in systems:
        for s in JUDGED_SETS:
            a, b = ({r["id"]: r["verdicts"] for r in read_jsonl(eval_dir(cfg) / "judged" / role / name / f"{s}.jsonl")}
                    for role in roles[:2])
            pairs += [p for i in a.keys() & b.keys() for p in zip(a[i], b[i])]
    if not pairs:
        return None
    get = lambda k: (np.array([p[0][k] for p in pairs], dtype=float), np.array([p[1][k] for p in pairs], dtype=float))  # noqa: E731
    style, helpful = get("style"), get("helpfulness")
    tone, fact = get("tone_appropriate"), get("factual_error")
    return {
        "turns": len(pairs),
        "style_spearman": spearman(*style), "style_within_1": float((abs(style[0] - style[1]) <= 1).mean()),
        "helpfulness_spearman": spearman(*helpful),
        "tone_agree": float((tone[0] == tone[1]).mean()), "tone_kappa": cohen_kappa(*tone),
        "factual_error_agree": float((fact[0] == fact[1]).mean()), "factual_error_kappa": cohen_kappa(*fact),
    }


# ---------- generations and IFEval ----------

LOOP_UNIQUE_WORDS = 0.2  # a cut-off reply with fewer distinct words than this is a repetition loop


def is_loop(text: str) -> bool:
    words = text.split()
    return bool(words) and len(set(words)) / len(words) < LOOP_UNIQUE_WORDS


def generation_stats(cfg: dict, systems: list[str]) -> tuple[list[str], dict]:
    lines = ["| system | set | items | replies | words per reply | cut off at max_tokens | of which repetition loops |",
             "|---|---|---|---|---|---|---|"]
    out = {}
    for name in systems:
        for s in ALL_SETS + OPTIONAL_SETS:
            rows = read_jsonl(eval_dir(cfg) / "gen" / name / f"{s}.jsonl")
            if not rows:
                continue
            replies = [(m["content"], t) for r in rows
                       for m, t in zip([m for m in r["messages"] if m["role"] == "assistant"], r["truncated"])]
            words = [len(text.split()) for text, _ in replies]
            truncated = sum(t for _, t in replies)
            loops = sum(t and is_loop(text) for text, t in replies)
            out[f"{name}/{s}"] = {"items": len(rows), "replies": len(words), "mean_words": float(np.mean(words)),
                                  "truncated": truncated, "loops": loops}
            lines.append(f"| {LABEL[name]} | {s} | {len(rows)} | {len(words)} | {np.mean(words):.0f} "
                         f"| {truncated} | {loops} |")
    return lines, out


def cjk_share(text: str) -> float:
    """Share of Chinese characters among letters, to tell a Chinese reply from an English one."""
    cjk = sum("一" <= ch <= "鿿" for ch in text)
    latin = sum(ch.isascii() and ch.isalpha() for ch in text)
    return cjk / (cjk + latin) if cjk + latin else 0.0


def chinese_reply_rate(cfg: dict, systems: list[str]) -> dict:
    """Optional Chinese set: does each system answer a Chinese prompt in Chinese?"""
    out = {}
    for name in systems:
        rows = read_jsonl(eval_dir(cfg) / "gen" / name / "zh.jsonl")
        if rows:
            flags = [float(cjk_share(r["messages"][-1]["content"]) >= 0.5) for r in rows]
            out[name] = ci(bootstrap(flags), len(flags))
    return out


IFEVAL_METRICS = {
    "prompt_strict": lambda r: (float(all(x["strict"] for x in r)), 1.0),
    "prompt_loose": lambda r: (float(all(x["loose"] for x in r)), 1.0),
    "inst_strict": lambda r: (sum(x["strict"] for x in r), len(r)),
    "inst_loose": lambda r: (sum(x["loose"] for x in r), len(r)),
    "conflict_inst_strict": lambda r: (sum(x["strict"] for x in r if x["conflict"]), sum(x["conflict"] for x in r)),
    "no_conflict_inst_strict": lambda r: (sum(x["strict"] for x in r if not x["conflict"]),
                                          sum(not x["conflict"] for x in r)),
}


def ifeval_table(cfg: dict, systems: list[str], drop_truncated: bool = False) -> tuple[list[str], dict]:
    """IFEval with CIs. drop_truncated keeps only prompts where no system's reply was cut off."""
    rows = {name: read_jsonl(eval_dir(cfg) / "ifeval_scores" / f"{name}.jsonl") for name in systems}
    cut = {r["id"] for name in systems for r in rows[name] if r["truncated"]} if drop_truncated else set()
    scored = {name: {r["id"]: r["results"] for r in rows[name] if r["id"] not in cut} for name in systems}
    systems = [s for s in systems if scored[s]]
    if not systems:
        return [], {}
    pairs = [p for p in PAIRS if p[0] in systems and p[1] in systems]
    head = ["metric", "n"] + [LABEL[s] for s in systems] + [f"{LABEL[a]} − {LABEL[b]}" for a, b in pairs]
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    out = {}
    for metric, f in IFEVAL_METRICS.items():
        cells = {}
        for name in systems:
            num, den = zip(*(f(r) for r in scored[name].values()))
            cells[name] = ci(bootstrap(num, den), int(sum(den)))
        for a, b in pairs:
            ids = sorted(scored[a].keys() & scored[b].keys())
            na, den = zip(*(f(scored[a][i]) for i in ids))
            nb = [f(scored[b][i])[0] for i in ids]
            cells[f"{a}-{b}"] = ci(bootstrap(np.subtract(na, nb), den), int(sum(den)))
        n = cells[systems[0]]["n"]
        lines.append("| " + " | ".join([metric, str(n)] + [fmt(cells[x], True) for x in systems]
                                       + [fmt(cells[f"{a}-{b}"], True, diff=True) for a, b in pairs]) + " |")
        out[metric] = cells
    return lines, out


# ---------- figures ----------

def style_axes(ax) -> None:
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(INK2)
    ax.tick_params(colors=INK2, labelsize=8)
    ax.grid(axis="y", color=GRID, linewidth=0.6)
    ax.set_axisbelow(True)


def figure_header(fig, ax, title: str) -> None:
    """Title on top, one shared legend under it, panels below; nothing overlaps."""
    fig.tight_layout(rect=(0, 0, 1, 0.84))
    fig.suptitle(title, y=0.99, fontsize=10, color=INK)
    handles, labels = ax.get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper center", bbox_to_anchor=(0.5, 0.93), ncol=len(labels),
               frameon=False, fontsize=8)


def spread(values: list[float], gap: float) -> list[float]:
    """Nudge end-of-line label positions apart so they do not overlap."""
    order = np.argsort(values)
    pos = np.array(values, dtype=float)
    for k in range(1, len(order)):
        prev, cur = order[k - 1], order[k]
        pos[cur] = max(pos[cur], pos[prev] + gap)
    return list(pos)


def fig_drift(cfg: dict, roles: list[str], scores: dict, systems: list[str], path) -> None:
    turns = list(range(1, cfg["eval"]["multiturn_turns"] + 1))
    fig, axes = plt.subplots(1, len(roles), figsize=(4.4 * len(roles), 3.3), sharey=True, squeeze=False)
    for ax, role in zip(axes[0], roles):
        ends = {}
        for name in systems:
            cs = [metric_ci(scores[role][name]["multiturn"], f"style_turn{t}") for t in turns]
            if not all(cs):
                continue
            mean = [c["mean"] for c in cs]
            ax.fill_between(turns, [c["lo"] for c in cs], [c["hi"] for c in cs], color=COLOR[name], alpha=0.15, lw=0)
            ax.plot(turns, mean, color=COLOR[name], lw=2, marker=MARKER[name], ms=5,
                    markeredgecolor=SURFACE, markeredgewidth=1, label=LABEL[name])
            ends[name] = mean[-1]
        for name, y in zip(ends, spread(list(ends.values()), 0.22)):
            ax.annotate(LABEL[name], (turns[-1], y), xytext=(8, 0), textcoords="offset points",
                        va="center", fontsize=8, color=INK)
        ax.set_title(cfg["roles"][role]["model"].split("/")[-1], fontsize=9, color=INK)
        ax.set_xticks(turns)
        ax.set_xlabel("Assistant turn", fontsize=8, color=INK2)
        ax.set_ylim(0.85, 5.1)
        ax.set_yticks(range(1, 6))
        ax.set_xlim(turns[0] - 0.3, turns[-1] + 1.6)
        style_axes(ax)
    axes[0][0].set_ylabel("Mean style score (1–5)", fontsize=8, color=INK2)
    figure_header(fig, axes[0][0], "Style over an 8-turn conversation (95% CI)")
    fig.savefig(path, dpi=200, bbox_inches="tight", facecolor=SURFACE)
    plt.close(fig)


def grouped_bars(ax, groups: list[str], cells: dict[str, list[dict | None]], systems: list[str]) -> None:
    width = 0.8 / len(systems)
    for k, name in enumerate(systems):
        x = np.arange(len(groups)) + (k - (len(systems) - 1) / 2) * width
        cs = cells[name]
        mean = np.array([c["mean"] * 100 if c else np.nan for c in cs])
        err = np.array([[c["mean"] * 100 - c["lo"] * 100, c["hi"] * 100 - c["mean"] * 100] if c else [0, 0]
                        for c in cs]).T
        ax.bar(x, mean, width - 0.03, color=COLOR[name], label=LABEL[name], yerr=err,
               error_kw={"ecolor": INK2, "elinewidth": 1, "capsize": 0})
        for xi, c in zip(x, cs):
            if c:
                ax.text(xi, c["hi"] * 100 + 2, f"{c['mean'] * 100:.0f}", ha="center", va="bottom", fontsize=7, color=INK)
    ax.set_xticks(np.arange(len(groups)), groups, fontsize=8)
    ax.set_ylim(0, 112)
    ax.set_yticks(range(0, 101, 20))
    style_axes(ax)


def fig_tone(cfg: dict, roles: list[str], scores: dict, systems: list[str], path) -> None:
    groups = [("sensitive", "tone_ok", "Sensitive prompt\n(single turn)"),
              ("pivot", "tone_ok_at_pivot", "Turns sensitive\nat turn 3 (pivot)")]
    fig, axes = plt.subplots(1, len(roles), figsize=(4.2 * len(roles), 3.3), sharey=True, squeeze=False)
    for ax, role in zip(axes[0], roles):
        cells = {name: [metric_ci(scores[role][name][s], m) for s, m, _ in groups] for name in systems}
        grouped_bars(ax, [g[2] for g in groups], cells, systems)
        ax.set_title(cfg["roles"][role]["model"].split("/")[-1], fontsize=9, color=INK)
    axes[0][0].set_ylabel("Replies with appropriate tone (%)", fontsize=8, color=INK2)
    figure_header(fig, axes[0][0], "Dropping the sarcasm when it is not appropriate (95% CI)")
    fig.savefig(path, dpi=200, bbox_inches="tight", facecolor=SURFACE)
    plt.close(fig)


def fig_ifeval(ifeval: dict, systems: list[str], path) -> None:
    keys = [("conflict_inst_strict", "Conflicts with the persona\n(length or format)"),
            ("no_conflict_inst_strict", "No conflict")]
    systems = [s for s in systems if s in ifeval.get(keys[0][0], {})]
    fig, ax = plt.subplots(figsize=(4.6, 3.3))
    cells = {name: [ifeval[k][name] for k, _ in keys] for name in systems}
    groups = [f"{label}\nn = {ifeval[k][systems[0]]['n']}" for k, label in keys]
    grouped_bars(ax, groups, cells, systems)
    ax.set_ylabel("IFEval instructions followed (%, strict)", fontsize=8, color=INK2)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, 1.13), ncol=len(systems), frameon=False, fontsize=8)
    ax.set_title("Instruction following (95% CI)", fontsize=10, color=INK, pad=24)
    fig.tight_layout()
    fig.savefig(path, dpi=200, bbox_inches="tight", facecolor=SURFACE)
    plt.close(fig)


# ---------- main ----------

def main() -> None:
    utf8_console()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", default=None)
    ap.add_argument("--out", default=str(ROOT / "results" / "eval"))
    args = ap.parse_args()
    cfg = load_config(args.config)
    OUT = Path(args.out)
    systems = list(cfg["eval"]["systems"])
    roles = cfg["eval"]["judges"]
    metrics = table_metrics(cfg["eval"]["multiturn_turns"])
    OUT.mkdir(parents=True, exist_ok=True)

    md = [f"# Evaluation results\n",
          f"Generated by `scripts/11_eval_report.py` on {date.today().isoformat()}. "
          "Each cell is the mean with its 95% bootstrap CI over test items in brackets. "
          "Difference columns are paired by item; `*` marks a CI that excludes 0. "
          "Rates (tone_ok, factual_error, IFEval) are in percent, and their differences in percentage points. "
          "Metric definitions: `docs/design.md`, section 6.\n"]
    summary = {}

    lines, summary["generation"] = generation_stats(cfg, systems)
    md += ["## Generations\n", *lines, "",
           "Words are whitespace-separated, so a Chinese reply (set zh) counts as very few words.\n"]

    scores = {role: load_scores(cfg, role, systems) for role in roles}
    summary["judges"] = {}
    for role in roles:
        lines, summary["judges"][role] = judge_table(scores[role], systems, metrics)
        md += [f"## Judge: {cfg['roles'][role]['model']}\n", *lines, ""]

    agree = agreement(cfg, roles, systems)
    if agree:
        summary["judge_agreement"] = agree
        md += [f"## Agreement between the two judges\n",
               f"Over {agree['turns']} assistant turns graded by both judges.\n",
               "| measure | value |", "|---|---|",
               f"| style, Spearman correlation | {agree['style_spearman']:.2f} |",
               f"| style, scores within 1 point | {agree['style_within_1']:.0%} |",
               f"| helpfulness, Spearman correlation | {agree['helpfulness_spearman']:.2f} |",
               f"| tone appropriate, agreement / Cohen's kappa | {agree['tone_agree']:.0%} / {agree['tone_kappa']:.2f} |",
               f"| factual error, agreement / Cohen's kappa | {agree['factual_error_agree']:.0%} / "
               f"{agree['factual_error_kappa']:.2f} |", ""]

    lines, summary["ifeval"] = ifeval_table(cfg, systems)
    if lines:
        md += ["## IFEval\n",
               "Prompt-level metrics count a prompt as passed only if all its instructions pass. "
               "CIs resample whole prompts.\n", *lines, ""]
        lines, summary["ifeval_no_cutoff"] = ifeval_table(cfg, systems, drop_truncated=True)
        n = summary["ifeval_no_cutoff"]["prompt_strict"][systems[0]]["n"]
        md += ["### IFEval without cut-off replies (exploratory, not pre-registered)\n",
               f"The same metrics on the {n} prompts where no system's reply hit the length limit, "
               "which removes the repetition loops (see Generations).\n", *lines, ""]

    zh_scores = {role: {name: {"zh": {r["id"]: item_scores("zh", r, r["verdicts"]) for r in
                                      read_jsonl(eval_dir(cfg) / "judged" / role / name / "zh.jsonl")}}
                        for name in systems} for role in roles}
    zh_rate = chinese_reply_rate(cfg, systems)
    if zh_rate:
        summary["zh"] = {"replied_in_chinese": zh_rate}
        md += ["## Optional: Chinese prompts (exploratory)\n",
               "The training data is English only. Does the style carry over to Chinese prompts, "
               "and does the system still answer in Chinese?\n",
               "| | " + " | ".join(LABEL[s] for s in zh_rate) + " |", "|---|" + "---|" * len(zh_rate),
               "| replied in Chinese | " + " | ".join(fmt(zh_rate[s], True) for s in zh_rate) + " |", ""]
        for role in roles:
            lines, summary["zh"][role] = judge_table(zh_scores[role], systems,
                                                     {"zh": ["style", "helpfulness", "factual_error"]})
            if summary["zh"][role]:
                md += [f"Judge {cfg['roles'][role]['model']}:\n", *lines, ""]

    fig_drift(cfg, roles, scores, systems, OUT / "fig_drift.png")
    fig_tone(cfg, roles, scores, systems, OUT / "fig_tone.png")
    figs = ["fig_drift.png", "fig_tone.png"]
    if summary["ifeval"]:
        fig_ifeval(summary["ifeval"], systems, OUT / "fig_ifeval.png")
        figs.append("fig_ifeval.png")
    md += ["## Figures\n", *[f"![{f}]({f})\n" for f in figs]]

    (OUT / "report.md").write_text("\n".join(md), encoding="utf-8")
    (OUT / "summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    print(f"wrote {OUT / 'report.md'}, summary.json and {len(figs)} figures")


if __name__ == "__main__":
    main()
