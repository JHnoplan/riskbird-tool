# -*- coding: utf-8 -*-
"""双平台状态机离线回归（不依赖真机/OCR 实图）：
- WeChatFlow：结果卡 → 资料页(添加到通讯录) → 申请页(打招呼内容) → 人工确认
- DingTalkFlow：结果卡(蓝) → 直达申请页(验证消息，无中间资料页) → 人工确认
定位函数全部打桩，只验证流程编排、点按顺序与状态判定。
"""
import sys

import wechat_flow as WF
import locator as L


class MockADB:
    def __init__(self, screens):
        self.screens = screens          # 名称序列
        self.i = 0
        self.taps = []
        self.texts = []
        self.chinese = []
        self.backs = 0

    def screencap_png(self):
        self.i += 1
        return f"PNG:{self.screens[min(self.i - 1, len(self.screens) - 1)]}".encode()

    def uiautomator_dump(self):
        return b""

    def tap(self, x, y):
        self.taps.append((round(x), round(y)))

    def press_back(self):
        self.backs += 1

    def input_text(self, t):
        self.texts.append(t)

    def input_chinese(self, t):
        self.chinese.append(t)

    def shell(self, *a, **k):
        return ""


# 各屏幕的 OCR 命中表：关键字 → (x, y)；ocr_text 返回整页文本供 _has 用
HITS = {
    "search": {"搜索": (360, 107), "取消": (600, 107)},
    "search2": {"搜索": (360, 107), "取消": (600, 107)},
    "result": {"添加": (360, 207)},
    "profile": {"添加到通讯录": (540, 1200)},
    "request_wx": {"打招呼内容": (360, 900)},
    "request_dt": {"验证消息": (360, 900)},
    "notfound": {},
    # loading 瞬态用例（result_slow/result_clear 不以 result 结尾 → 图色探针不命中，
    # 顺带覆盖 OCR 兜底找号码路径）
    "result_slow": {"1380013800": (360, 207)},
    "loading": {},
    "result_clear": {"1380013800": (360, 207)},
    # 复位收敛用例：更新弹窗（证据词+白名单按钮成对出现才点，见 POPUP_PAIRS）
    "update_dialog": {"以后再说": (360, 800)},
}
TEXTS = {
    "search": "取消 搜索",
    "search2": "取消 搜索",
    "result": "搜索:1380013800",
    "profile": "张三 添加到通讯录",
    "request_wx": "打招呼内容 发送",
    "request_dt": "申请加好友 验证消息 发送",
    "notfound": "该用户不存在",
    "result_slow": "搜索:1380013800 正在查找联系人",
    "loading": "正在查找联系人",
    "result_clear": "搜索:1380013800",
    "greet_filled": "王先生你好，我是小李",
    "update_dialog": "发现新版本 以后再说",
}


def install_stub():
    def fake_ocr_find(png, keywords, region=None, **k):
        name = png.decode().split(":")[1]
        for kw in keywords:
            hit = HITS.get(name, {}).get(kw)
            if hit:
                return L.Point(hit[0], hit[1], text=kw)
        return None

    def fake_ocr_text(png, **k):
        name = png.decode().split(":")[1]
        return TEXTS.get(name, TEXTS.get(name.replace("2", ""), ""))

    def fake_color_green(png):
        return (360, 207) if png.decode().endswith("result") else None

    def fake_color_blue(png):
        return (360, 207) if png.decode().endswith("result") else None

    WF.L.ocr_find = fake_ocr_find
    WF.L.ocr_text = fake_ocr_text
    WF.L.find_green_button = fake_color_green
    WF.L.find_blue_button = fake_color_blue
    # 假 PNG 无法真解码：打桩 image_size 让 _shot 的坏图重试判定放行
    WF.L.image_size = lambda png: (720, 1612) if png else None


def run(platform):
    # 弹窗自愈拍 search；搜索框拍 search；结果卡拍 result；loading 瞬态检查后
    # 重定位再拍一张 result；确定性等待拍下页；复位分诊拍 search2、收敛验证再拍一张
    if platform == "wx":
        screens = ["search", "search", "result", "result", "profile", "request_wx",
                   "greet_filled", "search2", "search2"]
        flow = WF.WeChatFlow
    else:
        screens = ["search", "search", "result", "result", "request_dt",
                   "greet_filled", "search2", "search2"]
        flow = WF.DingTalkFlow
    adb = MockADB(screens)
    f = flow(adb, logger=lambda m: None, wait_human=lambda r: "sent",
             sleeper=lambda s: None, daily_cap=80, hourly_cap=25, delay_range=(1, 1))
    r = f.process_one("1380013800", "王先生你好，我是小李")
    return adb, r


def main():
    install_stub()
    ok = True

    for plat in ("wx", "dt"):
        adb, r = run(plat)
        print(f"--- {plat}: status={r.status} note={r.note}")
        print(f"    taps={adb.taps}")
        print(f"    input={adb.texts} chinese={adb.chinese} backs={adb.backs}")
        if plat == "wx":
            assert r.status == WF.ST_REAL_WAIT, r
            # 搜索框×2 → 结果卡 → 添加到通讯录 → 打招呼内容 → 复位后搜索框
            assert len(adb.taps) == 5, adb.taps
            assert (540, 1200) in adb.taps, adb.taps
        else:
            assert r.status == WF.ST_REAL_WAIT, r
            # 钉钉直达申请页：搜索框×2 → 结果卡 → 验证消息（tap 下移 0.06h 进卡片）
            # → 复位后搜索框（无添加中间步）
            assert len(adb.taps) == 4, adb.taps
            assert (360, 900 + 96) in adb.taps, adb.taps  # 0.06*1612=96，tap 落在输入卡片
        assert adb.chinese == ["王先生你好，我是小李"], adb.chinese
        # 分诊复位：结束时停在搜索页，应直接收敛（tap+清空+验证），不再盲退
        assert adb.backs == 0, adb.backs
        print(f"    {plat} PASS")

    # 未找到分支（两平台共用）：弹窗拍 search、搜索框拍 search、结果卡拍 result、
    # 重定位再拍 result；确定性等待拍 notfound（命中 not_found）、复位分诊+收敛验证拍 search2×2
    adb = MockADB(["search", "search", "result", "result", "notfound", "search2", "search2"])
    f = WF.WeChatFlow(adb, logger=lambda m: None, wait_human=lambda r: "sent",
                      sleeper=lambda s: None)
    r = f.process_one("1380013800", "x")
    assert r.status == WF.ST_NOT_FOUND, r
    print("not-found PASS:", r.note)

    # loading 瞬态用例：结果卡已显示但查询未完成（慢网）→ 等消失、重定位后才点卡
    adb = MockADB(["search", "search", "result_slow", "loading", "result_clear",
                   "result_clear", "profile", "request_wx", "greet_filled",
                   "search2", "search2"])
    f = WF.WeChatFlow(adb, logger=lambda m: None, wait_human=lambda r: "sent",
                      sleeper=lambda s: None)
    r = f.process_one("1380013800", "王先生你好，我是小李")
    assert r.status == WF.ST_REAL_WAIT, r
    assert len(adb.taps) == 5, adb.taps  # 搜索框×2 + 卡片(重定位后) + 添加 + 打招呼
    assert (540, 1200) in adb.taps, adb.taps
    assert adb.chinese == ["王先生你好，我是小李"], adb.chinese
    print("loading-gone PASS:", r.note)

    # _wait_any 瞬态延长：命中 loading 组延长 deadline 继续等，业务锚点出现才返回
    sleeper_calls = []
    adb = MockADB(["loading", "loading", "profile"])
    f = WF.WeChatFlow(adb, logger=lambda m: None, wait_human=lambda r: "sent",
                      sleeper=lambda s: sleeper_calls.append(s))
    png, hit = f._wait_any([("add", ["添加到通讯录"]), ("loading", WF.ANCHOR_LOADING)],
                           timeout=10.0)
    assert hit == "add", hit
    assert len(sleeper_calls) == 2, sleeper_calls  # 延长 2 次（每轮各 sleep 一次）
    # 延长额度用尽：仍停留在瞬态 → 返回瞬态名，由调用方按超时处理
    adb = MockADB(["loading", "loading", "loading", "loading"])
    f = WF.WeChatFlow(adb, logger=lambda m: None, wait_human=lambda r: "sent",
                      sleeper=lambda s: None)
    png, hit = f._wait_any([("add", ["添加到通讯录"]), ("loading", WF.ANCHOR_LOADING)],
                           timeout=10.0, extend=0.1)
    assert hit == "loading", hit
    print("wait-any-extend PASS")

    # 复位收敛（Bug 2）：从资料页出发 → 分诊 back 一层 → 搜索页 tap+清空+验证收敛。
    # 全程仅 1 次 back、1 次搜索框 tap，修复前“盲 back 后重进添加页”的问题不再发生
    adb = MockADB(["profile", "search2", "search2"])
    f = WF.WeChatFlow(adb, logger=lambda m: None, wait_human=lambda r: "sent",
                      sleeper=lambda s: None)
    assert f._reset_to_search() is True
    assert adb.backs == 1, adb.backs
    assert adb.taps == [(360, 107)], adb.taps
    print("reset-from-profile PASS")

    # 复位收敛：更新弹窗插队 → 先点掉弹窗，下一轮分诊才收敛回搜索页
    adb = MockADB(["update_dialog", "search2", "search2"])
    f = WF.WeChatFlow(adb, logger=lambda m: None, wait_human=lambda r: "sent",
                      sleeper=lambda s: None)
    assert f._reset_to_search() is True
    assert adb.taps == [(360, 800), (360, 107)], adb.taps
    assert adb.backs == 0, adb.backs
    print("reset-with-popup PASS")

    # Bug 1 单测：一行多号码（英文逗号分隔）逐号拆分各自排入 + 本批去重，
    # 脏后缀（“姓 名”）由 phone_verifier 的形似兜底回收
    import types
    import wechat_helper_gui as GUI
    g = GUI.MainWindow.__new__(GUI.MainWindow)
    g.combo_phone = types.SimpleNamespace(currentData=lambda: 1)
    g.headers = ["姓名", "电话"]
    g.rows = [["张三", "13800138000,13800138001 姓 名"], ["李四", "13800138000"]]
    items, skipped = g._build_items()
    real = [it["phone"] for it in items if not it.get("_prefilter")]
    assert real == ["13800138000", "13800138001"], real  # 不再整串搜索
    assert skipped == 1, skipped
    dup = [it for it in items if it.get("_skip_note")]
    assert len(dup) == 1 and "重复" in dup[0]["_skip_note"], dup
    print("bug1-split-numbers PASS:", real)
    print("ALL PASS")


if __name__ == "__main__":
    sys.exit(main())
