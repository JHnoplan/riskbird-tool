#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""企知道补采通道「瞬时故障退避重试」离线断言（零网络、零浏览器、零 Qt 事件循环）

覆盖 2026-11-04 08:33 事故的两处修复：
  A. parallel_session：附加失败按 chrome/driver/login 归类；chrome/driver 型退避重试，
     而不是一次失败就整轮熄火；熄火不再把这一路的勾选置 False（已补到的结果不能被导出漏掉）。
  B. riskbird_build/chrome_driver：建会话失败分「版本型/连接型」两种文案与归因；连接型先
     原地退避重试本地驱动，而不是立刻联网走 Selenium Manager 慢兜底。
跑法：./.venv/bin/python debug/qzd_channel_offline_test.py
"""
import os
import sys
import time
import types

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 本套件验的是 **b2bcollect 这一份产品**，而 customer_library_cleaner / task_registry /
# yzd_client 在三个产品目录里各有一份（不同名目录、同名模块）。riskbird_build 只需要
# 借出 block_guard / qizhidao_crawler / chrome_driver 那几份独有模块，因此它只能
# **append**（排在后面），本目录才能 insert(0)。旧写法两个目录都 insert(0)，后插的
# riskbird 反而抢到最前，于是并行层跑的是 riskbird 的清洗器与台账——那份组合任何
# 一个交付产物里都不存在（同一个坑在 batch3 回归的 7a 那一格已经红过一次）。
sys.path.insert(0, os.path.join(BASE, "b2bcollect_build"))
if os.path.join(BASE, "riskbird_build") not in sys.path:
    sys.path.append(os.path.join(BASE, "riskbird_build"))

PASS, FAIL = [], []


def check(name, got, want):
    (PASS if got == want else FAIL).append(name if got == want else f"{name}: 期望 {want!r}，实际 {got!r}")


def true(name, cond):
    check(name, bool(cond), True)


# ══════════════════════════ A. parallel_session 看门狗 ══════════════════════════
import parallel_session as ps                      # noqa: E402
import customer_library_cleaner as _cl_q           # noqa: E402
import task_registry as _tr_q                      # noqa: E402

# 导对了哪一份产品必须自证（拿实现验实现永远测不出“哪天又被副本遮蔽”）
for _nm, _mod in (("parallel_session", ps), ("customer_library_cleaner", _cl_q),
                  ("task_registry", _tr_q)):
    assert os.sep + "b2bcollect_build" + os.sep in _mod.__file__, \
        f"导错产品的 {_nm}（被另一份同名副本遮蔽）：{_mod.__file__}"


QZD = "qzd"          # N 路化后一切都按 slug 寻址（企知道只是其中的默认那一路）


class _Q:
    """企知道名单通道替身：只实现看门狗用到的那几样契约。

    classify 直接复用真适配器 QizhidaoSite.classify——测试自己另写一套判定口径，
    就等于把要验的东西换了（平台状态词一改，这里会安静地一直绿）。"""

    LIST_CHANNEL = True
    NEEDS_BROWSER = True
    PLATFORM_SLUG = QZD
    PLATFORM_NAME = "企知道"
    MIN_DELAY = 0.8

    def __init__(self, attach_seq=("fail",), login_seq=("ok",), results=None, stop_after=None,
                 record=None):
        self.driver = None
        self._stop_requested = False
        self._pause_requested = False
        self.results = list(results or [])
        self.delay_between = 0
        self._stat_found = 0
        self._stat_missing = 0
        self._stat_queried = 0
        self._pf_dropped = 0
        self._pf_unknown = 0
        self.province = ""
        self._attach_seq = list(attach_seq)
        self._login_seq = list(login_seq)
        self._stop_after = stop_after       # 查到第 N 家后请求停止（让看门狗自然收尾）
        self._record = record               # 自定义回执（空壳用例要「没有联系方式」的回执）
        self.attach_calls = 0
        self.queried = []
        self.added = []

    def is_attached(self):
        """懒附加的幂等判据：编排层不猜各平台怎么接浏览器（爱企查就不是看 driver）。"""
        return self.driver is not None

    def attach(self):
        self.attach_calls += 1
        ok = self._attach_seq.pop(0) if self._attach_seq else "fail"
        if ok == "ok":
            self.driver = object()
            return True
        self.driver = None
        return False

    def login_state(self):
        st = self._login_seq.pop(0) if len(self._login_seq) > 1 else self._login_seq[0]
        if st == "stop_soon":               # 让 10 分钟登录门立刻结束，避免测试里空转
            self._stop_requested = True
            return "need_login", "未登录"
        return st, ""

    def _detect_block(self):
        return ""

    def _wait_unblock(self, block):
        return True

    def search_company(self, name):
        self.queried.append(name)
        if self._stop_after and len(self.queried) >= self._stop_after:
            self._stop_requested = True
        if self._record is not None:
            return dict(self._record, **{"公司名": name})
        return {"公司名": name, "电话": "13800000000"}

    def classify(self, result):
        import site_adapters as _ad
        return _ad.QizhidaoSite.classify(self, result)

    def _add_result(self, r):
        self.added.append(r)
        self.results.append(r)
        return r

    def bump_stat(self, found=None, missing=None):
        self._stat_queried += 1
        if found:
            self._stat_found += 1
        elif missing:
            self._stat_missing += 1

    def snapshot_results(self):
        return list(self.results)

    def stats(self):
        return {"查到": self._stat_found}

    def reset_run_state(self):
        self.results = []
        self.queried = []
        self.added = []
        self._stat_queried = self._stat_found = self._stat_missing = 0
        self._stop_requested = False
        self._pause_requested = False
        return True

    def close_tab(self):
        pass

    def _to_cleaner_record(self, r):
        return {"公司名": r.get("公司名") or "", "联系人": "", "岗位": "",
                "电话": r.get("电话") or "", "邮箱": r.get("邮箱") or "", "微信号": "",
                "地址": r.get("地址") or "", "官网": "", "数据来源": self.PLATFORM_NAME,
                "状态": "", "备注": "", "行业": "", "主营": "", "所在区域": ""}


class _Pending:
    """待采台账替身：一份 master + 每平台独立进度（这里只有一路，slug 参数照样收下，
    这样「编排层是不是按平台寻址」在测试里也是被验的——漏传就会 KeyError/口径串台）。"""

    def __init__(self, names):
        self._q = list(names)
        self.requeued = []
        self.marks = []
        self.is_bound = True

    def pop(self, slug=None):
        return self._q.pop(0) if self._q else None

    def requeue(self, name, slug=None):
        self.requeued.append(name)
        self._q.insert(0, name)

    def has_work(self, slug=None):
        return bool(self._q)

    def counts(self, slug=None):
        return {"pending": len(self._q), "requeued": len(self.requeued)}

    def master(self):
        return list(self._q)

    def mark_result(self, name, found, slug=None):
        self.marks.append((name, found))
        if name in self._q:
            self._q.remove(name)

    def add_waste(self, names):
        self._q.extend(names)
        return len(names)


def _session(names, q):
    """绕开 __init__（不建七个采集器、不接 Qt 信号），只装配通道线程要用的字段。"""
    s = ps.ParallelSession.__new__(ps.ParallelSession)
    s.qzd = q
    s.channels = {QZD: q}
    s.list_enabled = {QZD: True}
    s._list_rt = {}
    s._list_threads = {}
    s.pending = _Pending(names)
    s.logs = []
    s.emits = []
    s._log = lambda m, *a, **k: s.logs.append(m)
    s._emit = lambda name, *a, **k: s.emits.append((name, a))
    s._list_idle_exit = 300
    s._list_attach_retries = 5
    s._list_throttle_halt = 5
    s._crawling_active = False
    s.port = 9255
    return s


class _NoSc:
    """shared_chrome 替身：默认「共享浏览器在跑」，个别用例改成起不来。"""
    alive = True

    @staticmethod
    def ensure_shared_chrome(log, port, headed):
        return _NoSc.alive

    @staticmethod
    def is_alive(port):
        return _NoSc.alive


sys.modules["shared_chrome"] = _NoSc


class _Clock:
    """虚拟时钟：退避 5/10/20 秒、10 分钟登录门在测试里都是「推进读数」而非墙钟空转。

    旧写法直接改 ps.time.sleep（＝全局 time 模块），同进程里的 chrome_driver、
    requests 一起被改成不睡觉；这里只替换 parallel_session 一个模块看到的 time。"""

    def __init__(self):
        self.now = 1_000_000.0
        self.slept = []

    def time(self):
        return self.now

    def sleep(self, secs=0):
        s = max(0.0, float(secs or 0))
        self.slept.append(s)
        self.now += s

    def __getattr__(self, name):
        return getattr(_real_time, name)


_real_time = ps.time
ps.time = _Clock()
try:
    # ── ① 现场复刻：附加失败两次后恢复 → 必须真的把这一家补采到 ─────────────
    _NoSc.alive = True
    import block_guard
    _shell_real = block_guard.is_shell_record
    block_guard.is_shell_record = lambda r: False         # 空壳分支由 ⑤ 单独覆盖
    q1 = _Q(attach_seq=("fail", "fail", "ok"), stop_after=1)
    s1 = _session(["甲公司", "乙公司"], q1)
    s1._run_list_channel(QZD)
    block_guard.is_shell_record = _shell_real
    check("① 附加失败 2 次后第 3 次成功", q1.attach_calls, 3)
    check("① 恢复后确实补采到了公司", q1.queried, ["甲公司"])
    check("① 补到的结果进账", len(q1.results), 1)
    true("① 重试日志写出退避秒数与次数", any("5 秒后重试" in m and "第 1/5 次" in m for m in s1.logs))
    true("① 第二次退避翻倍", any("10 秒后重试" in m and "第 2/5 次" in m for m in s1.logs))
    true("① 重试日志承诺名单不丢", any("待采名单一家都不会丢" in m for m in s1.logs))
    check("① 未熄火", s1._rt(QZD)["halted"], False)
    true("① 开关全程为真", s1.list_enabled[QZD])
    true("① 已补到的结果在导出里", bool(s1._list_active(QZD)))
    true("① 收尾账目日志走到", any("企知道补采通道收工" in m for m in s1.logs))
    check("① 名字回队首恰好两次（两次失败）", s1.pending.requeued, ["甲公司", "甲公司"])

    # ── ② 一直附加不上：退避到上限后放弃，但绝不关开关、名单一家不丢 ────────
    q2 = _Q(attach_seq=("fail",) * 99)
    s2 = _session(["丙公司", "丁公司"], q2)
    s2._list_attach_retries = 3
    s2._run_list_channel(QZD)
    check("② 附加尝试 = 首次 + 3 次重试", q2.attach_calls, 4)
    check("② 失败原因归类为驱动型", s2._rt(QZD)["fail_kind"], "driver")
    check("② 到手名字回队首（不记 tried）", s2.pending.requeued, ["丙公司"] * 4)
    true("② 名单仍完整（2 家一个不少）", s2.pending.counts(QZD)["pending"] == 2)
    true("② 放弃日志说明多次重试仍未接上", any("多次重试仍未接上浏览器" in m for m in s2.logs))
    check("② 放弃标志置位", s2._rt(QZD)["halted"], True)
    true("② 放弃时不关开关（旧写法会让已补结果消失）", s2.list_enabled[QZD])
    true("② _list_can_run 反映真实可用性", not s2._list_can_run(QZD))
    check("② 从未查询任何公司（没附加成功就不烧请求）", len(q2.queried), 0)

    # ── ③ 共享浏览器起不来：归 chrome 型，文案点名「共享浏览器没起来」───────
    _NoSc.alive = False
    q3 = _Q(attach_seq=("fail",) * 99)
    s3 = _session(["戊公司"], q3)
    s3._list_attach_retries = 1
    s3._run_list_channel(QZD)
    check("③ 失败原因归类为浏览器型", s3._rt(QZD)["fail_kind"], "chrome")
    true("③ 文案点名共享浏览器", any("共享浏览器没起来" in m for m in s3.logs))
    check("③ 浏览器没起来时不去附加", q3.attach_calls, 0)
    check("③ 仍按退避重试一次后放弃", s3._rt(QZD)["halted"], True)
    _NoSc.alive = True

    # ── ④ 登录型失败：不做无意义退避重试，文案要用户去登录 ──────────────────
    q4 = _Q(attach_seq=("ok",), login_seq=("need_login", "stop_soon"))
    s4 = _session(["己公司"], q4)
    s4._run_list_channel(QZD)
    check("④ 失败原因归类为登录型", s4._rt(QZD)["fail_kind"], "login")
    check("④ 登录型不重试：只附加过一次", q4.attach_calls, 1)
    true("④ 文案要用户登录后重新开始任务",
         any("企知道还没登录" in m and "登录后重新开始任务" in m for m in s4.logs))
    check("④ 到手名字回队首", s4.pending.requeued, ["己公司"])
    true("④ 登录型不写「多次重试接不上浏览器」",
         not any("多次重试仍未接上浏览器" in m for m in s4.logs))

    # ── ⑤ 连续空壳熄火：不关开关，且日志承诺已补到的仍入库 ──────────────────
    import block_guard
    _shell_real = block_guard.is_shell_record
    block_guard.is_shell_record = lambda r: True
    try:
        # 空壳回执要「工商三件套全空 + 没有联系方式」：带电话的记录在 _shell_check 的
        # 联系方式逃生门那里是有效产出，判成空壳反而会把有联系方式的公司白烧掉
        q5 = _Q(attach_seq=("ok",), record={"法定代表人": "", "注册资本": "", "地址": ""},
                results=[{"公司名": "旧公司", "电话": "13900000000"}])
        s5 = _session(["庚公司", "辛公司", "壬公司", "癸公司"], q5)
        # 记下每次要求等待的秒数，但**照原样放行**给真 _list_wait（虚拟时钟下不占墙钟）：
        # 只验「有没有给过这一次 180 秒窗口」，不把被验的等待函数换成替身
        _waits5 = []
        _real_wait5 = s5._list_wait
        s5._list_wait = lambda secs, ch=None: (_waits5.append(float(secs)) or _real_wait5(secs, ch))
        s5._run_list_channel(QZD)
        # 3 家空壳触发窗口 + 窗口后 1 家当恢复判据＝4 家收工。旧写法在窗口后把 streak
        # 清零，于是还要再烧 gate 家才熄火（实跑 6 家）：白烧一倍名单换不来更多结论。
        check("⑤ 空壳熄火＝连续 3 家＋窗口后只再拿 1 家当恢复判据", len(q5.queried), 4)
        check("⑤ 熄火标志置位", s5._rt(QZD)["halted"], True)
        true("⑤ 不关开关", s5.list_enabled[QZD])
        true("⑤ 承诺已补到的仍入库", any("仍照常入库" in m for m in s5.logs))
        true("⑤ 旧结果仍在导出（开关未关）", bool(s5._list_active(QZD)))
        check("⑤ 给过一次恢复窗口（量级＝SHELL_RECOVERY_WAIT_S）",
              sum(1 for v in _waits5 if v >= ps.SHELL_RECOVERY_WAIT_S), 1)
        check("⑤ 窗口只给一次（日志与引导信号都不重复承诺）",
              (sum(1 for m in s5.logs if "本轮只给这一次机会" in m),
               sum(1 for n, a in s5.emits
                   if n == "login_required_signal" and a and "详情空壳" in str(a[0]))),
              (1, 1))
    finally:
        block_guard.is_shell_record = _shell_real

    # ── ⑥ 退避等待可被停止打断（不能闷头睡死 60 秒）─────────────────────────
    q6 = _Q()
    s6 = _session(["子企业"], q6)
    q6._stop_requested = True
    t0 = time.time()
    check("⑥ 停止打断返回 False", s6._list_wait(60, q6), False)
    true("⑥ 打断几乎不耗时", time.time() - t0 < 2)
    q6._stop_requested = False
    check("⑥ 正常等待返回 True", s6._list_wait(0.01, q6), True)

    # ── ⑦ 废数据入列提示：熄火后不再空头承诺「企知道将逐家补采」─────────────
    s2._waste_sink(["丑公司"])
    true("⑦ 熄火后改口为「自动接续」", any("自动接续" in m for m in s2.logs))
    true("⑦ 熄火后不再承诺本轮补采", not any("补采通道将逐家补采" in m for m in s2.logs[-1:]))
    s1._waste_sink(["寅公司"])
    true("⑦ 通道正常时仍承诺逐家补采", any("补采通道将逐家补采" in m for m in s1.logs))
finally:
    ps.time = _real_time

# ══════════════════ B. chrome_driver 失败归因与连接型退避 ══════════════════
import chrome_driver as cd                         # noqa: E402
import selenium.webdriver as _sw                   # noqa: E402
import selenium.webdriver.chrome.service as _svc_mod  # noqa: E402


class _FakeService:
    """替掉真实 Service：测试里不启动任何 chromedriver 进程。"""

    def __init__(self, executable_path=None, **kw):
        self.executable_path = executable_path

    def stop(self):
        pass


check("⑧ 连接型报错判 connect",
      cd.classify_driver_error("Message: session not created: cannot connect to chrome "
                               "at 127.0.0.1:9255", "156.0.8078.47", "156"), "connect")
check("⑧ 版本不符判 version",
      cd.classify_driver_error("This version of chromedriver only supports Chrome version 135",
                               "135.0.7049.84", "156"), "version")
check("⑧ 含糊报错按主版本比对兜底",
      cd.classify_driver_error("some weird failure", "135.0.7049.84", "156"), "version")
check("⑧ 版本问不到时保守归 connect", cd.classify_driver_error("boom", "", ""), "connect")

_cand_real, _dver_real, _major_real = cd.candidates, cd.driver_version, cd.chrome_major
_svc_real, _chrome_real = _svc_mod.Service, _sw.Chrome
_cd_sleep_real = cd.time.sleep
cd.candidates = lambda ports=None: ["/fake/dir156/chromedriver", "/fake/dir135/chromedriver"]
cd.driver_version = lambda p: "156.0.8078.47" if "156" in str(p) else "135.0.7049.84"
cd.chrome_major = lambda ports=None: "156"
_svc_mod.Service = _FakeService
cd.time.sleep = lambda *a, **k: None
try:
    # ⑨ 前两次连接型失败、退避重试轮第三次成功 → 绝不走 Selenium Manager
    calls9 = {"local": 0, "sm": 0}

    def _chrome9(*args, **kw):
        if "service" not in kw:
            calls9["sm"] += 1
            raise AssertionError("连接型失败不该联网走 Selenium Manager")
        calls9["local"] += 1
        if calls9["local"] <= 2:
            raise Exception("Message: session not created: cannot connect to chrome "
                            "at 127.0.0.1:9255")
        return types.SimpleNamespace(tag="driver9")

    _sw.Chrome = _chrome9
    logs9 = []
    d9 = cd.create(options=object(), log=lambda m: logs9.append(m), ports=[9255])
    check("⑨ 退避后本地重试成功", getattr(d9, "tag", ""), "driver9")
    check("⑨ 未联网兜底", calls9["sm"], 0)
    check("⑨ 本地尝试次数（2 败 + 1 成）", calls9["local"], 3)
    true("⑨ 日志点名是哪个驱动、什么版本",
         any("dir156/chromedriver 156.0.8078.47" in m for m in logs9))
    true("⑨ 日志写明「连不上浏览器」而非版本问题", any("连不上浏览器" in m for m in logs9))
    true("⑨ 退避重试日志注明不联网", any("原地重试一遍" in m and "不联网下载" in m for m in logs9))

    # ⑩ 版本型失败：不原地退避重试（换驱动才是解），仍按老路走 Selenium Manager 兜底
    calls10 = {"local": 0, "sm": 0}

    def _chrome10(*args, **kw):
        if "service" not in kw:
            calls10["sm"] += 1
            raise Exception("Message: cannot find Chrome binary")
        calls10["local"] += 1
        raise Exception("This version of chromedriver only supports Chrome version 135")

    _sw.Chrome = _chrome10
    logs10 = []
    err10 = ""
    try:
        cd.create(options=object(), log=lambda m: logs10.append(m), ports=[9255])
    except RuntimeError as e:
        err10 = str(e)
    true("⑩ 版本型仍抛错（不静默）", bool(err10))
    true("⑩ 异常文案归因到版本不匹配", "版本不匹配" in err10)
    check("⑩ 本地只试一轮（不原地退避重试）", calls10["local"], 2)
    check("⑩ 版本型仍走 Selenium Manager 兜底", calls10["sm"], 1)
    true("⑩ 日志写明版本不符与两侧版本号",
         any("版本不符" in m and "Chrome 156" in m for m in logs10))

    # ⑪ 连接型一直失败：退避两轮仍不成 → 才允许兜底，异常文案讲清「稍后重试即可」
    calls11 = {"local": 0, "sm": 0}

    def _chrome11(*args, **kw):
        if "service" not in kw:
            calls11["sm"] += 1
            raise Exception("Message: session not created: cannot connect to chrome "
                            "at 127.0.0.1:9255")
        calls11["local"] += 1
        raise Exception("Message: session not created: cannot connect to chrome "
                        "at 127.0.0.1:9255")

    _sw.Chrome = _chrome11
    logs11 = []
    err11 = ""
    try:
        cd.create(options=object(), log=lambda m: logs11.append(m), ports=[9255])
    except RuntimeError as e:
        err11 = str(e)
    check("⑪ 连接型重试两轮（2 候选 × 3 轮）", calls11["local"], 6)
    true("⑪ 退避重试日志出现两次", sum(1 for m in logs11 if "原地重试一遍" in m) == 2)
    true("⑪ 异常文案归因到浏览器没接连接", "没接受驱动的连接" in err11)
    check("⑪ 最后仍给 Selenium Manager 一次机会", calls11["sm"], 1)
finally:
    _sw.Chrome = _chrome_real
    _svc_mod.Service = _svc_real
    cd.time.sleep = _cd_sleep_real
    cd.candidates, cd.driver_version, cd.chrome_major = _cand_real, _dver_real, _major_real

print("\n=== C. 「链路没通」不能记成「企知道查不到→永久丢弃」 ===")
# 2026-11-04 09:35:09 实机：断网那一下 net::ERR_INTERNET_DISCONNECTED 被当成
# 「suggestPredict 无结果」，「青岛太平洋海洋工程(深圳)有限公司」直接进了 tried，
# 再也不会被补采——而它只是那一刻没网。用户口径「企知道都查不出的直接丢弃」
# 前提是查过；没查成必须回队首。
import block_guard as _bg_c
_bg_shell = _bg_c.is_shell_record
_bg_c.is_shell_record = lambda r: False
ps.time = _Clock()


def _c(names, q):
    """C 段专用装配：退避等待不真走墙钟（_list_wait 用的是 time.time 到点判定）。"""
    s = _session(names, q)
    s._list_wait = lambda sec, qq=None: True
    return s


try:
    # ⑧ 第一家撞上断网、重试后查到：必须回队首且不记 tried，最终照样补到
    q8 = _Q(attach_seq=("ok",))
    s8 = _c(["A公司", "B公司"], q8)
    seen8 = []

    def _sc8(name):
        seen8.append(name)
        if name == "A公司" and seen8.count("A公司") == 1:
            return {"公司名": name,
                    "错误": "企知道链路异常（可重采）：net::ERR_INTERNET_DISCONNECTED",
                    "可重采": True}
        if name == "B公司":
            q8._stop_requested = True
        return {"公司名": name, "电话": "13800000000"}

    q8.search_company = _sc8
    s8._run_list_channel(QZD)
    check("⑧ 断网那一家不是被丢弃，而是重试后查到入库",
          s8.pending.marks, [("A公司", True), ("B公司", True)])
    check("⑧ 链路异常被重排了一次", s8.pending.requeued, ["A公司"])
    check("⑧ 链路恢复后两家都补到", len(q8.results), 2)
    true("⑧ 日志说清「退回队首・不记查不到」",
         any("退回队首" in m and "不记「查不到」" in m for m in s8.logs))
    check("⑧ 没熄火", s8._rt(QZD)["halted"], False)
    true("⑧ 开关仍为真（已补到的结果不能被导出漏掉）", s8._list_active(QZD))

    # ⑨ 连续 4 家都是链路异常（还在断网）：本轮休眠，名单一家不丢
    q9 = _Q(attach_seq=("ok",))
    s9 = _c(["C公司", "D公司"], q9)
    q9.search_company = lambda n: {"公司名": n, "错误": "企知道链路异常（可重采）：断网",
                                   "可重采": True}
    s9._run_list_channel(QZD)
    check("⑨ 连到 4 家就休眠收通道", s9._rt(QZD)["halted"], True)
    check("⑨ 同一家被重排 4 次（不死循环烧名单）", s9.pending.requeued, ["C公司"] * 4)
    check("⑨ 一家也没记进 tried（没断网时还能重采）", s9.pending.marks, [])
    true("⑨ 名单完整（2 家一个不少）", s9.pending.counts(QZD)["pending"] == 2)
    true("⑨ 休眠日志点名断网/浏览器掉线", any("都是链路异常" in m for m in s9.logs))
    check("⑨ 开关全程不动（否则已补到的会被导出漏掉）", s9.list_enabled[QZD], True)

    # ⑩ 企知道真查不到（无「可重采」标记）：仍按约定丢弃，不重排
    q10 = _Q(attach_seq=("ok",))
    s10 = _c(["E公司", "F公司"], q10)
    n10 = [0]

    def _sc10(name):
        n10[0] += 1
        if name == "E公司":
            return {"公司名": name, "错误": "未找到公司（suggestPredict 无结果）"}
        q10._stop_requested = True
        return {"公司名": name, "电话": "13800000000"}

    q10.search_company = _sc10
    s10._run_list_channel(QZD)
    check("⑩ 真查不到→记 tried 丢弃（用户原口径），下一家照常补采",
          s10.pending.marks, [("E公司", False), ("F公司", True)])
    check("⑩ 真查不到不被重排", s10.pending.requeued, [])
    true("⑩ 丢弃日志照旧", any("按约定直接丢弃" in m for m in s10.logs))

    # ⑪ search_company 直接抛异常（根本没查成）：同样回队首，不记 tried
    q11 = _Q(attach_seq=("ok",))
    s11 = _c(["G公司", "H公司"], q11)
    n11 = [0]

    def _sc11(name):
        n11[0] += 1
        if n11[0] <= 4:                       # G 两次异常 + 重排后再两次，够到休眠阈值
            raise Exception("Message: unknown error: net::ERR_INTERNET_DISCONNECTED")
        q11._stop_requested = True
        return {"公司名": name, "电话": "13800000000"}

    q11.search_company = _sc11
    s11._run_list_channel(QZD)
    check("⑪ 异常不记 tried", s11.pending.marks, [])
    check("⑪ 异常重排到 4 次就收（不原地死循环）", s11.pending.requeued, ["G公司"] * 4)
    check("⑪ 异常连到阈值同样休眠", s11._rt(QZD)["halted"], True)
    true("⑪ 异常日志承诺下次接着补采",
         any("已退回队首" in m for m in s11.logs))
    true("⑪ 休眠日志点名异常连发", any("连续 4 家都异常" in m for m in s11.logs))

    # ⑫ 爬虫层：备用方案异常必须返回 None（与「搜索页真没这家公司」的 "" 分开）
    import qizhidao_crawler as qc
    c12 = qc.QiZhiDaoCrawler.__new__(qc.QiZhiDaoCrawler)
    c12.BASE_URL = qc.QiZhiDaoCrawler.BASE_URL
    c12._log = lambda *a, **k: None

    class _BadDriver:
        def get(self, url):
            raise Exception("Message: unknown error: net::ERR_INTERNET_DISCONNECTED")

    c12.driver = _BadDriver()
    check("⑫ 断网时备用方案返回 None（不是空串）", c12._get_hash_via_search("甲公司"), None)
    true("⑫ 失败原因存下来给上层用", "ERR_INTERNET_DISCONNECTED" in c12._hash_fail_reason)

    class _EmptyDriver:
        """「页面真打开了、正文也水合完成，但里面确实没有这家公司的链接」：
        这才是与断网不同形的那一种。备用方案里有一道 readyState + 正文规模的水合闸，
        替身必须过闸——否则会掉进 except 被当成链路异常（返回 None），用例就在
        「假的失败」和「真的失败」之间分不清。
        ⚠ 每一段 JS 要**按自己的身份**回答：2026-11-18 这一格对一切非 readyState 的 JS
        一律回「正文×400」，把后加的可见弹层探测（CAPTCHA_OVERLAY_JS，真站无弹层时回空串）
        也答成了「有个弹层」，于是「真查不到」被走成弹层等待分支、返回 None。替身猜内容
        而不是认调用，就会把两种形状焊回同一种。"""
        def get(self, url):
            pass

        def execute_script(self, js):
            if "readyState" in js:
                return "complete"
            if js == block_guard.CAPTCHA_OVERLAY_JS:
                return ""                      # 这一页上没有验证弹层
            return "正文" * 400

        def find_elements(self, by, value):
            return []

    c12.driver = _EmptyDriver()
    check("⑫ 页面正常但没这家公司→仍返回空串（两种情形不同形）",
          c12._get_hash_via_search("甲公司"), "")

    class _OverlayDriver(_EmptyDriver):
        """腾讯验证码弹层正挡在结果区上面：URL/标题/正文长度都不变，只有第 4 层探测看得到。
        这一形必须与上面那一格分形：回空串会被上层记成「真查不到」永久丢弃（实机一轮 175 家）。"""
        def execute_script(self, js):
            if js == block_guard.CAPTCHA_OVERLAY_JS:
                return "验证码弹层已可见"
            return _EmptyDriver.execute_script(self, js)

    c12.driver = _OverlayDriver()
    c12._wait_unblock = lambda *a, **k: False      # 等人过验证没等到：不许记「查不到」
    # 两个原因字段要先归零：上一格（断网、真查不到）留的值还在实例上，不清就会拿
    # 「上一格的现场」当「这一格的行为」来断言（验的是内存，不是代码）
    c12._hash_fail_reason = c12._last_fail_reason = ""
    check("⑫ 有弹层挡着时绝不返回空串（那是把拦成记成查不到）",
          c12._get_hash_via_search("甲公司"), None)
    check("⑫ 弹层那一格的原因要说得出是弹层没解除",
          "验证弹层" in str(getattr(c12, "_hash_fail_reason", "")), True)
    check("⑫ 弹层不写「搜索页无该公司链接」那一格（两种病不同处方）",
          not str(getattr(c12, "_last_fail_reason", "")).startswith("搜索页无该公司链接"), True)

    # ⑬ search_company 分流：None → 可重采；"" → 真查不到（不带可重采）
    def _mk13(hash_ret):
        c = qc.QiZhiDaoCrawler.__new__(qc.QiZhiDaoCrawler)
        c.BASE_URL = qc.QiZhiDaoCrawler.BASE_URL
        c.attach_port = 9255
        c._log = lambda *a, **k: None
        c.start_browser = lambda *a, **k: True
        c._is_session_alive = lambda: True
        c._ensure_own_tab = lambda *a, **k: True
        c._hash_fail_reason = "net::ERR_INTERNET_DISCONNECTED"

        class _D:
            current_url = c.BASE_URL
            quit = staticmethod(lambda: None)

        c.driver = _D()
        c._get_company_hash = lambda name: hash_ret
        return c

    r13a = _mk13(None).search_company("甲公司")
    check("⑬ 链路异常→带可重采标记", r13a.get("可重采"), True)
    true("⑬ 错误文案点名链路异常", "链路异常" in (r13a.get("错误") or ""))
    r13b = _mk13("").search_company("甲公司")
    check("⑬ 真查不到→不带可重采标记", bool(r13b.get("可重采")), False)
    true("⑬ 真查不到仍按原文案", "suggestPredict" in (r13b.get("错误") or ""))
finally:
    _bg_c.is_shell_record = _bg_shell
    ps.time = _real_time

print(f"\n通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
for f in FAIL:
    print("  ✗", f)
sys.exit(1 if FAIL else 0)
