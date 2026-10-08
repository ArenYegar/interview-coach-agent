"""题库工具：生成草稿池 / 校验 / 合并（合并前必须校验通过）。

为什么要有校验这一步：题库是本项目所有评测指标的分母（覆盖率、要点命中率、选题质量都靠它），
题库一脏，上面所有数字都不成立。所以草稿必须先过一遍机器校验，再由人抽查，才允许合并。

用法：
    python -m coach.eval.bank_tools build                    # 生成 gold_bank_draft.jsonl
    python -m coach.eval.bank_tools validate [file]          # 校验（默认校验草稿池）
    python -m coach.eval.bank_tools merge --yes              # 校验通过后合并进 gold_bank.jsonl
"""
from __future__ import annotations

import argparse
import collections
import difflib
import json
import sys
from pathlib import Path

from .. import config

KNOWN_CATEGORIES = {"项目深挖", "RAG", "Agent", "LLM应用", "技术基础", "算法模型", "行为动机"}
# 现有题库里实际出现过的维度名（「结构」「量化与证据」是历史口径，一并认）
KNOWN_DIMS = {"覆盖度", "深度与取舍", "结构", "量化与证据"}
GOLD = "coach/eval/gold_bank.jsonl"
DRAFT = "coach/eval/gold_bank_draft.jsonl"


def gold_path() -> Path:
    return Path(config.ROOT) / GOLD


def draft_path() -> Path:
    return Path(config.ROOT) / DRAFT


def load_jsonl(p: Path) -> list[dict]:
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def build_draft() -> tuple[Path, list[dict]]:
    from .bank_drafts import build
    existing = load_jsonl(gold_path())
    recs = build(existing)
    p = draft_path()
    p.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in recs) + "\n", encoding="utf-8")
    return p, recs


def _norm(t: str) -> str:
    return "".join(ch for ch in t if ch.isalnum() or "\u4e00" <= ch <= "\u9fff").lower()


def validate(records: list[dict], existing: list[dict] | None = None) -> dict:
    """返回 {errors, warnings, stats}。errors 非空时**不允许合并**。"""
    existing = existing if existing is not None else load_jsonl(gold_path())
    errors: list[str] = []
    warnings: list[str] = []
    all_recs = list(existing) + list(records)

    # 1) qid 唯一
    cnt = collections.Counter(r["qid"] for r in all_recs)
    for qid, n in cnt.items():
        if n > 1:
            errors.append(f"qid 重复 {n} 次：{qid}")

    # 2) 题目文本唯一（精确 + 近似）
    texts = collections.Counter(_norm(r["text"]) for r in records)
    for t, n in texts.items():
        if n > 1:
            errors.append(f"草稿内部有重复题目：{t[:30]}")
    pool = {_norm(r["text"]) for r in existing}
    for r in records:
        nt = _norm(r["text"])
        if nt in pool:
            errors.append(f"与现有题库重复：{r['qid']} {r['text'][:30]}")
            continue
        close = difflib.get_close_matches(nt, pool, n=1, cutoff=0.85)
        if close:
            errors.append(f"与现有题库近似重复：{r['qid']} {r['text'][:30]}")

    # 3) rubric 完整性
    dim_counter = collections.Counter()
    kw_counter = collections.Counter()
    for r in all_recs:
        q = r["qid"]
        if r.get("category") not in KNOWN_CATEGORIES:
            errors.append(f"{q} 类别非法：{r.get('category')}")
        if not r.get("skills"):
            errors.append(f"{q} 缺少 skills")
        if not (8 <= len(r.get("text", "")) <= 120):
            errors.append(f"{q} 题目长度异常：{len(r.get('text',''))}")
        dims = r.get("rubric") or []
        if not dims:
            errors.append(f"{q} 没有评分维度")
        for d in dims:
            name = d.get("name", "")
            dim_counter[name] += 1
            if name not in KNOWN_DIMS:
                errors.append(f"{q} 维度名不在口径内：{name}")
            if not (d.get("weight") or 0) > 0:
                errors.append(f"{q} 维度权重非正数")
            kws = d.get("keywords") or []
            if not kws:
                errors.append(f"{q} 维度「{name}」没有任何关键词（该维度无法计分）")
            elif len(kws) < 2:
                # 现有题库里不少历史条目只有 1 个关键词，所以这算"可改进"而不是"错误" ——
                # 口径要和项目现状一致，否则校验器会把 100 条老题全判成错，反而失去意义
                warnings.append(f"{q} 维度「{name}」只有 1 个关键词，要点命中率会糙（建议 ≥2）")
            if len(set(kws)) != len(kws):
                warnings.append(f"{q} 维度「{name}」关键词有重复")
            for k in kws:
                kw_counter[k] += 1

    # 4) 关键词过泛：出现在 >15% 的题里，会系统性抬高要点命中率
    n_q = max(1, len(all_recs))
    for k, n in kw_counter.most_common():
        if n / n_q > 0.15:
            warnings.append(f"关键词「{k}」出现在 {n}/{n_q} 题里（>15%），过于泛化，建议换更具体的表述")

    # 5) 关键词组合完全相同的两题（可疑复制）
    sig = collections.Counter(
        "|".join(sorted(k for d in r.get("rubric") or [] for k in (d.get("keywords") or [])))
        for r in records)
    for s, n in sig.items():
        if n > 1 and s:
            warnings.append(f"有 {n} 道题的参考要点完全相同（疑似复制）：{s[:40]}")

    stats = {"records": len(records), "existing": len(existing), "total": len(all_recs),
             "by_category": dict(collections.Counter(r["category"] for r in all_recs)),
             "draft_by_category": dict(collections.Counter(r["category"] for r in records)),
             "dims": dict(dim_counter), "unique_keywords": len(kw_counter)}
    return {"errors": errors, "warnings": warnings, "stats": stats}


def format_report(res: dict) -> str:
    s = res["stats"]
    L = ["题库校验报告",
         f"  现有 {s['existing']} 条 + 草稿 {s['records']} 条 = {s['total']} 条",
         f"  草稿分布: {s['draft_by_category']}",
         f"  合并后分布: {s['by_category']}",
         f"  维度统计: {s['dims']}｜去重后关键词 {s['unique_keywords']} 个",
         f"  错误 {len(res['errors'])} 项｜警告 {len(res['warnings'])} 项"]
    for e in res["errors"][:20]:
        L.append("  [错误] " + e)
    if len(res["errors"]) > 20:
        L.append(f"  …还有 {len(res['errors']) - 20} 项错误")
    for w in res["warnings"][:12]:
        L.append("  [警告] " + w)
    if len(res["warnings"]) > 12:
        L.append(f"  …还有 {len(res['warnings']) - 12} 项警告")
    L.append("  结论: " + ("校验通过，可以合并且人工抽查" if not res["errors"] else "有错误，不允许合并"))
    return "\n".join(L)


def merge(yes: bool = False) -> dict:
    recs = load_jsonl(draft_path())
    if not recs:
        raise SystemExit("草稿池为空，先跑 build")
    res = validate(recs)
    if res["errors"]:
        print(format_report(res))
        raise SystemExit("校验未通过，拒绝合并")
    if not yes:
        print(format_report(res))
        raise SystemExit("校验通过。确认合并请加 --yes（会追加到 gold_bank.jsonl，不覆盖原有条目）")
    with gold_path().open("a", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return {"merged": len(recs), "total": len(load_jsonl(gold_path()))}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="题库工具：build / validate / merge")
    ap.add_argument("cmd", choices=["build", "validate", "merge"])
    ap.add_argument("file", nargs="?", default=None)
    ap.add_argument("--yes", action="store_true")
    a = ap.parse_args(list(sys.argv[1:]) if argv is None else list(argv))
    if a.cmd == "build":
        p, recs = build_draft()
        print(f"草稿池已写出 {len(recs)} 条 -> {p}")
        return 0
    if a.cmd == "validate":
        p = Path(a.file) if a.file else draft_path()
        res = validate(load_jsonl(p))
        print(format_report(res))
        (Path(config.RESULTS_DIR)).mkdir(parents=True, exist_ok=True)
        (Path(config.RESULTS_DIR) / "bank_validation.md").write_text(
            "# 题库校验\n\n```\n" + format_report(res) + "\n```\n", encoding="utf-8")
        return 1 if res["errors"] else 0
    print(json.dumps(merge(a.yes), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
