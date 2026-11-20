#!/bin/bash
# 双击启动 B2B 多平台企业数据自动采集工具（Mac）
#
# 解释器怎么找（2026-11-20 交接复核时改）：这里原先写死了一台开发机的绝对路径
# （/Users/.../PythonDemo/.venv/bin/python）。换一台 Mac、换一个用户名就找不到，脚本退到
# 系统 python3 —— 那里没装 PyQt5，双击的表现是「窗口一闪就没了」，真正的原因只在终端里
# 看得见，而且报的是 import error 而不是「环境没建」。本仓库第 25 道门
# （check_no_dev_machine_paths）立的正是这条规矩，但那时只扫交付链，没扫这两个双击入口。
#
# 现在两件事一起做：①按「脚本所在目录的上一级 /.venv」找，不认任何一台机器；
# ②找到还不算，先探一次 import PyQt5 —— 「解释器在位」与「这个包能跑」是两格，
# 混用就会把「环境没建好」报成「程序坏了」。探不过就换下一个候选，全失败说清怎么修。
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE" || { echo "❌ 进不去脚本所在目录：$HERE"; exit 1; }

for PY in "$HERE/../.venv/bin/python" "$(command -v python3)"; do
    [ -n "$PY" ] && [ -x "$PY" ] || continue
    if "$PY" -c "import PyQt5" >/dev/null 2>&1; then
        exec "$PY" b2bcollect_gui.py
    fi
done

echo "❌ 没找到「装了 PyQt5 的 Python」，所以工具起不来（不是程序坏了，是本机环境还没建）。"
echo "   在仓库根目录跑这两条就能建好，建完再双击本文件："
echo "     python3 -m venv .venv"
echo "     .venv/bin/pip install -r .github/requirements/b2bcollect.txt"
echo "   要打包成本机 .app 的话：bash debug/build_b2bcollect_mac_local.sh"
exit 1
