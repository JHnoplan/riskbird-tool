# -*- coding: utf-8 -*-
"""
ADB 设备控制层（纯 adb 命令，不依赖手机端额外 agent）
========================================================

设计要点：
- 所有交互走标准 adb（tap/swipe/keyevent/screencap/uiautomator dump），
  天然适配任意分辨率与机型（坐标来自控件 bounds，而非固定像素）。
- 中文输入依赖 ADBKeyboard 输入法（业界标准方案）：
  先 `am broadcast -a ADB_INPUT_TEXT --es msg "<文本>"`。数字/英文用 `input text` 即可。
- 本层不含任何业务逻辑，可被 mock，便于离线单测。
"""

from __future__ import annotations

import re
import shutil
import subprocess
import time
from typing import List, Optional, Tuple


class ADBError(Exception):
    pass


class ADBController:
    def __init__(self, adb_path: Optional[str] = None, serial: Optional[str] = None):
        self.adb = adb_path or shutil.which("adb") or "adb"
        self.serial = serial  # 指定设备序列号（多设备时用 -s）

    # --------------------------- 基础执行 ---------------------------
    def _base(self) -> List[str]:
        cmd = [self.adb]
        if self.serial:
            cmd += ["-s", self.serial]
        return cmd

    def run(self, args: List[str], timeout: int = 15) -> str:
        """执行 adb 子命令，返回 stdout（文本）。"""
        try:
            r = subprocess.run(self._base() + args, capture_output=True, text=True,
                               timeout=timeout)
        except subprocess.TimeoutExpired:
            raise ADBError(f"adb 命令超时：{' '.join(args)}")
        if r.returncode != 0:
            raise ADBError((r.stderr or r.stdout or "adb 命令失败").strip().splitlines()[:1])
        return (r.stdout or "").strip()

    def run_bytes(self, args: List[str], timeout: int = 20) -> bytes:
        """执行 adb 子命令，返回 stdout（二进制，用于 screencap）。"""
        try:
            r = subprocess.run(self._base() + args, capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise ADBError(f"adb 命令超时：{' '.join(args)}")
        if r.returncode != 0:
            raise ADBError((r.stderr or b"").decode("utf-8", "ignore").splitlines()[:1])
        return r.stdout or b""

    def shell(self, cmd: str, timeout: int = 15) -> str:
        return self.run(["shell", cmd], timeout=timeout)

    # --------------------------- 设备 ---------------------------
    def list_devices(self) -> List[str]:
        """返回状态为 device 的序列号列表。"""
        try:
            out = subprocess.run([self.adb, "devices"], capture_output=True, text=True,
                                 timeout=10).stdout
        except subprocess.TimeoutExpired:
            return []
        devs = []
        for line in out.splitlines()[1:]:
            parts = line.strip().split("\t")
            if len(parts) == 2 and parts[1] == "device":
                devs.append(parts[0])
        return devs

    def is_connected(self) -> bool:
        devs = self.list_devices()
        if not devs:
            return False
        if self.serial and self.serial in devs:
            return True
        if not self.serial:
            self.serial = devs[0]  # 默认取第一台
        return self.serial in devs

    def screen_size(self) -> Tuple[int, int]:
        """wm size -> (w, h)。"""
        out = self.shell("wm size")
        m = re.findall(r"(\d+)x(\d+)", out)
        if not m:
            raise ADBError("无法获取屏幕分辨率")
        w, h = map(int, m[-1])
        return w, h

    # --------------------------- 交互 ---------------------------
    def tap(self, x: int, y: int):
        self.shell(f"input tap {int(x)} {int(y)}")

    def swipe(self, x1, y1, x2, y2, duration=400):
        self.shell(f"input swipe {int(x1)} {int(y1)} {int(x2)} {int(y2)} {duration}")

    def press_back(self):
        self.shell("input keyevent KEYCODE_BACK")

    def press_enter(self):
        self.shell("input keyevent KEYCODE_ENTER")

    def input_text(self, text: str):
        """输入数字/英文（空格转 %%）。中文请用 input_chinese。"""
        safe = text.replace(" ", "%s")
        # input text 对特殊字符敏感，仅用于 ASCII
        self.shell(f"input text '{safe}'")

    def input_chinese(self, text: str) -> bool:
        """通过 ADBKeyboard 广播输入中文/任意文本。

        返回 True 表示广播已发出（需手机已启用 ADBKeyboard 输入法才会生效）。
        """
        b64 = _shquote(text)
        # ADBKeyboard 支持 --es msg 直接文本；为兼容引号，走 shell 转义变体
        out = self.shell(f"am broadcast -a ADB_INPUT_TEXT --es msg {b64}")
        return "Broadcast completed" in out or out == ""

    def set_adb_keyboard(self, enabled: bool = True) -> bool:
        """切换/恢复输入法。enabled=True 切到 ADBKeyboard，False 切回原输入法。"""
        if enabled:
            self.shell("ime enable com.android.adbkeyboard/.AdbIME")
            out = self.shell("ime set com.android.adbkeyboard/.AdbIME")
            return "Error" not in out
        return True

    def current_ime(self) -> str:
        return self.shell("settings get secure default_input_method")

    # --------------------------- 截图 / 控件树 ---------------------------
    def screencap_png(self) -> bytes:
        """exec-out screencap 直接取 PNG 字节（不落盘、不 pull，最快）。"""
        return self.run_bytes(["exec-out", "screencap", "-p"])

    def uiautomator_dump(self) -> str:
        """dump 控件树 XML。优先 exec-out，回退到写文件再 cat。"""
        try:
            out = self.run_bytes(["exec-out", "uiautomator", "dump", "/dev/tty"])
            txt = out.decode("utf-8", "ignore")
            if "<hierarchy" in txt:
                return txt[txt.index("<?xml") if "<?xml" in txt else txt.index("<hierarchy"):]
        except ADBError:
            pass
        self.shell("uiautomator dump /sdcard/ui.xml")
        xml = self.run_bytes(["exec-out", "cat", "/sdcard/ui.xml"]).decode("utf-8", "ignore")
        return xml


# ---------------------------------------------------------------------------
def _shquote(text: str) -> str:
    # ADBKeyboard 的 --es msg 直接支持 UTF-8；这里加单引号防 shell 破坏
    return "'" + text.replace("'", "'\\''") + "'"


def human_delay(seconds: float):
    time.sleep(seconds)
