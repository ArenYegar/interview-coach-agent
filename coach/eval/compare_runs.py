"""比较两场运行：差异到底有没有大于噪声带。

为什么需要它：实测同一份输入重复跑，单题极差常态 5~10pp（见 docs/eval_stability.md）。
所以"这版比上版高 3pp"这种结论必须配一条判定线，否则就是拿噪声当结论。

用法：
    python -m coach.eval.compare_runs <run_a> <run_b>
    python -m coach.eval.compare_runs <a> <b> --noise-band 8     # 手动指定单题判定线（pp）

判定线来源（按优先级，且会在输出里标明出处）：
    1) results/noiseband-*.json —— 由 coach/eval/stability_probe.py 在 ≥10 题上重复采样得到；
    2) 手动 --noise-band；
    3) 兜底默认单题 10pp / 整场 5pp（明确标注"不是实测值"）。
整场均值的判定线按标准误近似缩放：单题判定线 / sqrt(题目数)。
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import sys
from pathlib import Path

from .. import config


def _load(run_id: str) -> dict:
    p = Path(config.RESULTS_DIR) / f"run_{run_id}.json"
    if not p.exists():
        raise SystemExit(f"找不到 {p}（先跑这场，或检查 run_id）")
    return json.loads(p.read_text(encoding="utf-8"))


def _main_scores(doc: dict) -> dict:
    """只取主问题的分数（追问题号 -F1/-F2 归并到主问题，取该题所有轮次的均值）。"""
    bucket: dict[str, list[float]] = {}
    for t in doc.get("turns") or []:
        sc = t.get("scorecard")
        if not sc:
            continue
        base = str(t["qid"]).split("-F")[0]
        bucket.setdefault(base, []).append(float(sc["total"]))
    return {k: round(sum(v) / len(v), 4) for k, v in bucket.items()}


def noise_band(default_single: float = 10.0) -> tuple[float, float, str]:
    """返回 (单题判定线 pp, 整场均值判定线 pp, 来源说明)。"""
    # 探针写出来的是 results/stability_noiseband-*.json（带 stability_ 前缀），所以这里要匹配子串
    files = sorted(glob.glob(str(Path(config.RESULTS_DIR) / "*noiseband*.json")))
    files.sort(key=lambda f: (0 if "single" in Path(f).name else 1, f))
    for f in files:
        try:
            d = json.loads(Path(f).read_text(encoding="utf-8"))
        except Exception:
            continue
        for name, cond in (d.get("conditions") or {}).items():
            if cond.get("max_range_pp") and cond.get("mean_range_pp"):
                n = len(d.get("questions") or []) or 1
                return (float(cond["max_range_pp"]),
                        round(float(cond["mean_range_pp"]) / math.sqrt(n), 1),
                        f"{Path(f).name} · {name}（{n} 题）")
    return default_single, round(default_single / 2, 1), "兜底默认值（没有 noiseband 产物，不是实测值）"


def compare(a: str, b: str, band_single: float | None = None) -> dict:
    da, db = _load(a), _load(b)
    sa, sb = _main_scores(da), _main_scores(db)
    common = [q for q in sa if q in sb]
    bs, bm, src = noise_band()
    if band_single is not None:
        bs, bm, src = band_single, round(band_single / math.sqrt(max(1, len(common))), 1), "命令行 --noise-band"
    if not common:
        return {"ok": False, "reason": "两场没有共同题号，无法比较（选题不同）", "band": (bs, bm, src)}
    rows = [{"qid": q, "a": sa[q], "b": sb[q], "diff_pp": round((sb[q] - sa[q]) * 100, 1)} for q in common]
    mean_a = round(sum(sa[q] for q in common) / len(common), 4)
    mean_b = round(sum(sb[q] for q in common) / len(common), 4)
    diff = round((mean_b - mean_a) * 100, 1)
    band_mean = round(bs / math.sqrt(len(common)), 1)
    return {
        "ok": True, "run_a": a, "run_b": b, "n_common": len(common),
        "same_question_set": set(sa) == set(sb),
        "rows": rows, "mean_a": mean_a, "mean_b": mean_b, "mean_diff_pp": diff,
        "max_abs_diff_pp": max(abs(r["diff_pp"]) for r in rows),
        "band_single_pp": bs, "band_mean_pp": band_mean, "band_source": src,
        "conclusion": ("差异大于噪声带 → 可以下结论" if abs(diff) > band_mean
                       else "差异在噪声带内 → 不能下结论（需要更多题目/更多重复）"),
    }


def format_text(r: dict) -> str:
    if not r.get("ok"):
        return f"无法比较：{r['reason']}\n判定线来源：{r['band'][2]}"
    L = [f"比较 {r['run_a']} → {r['run_b']}",
         f"  共同题号 {r['n_common']} 道｜选题完全一致={r['same_question_set']}",
         "  逐题（a → b，差 pp）:"]
    for row in sorted(r["rows"], key=lambda x: -abs(x["diff_pp"])):
        L.append("    %-12s %.2f → %.2f  %+6.1f" % (row["qid"], row["a"], row["b"], row["diff_pp"]))
    L += ["",
          f"  整场均值: {r['mean_a']:.3f} → {r['mean_b']:.3f}  （差 {r['mean_diff_pp']:+.1f}pp）",
          f"  单题最大差: {r['max_abs_diff_pp']}pp",
          f"  噪声带: 单题 ±{r['band_single_pp']}pp / 整场均值 ±{r['band_mean_pp']}pp",
          f"  判定线来源: {r['band_source']}",
          f"  结论: {r['conclusion']}"]
    return "\n".join(L)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="比较两场运行：差异是否大于噪声带")
    ap.add_argument("run_a"); ap.add_argument("run_b")
    ap.add_argument("--noise-band", type=float, default=None, help="手动指定单题判定线（pp）")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(list(sys.argv[1:]) if argv is None else list(argv))
    r = compare(a.run_a, a.run_b, band_single=a.noise_band)
    print(json.dumps(r, ensure_ascii=False, indent=2) if a.json else format_text(r))
    return 0 if r.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
