"""检索层：bge 稠密向量 + 字符 n-gram BM25 + RRF 融合，用于"选题"打分。

设计要点（面试可讲）：
  1. 零下载：优先复用本机已有的 bge-large-zh-v1.5 快照；找不到就退化为纯关键词模式，不阻塞主流程。
  2. 中文不依赖分词器：BM25 用"字符二元组 + 英文词"切词，避免额外依赖，短文本上表现稳定。
  3. 融合用 RRF（按名次而非分数），因为稠密相似度与 BM25 分数不可比。
"""
from __future__ import annotations

import glob
import os
import re
from functools import lru_cache
from typing import Iterable

import numpy as np

from . import config
from .schemas import JDProfile, Question

RRF_K = 60
_ASCII = re.compile(r"[A-Za-z][A-Za-z0-9+#._/-]{1,20}")


def _tok(text: str) -> list[str]:
    """中文按字符二元组、英文按词切分（不依赖 jieba）。"""
    t = re.sub(r"\s+", "", text or "")
    toks = [w.lower() for w in _ASCII.findall(text or "")]
    zh = re.sub(r"[^\u4e00-\u9fa5]", "", t)
    toks += [zh[i:i + 2] for i in range(max(0, len(zh) - 1))]
    if len(zh) == 1:
        toks.append(zh)
    return toks


@lru_cache(maxsize=4)
def resolve_embed_model() -> str | None:
    """按优先级挑一个本机已存在的句向量模型目录（避免下载）。"""
    explicit = os.getenv("COACH_EMBED_MODEL")
    if explicit and os.path.isdir(explicit):
        return explicit
    patterns = [
        r"E:\\Kaggle\\LLM\\Depression_RAG_OA\\models\\embeddings\\models--BAAI--bge-large-zh-v1.5\\snapshots\\*",
        os.path.join(os.path.expanduser("~"), ".cache", "huggingface", "hub",
                     "models--moka-ai--m3e-base", "snapshots", "*"),
        os.path.join(os.path.expanduser("~"), ".cache", "huggingface", "hub",
                     "models--BAAI--bge-small-en-v1.5", "snapshots", "*"),
    ]
    for pat in patterns:
        for p in sorted(glob.glob(pat)):
            if os.path.isfile(os.path.join(p, "config.json")):
                return p
    return None


class Embedder:
    """句向量封装：加载失败不抛错，available=False 时上层自动走关键词模式。"""

    def __init__(self, model_path: str | None = None) -> None:
        self.error = ""
        self.available = False
        self.backend = "none"
        path = model_path or resolve_embed_model()
        if not path:
            self.error = "本机未找到可用的句向量模型（可设 COACH_EMBED_MODEL 指定目录）"
            return
        try:
            from sentence_transformers import SentenceTransformer
            self.model = SentenceTransformer(path, device="cpu")
            self.model_path = path
            self.dim = self.model.get_sentence_embedding_dimension()
            self.backend = "sentence-transformers"
            self.available = True
        except Exception as e:
            st_err = f"{type(e).__name__}: {e}"
            # 兜底：transformers 直接加载 + 平均池化（sentence-transformers 版本不兼容时很常见）
            try:
                import torch
                from transformers import AutoModel, AutoTokenizer
                self.tok = AutoTokenizer.from_pretrained(path)
                self.hf = AutoModel.from_pretrained(path)
                self.hf.eval()
                self.model_path = path
                self.dim = int(self.hf.config.hidden_size)
                self.backend = "transformers+meanpool"
                self.available = True
                self._torch = torch
                self.error = f"（已回退到 transformers：{st_err[:80]}）"
            except Exception as e2:
                self.error = f"ST 失败:{st_err[:80]} | transformers 失败:{type(e2).__name__}: {e2}"
                return

    def encode(self, texts: Iterable[str], is_query: bool = False) -> np.ndarray:
        texts = list(texts)
        if not self.available:
            raise RuntimeError(self.error)
        # bge 系列检索场景建议加指令前缀（中文官方推荐）
        if is_query and "bge" in (self.model_path or "").lower():
            texts = [f"为这个句子生成表示以用于检索相关文章：{t}" for t in texts]
        if self.backend == "sentence-transformers":
            vecs = self.model.encode(texts, normalize_embeddings=True, batch_size=16,
                                     show_progress_bar=False)
            return np.asarray(vecs, dtype="float32")
        # transformers + 注意力掩码平均池化 + L2 归一化
        torch = self._torch
        out = []
        with torch.no_grad():
            for i in range(0, len(texts), 16):
                batch = texts[i:i + 16]
                enc = self.tok(batch, padding=True, truncation=True, max_length=256,
                               return_tensors="pt")
                hidden = self.hf(**enc).last_hidden_state
                mask = enc["attention_mask"].unsqueeze(-1).float()
                pooled = (hidden * mask).sum(1) / mask.sum(1).clamp(min=1e-6)
                out.append(torch.nn.functional.normalize(pooled, p=2, dim=1).numpy())
        return np.vstack(out).astype("float32")


class HybridSelector:
    """对题库做混合检索：稠密(余弦) + 稀疏(BM25) → RRF 融合。"""

    def __init__(self, bank: list[Question], embedder: Embedder | None = None) -> None:
        self.bank = list(bank)
        self.embedder = embedder if embedder is not None else Embedder()
        self.docs = [self._doc_text(q) for q in self.bank]
        self._dense = None
        try:
            from rank_bm25 import BM25Okapi
            self.bm25 = BM25Okapi([_tok(d) for d in self.docs])
        except Exception:
            self.bm25 = None

    @staticmethod
    def _doc_text(q: Question) -> str:
        return f"{q.text} {' '.join(q.skills)} {' '.join(k for d in q.rubric.dims for k in d.keywords)}"

    @property
    def mode(self) -> str:
        parts = ["dense" if self.embedder.available else "dense:off",
                 "bm25" if self.bm25 else "bm25:off"]
        return "+".join(parts)

    def _dense_ranks(self, query: str) -> list[int]:
        if not self.embedder.available:
            return []
        if self._dense is None:
            self._dense = self.embedder.encode(self.docs)
        qv = self.embedder.encode([query], is_query=True)[0]
        sims = self._dense @ qv
        return list(np.argsort(-sims))

    def _bm25_ranks(self, query: str) -> list[int]:
        if self.bm25 is None:
            return []
        scores = self.bm25.get_scores(_tok(query))
        return list(np.argsort(-np.asarray(scores)))

    def score(self, query: str) -> dict[str, float]:
        """返回 qid -> RRF 分数（越大越相关）。"""
        out: dict[str, float] = {}
        for ranks in (self._dense_ranks(query), self._bm25_ranks(query)):
            for rank, idx in enumerate(ranks):
                qid = self.bank[idx].qid
                out[qid] = out.get(qid, 0.0) + 1.0 / (RRF_K + rank + 1)
        return out

    def rank(self, query: str) -> list[tuple[float, Question]]:
        sc = self.score(query)
        return sorted(((s, self.bank[i]) for i, s in ((i, sc.get(q.qid, 0.0))
                      for i, q in enumerate(self.bank))), key=lambda x: (-x[0], x[1].qid))


def build_selector(bank: list[Question]) -> HybridSelector:
    return HybridSelector(bank)
