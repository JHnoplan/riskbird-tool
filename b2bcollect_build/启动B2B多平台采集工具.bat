@echo off
rem 双击启动 B2B 多平台企业数据自动采集工具（Windows，从源码运行）
chcp 65001 >nul
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
    echo 未检测到 Python，请先安装 Python 3.11 并勾选 Add to PATH。
    pause
    exit /b 1
)
python b2bcollect_gui.py
if errorlevel 1 pause
