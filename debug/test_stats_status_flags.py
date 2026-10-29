# -*- coding: utf-8 -*-
"""离线回归：五站并行「状态行站点完成标识」（图标+颜色双通道）。
覆盖：site_status 三态判定 / 单站完成补发状态行 / 重跑清空旧标记 / HTML 渲染与降级。
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
# 状态行必须覆盖当前 SITE_ORDER 的全部站（gys 已下线、1688 后加入，不能硬写五站）
NUMS = {"gys": 60, "mic": 143, "b2b168": 285, "11467": 321, "aipage": 281, "1688": 42}
STOPPED, RUNNING, DONE_S = "11467", "aipage", "b2b168"
text = "已采 1072 家　" + "｜".join(f"{SITE_NAMES[s]} {NUMS[s]}" for s in SITE_ORDER)
st = {s: ("stopped" if s == STOPPED else "running" if s == RUNNING else "done")
      for s in SITE_ORDER}
h = build_stats_html(text, st)
check("完成站：绿字加粗 + ✅ + 已完成",
      f'<span style="color:{STATS_STYLE["done"][0]};font-weight:bold;">✅ <b>{SITE_NAMES[DONE_S]} {NUMS[DONE_S]}</b>·已完成</span>' in h, h)
check("停止站：橙字加粗 + ■ + 已停止",
      f'<span style="color:{STATS_STYLE["stopped"][0]};font-weight:bold;">■ <b>{SITE_NAMES[STOPPED]} {NUMS[STOPPED]}</b>·已停止</span>' in h, h)
check("进行中：蓝字常规 + ● + 进行中",
      f'<span style="color:{STATS_STYLE["running"][0]};">● <b>{SITE_NAMES[RUNNING]} {NUMS[RUNNING]}</b>·进行中</span>' in h, h)
check("总数前缀保留", h.startswith("已采 1072 家　"), h[:30])
check("在采站点全部有标识", h.count("<span") == len(SITE_ORDER), h.count("<span"))
check("空状态降级为纯文本", build_stats_html(text, None) == text)
check("部分状态只标给出的站（其余归进行中）",
      build_stats_html(text, {DONE_S: "done"}).count("已完成") == 1 and
      build_stats_html(text, {DONE_S: "done"}).count("进行中") == len(SITE_ORDER) - 1,
      build_stats_html(text, {DONE_S: "done"}))
check("已采家数与首站间不吞分隔", h.split("　")[1].startswith("<span"), h.split("　")[1][:20])
check("无低覆盖度字形（避开小字号豆腐块）", "⏳" not in h and "⏹" not in h, h)
check("站点顺序与 SITE_ORDER 一致",
      [h.index(SITE_NAMES[s]) for s in SITE_ORDER] == sorted(h.index(SITE_NAMES[s]) for s in SITE_ORDER))
check("三位数家数不误切", "<b>八方资源网 285</b>·已完成" in h and "<b>顺企网 321</b>·已停止" in h, h)

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
check("补发文本渲染出 ✅ 八方资源网", f'✅ <b>{SITE_NAMES["b2b168"]} 0</b>·已完成' in html, html)

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
