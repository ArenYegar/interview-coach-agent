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
# 模型降级链：第一个不可用（模型不存在/无权限）时自动换下一个
DEFAULT_MODEL_CHAIN = ("qwen-plus", "qwen-max", "qwen-turbo")

TEMPERATURE = float(os.getenv("COACH_TEMPERATURE", "0.3"))
MAX_TOKENS = int(os.getenv("COACH_MAX_TOKENS", "900"))
RETRIES = int(os.getenv("COACH_RETRIES", "3"))
TIMEOUT = float(os.getenv("COACH_TIMEOUT", "60"))
MAX_QUESTIONS = int(os.getenv("COACH_MAX_QUESTIONS", "5"))


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
        return [forced]
    return list(DEFAULT_MODEL_CHAIN)


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
