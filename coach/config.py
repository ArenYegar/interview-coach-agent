"""全局配置：路径、模型接入、运行开关。

原则：密钥只从环境变量（或显式指定的 .env 文件）读取，绝不写入仓库。
"""
from __future__ import annotations

import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNS_DIR = ROOT / "runs"
RESULTS_DIR = ROOT / "results"
BANK_PATH = ROOT / "coach" / "eval" / "gold_bank.jsonl"
DB_PATH = RUNS_DIR / "coach.db"

# 按优先级探测可用的 API Key 环境变量名（只读名字，不打印值）
API_KEY_ENV_NAMES = ("COACH_API_KEY", "DASHSCOPE_API_KEY", "QWEN_API_KEY",
                     "DEEPSEEK_API_KEY", "OPENAI_API_KEY")
DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEEPSEEK_BASE_URL = "https://api.deepseek.com"

# 模型降级链：第一个不可用（模型不存在/无权限）时自动换下一个。
# 按端点判断供应商——换端点却不换模型名（拿 DeepSeek 端点请求 qwen-*）是最常见的错配，这里挡掉。
MODEL_CHAINS = {
    # 2026-09-20 实测：本机 Key 的端点上 deepseek-v4-flash / deepseek-chat 都解析到 deepseek-flash，
    # deepseek-v4-pro 是推理型（可能把 token 预算全用在 reasoning 上而返回空 content），故不作默认。
    "deepseek": ("deepseek-flash", "deepseek-chat"),
    "dashscope": ("qwen-plus", "qwen-max", "qwen-turbo"),
}
DEFAULT_MODEL_CHAIN = MODEL_CHAINS["dashscope"]      # 保留这个名字，向后兼容


def provider(base: str | None = None) -> str:
    """按 base_url 判断供应商（只看域名关键字，不发网络请求）。"""
    url = (base or base_url() or "").lower()
    if "deepseek" in url:
        return "deepseek"
    if "dashscope" in url or "aliyuncs" in url:
        return "dashscope"
    return "unknown"

# 下面这几个参数原先在 import 时就取值（快照），导致 .env 里写的 COACH_MAX_TOKENS / COACH_TEMPERATURE
# / COACH_MAX_QUESTIONS 全部失效——因为 import 发生在 load_env_file 之前。改成"每次调用现读"，
# 同时保留同名常量（供旧代码引用，取到的是 import 时的快照，不要再用于新代码）。
def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def temperature() -> float:
    return _env_float("COACH_TEMPERATURE", 0.3)


def seed() -> int | None:
    """可选随机种子（COACH_SEED）。OpenAI 兼容服务端对 seed 是"尽力而为"，
    所以它只作为"降低抖动"的一档，不能当成确定性保证 —— 到底有没有用要用探针测。"""
    v = os.getenv("COACH_SEED")
    if not v:
        return None
    try:
        return int(v)
    except ValueError:
        return None


def max_tokens() -> int:
    return _env_int("COACH_MAX_TOKENS", 900)


def retries() -> int:
    return _env_int("COACH_RETRIES", 3)


def timeout() -> float:
    return _env_float("COACH_TIMEOUT", 60)


def api_token() -> str:
    """可选的接口门禁：设了 COACH_API_TOKEN 后 /api/* 与 /report/* 必须带 Bearer Token。

    这是**门禁**不是多租户：单一 token 只回答"谁可以访问"，回答不了"这些场次属于谁"。
    真正的多租户要 per-user token + 会话归属校验 + 配额，属于后续（文档已写明）。
    """
    return os.getenv("COACH_API_TOKEN", "").strip()


def hitl() -> bool:
    """人工确认闸门：COACH_HITL=1 时，弱点画像先进 pending，人工确认后才写库（见 coach/review.py）。"""
    return os.getenv("COACH_HITL", "0") == "1"


def score_reps() -> int:
    """每题的模型采样次数（COACH_SCORE_REPS，默认 1）。

    为什么需要：实测同一份输入重复打分，单题极差常态在 10pp 量级，而且 temperature=0 也消不掉
    （见 results/stability_*.json）。既然调参数没用，就把"单次偶发测量"换成"多次取中位数"的估计。
    成本线性上升，所以默认 1（练习够用），评测/回归跑时才开 3。
    """
    return max(1, min(5, _env_int("COACH_SCORE_REPS", 1)))


def max_questions() -> int:
    return _env_int("COACH_MAX_QUESTIONS", 5)


TEMPERATURE = _env_float("COACH_TEMPERATURE", 0.3)      # 兼容旧引用（import 时快照）
MAX_TOKENS = _env_int("COACH_MAX_TOKENS", 900)          # 兼容旧引用（import 时快照）
RETRIES = _env_int("COACH_RETRIES", 3)
TIMEOUT = _env_float("COACH_TIMEOUT", 60)
MAX_QUESTIONS = _env_int("COACH_MAX_QUESTIONS", 5)      # 兼容旧引用（import 时快照）


def load_env_file(path: str | os.PathLike | None) -> int:
    """极简 .env 解析（不引入额外依赖）。已存在的环境变量优先，不覆盖。"""
    if not path:
        return 0
    p = Path(path)
    if not p.exists():
        return 0
    loaded = 0
    for raw in p.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v
            loaded += 1
    return loaded


def api_key() -> str | None:
    for name in API_KEY_ENV_NAMES:
        v = os.getenv(name)
        if v and v.strip():
            return v.strip()
    return None


def base_url() -> str:
    return (os.getenv("COACH_BASE_URL") or os.getenv("BASE_URL") or DEFAULT_BASE_URL).rstrip("/")


def model_chain() -> list[str]:
    forced = os.getenv("COACH_MODEL")
    if forced:
        return [m.strip() for m in forced.split(",") if m.strip()]
    return list(MODEL_CHAINS.get(provider(), DEFAULT_MODEL_CHAIN))


def provider_summary() -> str:
    """一行说明"端点 + 模型链"，启动时打印，避免"以为在用 A 其实在用 B"。"""
    return f"{provider()} | 端点 {base_url()} | 模型链 {' → '.join(model_chain())}"


def offline() -> bool:
    """离线模式：显式开关打开，或没有任何可用的 API Key。"""
    if os.getenv("COACH_OFFLINE", "0") == "1":
        return True
    return api_key() is None


def ensure_dirs() -> None:
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)


PII_PATTERNS = (
    (re.compile(r"1[3-9]\d{9}"), lambda m: m.group(0)[:3] + "****" + m.group(0)[-2:]),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"), lambda m: m.group(0)[:2] + "***@***"),
)


def redact(text: str, keep: int = 200) -> str:
    """trace / 日志落盘前脱敏：掩码手机号与邮箱，并截断。"""
    if text is None:
        return ""
    s = str(text)
    for pat, fn in PII_PATTERNS:
        s = pat.sub(fn, s)
    s = " ".join(s.split())
    return s if len(s) <= keep else s[:keep] + f"...(+{len(s) - keep})"
