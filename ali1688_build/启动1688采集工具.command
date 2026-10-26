#!/bin/bash
# 双击启动 1688 企业数据自动采集工具（Mac）
cd "$(dirname "$0")"
PY="/Users/jhnoplan/Desktop/PythonDemo/.venv/bin/python"
[ -x "$PY" ] || PY="python3"
exec "$PY" ali1688_gui.py
