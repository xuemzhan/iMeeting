#!/usr/bin/env python3
"""Paired comparison, bootstrap CI, and error breakdown on WenetSpeech meeting."""

from __future__ import annotations

import json
import random
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / "results"
DIGITS = str.maketrans("0123456789", "零一二三四五六七八九")


def norm(text: str) -> str:
    text = re.sub(r"<\|[^|]*\|>", "", text or "")
    text = re.sub(r"[^\w\u4e00-\u9fff]", "", text).upper()
    return text.translate(DIGITS)


def content_only(text: str) -> str:
    return re.sub(r"[嗯呃啊哦唉呀]", "", text)


def align(ref: str, hyp: str):
    """Return (edits, substitutions, deletions, insertions, sub_pairs)."""
    r, h = list(ref), list(hyp)
    n, m = len(r), len(h)
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        dp[i][0] = i
    for j in range(m + 1):
        dp[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            dp[i][j] = min(
                dp[i - 1][j] + 1,
                dp[i][j - 1] + 1,
                dp[i - 1][j - 1] + (r[i - 1] != h[j - 1]),
            )
    subs = dels = ins = 0
    pairs: Counter = Counter()
    i, j = n, m
    while i > 0 or j > 0:
        if i > 0 and j > 0 and r[i - 1] == h[j - 1] and dp[i][j] == dp[i - 1][j - 1]:
            i -= 1
            j -= 1
        elif i > 0 and j > 0 and dp[i][j] == dp[i - 1][j - 1] + 1:
            subs += 1
            pairs[(r[i - 1], h[j - 1])] += 1
            i -= 1
            j -= 1
        elif i > 0 and dp[i][j] == dp[i - 1][j] + 1:
            dels += 1
            i -= 1
        else:
            ins += 1
            j -= 1
    return dp[n][m], subs, dels, ins, pairs


def load_rows(path: Path) -> dict[str, dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return {row["id"]: row for row in data["rows"]}


def main() -> None:
    models = {
        "seaco": load_rows(RESULTS / "wenetspeech_meeting" / "seaco_vad.json"),
        "paraformer": load_rows(RESULTS / "wenetspeech_meeting" / "paraformer_vad.json"),
        "sensevoice": load_rows(RESULTS / "wenetspeech_meeting" / "sensevoice_vad.json"),
        "sensevoice_gguf": load_rows(RESULTS / "sensevoice_gguf_wenetspeech.json"),
        "nano_q4": load_rows(RESULTS / "nano_q4_wenetspeech.json"),
        "nano_q5": load_rows(RESULTS / "nano_gguf_wenetspeech.json"),
        "nano_q8": load_rows(RESULTS / "nano_q8_wenetspeech.json"),
    }
    ids = sorted(set.intersection(*(set(rows) for rows in models.values())))
    print(f"aligned utterances: {len(ids)}")

    per = {name: {} for name in models}
    for name, rows in models.items():
        for key in ids:
            row = rows[key]
            ref, hyp = norm(row["ref"]), norm(row["hyp"])
            edits, subs, dels, ins, pairs = align(ref, hyp)
            per[name][key] = {
                "ref": ref,
                "hyp": hyp,
                "ref_chars": len(ref),
                "edits": edits,
                "subs": subs,
                "dels": dels,
                "ins": ins,
                "pairs": pairs,
                "duration": row["duration"],
                "rtf": row.get("infer_s", row.get("compute_s", 0)) / max(row["duration"], 1e-9),
            }

    def cer(name: str, indices: list[int]) -> float:
        edits = sum(per[name][ids[i]]["edits"] for i in indices)
        chars = sum(per[name][ids[i]]["ref_chars"] for i in indices)
        return edits / max(chars, 1)

    rng = random.Random(20261019)
    n = len(ids)
    order = list(models)
    out = {"items": n, "pairs": {}, "models": {}}
    for name in order:
        edits = sum(per[name][k]["edits"] for k in ids)
        chars = sum(per[name][k]["ref_chars"] for k in ids)
        subs = sum(per[name][k]["subs"] for k in ids)
        dels = sum(per[name][k]["dels"] for k in ids)
        ins = sum(per[name][k]["ins"] for k in ids)
        pairs: Counter = Counter()
        for k in ids:
            pairs.update(per[name][k]["pairs"])
        by_duration = {}
        for label, pred in (
            ("short_lt5s", lambda d: d < 5),
            ("medium_5to10s", lambda d: 5 <= d < 10),
            ("long_ge10s", lambda d: d >= 10),
        ):
            subset = [k for k in ids if pred(per[name][k]["duration"])]
            e = sum(per[name][k]["edits"] for k in subset)
            c = sum(per[name][k]["ref_chars"] for k in subset)
            by_duration[label] = {
                "items": len(subset),
                "cer": round(e / max(c, 1) * 100, 2),
            }
        content_edits = sum(
            align(content_only(per[name][k]["ref"]), content_only(per[name][k]["hyp"]))[0]
            for k in ids
        )
        content_chars = sum(len(content_only(per[name][k]["ref"])) for k in ids)
        out["models"][name] = {
            "cer": round(edits / max(chars, 1) * 100, 2),
            "content_cer": round(content_edits / max(content_chars, 1) * 100, 2),
            "edits": edits,
            "ref_chars": chars,
            "subs": subs,
            "dels": dels,
            "ins": ins,
            "sub_rate": round(subs / max(chars, 1) * 100, 2),
            "del_rate": round(dels / max(chars, 1) * 100, 2),
            "ins_rate": round(ins / max(chars, 1) * 100, 2),
            "top_substitutions": [
                {"ref": a, "hyp": b, "count": c} for (a, b), c in pairs.most_common(10)
            ],
            "cer_by_duration": by_duration,
        }

    for a_idx, a in enumerate(order):
        for b in order[a_idx + 1 :]:
            obs = cer(a, list(range(n))) - cer(b, list(range(n)))
            boot = []
            for _ in range(5000):
                idxs = [rng.randrange(n) for _ in range(n)]
                boot.append(cer(a, idxs) - cer(b, idxs))
            boot.sort()
            lo, hi = boot[int(0.025 * len(boot))], boot[int(0.975 * len(boot)) - 1]
            lower = sum(1 for x in boot if x <= 0) / len(boot)
            upper = sum(1 for x in boot if x >= 0) / len(boot)
            p = min(1.0, 2 * min(lower, upper))
            wins = losses = ties = 0
            for k in ids:
                ea, eb = per[a][k]["edits"], per[b][k]["edits"]
                if ea < eb:
                    wins += 1
                elif ea > eb:
                    losses += 1
                else:
                    ties += 1
            out["pairs"][f"{a}_vs_{b}"] = {
                "cer_diff_pp": round(obs * 100, 2),
                "ci95_pp": [round(lo * 100, 2), round(hi * 100, 2)],
                "p_bootstrap": round(p, 4),
                "a_better_utterances": wins,
                "b_better_utterances": losses,
                "ties": ties,
            }

    (RESULTS / "meeting_comparison.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    lines = [
        "| 模型 | CER | 去语气词 CER | 替换率 | 删除率 | 插入率 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for name in order:
        m = out["models"][name]
        lines.append(
            f"| {name} | {m['cer']:.2f}% | {m['content_cer']:.2f}% | {m['sub_rate']:.2f}% | "
            f"{m['del_rate']:.2f}% | {m['ins_rate']:.2f}% |"
        )
    lines.append("")
    lines.append("| 配对 | CER 差（A-B） | 95% CI | bootstrap p | A 更好 / B 更好 / 平 |")
    lines.append("|---|---:|---:|---:|---:|")
    for key, v in out["pairs"].items():
        lines.append(
            f"| {key} | {v['cer_diff_pp']:+.2f} pp | "
            f"[{v['ci95_pp'][0]:+.2f}, {v['ci95_pp'][1]:+.2f}] | "
            f"{v['p_bootstrap']:.4f} | "
            f"{v['a_better_utterances']} / {v['b_better_utterances']} / {v['ties']} |"
        )
    (RESULTS / "meeting_comparison.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
