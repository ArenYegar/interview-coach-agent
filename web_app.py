"""Web 界面启动器：一条命令起服务（自动读 .env，省得每次手动注入密钥）。

  python web_app.py                # 默认 http://127.0.0.1:8010
  python web_app.py --port 8000    # 换端口
  ICA_NO_BROWSER=1 python web_app.py
"""
from __future__ import annotations

import argparse
import os
import sys
import threading
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from coach import config  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="interview-coach-agent Web 界面启动器")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=int(os.getenv("ICA_PORT", "8010")))
    ap.add_argument("--env-file", default=str(ROOT / ".env"))
    ap.add_argument("--no-open", action="store_true", help="不自动打开浏览器")
    a = ap.parse_args()

    loaded = config.load_env_file(a.env_file)
    print(f"[env] 从 {a.env_file} 载入变量 {loaded} 个 | 模式: "
          f"{'离线(规则打分)' if config.offline() else '在线(真实模型)'}")
    print(f"[模型] {config.provider_summary()}")
    url = f"http://{a.host}:{a.port}/"
    print(f"[web] 界面地址: {url}    （Ctrl+C 停止）")

    if not a.no_open and not os.getenv("ICA_NO_BROWSER"):
        def _open():
            time.sleep(1.5)
            try:
                webbrowser.open(url)
            except Exception:
                pass
        threading.Thread(target=_open, daemon=True).start()

    import uvicorn
    uvicorn.run("api:app", host=a.host, port=a.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
