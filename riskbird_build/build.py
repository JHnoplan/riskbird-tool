# -*- coding: utf-8 -*-
"""
build.py - 美亚风鸟采集工具 一键打包脚本
在 Windows 上运行: python build.py
"""

import os
import sys
import subprocess
import shutil
import time

# 颜色输出
def info(msg):
    print(f"\n[INFO] {msg}")

def ok(msg):
    print(f"[OK]   {msg}")

def warn(msg):
    print(f"[WARN] {msg}")

def err(msg):
    print(f"[ERR]  {msg}")

def banner():
    print("=" * 55)
    print("  美亚风鸟企业数据采集工具 - 一键打包")
    print("  自动安装依赖 + 打包成 exe + 生成安装程序")
    print("=" * 55)

# ── 第1步：检查 Python ──────────────────────
def check_python():
    info("第1步: 检查 Python 环境")
    try:
        ver = subprocess.check_output(["python", "--version"], stderr=subprocess.STDOUT).decode().strip()
        ok(f"Python 已安装: {ver}")
        return True
    except Exception:
        pass
    try:
        ver = subprocess.check_output(["python3", "--version"], stderr=subprocess.STDOUT).decode().strip()
        ok(f"Python3 已安装: {ver}")
        return True
    except Exception:
        pass

    err("未检测到 Python!")
    print()
    print("请手动安装 Python 3.11:")
    print("  1. 打开浏览器访问: https://www.python.org/downloads/")
    print("  2. 下载 Python 3.11")
    print("  3. 安装时务必勾选 'Add Python to PATH'")
    print("  4. 安装完成后重新运行此脚本")
    print()
    input("按 Enter 退出...")
    return False

# ── 第2步：安装依赖 ─────────────────────────
def install_deps():
    info("第2步: 安装 Python 依赖 (使用国内镜像加速)")
    deps = ["PyQt5", "selenium", "openpyxl", "pyinstaller"]

    # 国内镜像列表（按优先级）
    mirrors = [
        "https://pypi.tuna.tsinghua.edu.cn/simple",
        "https://mirrors.aliyun.com/pypi/simple",
        "https://pypi.douban.com/simple",
    ]

    success = False
    for mirror in mirrors:
        print(f"  尝试镜像: {mirror}")
        cmd = [
            sys.executable, "-m", "pip", "install",
        ] + deps + [
            "-q",
            "-i", mirror,
            "--trusted-host", mirror.split("//")[1].split("/")[0],
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            success = True
            break
        print(f"  该镜像失败，切换下一个...")

    if not success:
        err("所有镜像均安装失败，请检查网络连接")
        if result.stderr:
            print(f"  错误信息: {result.stderr[:300]}")
        input("按 Enter 退出...")
        return False

    ok("依赖安装完成")

    # 验证关键依赖
    for mod in ["PyQt5", "selenium", "openpyxl"]:
        try:
            __import__(mod)
        except ImportError:
            err(f"{mod} 安装失败，请手动执行: pip install {mod}")
            input("按 Enter 退出...")
            return False

    return True

# ── 第3步：PyInstaller 打包 ─────────────────
def build_exe():
    info("第3步: 打包成 exe (可能需要 3-5 分钟)")

    # 清理旧文件
    for d in ["build", "dist"]:
        if os.path.isdir(d):
            shutil.rmtree(d)
    for f in os.listdir("."):
        if f.endswith(".spec"):
            os.remove(f)

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--onefile",
        "--windowed",
        "--name", "RiskBirdTool",
        "--hidden-import", "PyQt5",
        "--hidden-import", "PyQt5.QtWidgets",
        "--hidden-import", "PyQt5.QtCore",
        "--hidden-import", "PyQt5.QtGui",
        "--hidden-import", "PyQt5.sip",
        "--hidden-import", "openpyxl",
        "--hidden-import", "openpyxl.cell",
        "--hidden-import", "openpyxl.styles",
        "--hidden-import", "selenium",
        "--hidden-import", "selenium.webdriver",
        "--hidden-import", "selenium.webdriver.chrome",
        "--hidden-import", "selenium.webdriver.chrome.service",
        "--hidden-import", "selenium.webdriver.common",
        "--hidden-import", "selenium.webdriver.common.by",
        "--hidden-import", "selenium.webdriver.support",
        "--hidden-import", "selenium.webdriver.support.ui",
        "--hidden-import", "selenium.webdriver.support.expected_conditions",
        "--hidden-import", "selenium.common",
        "--hidden-import", "selenium.common.exceptions",
        "--hidden-import", "customer_library_cleaner",
        "--hidden-import", "dual_session",
        "--hidden-import", "block_guard",
        "--hidden-import", "crawl_checkpoint",
        "--hidden-import", "requests",
        "--hidden-import", "websocket",
        "--collect-all", "selenium",
        "--collect-all", "PyQt5",
        "riskbird_gui.py"
    ]

    print("  正在打包，请稍候...")
    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        err("打包失败!")
        print(result.stderr[-500:] if result.stderr else "未知错误")
        input("按 Enter 退出...")
        return False

    # 检查输出
    exe_name = "RiskBirdTool.exe"
    exe_path = os.path.join("dist", exe_name)
    if not os.path.exists(exe_path):
        err(f"未找到生成的 exe: {exe_path}")
        input("按 Enter 退出...")
        return False

    size_mb = os.path.getsize(exe_path) / (1024 * 1024)
    ok(f"exe 打包成功! 大小: {size_mb:.1f} MB")
    return True

# ── 第4步：创建发布包 ───────────────────────
def create_release():
    info("第4步: 创建发布包")

    release_dir = "release"
    if os.path.isdir(release_dir):
        shutil.rmtree(release_dir)
    os.makedirs(release_dir)

    # 复制 exe
    exe_src = os.path.join("dist", "RiskBirdTool.exe")
    exe_dst = os.path.join(release_dir, "RiskBirdTool.exe")
    shutil.copy2(exe_src, exe_dst)

    # 创建使用说明
    readme = """美亚风鸟企业数据采集工具 - 使用说明
====================================

【运行前准备】
1. 确保电脑已安装 Google Chrome 浏览器
   下载地址: https://www.google.com/chrome/

2. 首次运行需要联网 (下载 ChromeDriver)

【使用方法】
1. 双击 "RiskBirdTool.exe" 启动程序
2. 点击右上角 "登录" 按钮，登录美亚风鸟账号
3. 点击 "选择文件" 选择包含公司名的 Excel 文件
4. 点击 "开始采集" 开始自动采集
5. 采集完成后点击 "导出 Excel" 保存结果

【注意事项】
- 程序会自动保存登录信息，下次无需重新登录
- 建议设置请求间隔为 3-5 秒，避免被限速
- 如遇到验证码，程序会提示手动处理
- 采集过程中可随时点击 "停止采集"

【文件说明】
- riskbird_data/ 文件夹: 保存登录Cookie (不要删除)
- 导出的 Excel 文件: 包含所有采集的企业数据

【技术支持】
如有问题请联系开发者
"""
    with open(os.path.join(release_dir, "使用说明.txt"), "w", encoding="utf-8") as f:
        f.write(readme)

    ok("发布包创建完成")
    return True

# ── 第5步：NSIS 安装程序 ────────────────────
def build_installer():
    info("第5步: 检查 NSIS 安装程序编译器")

    nsis_paths = [
        r"C:\Program Files (x86)\NSIS\makensis.exe",
        r"C:\Program Files\NSIS\makensis.exe",
    ]
    nsis_exe = None
    for p in nsis_paths:
        if os.path.exists(p):
            nsis_exe = p
            break

    if not nsis_exe:
        warn("未找到 NSIS，跳过安装程序生成")
        print("  如需生成安装程序，请安装 NSIS:")
        print("  https://nsis.sourceforge.io/Download")
        print("  安装后重新运行此脚本")
        return True

    ok(f"NSIS 已找到: {nsis_exe}")

    # 创建 license.txt
    with open("license.txt", "w", encoding="utf-8") as f:
        f.write("美亚风鸟企业数据采集工具 用户许可协议\n\n")
        f.write("本软件仅供合法用途使用。\n")
        f.write("使用本软件即表示您同意遵守相关网站的服务条款。\n")
        f.write("开发者不对因使用本软件产生的任何问题负责。\n")

    print("  正在生成安装程序...")
    result = subprocess.run([nsis_exe, "installer.nsi"], capture_output=True, text=True)

    setup_name = "setup_RiskBirdTool.exe"
    if result.returncode == 0 and os.path.exists(setup_name):
        shutil.move(setup_name, os.path.join("release", setup_name))
        size_mb = os.path.getsize(os.path.join("release", setup_name)) / (1024 * 1024)
        ok(f"安装程序生成成功! 大小: {size_mb:.1f} MB")
    else:
        warn("NSIS 编译失败，将只提供绿色版")
        if result.stderr:
            print(f"  {result.stderr[-200:]}")

    return True

# ── 主流程 ──────────────────────────────────
def main():
    banner()
    print()

    steps = [
        ("检查 Python", check_python),
        ("安装依赖", install_deps),
        ("打包 exe", build_exe),
        ("创建发布包", create_release),
        ("生成安装程序", build_installer),
    ]

    for name, func in steps:
        if not func():
            err(f"步骤 '{name}' 失败，打包中止")
            return
        print()

    # 完成
    print("=" * 55)
    print("  打包完成!")
    print("=" * 55)
    print()
    print("  生成的文件在 release/ 文件夹中:")
    print("  - RiskBirdTool.exe          (绿色版，双击即用)")
    print("  - setup_RiskBirdTool.exe    (安装版，如有)")
    print("  - 使用说明.txt")
    print()
    print("  将 release 文件夹分发给用户即可!")
    print("  用户电脑需要安装 Google Chrome 浏览器")
    print()

    # 打开文件夹
    release_path = os.path.abspath("release")
    os.startfile(release_path)

    input("按 Enter 退出...")

if __name__ == "__main__":
    main()
