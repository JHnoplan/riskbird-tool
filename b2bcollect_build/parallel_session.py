"""parallel_session.py —— 六通道并行采集编排（一个共享 Chrome，需浏览器的站各占一个标签）。

职责：
  · mic / b2b168 / 顺企网(11467) / 爱采购(aipage) / 1688 五个**关键词并行**采集器
    ＋ 企知道(qzd) 一个**名单驱动**通道（从待采集公司名单逐家补采，看门狗语义：
    名单空则休眠，废数据回流后自动苏醒），按界面开关决定谁参与本轮；
  · crawl(keywords) 在**各自的线程**里并行推进各站点（裸 CDP 命令通道彼此独立，
    每条命令显式带本站标签 ws 地址，故各线程真并行、互不抢活动标签；
    纯 HTTP 站 b2b168/11467/aipage 不占标签；1688/企知道用 Selenium 附加同一
    共享 Chrome，各占自己的标签/窗口，绝不起第二个浏览器实例）；
  · 把各采集器的信号汇聚到 session 级信号，界面只连一处即可；
  · 结果合并：原始明细各站拼接、清洗走一次 customer_library_cleaner 做跨站按公司名去重；
  · 暂停/继续/停止/清种子库/开关浏览器等控制指令广播到所有采集器。

界面契约（GUI 只依赖这些方法，切换站点/控制按钮才不会报错）：
  signals(log/progress/crawl_finished/blocked/block_cleared/stats)、crawl、snapshot_results、
  clean_records、export_excel、export_cleaned_excel、pause_crawl/resume_crawl/stop_crawl、
  start_browser/close_browser、clear_seen_store、_is_session_alive、_log。
"""
import re
import sys
import time
import threading
from pathlib import Path

try:
    from PyQt5.QtCore import QObject, pyqtSignal
    HAS_PYQT = True
except ImportError:
    HAS_PYQT = False

try:
    import customer_library_cleaner as _cleaner
except ImportError:
    sys.path.insert(0, str(Path(__file__).parent))
    import customer_library_cleaner as _cleaner

from cdp_base import clean_cell, CdpSiteCrawler
from mic_crawler import MicCrawler
from b2b168_crawler import B2b168Crawler
from qihuo11467_crawler import Qihuo11467Crawler
from aipage_crawler import AipageCrawler
from pending_companies import PendingCompanyList, extract_company_names
import site_adapters as _adapters

# 站点顺序固定：日志/进度里显示顺序稳定，便于用户对照浏览器标签
# （gys 供应商网已下线：验证码频发+限 IP，留着只会拖累整体成功率；文件保留备将来攻坚）
SITE_ORDER = ["mic", "b2b168", "11467", "aipage"]
SITE_CLASSES = {"mic": MicCrawler, "b2b168": B2b168Crawler,
                "11467": Qihuo11467Crawler, "aipage": AipageCrawler}
SITE_NAMES = {"mic": "中国制造网", "b2b168": "八方资源网",
              "11467": "顺企网", "aipage": "爱采购"}
# Selenium 附加通道（共享同一 Chrome，各自管自己的标签/窗口）
if _adapters.HAS_1688:
    SITE_ORDER.append("1688")
    SITE_CLASSES["1688"] = _adapters.Ali1688Site
    SITE_NAMES["1688"] = "1688"
QZD_SLUG = "qzd"
if _adapters.HAS_QZD:
    SITE_NAMES[QZD_SLUG] = "企知道"

RAW_HEADERS = ["序号", "关键词", "公司名称", "联系人", "岗位", "电话", "手机", "邮箱",
               "地址", "主营产品", "经营模式", "所在区域", "公司网址", "详情URL", "memberId",
               "数据来源", "采集时间", "状态"]

if HAS_PYQT:
    class SessionSignals(QObject):
        log_signal = pyqtSignal(str)
        progress_signal = pyqtSignal(int, int)
        kw_progress_signal = pyqtSignal(str, int, int)   # 聚合同步：已完成关键词单位/总单位
        crawl_finished_signal = pyqtSignal()
        blocked_signal = pyqtSignal(str)
        block_cleared_signal = pyqtSignal(str)
        login_required_signal = pyqtSignal(str)
        stats_signal = pyqtSignal(str, dict)     # 状态行文本 + 各站状态（slug -> done/stopped/running）
        site_finished_signal = pyqtSignal(str)      # 单站完成
else:
    class SessionSignals:
        pass


class ParallelSession:
    """五站并行会话。enabled 为参与本轮的站点 slug 集合。"""

    def __init__(self, enabled=None, port=9255):
        self.port = port
        self.enabled = set(enabled or SITE_ORDER)
        self.crawlers = {}
        for slug in SITE_ORDER:
            self.crawlers[slug] = SITE_CLASSES[slug](port=port)
        self.signals = SessionSignals() if HAS_PYQT else None
        self._threads = {}
        self._done_sites = set()
        self._total_done = 0
        self._kw_progress = {}          # slug -> (已完成关键词数, 关键词总数)
        self._lock = threading.RLock()
        # ── 企知道名单通道（第六路）：不在 SITE_ORDER/关键词并行里，独立线程按需驱动 ──
        self.qzd = _adapters.QizhidaoSite(port=port) if _adapters.HAS_QZD else None
        self.qzd_enabled = False        # 界面「启用企知道补采」开关（有名单通道才 True）
        self.pending = PendingCompanyList()
        self.pending.set_collected_probe(self._probe_collected)
        self._qzd_thread = None
        self._qzd_idle_exit = 300       # 名单空转这么久（秒）后通道休眠退出（看门狗）
        self._crawling_active = False   # 关键词采集进行中：企知道通道据此保持监听废数据回流
        self._wire_signals()

    # ── 信号汇聚 ───────────────────────────────────────────────
    def _wire_signals(self):
        if not self.signals:
            return
        wired = list(self.crawlers.items())
        if self.qzd is not None:
            wired.append((QZD_SLUG, self.qzd))
        for slug, c in wired:
            if not c.signals:
                continue
            # 逐个信号独立 try：某个爬虫缺一种信号（如 1688 无 handoff）不连累其余接线
            for name, slot in (("log_signal", self.signals.log_signal.emit),
                               ("blocked_signal", self.signals.blocked_signal.emit),
                               ("block_cleared_signal", self.signals.block_cleared_signal.emit),
                               ("login_required_signal", self.signals.login_required_signal.emit)):
                try:
                    getattr(c.signals, name).connect(slot)
                except Exception:
                    pass
            try:
                # progress/stats 汇聚成 session 级总量
                c.signals.progress_signal.connect(lambda *_a, s=slug: self._on_site_progress())
                c.signals.kw_progress_signal.connect(self._on_site_kw_progress)
                if slug != QZD_SLUG:    # 企知道通道不由 crawler.crawl 驱动，finished 不转发
                    c.signals.crawl_finished_signal.connect(lambda s=slug: self._on_site_finished(s))
            except Exception:
                pass

    def _active_crawlers(self):
        return [self.crawlers[s] for s in SITE_ORDER if s in self.enabled]

    def _qzd_active(self):
        """企知道通道本轮是否有产出（参与总量/看板/清洗的判据）。"""
        return self.qzd is not None and self.qzd_enabled and \
            (self.qzd.results or self.qzd._stat_queried)

    def _all_result_crawlers(self):
        """所有持有结果的通道（关键词并行 + 企知道名单通道）。"""
        out = self._active_crawlers()
        if self._qzd_active():
            out.append(self.qzd)
        return out

    def site_status(self):
        """各站状态（slug -> done/stopped/running）：供状态行图标+着色渲染。
        done=正常跑完；stopped=用户停止/熔断后线程退出；running=仍在推进。"""
        with self._lock:
            out = {}
            for s in SITE_ORDER:
                if s not in self.enabled:
                    continue
                if s in self._done_sites:
                    out[s] = "stopped" if self.crawlers[s]._stop_requested else "done"
                else:
                    out[s] = "running"
            return out

    def _on_site_progress(self):
        with self._lock:
            self._total_done = sum(len(c.results) for c in self._all_result_crawlers())
        parts = "｜".join(f"{SITE_NAMES[s]} {len(self.crawlers[s].results)}"
                         for s in SITE_ORDER if s in self.enabled)
        if self._qzd_active():
            parts += f"｜企知道补采 {len(self.qzd.results)}"
        self._emit("progress_signal", self._total_done, max(self._total_done, 1))
        self._emit("stats_signal", f"已采 {self._total_done} 家　{parts}", self.site_status())

    def _on_site_kw_progress(self, slug, done_kw, total_kw):
        """把各站「关键词完成度」聚合成可定量计算的总进度：
        单位＝关键词×站点（多站并行时天然按站等权），界面据此驱动百分比进度条
        （旧链路里 progress 的 total 永远等于已采条数，只能转圈无法定量）。"""
        with self._lock:
            self._kw_progress[slug] = (int(done_kw), int(total_kw))
            units_done = sum(d for d, _t in self._kw_progress.values())
            units_total = sum(max(t, d) for d, t in self._kw_progress.values())
        self._emit("kw_progress_signal", "", units_done, max(units_total, 1))

    def site_stats(self):
        """各站量化账目（GUI 看板/结束日志）：翻页数/列表条目/成功/无联系方式/失败/早过滤/详情不相关。"""
        out = {SITE_NAMES[s]: self.crawlers[s].stats() for s in SITE_ORDER if s in self.enabled}
        if self._qzd_active():
            out["企知道补采"] = self.qzd.stats()
        return out

    def _pinned_tab_ids(self):
        return [c.pin_tab_id for c in self._active_crawlers() if getattr(c, "NEEDS_BROWSER", True) and c.pin_tab_id]

    def set_browser_background(self, on):
        """后台化：把整个共享 Chrome（含所有标签/窗口）最小化/还原。
        不再只按 CDP pin 标签定位窗口——Selenium 附加通道（1688/企知道）的
        标签/窗口 pin_tab_id 恒为空，旧写法会把它们漏在后台化之外（企知道窗
        一直杵在最前）。验证码人在环不受损：界面层命中 blocked 时自动还原窗口、过完再最小化。"""
        import shared_chrome as sc
        state = "minimized" if on else "normal"
        try:
            return sc.set_all_windows_state(state, self.port)
        except Exception:
            return 0

    def _on_site_finished(self, slug):
        with self._lock:
            self._done_sites.add(slug)
        self._emit("site_finished_signal", SITE_NAMES.get(slug, slug))
        # 末站完成后不会再有 progress 事件，主动补发一次状态行，
        # 保证「✅ 已完成」标记在全部站点收尾后都能点亮
        self._on_site_progress()

    def _emit(self, name, *args):
        if self.signals:
            try:
                getattr(self.signals, name).emit(*args)
            except Exception:
                pass

    def _log(self, msg):
        line = f"[{time.strftime('%H:%M:%S')}][编排] {msg}"
        print(line, flush=True)
        self._emit("log_signal", line)

    # ── 配置下发 ───────────────────────────────────────────────
    def configure(self, delay_between=None, max_pages=None, relevance_guard=None,
                  incremental=None, max_records=None):
        for c in self._active_crawlers():
            if delay_between is not None:
                d = float(delay_between)
                # 站点下限：纯 HTTP 站一条记录要发多个请求，界面上的「采集间隔」对它们
                # 来说不是字面意义的那个间隔。不钳住的话，用户把间隔调到 2s 就会
                # 重现客户那次「200 多家后被 IP 限流」。下限由站点自己声明。
                floor = float(getattr(c, "MIN_DELAY", 0.0) or 0.0)
                if d < floor:
                    self._log(f"ℹ {c.PLATFORM_NAME}：本站一条记录要发多个请求，"
                              f"采集间隔按站点安全下限 {floor:g}s 执行（界面设的 {d:g}s 会被 IP 限流）")
                    d = floor
                c.delay_between = d
                c._current_delay = d
            if max_pages is not None:
                c.max_pages = int(max_pages)
            if relevance_guard is not None:
                c.relevance_guard = bool(relevance_guard)
            if incremental is not None:
                c.incremental = bool(incremental)
            if max_records is not None:
                c.max_records = int(max_records)
        # 企知道名单通道只吃节奏参数（逐家查询没有翻页/相关性的概念）
        if self.qzd is not None and delay_between is not None:
            self.qzd.delay_between = max(0.8, float(delay_between) / 2)

    def set_enabled(self, enabled):
        self.enabled = set(enabled or [])

    # ── 采集主入口（阻塞，供 QThread worker 调用）──────────────
    def crawl(self, keywords, start_index=0):
        kws = [str(x).strip() for x in (keywords or []) if str(x).strip()]
        self._kw_count = len(kws)
        actives = self._active_crawlers()
        qzd_on = self.qzd is not None and self.qzd_enabled
        if not kws and not actives and not qzd_on:
            self._log("没有关键词、没有勾选站点、也没有待采名单，采集结束")
            self._emit("crawl_finished_signal")
            return
        with self._lock:
            self._done_sites = set()
            self._total_done = 0
            self._kw_progress = {}
        names = [SITE_NAMES[c.PLATFORM_SLUG] for c in actives]
        tail = f"｜{len(kws)} 个关键词" if kws else ""
        qzd_note = ""
        if qzd_on:
            n = self.pending.counts()["pending"]
            qzd_note = f"＋企知道补采（待采名单 {n} 家）"
            if not kws:
                names = []
                tail = ""
        self._log(f"🚀 多平台并行启动：{'、'.join(names) if names else '企知道名单通道'}"
                  f"{qzd_note}{tail}")
        # 每站一个线程，真并行推进（命令通道独立）；企知道名单通道另起看门狗线程
        self._threads = {}
        # 有关键词采集时，清洗会持续产生废数据→回流名单，企知道通道需全程监听；
        # 纯名单模式（无关键词）无废数据产生，通道排空名单后自然休眠。
        self._crawling_active = bool(kws and actives)
        if kws:
            for c in actives:
                t = threading.Thread(target=self._run_site, args=(c, kws, start_index),
                                     name=f"crawl-{c.PLATFORM_SLUG}", daemon=True)
                self._threads[c.PLATFORM_SLUG] = t
                t.start()
        if qzd_on:
            self._qzd_stop = False
            self._qzd_thread = threading.Thread(target=self._run_qzd_channel,
                                                name="crawl-qzd", daemon=True)
            self._qzd_thread.start()
        for t in self._threads.values():
            while t.is_alive():
                t.join(timeout=0.5)
        # 关键词通道全停后再给企知道一段收尾宽限（把在手的公司查完、等最后一次
        # 自动保存把废数据回流入列），到点仍未空则温和收通道
        self._crawling_active = False       # 关键词采集已结束：通道转入「排空剩余名单后休眠」
        if qzd_on and self._qzd_thread is not None:
            deadline = time.time() + 90
            while self._qzd_thread.is_alive() and time.time() < deadline \
                    and not self.qzd._stop_requested:
                self._qzd_thread.join(timeout=0.5)
            if self._qzd_thread.is_alive():
                self._log("⏳ 企知道通道收尾：查完当前这家后本轮结束（未查的名单已落盘，下次接着采）")
                self.qzd._stop_requested = True
            while self._qzd_thread.is_alive():
                self._qzd_thread.join(timeout=0.5)
        total = sum(len(c.results) for c in actives)
        if self._qzd_active():
            total += len(self.qzd.results)
        self._log(f"✅ 多平台采集全部结束，合计 {total} 家")
        # 汇总账目：回答「实际采了多少 / 过滤了多少」
        for name, s in self.site_stats().items():
            self._log(f"📊 {name}：翻 {s['pages']} 页见 {s['listed']} 条｜成功 {s['ok']}｜"
                      f"无联系方式 {s['no_contact']}｜失败/可重采 {s['failed']}｜"
                      f"早过滤 {s['early']}｜详情不相关 {s['filtered']}")
        self._emit("crawl_finished_signal")

    def _run_site(self, crawler, keywords, start_index):
        try:
            crawler.crawl(keywords, start_index=start_index)
        except Exception as e:
            crawler._log(f"❌ {crawler.PLATFORM_NAME}采集线程异常：{str(e).splitlines()[0][:160]}")

    # ── 企知道名单通道（看门狗：名单空则休眠，废数据回流后自动苏醒）────
    def bind_pending(self, session_dir, list_file="", want_qzd=None):
        """任务开始时调用：把待采名单绑定到任务文件夹（与原数据/清洗入库同层级，
        任务文件夹里一定会出现「待采集公司名单.xlsx」）。

        want_qzd：是否开企知道补采通道——
          None（默认）= 上传了名单或任务文件夹里已有待采数据就开；
          True / False = 界面显式指定（例如勾选了「利用废数据补采」）。
        不论通道开否，只要绑定了任务文件夹，清洗废数据就会持续回流到名单（变废为宝）。
        返回 (通道启用与否, 本次入列家数)。"""
        self.qzd_enabled = False
        names = []
        if list_file:
            if self.qzd is None:
                self._log("⚠ 企知道适配器不可用，公司名单本轮不参与补采")
            else:
                try:
                    names = extract_company_names(list_file, log=self._log)
                except Exception as e:
                    self._log(f"❌ 读取公司名单失败：{str(e).splitlines()[0][:120]}")
                if not names:
                    self._log("⚠ 名单文件里没识别到公司名（支持 xlsx/csv/txt，需有一列是公司名）")
        added = self.pending.bind(session_dir)      # 总是绑定：建任务文件夹 + 生成名单表
        if names:
            added += self.pending.seed(names)
        n = self.pending.counts()["pending"]
        if want_qzd is None:
            want_qzd = bool(names) or n > 0
        self.qzd_enabled = bool(want_qzd) and self.qzd is not None
        if self.qzd_enabled:
            if n:
                src = "（含用户上传名单）" if names else "（续用上次的待采名单）"
                self._log(f"📋 待采集公司名单就绪{src}：{n} 家，企知道补采通道将随任务启动")
            else:
                self._log("📋 企知道补采通道已就绪：待采名单暂空，清洗产生的废数据会自动入列并被补采")
        elif names:
            self._log("📋 名单已写入待采集表（未开企知道补采，废数据仍会累积供下次使用）")
        return self.qzd_enabled, added

    def _probe_collected(self, name):
        """pop 前探针：只有当关键词通道已采到「带联系方式」的同名公司，才省下这次企知道请求。

        ⚠ 不能只按公司名匹配：待采名单里的公司恰恰是「采到了但手机/固话/邮箱/微信四项全空」
        的废数据，它们的公司名本来就留在关键词通道的原始结果里。若只比名字，探针会对每一家
        废数据都命中它自己 → 全部标记 collected 跳过 → 企知道永远一家都补采不到（2026-10-29
        实测：13 家废数据全被跳过、企知道本轮查到 0 家、两份产出文档无一条企知道来源）。
        故命中同名后必须再校验该记录是否真有联系方式（与 build_clean_rows 废数据判定同口径）：
        确有联系方式才算「已采到」并跳过；否则它正是等着企知道补联系方式的对象，必须放行。"""
        try:
            import block_guard
            norm = block_guard.norm_company
        except Exception:
            norm = lambda s: re.sub(r"[\s（）()]+", "", str(s or ""))
        target = norm(name)
        if not target:
            return False
        for c in self._active_crawlers():
            for r in c.snapshot_results():
                if norm(r.get("公司名称") or r.get("公司名") or "") != target:
                    continue
                if self._record_has_contact(c, r):
                    return True
        return False

    @staticmethod
    def _record_has_contact(crawler, r):
        """原始记录是否确有联系方式（与 build_clean_rows 的废数据判定同口径：能拆出
        手机/固话、或有邮箱、或有微信才算数；「无电话(未公开)」这类占位文本不算）。"""
        try:
            rec = crawler._to_cleaner_record(r)
            tel, email = rec.get("电话", ""), rec.get("邮箱", "")
        except Exception:
            tel = " ; ".join(str(r.get(k) or "") for k in ("手机", "电话"))
            email = str(r.get("邮箱") or "")
        try:
            mobiles, landlines = _cleaner.split_phones(tel)
            if mobiles or landlines:
                return True
            if _cleaner.split_emails(email):
                return True
        except Exception:
            if tel.strip() or email.strip():
                return True
        return any(str(r.get(k) or "").strip() for k in ("微信", "微信号"))

    def _waste_sink(self, names):
        """清洗钩子（自动保存/导出共用）：被入库口径剔掉的废数据公司名回流待采名单。
        只要任务文件夹已绑定就持续累积（不依赖通道是否正在跑，变废为宝）。"""
        if not self.pending.is_bound:
            return
        try:
            n = self.pending.add_waste(names)
            if n:
                note = "企知道将逐家补采" if self.qzd_enabled else "已存待采名单，开企知道补采后可用"
                self._log(f"♻ {n} 家无联系方式的公司已加入待采集名单（{note}）")
        except Exception:
            pass

    def _ensure_qzd_ready(self):
        """懒附加 + 登录门（首次有活干时才开，名单空转不占任何浏览器资源）。"""
        if self.qzd.driver is not None:
            return True
        import shared_chrome as sc
        if not sc.ensure_shared_chrome(self._log, self.port, True):
            return False
        if not self.qzd.attach():
            return False
        state, detail = self.qzd.login_state()
        if state == "ok":
            return True
        if state == "need_login":
            self._emit("login_required_signal", "企知道未登录，补采通道等待登录（已在浏览器打开企知道）")
            self._log("🔐 企知道未登录：已打开企知道首页，请在浏览器里完成登录（最多等 10 分钟）")
            deadline = time.time() + 600
            while time.time() < deadline and not self.qzd._stop_requested:
                time.sleep(5)
                state, _d = self.qzd.login_state()
                if state == "ok":
                    self._log("✅ 企知道登录确认，补采通道继续")
                    return True
            self._log("⚠ 等待企知道登录超时，补采通道本轮休眠（名单保留，登录后可继续）")
            return False
        return True     # unknown（读 Cookie 失败等）：不硬拦，交给空壳门兑底

    def _run_qzd_channel(self):
        """看门狗循环：pop→查→回执。名单空时每 5s 轮询（废数据随时可能回流），
        空转超 _qzd_idle_exit 秒则休眠退出；主循环收尾时会再给一次宽限。"""
        q = self.qzd
        q._stop_requested = False
        started = False
        idle_since = None
        shell_streak = 0            # 连续空壳计数：登录门解决不了的空壳不能死循环重排
        self._log("🐕 企知道补采通道启动（看门狗：名单空自动休眠，废数据回流自动苏醒）")
        while not q._stop_requested and self.qzd_enabled:
            while q._pause_requested and not q._stop_requested:
                time.sleep(0.5)
            if q._stop_requested:
                break
            name = self.pending.pop()
            if name is None:
                if self._crawling_active:
                    # 关键词采集仍在进行：清洗随时会把废数据回流入列，
                    # 保持监听（started=False 时尚未附加浏览器，零资源占用）
                    time.sleep(5)
                    continue
                if not started and not self.pending.has_work():
                    # 没附加过浏览器、也没活干（纯名单模式已排空）：直接休眠
                    self._log("💤 待采名单为空，企知道通道休眠（有废数据回流会自动苏醒）")
                    return
                if idle_since is None:
                    idle_since = time.time()
                elif time.time() - idle_since > self._qzd_idle_exit:
                    self._log("💤 待采名单已空一段时间，企知道通道休眠（下次任务自动接续）")
                    return
                time.sleep(5)
                continue
            idle_since = None
            if not started:
                if not self._ensure_qzd_ready():
                    self.pending.requeue(name)   # 没真正查成：回队首，不记 tried（到手名字不能丢）
                    self.qzd_enabled = False
                    return
                started = True
            try:
                block = q._detect_block()
                if block and not q._wait_unblock(block):
                    self.pending.requeue(name)   # 被拦截退出：这一家没查成，回队首下次接着采
                    break
                self._log(f"🔎 企知道补采：{name}（待采剩 {self.pending.counts()['pending']} 家）")
                result = q.search_company(name)
                err = (result.get("错误") or "").strip()
                if err:
                    found = False
                    q.bump_stat(missing=True)
                    self._log(f"  ⚠ 企知道未查到「{name}」，按约定直接丢弃（不再重复尝试）")
                else:
                    # 空壳（未登录时的假成功）不算数：回队首，等登录门解决后重采
                    try:
                        import block_guard
                        shell = block_guard.is_shell_record(result)
                    except Exception:
                        shell = False
                    if shell:
                        shell_streak += 1
                        # 连续多家空壳＝登录态确实掉了：回队首、重过登录门；
                        # 若重登仍连续空壳（阈值），说明登录门解决不了，休眠通道避免死循环烧请求
                        self.pending.requeue(name)
                        if shell_streak >= 3 or not self._ensure_qzd_ready():
                            self._log("💤 企知道详情连续空壳（多为登录态失效），补采通道休眠；"
                                      "请在浏览器完成企知道登录后重新开始任务")
                            self.qzd_enabled = False
                            return
                        continue
                    shell_streak = 0
                    found = True
                    q._add_result(result)
                    q.bump_stat(found=True)
                self.pending.mark_result(name, found)
                self._emit("progress_signal", 0, 1)   # 触发总量/状态行刷新
            except Exception as e:
                q.bump_stat(found=False)
                self.pending.mark_result(name, False)
                self._log(f"❌ 企知道补采「{name}」异常：{str(e).splitlines()[0][:120]}")
            time.sleep(max(0.5, q.delay_between))
        c = self.pending.counts()
        self._log(f"🏁 企知道补采通道收工：本轮查到 {q._stat_found} 家｜查不到丢弃 {q._stat_missing} 家"
                  f"｜名单剩余 {c['pending']} 家（已落盘，下次任务自动接续）")

    # ── 结果合并 ───────────────────────────────────────────────
    def snapshot_results(self):
        """各站原始明细按站点顺序拼接（每条已带「数据来源」），企知道补采结果殿后。"""
        out = []
        for slug in SITE_ORDER:
            if slug in self.enabled:
                out.extend(self.crawlers[slug].snapshot_results())
        if self._qzd_active():
            out.extend(self.qzd.snapshot_results())
        return out

    def per_site_results(self):
        out = {slug: self.crawlers[slug].snapshot_results()
               for slug in SITE_ORDER if slug in self.enabled}
        if self._qzd_active():
            out[QZD_SLUG] = self.qzd.snapshot_results()
        return out

    def clean_records(self, records=None, log=None):
        """跨站清洗：各站用自己的 _to_cleaner_record 映射（数据来源正确），
        合并后走一次 build_clean_rows 做跨站按公司名去重；入库口径再按
        filter_import_rows 剔除「手机号/固话/邮箱/微信号全空」的行（不污染后台客户库，
        原始表仍全量保留）。
        records=None：只清洗本次已勾选站点的实时结果；
        records 显式传入：按「数据来源」跨**全部**站点匹配（历史/导入数据可能
        含当前未勾选的站，若仍按 enabled 过滤会静默丢掉这些行）。"""
        log = log or self._log
        cleaner_recs = []
        slugs = SITE_ORDER if records is not None else [s for s in SITE_ORDER if s in self.enabled]
        for slug in slugs:
            c = self.crawlers[slug]
            recs = c.snapshot_results() if records is None else \
                [r for r in records if (r.get("数据来源") or "") == c.PLATFORM_NAME]
            cleaner_recs.extend(c._to_cleaner_record(r) for r in recs)
        if self.qzd is not None and (records is not None or self._qzd_active()):
            recs = self.qzd.snapshot_results() if records is None else \
                [r for r in records if (r.get("数据来源") or "") == self.qzd.PLATFORM_NAME]
            cleaner_recs.extend(self.qzd._to_cleaner_record(r) for r in recs)
        rows = _cleaner.build_clean_rows(cleaner_recs, log=log, waste_sink=self._waste_sink)
        return self.filter_import_rows(rows, log=log)

    @staticmethod
    def filter_import_rows(rows, log=None):
        """入库口径与各站同源（cdp_base.filter_import_rows）：手机号/固话/邮箱/微信号
        四项全空的行剔掉，不写进「清洗后入库数据」也不上传云展（避免污染后台客户库）；
        原始表仍全量保留，补查线索不丢。"""
        return CdpSiteCrawler.filter_import_rows(rows, log=log)

    def reset_results(self):
        """「开始新任务」用：清掉本次已采数据、各站本轮账目与聚合进度，
        不动跨运行历史种子库（要不要清由界面里的选项单独决定）。"""
        for c in list(self.crawlers.values()) + ([self.qzd] if self.qzd else []):
            try:
                c.reset_run_state()
            except Exception as e:
                self._log(f"⚠ {c.PLATFORM_NAME} 进度复位失败：{str(e).splitlines()[0][:80]}")
        with self._lock:
            self._done_sites = set()
            self._total_done = 0
            self._kw_progress = {}
        return True

    def build_clean_rows(self, log=None):
        return self.clean_records(log=log or self._log)

    # ── 导出：原始 + 清洗两份 ─────────────────────────────────
    def export_excel(self, output_path, rows=None):
        """原始明细导出（各平台合并，含企知道补采）。"""
        rows = self.snapshot_results() if rows is None else rows
        if not rows:
            self._log("没有数据可导出")
            return False
        try:
            from openpyxl import Workbook
            wb = Workbook()
            ws = wb.active
            ws.title = "B2B五站企业数据"
            ws.append(RAW_HEADERS)
            for i, r in enumerate(rows, 1):
                ws.append([i] + [clean_cell(r.get(h, "")) for h in RAW_HEADERS[1:]])
            wb.save(output_path)
            self._log(f"★ 原始文件已导出：{output_path}（{len(rows)} 条）")
            return True
        except Exception as e:
            self._log(f"❌ 原始导出失败：{e}")
            return False

    def export_cleaned_excel(self, output_path, template_path="", log=None):
        log = log or self._log
        rows = self.build_clean_rows(log=log)
        if not rows:
            log("没有可清洗入库的数据")
            return False
        if not template_path:
            template_path = _cleaner.locate_template(
                [str(Path(output_path).parent), str(Path.home() / "Downloads")])
        ok = _cleaner.write_clean_excel(rows, output_path, template_path=template_path, log=log)
        if ok:
            log(f"★ 清洗后文件已导出：{output_path}（{len(rows)} 条）")
        return ok

    # ── 控制广播 ───────────────────────────────────────────────
    def _broadcast_targets(self):
        return list(self._active_crawlers()) + \
            ([self.qzd] if self.qzd is not None and self.qzd_enabled else [])

    def pause_crawl(self):
        for c in self._broadcast_targets():
            c.pause_crawl()

    def resume_crawl(self):
        for c in self._broadcast_targets():
            c.resume_crawl()

    def stop_crawl(self):
        for c in self.crawlers.values():
            c._stop_requested = True
        if self.qzd is not None:
            self.qzd._stop_requested = True

    def start_browser(self, headed=True):
        """确保共享 Chrome 在跑；需浏览器的站各取得自己的标签（每站全程只建一次标签）。
        纯 HTTP 站（NEEDS_BROWSER=False，如 b2b168）不占用标签、不驱动浏览器；
        Selenium 站（1688）与企知道通道在自己的采集线程里懒附加，这里只保活共享 Chrome。"""
        import shared_chrome as sc
        browser_crawlers = [c for c in self._active_crawlers()
                            if getattr(c, "NEEDS_BROWSER", True) and hasattr(c, "ensure_tab")]
        need_chrome = browser_crawlers or \
            any(getattr(c, "NEEDS_BROWSER", False)
                for c in self._active_crawlers() if not hasattr(c, "ensure_tab")) or \
            (self.qzd is not None and self.qzd_enabled)
        if need_chrome and not sc.is_alive(self.port):
            if not sc.ensure_shared_chrome(self._log, self.port, headed):
                return False
        ok = True
        for c in browser_crawlers:
            if not c.ensure_tab():
                ok = False
                continue
            # 逐站立即导航到本站首页：让各站标签的 URL 域名立刻分叉，
            # 配合 shared_chrome 认领表形成双保险，杜绝冷启动时多站挤同一空白标签。
            if c.BASE_URL:
                try:
                    c._goto(c.BASE_URL, expect_domain=c.HOME_DOMAIN or None, tries=2)
                except Exception as e:
                    self._log(f"⚠ {c.PLATFORM_NAME} 首页导航失败：{str(e).splitlines()[0][:80]}")
        return ok

    def close_browser(self):
        """收工：关本工具的 CDP pin 标签＋ Selenium 通道自己的标签/窗口，共享 Chrome 保留。"""
        import shared_chrome as sc
        pinned = [c.pin_tab_id for c in self.crawlers.values() if c.pin_tab_id]
        try:
            sc.release_tabs(pinned, self.port, self._log)
        except Exception:
            pass
        for c in self.crawlers.values():
            c.pin_tab_id = ""
            c._ws_cache = ""
        c1688 = self.crawlers.get("1688")
        if c1688 is not None:
            try:
                c1688.close_browser()   # 适配器版：只关自己的标签，不碰共享 Chrome
            except Exception:
                pass
        if self.qzd is not None:
            try:
                self.qzd.close_tab()
            except Exception:
                pass

    def open_login_page(self, slug):
        """界面「打开1688 / 打开企知道」按钮：确保共享 Chrome 在跑，把对应站点首页
        摆到用户眼前让其登录。有网络 IO + 可能拉起浏览器，务必在后台线程调用，
        绝不能卡 GUI 主线程。返回 (ok, message)。"""
        import shared_chrome as sc
        if not sc.is_alive(self.port):
            self._log("正在启动共享浏览器…")
            if not sc.ensure_shared_chrome(self._log, self.port, True):
                return False, "共享浏览器启动失败，请确认已安装 Google Chrome"
        if slug == "1688":
            c = self.crawlers.get("1688")
            if c is None:
                return False, "1688 适配器不可用"
            if not c.start_browser(headed=True):
                return False, "1688 附加共享浏览器失败"
            try:
                c._ensure_tab()
                c._goto(c.BASE_URL, tries=2)
            except Exception as e:
                return True, f"1688 已打开（首页导航异常：{str(e).splitlines()[0][:80]}）"
            self.set_browser_background(False)   # 还原/前置窗口，把登录页递到眼前
            return True, "1688 已在浏览器打开，请确认登录状态"
        if slug == QZD_SLUG:
            if self.qzd is None:
                return False, "企知道适配器不可用"
            if not self.qzd.attach():
                return False, "企知道附加共享浏览器失败"
            self.set_browser_background(False)   # 还原/前置窗口，把登录页递到眼前
            return True, "企知道已在浏览器打开，请完成登录"
        return False, f"站点 {slug} 无需登录页"

    def open_site_check(self, name=""):
        """拦截提醒里的「打开该站页面自查」：由用户主动点，把被拦的那一页（没有则首页）
        摆到共享 Chrome 眼前，让他自己判断「是 IP 被拒（Chrome 也打不开/同样报错）还是
        真有滑块可过」。

        为什么不由工具自动打开：IP 级限流（八方 502、供应商网 520）页面里根本没有验证
        控件，自动拉起窗口只会让用户对着打不开的页面找入口白等（实测八方等了很久）——
        旧文案还写着「已在 Chrome 里打开对应页面」，与实际行为相反，就是客户反馈
        「八方出验证码找不到入口、只能在你软件点确认」的直接原因。
        name 为空（旧适配器的拦截文本里本来就没有站名）时，按「当前处于被拦状态的站」兜底。
        必须在后台线程调用（有网络 IO、可能拉起浏览器）。返回 (ok, message)。"""
        target = None
        for c in self.crawlers.values():
            if name and getattr(c, "PLATFORM_NAME", "") == name:
                target = c
                break
            if not name and getattr(c, "_blocked", False):
                target = c
                break
        if target is None and not name:
            for c in self.crawlers.values():
                if getattr(c, "_block_probe_url", ""):
                    target = c
                    break
        if target is None:
            return False, f"「{name or '相关站'}」当前没有在采集，无页面可打开"
        if not hasattr(target, "open_verify_page"):
            return False, f"「{target.PLATFORM_NAME}」不支持自查页，请直接去浏览器里打开该站"
        name = target.PLATFORM_NAME
        url = getattr(target, "_block_probe_url", "") or getattr(target, "BASE_URL", "")
        if not url:
            return False, f"「{name}」没有可打开的地址"
        try:
            if target.open_verify_page(url):
                self.set_browser_background(False)   # 还原/前置窗口，把页面递到眼前
                return True, f"已在 Chrome 里打开 {name} 的页面，请自行核对"
        except Exception as e:
            return False, f"打开失败：{str(e).splitlines()[0][:80]}"
        return False, "打开失败：共享浏览器不可用（请确认已安装 Google Chrome）"

    def _is_session_alive(self):
        if any(c._is_session_alive() for c in self._active_crawlers()):
            return True
        c1688 = self.crawlers.get("1688")
        if c1688 is not None and c1688._is_session_alive():
            return True
        return False

    def clear_seen_store(self):
        n = 0
        for c in self.crawlers.values():
            c.clear_seen_store()
            n += 1
        return n

    def seen_store_counts(self):
        return {SITE_NAMES[s]: len(self.crawlers[s]._seen_store) for s in SITE_ORDER}

    @staticmethod
    def parse_keywords(text):
        return MicCrawler.parse_keywords(text)
