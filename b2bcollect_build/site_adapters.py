# -*- coding: utf-8 -*-
"""site_adapters.py —— 把两个成熟的独立工具适配成 b2bcollect 六通道体系的成员。

  · Ali1688Site   ：ali1688_build/ali1688_crawler.Ali1688Crawler 的子类（关键词并行通道）。
  · QizhidaoSite  ：riskbird_build/qizhidao_crawler.QiZhiDaoCrawler 的子类（名单驱动通道，
                    由 parallel_session 的企知道线程从 PendingCompanyList 逐家喂公司名）。

设计原则（对齐「原样复用成熟模块而非重写」的既定决策）：
  1. **子类化而非包装**：两个爬虫的采集逻辑已实机验证，一行不改全部继承；
     这里只覆写「与共享 Chrome / session 编排契约冲突」的最小切面。
  2. **单共享 Chrome（端口 9255）**：绝不为 1688/企知道再起第二、第三个浏览器
     （用户同事反馈"开工具后电脑很卡"，多 Chrome 实例是最大内存杀手）。
     1688 用 Selenium debuggerAddress 附加；企知道用其原生 attach_port 共享模式。
  3. **共享浏览器纪律**：只操作自己的标签/窗口，绝不关别人的标签
     （原工具独占浏览器时的剪标签逻辑在这里全部覆写）。
  4. **导入安全**：ali1688_build / riskbird_build 里有同名模块
     （customer_library_cleaner、task_registry、yzd_client），sys.path 一律 append，
     且先预热 b2bcollect 自己的副本进 sys.modules，保证清洗口径以本工具为准。
"""
import importlib.util
import re
import sys
import time
from pathlib import Path

# 打包（PyInstaller）后 __file__ 落在临时解包目录，而本工具会跨目录引用
# ali1688_build / riskbird_build 的源文件与 chromedriver。frozen 时：
#   · 外部模块树根 _ROOT 指向 sys._MEIPASS（--add-data 已把 ali1688_build/、
#     riskbird_build/ 各自的 .py 解到此处，跨目录 import 才能命中）；
#   · 本工具可写数据基准 _HERE 指向 exe 同级目录（_MEIPASS 是临时目录、退出即清，
#     b2bcollect_data 里的种子库/进度等持久数据绝不能写在那）。
if getattr(sys, "frozen", False):
    _APP_DIR = Path(sys.executable).resolve().parent
    _HERE = _APP_DIR
    _ROOT = Path(getattr(sys, "_MEIPASS", str(_APP_DIR)))
else:
    _HERE = Path(__file__).resolve().parent
    _ROOT = _HERE.parent


def _writable_data_dir():
    """持久数据目录：先试 exe 同级，写不动就退用户主目录。

    macOS 被 Gatekeeper 隔离的 .app 会从只读随机路径（App Translocation）启动，
    这时 exe 同级在包内且不可写，裸 mkdir 直接 PermissionError 把程序带走；
    与 cdp_base._stable_data_dir 同一策略：Mac .app 优先放包旁边的可见目录，
    先写探针文件验可写，不行再退 ~/.b2bcollect。
    """
    if (getattr(sys, "frozen", False) and sys.platform == "darwin"
            and _HERE.parent.parent.name.endswith(".app")):
        primary = _HERE.parent.parent.parent / "b2bcollect_data"
    else:
        primary = _HERE / "b2bcollect_data"
    for d in (primary, Path.home() / ".b2bcollect" / "b2bcollect_data"):
        try:
            d.mkdir(parents=True, exist_ok=True)
            probe = d / ".write_test"
            probe.write_text("1", encoding="utf-8")
            probe.unlink()
            return d
        except Exception:
            continue
    return primary

try:
    from PyQt5.QtCore import QObject, pyqtSignal
    HAS_PYQT = True
except ImportError:
    HAS_PYQT = False

# ── 预热本目录同名模块（必须在 append 外部路径之前 import，锁定清洗口径）──
import customer_library_cleaner as _cleaner  # noqa: F401  先进 sys.modules 防遮蔽
import shared_chrome as _sc                 # noqa: F401

for _p in (str(_ROOT / "ali1688_build"), str(_ROOT / "riskbird_build")):
    if _p not in sys.path:
        sys.path.append(_p)      # append 而非 insert：本目录模块永远优先

HAS_1688 = False
HAS_QZD = False
Ali1688Crawler = None
QiZhiDaoCrawler = None

try:
    _spec = importlib.util.spec_from_file_location(
        "ali1688_crawler", str(_ROOT / "ali1688_build" / "ali1688_crawler.py"))
    _m1688 = importlib.util.module_from_spec(_spec)
    sys.modules["ali1688_crawler"] = _m1688
    _spec.loader.exec_module(_m1688)
    Ali1688Crawler = _m1688.Ali1688Crawler
    HAS_1688 = True
except Exception as _e:
    print(f"[site_adapters] ⚠ 1688 适配器不可用：{str(_e).splitlines()[0][:120]}", flush=True)

try:
    import qizhidao_crawler as _qzd_mod      # 其依赖 block_guard/chrome_driver 走 append 路径
    QiZhiDaoCrawler = _qzd_mod.QiZhiDaoCrawler
    HAS_QZD = True
except Exception as _e:
    print(f"[site_adapters] ⚠ 企知道适配器不可用：{str(_e).splitlines()[0][:120]}", flush=True)


# ── 信号扩展：session._wire_signals 要连 kw_progress_signal（原 1688 信号类没有，
#    而 _wire_signals 是一整个 try 块，缺一个属性会连累后面的 crawl_finished 也连不上）──
if HAS_PYQT and HAS_1688:
    class _Signals1688(_m1688.CrawlerSignals):
        kw_progress_signal = pyqtSignal(str, int, int)
else:
    _Signals1688 = None

# 1688 关键词推进日志（crawl 主循环固定格式）：「▶ [3/12] 关键词：太阳能路灯」
_KW_LOG_RE = re.compile(r"▶\s*\[(\d+)/(\d+)\]\s*关键词")


if HAS_1688:

    class Ali1688Site(Ali1688Crawler):
        """1688 → b2bcollect 通道适配器（关键词并行，Selenium 附加共享 Chrome 9255）。"""

        PLATFORM_SLUG = "1688"
        PLATFORM_NAME = "1688"
        NEEDS_BROWSER = True          # 参与 start_browser 的共享 Chrome 保活
        HOME_DOMAIN = "1688.com"

        def __init__(self, port=9255):
            super().__init__(debug_port=port)
            if _Signals1688 is not None:
                self.signals = _Signals1688()
            self.pin_tab_id = ""      # Selenium 站不进 shared_chrome 的 CDP pin 表
            # 种子库落在本工具数据目录（与原 1688 工具的库分开，互不污染）；
            # 只读启动环境（App Translocation）下自动退到 ~/.b2bcollect
            self._data_dir = _writable_data_dir()
            self._seen_store_path = self._data_dir / "seen_store_1688.json"
            # session 级量化账目（stats() 八键契约）
            self.stat_pages = 0
            self.stat_listed = 0

        # ── 参数护栏 ────────────────────────────────────────────
        @property
        def max_pages(self):
            return self._max_pages

        @max_pages.setter
        def max_pages(self, v):
            # 基类翻页是 range(1, max_pages+1)：下发 0 会变「一页都不采」的哑炮。
            # session.configure(max_pages=0) 语义是"不限制"，这里守住下限 1。
            try:
                v = int(v)
            except Exception:
                return
            self._max_pages = max(1, v)

        # ── 原始记录补全「数据来源」──────────────────────────
        def _add_result(self, rec):
            """基类构造的原始记录 dict 不带「数据来源」键（CDP 各站都带），导致：
            ① 原始.xlsx 里 1688 那几条数据来源列为空；② 自动保存走
            clean_records(records=...) 按「数据来源」跨站匹配时，1688 记录匹配不上
            任何站点被整批丢弃（实测 10 条 1688 数据采到却没进清洗入库表）。
            所有结果都经此单一漏斗，统一在这里补齐。"""
            try:
                if isinstance(rec, dict) and not str(rec.get("数据来源") or "").strip():
                    rec["数据来源"] = self.PLATFORM_NAME
            except Exception:
                pass
            return super()._add_result(rec)

        # ── 共享浏览器纪律 ──────────────────────────────────────
        def start_browser(self, headed=True):
            """只附加共享 Chrome，绝不自启独立实例（基类在端口死时会 launch 一个
            /tmp/chrome_1688_profile 的新 Chrome —— 在六通道体系里这是内存灾难）。
            共享 Chrome 由 session.start_browser → shared_chrome 统一保证在跑。"""
            with self._browser_lock:
                if self.driver is not None and self._is_session_alive():
                    return True
                if not _sc.is_alive(self.debug_port):
                    self._log("❌ 共享浏览器未就绪（1688 需要它承载登录态），"
                              "请先在界面点「打开浏览器」")
                    return False
                self._ensure_debug_tab()      # 0 标签附加会报 unable to discover open pages
                try:
                    self.driver = self._attach()
                except Exception as e:
                    self._log(f"❌ 1688 附加共享浏览器失败：{str(e).splitlines()[0][:140]}")
                    self.driver = None
                    return False
                self._log("✅ 1688 已附加到共享浏览器")
                return True

        def _ensure_tab(self):
            """共享浏览器下绝不能接管「第一个标签」（那可能是 mic 等 CDP 站正在用的），
            必须给自己新开一个专属标签并全程复用。"""
            handles = self.driver.window_handles
            if getattr(self, "_tab", None) in handles:
                if self.driver.current_window_handle != self._tab:
                    self.driver.switch_to.window(self._tab)
                return
            own = None
            for h in handles:      # 先认领已停在 1688 域的标签（用户登录时开的）
                try:
                    self.driver.switch_to.window(h)
                    if "1688.com" in (self.driver.current_url or ""):
                        own = h
                        break
                except Exception:
                    continue
            if own is None:
                self.driver.switch_to.new_window("tab")
                own = self.driver.current_window_handle
            self._tab = own
            if self.driver.current_window_handle != own:
                self.driver.switch_to.window(own)

        def _prune_to_own_tab(self):
            """关掉 1688 页面自动弹出的多余标签——但**绝不切换/激活任何标签**。
            旧实现用 driver.switch_to.window 逐个遍历所有标签读 URL，而 switch_to
            底层是 CDP Target.activateTarget——会把共享窗口的可见标签切到那个页。
            因为它在每次 _goto 后都被调，多个 Selenium 会话并行时可见标签就在
            每次翻页/进详情后疯狂跳动（用户看到的“标签不停切换”主因）。
            改走 CDP /json/list 只读 URL、/json/close 关闭，全程不激活任何标签。"""
            try:
                import shared_chrome as sc
                own = getattr(self, "_tab", None)
                if not own:
                    return
                targets = sc.list_page_targets(self.debug_port)
                ids = {t.get("id") for t in targets}
                # 句柄与 target id 对不上（异常情形）：宁可不剪也不误关自己的标签
                if own not in ids:
                    return
                for t in targets:
                    tid = t.get("id")
                    if tid and tid != own and "1688.com" in (t.get("url") or ""):
                        sc.close_tab(self.debug_port, tid)
            except Exception:
                pass

        def close_browser(self):
            """只关自己的标签，共享 Chrome 留给其它通道（基类 driver.quit 虽不杀
            attach 的浏览器，但会顺手关窗口，这里收敛成最小动作）。"""
            try:
                if self.driver is not None and getattr(self, "_tab", None):
                    handles = self.driver.window_handles
                    if self._tab in handles:
                        if self.driver.current_window_handle != self._tab:
                            self.driver.switch_to.window(self._tab)
                        self.driver.close()
            except Exception:
                pass
            try:
                if self.driver is not None:
                    self.driver.quit()     # 附加模式下只结束 chromedriver 会话
            except Exception:
                pass
            self.driver = None
            self._tab = None

        # ── 账目 / 进度 ────────────────────────────────────────
        def _log(self, msg):
            super()._log(msg)
            # 借主循环固定格式的关键词日志发 kw_progress（驱动界面定量进度条）
            m = _KW_LOG_RE.search(msg or "")
            if m and self.signals:
                try:
                    self.signals.kw_progress_signal.emit(
                        self.PLATFORM_SLUG, int(m.group(1)), int(m.group(2)))
                except Exception:
                    pass

        def _goto(self, url, tries=4):
            if "beginPage=" in (url or ""):
                self.stat_pages += 1       # 列表页翻页计数（三个渠道 URL 都带 beginPage）
            return super()._goto(url, tries=tries)

        def _parse_list_page(self, html):
            offers = super()._parse_list_page(html)
            self.stat_listed += len(offers or [])
            return offers

        def _parse_company_page(self, html):
            entries = super()._parse_company_page(html)
            self.stat_listed += len(entries or [])
            return entries

        def _parse_main_search_page(self, src):
            pairs = super()._parse_main_search_page(src)
            self.stat_listed += len(pairs or [])
            return pairs

        def stats(self):
            """与 cdp_base.stats() 同构的八键账目（GUI 看板/结束日志直接复用渲染）。"""
            ok = no_contact = failed = 0
            for r in self.snapshot_results():
                st = r.get("状态", "") or ""
                if st == "成功":
                    ok += 1
                elif "无电话" in st or "无联系方式" in st:
                    no_contact += 1
                else:
                    failed += 1
            return {"pages": self.stat_pages, "listed": self.stat_listed, "ok": ok,
                    "early": self._early_skipped, "filtered": self._filtered_count,
                    "no_contact": no_contact, "failed": failed, "got": len(self.results)}

        def reset_run_state(self):
            with self.results_lock:
                self.results = []
            self.stat_pages = 0
            self.stat_listed = 0
            self._filtered_count = 0
            self._early_skipped = 0
            self._seen_members = set()
            self._stop_requested = False
            self._pause_requested = False
            self._crawling = False
            return True

        def clear_seen_store(self):
            self._seen_store = {}
            self._seen_members = set()
            try:
                if self._seen_store_path.exists():
                    self._seen_store_path.unlink()
            except Exception:
                pass
            return True


if HAS_QZD:

    class QizhidaoSite(QiZhiDaoCrawler):
        """企知道 → b2bcollect 名单通道适配器。

        不参与关键词并行：由 session 的企知道线程从待采集名单 pop 公司名，
        走 search_company 逐家补采（看门狗语义：名单空则线程休眠，不占任何资源）。
        复用基类的共享模式（attach_port → 独占新窗口），窗口不关、登录态长期复用。"""

        PLATFORM_SLUG = "qzd"
        PLATFORM_NAME = "企知道"
        NEEDS_BROWSER = True

        def __init__(self, port=9255):
            super().__init__()
            self.attach_port = port
            self.pin_tab_id = ""
            # 共享窗口内以**标签**形式存在（而非另开一个独立窗口）：让 mic/1688/企知道
            # 三个需浏览器的站同居一个 Chrome 窗口的 3 个标签，后台化时一次性整窗最小化，
            # 不会出现“另一个窗口一直杵在最前”。共享 Chrome 已带 --disable-*-throttling
            # 系列开关，同窗后台标签不会被节流，故无需再各占独立窗口。
            self._own_window_type = "tab"
            self._seen_store = {}     # session.seen_store_counts 会读（企知道无种子库）
            self._stat_queried = 0    # 名单通道账目（stats() 由通道侧回填八键）
            self._stat_found = 0
            self._stat_missing = 0
            self.delay_between = 1.5  # 名单逐家查：比原工具略放缓，挂机过夜稳字当头

        # ── 通道入口（session 企知道线程调用）──────────────────
        def attach(self):
            """懒附加共享浏览器（幂等）。基类 attach 分支在 driver 已存在时返回 None，
            这里统一按「附加后 driver 是否可用」判成败。"""
            if not _sc.is_alive(self.attach_port):
                self._log("❌ 共享浏览器未就绪，企知道通道暂缓启动")
                return False
            try:
                self.start_browser(headed=True, attach_port=self.attach_port)
            except Exception as e:
                self._log(f"❌ 企知道附加共享浏览器失败：{str(e).splitlines()[0][:140]}")
                self.driver = None
                return False
            return self.driver is not None

        def login_state(self):
            """(state, detail)：state ∈ ok / need_login / unknown（block_guard 口径）。"""
            try:
                return self.check_login()
            except Exception as e:
                return "unknown", str(e).splitlines()[0][:80]

        # ── 清洗契约 ───────────────────────────────────────────
        def _to_cleaner_record(self, r):
            """企知道查询结果 → cdp_base 十字键形状（法定代表人进「联系人」，
            cleaner 的 make_row 会按 联系人→法定代表人→公司名 兜底并补岗位）。"""
            err = (r.get("错误") or "").strip()
            if err:
                status = "未找到" if "未找到" in err else "查询失败"
            else:
                status = ""
            remark_bits = []
            if r.get("状态"):
                remark_bits.append("工商状态:" + str(r["状态"]))
            if r.get("注册资本"):
                remark_bits.append("注册资本:" + str(r["注册资本"]))
            if r.get("成立日期"):
                remark_bits.append("成立:" + str(r["成立日期"]))
            if r.get("统一社会信用代码"):
                remark_bits.append("信用代码:" + str(r["统一社会信用代码"]))
            return {
                "公司名": r.get("公司名") or "",
                "联系人": r.get("法定代表人") or "",
                "岗位": "",
                "电话": r.get("电话") or "",
                "邮箱": r.get("邮箱") or "",
                "地址": r.get("地址") or "",
                "官网": r.get("官网") or "",
                "数据来源": "企知道",
                "状态": status,
                "备注": "｜".join(remark_bits),
            }

        # ── 账目 ──────────────────────────────────────────────
        def bump_stat(self, found=None, missing=None):
            """通道侧每查完一家回填：found=拿到联系方式/详情，missing=查不到（丢弃）。"""
            self._stat_queried += 1
            if found:
                self._stat_found += 1
            elif missing:
                self._stat_missing += 1

        def stats(self):
            return {"pages": 0, "listed": self._stat_queried, "ok": self._stat_found,
                    "early": 0, "filtered": 0, "no_contact": self._stat_missing,
                    "failed": max(0, self._stat_queried - self._stat_found - self._stat_missing),
                    "got": len(self.results)}

        def reset_run_state(self):
            with self.results_lock:
                self.results = []
            self._stat_queried = 0
            self._stat_found = 0
            self._stat_missing = 0
            self._stop_requested = False
            self._pause_requested = False
            self._crawling = False
            self._shell_streak = 0
            self._shell_gate_passed = False
            return True

        def clear_seen_store(self):
            return True     # 企知道无种子库（防重复由 PendingCompanyList 三台账负责）

        def close_tab(self):
            """收工时只关自己的独占窗口，共享 Chrome 保持运行。"""
            try:
                self._close_own_tab()
            except Exception:
                pass
            try:
                if self.driver is not None:
                    self.driver.quit()     # 附加模式：只结束本 chromedriver 会话
            except Exception:
                pass
            self.driver = None
            self._own_handle = None
