"""parallel_session.py —— 多通道并行采集编排（一个共享 Chrome，需浏览器的站各占一个标签）。

职责：
  · mic / b2b168 / 顺企网(11467) / 爱采购(aipage) / 麦麦B2B(maimaib2b)
    / 跨国采购网(globalimporter) / 马可波罗(makepolo) / 世界工厂网(gongchang)
    / 1688 九个**关键词并行**采集器
    ＋ 企知道(qzd) 一个**名单驱动**通道（从待采集公司名单逐家补采，看门狗语义：
    名单空则休眠，废数据回流后自动苏醒），按界面开关决定谁参与本轮；
  · crawl(keywords) 在**各自的线程**里并行推进各站点（裸 CDP 命令通道彼此独立，
    每条命令显式带本站标签 ws 地址，故各线程真并行、互不抢活动标签；
    纯 HTTP 站 b2b168/11467/aipage/maimaib2b/globalimporter/makepolo/gongchang 不占标签（其中两站被反采质询时才临时
    借一个标签过盾）；1688/企知道用 Selenium 附加同一
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
import os
import sys
import json
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
from maimaib2b_crawler import Maimaib2bCrawler
from gi_crawler import GiCrawler
from makepolo_crawler import MakepoloCrawler
from gongchang_crawler import GongchangCrawler
from pending_companies import PendingCompanyList, extract_company_names
import site_adapters as _adapters

# 站点顺序固定：日志/进度里显示顺序稳定，便于用户对照浏览器标签
# （gys 供应商网已下线：验证码频发+限 IP，留着只会拖累整体成功率；文件保留备将来攻坚）
# （solarbe 索比光伏网按用户指令下线：光伏行业垂直库，改用独立脚本全量采一次性数据，
#   不占工具日常关键词通道；文件保留 solarbe_crawler.py 供那条脚本复用解析）
# （qympw 企业名片网 / b2b818 快采购网 / goepe 谷瀑网 按用户指令下线：实机复验发现
#   goepe 关键词公司搜索是 JS 后加载（纯 HTTP 只会拿到全关键词相同的固定推荐位）；
#   b2b818 真实但易触发对端 WAF 出口 IP 封锁；qympw 名片库对深度工业词覆盖过薄——
#   三者数据质量不达要求，已从工具移除，采集器文件一并删除）
SITE_ORDER = ["mic", "b2b168", "11467", "aipage", "maimaib2b",
              "globalimporter", "makepolo", "gongchang"]
SITE_CLASSES = {"mic": MicCrawler, "b2b168": B2b168Crawler,
                "11467": Qihuo11467Crawler, "aipage": AipageCrawler,
                "maimaib2b": Maimaib2bCrawler, "globalimporter": GiCrawler,
                "makepolo": MakepoloCrawler, "gongchang": GongchangCrawler}
SITE_NAMES = {"mic": "中国制造网", "b2b168": "八方资源网",
              "11467": "顺企网", "aipage": "爱采购",
              "maimaib2b": "麦麦B2B", "globalimporter": "跨国采购网",
              "makepolo": "马可波罗", "gongchang": "世界工厂网"}
# Selenium 附加通道（共享同一 Chrome，各自管自己的标签/窗口）
if _adapters.HAS_1688:
    SITE_ORDER.append("1688")
    SITE_CLASSES["1688"] = _adapters.Ali1688Site
    SITE_NAMES["1688"] = "1688"
QZD_SLUG = "qzd"
if _adapters.HAS_QZD:
    SITE_NAMES[QZD_SLUG] = "企知道"
# 名单通道（同一份待采名单，每平台一条独立线程 + 独立游标台账）：
# 站名在这里登记一次，seed_results / clean_records / 原始表「数据来源」都对得上号。
LIST_CHANNEL_SLUGS = tuple(s for s in _adapters.LIST_CHANNEL_SLUGS
                           if _adapters.LIST_CHANNEL_AVAILABLE.get(s))
for _slug in LIST_CHANNEL_SLUGS:
    SITE_NAMES.setdefault(_slug, _adapters.LIST_CHANNEL_LABELS.get(_slug, _slug))

# 状态行/看板上每一路的显示名：名单通道带「补采」后缀，与 _on_site_progress 拼的
# 文案**同源**。GUI 的 build_stats_html 是按这份名字从状态行里把「哪一路出了多少家、
# 什么状态」解析出来的——两处不同源就会出现「风鸟补采 12 家」在日志里有、看板上没有。
STATS_LABELS = dict(SITE_NAMES)
for _slug in LIST_CHANNEL_SLUGS:
    STATS_LABELS[_slug] = SITE_NAMES[_slug] + "补采"
# 状态行的排列顺序：关键词各站在前，三路名单补采殿后（与产出文件里的拼接顺序一致）
STATUS_ORDER = tuple(SITE_ORDER) + LIST_CHANNEL_SLUGS

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
    """多站并行会话。enabled 为参与本轮的站点 slug 集合。"""

    def __init__(self, enabled=None, port=9255):
        self.port = port
        # None＝全部启用；显式传空列表＝一个都不启用（离线测试要的正是「只跑名单通道」）。
        # 旧写法 `set(enabled or SITE_ORDER)` 把空列表当成 None，测试里会造成
        # 「自称离线的用例真的联网开爬」（2026-11-10 自查抓到）。
        self.enabled = set(SITE_ORDER if enabled is None else enabled)
        self.crawlers = {}
        for slug in SITE_ORDER:
            self.crawlers[slug] = SITE_CLASSES[slug](port=port)
        self.signals = SessionSignals() if HAS_PYQT else None
        self._threads = {}
        self._done_sites = set()
        self._total_done = 0
        self._kw_progress = {}          # slug -> (已完成关键词数, 关键词总数)
        # 关键词级进度台账（slug -> {"yield": {词: 净新增}, "done": [跑完的词]}）：
        # 续采靠它而不是靠 start_index——五个站并行推进速度不同，下标根对不齐。
        self._kw_ledger = {}
        self._last_keywords = []
        self.task_folder = ""
        self.progress_path = ""
        self._lock = threading.RLock()
        # ── 名单通道（企知道 / 风鸟 / 爱企查）：不参与关键词并行，各自独立线程 ──
        # 三路共用**同一份**待采名单（PendingCompanyList 的 master），每路在自己的
        # 台账里记 tried/collected/cursor，所以「100 家名单」= 每个平台都从第 1 家
        # 跑到第 100 家（用户口径），而不是三家分掉这 100 家。
        self.channels = _adapters.build_list_channels(port=port)
        self.qzd = self.channels.get(QZD_SLUG)      # 兼容既有引用名（界面/测试）
        self.list_enabled = {slug: False for slug in self.channels}
        self._list_threads = {}                     # slug -> 看门狗线程
        self._list_rt = {}                          # slug -> 看门狗运行态
        self.pending = PendingCompanyList()
        self.pending.set_collected_probe(self._probe_collected)
        self._list_idle_exit = 300      # 名单空转这么久（秒）后通道休眠退出（看门狗）
        self._crawling_active = False   # 关键词采集进行中：名单通道据此保持监听废数据回流
        # 通道启动失败类型（""/chrome/driver/login）：浏览器/驱动型属瞬时故障要退避重试，
        # 登录型属要用户动手的状态（等登录超时后休眠，反复重试没有意义）。
        self._list_attach_retries = 5    # 瞬时故障的退避重试上限（5/10/20/40/60 秒）
        # 频率限流连续这么多家就本路让路：限流虽会自愈，但连着 5 家都撞＝当天配额见底，
        # 再退避重试就是无底洞（纯名单模式下这一轮永远收不了尾）。
        self._list_throttle_halt = 5
        # 拦截（要人动手过验证）不再「等 10 分钟就永久让路」，改成熔断器半开监测：
        # 停止发采集请求、每 15 秒只做一次只读探测，过完验证自动接着采这一家。
        # 但一轮里连续这么多次「监测到放行 → 一试还是被拦」＝人确实不在或账号真被限制，
        # 再无限盯下去只会让这一轮永远收不了尾，到上限就如实结束本路（名单已存盘）。
        self._list_block_cycles = 5
        # 心跳与单轮上限都取自 site_adapters 那组「等人过验证」的唯一口径：同一件事
        # 在两个文件里各写一个数字，迟早会有一处改了另一处没改（节奏对不上＝日志看着假）。
        self._list_watch_heartbeat = int(_adapters.LIST_BLOCK_WATCH_HEARTBEAT)
        self._list_watch_round_max = int(_adapters.LIST_BLOCK_WATCH_ROUND_MAX)
        self._wire_signals()

    # ── 名单通道开关（qzd_enabled 是历史名字，保持兼容）────────────
    @property
    def qzd_enabled(self):
        return bool(self.list_enabled.get(QZD_SLUG))

    @qzd_enabled.setter
    def qzd_enabled(self, value):
        if QZD_SLUG in self.list_enabled:
            self.list_enabled[QZD_SLUG] = bool(value)

    def list_enabled_any(self):
        return any(self.list_enabled.values())

    def enabled_list_channels(self):
        """本轮真正要跑的名单通道 [(slug, 采集器, 运行态), ...]（按注册表顺序）。"""
        out = []
        for slug, c in self.channels.items():
            if self.list_enabled.get(slug):
                out.append((slug, c, self._list_rt.setdefault(slug, self._new_list_rt(slug))))
        return out

    @staticmethod
    def _new_list_rt(slug):
        """一路看门狗的运行态。分级熔断是这套设计的关键：
        额度用尽＝当天白跑（立即让路）、限流＝几分钟自愈（退避后重试）、
        拦截＝要人动手（限时等，等人期间不烧名单）、链路异常/空壳＝连续多家才判死。
        没有这套阈值的后果是断网或掉登录时 pop→异常→requeue→pop 同一个名字原地死循环。"""
        return {"slug": slug, "thread": None, "started": False, "idle_since": None,
                "halt": False, "halted": False, "fail_kind": "", "attach_try": 0,
                "shell_streak": 0, "net_streak": 0, "throttle_streak": 0,
                # watch_cycles＝当前半开态连着盯了几轮（查到就清零，用来判「要不要收工」）；
                # watch_total＝本轮累计进过几次监测（只增不清，用来解释「这一路为什么慢」）
                # 两个数必须分开：只看前者会在自愈后丢失全部坏账记录。
                "blocked_streak": 0, "watch_cycles": 0, "watch_total": 0,
                "watching": False}

    def _rt(self, slug):
        return self._list_rt.setdefault(slug, self._new_list_rt(slug))

    # ── 信号汇聚 ───────────────────────────────────────────────
    def _wire_signals(self):
        if not self.signals:
            return
        wired = list(self.crawlers.items()) + list(self.channels.items())
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
                if not getattr(c, "LIST_CHANNEL", False):
                    # 名单通道不由 crawler.crawl 驱动（由本模块的看门狗逐家喂），
                    # 转发 finished 会让看板把「这一路查完」误报成「本站已跑完」
                    c.signals.crawl_finished_signal.connect(lambda s=slug: self._on_site_finished(s))
            except Exception:
                pass

    def _active_crawlers(self):
        return [self.crawlers[s] for s in SITE_ORDER if s in self.enabled]

    def _list_active(self, slug):
        """某一路名单通道本轮是否有产出（参与总量/看板/清洗的判据）。

        开关关了也算「有过产出」：用户采到一半取消勾选，已补到的结果仍要出现在
        两份产出文件里，否则那就是把数据丢掉（≠ 开关状态，与 halted 同理）。"""
        c = self.channels.get(slug)
        return bool(c is not None and (c.results or getattr(c, "_stat_queried", 0)))

    def _active_list_channels(self):
        return [c for slug, c in self.channels.items() if self._list_active(slug)]

    def _all_result_crawlers(self):
        """所有持有结果的通道（关键词并行 + 各路名单通道）。"""
        return self._active_crawlers() + self._active_list_channels()

    def site_status(self):
        """各站状态（slug -> done/stopped/running）：供状态行图标+着色渲染。
        done=正常跑完；stopped=用户停止/熔断后线程退出；running=仍在推进。
        名单通道也在表内报状态：三平台并行时「哪一路已经让路了」必须看得见，
        否则界面只剩一个「企知道补采已启用」，用户分不清是额度没了还是根本没启动。"""
        with self._lock:
            out = {}
            for s in SITE_ORDER:
                if s not in self.enabled:
                    continue
                if s in self._done_sites:
                    out[s] = "stopped" if self.crawlers[s]._stop_requested else "done"
                else:
                    out[s] = "running"
            for slug, c in self.channels.items():
                rt = self._rt(slug)
                if not self.list_enabled.get(slug) and not self._list_active(slug):
                    continue
                if c._stop_requested or rt["halted"]:
                    out[slug] = "stopped"
                elif rt["thread"] is not None and rt["thread"].is_alive():
                    out[slug] = "running"
                elif rt["started"] or self._list_active(slug):
                    out[slug] = "done"       # 真跑过这一路了（线程已退出/本轮排空）
                else:
                    out[slug] = "running"    # 还没轮到开工：与关键词各站的空闲口径一致
            return out

    def _on_site_progress(self):
        with self._lock:
            self._total_done = sum(len(c.results) for c in self._all_result_crawlers())
        parts = "｜".join(f"{SITE_NAMES[s]} {len(self.crawlers[s].results)}"
                         for s in SITE_ORDER if s in self.enabled)
        # 三路名单通道各自报自己那一路的入库数（口径与关键词站分列，不混）：
        # 名字必须取 STATS_LABELS，GUI 看板按它解析，写死一处就会有一处对不上
        parts += "".join(f"｜{STATS_LABELS[c.PLATFORM_SLUG]} {len(c.results)}"
                         for c in self._active_list_channels())
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
        """各站量化账目（GUI 看板/结束日志）：翻页数/列表条目/成功/无联系方式/失败/早过滤/详情不相关。
        三路名单通道单列（键名取 STATS_LABELS，与状态行、看板解析同源）。"""
        out = {SITE_NAMES[s]: self.crawlers[s].stats() for s in SITE_ORDER if s in self.enabled}
        for slug, c in self.channels.items():
            if self._list_active(slug):
                out[STATS_LABELS[slug]] = c.stats()
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
                  incremental=None, max_records=None, province=None):
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
                # 站点可以声明「每关键词页数硬上限」：有的站是按行业限量的（实测跨国采购网：
                # 非会员每行业只给前 3 页，超了直接 2 小时冷却），不能让全局的「不限页数」
                # 把这类站的配额撞光；马可波罗则是库太大（一个词上百万条），不限页会失控。
                req = int(max_pages)
                cap = int(getattr(c, "MAX_PAGES_HARD_CAP", 0) or 0)
                c.max_pages = min([x for x in (req, cap) if x > 0] or [req or cap])
                if cap and req > cap:
                    self._log(f"ℹ {c.PLATFORM_NAME}：本站每个关键词最多翻 {cap} 页"
                              f"（站方限量，翻多了会被冷却），已按 {cap} 页执行")
            if relevance_guard is not None:
                c.relevance_guard = bool(relevance_guard)
            if incremental is not None:
                c.incremental = bool(incremental)
            if max_records is not None:
                c.max_records = int(max_records)
            if province is not None:
                # 省份精准采集（支持多选）：参数直筛站拼进搜索 URL（各爬虫 build_search_url），
                # 数据层站在 _add_result 入库关卡过滤（cdp_base._apply_province_filter）。
                # 1688 适配器是独立基类，province 属性已在其 __init__ 里声明。
                # 多省场景：列表转逗号分隔字符串下发，各爬虫 build_search_url 判断含逗号
                # 时放弃 URL 参数、退回数据层过滤（trust-but-verify）。
                if isinstance(province, (list, tuple)):
                    c.province = ",".join(str(p).strip() for p in province if str(p).strip())
                else:
                    c.province = str(province or "").strip()
            # 属地判不出的公司名回流待采名单（由名单补采通道按工商登记地址反查一次属地）：
            # 入库关卡是 fail-closed（判不出就不入库，宁可少不可污），但这些公司大
            # 概率只是页面没写省份、实际就在目标省，直接丢是纯产能损失——补采通道查
            # 一家就能同时定属地＋补联系方式，比人工黑盒重试便宜。
            if hasattr(c, "province_unknown_sink"):
                c.province_unknown_sink = self._unverified_sink
        # 名单通道（企知道/风鸟/爱企查）：逐家查询没有翻页/相关性的概念，但属地关卡
        # 必须接上——它们不参与关键词并行、也不经过 cdp_base 漏斗，不拦就会把外省公司
        # 放进「清洗后入库数据」（企知道本轮就放进过 1 条浙江公司，属监管空白）。
        # 三套判据同源：属地口径共用 site_adapters._pf_admit，间隔口径在这里统一下发。
        for c in self.channels.values():
            if delay_between is not None:
                # 名单逐家查询的间隔同样要站方安全下限：风鸟两次列表页导航最小
                # 2 秒（基类 min_interval），低于它就是自己撞「操作过于频繁」，
                # 撞了三次本路熔断让路——用户看到的将是「名单还有 80 家没查」。
                floor = float(getattr(c, "MIN_DELAY", 0.8) or 0.8)
                c.delay_between = max(floor, float(delay_between) / 2)
            if province is not None:
                if isinstance(province, (list, tuple)):
                    c.province = ",".join(str(p).strip() for p in province if str(p).strip())
                else:
                    c.province = str(province or "").strip()

    def set_enabled(self, enabled):
        self.enabled = set(enabled or [])

    # ── 任务进度台账（暂停/停止/关工具后下次接着采）───────────────
    PROGRESS_FILENAME = "采集进度.json"

    def set_task_folder(self, folder):
        """绑定任务文件夹（桌面/B2B三站企业数据自动采集工具/<会话时间戳>/）。
        进度台账与原数据 Excel 同层：用户拷走整个文件夹就把「关键词、参数、
        采到哪儿」一起带走了。"""
        self.task_folder = str(folder or "")
        self.progress_path = (str(Path(self.task_folder) / self.PROGRESS_FILENAME)
                              if self.task_folder else "")
        return self.progress_path

    def snapshot_site_progress(self):
        """把各爬虫内存里的逐词台账同步到会话台账（爬虫才是逐词的权威记录者）。"""
        with self._lock:
            for slug, c in self.crawlers.items():
                snap = getattr(c, "progress_snapshot", None)
                if callable(snap):
                    d = snap() or {}
                    self._kw_ledger[slug] = {"yield": dict(d.get("yield") or {}),
                                             "done": list(d.get("done") or [])}
        return self._kw_ledger

    def keyword_progress(self):
        """当前各通道进度（界面看板/任务索引显示用）。"""
        with self._lock:
            return {s: {"yield": dict(v.get("yield") or {}),
                        "done": list(v.get("done") or [])}
                    for s, v in self._kw_ledger.items()}

    def total_yield(self):
        """台账里的净新增总数（续采后显示「累计已采」而不是只有本轮）。"""
        with self._lock:
            return sum(sum((v.get("yield") or {}).values()) for v in self._kw_ledger.values())

    def top_keywords(self, limit=12):
        """跨站汇总的每词产出榜（高产词榜）——下一轮同族派生扩词的输入。"""
        merged = {}
        for v in self.keyword_progress().values():
            for kw, n in (v.get("yield") or {}).items():
                merged[kw] = merged.get(kw, 0) + int(n or 0)
        return sorted(merged.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]

    def save_progress(self):
        """进度落盘：tmp + os.replace 原子替换，强杀/断电不会留下半个坏文件。"""
        if not self.progress_path:
            return False
        self.snapshot_site_progress()
        data = {
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "keywords": list(self._last_keywords or []),
            "sites": sorted(self.enabled),
            "ledger": self.keyword_progress(),
        }
        try:
            Path(self.progress_path).parent.mkdir(parents=True, exist_ok=True)
            tmp = self.progress_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.progress_path)
            return True
        except Exception as e:
            self._log(f"⚠ 采集进度落盘失败（不影响本次采集）：{str(e).splitlines()[0][:100]}")
            return False

    def load_progress(self, folder=""):
        """读任务里的进度台账；没文件返回 None（当全新任务）。
        【关键修复 2026-11-07】加载时自动清洗零产出脏数据：done 里只保留 yield > 0
        的关键词，零产出的释放出来让续采自动重试，避免 1688/麦麦B2B 因历史脏数据
        永久跳过。"""
        if folder:
            self.set_task_folder(folder)
        if not self.progress_path or not os.path.exists(self.progress_path):
            return None
        try:
            with open(self.progress_path, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return None
        if isinstance(data, dict) and isinstance(data.get("ledger"), dict):
            # 加载时清洗：每个站点的 done 里只保留 yield > 0 的关键词
            cleaned_ledger = {}
            for slug, entry in data["ledger"].items():
                y = entry.get("yield") or {}
                done_list = entry.get("done") or []
                # 只保留有产出的关键词为 done；零产出的释放出来让续采重试
                cleaned_done = [str(k) for k in done_list if int(y.get(str(k), 0) or 0) > 0]
                cleaned_ledger[slug] = {"yield": dict(y), "done": cleaned_done}
            with self._lock:
                self._kw_ledger = cleaned_ledger
            data["ledger"] = cleaned_ledger  # 回写清洗后的数据，save_progress 会覆盖
            return data
        return None

    def apply_progress(self):
        """把台账装回各爬虫（CDP 系有细粒度逐词台账；1688 适配器没有，
        就靠下面的关键词子集过滤实现续采）。返回生效的站点数。"""
        applied = 0
        with self._lock:
            items = list(self._kw_ledger.items())
        for slug, snap in items:
            c = self.crawlers.get(slug)
            if c is None:
                continue
            rp = getattr(c, "restore_progress", None)
            if callable(rp) and rp(snap):
                applied += 1
        return applied

    def seed_results(self, records):
        """续采第 2 步：把任务里已采到的记录按「数据来源」装回各通道内存。
        这一步不能省——自动保存是按内存 results 全量覆写原数据 Excel 的，
        不装回来就会用本轮新采的几十条盖掉上次已采的几百条（客户最敏感的
        「数据丢了」就这么发生）。返回装回的条数。"""
        by_src = {}
        for r in records or []:
            if isinstance(r, dict):
                by_src.setdefault(str(r.get("数据来源") or "").strip(), []).append(r)
        total = 0
        targets = list(self.crawlers.items()) + list(self.channels.items())
        for slug, c in targets:
            rows = by_src.get(SITE_NAMES.get(slug, slug)) or by_src.get(slug) or []
            if not rows:
                continue
            seed = getattr(c, "seed_results", None)
            if callable(seed):
                total += seed(rows) or 0
            else:
                lock = getattr(c, "results_lock", None)
                if lock is not None:
                    with lock:
                        c.results.extend(rows)
                else:
                    c.results.extend(rows)
                total += len(rows)
        return total

    # ── 采集主入口（阻塞，供 QThread worker 调用）──────────────
    def crawl(self, keywords, start_index=0):
        kws = [str(x).strip() for x in (keywords or []) if str(x).strip()]
        self._kw_count = len(kws)
        with self._lock:
            self._last_keywords = list(kws)
        actives = self._active_crawlers()
        list_rts = self.enabled_list_channels()
        if not kws and not actives and not list_rts:
            self._log("没有关键词、没有勾选站点、也没有待采名单，采集结束")
            self._emit("crawl_finished_signal")
            return
        with self._lock:
            self._done_sites = set()
            self._total_done = 0
            self._kw_progress = {}
        names = [SITE_NAMES[c.PLATFORM_SLUG] for c in actives]
        tail = f"｜{len(kws)} 个关键词" if kws else ""
        # 每一路名单通道都单独报「自己还剩多少家」：三路共用同一份 master，
        # 数字看起来一样是**正确**的（各家都要从第 1 家跑到第 100 家），
        # 合并成一句「名单 N 家」反而让人以为这 N 家由三家分摊。
        list_note = ""
        if list_rts:
            list_note = "＋" + "＋".join(
                f"{c.PLATFORM_NAME}补采（待采 {self.pending.counts(slug)['pending']} 家）"
                for slug, c, _rt in list_rts)
            if not kws:
                names = []
                tail = ""
        self._log(f"🚀 多平台并行启动：{'、'.join(names) if names else '名单通道'}"
                  f"{list_note}{tail}")
        # 每站一个线程，真并行推进（命令通道独立）；名单通道每平台各一条看门狗线程
        self._threads = {}
        # 有关键词采集时，清洗会持续产生废数据→回流名单，名单通道需全程监听；
        # 纯名单模式（无关键词）无废数据产生，通道排空名单后自然休眠。
        self._crawling_active = bool(kws and actives)
        if kws:
            for c in actives:
                t = threading.Thread(target=self._run_site, args=(c, kws, start_index),
                                     name=f"crawl-{c.PLATFORM_SLUG}", daemon=True)
                self._threads[c.PLATFORM_SLUG] = t
                t.start()
        for slug, c, rt in list_rts:
            # 每轮启动复位熔断位：上一轮因额度/断网让过路的，这一轮要重新试一次
            rt["halt"] = False
            rt["halted"] = False
            rt["started"] = False
            rt["idle_since"] = None
            rt["attach_try"] = 0
            rt["shell_streak"] = rt["net_streak"] = 0
            rt["throttle_streak"] = rt["blocked_streak"] = 0
            c._stop_requested = False
            t = threading.Thread(target=self._run_list_channel, args=(slug,),
                                 name=f"crawl-{slug}", daemon=True)
            rt["thread"] = t
            t.start()
        for t in self._threads.values():
            while t.is_alive():
                t.join(timeout=0.5)
        # 关键词通道全停后再给各路名单通道一段收尾宽限（把在手的公司查完、等最后一次
        # 自动保存把废数据回流入列），到点仍未空则温和收通道。
        # 三路**并行等**，不是首尾相接各等 90 秒：串行等待会让收尾变成半小时。
        self._crawling_active = False       # 关键词采集已结束：通道转入「排空剩余名单后休眠」
        if not self._threads:
            # 纯名单模式（本轮没有任何关键词线程）：各路补采通道就是主任务本身，
            # 不能套用 90 秒「收尾宽限」——100 家逐家查要十几分钟起步，到点强停等于
            # 把用户要的「按公司名精准找联系方式」砍成前几家（2026-11-10 自查发现）。
            # 这里等到每一路自己排空名单或分级熔断让路为止（用户点停止会置
            # _stop_requested，看门狗随即退出，不会挂死）。
            self._log("📋 本轮只跑公司名单：补采通道各自把待采名单查到头（或让路）为止")
            alive = [rt for _slug, _c, rt in list_rts if rt["thread"] is not None]
            while alive:
                for rt in alive:
                    rt["thread"].join(timeout=0.5)
                alive = [rt for rt in alive if rt["thread"].is_alive()]
            finishing = []
        else:
            finishing = [(slug, c, rt) for slug, c, rt in list_rts
                         if rt["thread"] is not None and rt["thread"].is_alive()]
        if finishing:
            deadline = time.time() + 90
            while finishing and time.time() < deadline:
                still = []
                for slug, c, rt in finishing:
                    rt["thread"].join(timeout=0.5)
                    if rt["thread"].is_alive():
                        still.append((slug, c, rt))
                finishing = still
            for slug, c, rt in finishing:
                self._log(f"⏳ {c.PLATFORM_NAME}通道收尾：查完当前这家后本轮结束"
                          f"（未查的名单已落盘，下次接着采）")
                c._stop_requested = True
            for slug, c, rt in finishing:
                while rt["thread"].is_alive():
                    rt["thread"].join(timeout=0.5)
        total = sum(len(c.results) for c in actives)
        for c in self._active_list_channels():
            total += len(c.results)
        self._log(f"✅ 多平台采集全部结束，合计 {total} 家")
        # 名单余量对账：三路各自还剩多少家没查完，直接回答用户最关心的「有没有漏」。
        # 不打印的话，「本轮查到 12 家」和「名单其实还有 88 家」在日志里看不出区别。
        if list_rts:
            cc = self.pending.channel_counts([slug for slug, _c, _rt in list_rts])
            bits = []
            for slug, c, rt in list_rts:
                one = cc.get(slug) or {}
                state = ("已让路" if rt["halted"] else
                         ("已停止" if c._stop_requested else "已跑完"
                          if not one.get("pending") else "未跑完"))
                bits.append(f"{c.PLATFORM_NAME}：查到 {one.get('collected', 0)} 家、"
                            f"待采剩 {one.get('pending', 0)} 家（{state}）")
            self._log("📋 名单对账（master 共 "
                      f"{cc.get('__master__', {}).get('total', 0)} 家）：" + "｜".join(bits))
        self.save_progress()
        # 每词产出榜：直接回答「哪些词白跑了」——0 产出的词就是下一轮该换掉的词，
        # 高产词则是同族派生扩词的种子（客户「挂一天才几百条」的根因就在这张表里）。
        board = self.top_keywords(999)
        if board:
            hit = [(k, n) for k, n in board if n > 0]
            dry = [k for k, n in board if n == 0]
            self._log("🏆 每词产出榜（跨站净新增）：" +
                      "、".join(f"{k} {n}家" for k, n in self.top_keywords(8)))
            if dry:
                self._log(f"🪙 其中 {len(dry)} 个词一家没出（{('、'.join(dry[:8]))}"
                          f"{('…' if len(dry) > 8 else '')}）——这类词多半是行业类别名而非具体产品词，"
                          f"建议点「智能解析」展开或换更具体的产品名")
            if hit:
                self._log(f"📈 高产词 {hit[0][0]}（{hit[0][1]} 家）可继续同族派生扩量"
                          f"（例：螺杆式/风冷式/水冷式…），工具下一轮会自动带上")
        # 汇总账目：回答「实际采了多少 / 过滤了多少」——省份把关也算进去，
        # 否则“采到很多却没入库”在账上看不透（客户上一轮就是因为这一项没披露，
        # 只能看到总数、不知道 300+ 家是被属地关卡拦下的）。
        for name, s in self.site_stats().items():
            pf = (f"｜省份把关：外省 {s.get('pf_miss', 0)}、"
                  f"属地未标注转名单补采 {s.get('pf_unknown', 0)}"
                  if (s.get('pf_miss') or s.get('pf_unknown')) else "")
            self._log(f"📊 {name}：翻 {s['pages']} 页见 {s['listed']} 条｜成功 {s['ok']}｜"
                      f"无联系方式 {s['no_contact']}｜失败/可重采 {s['failed']}｜"
                      f"早过滤 {s['early']}｜详情不相关 {s['filtered']}{pf}")
        self._emit("crawl_finished_signal")

    def _pending_keywords(self, crawler, keywords):
        """续采过滤：把本站**已完整跑完**的关键词剔掉，只把剩下的交给爬虫。
        放在会话层做（而不是每个爬虫里改）是因为 1688 适配器继承的是 Selenium 爬虫、
        没有逐词台账；在这里统一过滤，五个通道一套语义。"""
        snap = getattr(crawler, "progress_snapshot", None)
        if callable(snap):
            done = {str(k) for k in ((snap() or {}).get("done") or [])}
        else:
            slug = getattr(crawler, "PLATFORM_SLUG", "")
            done = {str(k) for k in ((self._kw_ledger.get(slug) or {}).get("done") or [])}
        if not done:
            return list(keywords)
        return [k for k in keywords if str(k) not in done]

    def _merge_site_progress(self, crawler, subset):
        """单站跑完后并账：CDP 系直接取爬虫的逐词台账（它知道哪些词真跑完了、
        每个词净新增几条）；适配器没有台账，就按「本轮子集 + 未被打断 + 有产出」
        推 done，净新增从 results 按关键词回算（含续采装回的 history，口径为累计）。
        【大厂思想】零产出关键词绝不记 done——无论 CDP 还是适配器路径，
        续采时自动重来，避免 1688/麦麦B2B 被验证码卡住后整轮 0 条却永久跳过。"""
        slug = getattr(crawler, "PLATFORM_SLUG", "")
        if not slug:
            return
        snap = getattr(crawler, "progress_snapshot", None)
        if callable(snap):
            d = snap() or {}
            with self._lock:
                self._kw_ledger[slug] = {"yield": dict(d.get("yield") or {}),
                                         "done": list(d.get("done") or [])}
            return
        with self._lock:
            prev = self._kw_ledger.get(slug) or {"yield": {}, "done": []}
            prev_yield = dict(prev.get("yield") or {})
            # 每词条数按 results 回算（续采装回的 history 也算在内，口径为累计）
            n = {}
            for rec in crawler.results:
                kw = str(rec.get("关键词") or "").strip()
                if kw:
                    n[kw] = n.get(kw, 0) + 1
            y = dict(prev_yield)
            for kw, v in n.items():
                y[kw] = max(int(y.get(kw, 0) or 0), v)
            # 【关键修复】只记「有产出」的关键词为 done；零产出词（被拦/登录失败/解析空）
            # 一律不记，下次续采自动重来。旧实现只要 _stop_requested=False 就把子集全推
            # done，导致 1688 被验证码卡住整轮 0 条后续采永久跳过（2026-11-07 修复）。
            done = set()
            if not getattr(crawler, "_stop_requested", False):
                for k in subset:
                    if y.get(str(k), 0) > 0:
                        done.add(str(k))
            # 历史 done 中只保留有产出的（零产出的释放出来让续采重试）
            for k in (prev.get("done") or []):
                if prev_yield.get(str(k), 0) > 0:
                    done.add(str(k))
            self._kw_ledger[slug] = {"yield": y, "done": sorted(done)}

    def _run_site(self, crawler, keywords, start_index):
        subset = self._pending_keywords(crawler, keywords)
        skipped = len(keywords) - len(subset)
        if skipped:
            crawler._log(f"↻ 续采：本站上次已跑完 {skipped} 个关键词，本轮只跑剩下 {len(subset)} 个")
        if not subset:
            crawler._log("↻ 本站关键词上次已全部跑完，本轮不重跑（要彻底重采请点「开始新任务」）")
            self._on_site_finished(crawler.PLATFORM_SLUG)
            return
        try:
            crawler.crawl(subset, start_index=start_index)
        except Exception as e:
            crawler._log(f"❌ {crawler.PLATFORM_NAME}采集线程异常：{str(e).splitlines()[0][:160]}")
        finally:
            self._merge_site_progress(crawler, subset)

    # ── 名单通道（企知道 / 风鸟 / 爱企查）：绑定、探针、看门狗 ─────
    def bind_pending(self, session_dir, list_file="", want_qzd=None, want_slugs=None):
        """任务开始时调用：把待采名单绑定到任务文件夹（与原数据/清洗入库同层级，
        任务文件夹里一定会出现「待采集公司名单.xlsx」）。

        want_slugs：本轮开哪几路补采通道（qzd / riskbird / aiqicha 的子集）。
          None（默认）＝沿用 want_qzd 的老语义；两者都没给时，上传了名单或任务文件夹里
          已有待采数据就开默认那一路（企知道）。
        want_qzd：历史参数，只控制企知道那一路（界面旧勾选框与既有回归断言仍走这条路）。
        不论通道开否，只要绑定了任务文件夹，清洗废数据就会持续回流到名单（变废为宝）。
        返回 (是否至少开了一路, 本次入列家数)。"""
        for slug in self.list_enabled:
            self.list_enabled[slug] = False
        names = []
        if list_file:
            if not self.channels:
                self._log("⚠ 补采平台适配器都不可用，公司名单本轮不参与补采")
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
        pending_n = len(self.pending.master())      # master 总数＝每一路都要从头跑的家数
        if want_slugs is not None:
            want = {str(s) for s in want_slugs}
            if want_qzd is not None:
                # 两个参数同时传进来时以旧语义覆盖企知道那一路，不留互相矛盾的开关
                want = (want | {QZD_SLUG}) if want_qzd else (want - {QZD_SLUG})
        elif want_qzd is not None:
            want = {QZD_SLUG} if want_qzd else set()
        else:
            want = {QZD_SLUG} if names or pending_n else set()
        on = [slug for slug in self.channels if slug in want]
        for slug in on:
            self.list_enabled[slug] = True
        labels = "、".join(_adapters.LIST_CHANNEL_LABELS.get(s, s) for s in on)
        missing = [s for s in want if s not in self.channels]
        if missing:
            # 勾了但本机没有这个平台的采集器：必须说出来，不能默默少跑一路
            self._log("⚠ 这些平台在本机不可用（缺 riskbird_build 采集器），本轮不补采："
                      + "、".join(_adapters.LIST_CHANNEL_LABELS.get(s, s) for s in missing))
        if on:
            if pending_n:
                src = "（含用户上传名单）" if names else "（续用上次的待采名单）"
                self._log(f"📋 待采集公司名单就绪{src}：{pending_n} 家，"
                          f"{labels}补采通道将随任务启动（每一路都从第 1 家跑到第 {pending_n} 家）")
            else:
                self._log(f"📋 {labels}补采通道已就绪：待采名单暂空，"
                          f"清洗产生的废数据会自动入列并被补采")
        elif want:
            self._log("📋 名单已写入待采集表（补采通道都没启用，废数据仍会累积供下次使用）")
        elif names:
            self._log("📋 名单已写入待采集表（未开补采通道，废数据仍会累积供下次使用）")
        return bool(on), added

    def _probe_collected(self, name):
        """pop 前探针：只有当关键词通道已采到「带联系方式」的同名公司，才省下这次补采请求。

        ⚠ 不能只按公司名匹配：待采名单里的公司恰恰是「采到了但手机/固话/邮箱/微信四项全空」
        的废数据，它们的公司名本来就留在关键词通道的原始结果里。若只比名字，探针会对每一家
        废数据都命中它自己 → 全部标记 collected 跳过 → 补采通道永远一家都补不到（2026-10-29
        实测：13 家废数据全被跳过、企知道本轮查到 0 家、两份产出文档无一条企知道来源）。
        故命中同名后必须再校验该记录是否真有联系方式（与 build_clean_rows 废数据判定同口径）：
        确有联系方式才算「已采到」并跳过；否则它正是等着补采联系方式的对象，必须放行。

        ⚠ 探针只看关键词通道，不看另两路名单通道：三平台并行是用户明确要求的
        「同一份名单三家都从头采一遍」，靠别家采过就跳过等于替用户砍掉两路产出。
        """
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

    def _unverified_sink(self, names):
        """属地关卡钩子：页面/公司名都判不出省份的公司名回流待采名单，由名单通道
        按工商登记地址反查一次属地（外省不入库、在本省则连联系方式一起补上）。
        与废数据回流共用一套台账，只要任务文件夹已绑定就持续累积。"""
        if not self.pending.is_bound:
            return
        try:
            n = self.pending.add_waste(names)
            if n:
                note = "补采通道将逐家反查属地并补采" if self._list_can_run() \
                    else "已存待采名单，下次任务（或补采通道恢复后）自动接续"
                self._log(f"🗺 {n} 家页面未写属地的公司已加入待采名单（{note}）")
        except Exception:
            pass     # 辅佐通道不得影响采集主流程

    def _waste_sink(self, names):
        """清洗钩子（自动保存/导出共用）：被入库口径剔掉的废数据公司名回流待采名单。
        只要任务文件夹已绑定就持续累积（不依赖通道是否正在跑，变废为宝）。"""
        if not self.pending.is_bound:
            return
        try:
            n = self.pending.add_waste(names)
            if n:
                note = "补采通道将逐家补采" if self._list_can_run() \
                    else "已存待采名单，下次任务（或补采通道恢复后）自动接续"
                self._log(f"♻ {n} 家无联系方式的公司已加入待采集名单（{note}）")
        except Exception:
            pass

    def enabled_list_slugs(self):
        """本轮勾选的名单通道 slug（按注册表顺序）。"""
        return [slug for slug in self.channels if self.list_enabled.get(slug)]

    def _list_can_run(self, slug=None):
        """文案口径：本轮（某一路）通道是否还会去补采。
        开关开着但已放弃（未接上浏览器/登录失效/额度用尽/连续异常）不算。
        slug=None＝「还有任何一路在跑」，废数据回流文案用这个笼统口径。"""
        if slug is None:
            return any(self._list_can_run(s) for s in self.enabled_list_slugs())
        return bool(self.list_enabled.get(slug)) and not self._rt(slug)["halted"]

    def _list_wait(self, seconds, c):
        """可打断的退避等待：期间点了停止就立刻收工，不许闷头睡死。"""
        deadline = time.time() + max(0.0, float(seconds))
        while time.time() < deadline:
            if c._stop_requested:
                return False
            time.sleep(0.5)
        return not c._stop_requested

    def _ensure_list_ready(self, slug):
        """某一路名单通道的懒附加 + 登录门（首次有活干时才开，名单空转不占浏览器资源）。

        三路接浏览器的方式完全不同（企知道/风鸟 Selenium debuggerAddress 附加、爱企查裸
        CDP 独占标签），所以「已经接上了没有」由各自的 is_attached() 回答，编排层不猜。
        失败原因归类记进 rt["fail_kind"]，看门狗据此决定「退避后重试」还是「休眠」：
        chrome/driver 属浏览器侧的瞬时状态（2026-11-04 实测：同一份驱动 08:33 连不上、
        几分钟后 0.2 秒附加成功），login 属需要用户动手的状态。
        """
        c = self.channels[slug]
        rt = self._rt(slug)
        label = c.PLATFORM_NAME
        rt["fail_kind"] = ""
        if c.is_attached():
            return True
        import shared_chrome as sc
        if not sc.ensure_shared_chrome(self._log, self.port, True):
            rt["fail_kind"] = "chrome"
            return False
        if not c.attach():
            rt["fail_kind"] = "driver"
            return False
        state, detail = c.login_state()
        if state == "ok":
            return True
        if state == "need_login":
            return self._wait_login_gate(slug)
        return True     # unknown（读 Cookie 失败等）：不硬拦，交给空壳门兑底

    def _wait_login_gate(self, slug):
        """限时等某一路平台登录（最多 10 分钟），期间该路不烧名单、其余通道照常跑。

        三路并行后「一路死等用户登录」不能变成全局卡住：本方法只阻塞自己这一条线程，
        超时即返回 False，由看门狗转入休眠并把待采名单原样留在台账里（下次接续）。
        """
        c = self.channels[slug]
        rt = self._rt(slug)
        label = c.PLATFORM_NAME
        rt["fail_kind"] = "login"
        self._emit("login_required_signal",
                   f"{label}未登录，补采通道等待登录（已在浏览器打开{label}）")
        self._log(f"🔐 {label}未登录：已打开{label}首页，请在浏览器里完成登录（最多等 10 分钟）")
        deadline = time.time() + 600
        while time.time() < deadline and not c._stop_requested:
            time.sleep(5)
            state, _d = c.login_state()
            if state == "ok":
                self._log(f"✅ {label}登录确认，补采通道继续")
                return True
        self._log(f"⚠ 等待{label}登录超时，补采通道本轮休眠（名单保留，登录后可继续）")
        return False

    # ── 名单通道进度（纯名单模式也要有确定百分比，不能一直 0% 转圈）────────
    def _list_units(self, slug):
        """某一路的进度单位 (已查家次, 总家次)。

        台账只列「登记过的」通道，而刚点开始采集时某一路可能一次都没 pop 过（队列还没
        建）——这时它的待采数就该是 master 的全量：分母绝不能因为「还没轮到」少算一路，
        否则 100 家×3 路会先显示成 100 家次、跑完第一路就谎报 100%。
        """
        try:
            cc = self.pending.channel_counts([slug]) or {}
        except Exception:
            cc = {}
        one = cc.get(slug) or {}
        tried = int(one.get("tried", 0) or 0)
        pending = int(one.get("pending", 0) or 0)
        if not one:
            pending = int((cc.get("__master__") or {}).get("master", 0) or 0)
        return tried, max(tried + pending, tried)

    def list_progress(self, slugs=None):
        """名单通道整体进度 (已完成家次, 总家次)。

        分母是「家数 × 路数」：三路共用同一份 master，每一路都要从第 1 家查到最后一
        家（用户口径「100 条名单」＝每个平台各跑 100 家），所以勾三路 × 100 家 = 300
        家次，而不是 100 家。查过一家算一家（含平台确实查不到、按约定丢弃的）；被退回
        队首重查的那一家会从 tried 里退出来，进度随之小幅回退——这是诚实显示，比把
        「回队重查」藏起来、最后突然从 99% 跳到 100% 更容易核对。
        """
        done = total = 0
        for slug in list(slugs or self.enabled_list_slugs()):
            d, t = self._list_units(slug)
            done += d
            total += t
        return done, total

    def _emit_list_progress(self, slug):
        """把这一路的台账进度喂给总进度条（与关键词站点共用同一套单位聚合口径）。"""
        try:
            d, t = self._list_units(slug)
            self._on_site_kw_progress(slug, d, t)
        except Exception:
            pass

    # ── 拦截持续监测（熔断器半开态，2026-11-11 用户反馈定版）──────────────
    def _watch_block_cleared(self, slug, c, rt, label):
        """本路停止发采集请求，只做只读轮询等人工把验证过完。

        True＝验证已解除（可以继续采）；False＝用户停止/取消勾选/本路已被叫停；
        None＝盯满一轮仍没人处理（由 _recover_after_block 决定再给一轮还是收工）。

        为什么要有这一步：旧实现是「限时等 10 分钟 → 超时就永久让路，请重新开始任务」，
        用户后来在浏览器里过完了验证，这一路却已经收工了（2026-11-11 实机：爱企查查到
        47 家后停在验证页，用户过了验证也得重开任务才接着跑）。这里改成业界熔断器的
        half-open 语义：OPEN＝零采集请求、每 15 秒读一次页面签名（URL/标题/正文长度），
        期间**不导航、不刷新**（人在拖滑块时刷一下挑战就作废）；签名消失＝放行一次真实
        查询，成功就 CLOSED 接着采，仍被拦就重新 OPEN 并计入 watch_cycles。

        只阻塞本路这一条线程，企知道/风鸟照常推进；用户点停止或取消勾选立即退出。
        一轮不是无限期：断路器长期停在 OPEN 会把这一路变成僵尸（用户已经不在电脑前，
        界面却一直挂着"等待验证"），所以每轮最多盯 _list_watch_round_max 秒，计一轮次
        后按上面三种出口处置；轮次用尽才如实结束本路（名单已存盘，下次自动接续）。
        """
        probe = int(getattr(c, "block_probe_interval", 15) or 15)
        round_max = int(self._list_watch_round_max)
        rt["watching"] = True
        rt["watch_cycles"] = int(rt.get("watch_cycles") or 0) + 1
        rt["watch_total"] = int(rt.get("watch_total") or 0) + 1
        self._log(f"👀 {label}这一路转入持续监测：每 {probe} 秒只看一次验证状态、不刷新页面"
                  f"（其余通道照常跑），过完验证自动接着采这一家"
                  f"（第 {rt['watch_cycles']}/{self._list_block_cycles} 轮）")
        waited = 0
        next_beat = self._list_watch_heartbeat
        while True:
            while c._pause_requested and not c._stop_requested:
                time.sleep(0.5)
            if c._stop_requested or not self.list_enabled.get(slug) or rt["halt"]:
                rt["watching"] = False
                return False
            if waited >= round_max:
                rt["watching"] = False
                self._log(f"⏱ {label}这一路已连盯 {round_max // 60} 分钟仍停在验证页，"
                          f"这一轮先记下（第 {rt['watch_cycles']}/{self._list_block_cycles} 轮）")
                return None
            if not self._list_wait(probe, c):
                rt["watching"] = False
                return False
            waited += probe
            try:
                block = c._detect_block()       # 只读探测，不发采集请求、不动页面
            except Exception:
                block = ""
            if not block:
                rt["watching"] = False
                # 限时等待的预算要重新起算：爱企查基类按 _blocked_at 累计「整段拦截」，
                # 不重置的话这一路之后每家公司一进来都算「已超时」，白白多绕一圈监测。
                try:
                    c._blocked_at = 0.0
                except Exception:
                    pass
                self._log(f"✅ {label}验证已通过（已盯约 {max(1, waited // 60)} 分钟），"
                          f"自动接着采这一家")
                # 界面上的红条/弹窗与「等待不计入耗时」的计时暂停都是 blocked_signal
                # 挂上去的，只写日志收不了场：用户已过完验证，横幅还挂着"等你处理"，
                # 计时器也一直停着（看起来像工具卡死）。
                self._emit("block_cleared_signal", f"{label}补采")
                return True
            if waited >= next_beat:
                self._log(f"   👀 {label}仍在等人工过验证…已盯 {waited // 60} 分钟"
                          f"（每 {probe} 秒看一次，不刷新页面、不影响其余通道）")
                next_beat += self._list_watch_heartbeat

    # ── 名单通道看门狗（企知道 / 风鸟 / 爱企查 各跑同一份 master 名单）────
    def _recover_after_block(self, slug, c, rt, label):
        """验证限时等待仍未解除后的统一处置（两处撞墙共用一套语义，别长出两个版本）。

        反复转入持续监测（零采集请求、每 15 秒只读探测、不刷新页面），只有轮次用尽或
        用户停止/取消勾选才真的结束本路。返回 True=验证已解除可接着采，False=本路结束。
        """
        while True:
            if rt["watch_cycles"] >= self._list_block_cycles:
                self._log(f"⛔ {label}本轮已监测 {rt['watch_cycles']} 轮仍未过验证，这一路先结束："
                          f"待采名单已存盘，过完验证重新开始任务会自动接续（已补到的照样入库）")
                rt["halt"] = True
                rt["halted"] = True
                return False
            got = self._watch_block_cleared(slug, c, rt, label)
            if got is True:
                return True
            if got is None:
                continue        # 这一轮到点没人处理：还剩轮次就接着盯
            rt["halt"] = True
            rt["halted"] = True
            return False

    def _run_list_channel(self, slug):
        """某一路名单通道的看门狗循环：pop→查→回执。名单空时每 5s 轮询（废数据随时
        可能回流），空转超 _list_idle_exit 秒则休眠退出；主循环收尾时会再给一次宽限。

        三路共用**同一份** master 名单、各自记 tried/collected/游标，所以「100 家名单」
        = 每一路都从第 1 家查到第 100 家（用户口径），不是三家分摊这 100 家；三路结果最后
        在清洗层按公司名合并成一个文件（手机号/邮箱取并集、英文逗号分隔）。

        分级熔断（一路摔倒不能拖着另两路一起干等）：额度用尽＝当天白跑（立即让路）、
        频率限流＝几分钟自愈（退避后重试）、拦截＝要人动手（限时等，等人期间不烧名单）、
        链路异常/空壳＝连续多家才判死。凡「根本没查成」一律回队首、不记 tried——误记就
        是把公司永久丢掉（用户要求「不要有任何遗漏」），而重复查一次的代价只是几条请求。
        """
        c = self.channels[slug]
        rt = self._rt(slug)
        label = c.PLATFORM_NAME
        idle_since = None
        c._stop_requested = False
        rt["halt"] = False          # 本轮放弃（绝不动开关：关了开关已补到的结果会被导出漏掉）
        rt["halted"] = False
        # 熔断计数必须在每轮起点清零：`reset_results()` 只在「重置任务」里调，用户停止后
        # 直接再点「开始采集」时 rt 是复用的——上一轮攒下的 blocked_streak=2 / watch_cycles=5
        # / attach_try=3 会带着跨轮坏账，新任务一撞验证（或一接不上浏览器）就当轮判死，
        # 又变回「用户过了验证还是接不上」那个老问题。started/游标不清（那是浏览器与台账的
        # 跨轮资产，清了就会重复附加、重复烧额度）。
        for _k in ("shell_streak", "net_streak", "throttle_streak", "blocked_streak",
                   "watch_cycles", "watch_total", "attach_try"):
            rt[_k] = 0
        rt["watching"] = False
        self._log(f"🐕 {label}补采通道启动（看门狗：名单空自动休眠，废数据回流自动苏醒）")
        while not c._stop_requested and self.list_enabled.get(slug) and not rt["halt"]:
            while c._pause_requested and not c._stop_requested:
                time.sleep(0.5)
            if c._stop_requested:
                break
            # 台账→进度条：本轮只跑公司名单时没有关键词单位可算，旧版进度条因此
            # 一直停在 0%（用户反馈「只有名单也要进度条」）。这里每开工一次就把
            # 三路台账刷进同一个聚合口径，关键词模式下两种单位还会一起加总。
            self._emit_list_progress(slug)
            name = self.pending.pop(slug)
            if name is None:
                if self._crawling_active:
                    # 关键词采集仍在进行：清洗随时会把废数据回流入列，
                    # 保持监听（未 started 时尚未附加浏览器，零资源占用）
                    time.sleep(5)
                    continue
                if not rt["started"] and not self.pending.has_work(slug):
                    # 没附加过浏览器、也没活干（纯名单模式已排空）：直接休眠
                    self._log(f"💤 待采名单为空，{label}通道休眠（有废数据回流会自动苏醒）")
                    break
                if idle_since is None:
                    idle_since = time.time()
                elif time.time() - idle_since > self._list_idle_exit:
                    self._log(f"💤 待采名单已空一段时间，{label}通道休眠（下次任务自动接续）")
                    break
                time.sleep(5)
                continue
            idle_since = None
            if not rt["started"]:
                if not self._ensure_list_ready(slug):
                    self.pending.requeue(name, slug)   # 没真正查成：回队首，不记 tried
                    kind = rt["fail_kind"]
                    # 浏览器/驱动型属瞬时故障：退避后重试。一次失败就把整轮补采判死是
                    # 2026-11-04 的实际事故 —— 08:33 附加失败后，待采名单一路攒到 30+ 家
                    # 再没被查过一次，而界面上「补采已启用」看着一切正常。
                    if kind in ("chrome", "driver"):
                        rt["attach_try"] += 1
                        if rt["attach_try"] <= self._list_attach_retries:
                            wait = min(60, 5 * (2 ** (rt["attach_try"] - 1)))
                            why = "共享浏览器没起来" if kind == "chrome" \
                                else "浏览器正忙，没接受补采通道的连接"
                            self._log(f"↻ {label}暂时接不上浏览器（{why}），{wait} 秒后重试"
                                      f"（第 {rt['attach_try']}/{self._list_attach_retries} 次）；"
                                      f"其余通道照常跑，待采名单一家都不会丢")
                            if not self._list_wait(wait, c):
                                break
                            continue
                    c0 = self.pending.counts(slug)
                    if kind == "login":
                        self._log(f"💤 {label}还没登录，补采通道本轮休眠："
                                  f"待采 {c0['pending']} 家已存盘，登录后重新开始任务会自动接续")
                    else:
                        self._log(f"💤 {label}补采通道多次重试仍未接上浏览器，本轮放弃："
                                  f"待采 {c0['pending']} 家已存盘，重新开始任务会自动接续")
                    rt["halt"] = True
                    rt["halted"] = True
                    break
                rt["started"] = True
                rt["attach_try"] = 0
            try:
                # 查询前先看这一路是不是停在验证页：三路的拦截签名与过码方式各不相同，
                # 但都实现了 _detect_block()→原因串、_wait_unblock(原因)→是否解除。
                block = c._detect_block()
                if block and not c._wait_unblock(block):
                    # 限时等待没解除：这一家没查成，回队首；不再直接判死本路，而是转入
                    # 持续监测——用户过完验证后自动接着采这一家（旧版在这里让路，用户
                    # 事后过了验证也只能重开任务，2026-11-11 实机反馈）。
                    self.pending.requeue(name, slug)
                    c.bump_stat()
                    self._emit_list_progress(slug)
                    if not self._recover_after_block(slug, c, rt, label):
                        break
                    continue
                self._log(f"🔎 {label}补采：{name}"
                          f"（待采剩 {self.pending.counts(slug)['pending']} 家）")
                result = c.search_company(name)
                # 各平台状态词不同（未找到/查询失败/额度用尽待重试/拦截未解除/跳转失败…），
                # 由通道自己的 classify() 翻译成中性结论，编排层只认 OUTCOME_*（见 site_adapters）
                outcome, reason = c.classify(result)
                if outcome == _adapters.OUTCOME_QUOTA:
                    # 当日额度用尽＝今天再怎么查都是白烧：立即让路。这一家绝不能记成
                    # 「查不到」，否则明天额度恢复了也永远找不回它（用户要求零遗漏）。
                    self.pending.requeue(name, slug)
                    c.bump_stat()
                    self._log(f"🈵 {label}当日额度用尽（{reason}），本路今天让路："
                              f"待采名单已存盘，额度恢复后重新开始任务会自动接续")
                    rt["halt"] = True
                    rt["halted"] = True
                    break
                if outcome == _adapters.OUTCOME_THROTTLE:
                    # 频率限流是几分钟内自愈的状态：退避后重试这一家，而不是放弃整轮。
                    # 但连着多家都限流＝今天这个平台的频率配额已见底，再原地退避就是
                    # 无底洞（纯名单模式下整轮永远收不了尾），让路比死等诚实。
                    rt["throttle_streak"] += 1
                    self.pending.requeue(name, slug)
                    c.bump_stat()
                    if rt["throttle_streak"] >= self._list_throttle_halt:
                        self._log(f"⏳ {label}连续 {rt['throttle_streak']} 家触发频率限流（{reason}），"
                                  f"本路先让路：待采名单已存盘，稍后重新开始任务会自动接续")
                        rt["halt"] = True
                        rt["halted"] = True
                        break
                    wait = min(600, 60 * rt["throttle_streak"])
                    self._log(f"⏳ {label}触发频率限流（{reason}），{wait} 秒后重试这一家"
                              f"（连续 {rt['throttle_streak']} 家）；其余通道照常跑")
                    if not self._list_wait(wait, c):
                        break
                    continue
                if outcome == _adapters.OUTCOME_BLOCKED:
                    # 回执本身就是拦截结论（查询途中撞墙、页面签名还没显形）
                    rt["blocked_streak"] += 1
                    self.pending.requeue(name, slug)
                    c.bump_stat()
                    self._emit_list_progress(slug)
                    if rt["blocked_streak"] >= 2:
                        # 连着两家都撞墙＝不是偶发，转入持续监测等人工（不再"本路先让路"
                        # 把这一路永久判死：用户过完验证就该自动接着采，见 _recover_after_block）
                        if not self._recover_after_block(slug, c, rt, label):
                            break
                    elif not self._list_wait(max(30.0, c.delay_between), c):
                        break
                    continue
                if outcome in (_adapters.OUTCOME_RETRY, _adapters.OUTCOME_SHELL):
                    # 链路异常/空壳＝这一家根本没查成：回队首、不记 tried，链路或登录态
                    # 恢复后自动补采（旧实现把断网记成「查不到→永久丢弃」，2026-11-04
                    # 09:35 断网那一下永久判死了「青岛太平洋海洋工程(深圳)有限公司」，
                    # 而它只是那一刻没网）。连着多家都查不成＝还没恢复，退避重试而不是
                    # 死循环烧名单，攒够阈值就本轮休眠等下次接续。
                    shell = outcome == _adapters.OUTCOME_SHELL
                    rt["shell_streak" if shell else "net_streak"] += 1
                    streak = rt["shell_streak" if shell else "net_streak"]
                    gate = 3 if shell else 4
                    self.pending.requeue(name, slug)
                    c.bump_stat()
                    self._log(f"  ↻ {label}这一家没查成：{reason}；已退回队首，"
                              f"{'重新登录后' if shell else '链路恢复后'}自动补采（不记「查不到」）")
                    if streak >= gate:
                        if shell:
                            # 连续空壳的头号原因是登录态掉了：限时等用户重新登录一次，
                            # 登录还在却仍空壳＝不是登录问题，别再白烧请求，直接休眠。
                            state, _d = c.login_state()
                            if state == "need_login" and self._wait_login_gate(slug):
                                rt["shell_streak"] = 0
                                continue
                        c0 = self.pending.counts(slug)
                        self._log(f"💤 {label}补采连续 {streak} 家"
                                  f"{'详情空壳（多为登录态失效）' if shell else '都是链路异常（多为断网或浏览器掉线）'}"
                                  f"，本轮休眠：待采 {c0['pending']} 家已存盘，"
                                  f"重新开始任务会自动接续（已补到的 {len(c.results)} 家仍照常入库）")
                        rt["halt"] = True
                        rt["halted"] = True
                        break
                    if not self._list_wait(max(3.0, c.delay_between), c):
                        break
                    continue
                if outcome == _adapters.OUTCOME_MISSING:
                    # 平台确实没有这家公司：记 tried 后丢弃（用户原话「查不出的数据直接丢弃」）。
                    # 台账状态列会写「已查过」，不伪装成「还没干」。
                    rt["shell_streak"] = rt["net_streak"] = 0
                    c.bump_stat(missing=True)
                    self.pending.mark_result(name, False, slug)
                    self._log(f"  ⚠ {label}未查到「{name}」，按约定直接丢弃（不再重复尝试）")
                    self._emit("progress_signal", 0, 1)   # 触发总量/状态行刷新
                    time.sleep(max(0.5, c.delay_between))
                    continue
                # HIT：平台查到了内容。进不进表要过完属地关卡才知道（三平台共用
                # _pf_admit）。入库前后一比确定这条有没有真进表：被属地拦下的一家
                # 台账上算「查过了」（记 tried、不回队反复烧请求），但账目不能记成
                # 「查到」，否则看板会出现「查到 N 家、实际入库不足 N 条」的对不上账。
                rt["shell_streak"] = rt["net_streak"] = 0
                rt["throttle_streak"] = rt["blocked_streak"] = 0
                # 真查到了＝熔断彻底闭合（CLOSED），监测轮次重新起算：轮次是给「连着
                # 几轮都放行不了」用的，不该把一小时前的成功也算进坏账里
                rt["watch_cycles"] = 0
                before = len(c.results)
                c._add_result(result)
                found = len(c.results) > before
                if found:
                    c.bump_stat(found=True)
                else:
                    c.bump_stat()      # 只记「查过一家」，既不算查到也不算查不到
                self.pending.mark_result(name, found, slug)
                self._emit("progress_signal", 0, 1)   # 触发总量/状态行刷新
            except Exception as e:
                # 抛异常同样＝没查成，与上面 OUTCOME_RETRY 一个口径：回队首，不记 tried。
                # 但必须同样带闸：断网持续时 pop→异常→requeue→pop 是同一个名字，
                # 不设阈值就原地死循环，而且一家接一家连发请求（2026-11-04 09:35
                # 那种整段断网最容易撞上）。
                rt["net_streak"] += 1
                c.bump_stat()
                self.pending.requeue(name, slug)
                self._log(f"❌ {label}补采「{name}」异常：{str(e).splitlines()[0][:120]}"
                          f"（已退回队首，下次接着补采）")
                if rt["net_streak"] >= 4:
                    c0 = self.pending.counts(slug)
                    self._log(f"💤 {label}补采连续 4 家都异常（多为断网或浏览器掉线），本轮休眠："
                              f"待采 {c0['pending']} 家已存盘，重新开始任务会自动接续"
                              f"（已补到的 {len(c.results)} 家仍照常入库）")
                    rt["halt"] = True
                    rt["halted"] = True
                    break
                if not self._list_wait(max(3.0, c.delay_between), c):
                    break
            time.sleep(max(0.5, c.delay_between))
        c0 = self.pending.counts(slug)
        self._emit_list_progress(slug)      # 收尾再刷一次：进度条停在最后的真实台账上
        # 本轮进过监测就说一句：watch_cycles 在查到后会清零（半开→闭合，判收工用的），
        # 单看它无法回答「这一路为什么跑了 40 分钟」；watch_total 只增不清，专给汇报。
        _wt = int(rt.get("watch_total") or 0)
        self._log(f"🏁 {label}补采通道收工：本轮查到 {c._stat_found} 家"
                  f"｜查不到丢弃 {c._stat_missing} 家"
                  f"｜名单剩余 {c0['pending']} 家（已落盘，下次任务自动接续）"
                  + (f"｜其间撞上验证 {_wt} 次，过完都已自动接上" if _wt else ""))

    # ── 结果合并 ───────────────────────────────────────────────
    def snapshot_results(self):
        """各站原始明细按站点顺序拼接（每条已带「数据来源」），三路名单补采结果殿后。"""
        out = []
        for slug in SITE_ORDER:
            if slug in self.enabled:
                out.extend(self.crawlers[slug].snapshot_results())
        for slug in self.channels:
            if self._list_active(slug):
                out.extend(self.channels[slug].snapshot_results())
        return out

    def per_site_results(self):
        """按站分组的原始结果。名单通道只要有过产出就单列（含本轮关掉的开关）：
        「已补到但没进本轮视图」和「没补到」在两处报表里必须一致，否则原始表会少行。"""
        out = {slug: self.crawlers[slug].snapshot_results()
               for slug in SITE_ORDER if slug in self.enabled}
        for slug, c in self.channels.items():
            if self._list_active(slug):
                out[slug] = c.snapshot_results()
        return out

    def clean_records(self, records=None, log=None):
        """跨站清洗：各站用自己的 _to_cleaner_record 映射（数据来源正确），
        合并后走一次 build_clean_rows 做跨站按公司名去重。企知道/风鸟/爱企查三路
        名单结果进的是**同一次**合并——用户要的「3 个平台采到的数据合并到一个文件」
        正是在这一步完成：同名公司的手机号/固话/邮箱取并集（英文逗号分隔），
        其它字段按「非空补齐」合并，不会因为某一路先入库就把另两路的信息盖掉。
        入库口径再按 filter_import_rows 剔除「手机号/固话/邮箱/微信号全空」的行（不污染后台客户库，
        原始表仍全量保留）。
        records=None：只清洗本次已勾选站点 + 有过产出的名单通道的实时结果；
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
        for slug, c in self.channels.items():
            if records is None and not self._list_active(slug):
                continue
            recs = c.snapshot_results() if records is None else \
                [r for r in records if (r.get("数据来源") or "") == c.PLATFORM_NAME]
            cleaner_recs.extend(c._to_cleaner_record(r) for r in recs)
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
        for c in list(self.crawlers.values()) + list(self.channels.values()):
            try:
                c.reset_run_state()
            except Exception as e:
                self._log(f"⚠ {c.PLATFORM_NAME} 进度复位失败：{str(e).splitlines()[0][:80]}")
        with self._lock:
            self._list_rt = {}
            self._list_threads = {}
            self._done_sites = set()
            self._total_done = 0
            self._kw_progress = {}
            # 逐词游标账目也要清：留着的话 _pending_keywords 还会按上一个任务的
            # 记录跳过老关键词，用户点「开始新任务」等于什么都没采。
            self._kw_ledger = {}
            self._last_keywords = []
        self.set_task_folder("")
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
            ws.title = "B2B企业数据"
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
        """暂停/恢复的广播对象：关键词各站 + 本轮启用的各路名单通道。"""
        return list(self._active_crawlers()) + \
            [c for slug, c in self.channels.items() if self.list_enabled.get(slug)]

    def pause_crawl(self):
        for c in self._broadcast_targets():
            c.pause_crawl()

    def resume_crawl(self):
        for c in self._broadcast_targets():
            c.resume_crawl()

    def stop_crawl(self):
        """停止本轮：所有站与所有名单通道都置停止位。
        通道侧的退避等待（限流 10 分钟、登录 10 分钟）都做成可打断的，
        所以点「停止」不会真的等满——那正是用户抱怨的「停了还在转」。"""
        for c in list(self.crawlers.values()) + list(self.channels.values()):
            c._stop_requested = True

    def start_browser(self, headed=True):
        """确保共享 Chrome 在跑；需浏览器的站各取得自己的标签（每站全程只建一次标签）。
        纯 HTTP 站（NEEDS_BROWSER=False，如 b2b168）不占用标签、不驱动浏览器；
        Selenium 站（1688）与各路名单通道在自己的采集线程里懒附加，这里只保活共享 Chrome。"""
        import shared_chrome as sc
        browser_crawlers = [c for c in self._active_crawlers()
                            if getattr(c, "NEEDS_BROWSER", True) and hasattr(c, "ensure_tab")]
        need_chrome = browser_crawlers or \
            any(getattr(c, "NEEDS_BROWSER", False)
                for c in self._active_crawlers() if not hasattr(c, "ensure_tab")) or \
            any(getattr(c, "NEEDS_BROWSER", False)
                for slug, c in self.channels.items() if self.list_enabled.get(slug))
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
        """收工：关本工具的 CDP pin 标签＋各 Selenium 通道/名单通道自己的标签，共享 Chrome 保留。"""
        import shared_chrome as sc
        targets = list(self.crawlers.values()) + list(self.channels.values())
        pinned = [c.pin_tab_id for c in targets if getattr(c, "pin_tab_id", "")]
        try:
            sc.release_tabs(pinned, self.port, self._log)
        except Exception:
            pass
        for c in targets:
            c.pin_tab_id = ""
            if hasattr(c, "_ws_cache"):
                c._ws_cache = ""
            # 标签收回与「临时借来过验证」必须同步失效：只清 pin_tab_id 而留着借页标志，
            # 站点就会在下一轮去归还一枚早已不存在的标签（见 _release_verify_tab）。
            if hasattr(c, "_borrowed_tab"):
                c._borrowed_tab = False
        c1688 = self.crawlers.get("1688")
        if c1688 is not None:
            try:
                c1688.close_browser()   # 适配器版：只关自己的标签，不碰共享 Chrome
            except Exception:
                pass
        # 三路名单通道各自只关自己那个标签/会话（风鸟 close_tab 里连 chromedriver 会话
        # 都只 quit 自己附加的，绝不 quit 共享 Chrome）
        for c in self.channels.values():
            try:
                c.close_tab()
            except Exception:
                pass

    def open_login_page(self, slug):
        """界面「打开1688 / 打开企知道 / 打开风鸟 / 打开爱企查」按钮：确保共享 Chrome 在跑，
        把对应站点首页摆到用户眼前让其登录。有网络 IO + 可能拉起浏览器，务必在后台线程调用，
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
        c = self.channels.get(slug)
        if c is not None:
            # 三路名单通道都是「自己那一个标签」：attach() 内部已导航到本平台首页，
            # 不会出现「点了打开风鸟却把企知道标签前置」这种串台。
            label = c.PLATFORM_NAME
            if not c.attach():
                return False, f"{label}附加共享浏览器失败"
            self.set_browser_background(False)   # 还原/前置窗口，把登录页递到眼前
            return True, f"{label}已在浏览器打开，请完成登录"
        return False, f"站点 {slug} 无需登录页"

    def list_channel_login_state(self, slug):
        """某一路名单通道的登录态（界面「已登录/未登录」提示用，需在后台线程调）。
        通道还没接上浏览器时回 unknown——这不是「未登录」，文案必须区分开。"""
        c = self.channels.get(slug)
        if c is None:
            return "missing", "该平台在本机不可用"
        if not c.is_attached():
            return "unknown", "还没接上浏览器（开始采集时会自动接）"
        try:
            return c.login_state()
        except Exception as e:
            return "unknown", str(e).splitlines()[0][:80]

    def _pick_site(self, name=""):
        """按站名找采集器；没给名字就找「当前正在等放行的站」，再退而找
        「手上记着被拦页地址的站」（旧适配器的拦截文本里本来就没有站名）。

        三路名单通道也在这里：拦截提醒弹窗的「打开网站让我看一眼 / 我换好网络了」
        对风鸟、爱企查同样必须可用，否则用户看到提醒却没有任何出口。"""
        targets = list(self.crawlers.values()) + list(self.channels.values())
        for c in targets:
            if name and getattr(c, "PLATFORM_NAME", "") == name:
                return c
            if not name and getattr(c, "_blocked", False):
                return c
        if not name:
            for c in targets:
                if getattr(c, "_block_probe_url", ""):
                    return c
        return None

    def open_site_check(self, name=""):
        """拦截提醒里的「打开网站让我看一眼」：由用户主动点，把被拦的那一页（没有则首页）
        摆到共享 Chrome 眼前，让他自己判断「是 IP 被拒（Chrome 也打不开/同样报错）还是
        真有滑块可过」。

        为什么不由工具自动打开：IP 级限流（八方 502、供应商网 520）页面里根本没有验证
        控件，自动拉起窗口只会让用户对着打不开的页面找入口白等（实测八方等了很久）——
        旧文案还写着「已在 Chrome 里打开对应页面」，与实际行为相反，就是客户反馈
        「八方出验证码找不到入口、只能在你软件点确认」的直接原因。
        必须在后台线程调用（有网络 IO、可能拉起浏览器）。返回 (ok, message)。"""
        target = self._pick_site(name)
        if target is None:
            return False, f"「{name or '这个网站'}」当前没有在采集，无页面可打开"
        if not hasattr(target, "open_verify_page"):
            return False, f"「{target.PLATFORM_NAME}」打不开它的网页，请自己用浏览器打开该站看看"
        name = target.PLATFORM_NAME
        url = getattr(target, "_block_probe_url", "") or getattr(target, "BASE_URL", "")
        if not url:
            return False, f"「{name}」没有可打开的地址"
        try:
            if target.open_verify_page(url):
                self.set_browser_background(False)   # 还原/前置窗口，把页面递到眼前
                return True, f"已在 Chrome 里打开 {name} 的网页，你看一眼就行"
        except Exception as e:
            return False, f"打开失败：{str(e).splitlines()[0][:80]}"
        return False, "打开失败：共享浏览器不可用（请确认已安装 Google Chrome）"

    def retry_site_now(self, name=""):
        """拦截提醒里的「我换好网络了，马上继续」：让对应采集器不等退避立即重测。

        这是给完全不懂技术的人用的逃生口：IP 被拦时唯一立刻有效的事就是换个出口 IP
        （手机热点/重拨宽带），而这一下只能人手做；做完点这个按钮，剩下的全部自动，
        不需要他等下一个 45 秒~10 分钟的探测周期，也不需要重启工具或重头采。"""
        target = self._pick_site(name)
        if target is None:
            return False, f"「{name or '这个网站'}」当前没有在采集，不用点这个"
        if not hasattr(target, "retry_now"):
            return False, f"「{getattr(target, 'PLATFORM_NAME', name)}」不支持立即重测"
        waiting = target.retry_now()
        label = getattr(target, "PLATFORM_NAME", "")
        if waiting:
            return True, f"已让 {label} 马上再试一次（被拦计数与采集间隔已重置）"
        return True, f"{label} 当前没在被拦，正常采集不受影响"

    def _is_session_alive(self):
        """本工具持有的浏览器会话是否还活着（界面据此提示「共享浏览器被关了」）。
        三路名单通道也要算：用户关掉窗口的典型现场就是「名单正在跑，浏览器突然没了」，
        这时必须给瞬时故障式的退避重试，而不是把整份名单记成查不到。"""
        targets = self._active_crawlers() + list(self.channels.values())
        for c in targets:
            try:
                if c._is_session_alive():
                    return True
            except Exception:
                pass
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
    def parse_keywords(text, junk_out=None):
        # junk_out 必须透传：界面靠它把「剔掉了哪些碎片、为什么」如实说给用户。
        return MicCrawler.parse_keywords(text, junk_out=junk_out)
