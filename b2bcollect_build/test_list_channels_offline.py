# 离线自测：三平台名单通道并行（企知道 / 风鸟 / 爱企查）—— 2026-11-10 用户新需求①
# 用户原话：「3 个平台根据公司名单同步进行采集（比如 100 条，也是 100 条从头开始采集），
# 采集完后合并数据，即 3 个平台采到的数据合并到一个文件里（同样的，多个手机号、邮箱等
# 也是用英文逗号隔开）」「如果没有填关键词，只选择了公司名单，也能点击开始采集」。
# 这套断言盯的就是四件最容易做错的事：
#   ① 分摊 vs 从头：一份 master 名单必须被**每一路**各跑一遍，不是三家分掉；
#   ② 零遗漏：凡「根本没查成」（额度/拦截/限流/链路/空壳/异常）一律回队首、不记 tried；
#   ③ 分级熔断：一路摔倒不能把整份名单卡死，也不能把另两路一起拖停；
#   ④ 合并口径：三路同名公司在清洗层并成一行，手机号/邮箱英文逗号取并集。
# 全程不联网、不启 Chrome：通道采集器全部注入假实现，Qt 走 offscreen，时间走虚拟时钟。
import os
import sys
import time as _real_time
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")   # 无头跑 Qt，不打扰用户桌面
sys.path.insert(0, "/Users/jhnoplan/Desktop/PythonDemo/b2bcollect_build")

import parallel_session as ps
import site_adapters as ad
from parallel_session import ParallelSession, STATS_LABELS, STATUS_ORDER

FAILS = []
PASS = 0


def check(name, cond, extra=""):
    global PASS
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAILS.append(name)
        print(f"  ✗ {name}{('：' + extra) if extra else ''}")


class Clock:
    """虚拟时钟：把退避/等待变成瞬时推进。

    熔断逻辑里全是「等 60 秒重试」「等 10 分钟登录」，用真实时间跑一轮要半小时；
    更关键的是真实时间下 busy-wait 会真的空转，测试既不慢也不稳。只替换
    parallel_session 模块看到的 time，不动全局 time 模块。"""

    def __init__(self):
        self.now = 1_000_000.0
        self.slept = []

    def time(self):
        return self.now

    def sleep(self, secs):
        s = max(0.0, float(secs or 0))
        self.slept.append(s)
        self.now += s

    def __getattr__(self, name):
        return getattr(_real_time, name)


CLOCK = Clock()
ps.time = CLOCK


class _NoSc:
    """shared_chrome 替身：本套件一次都不许碰用户桌面上真在跑的共享 Chrome。

    9255 是真实端口（用户工具可能正开着），而 close_browser/open_login_page 都会
    `import shared_chrome`——不拦掉就是拿真浏览器做离线断言。"""
    alive = True
    released = []

    @classmethod
    def ensure_shared_chrome(cls, log, port, headed):
        return cls.alive

    @classmethod
    def is_alive(cls, port):
        return cls.alive

    @classmethod
    def release_tabs(cls, pinned, port, log=None):
        cls.released.append(list(pinned or []))
        return len(pinned or [])

    @classmethod
    def set_all_windows_state(cls, state, port):
        return 0


sys.modules["shared_chrome"] = _NoSc


class FakeChannel:
    """假名单通道：只实现编排层用到的契约方法，产出由脚本逐条喂。

    脚本项 = (kind, 附加字段)：kind ∈ hit/missing/retry/shell/blocked/quota/throttle/error。
    hit 且 _drop=True 模拟「查到但被属地关卡拦下」（不进 results）。"""

    LIST_CHANNEL = True
    NEEDS_BROWSER = True
    MIN_DELAY = 2.0

    def __init__(self, slug, name, script=(), delay=0.0, attach_ok=True, login="ok"):
        self.PLATFORM_SLUG = slug
        self.PLATFORM_NAME = name
        self.script = list(script)
        self.delay_between = delay
        self.results = []
        self.queried = []               # 实际发起查询的公司名（含顺序）
        self.added = []                 # 真正进表的公司名
        self.dropped = []               # 查到但被属地关卡拦下的公司名
        self._stop_requested = False
        self._pause_requested = False
        self._stat_queried = 0
        self._stat_found = 0
        self._stat_missing = 0
        self._pf_dropped = 0
        self._pf_unknown = 0
        self.province = ""
        self.attach_calls = 0
        self.login_calls = 0
        self.attach_ok = attach_ok
        self.login_seq = list(login) if isinstance(login, str) else list(login)
        self.block_reason = ""
        self.unblock_ok = True
        self.close_tab_calls = 0
        self.reset_calls = 0
        self.classify_map = {
            "hit": ad.OUTCOME_HIT, "missing": ad.OUTCOME_MISSING,
            "retry": ad.OUTCOME_RETRY, "shell": ad.OUTCOME_SHELL,
            "blocked": ad.OUTCOME_BLOCKED, "quota": ad.OUTCOME_QUOTA,
            "throttle": ad.OUTCOME_THROTTLE,
        }

    # ── 浏览器接入 ──────────────────────────────────────────
    def is_attached(self):
        return getattr(self, "_attached", False)

    def attach(self):
        self.attach_calls += 1
        if self.attach_ok:
            self._attached = True
        return bool(self.attach_ok)

    def login_state(self):
        self.login_calls += 1
        s = self.login_seq[min(self.login_calls - 1, len(self.login_seq) - 1)] \
            if self.login_seq else "ok"
        return s, "离线假通道"

    # ── 拦截与查询 ──────────────────────────────────────────
    def _detect_block(self):
        return self.block_reason

    def _wait_unblock(self, reason, max_wait=None):
        return bool(self.unblock_ok)

    def search_company(self, name):
        self.queried.append(name)
        if self.script:
            kind, extra = self.script.pop(0)
        else:
            kind, extra = "hit", {}
        if kind == "error":
            raise RuntimeError(extra.get("错误", "离线注入异常"))
        r = {"公司名": name, "电话": "", "邮箱": ""}
        r.update(extra)
        r["_kind"] = kind
        return r

    def classify(self, result):
        kind = (result or {}).get("_kind", "hit")
        return self.classify_map.get(kind, ad.OUTCOME_HIT), str(result.get("why", kind))

    def _add_result(self, result):
        if result.get("_drop"):                    # 属地关卡拦下（外省/判不出）
            self.dropped.append(result.get("公司名"))
            self._pf_dropped += 1
            return None
        self.results.append(result)
        self.added.append(result.get("公司名"))
        return result

    def bump_stat(self, found=None, missing=None):
        self._stat_queried += 1
        if found:
            self._stat_found += 1
        elif missing:
            self._stat_missing += 1

    def stats(self):
        return {"pages": 0, "listed": self._stat_queried, "ok": self._stat_found,
                "early": 0, "filtered": 0, "no_contact": self._stat_missing,
                "failed": max(0, self._stat_queried - self._stat_found - self._stat_missing),
                "got": len(self.results), "pf_miss": self._pf_dropped,
                "pf_unknown": self._pf_unknown}

    def snapshot_results(self):
        return list(self.results)

    def _to_cleaner_record(self, r):
        return {"公司名": r.get("公司名") or "", "联系人": "", "岗位": "",
                "电话": r.get("电话") or "", "邮箱": r.get("邮箱") or "",
                "微信号": r.get("微信号") or "", "地址": r.get("地址") or "",
                "官网": "", "数据来源": self.PLATFORM_NAME, "状态": "", "备注": "",
                "行业": "", "主营": "", "所在区域": ""}

    def reset_run_state(self):
        self.reset_calls += 1
        self.results = []
        self.queried = []
        self.added = []
        self.dropped = []
        self._stat_queried = self._stat_found = self._stat_missing = 0
        self._stop_requested = False
        self._pause_requested = False
        return True

    def close_tab(self):
        self.close_tab_calls += 1


def _no_live_crawl(*_a, **_k):
    """关键词通道的兜底闸门：被调用就说明离线装配漏了口子（宁可断言失败也不联网）。"""
    raise AssertionError("离线套件不该启动关键词爬取")


def make_session(script_map=None, n_companies=3, slugs=("qzd", "riskbird", "aiqicha")):
    """造一个只挂假名单通道的会话（三路通道名与真平台一致，产出契约同源）。"""
    s = ParallelSession(enabled=[])
    # enabled=[] 必须是「一个关键词平台都不开」：曾经 `enabled or SITE_ORDER` 把空列表
    # 当成默认全开，于是这个自称离线的套件真的起了 9 条联网爬取线程（2026-11-10 抓到）
    assert not s.enabled, "离线套件不该启用任何关键词站点"
    for _c in s.crawlers.values():
        _c.crawl = _no_live_crawl
    names = {"qzd": "企知道", "riskbird": "风鸟", "aiqicha": "爱企查"}
    s.channels = {slug: (script_map or {}).get(slug) or FakeChannel(slug, names[slug])
                  for slug in slugs}
    s.qzd = s.channels.get("qzd")
    tmp = tempfile.mkdtemp(prefix="lc_offline_")
    s.pending.bind(os.path.join(tmp, "task"))
    for slug, c in s.channels.items():
        s.list_enabled[slug] = True
    return s, tmp


def seed(s, n=3, prefix="公司"):
    s.pending.seed([f"{prefix}{i}有限公司" for i in range(1, n + 1)])
    return s.pending.master()


def run(s, slug):
    s._run_list_channel(slug)


def status(s, slug, name):
    """某一家在某平台队列里的进度文案（与「待采集公司名单.xlsx」状态列同源）。"""
    return s.pending.queue(slug).status_of(name)


print("A. 平台注册表与开关口径（三路都认得、都能单独开关）")
check("名单通道注册表顺序＝企知道→风鸟→爱企查（企知道默认第一路）",
      ps.LIST_CHANNEL_SLUGS == ("qzd", "riskbird", "aiqicha"), str(ps.LIST_CHANNEL_SLUGS))
check("三路站名进 SITE_NAMES（原始表「数据来源」对得上号）",
      {"风鸟", "爱企查", "企知道"} <= set(ps.SITE_NAMES.values()))
check("看板名字单一来源：名单通道带「补采」后缀",
      STATS_LABELS["riskbird"] == "风鸟补采" and STATS_LABELS["qzd"] == "企知道补采")
check("状态行解析顺序＝关键词各站 + 三路补采",
      STATUS_ORDER[:len(ps.SITE_ORDER)] == tuple(ps.SITE_ORDER)
      and STATUS_ORDER[-3:] == ("qzd", "riskbird", "aiqicha"))
check("平台中文名与待采台账 CHANNEL_LABELS 同源（台账列名↔界面↔PLATFORM_NAME）",
      all(ad.LIST_CHANNEL_LABELS[slug] == ch.PLATFORM_NAME
          for slug, ch in ad.build_list_channels().items()))

s, _ = make_session()
on, _added = s.bind_pending(tempfile.mkdtemp(), "", want_slugs=["riskbird", "aiqicha"])
check("只勾风鸟＋爱企查：企知道那一路不开（默认企知道 ≠ 强制）",
      s.list_enabled["riskbird"] and s.list_enabled["aiqicha"] and not s.list_enabled["qzd"]
      and on is True)
on2, _ = s.bind_pending(tempfile.mkdtemp(), "", want_qzd=True)
check("老参数 want_qzd=True 仍然只开企知道（旧断言语义不变）",
      s.list_enabled == {"qzd": True, "riskbird": False, "aiqicha": False} and on2 is True)
on3, _ = s.bind_pending(tempfile.mkdtemp(), "", want_slugs=["qzd", "aiqicha"], want_qzd=False)
check("两个参数同时给时旧语义覆盖企知道，不留互相矛盾的开关",
      s.list_enabled["qzd"] is False and s.list_enabled["aiqicha"] is True)
on4, _ = s.bind_pending(tempfile.mkdtemp(), "", want_slugs=["qzd", "nosuch"])
check("勾了本机没有的平台：不崩、不静默少跑（返回仍为 True 且报缺失）",
      on4 is True and s.list_enabled["qzd"] is True and s.list_enabled.get("nosuch") is None)
s_auto, _ = make_session()
n = s_auto.bind_pending(tempfile.mkdtemp(), "", want_qzd=None)[0]
check("什么都没勾、名单也空：默认不开任何一路（不白烧额度）", n is False)

print("B. 一份名单，三路各自从第 1 家跑到第 N 家（用户核心口径）")
names = ["甲公司", "乙公司", "丙公司"]
chan = {slug: FakeChannel(slug, ps.SITE_NAMES[slug],
                          script=[("hit", {"电话": f"1390806050{ i + 1}"})
                                  for i in range(len(names))])
        for slug in ("qzd", "riskbird", "aiqicha")}
s, _ = make_session(chan, slugs=tuple(chan))
s.pending.seed(names)
for slug in chan:
    run(s, slug)
check("三路查的都是同一份 master（100 条就是各查 100 条，不是三家分摊）",
      all(c.queried == names for c in chan.values()),
      str({k: c.queried for k, c in chan.items()}))
check("三路各自都查到 3 家（每路 collected 独立记满）",
      all(s.pending.counts(slug)["collected"] == 3 for slug in chan),
      str({slug: s.pending.counts(slug) for slug in chan}))
check("三路台账互不污染（企知道记 collected 不会让风鸟跳过）",
      all(s.pending.counts(slug)["tried"] == 3 for slug in chan))
check("master 总数与每路 pending 归零（跑完就是跑完，不留悬挂）",
      s.pending.channel_counts(list(chan))["__master__"]["pending"] == 3
      and all(s.pending.counts(slug)["pending"] == 0 for slug in chan))
# 关掉其中一路的开关再跑：另两路必须照跑（一路的开关不改变另一路的进度）
chan["aiqicha"].queried.clear()
s.list_enabled["aiqicha"] = False
run(s, "aiqicha")
check("关掉某一路开关后这一路一家都不查（不偷偷烧额度）", chan["aiqicha"].queried == [])
chan["riskbird"].queried.clear()
run(s, "riskbird")
check("另各路不受影响：风鸟再跑一遍是「名单已排空」而不是把别路进度当自己的",
      chan["riskbird"].queried == [])

print("C. 分级熔断：每种结论都要落在正确的账目上")
# C1 额度用尽：立即让路 + 这家回队首（不记 tried，明天还能查）
c1 = FakeChannel("riskbird", "风鸟", script=[("quota", {"why": "额度用尽待重试"}),
                                            ("hit", {})])
s, _ = make_session({"riskbird": c1})
seed(s, 2)
run(s, "riskbird")
check("额度用尽＝本路当天让路（不再查下一家）", c1.queried == ["公司1有限公司"])
check("额度用尽那一家回队首、没记 tried（明天额度恢复仍能查到它）",
      s.pending.counts("riskbird")["pending"] == 2
      and status(s, "riskbird", "公司1有限公司") in ("待重试", "待采集"))
check("额度让路记进 halted（看板要显示「已停止」而不是骗人说跑完）",
      s._rt("riskbird")["halted"] is True)
# C2 频率限流：退避后重试同一家，不记 tried
c2 = FakeChannel("qzd", "企知道",
                 script=[("throttle", {"why": "访问频繁待重试"}), ("hit", {})])
s, _ = make_session({"qzd": c2})
seed(s, 1)
# 量「两次查询同一家之间虚拟时钟推进了多少」：这才是「确实退避过」的直接证据
# （_list_wait 是 0.5 秒步进的可打断等待，看 sleep 粒度会得出错误结论）
_at = []
_sc_real = c2.search_company
c2.search_company = lambda n: (_at.append(CLOCK.now), _sc_real(n))[1]
run(s, "qzd")
_gap = (_at[1] - _at[0]) if len(_at) >= 2 else 0.0
check("限流走退避重试（同一家确实退了 ≥60 秒再查，不是原地死循环）",
      _gap >= 60 and c2.queried[:2] == ["公司1有限公司"] * 2, f"gap={_gap} queried={c2.queried}")
check("限流后同一家重查成功、记 collected",
      c2.queried == ["公司1有限公司", "公司1有限公司"]
      and s.pending.counts("qzd")["collected"] == 1)
# C3 拦截：查询前停在验证页且解不开 → 让路 + 回队首
c3 = FakeChannel("aiqicha", "爱企查")
c3.block_reason = "安全验证"
c3.unblock_ok = False
s, _ = make_session({"aiqicha": c3})
seed(s, 1)
run(s, "aiqicha")
check("查询前被拦且等不到解除：这一家没查成，回队首",
      c3.queried == [] and s.pending.counts("aiqicha")["pending"] == 1
      and s._rt("aiqicha")["halted"] is True)
# C3b 回执里带拦截结论（连续 2 家＝不是偶发）。旧语义是「本路先让路」，2026-11-11 用户
# 实机反馈推翻了它：让路之后用户过完验证也接不上，只能重开任务。现在改成转入持续监测，
# 监测到验证解除就自动接着采同一家（见 _recover_after_block / _watch_block_cleared）。
c3b = FakeChannel("aiqicha", "爱企查",
                  script=[("blocked", {"why": "拦截未解除"}), ("blocked", {"why": "拦截未解除"}),
                          ("hit", {})])
s, _ = make_session({"aiqicha": c3b})
seed(s, 3)
run(s, "aiqicha")
check("回执判拦截：第一家退避重查、第二家仍拦则转入持续监测（不再把本路永久判死）",
      c3b.queried == ["公司1有限公司"] * 3 + ["公司2有限公司", "公司3有限公司"]
      and s._rt("aiqicha")["halted"] is False, str(c3b.queried))
# 半开→闭合的计数口径要分清：watch_cycles 是「连着几轮放行不了」（查到就归零，用来判收工），
# watch_total 是「本轮累计进过几次监测」（只增不清，用来解释这一路为什么慢）。
check("查成之后熔断器回到 CLOSED（watch_cycles 清零），但本轮进过监测的次数留痕（watch_total）",
      s._rt("aiqicha")["watch_cycles"] == 0
      and s._rt("aiqicha")["watch_total"] == 1,
      f"cycles={s._rt('aiqicha')['watch_cycles']} total={s._rt('aiqicha')['watch_total']}")
check("监测判定验证已解除后接着采这一家，名单一路跑到收尾（一家都没被拦掉）",
      s.pending.counts("aiqicha")["collected"] == 3
      and s.pending.counts("aiqicha")["pending"] == 0, str(s.pending.counts("aiqicha")))
# C4 链路异常（可重采）：连续 4 家才休眠，且一家都不许记「查不到」
c4 = FakeChannel("qzd", "企知道",
                 script=[("retry", {"why": "浏览器错误"}) for _ in range(5)] + [("hit", {})])
s, _ = make_session({"qzd": c4})
seed(s, 5)
run(s, "qzd")
check("链路异常连续 4 家 → 本路休眠（不再一家家白烧）",
      s._rt("qzd")["halted"] is True and c4.queried.count("公司1有限公司") >= 1)
check("断网那几家全部退回队首、没记 tried（用户要求零遗漏）",
      status(s, "qzd", "公司1有限公司") in ("待重试", "待采集"),
      status(s, "qzd", "公司1有限公司"))
# C5 抛异常与「可重采」同口径
c5 = FakeChannel("qzd", "企知道",
                 script=[("error", {"错误": "浏览器掉线"}) for _ in range(4)])
s, _ = make_session({"qzd": c5})
seed(s, 2)
run(s, "qzd")
check("通道抛异常＝没查成：回队首并计数熔断，绝不记「查不到」",
      s._rt("qzd")["halted"] is True
      and s.pending.counts("qzd")["collected"] == 0
      and status(s, "qzd", "公司1有限公司") in ("待重试", "待采集"))
# C6 空壳：连续 3 家 → 先重过登录门，登录确实没掉才休眠
c6 = FakeChannel("riskbird", "风鸟", script=[("shell", {"why": "详情字段全空"}) for _ in range(4)])
c6.login_seq = ["ok", "need_login", "ok"]   # 第 1 次＝懒附加门，第 2/3 次＝空壳后的重过登录门
s, _ = make_session({"riskbird": c6})
seed(s, 4)
run(s, "riskbird")
check("连续空壳先按「登录态掉了」处理：重过登录门成功后继续跑",
      c6.queried[:4] == ["公司1有限公司"] * 4 and s._rt("riskbird")["halted"] is False)
c7 = FakeChannel("riskbird", "风鸟", script=[("shell", {}) for _ in range(5)])
c7.login_seq = ["ok"]
s, _ = make_session({"riskbird": c7})
seed(s, 5)
run(s, "riskbird")
check("登录还在却连续空壳＝不是登录问题：休眠别白烧请求",
      s._rt("riskbird")["halted"] is True and s.pending.counts("riskbird")["collected"] == 0)
# C7 查不到：记 tried 后丢弃（用户约定）
c8 = FakeChannel("qzd", "企知道", script=[("missing", {"why": "未找到"}), ("hit", {})])
s, _ = make_session({"qzd": c8})
seed(s, 2)
run(s, "qzd")
check("平台确实查不到 → 记「已查过」并丢弃，不重复烧请求",
      status(s, "qzd", "公司1有限公司") == "已查过"
      and s.pending.counts("qzd")["collected"] == 1 and c8.queried[1] == "公司2有限公司")
check("「查不到」与「没查成」在账上分开（missing 计数只给真查不到的）",
      c8._stat_missing == 1 and c8._stat_queried == 2)

print("D. 属地关卡：查到但外省 → 查过未入库（账实相符）")
c9 = FakeChannel("aiqicha", "爱企查",
                 script=[("hit", {"_drop": True, "电话": "13908060501"}), ("hit", {})])
s, _ = make_session({"aiqicha": c9})
seed(s, 2)
run(s, "aiqicha")
check("外省结果不进表、但台账记「已查过」（不回队反复烧请求）",
      c9.dropped == ["公司1有限公司"] and status(s, "aiqicha", "公司1有限公司") == "已查过")
check("被属地拦下的一家不算「查到」（看板不会出现查到 2 家、入库只有 1 条）",
      c9._stat_found == 1 and c9._stat_queried == 2
      and s.pending.counts("aiqicha")["collected"] == 1)

print("E. 一路摔倒不拖别路（三路真并行）")
cq = FakeChannel("qzd", "企知道", script=[("quota", {})])
cr = FakeChannel("riskbird", "风鸟", script=[("hit", {}), ("hit", {}), ("hit", {})])
ca = FakeChannel("aiqicha", "爱企查", script=[("hit", {}), ("hit", {}), ("hit", {})])
s, _ = make_session({"qzd": cq, "riskbird": cr, "aiqicha": ca})
seed(s, 3)
for slug in ("qzd", "riskbird", "aiqicha"):
    run(s, slug)
check("企知道当天额度用尽让路，风鸟/爱企查各自把整份名单跑完",
      cq.queried == ["公司1有限公司"] and cr.queried == ["公司1有限公司", "公司2有限公司", "公司3有限公司"]
      and ca.queried == cr.queried)
check("让路只影响本路状态位（另两路 halted 为假）",
      s._rt("qzd")["halted"] is True and s._rt("riskbird")["halted"] is False
      and s._rt("aiqicha")["halted"] is False)
check("三路台账各记各的：风鸟/爱企查 collected=3、企知道=0",
      (s.pending.counts("riskbird")["collected"],
       s.pending.counts("aiqicha")["collected"],
       s.pending.counts("qzd")["collected"]) == (3, 3, 0))

print("F. 合并到一个文件：三路同名公司 → 一行，手机/邮箱英文逗号并集")
mk = lambda slug, name, pairs: FakeChannel(
    slug, name, script=[("hit", {"电话": p, "邮箱": e}) for p, e in pairs])
f_q = mk("qzd", "企知道", [("13908060501", "a@b.com"), ("13908060502", "")])
f_r = mk("riskbird", "风鸟", [("13908060503;13908060504", "c@d.com"), ("13908060505", "")])
f_a = mk("aiqicha", "爱企查", [("13908060506", "e@f.com;g@h.com"), ("", "")])
s, tmp = make_session({"qzd": f_q, "riskbird": f_r, "aiqicha": f_a})
seed(s, 2)
for slug in ("qzd", "riskbird", "aiqicha"):
    run(s, slug)
rows = s.clean_records()
check("三路 6 条产出合并成 2 行（按公司名跨平台合并到一个文件）", len(rows) == 2, str(len(rows)))
row1 = next((r for r in rows if r.get("客户名称") == "公司1有限公司"), None)
phones = (row1 or {}).get("手机号", "")
emails = (row1 or {}).get("邮箱", "")
check("公司1 手机号＝三路并集且用英文逗号分隔",
      all(m in phones for m in ("13908060501", "13908060503", "13908060504", "13908060506"))
      and ";" not in phones and "，" not in phones, phones)
check("公司1 邮箱＝三路并集且用英文逗号分隔",
      all(e in emails for e in ("a@b.com", "c@d.com", "e@f.com", "g@h.com"))
      and ";" not in emails, emails)
check("合并行的「数据来源」含三路平台名（合并后仍可追溯哪一路给的）",
      all(p in str((row1 or {}).get("数据来源") or "") for p in ("企知道", "风鸟", "爱企查")),
      str((row1 or {}).get("数据来源")))
# ── 落盘取证：内存里合好 ≠ 「合并到一个文件」 ──────────────────────────────
# 用户要的是文件，所以必须真写 xlsx 再回读行列：曾经只断言内存行数，结果列结构
# 错位、双块列头只填了一块这类缺陷完全检不出来（2026-11-10 教训）。
# 模板查找先空掉：不蹭用户桌面上的真实模板，表头走内置兜底，断言才是确定的。
from openpyxl import load_workbook
_real_locate = ps._cleaner.locate_template
ps._cleaner.locate_template = lambda *a, **k: ""
_raw_p, _clean_p = Path(tmp) / "企业数据_原始.xlsx", Path(tmp) / "企业数据_清洗后入库数据.xlsx"
try:
    _ok_raw = s.export_excel(str(_raw_p))
    _ok_clean = s.export_cleaned_excel(str(_clean_p), template_path="")
finally:
    ps._cleaner.locate_template = _real_locate
check("三路产出真落盘成一个清洗后文件（不只在内存里合好）",
      bool(_ok_raw and _ok_clean and _clean_p.exists()))
_wb = load_workbook(_raw_p, read_only=True)
_ws = _wb.worksheets[0]
_raw_rows = [r for r in _ws.iter_rows(min_row=2, values_only=True) if any(r)]
_wb.close()
check("原始表逐平台各留一行（6 行，事后追得到哪一路给的号）",
      len(_raw_rows) == 6, str(len(_raw_rows)))
_wb2 = load_workbook(_clean_p, read_only=True)
_grid = [list(r) for r in _wb2.worksheets[0].iter_rows(values_only=True)]
_sheet_title = _wb2.worksheets[0].title
_wb2.close()
_hdr, _body = _grid[0], [r for r in _grid[1:] if any(r)]
_i = {c: [k for k, v in enumerate(_hdr) if v == c] for c in
      ("客户名称", "手机号", "邮箱", "数据来源")}
check("清洗后文件收成 2 行（三路同名公司在同一个文件里合并完成）",
      len(_body) == 2, str(len(_body)))
_f1 = next(r for r in _body if r[_i["客户名称"][0]] == "公司1有限公司")
for _col, _vals in (("手机号", ("13908060501", "13908060503", "13908060504", "13908060506")),
                    ("邮箱", ("a@b.com", "c@d.com", "e@f.com", "g@h.com"))):
    _cell = str(_f1[_i[_col][0]] or "")
    check(f"文件里「{_col}」列＝三路并集且只用英文逗号分隔",
          all(v in _cell for v in _vals) and ";" not in _cell and "，" not in _cell, _cell)
check("文件里「数据来源」可追溯三路（合并后仍分得清来源）",
      all(p in str(_f1[_i["数据来源"][0]] or "") for p in ("企知道", "风鸟", "爱企查")),
      str(_f1[_i["数据来源"][0]]))
check("双块列头都填了同一份合并结果（客户库导入两块都不留空）",
      len(_i["手机号"]) == 2 and str(_f1[_i["手机号"][-1]] or "") == str(_f1[_i["手机号"][0]] or ""),
      str([_f1[k] for k in _i["手机号"]]))
check("清洗后表名保持「客户库」（入库模板兼容口径不变）", _sheet_title == "客户库", _sheet_title)
check("原始表拼接三路产出（6 条都在，一条不丢）",
      len(s.snapshot_results()) == 6, str(len(s.snapshot_results())))
check("按站分组导出含三路 slug（补采结果分列可核对）",
      {"qzd", "riskbird", "aiqicha"} <= set(s.per_site_results()))
# 关了开关也要在产出里（已补到的数据不能因为取消勾选就丢）
s.list_enabled["aiqicha"] = False
check("取消勾选的一路，已补到的结果仍在原始表里（不丢数据）",
      len(s.snapshot_results()) == 6 and "aiqicha" in s.per_site_results())
check("取消勾选的一路，已补到的结果仍参与清洗合并", len(s.clean_records()) == 2)

print("G. 看板与账目：三路各报各的数")
st = s.site_status()
check("三路都进状态表（哪一路让路了必须看得见）",
      {"qzd", "riskbird", "aiqicha"} <= set(st))
check("跑完的一路标 done（不是「还在跑」的假象）",
      all(st[x] == "done" for x in ("qzd", "riskbird", "aiqicha")), str(st))
s._rt("riskbird")["halted"] = True
check("让路的一路标 stopped（额度/拦截让路与跑完要分得开）",
      s.site_status()["riskbird"] == "stopped")
s._rt("riskbird")["halted"] = False
parts = []
orig_emit = s._emit


def spy_emit(name, *args):
    if name == "stats_signal":
        parts.append(args[0])
    return orig_emit(name, *args)


s._emit = spy_emit
s._on_site_progress()
s._emit = orig_emit
txt = parts[-1] if parts else ""
check("状态行按 STATS_LABELS 三路分列（口径不与关键词站混）",
      all(f"{STATS_LABELS[x]} " in txt for x in ("qzd", "riskbird", "aiqicha")), txt)
import b2bcollect_gui as gui
html = gui.build_stats_html(txt, s.site_status())
check("看板 HTML 渲染保留三路补采计数（过去只解析 SITE_ORDER，风鸟/爱企查会被静默丢掉）",
      all(f"{STATS_LABELS[x]} " in html for x in ("qzd", "riskbird", "aiqicha")), html)
check("site_stats 汇报三路各自账目（十键同构，看板能直接渲染）",
      all(f"{ps.SITE_NAMES[x]}补采" in s.site_stats() for x in ("qzd", "riskbird", "aiqicha")),
      str(list(s.site_stats())))

print("H. 生命周期与浏览器接入（共享 Chrome 纪律）")
s2, _ = make_session()
s2.stop_crawl()
check("停止一轮：三路都置停止位（退避中的等待因此可立刻打断）",
      all(c._stop_requested for c in s2.channels.values()))
s2.reset_results()
check("开始新任务：三路账目都复位（不清就带着昨天的额度判定整轮不进详情页）",
      all(c.reset_calls >= 1 for c in s2.channels.values()))
s2.close_browser()
check("收工：每路只关自己的标签（close_tab 各调一次，不碰共享 Chrome）",
      all(c.close_tab_calls == 1 for c in s2.channels.values()))
b = FakeChannel("riskbird", "风鸟", attach_ok=False)
s3, _ = make_session({"riskbird": b})
seed(s3, 2)
run(s3, "riskbird")
check("接不上浏览器＝瞬时故障：按上限退避重试而不是第一次就判死",
      b.attach_calls == s3._list_attach_retries + 1, str(b.attach_calls))
check("退避重试用尽才让路，名单一家都没丢",
      s3._rt("riskbird")["halted"] is True and s3.pending.counts("riskbird")["pending"] == 2)
b2 = FakeChannel("aiqicha", "爱企查", attach_ok=True, login=["need_login", "ok"])
s4, _ = make_session({"aiqicha": b2})
seed(s4, 1)
run(s4, "aiqicha")
check("未登录：限时等登录后继续（等满才休眠，不把名单记成查不到）",
      b2._stat_queried >= 1 and s4.pending.counts("aiqicha")["collected"] == 1)
check("懒附加幂等：已接上浏览器后不再重复 attach", b2.attach_calls == 1, str(b2.attach_calls))
b3 = FakeChannel("riskbird", "风鸟", attach_ok=True, login=["need_login"])
s5, _ = make_session({"riskbird": b3})
seed(s5, 1)
run(s5, "riskbird")
check("等登录超时（10 分钟）→ 本路休眠且这家回队首",
      s5._rt("riskbird")["halted"] is True and s5.pending.counts("riskbird")["pending"] == 1)

print("I. 只选公司名单、不填关键词也能启动（用户要的「精准搜索公司名拿联系方式」）")
s6, _ = make_session()
calls = []
s6._run_list_channel = lambda slug: calls.append(slug)
for _slug in s6.list_enabled:
    s6.list_enabled[_slug] = False       # 三路都关掉：只剩「无事可做」这一种可能
s6.crawl([], start_index=0)
check("没有关键词、没有勾选站点、也没开名单通道 → 明确告知无事可做（不空跑）",
      calls == [])
s6.list_enabled.update({"qzd": True, "riskbird": True, "aiqicha": True})
s6.crawl([], start_index=0)
check("只勾三平台、不填关键词：三路看门狗线程各自启动（三家都从第 1 家开始）",
      sorted(calls) == ["aiqicha", "qzd", "riskbird"], str(calls))
finishes = []
s6._emit("crawl_finished_signal") if False else None
check("纯名单模式不置 _crawling_active（排空名单后通道自己休眠，不空转）",
      s6._crawling_active is False)
s7, _ = make_session()
c_run = []
s7._run_list_channel = lambda slug: c_run.append(slug)
for _slug in s7.list_enabled:
    s7.list_enabled[_slug] = False
s7.list_enabled["qzd"] = True
s7.crawl(["太阳能路灯"])
check("关键词 + 名单同时存在：两件事一起做，互不挡路", c_run == ["qzd"])

print("J. 参数下发与安全下限（别自己撞限流）")
s8, _ = make_session()
s8.configure(delay_between=0.5, province=["广东"])
check("名单通道间隔尊重站方安全下限（低于下限就是自己撞「操作过于频繁」）",
      all(c.delay_between >= c.MIN_DELAY for c in s8.channels.values()),
      str({k: v.delay_between for k, v in s8.channels.items()}))
check("省份下发到三路名单通道（不参与关键词漏斗，不拦就会把外省放进入库数据）",
      all(c.province == "广东" for c in s8.channels.values()))
check("三路 MIN_DELAY 各自声明（企知道走接口更轻、风鸟/爱企查两次导航更贵）",
      ad.QizhidaoSite.MIN_DELAY < ad.RiskbirdSite.MIN_DELAY
      and ad._ListChannelBase.MIN_DELAY == 2.0)

print("K. 验证看门狗与名单进度（2026-11-11 用户实机反馈四条）")
# 用户原话：①「每隔 15 秒判断是否过了验证，过了验证则继续采集，没过继续监测，注意期间
# 不要刷新」；②「没有关键词，只有企知道/爱企查/风鸟采集公司名单时，也需要进度条」；
# ③爱企查那次手动过完验证工具却没反应（实为 _wait_unblock ↔ _wait_unblock_limited 互调
# 爆栈）；④「10 分钟无人处理就永久让路」让用户过完验证也只能重开任务。
check("等人过验证的节奏写成一个地方的常量（15 秒只读轮询＝用户原话）",
      ad.LIST_BLOCK_PROBE_INTERVAL == 15 and ad.LIST_BLOCK_HEAL_EVERY == 60,
      f"{ad.LIST_BLOCK_PROBE_INTERVAL}/{ad.LIST_BLOCK_HEAL_EVERY}")
_built = ad.build_list_channels()
check("三路真实通道实例都是 15 秒只读轮询（不许长出「企知道 5 秒、风鸟 30 秒」的口径分叉）",
      all(getattr(c, "block_probe_interval", None) == ad.LIST_BLOCK_PROBE_INTERVAL
          for c in _built.values()),
      str({k: getattr(c, "block_probe_interval", None) for k, c in _built.items()}))
check("会给页面动刀的两路（风鸟/爱企查）自愈周期 60 秒，单轮监测上限也大于心跳",
      all(getattr(_built[k], "block_heal_every", None) == ad.LIST_BLOCK_HEAL_EVERY
          for k in ("riskbird", "aiqicha"))
      and ad.LIST_BLOCK_WATCH_ROUND_MAX > ad.LIST_BLOCK_WATCH_HEARTBEAT)

chan_k = {slug: FakeChannel(slug, ps.SITE_NAMES[slug], delay=0.0)
          for slug in ("qzd", "riskbird", "aiqicha")}
sK, _ = make_session(chan_k, slugs=tuple(chan_k))
seed(sK, n=2)
_d0, _t0 = sK.list_progress()
check("纯名单进度：分母＝家数×路数（2 家×3 路＝6 家次，每路都要从第 1 家查到第 2 家）",
      (_d0, _t0) == (0, 6), f"{_d0}/{_t0}")
_evt_k = []
_orig_emit_k = sK._emit
sK._emit = lambda name, *a: (_evt_k.append((name,) + tuple(a)), _orig_emit_k(name, *a))[1]
sK._emit_list_progress("qzd")
check("台账把本路家次喂给进度条：一家没查也给出确定分母（不是没有分母的 0%）",
      _evt_k[-1] == ("kw_progress_signal", "", 0, 2), str(_evt_k[-1]))
sK._on_site_kw_progress("mic", 3, 9)          # 关键词那边翻了 3/9 个词
sK._emit_list_progress("qzd")
check("关键词单位与名单家次并进同一个分母（混合模式进度条不许各算各的）",
      _evt_k[-1] == ("kw_progress_signal", "", 3, 11), str(_evt_k[-1]))
sK._log = lambda m: None
sK._run_list_channel("qzd")
check("查过的两家立刻体现在台账进度上（进度条按家次真实往前走，不是卡在 0%）",
      sK.list_progress(("qzd",)) == (2, 2), str(sK.list_progress(("qzd",))))


class _BlockThenClear(FakeChannel):
    """第一家撞防采验证：限时等待失败 → 转入只读监测 → 第 N 次探测时用户过完了验证。

    监测期间任何「真的去查询」都必须炸掉测试（用户要求：没过验证不许发采集请求）。"""

    def __init__(self, slug, name, clear_after=3, **kw):
        super().__init__(slug, name, **kw)
        self.clear_after = clear_after
        self.probe_calls = 0
        self.unblock_ok = False                  # 限时等待这一轮先失败
        self.block_reason = f"{name}：需要安全验证"
        self._blocked_at = 1_000_000.0           # 模拟"已计了一段拦截时长"
        self.block_probe_interval = ad.LIST_BLOCK_PROBE_INTERVAL

    def _detect_block(self):
        self.probe_calls += 1
        if self.probe_calls >= self.clear_after:
            self.block_reason = ""               # 用户手动把滑块拖完了
        return self.block_reason

    def search_company(self, name):
        if self.block_reason:
            raise AssertionError("验证还没过就发了采集请求（该只做只读探测）")
        return super().search_company(name)


chan_h = {slug: FakeChannel(slug, ps.SITE_NAMES[slug], delay=0.0)
          for slug in ("qzd", "riskbird")}
_h = _BlockThenClear("aiqicha", "爱企查", clear_after=3)
chan_h["aiqicha"] = _h
sH, _ = make_session(chan_h, slugs=tuple(chan_h))
seed(sH, n=1)
_evt_h = []
_orig_emit_h = sH._emit
sH._emit = lambda name, *a: (_evt_h.append((name,) + tuple(a)), _orig_emit_h(name, *a))[1]
_waits = []
_orig_wait = sH._list_wait
sH._list_wait = lambda secs, c: (_waits.append(float(secs)), _orig_wait(secs, c))[1]
sH._run_list_channel("aiqicha")
check("撞验证后本路不再永久让路：过完验证自动接着采这一家（同一家最终查成）",
      _h.queried == ["公司1有限公司"], str(_h.queried))
check("监测期间的等待严格按 15 秒一跳（不是 1 秒狂探、也不是 30 秒才看一眼）",
      set(_waits) == {15.0}, str(sorted(set(_waits))[:6]))
check("监测期间只读探测、一次都没去刷新/导航/重开标签（用户：刷一下挑战就作废）",
      _h.close_tab_calls == 0 and _h.attach_calls <= 1,
      f"close_tab={_h.close_tab_calls} attach={_h.attach_calls}")
_kc = sH.pending.counts("aiqicha")
check("回队重查那一家没被记成「已查过」：最终台账正好记 1 家、名单清零",
      _kc["tried"] == 1 and _kc["pending"] == 0 and len(_h.results) == 1, str(_kc))
check("验证通过后收掉界面上的「等你处理」（block_cleared_signal），暂停的计时跟着恢复",
      any(n == "block_cleared_signal" for n, *_ in _evt_h), str([n for n, *_ in _evt_h]))
check("验证通过后重置「整段拦截」计时（否则后面每家一进来就算已超时、白绕一圈）",
      _h._blocked_at == 0.0, str(_h._blocked_at))

_never = _BlockThenClear("aiqicha", "爱企查", clear_after=10 ** 9)
sN, _ = make_session({"aiqicha": _never}, slugs=("aiqicha",))
sN._log = lambda m: None
seed(sN, n=1)
sN._run_list_channel("aiqicha")
check("一直没人过验证也不会无限盯：轮次用尽才如实结束本路（熔断器 OPEN 态有界）",
      sN._rt("aiqicha")["halted"] is True
      and sN._rt("aiqicha")["watch_cycles"] == sN._list_block_cycles,
      str(sN._rt("aiqicha")["watch_cycles"]))
check("让路时名单仍完整存盘（一家都没被记成查过，下次开始任务自动接续）",
      sN.pending.counts("aiqicha")["pending"] == 1
      and sN.pending.counts("aiqicha")["tried"] == 0)
# 上一轮盯满 5 轮判死的运行态是复用的（reset_results 只在「重置任务」里调），
# 用户过完验证直接再点「开始采集」时若带着 watch_cycles=5 的坏账，本路一撞验证
# 就当场判死——等于把「过了验证还是接不上」这个老毛病原样留在新逻辑里。
_never.clear_after = _never.probe_calls + 3      # 这一轮用户坐回去把验证过完
sN._log = lambda m: None
sN._run_list_channel("aiqicha")
check("重开任务不许带着上一轮的熔断坏账：让路后再点开始采集，本路重新获得完整监测轮次",
      _never.queried == ["公司1有限公司"] and sN.pending.counts("aiqicha")["pending"] == 0
      and sN._rt("aiqicha")["halted"] is False, str(_never.queried))
check("新一轮的监测留痕只算自己这一轮（watch_total 不跨轮累加）",
      sN._rt("aiqicha")["watch_total"] == 1
      and sN._rt("aiqicha")["watch_cycles"] == 0, str(sN._rt("aiqicha")))
check("跨轮该留的资产没被顺手清掉：浏览器仍复用（不重复附加、不重复开标签）",
      _never.attach_calls == 1, str(_never.attach_calls))
_other = FakeChannel("qzd", "企知道", delay=0.0)
sM, _ = make_session({"qzd": _other, "aiqicha": _BlockThenClear("aiqicha", "爱企查",
                                                                clear_after=10 ** 9)})
sM._log = lambda m: None
seed(sM, n=2)
for _sl in sM.list_enabled:
    sM.list_enabled[_sl] = (_sl == "qzd")       # 只开企知道那一路
sM._run_list_channel("qzd")
check("爱企查停在验证页不影响另一路：企知道这路独立把名单查到头（三路各自的线程）",
      len(_other.queried) == 2, str(_other.queried))

import inspect as _ins
_src_aqc = _ins.getsource(ad.AiqichaSite.__dict__["_wait_unblock"])
check("爱企查通道级等待走 super()._wait_unblock（RecursionError 根因：子类覆写回调 limited）",
      "super()._wait_unblock" in _src_aqc
      and "_wait_unblock_limited" not in _src_aqc.split('"""')[-1])
check("爱企查不再自己定义第二份「限时等待」（节奏统一由基类＋实例属性承担）",
      "_wait_unblock_limited" not in ad.AiqichaSite.__dict__,
      str(sorted(k for k in ad.AiqichaSite.__dict__ if "unblock" in k)))
_src_rb = _ins.getsource(ad.RiskbirdSite.__dict__["_wait_unblock"])
check("风鸟的破坏性自愈挂在 revive、heal 只做「认领回自己的标签」",
      "heal=self._ensure_own_tab" in _src_rb
      and "revive=self._goto_homepage_verified" in _src_rb)
_src_revive = _ins.getsource(ad.AiqichaSite._revive_after_block)
check("爱企查 revive 自带两道护栏：验证页还挂着不动它、没认领回独占标签绝不动页面",
      "_detect_block" in _src_revive and "pin_tab_id" in _src_revive)
print("\n═══ 三平台名单通道：%d 项断言 %s" % (
    PASS, "全部通过" if not FAILS else f"｜失败 {len(FAILS)} 项"))
for f in FAILS:
    print("   ✗ " + f)
sys.exit(1 if FAILS else 0)
