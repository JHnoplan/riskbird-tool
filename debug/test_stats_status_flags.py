# -*- coding: utf-8 -*-
"""离线回归：多站并行「状态行站点完成标识」（图标+颜色双通道）。
覆盖：site_status 三态判定 / 单站完成补发状态行 / 重跑清空旧标记 / HTML 渲染与降级 /
三路名单补采（企知道·风鸟·爱企查）在看板上都有计数且名字与状态行同源。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "b2bcollect_build"))

from parallel_session import ParallelSession, SITE_ORDER, SITE_NAMES  # noqa: E402
from b2bcollect_gui import build_stats_html, STATS_STYLE              # noqa: E402

PASS = 0
FAIL = []


def check(name, cond, got=None):
    global PASS
    if cond:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL.append(name)
        print(f"  ❌ {name}｜实得={got!r}")


print("── 1. build_stats_html 渲染 ─────────────────────────")
# 状态行必须覆盖当前 SITE_ORDER 的全部站（gys/索比已下线、1688/麦麦是后加入，不能硬写站数）
NUMS = {"gys": 60, "mic": 143, "b2b168": 285, "11467": 321, "aipage": 281,
        "maimaib2b": 214, "1688": 42, "globalimporter": 96, "makepolo": 77}
# 将来再加站（本轮已经因为漏写 globalimporter/makepolo 直接 KeyError 过一次）：
# 给个兜底数字，让这套回归测的是「状态行渲染」而不是「测试脚本有没有跟上站点表」。
NUMS.update({s: 10 for s in SITE_ORDER if s not in NUMS})
STOPPED, RUNNING, DONE_S = "11467", "aipage", "b2b168"
text = "已采 1072 家　" + "｜".join(f"{SITE_NAMES[s]} {NUMS[s]}" for s in SITE_ORDER)
st = {s: ("stopped" if s == STOPPED else "running" if s == RUNNING else "done")
      for s in SITE_ORDER}
h = build_stats_html(text, st)
# 渲染契约（紧凑徽章式）：可见区只有「图标+站名+家数」，状态文字挪到 tooltip。
# 断言按这个契约逐条写，站名/数字/颜色/图标四样都必须同时命中——少一样用户就看不出来
# 这一路到底是跑完了、停了还是在跑。
tip, _, vis = h.partition('">')          # tooltip 段（含状态文字）与可见徽章段


def badge(slug):
    color, icon = STATS_STYLE[st[slug]]
    return f'<span style="color:{color}">{icon}{SITE_NAMES[slug]} {NUMS[slug]}</span>'


check("完成站：绿字 + ✅ + 站名家数", badge(DONE_S) in vis, vis[:120])
check("停止站：橙字 + ■ + 站名家数", badge(STOPPED) in vis, vis[:120])
check("进行中：蓝字 + ● + 站名家数", badge(RUNNING) in vis, vis[:120])
check("总数前缀一个字都不吞", vis.startswith("已采 1072 家　"), vis[:24])
check("状态文字挂 tooltip（可见区保持紧凑）",
      all(f"{ic} {SITE_NAMES[slug]} {NUMS[slug]} {mark}" in tip
          for slug, ic, mark in ((DONE_S, "✅", "已完成"),
                                 (STOPPED, "■", "已停止"),
                                 (RUNNING, "●", "进行中"))), tip[:160])
check("在采站点全部有标识", all(badge(x) in vis for x in SITE_ORDER), len(SITE_ORDER))
check("空状态降级为纯文本", build_stats_html(text, None) == text)
check("部分状态只标给出的站（其余归进行中）",
      build_stats_html(text, {DONE_S: "done"}).count("已完成") == 1 and
      build_stats_html(text, {DONE_S: "done"}).count("进行中") == len(SITE_ORDER) - 1,
      build_stats_html(text, {DONE_S: "done"}))
check("已采家数与首站间不吞分隔", h.split("　")[1].startswith("<span"), h.split("　")[1][:20])
check("无低覆盖度字形（避开小字号豆腐块）", "⏳" not in h and "⏹" not in h, h)
check("按状态分组：进行中在前、已完成居中、已停止垫后",
      vis.index(badge(RUNNING)) < vis.index(badge(DONE_S)) < vis.index(badge(STOPPED)), vis[:120])
check("三位数家数不误切", f"顺企网 {NUMS[STOPPED]}" in vis and f"八方资源网 {NUMS[DONE_S]}" in vis, vis[:160])

# ── 三路名单补采（企知道/风鸟/爱企查）必须同样出现在看板上 ──
# 过去这里只遍历 SITE_ORDER，「风鸟补采 12」在日志里有、看板上没有，三平台并行时
# 用户完全判断不出哪一路出了数。看板名与状态行文案必须同源于 STATS_LABELS。
from parallel_session import LIST_CHANNEL_SLUGS, STATS_LABELS, STATUS_ORDER  # noqa: E402
check("看板名与状态行同源（名单通道统一带「补采」后缀）",
      all(STATS_LABELS[sl] == SITE_NAMES[sl] + "补采" for sl in LIST_CHANNEL_SLUGS),
      {sl: STATS_LABELS[sl] for sl in LIST_CHANNEL_SLUGS})
check("STATUS_ORDER = 关键词各站 + 名单通道（看板解析顺序的唯一来源）",
      list(STATUS_ORDER) == list(SITE_ORDER) + list(LIST_CHANNEL_SLUGS), STATUS_ORDER)
text3 = text + "｜" + "｜".join(f"{STATS_LABELS[sl]} 7" for sl in LIST_CHANNEL_SLUGS)
h3 = build_stats_html(text3, st)
for sl in LIST_CHANNEL_SLUGS:
    _color, _icon = STATS_STYLE[st[RUNNING]]
    check(f"看板含「{STATS_LABELS[sl]}」计数",
          f'<span style="color:{_color}">{_icon}{STATS_LABELS[sl]} 7</span>' in h3,
          h3[-160:])

print("── 2. ParallelSession.site_status 三态 ───────────────")
sess = ParallelSession(enabled=list(SITE_ORDER))
sent = []
if sess.signals:
    sess.signals.stats_signal.connect(lambda t, d: sent.append((t, d)))
check("初始全部 running", set(sess.site_status().values()) == {"running"}, sess.site_status())
sess._done_sites.add(DONE_S)
check("完成未停止 → done", sess.site_status()[DONE_S] == "done", sess.site_status())
sess.crawlers["mic"]._stop_requested = True
sess._done_sites.add("mic")
check("用户停止 → stopped", sess.site_status()["mic"] == "stopped", sess.site_status())
check("未影响其它站", sess.site_status()[RUNNING] == "running")
check("只报 enabled 站", set(sess.site_status()) == set(SITE_ORDER), sess.site_status())
sess.set_enabled({DONE_S})
check("停用站不出现在状态里", set(sess.site_status()) == {DONE_S}, sess.site_status())

print("── 3. 单站完成即补发状态行（末站也能点亮 ✅）─────────")
sent.clear()
sess.set_enabled(set(SITE_ORDER))
sess.crawlers[STOPPED]._stop_requested = False
sess._on_site_finished(DONE_S)
check("site_finished 触发一次 stats_signal", len(sent) == 1, len(sent))
check("补发内容带 b2b168=done", sent and sent[-1][1].get("b2b168") == "done", sent[-1][1] if sent else None)
html = build_stats_html(sent[-1][0], sent[-1][1])
check("补发文本渲染出 ✅ 八方资源网",
      f'<span style="color:{STATS_STYLE["done"][0]}">✅{SITE_NAMES["b2b168"]} 0</span>' in html,
      html)

print("── 4. 重跑启动段会清空上一轮完成标记 ─────────────────")
import parallel_session as ps                              # noqa: E402


class _NoThread:                                            # 把采集线程换成空跑，只验编排逻辑
    def __init__(self, target=None, args=(), name=None, daemon=None):
        pass

    def start(self):
        pass

    def is_alive(self):
        return False

    def join(self, timeout=0):
        pass


sess.set_enabled(set(SITE_ORDER))
sess._done_sites = {STOPPED, "mic"}
sess.crawlers[STOPPED]._stop_requested = True
check("预置旧标记：停止站被误报 stopped", sess.site_status()[STOPPED] == "stopped", sess.site_status())
_old = ps.threading.Thread
ps.threading.Thread = _NoThread
try:
    sess.crawl(["测试关键词"])
finally:
    ps.threading.Thread = _old
check("crawl() 启动已重置 _done_sites", sess._done_sites == set(), sess._done_sites)
check("重跑后状态行不再残留旧完成/停止标记",
      set(sess.site_status().values()) == {"running"}, sess.site_status())

print()
print(f"通过 {PASS} 项，失败 {len(FAIL)} 项")
for n in FAIL:
    print(f"  · 失败：{n}")
sys.exit(1 if FAIL else 0)
