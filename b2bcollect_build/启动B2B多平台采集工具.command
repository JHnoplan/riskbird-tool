#!/bin/bash
# 双击启动 B2B 多平台企业数据自动采集工具（Mac）
cd "$(dirname "$0")"
PY="/Users/jhnoplan/Desktop/PythonDemo/.venv/bin/python"
[ -x "$PY" ] || PY="python3"
exec "$PY" b2bcollect_gui.py
