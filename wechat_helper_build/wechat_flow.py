# -*- coding: utf-8 -*-
"""
微信/钉钉加好友半自动流程（状态机）
================================

流程（用户已在手机上手动打开到「添加好友-搜索」页）：
  粘贴手机号 → 点结果卡 → 判定页面对象 →
    · 有「添加到通讯录/添加好友」= 真实用户 → 点它 → 进申请页 → 把话术填进输入框
      → 【停下】触发人工提示（电脑声音+界面高亮），等操作员在手机上手动点「发送」
    · 直接进入聊天/显示「已添加」= 已是好友 → 记录并返回
    · 无结果 / 「未找到」= 空号或未注册 → 记录并返回
  → 回搜索页 → 随机间隔 → 下一条

机型适配（不写死任何像素坐标）：
  - 定位来源：控件树(uiauto) → OCR 整行聚合 → 图色质心 → 相对推导，任意分辨率一致；
  - OCR 分辨率归一化到 720 宽（locator._normalize），高分屏提速且坐标无损映射回原屏；
  - 暗色模式自愈：截图平均亮度低自动反相后再识别；
  - 确定性等待：页面跳转后轮询锚点出现（替代盲睡），慢机/弱网不误判；
  - 弹窗自愈：更新/权限弹窗按白名单词对自动点掉，防插队卡死；
  - screencap 偶发空字节自动重试；异常路径现场截图存 failshots/ 供排障。

安全与合规护栏（本工具红线）：
  - 绝不自动点「发送」；最后一步永远由人完成。
  - 出现验证码 / 「操作频繁」/ 异常登录提示即熔断暂停，交人工处理。
  - 每日、每小时处理条数硬上限，超限自动停，避免高频枚举触发风控。
  - 全程结构化台账（号码/判定/时间/备注），留存"操作员本人知情点击"证据。

本模块不含 GUI、不直接 sleep 死等，人工等待与节流通过回调注入，便于离线单测。
"""

from __future__ import annotations

import os
import random
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Dict, List, Optional

import locator as L

# ---------------------------------------------------------------------------
# 微信界面文本锚点（按用户截图校准；换版本可能需调整，集中在此便于维护）
# ---------------------------------------------------------------------------
ANCHOR_SEARCH_BOX = ["搜索", "账号/手机号", "帐号/手机号", "手机号", "微信号"]
ANCHOR_SEARCH_RESULT = ["搜索:"]           # 绿色结果卡片
ANCHOR_ADD_TO_CONTACTS = ["添加到通讯录", "添加到通讯"]   # 资料页可添加（后者为截断兜底）
ANCHOR_GREET_FIELD = ["打招呼内容", "打招呼"]
ANCHOR_SEND = ["发送"]
# 已是好友 / 直达聊天（无"添加到通讯录"、无打招呼页）
ANCHOR_ALREADY = ["发送消息", "发消息", "已添加", "朋友资料"]
# 未找到
ANCHOR_NOT_FOUND = ["该用户不存在", "不存在", "未找到", "找不到", "无相关", "没有搜索到", "无法找到"]
# 风控 / 异常
ANCHOR_RISK = ["操作过于频繁", "操作频繁", "请输入验证码", "安全验证", "异常登录",
               "你的账号", "请稍后再试", "访问过于频繁"]
# 加载/查询瞬态（慢网时序：结果卡先显示而服务端查询未完成，点卡无响应）
ANCHOR_LOADING = ["正在查找", "查找联系人", "加载中", "搜索中", "请稍候"]

# 厂商/应用弹窗自愈白名单：成对（证据词 → 可点按钮），两项都在屏才点，防误触
POPUP_PAIRS = [
    (("发现新版本", "新版本", "更新到最新"),
     ["以后再说", "暂不更新", "稍后再说", "下次再说"]),
    (("申请获取以下权限", "权限申请", "获取以下权限"),
     ["允许", "始终允许"]),
]

# 状态枚举
ST_REAL_WAIT = "真实用户_待人工发送"
ST_ALREADY = "已是好友"
ST_NOT_FOUND = "未找到用户"
ST_RISK = "触发风控_已暂停"
ST_ERROR = "流程异常"
ST_SKIPPED = "跳过"
ST_PREFILTER = "预筛跳过(无有效手机号)"


@dataclass
class FlowResult:
    phone: str
    name: str = ""
    company: str = ""
    status: str = ST_ERROR
    note: str = ""
    at: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))


# 人工等待回调签名：(result: FlowResult) -> str  返回 "sent" / "skipped"
WaitForHuman = Callable[[FlowResult], str]
# 日志回调：(str)
Logger = Callable[[str], None]
# 节流回调：(seconds)
Sleeper = Callable[[float], None]


class WeChatFlow:
    def __init__(self, adb, *,
                 logger: Optional[Logger] = None,
                 wait_human: Optional[WaitForHuman] = None,
                 sleeper: Optional[Sleeper] = None,
                 should_stop: Optional[Callable[[], bool]] = None,
                 daily_cap: int = 80,
                 hourly_cap: int = 25,
                 delay_range=(6, 10),
                 anchors: Optional[Dict[str, list]] = None,
                 card_color_probe: Optional[Callable] = None):
        """
        adb: ADBController（或任何实现同类方法的 mock）
        wait_human: 到"该点发送"时调用，阻塞直到操作员完成，返回 sent/skipped
        daily_cap/hourly_cap: 风控护栏上限
        anchors: 平台锚点覆盖表（钉钉子类传入 DINGTALK_ANCHORS）
        card_color_probe: 结果卡图色探针（微信绿 find_green_button / 钉钉蓝 find_blue_button）
        """
        self.adb = adb
        self.log = logger or (lambda m: None)
        self.wait_human = wait_human or (lambda r: "sent")
        self.sleep = sleeper or time.sleep
        self.should_stop = should_stop or (lambda: False)
        self.daily_cap = daily_cap
        self.hourly_cap = hourly_cap
        self.delay_range = delay_range
        # 平台锚点集：默认微信；子类/调用方传 anchors 覆盖对应项
        self.anchors: Dict[str, list] = {
            "search_box": ANCHOR_SEARCH_BOX,
            "result_card_text": ["搜索:", "搜索：", "搜索"],
            "add_contact": ANCHOR_ADD_TO_CONTACTS,
            "greet": ANCHOR_GREET_FIELD,
            "not_found": ANCHOR_NOT_FOUND,
            "already": ANCHOR_ALREADY,
            "risk": ANCHOR_RISK,
        }
        if anchors:
            self.anchors.update(anchors)
        self.card_color_probe = card_color_probe or L.find_green_button
        # 节流计数
        self._day_count = 0
        self._timestamps: List[float] = []
        # 定位模式自适应：部分机型（华为/荣耀）微信对 uiautomator 隐身，
        # 连续取到空树后自动切为纯 OCR 定位，避免每步白做一次无用的 dump。
        self._skip_uiauto = False
        self._empty_cnt = 0
        self._w = self._h = 0

    # ---------------------- 内部工具 ----------------------
    def _dim(self):
        if not self._w:
            try:
                self._w, self._h = self.adb.screen_size()
            except Exception:  # noqa
                self._w, self._h = 720, 1612
        return self._w, self._h

    def _shot(self) -> bytes:
        """截图（偶发空字节/坏图自动重试，防一次坏图毁整条流程）。"""
        png = b""
        for _ in range(3):
            png = self.adb.screencap_png()
            if png and L.image_size(png):
                return png
            self.sleep(0.4)
        return png

    def _nodes(self):
        if self._skip_uiauto:
            return []
        return L.parse_hierarchy(self.adb.uiautomator_dump())

    def _note_empty_tree(self, nodes):
        """若整棵树无任何文本，累计计数，连续两次后判定本机 uiautomator 不可用。"""
        if self._skip_uiauto or any((n.text or n.content_desc).strip() for n in nodes):
            return
        self._empty_cnt += 1
        if self._empty_cnt >= 2:
            self._skip_uiauto = True
            self.log("本机 uiautomator 取不到微信控件树，已自动切换为 OCR 图像识别定位。")

    def _has(self, keywords, png=None) -> bool:
        """关键词是否在当前屏（uiauto 文本 或 整页 OCR 文本，用于存在性判断）。"""
        kws = [keywords] if isinstance(keywords, str) else list(keywords)
        nodes = self._nodes()
        self._note_empty_tree(nodes)
        if L.find_by_text(nodes, kws) is not None:
            return True
        txt = L.ocr_text(png if png is not None else self._shot())
        return any(k in txt for k in kws)

    def _locate(self, keywords, region=None, png=None):
        """返回可点击点（带 .center）：uiauto 主、逐词 OCR 兜底。"""
        kws = [keywords] if isinstance(keywords, str) else list(keywords)
        nodes = self._nodes()
        self._note_empty_tree(nodes)
        n = L.find_by_text(nodes, kws)
        if n is not None:
            return n
        return L.ocr_find(png if png is not None else self._shot(), kws, region=region)

    def _wait_any(self, groups, timeout=10.0, interval=0.6, extend=8.0, max_extend=2):
        """确定性等待：轮询截图直到任一组锚点出现（替代盲睡，慢机/弱网不误判）。

        groups: [(名称, 关键词列表), ...]；返回 (最后一张截图, 命中名称或 None)。
        组名 "loading" 为瞬态：命中时延长 deadline 继续等（最多 max_extend 次）
        而非直接返回——慢网下『正在查找联系人』可持续数秒，视为查询中而非超时。
        """
        deadline = time.time() + timeout
        extended = 0
        png = self._shot()
        while True:
            hit_name = None
            for name, kws in groups:
                if self._has(kws, png=png):
                    hit_name = name
                    break
            if hit_name is not None:
                if hit_name == "loading" and extended < max_extend:
                    extended += 1
                    deadline += extend
                    self.log(f"检测到加载中，延长等待 {extend:.0f}s（第 {extended} 次）。")
                else:
                    return png, hit_name
            if time.time() >= deadline or self.should_stop():
                return png, None
            self.sleep(interval)
            png = self._shot()

    def _save_fail(self, png: bytes, phone: str, tag: str):
        """异常现场截图存 failshots/（静默失败不影响主流程），供事后排障。"""
        try:
            d = os.path.join(os.path.dirname(os.path.abspath(__file__)), "failshots")
            os.makedirs(d, exist_ok=True)
            ts = datetime.now().strftime("%H%M%S")
            with open(os.path.join(d, f"fail_{ts}_{tag}_{phone}.png"), "wb") as f:
                f.write(png or b"")
        except Exception:  # noqa
            pass

    def _dismiss_popups(self, png=None) -> bool:
        """识别并点掉常见干扰弹窗（更新提示/权限申请），命中返回 True。

        仅当证据词与可点按钮同时出现（白名单词对）才点，避免误触任意位置。
        传入 png 则复用已有截图（复位分诊每轮共用一张，省一次截屏）。
        """
        png = png if png is not None else self._shot()
        txt = L.ocr_text(png)
        for ev, acts in POPUP_PAIRS:
            if any(k in txt for k in ev):
                btn = self._locate(acts, png=png)
                if btn is not None:
                    self.log("检测到系统/应用弹窗，已自动点掉。")
                    self.adb.tap(*btn.center)
                    self.sleep(1.0)
                    return True
        return False

    def _wait_loading_gone(self, png=None, timeout=12.0, interval=0.8) -> bool:
        """等待『正在查找/加载中』瞬态消失（确定性等待，替代盲睡）。

        慢网时序：搜索结果卡先显示而服务端查询未完成，此时点卡无响应，
        必须等查询落定再交互；传入已有截图可先判一次，命中瞬态才进入轮询。
        """
        deadline = time.time() + timeout
        if png is not None:
            txt = L.ocr_text(png)
            if not any(k in txt for k in ANCHOR_LOADING):
                return True
        while time.time() < deadline and not self.should_stop():
            txt = L.ocr_text(self._shot())
            if not any(k in txt for k in ANCHOR_LOADING):
                return True
            self.sleep(interval)
        return False

    def _find_search_box(self, png=None):
        """定位顶部搜索框：uiauto → OCR 占位文字 → 以右上角『取消』反推（跨分辨率）。

        OCR 加几何先验：搜索框必在屏幕顶部（y<18%h），否则主页功能列表里
        「通过手机号搜索企业微信用户」等含“手机号”的行会被宽泛锚点误命中，
        导致复位导航误入企业微信搜索页（真机实测踩过）。
        传入 png 则复用已有截图（复位分诊每轮共用一张，省一次截屏）。
        """
        w, h = self._dim()
        top_region = (0, 0, w, int(h * 0.18))
        if not self._skip_uiauto:
            nodes = self._nodes()
            self._note_empty_tree(nodes)
            n = L.find_by_text(nodes, self.anchors["search_box"])
            if n is not None and n.center[1] < int(h * 0.25):
                return n
        png = png if png is not None else self._shot()
        n = L.ocr_find(png, self.anchors["search_box"], region=top_region)
        if n is not None:
            return n
        c = L.ocr_find(png, ["取消"], region=top_region)  # 高对比锚点：搜索框在『取消』左侧同行
        if c is not None:
            return L.Point(int(w * 0.42), c.y, text="(搜索框:由取消反推)")
        return None

    def _find_result_card(self, png: bytes, phone: str, box):
        """定位搜索结果卡片：平台图色探针优先（微信绿卡/钉钉蓝），OCR 兜底。

        OCR 兜底直接找『号码本身』——输入后号码会同时出现在搜索框和结果行，
        把识别区域压到搜索框以下即可区分两处。
        """
        gp = self.card_color_probe(png)
        if gp:
            return L.Point(gp[0], gp[1], text="(结果卡:图色)")
        w, h = self._dim()
        top = int(h * 0.10)
        try:
            top = max(top, box.center[1] + 35)
        except Exception:  # noqa
            pass
        kws = [phone] + list(self.anchors["result_card_text"])
        return L.ocr_find(png, kws, region=(0, top, w, int(h * 0.35)))

    def _clear_input_box(self):
        """清空已聚焦的输入框：光标移到末尾后连发 DEL，跨机型通用。

        搜索框（号码≤15位）与申请页话术框（需清掉微信预填的『我是<昵称>』
        默认申请语，约 10 个汉字）都用 30 次 DEL 覆盖。
        """
        self.adb.shell("input keyevent KEYCODE_MOVE_END")
        self.adb.shell("input keyevent " + " ".join(["67"] * 30))  # 67 = KEYCODE_DEL

    def _within_caps(self) -> bool:
        now = time.time()
        self._timestamps = [ts for ts in self._timestamps if now - ts < 3600]
        if self._day_count >= self.daily_cap:
            self.log(f"已达当日处理上限 {self.daily_cap} 条，自动停止（防高频枚举）。")
            return False
        if len(self._timestamps) >= self.hourly_cap:
            self.log(f"已达每小时处理上限 {self.hourly_cap} 条，自动停止（防高频枚举）。")
            return False
        return True

    def _count(self):
        self._day_count += 1
        self._timestamps.append(time.time())

    # ---------------------- 单号码流程 ----------------------
    def process_one(self, phone: str, greeting: str,
                    name: str = "", company: str = "") -> FlowResult:
        res = FlowResult(phone=phone, name=name, company=company)
        try:
            # 0) 弹窗自愈：更新/权限弹窗插队会卡死后续定位
            if self._dismiss_popups():
                self.sleep(0.3)

            # 1) 定位顶部搜索框（uiauto → OCR 占位 → 取消反推）
            box = self._find_search_box()
            if box is None:
                res.status = ST_ERROR
                res.note = "未定位到搜索框（请确认已停在微信『添加好友-搜索』页）"
                self.log(f"[{phone}] {res.note}")
                return res

            # 1) 点搜索框聚焦 → 清空 → 输入手机号
            self.adb.tap(*box.center)
            self.sleep(0.6)
            self._clear_input_box()
            self.adb.input_text(phone)
            self.sleep(1.2)

            # 2) 点搜索结果卡片（平台图色探针主，OCR 找号码/『搜索:』兜底）
            png = self._shot()
            card = self._find_result_card(png, phone, box)
            # 慢网瞬态：结果卡已显示但查询未完成时点卡无响应，等『正在查找』
            # 消失后重定位再点（loading 不消失则用原卡点按，交第 3 步延长等待兜底）
            if card is not None and self._wait_loading_gone(png=png):
                png = self._shot()
                card = self._find_result_card(png, phone, box)
            if card is None:
                res.status = ST_NOT_FOUND
                res.note = "搜索后未出现结果卡片（号码格式无效/未注册）"
                self.log(f"[{phone}] {res.note}")
                self._reset_to_search()
                return res
            self.adb.tap(*card.center)

            # 3) 确定性等待结果页：任一关键锚点出现即继续（慢机/弱网不误判）
            png, hit = self._wait_any([
                ("risk", self.anchors["risk"]),
                ("not_found", self.anchors["not_found"]),
                ("greet", self.anchors["greet"]),
                ("add", self.anchors["add_contact"]),
                ("already", self.anchors["already"]),
                ("loading", ANCHOR_LOADING),
            ], timeout=10.0)
            if hit == "risk":
                self._save_fail(png, phone, "risk")
                res.status = ST_RISK
                res.note = "出现风控/验证提示，已暂停，请人工处理"
                self.log(f"[{phone}] {res.note}")
                return res
            if hit == "not_found":
                res.status = ST_NOT_FOUND
                res.note = "未找到该号码对应的用户（该用户不存在/未注册）"
                self.log(f"[{phone}] {res.note}")
                self._reset_to_search()
                return res
            if hit == "already":
                res.status = ST_ALREADY
                res.note = "已是好友/直达会话，无需添加"
                self.log(f"[{phone}] {res.note}")
                self._reset_to_search()
                return res
            if hit is None or hit == "loading":  # loading 延长额度用尽仍不落定 → 按超时
                self._save_fail(png, phone, "timeout")
                res.status = ST_NOT_FOUND
                res.note = "结果页超时未出现可识别元素（页面未加载/界面变动）"
                self.log(f"[{phone}] {res.note}")
                self._reset_to_search()
                return res

            # 4) 进入添加路径：钉钉可能点结果卡直达申请页(hit=greet)，微信需点添加
            greet = None
            if hit == "greet":
                greet = self._locate(self.anchors["greet"], png=png)
            else:  # hit == "add"
                add_btn = self._locate(self.anchors["add_contact"], png=png)
                if add_btn is None:
                    self._save_fail(png, phone, "noadd")
                    res.status = ST_NOT_FOUND
                    res.note = "资料页无可添加入口，按未找到处理"
                    self.log(f"[{phone}] {res.note}")
                    self._reset_to_search()
                    return res
                # 点添加到通讯录/添加好友 → 确定性等待申请页出现
                self.adb.tap(*add_btn.center)
                png, hit2 = self._wait_any([
                    ("greet", self.anchors["greet"]),
                    ("risk", self.anchors["risk"]),
                    ("loading", ANCHOR_LOADING),
                ], timeout=8.0)
                if hit2 == "risk":
                    self._save_fail(png, phone, "risk")
                    res.status = ST_RISK
                    res.note = "出现风控/验证提示，已暂停，请人工处理"
                    self.log(f"[{phone}] {res.note}")
                    return res
                if hit2 == "greet":
                    greet = self._locate(self.anchors["greet"], png=png)
            if greet is None:
                self._save_fail(png, phone, "nogreet")
                res.status = ST_ERROR
                res.note = "未定位到『打招呼/验证消息』输入区"
                self.log(f"[{phone}] {res.note}")
                self._reset_to_search()
                return res

            # 5) 填话术（绝不点发送）。
            # 真机实测：『打招呼内容/验证消息』锚点命中的是 section 标签行，
            # 可编辑卡片在其下方（微信 8.0.80：标签 y≈203、卡片 y≈290 @720x1612），
            # 直接 tap 标签不聚焦输入框，话术 broadcast 无处落笔——tap 需下移进卡片。
            _, gh = self._dim()
            gx, gy = greet.center
            self.adb.tap(gx, gy + int(gh * 0.06))
            self.sleep(0.6)
            # 申请页输入框预填微信默认申请语（『我是<昵称>』）：先清掉再填，
            # 避免话术追加在默认语之后。
            self._clear_input_box()
            self.adb.input_chinese(greeting)
            self.sleep(0.8)
            # 话术上屏自检：OCR 查话术前缀（去空格比对；OCR 对小字可能漏检，
            # 未命中仅提示不阻断——人工点发送前本就有肉眼确认环节）。
            probe = greeting.replace(" ", "")[:4]
            shot = self._shot()
            if probe and probe not in L.ocr_text(shot).replace(" ", ""):
                self._save_fail(shot, phone, "greet_no_text")
                res.note = "话术自检未命中，请在手机上核对/补填后再点『发送』"
                self.log(f"[{phone}] ⚠ {res.note}")

            res.status = ST_REAL_WAIT
            if not res.note:
                res.note = "话术已填入，等待操作员手动点击『发送』"
            self.log(f"[{phone}] ✅ 真实用户，话术已填，请手动点发送")

            # 6) 停下，交人工（回调里 GUI 播声音+高亮，阻塞至操作员确认）
            ctx = {"result": res, "name": name, "company": company, "greeting": greeting}
            decide = self.wait_human(ctx) or "sent"
            res.note += f"（操作员：{decide}）"
            # 无论发送或跳过，都返回搜索页
            self._reset_to_search()
            return res

        except Exception as e:  # noqa
            res.status = ST_ERROR
            res.note = f"异常：{str(e).splitlines()[0]}"
            self.log(f"[{phone}] {res.note}")
            return res

    def _reset_to_search(self) -> bool:
        """从任何页面收敛回干净的搜索页（页面分诊，替代盲 back）。

        真机实测：跳过/发送后手机可能停在申请页、资料页或搜索结果页，
        操作员还可能手动 back 过几层——不能假设起点层级。每轮先对当前页
        分诊：干扰弹窗→点掉；搜索页→tap 搜索框清空并验证结果卡已消失；
        申请页/资料页→back 一层；其他→盲退。最多 6 轮，失败存现场截图。
        """
        # 结果卡强特征（带冒号）——宽泛词“搜索”会撞上搜索框占位符导致永不收敛
        card_kws = [k for k in self.anchors["result_card_text"]
                    if any(c in k for c in ":：")]
        for _round in range(6):
            png = self._shot()
            if self._dismiss_popups(png=png):
                self.sleep(0.8)
                continue
            box = self._find_search_box(png=png)
            if box is not None:
                self.adb.tap(*box.center)
                self.sleep(0.5)
                self._clear_input_box()
                self.sleep(0.5)
                # 收敛验证：结果卡必须已消失（图色探针 + 强特征词都为空才算干净）
                vpng = self._shot()
                if self.card_color_probe(vpng) is None and \
                        not any(k in L.ocr_text(vpng) for k in card_kws):
                    return True
                continue  # 清空未生效（结果卡仍在）→ 下一轮继续收敛
            txt = L.ocr_text(png)
            if (any(k in txt for k in self.anchors["greet"])
                    or any(k in txt for k in self.anchors["add_contact"])
                    or any(k in txt for k in self.anchors["already"])):
                self.adb.press_back()  # 申请页/资料页 → 退一层
            else:
                self.adb.press_back()  # 未知页 → 盲退
            self.sleep(0.8)
        self._save_fail(self._shot(), "", "reset_timeout")
        self.log("复位未收敛回搜索页，请人工检查手机屏幕后继续。")
        return False

    # ---------------------- 批量循环 ----------------------
    def run_batch(self, items: List[Dict[str, str]],
                  greeting_fn: Callable[[Dict[str, str]], str],
                  on_item: Optional[Callable[[int, int, "FlowResult"], None]] = None
                  ) -> List[FlowResult]:
        """items: [{phone,name,company, mapping:{列名:值}}, ...]
        greeting_fn(item)->话术；on_item(i,total,result) 逐条回调（供进度显示）。
        返回逐条结果。"""
        results: List[FlowResult] = []
        total = len(items)
        for i, it in enumerate(items):
            if self.should_stop():
                self.log("收到停止指令，结束本轮。")
                break
            if not self._within_caps():
                break
            greeting = greeting_fn(it)
            r = self.process_one(it["phone"], greeting,
                                 name=it.get("name", ""), company=it.get("company", ""))
            self._count()
            results.append(r)
            if on_item:
                try:
                    on_item(i + 1, total, r)
                except Exception:  # noqa
                    pass
            if r.status == ST_RISK:
                self.log("因风控暂停，请人工处理后重新开始。")
                break
            # 随机间隔（最后一条不等待）
            if i < len(items) - 1 and not self.should_stop():
                d = random.uniform(*self.delay_range)
                self.log(f"随机间隔 {d:.1f}s 后处理下一条…")
                self.sleep(d)
        return results


# ---------------------------------------------------------------------------
# 钉钉平台：与微信流程同构（搜手机号 → 结果卡 → 添加 → 验证消息 → 人工发送），
# 仅锚点词与图色探针不同。首次真机校准后如有偏差，只改这里。
# ---------------------------------------------------------------------------
DINGTALK_ANCHORS = {
    "search_box": ["搜索", "手机号", "钉钉号"],
    "result_card_text": ["搜索:"],
    "add_contact": ["添加好友", "加好友", "添加"],
    "greet": ["验证消息", "验证信息", "打招呼"],
    "not_found": ["未找到", "没有找到", "未注册", "不存在", "无结果", "无相关"],
    "already": ["发消息", "发送消息"],
    "risk": ["操作频繁", "过于频繁", "请输入验证码", "安全验证", "请稍后再试", "异常"],
}


class DingTalkFlow(WeChatFlow):
    """钉钉半自动加好友：复用微信状态机，仅换锚点与蓝色图色探针。"""

    def __init__(self, adb, *, logger=None, wait_human=None, sleeper=None,
                 should_stop=None, daily_cap=80, hourly_cap=25, delay_range=(6, 10)):
        super().__init__(adb, logger=logger, wait_human=wait_human, sleeper=sleeper,
                         should_stop=should_stop, daily_cap=daily_cap,
                         hourly_cap=hourly_cap, delay_range=delay_range,
                         anchors=DINGTALK_ANCHORS, card_color_probe=L.find_blue_button)
