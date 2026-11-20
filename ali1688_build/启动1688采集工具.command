#!/bin/bash
# 双击启动 1688 企业数据自动采集工具（Mac）
#
# 与 b2bcollect_build 那份同一个修法（2026-11-20 交接复核）：原先写死一台开发机的绝对
# 路径，换机器就退到没装 PyQt5 的系统 python3，表现为「窗口一闪就没」。按脚本所在目录
# 的上一级找 .venv，并且探一次 import PyQt5 才算数——「解释器在位」不等于「这个包能跑」。
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE" || { echo "❌ 进不去脚本所在目录：$HERE"; exit 1; }

for PY in "$HERE/../.venv/bin/python" "$(command -v python3)"; do
    [ -n "$PY" ] && [ -x "$PY" ] || continue
    if "$PY" -c "import PyQt5" >/dev/null 2>&1; then
        exec "$PY" ali1688_gui.py
    fi
done

echo "❌ 没找到「装了 PyQt5 的 Python」，所以工具起不来（不是程序坏了，是本机环境还没建）。"
echo "   在仓库根目录跑这两条就能建好，建完再双击本文件："
echo "     python3 -m venv .venv"
echo "     .venv/bin/pip install -r .github/requirements/build-1688.txt"
exit 1
