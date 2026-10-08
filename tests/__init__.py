"""测试包的统一环境隔离。

必须在任何测试模块导入 coach 之前生效，所以要写在这里而不是各测试的 setUp：

1) **默认关闭打分缓存**（COACH_CACHE=0）。踩过的坑：缓存按"模型+提示版本+防护档+采样次数+题+答案"
   做键并落在 runs/score_cache.json，于是**跨测试、跨运行**都会命中上一次的结果 ——
   表现为"单个测试跑得过、一起跑就挂"，而且看代码完全正常。缓存必须让测试默认关掉。
2) 缓存文件指向临时目录（COACH_CACHE_PATH），避免污染真实运行的缓存。
"""
import os
import tempfile

# 注意：用 `python -m unittest discover -s tests` 时本文件不一定被导入（取决于是否按包导入），
# 所以真正的兜底是 coach/cache.py 里"缓存默认关闭"。这里只是双保险 + 隔离缓存文件位置。
os.environ["COACH_CACHE"] = "0"
os.environ["COACH_CACHE_PATH"] = os.path.join(
    tempfile.mkdtemp(prefix="coach_test_cache_"), "score_cache.json")
