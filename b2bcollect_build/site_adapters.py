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
from typing import Dict

# 打包（PyInstaller）后 __file__ 落在临时解包目录，而本工具会跨目录引用
# ali1688_build / riskbird_build 的源文件与 chromedriver。frozen 时：
#   · 外部模块树根 _ROOT 指向 sys._MEIPASS（--add-data 已把 ali1688_build/、
#     riskbird_build/ 各自的 .py 解到此处，跨目录 import 才能命中）；
#   · 本目录基准 _HERE 指向 exe 同级目录——**它不再是数据落点**：_MEIPASS 是临时目录、
#     退出即清，落点只问 :func:`_writable_data_dir`（口径在 cdp_base 那一份里）。
if getattr(sys, "frozen", False):
    _APP_DIR = Path(sys.executable).resolve().parent
    _HERE = _APP_DIR
    _ROOT = Path(getattr(sys, "_MEIPASS", str(_APP_DIR)))
else:
    _HERE = Path(__file__).resolve().parent
    _ROOT = _HERE.parent


def _writable_data_dir():
    """本工具持久数据目录：实现只在 :meth:`cdp_base.CdpSiteCrawler._stable_data_dir` 一处。

    这里保留一个同名函数是因为**调用方按这个名字用它**（``local_scan._forbidden_dirs``
    拿它来排除「扫到自己产物」的目录，本模块的 1688 通道也覆写父类时用它的结果），
    删掉就是让那两个调用方各写一份落点。以前这里与 cdp_base 各有一份一模一样的候选
    链，2026-11-12 全局自审时两份都要各自补齐「按形态排除包内」和「最后一级临时目录」
    ——同一个形状存两份的结果就是每修一次都得记得修两处，而「记得」从来不是保障。
    现在这个形状在本目录只有一份实现；跨目录那一侧（riskbird_build/runtime_paths.py）
    因为独立运行的 1688/风鸟工具不能依赖本目录，仍是另一份，两边由离线回归钉住不再
    分叉——test_data_dir_offline.py 第 1、2、3、8 节把五路采集器与状态目录逐字比对
    同一组期望，谁改出自己的说法就会红在哪一路。
    """
    return cdp_base.CdpSiteCrawler._stable_data_dir()

try:
    from PyQt5.QtCore import QObject, pyqtSignal
    HAS_PYQT = True
except ImportError:
    HAS_PYQT = False

# ── 预热本目录同名模块（必须在 append 外部路径之前 import，锁定清洗口径）──
import customer_library_cleaner as _cleaner  # noqa: F401  先进 sys.modules 防遮蔽
import shared_chrome as _sc                 # noqa: F401
import province_filter as _pf               # 属地判定词表（1688/企知道通道的省份核验用）
import tool_config as _tc                   # 落点口径本体（.app 形态判定只许问一处）
import cdp_base                             # 数据落点只有一份实现，在本模块里也是它定的

for _p in (str(_ROOT / "ali1688_build"), str(_ROOT / "riskbird_build")):
    if _p not in sys.path:
        sys.path.append(_p)      # append 而非 insert：本目录模块永远优先

HAS_1688 = False
HAS_QZD = False
Ali1688Crawler = None
QiZhiDaoCrawler = None

# ── 跨目录外部模块清单（打包清单的唯一真源）─────────────────────────────
# 为什么这张表必须写在这里，而不是只抄在 workflow 里：本文件是这些模块的**使用者**，
# 运行时是它去 sys._MEIPASS/ali1688_build、riskbird_build 里找这些 .py；而 --add-data
# 是另一处**手抄的副本**。两处只要漏一项，症状不是报错而是「界面上少一个平台」——
# 2026-11-12 实测就是这个形状：风鸟与爱企查的源文件没进包，等级 0 的账号在产物里
# 也只看到企知道一路，而权限矩阵、界面投影、CI 产物自检**全都是绿的**（那时自检只
# 验凭证链，没人验「功能模块在不在包里」）。
# 消费方（都用 ast 读这张表，不 import 本模块，免得把 selenium 拖进 CI 前置步）：
#   · .github/scripts/check_packaging_manifest.py —— 构建前硬门，缺项就不许开建；
#   · b2bcollect_build/test_frozen_delivery_offline.py —— 离线回归，防止改回来。
EXTERNAL_MODULES = (
    "ali1688_build/ali1688_crawler.py",
    "riskbird_build/qizhidao_crawler.py",
    "riskbird_build/block_guard.py",
    "riskbird_build/chrome_driver.py",
    "riskbird_build/riskbird_crawler_v2.py",
    "riskbird_build/aiqicha_crawler_v2.py",
    # 三个采集器共用的「可写数据落点」口径：漏了它不是少一个平台，而是**开机即崩**
    # （v2 采集器顶层 import 它），所以它与采集器本身同级进清单，由同一道构建前硬门看着。
    "riskbird_build/runtime_paths.py",
)

# 外部模块的传递依赖里，**故意不跟着进包**的那几个：打包态由本目录同名模块供给。
# 为什么单列一张表：`ali1688_crawler` 顶层 `import customer_library_cleaner`，硬门按
# 「谁的依赖谁登记」本来会要求把 `ali1688_build/customer_library_cleaner.py` 也 --add-data
# 进去。真不能进——本目录那份是新版本（岗位族识别、号码不截断、云展超长收拢），外部那份
# 是早期副本，两份一起进包就是「同一个形状两份实现」，谁赢取决于 import 顺序，正是本轮
# 落点口径花一整节去拆的那类缺陷。所以这里显式登记「谁供给、为什么不跟着抄」，硬门据此
# 放行，同时校验本目录确实有同名文件（没有就是真漏，照旧拦）。
# 键 = 模块名，值 = 一句理由。改这张表等于改打包清单，硬门会同步生效。
SHADOWED_EXTERNAL_MODULES = {
    "customer_library_cleaner":
        "本目录那份为准（更新过），外部旧副本不进包，避免两份清洗口径",
}

# 名单补采通道里「产品上就该有」的那几路。这里只回答**代码在不在**，不回答
# 「该不该显示」——后者归 edition 能力矩阵（档位/等级），两件事一旦混在一个判据里，
# 就会出现「模块没打进包」被当成「用户没权限」这种谁也查不出来的误诊。
EXPECTED_LIST_CHANNELS = ("qzd", "riskbird", "aiqicha")

# 关键词采集里同样**从外部目录加载**的那几路。判据与名单通道一字不差：它只回答
# 「代码在不在包里」，不回答「该不该给这个账号看」。为什么 1688 要单独列一张表——
# 它的采集器来自 ali1688_build/，与风鸟/爱企查是同一个装载形状，而自检与界面此前
# 只盯名单三路：2026-11-12 全局自审发现，1688 采集器顶层 `import customer_library_cleaner`
# 靠的是「本目录那份同名模块先一步进了 sys.modules」这条隐式链（见上面
# SHADOWED_EXTERNAL_MODULES）。这条链哪天断了（改名、动预热顺序、把外部副本也塞进包），
# 症状与「压根没打 1688」完全一样：界面上少一个来源、日志里一声不响。所以它必须被
# 产物自检打印出来，而不是等用户发现「阿里巴巴怎么没了」。
EXPECTED_KEYWORD_CHANNELS = ("1688",)
KEYWORD_CHANNEL_NAMES = {"1688": "1688 阿里巴巴"}


def missing_keyword_channels() -> tuple:
    """关键词通道里「产品上该有、本包/本机却没装载成功」的那几路。空 = 齐全。

    读的是模块级 ``HAS_*`` 的**当前值**（函数内取，不在定义时快照）：装载是分两处
    发生的，快照会拿到半路的旧值——那正是「自检绿、界面少功能」的成因之一。
    """
    avail = {"1688": HAS_1688}
    return tuple(s for s in EXPECTED_KEYWORD_CHANNELS if not avail.get(s))


# 各适配器加载失败的第一行原因。打包成 --windowed 之后 stdout 没人看得见，这些
# 原因只有落到产物自检报告里才算存在过——否则「少一个平台」永远是一句无从核对的
# 用户口述。键名与 EXPECTED_LIST_CHANNELS / 关键词站名同族（1688 用 "1688"）。
ADAPTER_ERRORS: Dict[str, str] = {}


def _adapter_fail(name: str, exc: BaseException) -> str:
    """记下适配器加载失败原因并返回那句短文案（打印与登记共用一份，不分叉）。"""
    short = str(exc).splitlines()[0][:160] if str(exc) else type(exc).__name__
    ADAPTER_ERRORS[name] = f"{type(exc).__name__}: {short}"
    return short


def missing_list_channels() -> tuple:
    """产品上该有、本机/本包却没加载出来的名单通道。
    空元组 = 三路齐全。与 ``edition.visible_list_channels`` 无关：那条管权限档位，
    这条管「文件在不在」，两条各自成立才能把「被档位藏起来」和「发包漏了」分开。
    """
    return tuple(s for s in EXPECTED_LIST_CHANNELS
                 if not LIST_CHANNEL_AVAILABLE.get(s))


try:
    _spec = importlib.util.spec_from_file_location(
        "ali1688_crawler", str(_ROOT / "ali1688_build" / "ali1688_crawler.py"))
    _m1688 = importlib.util.module_from_spec(_spec)
    sys.modules["ali1688_crawler"] = _m1688
    _spec.loader.exec_module(_m1688)
    Ali1688Crawler = _m1688.Ali1688Crawler
    HAS_1688 = True
except Exception as _e:
    print(f"[site_adapters] ⚠ 1688 适配器不可用：{_adapter_fail('1688', _e)}", flush=True)

try:
    import qizhidao_crawler as _qzd_mod      # 其依赖 block_guard/chrome_driver 走 append 路径
    QiZhiDaoCrawler = _qzd_mod.QiZhiDaoCrawler
    HAS_QZD = True
except Exception as _e:
    print(f"[site_adapters] ⚠ 企知道适配器不可用：{_adapter_fail('qzd', _e)}", flush=True)

HAS_RB = False
HAS_AQC = False
RiskBirdCrawlerV2 = None
AiQiChaCrawlerV2 = None

try:
    import riskbird_crawler_v2 as _rb_mod
    RiskBirdCrawlerV2 = _rb_mod.RiskBirdCrawlerV2
    HAS_RB = True
except Exception as _e:
    print(f"[site_adapters] ⚠ 风鸟适配器不可用：{_adapter_fail('riskbird', _e)}", flush=True)

try:
    import aiqicha_crawler_v2 as _aqc_mod
    AiQiChaCrawlerV2 = _aqc_mod.AiQiChaCrawlerV2
    HAS_AQC = True
except Exception as _e:
    print(f"[site_adapters] ⚠ 爱企查适配器不可用：{_adapter_fail('aiqicha', _e)}", flush=True)

# 各路采集器**模块本身**是否 import 成功。它和 LIST_CHANNEL_AVAILABLE 不是一回事：
# 这一张只回答「文件在不在包里」，那一张回答「这一路现在能不能用」。两张分开，
# 才可能把「发包漏文件」与「本机环境起不来」这两种成因说清楚（判据见下面两个函数）。
_LIST_CHANNEL_LOADED = {"qzd": HAS_QZD, "riskbird": HAS_RB, "aiqicha": HAS_AQC}

# 「这一路要找的模块没进包」这一类异常的开头。它们**自身**就是证据（异常文本说的
# 就是「某个 .py/.so 找不到」），不需要 import 快照来否决；见 channel_failure_kind。
_IMPORT_ERROR_HEADS = ("ModuleNotFoundError", "ImportError")


def channel_module_loaded(slug: str) -> bool:
    """这一路的采集器模块 import 成功了没有（与它能不能被创建无关）。"""
    return bool(_LIST_CHANNEL_LOADED.get(slug))


def channel_failure_kind(slug: str) -> str:
    """这一路失败的种类：``missing`` / ``broken`` / ``""``（没失败）。

    为什么这个判定必须由本模块回答、而不是由界面猜异常类型：
      · ``ModuleNotFoundError`` / ``ImportError`` 一律是 missing：异常文本说的就是
        「某个模块不在包里」，采集器自己不在、还是它的依赖（selenium/cv2）不在，
        对发包方都是同一件事——补 --add-data / --hidden-import；
      · ``FileNotFoundError`` **有歧义**，所以只有它需要 import 快照来定夺：模块没
        加载上时它指包里没有这个文件（missing）；模块明明已经 import 成功（文件必然
        在包里）时它指的是运行期某个可写目录/字体拿不到——2026-11-12 Mac 实机就是
        这一类，只读转置卷上写不出 Cookie 目录，按异常类型报成「安装包缺文件」会把
        发包方指到打包清单上去查，而真正该改的是采集器的数据落点口径（broken）；
      · 其余异常（OSError 只读、AttributeError 缺模板图……）都是「文件在、加载它的
        时候环境炸了」＝ broken。
    """
    err = str(ADAPTER_ERRORS.get(slug) or "")
    if not err:
        return ""
    if err.startswith(_IMPORT_ERROR_HEADS):
        return "missing"
    if err.startswith("FileNotFoundError") and not channel_module_loaded(slug):
        return "missing"
    return "broken"


# ── 名单通道产出契约 ───────────────────────────────────────────
# 三个平台（企知道 / 风鸟 / 爱企查）各自的状态词表完全不同（「未找到」/「查询失败」/
# 「额度用尽待重试」/「拦截未解除」/「跳转失败」…），但它们共用同一套待采名单台账与同
# 一块看板。所以把「这一家到底算查到 / 查不到 / 根本没查成」的判定收进各适配器自己的
# classify()，编排层只认下面这套中性结论——平台差异不再往编排层漏。
#
# classify() 只可能返回 HIT / MISSING / RETRY / SHELL / BLOCKED / QUOTA / THROTTLE；
# FOUND 与 DROPPED 是**编排层**在过完属地关卡（_add_result 是否真进表）之后才产生的
# 账目结论：适配器不知道关卡会不会拦，硬猜就会出现「看板说查到、原始表里没这条」。
OUTCOME_HIT = "hit"              # 平台查到了内容（进不进表由属地关卡定）
OUTCOME_FOUND = "found"          # 查到并入库（记 collected，结果进合并文件）
OUTCOME_MISSING = "missing"      # 平台确实查不到（记 tried、按约定丢弃）
OUTCOME_RETRY = "retry"          # 链路异常＝根本没查成（回队首、不记 tried）
OUTCOME_SHELL = "shell"          # 空壳＝登录态掉线的假成功（回队首 + 重过登录门）
OUTCOME_BLOCKED = "blocked"      # 被平台拦截等人工（回队首 + 本路让路，别烧名单）
OUTCOME_DROPPED = "dropped"      # 查到但被属地关卡拦下（记 tried，账目算「查过未入库」）
OUTCOME_QUOTA = "quota"          # 平台当日额度用尽（回队首 + 本路休眠，明天接续）
OUTCOME_THROTTLE = "throttle"    # 频率限流（回队首 + 连续多家则本路休眠）

OUTCOME_LABELS = {
    OUTCOME_HIT: "查到内容", OUTCOME_FOUND: "查到", OUTCOME_MISSING: "查不到",
    OUTCOME_RETRY: "没查成(链路)", OUTCOME_SHELL: "空壳(登录)", OUTCOME_BLOCKED: "被拦截",
    OUTCOME_DROPPED: "属地拦下", OUTCOME_QUOTA: "额度用尽", OUTCOME_THROTTLE: "频率限流",
}
# 「这一家根本没查成」：一律回队首、不记 tried——误记就是把数据永久丢掉（用户要求
# 「不要有任何遗漏」），而重复查一次的代价只是几条请求。
OUTCOME_NOT_QUERIED = (OUTCOME_RETRY, OUTCOME_SHELL, OUTCOME_BLOCKED,
                       OUTCOME_QUOTA, OUTCOME_THROTTLE)

# ── 名单通道「等人过验证」的统一口径（2026-11-11 用户实机反馈定）────────────
# 用户原话：看门狗发现出现验证时，每隔 15 秒判断是否过了验证，过了就继续采集，没过
# 继续监测，期间不要刷新——人在拖滑块时被刷一下，挑战直接作废、又得从头过一遍。
# 三条通道共用同一组常量，避免出现「企知道 5 秒、风鸟 30 秒、爱企查 15 秒」这种
# 同一件事三套节奏的口径分叉。
LIST_BLOCK_PROBE_INTERVAL = 15    # 只读轮询间隔（读 URL/标题/正文长度，不发采集请求）
LIST_BLOCK_HEAL_EVERY = 60        # 每 60 秒试一次非破坏性自愈（换回已验证通过的标签）
# 转入「持续监测」后的心跳：每这么久报一次还在盯，避免日志看起来像卡死
LIST_BLOCK_WATCH_HEARTBEAT = 300
# 一轮监测的最长时长：断路器一直停在 OPEN 不看门就是僵尸态（人早走了，界面还挂着
# 「等待验证」、这一轮永远收不了尾）。60 分钟足够人吃个饭回来接着过验证；到点记一轮，
# 轮次用尽（parallel_session._list_block_cycles）才如实结束本路。
LIST_BLOCK_WATCH_ROUND_MAX = 3600


def _shell_check(result):
    """「查到一家但什么都没拿到」＝未登录时的假成功，不能当查到、也不能当查不到丢掉。
    判据来自 block_guard（三个平台共用同一套字段口径），导入失败时保守判 False。

    block_guard 的空壳键只看 法定代表人/注册资本/地址（工商三件套），而本体系的最终
    目的是联系方式：已拿到手机号/邮箱的记录就算工商字段全缺也是有效产出，判成空壳会
    白烧请求、并把这一路推向「连续 3 家空壳 → 通道休眠」——风鸟列表卡片与爱企查小公司
    详情页都实测存在这种「只有电话、没有工商块」的页面。所以这里加一条联系方式逃生门。
    """
    if not isinstance(result, dict):
        return True
    if any(str(result.get(k) or "").strip() for k in ("电话", "邮箱", "手机号")):
        return False
    try:
        import block_guard
        return bool(block_guard.is_shell_record(result))
    except Exception:
        return False


def _pf_admit(crawler, result, err=""):
    """名单通道共用的属地关卡 + 原始表可读性回填（企知道/风鸟/爱企查一套口径）。

    工商登记地址是全体系最硬的属地证据，所以名单通道必须自己把关：它不参与关键词
    并行、也不经过 cdp_base 的漏斗，不拦就会把外省公司放进「清洗后入库数据」。
    err 非空＝这一家本来就没查到内容：线索照旧进表（不含联系方式，不会污染数据；
    把它们拦在外面反而让「为什么没补到」在原始表里查无对证）。
    返回 True＝可以进表。"""
    name = str(result.get("公司名") or result.get("公司名称") or "").strip()
    pv = str(getattr(crawler, "province", "") or "").strip()
    if not err:
        # 原始表表头只有「公司名称」：名单通道记录只带「公司名」的话这一列会整列空白
        result.setdefault("公司名称", name)
    if not pv or err:
        return True
    text = _pf.record_region_text(result)
    verdict, keep = _pf.judge_record(crawler.PLATFORM_SLUG, pv, result)
    if keep:
        if not str(result.get("所在区域") or "").strip():
            label = _pf.region_label(pv, text)
            if label:
                result["所在区域"] = label
        return True
    short = name[:24]
    pv_display = pv if "," not in pv else "目标省份"
    if verdict == "miss":
        crawler._pf_dropped += 1
        crawler._log(f"🗺 [{pv_display}] {crawler.PLATFORM_NAME}查回的工商登记地址不属目标省份、"
                     f"不入库：{short}（{text.strip()[:36]}）")
    else:
        crawler._pf_unknown += 1
        crawler._log(f"🗺 [{pv_display}] 工商地址也判不出属地、保守不入库：{short}")
    return False


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

        @staticmethod
        def _stable_data_dir():
            """本通道持久数据的落点：只问 :func:`_writable_data_dir` 这一处。

            父类 ``Ali1688Crawler.__init__`` 取的正是 ``self._stable_data_dir()``，
            所以在这里覆写，而不是等父类建完目录再回 ``__init__`` 里改
            ``self._data_dir``：后者会在 Windows 安装目录里凭空留下一个空的
            ``ali1688_data``（父类的可写探针 mkdir 出来的），而说明书、卸载脚本、
            发包前的运行期目录剔除清单对用户都只承诺「三个数据目录」。多出来那一个
            不是笔误——它是「同一件事两份口径」在磁盘上的样子，用户照着文档找不到
            东西时只会怀疑文档。种子库文件名另说：与原 1688 工具的库分开、互不污染。
            """
            return _writable_data_dir()

        def __init__(self, port=9255):
            super().__init__(debug_port=port)
            if _Signals1688 is not None:
                self.signals = _Signals1688()
            self.pin_tab_id = ""      # Selenium 站不进 shared_chrome 的 CDP pin 表
            # 种子库落在本工具数据目录里的独立文件名（落点见上面的 _stable_data_dir；
            # 只读启动环境（App Translocation）下自动退到 ~/.b2bcollect）
            self._seen_store_path = self._data_dir / "seen_store_1688.json"
            # session 级量化账目（stats() 八键契约）
            self.stat_pages = 0
            self.stat_listed = 0
            # 站外 SEM 渠道（www.1688.com/zw/page.html?src=zhanwai）在本体系里默认关闭：
            # 实测该页既不吃 keywords 也不吃 province，本轮 1688 入库的 91 条全部来自
            # 这个渠道、真属广东的只有 12 条 —— 属于「渠道不可信」而非「参数没设对」，
            # 大厂口径是直接封掉不可信渠道，而不是想着怎么给它叠补丁。
            # 需要跑商详/供应商搜索时把这里的开关打开即可（基类逻辑已补相关性护栏）。
            self.sem_channel = False
            # 属地判不出来的公司名回流到企知道待采名单，让工商数据反查一次属地
            self.province_unknown_sink = None

        # ── 参数护栏 ────────────────────────────────────────────
        @property
        def max_pages(self):
            return self._max_pages

        @max_pages.setter
        def max_pages(self, v):
            # 基类翻页是 range(1, max_pages+1)：下发 0 会变「一页都不采」的哑炮。
            # 0/负数在 session 语义里是「不限制」，对本站要翻译成站点硬上限 10 页；
            # 旧写法 max(1, v) 把「不限」读成了「最少」，等于界面说不限页、实际只跑
            # 1 页（客户那轮 1688 只翻 8 页、主搜几乎零新增，根子就在这里）。
            try:
                v = int(v)
            except Exception:
                return
            self._max_pages = v if v > 0 else self.MAX_PAGES_HARD_CAP

        # 1688 基类用 range(1, max_pages+1) 翻页，表达不了「不限」；10 页是实测
        # 不撞风控的安企深度（站方上限约 50 页，越往后越容易被弹验证）。
        MAX_PAGES_HARD_CAP = 10

        # ── 省份精准采集：参数直筛 + 本地核验（strict）──────
        def _apply_province_filter(self, rec):
            """基类只负责把 `&province=` 拼进搜索 URL；这里拿每条记录自己带回来的
            地址/公司名把属地核一遍，判不出就不入库（province_filter 把 1688 列为 strict：
            实测那个参数在站外页上完全不生效，不能拿它当保证）。
            同时修掉一个伪数据源：不再拿已选省份盖进空白的「所在区域」，
            回填的值永远有页面证据支撑（「广东·深圳」这种）。"""
            pv = str(getattr(self, "province", "") or "").strip()
            if not pv or not isinstance(rec, dict):
                return rec
            text = _pf.record_region_text(rec)
            verdict, keep = _pf.judge_record(self.PLATFORM_SLUG, pv, rec)
            if keep:
                if not str(rec.get("所在区域") or "").strip():
                    label = _pf.region_label(pv, text)
                    if label:
                        rec["所在区域"] = label
                return rec
            full = str(rec.get("公司名称") or "").strip()
            name = full[:24]
            pv_display = pv if "," not in pv else "目标省份"
            if verdict == "miss":
                self._pf_dropped += 1
                self._log(f"🗺 [{pv_display}] 站方 province 参数没筛住、本地按地址拦下："
                          f"{name}（{text.strip()[:36]}）")
                return None
            self._pf_unknown += 1
            self._log(f"🗺 [{pv_display}] 页面与公司名都没写省份、无法确认，不入库：{name}")
            sink = getattr(self, "province_unknown_sink", None)
            if callable(sink) and full:
                try:
                    sink([full])     # 企知道反查一次属地，比拍脑袋丢/入都便宜
                except Exception:
                    pass
            return None

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
                    "no_contact": no_contact, "failed": failed, "got": len(self.results),
                    "pf_miss": self._pf_dropped,      # 省份把关：确认外省已排除
                    "pf_unknown": self._pf_unknown}   # 省份把关：属地未标注已排除（宁可少不可污）

        def reset_run_state(self):
            with self.results_lock:
                self.results = []
            self.stat_pages = 0
            self.stat_listed = 0
            self._pf_dropped = 0
            self._pf_unknown = 0
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
        LIST_CHANNEL = True         # 名单驱动通道（不参与关键词并行）
        MIN_DELAY = 0.8             # 企知道走 suggestPredict 接口，比列表页/详情页轻得多

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
            # 省份精准采集：企知道是名单驱动通道（拿不到搜索参数），但它的查回结果是
            # 工商登记地址——全体系最硬的属地证据，所以必须在这里补上数据层关卡：
            # 本轮它就放进过 1 条浙江公司（嘉兴市桐乡），属于补采通道无监管的漏洞。
            self.province = ""
            self._pf_dropped = 0
            self._pf_unknown = 0
            self.delay_between = 1.5  # 名单逐家查：比原工具略放缓，挂机过夜稳字当头
            self.block_wait_limit = 600   # 等人工过码上限（见 _wait_unblock）
            # 等验证节奏与另两路一致（15 秒只读轮询）：三路并行时用户在任意一路过
            # 验证，都不该被另一路的刷新打断；本通道不给 heal/revive（见基类注释）。
            self.block_probe_interval = LIST_BLOCK_PROBE_INTERVAL

        # ── 省份关卡（名单通道的入库漏斗）───────────────────
        def _add_result(self, result):
            """企知道结果进表前过一遍属地：登记地址不在本省就不入库。
            查不到/查询失败的记录照旧进：它们是「未找到」台账线索、不含联系方式，
            不会污染数据；因判不出属地而丢线索反而适得其反。
            关卡本体在模块级 _pf_admit：风鸟/爱企查接入后三平台共用同一套判据，
            避免「同一个省口径在三条通道里长出不一致的细节」。"""
            if not isinstance(result, dict):
                return super()._add_result(result)
            err = str(result.get("错误") or "").strip()
            if not _pf_admit(self, result, err):
                return None
            return super()._add_result(result)

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

        def is_attached(self):
            """名单通道是否已接上浏览器：看门狗懒附加的幂等判据（编排层不猜各平台细节）。"""
            return getattr(self, "driver", None) is not None

        def login_state(self):
            """(state, detail)：state ∈ ok / need_login / unknown（block_guard 口径）。"""
            try:
                return self.check_login()
            except Exception as e:
                return "unknown", str(e).splitlines()[0][:80]

        # ── 产出分类（三平台共用台账的翻译层）──────────────────
        def classify(self, result):
            """企知道回执 → 中性结论。基类用「错误」+「可重采」两个键表达全部异常语义：
            可重采＝链路异常（断网/浏览器掉线）＝根本没查成 → 回队首不记 tried；
            不可重采的「未找到」＝平台确实没有这家公司 → 记 tried 后丢弃（用户约定）。
            查到的内容一律回 HIT：进不进表要过完属地关卡才知道（见 OUTCOME_* 契约）。"""
            err = str((result or {}).get("错误") or "").strip()
            if err:
                if (result or {}).get("可重采"):
                    return OUTCOME_RETRY, err[:70]
                return OUTCOME_MISSING, err[:70]
            if _shell_check(result):
                return OUTCOME_SHELL, "详情字段全空（多为登录态失效）"
            return OUTCOME_HIT, ""

        def _wait_unblock(self, reason, max_wait=None):
            """三通道并行后，等人工过码必须有上限：一路死等 30 分钟只会把整份名单
            卡在这一家上（其余两路不受影响，但这一路当晚就废了）。"""
            cap = int(getattr(self, "block_wait_limit", 600) or 600)
            return super()._wait_unblock(reason, min(int(max_wait or cap), cap))

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
                # 结构化字段必须独立成键（理由见 _base_cleaner_record 的注释）：
                # 「所在区域」是属地关卡回填的，跨平台合并时它就是外呼备注里的区域线索。
                "微信号": r.get("微信") or r.get("微信号") or "",
                "行业": r.get("所属行业") or r.get("行业") or "",
                "主营": r.get("经营范围") or r.get("主营") or "",
                "所在区域": r.get("所在区域") or "",
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
                    "got": len(self.results),
                    "pf_miss": self._pf_dropped, "pf_unknown": self._pf_unknown}

        def reset_run_state(self):
            with self.results_lock:
                self.results = []
            self._stat_queried = 0
            self._stat_found = 0
            self._stat_missing = 0
            self._pf_dropped = 0
            self._pf_unknown = 0
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


class _ListChannelBase:
    """风鸟/爱企查 作为「名单通道」接进 b2bcollect 的公共契约（与 QizhidaoSite 同一套口径）。

    三平台能并行的前提是对编排层说同一种话，这里只补四件平台无关的事：
      ① 属地关卡：查回的结果进表前过模块级 _pf_admit（与企知道同一个函数，口径不可能分叉）；
      ② 账目：bump_stat / stats 十键 + reset_run_state，看板与结束日志三平台一套；
      ③ classify：把各平台五花八门的状态词翻译成中性 OUTCOME_*（词表由子类声明）；
      ④ 清洗契约：_to_cleaner_record 产出 cdp_base 十字键，跨平台合并在清洗层完成。
    浏览器接入方式差得太远（风鸟 Selenium、爱企查裸 CDP），attach 与独占标签各自实现。
    """

    PLATFORM_SLUG = ""
    PLATFORM_NAME = ""
    NEEDS_BROWSER = True
    LIST_CHANNEL = True
    # 名单逐家查询的采集间隔安全下限：风鸟两次列表页导航最小 2 秒（min_interval），
    # 低于它就是自己撞「操作过于频繁」；爱企查每家要过联想＋详情页两次导航，同理。
    MIN_DELAY = 2.0

    # ── 状态词表（子类按平台实际词表覆写；判定顺序见 classify）──
    OK_STATUS_WORDS = ("成功",)                                  # 明确的成功标记，不是异常
    QUOTA_STATUS_WORDS = ("额度用尽", "额度已用完")                 # 当日额度墙：本路休眠
    THROTTLE_STATUS_WORDS = ("访问频繁", "操作过于频繁", "频率受限")
    BLOCKED_STATUS_WORDS = ("拦截未解除", "安全验证", "滑动验证")
    RETRY_STATUS_WORDS = ("失败", "浏览器错误", "跳转失败", "会话", "无法恢复")
    MISSING_STATUS_WORDS = ("未找到", "查无")

    def _init_list_channel(self, delay=1.5, block_wait=600):
        """名单通道公共字段初始化（子类在 super().__init__() 之后调用一次）。"""
        self.province = ""
        self._pf_dropped = 0
        self._pf_unknown = 0
        self._stat_queried = 0
        self._stat_found = 0
        self._stat_missing = 0
        self.block_wait_limit = int(block_wait)
        # 等人工过验证的统一节奏（三条通道一套口径，见模块常量注释）：15 秒只做一次
        # 只读轮询、60 秒才试一次不打断人工的自愈；验证页挂着期间绝不发导航/刷新命令。
        self.block_probe_interval = LIST_BLOCK_PROBE_INTERVAL
        self.block_heal_every = LIST_BLOCK_HEAL_EVERY
        self.delay_between = float(delay)
        self.pin_tab_id = ""
        self._seen_store = {}         # 名单通道无种子库（防重复由待采台账负责）

    # ── 状态取值 ──────────────────────────────────────────────
    def _record_status(self, result):
        """(状态, 错误)：风鸟/爱企查把结论写在「状态」，企知道写在「错误」。"""
        return (str((result or {}).get("状态") or "").strip(),
                str((result or {}).get("错误") or "").strip())

    def _status_is_error(self, st):
        """「状态」这个词到底是不是异常结论。

        风鸟的「状态」一栏兼职装工商经营状态（在营/存续/注销），爱企查成功时写「成功」，
        直接当错误串用会把一条好数据误判成线索行；只有命中本平台异常词表才算异常。"""
        if not st or st in self.OK_STATUS_WORDS:
            return False
        words = (self.QUOTA_STATUS_WORDS + self.THROTTLE_STATUS_WORDS +
                 self.BLOCKED_STATUS_WORDS + self.RETRY_STATUS_WORDS +
                 self.MISSING_STATUS_WORDS)
        return any(w in st for w in words)

    # ── 属地关卡（与企知道同一个函数）──────────────────────────
    def _add_result(self, result):
        if not isinstance(result, dict):
            return super()._add_result(result)
        st, err = self._record_status(result)
        hint = err or (st if self._status_is_error(st) else "")
        if not _pf_admit(self, result, hint):
            return None
        return super()._add_result(result)

    # ── 产出分类 ──────────────────────────────────────────────
    def classify(self, result):
        """一条回执 → 中性结论 (outcome, reason)。

        判定顺序很关键：额度/限流/拦截/链路异常都排在「未找到」之前——把平台当日
        额度用完误判成「这家公司查不到」，等于第二天额度恢复了也永远找不回这家。"""
        st, err = self._record_status(result)
        text = f"{st} {err}".strip()
        for words, outcome in ((self.QUOTA_STATUS_WORDS, OUTCOME_QUOTA),
                               (self.THROTTLE_STATUS_WORDS, OUTCOME_THROTTLE),
                               (self.BLOCKED_STATUS_WORDS, OUTCOME_BLOCKED),
                               (self.RETRY_STATUS_WORDS, OUTCOME_RETRY),
                               (self.MISSING_STATUS_WORDS, OUTCOME_MISSING)):
            if any(w in text for w in words):
                return outcome, text[:70]
        if _shell_check(result):
            return OUTCOME_SHELL, "详情字段全空（多为登录态失效）"
        return OUTCOME_HIT, ""

    # ── 登录态 ────────────────────────────────────────────────
    def is_attached(self):
        """名单通道是否已接上浏览器：看门狗懒附加的幂等判据（编排层不猜各平台细节）。"""
        return getattr(self, "driver", None) is not None

    def login_state(self):
        """(state, detail)：state ∈ ok / need_login / unknown（block_guard 口径）。"""
        try:
            state, detail = self.check_login()
        except Exception as e:
            return "unknown", str(e).splitlines()[0][:80]
        # 三个平台的 check_login 都走 block_guard.login_state，返回 missing 表示
        # 「读到了 Cookie 但没有登录标识」＝需要登录；unknown 交给空壳门兜底。
        return ("need_login", detail) if state == "missing" else (state, detail)

    # ── 账目 ──────────────────────────────────────────────────
    def bump_stat(self, found=None, missing=None):
        """通道侧每查完一家回填：found=查到并入库，missing=平台查不到（按约定丢弃）。"""
        self._stat_queried += 1
        if found:
            self._stat_found += 1
        elif missing:
            self._stat_missing += 1

    def stats(self):
        return {"pages": 0, "listed": self._stat_queried, "ok": self._stat_found,
                "early": 0, "filtered": 0, "no_contact": self._stat_missing,
                "failed": max(0, self._stat_queried - self._stat_found - self._stat_missing),
                "got": len(self.results),
                "pf_miss": self._pf_dropped, "pf_unknown": self._pf_unknown}

    def reset_run_state(self):
        with self.results_lock:
            self.results = []
        self._stat_queried = 0
        self._stat_found = 0
        self._stat_missing = 0
        self._pf_dropped = 0
        self._pf_unknown = 0
        self._stop_requested = False
        self._pause_requested = False
        self._crawling = False
        self._shell_streak = 0
        self._shell_gate_passed = False
        return True

    def clear_seen_store(self):
        return True     # 名单通道无跨运行种子库（防重复由 PendingCompanyList 三台账负责）

    def _base_cleaner_record(self, r, source_label):
        """三平台共用的十字键映射（法定代表人进「联系人」，cleaner 的 make_row 会按
        联系人→法定代表人→公司名 兜底并补岗位）。"""
        st, err = self._record_status(r)
        status = err or (st if self._status_is_error(st) else "")
        bits = []
        for label, key in (("注册资本", "注册资本"), ("成立", "成立日期"),
                           ("信用代码", "统一社会信用代码"), ("采集路径", "采集路径"),
                           ("疑似匹配", "疑似匹配")):
            v = str(r.get(key) or "").strip()
            if v:
                bits.append(f"{label}:{v}")
        biz = str(r.get("经营状态") or "").strip() or (
            st if st and st not in self.OK_STATUS_WORDS and not self._status_is_error(st) else "")
        if biz:
            bits.insert(0, "工商状态:" + biz)
        # 行业/主营/所在区域必须走独立键、不能塞备注透传：merge_records 对「备注」
        # 只取首个非空，跨平台合并时后一路的这些结构化信息会被前一路的备注整条盖掉
        # （历史上就是这么把主营/区域洗没的），而 _remark 会自己把它们组装进外呼备注。
        out = {
            "公司名": str(r.get("公司名") or r.get("公司名称") or "").strip(),
            "联系人": str(r.get("法定代表人") or "").strip(),
            "岗位": "",
            "电话": str(r.get("电话") or "").strip(),
            "邮箱": str(r.get("邮箱") or "").strip(),
            "微信号": str(r.get("微信") or r.get("微信号") or "").strip(),
            "地址": str(r.get("地址") or r.get("通信地址") or r.get("注册地址") or "").strip(),
            "官网": str(r.get("官网") or "").strip(),
            "数据来源": source_label,
            "状态": status,
            "备注": "｜".join(bits),
            "行业": str(r.get("所属行业") or r.get("行业") or "").strip(),
            "主营": str(r.get("经营范围") or r.get("主营") or "").strip(),
            "所在区域": str(r.get("所在区域") or "").strip(),
        }
        return out


if HAS_RB:
    from selenium.webdriver.chrome.options import Options as _SeleniumOptions
    from selenium.webdriver.support.ui import WebDriverWait as _WebDriverWait

    class RiskbirdSite(_ListChannelBase, RiskBirdCrawlerV2):
        """风鸟 → b2bcollect 名单通道适配器（与企知道/爱企查共用同一份待采名单）。

        原工具里的风鸟是**独占浏览器**的：start_browser 一上来就 pkill 掉机器上所有
        Chrome（含我们的共享 9255）、chromedriver 也一起杀，随后把用户 Chrome 配置目录
        整份拷到 /tmp 另起实例，首页导航时还会「关闭除第一个外的所有标签」——那是别人
        （mic/1688/企知道/爱企查）正在用的标签。这四条与「一个共享 Chrome、各站只碰自己
        标签」的体系纪律正面冲突，所以整段覆写成企知道同款 debuggerAddress 附加模式；
        采集逻辑（列表页优先、额度自适应、补全接口降级、卡片缓存）一行不动全部继承。
        """

        PLATFORM_SLUG = "riskbird"
        PLATFORM_NAME = "风鸟"

        # 风鸟状态词表（实测常量：STATUS_NO_MATCH/QUOTA/FUZZY_QUOTA/THROTTLE + "失败: xxx"）
        QUOTA_STATUS_WORDS = ("额度用尽", "额度已用完")
        THROTTLE_STATUS_WORDS = ("访问频繁", "操作过于频繁", "频率受限")
        BLOCKED_STATUS_WORDS = ("拦截未解除",)      # 风鸟侧的验证/限流在平台层已归入「访问频繁」
        RETRY_STATUS_WORDS = ("失败", "会话", "无法恢复", "浏览器")
        MISSING_STATUS_WORDS = ("未找到",)
        OK_STATUS_WORDS = ()

        def __init__(self, port=9255):
            super().__init__()
            self.attach_port = int(port)
            self.shared_session = True      # 本适配器永远附加共享 Chrome，绝不另起实例
            self._own_handle = None
            self._own_window_type = "tab"   # 与企知道同居一个窗口的一个标签（见 QizhidaoSite 注释）
            self._init_list_channel(delay=1.5)
            # Cookie / 30 天列表缓存的落点由采集器自己在构造时定（riskbird_build/
            # runtime_paths.py：只读包内绝不落盘）。这里**不再**事后改一遍 _data_dir：
            # 旧写法是在 super().__init__() 之后补救，而基类那句裸 mkdir 已经在前一步
            # 抛过 OSError 了——补丁晚于事故，等于没有补丁（2026-11-12 Mac 崩溃实证）。
            self._load_card_cache()         # 命中缓存的公司不消耗当日模糊查询次数

        # ── 独占标签管理（共享 Chrome 纪律）───────────────────
        def _ensure_own_tab(self) -> bool:
            """保证本会话的当前标签仍是自己的那个（平台偶尔弹新标签也不能跑偏）。"""
            if self.driver is None:
                return False
            try:
                if self.driver.current_window_handle == self._own_handle:
                    return True
                handles = self.driver.window_handles
                if self._own_handle not in handles:
                    self.driver.switch_to.new_window(self._own_window_type)
                    self._own_handle = self.driver.current_window_handle
                    self._log("  原标签已被关闭，风鸟重新开一个独占标签")
                    self._goto_homepage_verified()
                    return True
                self.driver.switch_to.window(self._own_handle)
                return True
            except Exception:
                return False

        def _close_own_tab(self):
            """只关自己的标签，共享 Chrome 与别人的标签一个都不碰。"""
            try:
                if self.driver is not None and self._own_handle:
                    handles = self.driver.window_handles
                    if self._own_handle in handles:
                        if self.driver.current_window_handle != self._own_handle:
                            self.driver.switch_to.window(self._own_handle)
                        self.driver.close()
            except Exception:
                pass

        def start_browser(self, headed: bool = True, attach_port: int = None):
            """附加到共享 Chrome 并开一个独占标签（覆写基类的 pkill + 独占实例）。"""
            with self._browser_lock:
                if self.driver is not None:
                    return True
                port = int(attach_port or self.attach_port)
                if not _sc.is_alive(port):
                    self._log("❌ 共享浏览器未就绪，风鸟通道无法附加")
                    return False
                opts = _SeleniumOptions()
                opts.add_experimental_option("debuggerAddress", f"127.0.0.1:{port}")
                # 附加模式下 chromedriver 不接受 excludeSwitches/useAutomationExtension；
                # 驱动必须先用本地的，否则打包态会走 Selenium Manager 联网查版本（卡顿元凶）
                try:
                    # 借风鸟模块自己的 chrome_driver（本目录没这份副本，且它的
                    # 驱动查找/回退逻辑与 riskbird_build 保持同一套）
                    self.driver = _rb_mod.chrome_driver.create(opts, self._log, ports=[port])
                except Exception as e:
                    self._log(f"❌ 风鸟附加共享浏览器失败：{str(e).splitlines()[0][:140]}")
                    self.driver = None
                    return False
                self.attach_port = port
                self.shared_session = True
                try:
                    self.driver.switch_to.new_window(self._own_window_type)
                    self._own_handle = self.driver.current_window_handle
                except Exception as e:
                    self._log(f"❌ 风鸟开独占标签失败：{str(e).splitlines()[0][:120]}")
                    self.driver = None
                    return False
                try:
                    self.driver.execute_cdp_cmd(
                        "Page.addScriptToEvaluateOnNewDocument",
                        {"source": "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"})
                except Exception:
                    pass
                self.wait = _WebDriverWait(self.driver, 15)
                try:
                    self.driver.set_page_load_timeout(30)
                    self.driver.set_script_timeout(20)
                except Exception:
                    pass
                self._log(f"已附加共享浏览器（端口 {port}），风鸟独占标签 "
                          f"{str(self._own_handle)[:8]}")
                self._goto_homepage_verified()
                return True

        def _goto_homepage_verified(self) -> bool:
            """把自己的标签导航到风鸟首页（基类版本会关掉除第一个外的全部标签＝误关别站）。"""
            if not self._ensure_own_tab():
                return False
            domain = self.BASE_URL.split("//")[-1].split("/")[0]
            for attempt in range(3):
                try:
                    cur = self.driver.current_url or ""
                except Exception:
                    cur = ""
                if domain in cur:
                    return True
                try:
                    self.driver.get(self.BASE_URL)
                    time.sleep(2)
                except Exception:
                    time.sleep(1)
                try:
                    if domain in (self.driver.current_url or ""):
                        return True
                except Exception:
                    pass
                self._log(f"  风鸟首页导航未生效（第{attempt + 1}次），重试…")
            self._log(f"  ⚠ 未能自动打开 {domain}，请手动在浏览器访问")
            return False

        def _restart_browser(self, headed: bool = True):
            """会话崩溃时的复位：基类版本会重走 pkill 全部 Chrome，这里只重建自己的标签。"""
            self._log("⚠️ 风鸟浏览器会话已断开，正在复位自己的标签…")
            self._close_own_tab()
            try:
                if self.driver is not None:
                    self.driver.quit()     # 附加模式：只结束本 chromedriver 会话
            except Exception:
                pass
            self.driver = None
            self._own_handle = None
            time.sleep(2)
            if self.start_browser(headed=headed, attach_port=self.attach_port):
                self._log("✅ 风鸟会话已复位")
                return True
            self._log("❌ 风鸟会话复位失败（共享浏览器可能已关闭）")
            return False

        # ── 拦截与登录 ────────────────────────────────────────
        def _detect_block(self):
            """只看自己这个标签是不是验证页（基类没有这套，共享浏览器里绝不能扫别人的页）。"""
            if self.driver is None:
                return ""
            if not self._ensure_own_tab():
                return ""
            try:
                import block_guard
                return block_guard.detect_selenium(self.driver)
            except Exception:
                return ""

        def _wait_unblock(self, reason, max_wait=None):
            cap = int(getattr(self, "block_wait_limit", 600) or 600)
            wait = min(int(max_wait or cap), cap)
            try:
                import block_guard
                # heal 只做不打断人工的动作（把焦点认领回自己的标签，不导航不刷新）；
                # 导航回首页属破坏性动作，挪到 revive——验证页还挂着时绝不触发，
                # 否则用户正拖到一半的滑块会被刷掉、挑战作废重来（2026-11-11 用户反馈）。
                ok = block_guard.wait_unblock(
                    reason, self._log, self._emit_blocked,
                    lambda: self._stop_requested, self._detect_block,
                    max_wait=wait,
                    interval=int(getattr(self, "block_probe_interval", 15) or 15),
                    heal=self._ensure_own_tab,
                    heal_every=int(getattr(self, "block_heal_every", 60) or 60),
                    revive=self._goto_homepage_verified)
            except Exception:
                return False
            if ok:
                self._emit_cleared(reason)
            return bool(ok)

        # ── 通道入口 ──────────────────────────────────────────
        def attach(self):
            if not _sc.is_alive(self.attach_port):
                self._log("❌ 共享浏览器未就绪，风鸟通道暂缓启动")
                return False
            if self.driver is not None and self._ensure_own_tab():
                return True
            return bool(self.start_browser(headed=True, attach_port=self.attach_port))

        def search_company(self, name):
            """查一家之前先把标签认领回来：上一家如果让平台弹了新标签，基类的 driver.get
            就会打在别人的页面上（共享浏览器里这是最贵的一类串台事故）。"""
            self._ensure_own_tab()
            return super().search_company(name)

        def close_tab(self):
            self._close_own_tab()
            try:
                if self.driver is not None:
                    self.driver.quit()
            except Exception:
                pass
            self.driver = None
            self._own_handle = None

        def close_browser(self):
            """基类版本会 driver.quit()（附加模式下等于只结束本会话），统一走 close_tab。"""
            self.close_tab()

        # ── 账目 / 清洗 ───────────────────────────────────────
        def reset_run_state(self):
            super().reset_run_state()
            # 基类这些状态原本由 crawl() 开头重置，而本工具走的是名单通道循环、
            # 不调 crawl()：不在此复位就会带着昨天的「额度已用完」判定整轮不进详情页。
            self._detail_used = 0
            self._quota_blocked = False
            self._budget_warned = False
            self._fuzzy_blocked = False
            self._fuzzy_warned = False
            self._throttle_hits = 0
            self._backoff = 0
            self._last_nav_ts = 0.0
            self.search_calls = 0
            self.pending_retry = []
            self.pending_detail = []
            return True

        def _to_cleaner_record(self, r):
            """风鸟记录 → 十字键（数据来源固定「风鸟」，跨平台合并靠它认亲）。"""
            return self._base_cleaner_record(r, self.PLATFORM_NAME)


if HAS_AQC:

    class _BlockWaitSentinel:
        """只为借用 AiQiChaCrawlerV2 里「有移交对象 → 等人工限时」这条判据。

        爱企查原实现：handoff is None（单平台跑）时被拦/未登录会原地等满 30 分钟；
        三平台并行时这一路死等没有意义——另外两路各走各的名单，本路让路比卡住好。
        give_up 是空实现：本工具的名单是三平台共享的一份 master，企知道/风鸟本来就会
        各自从头把整份名单跑一遍，不存在「把剩余名单移交别的平台」这件事。
        """

        def give_up(self, companies, why=""):
            return 0

        def pending(self):
            return []

        def finish(self):
            return None

        def wait_for(self, seconds, should_stop=None):
            return False


    class AiqichaSite(_ListChannelBase, AiQiChaCrawlerV2):
        """爱企查 → b2bcollect 名单通道适配器（裸 CDP，天然可挂共享 Chrome）。

        原工具里它已经是「双标签并行」的实现（attach=True 时不启动、不关闭任何 Chrome，
        只开一个自己的独占标签，每条 CDP 命令都显式带上自己的 ws），这套机制与本体系
        的共享浏览器纪律一致，因此这里几乎不需要覆写浏览器逻辑，只补三件事：
          ① 挂到 9255（而不是它自己的 9233，避免同机再起一个 Chrome）；
          ② 独占标签丢失时**绝不**回退去扫全局标签（会拿到企知道/mic 的页面）；
          ③ 注入 handoff 哨兵把「等人工」压到 block_wait_limit，并统一产出契约。
        """

        PLATFORM_SLUG = "aiqicha"
        PLATFORM_NAME = "爱企查"

        # 爱企查状态词表（search_company 实测出口）
        OK_STATUS_WORDS = ("成功",)
        QUOTA_STATUS_WORDS = ("额度",)
        THROTTLE_STATUS_WORDS = ("访问频繁", "操作过于频繁")
        BLOCKED_STATUS_WORDS = ("拦截未解除",)
        RETRY_STATUS_WORDS = ("浏览器错误", "跳转失败", "失败")
        MISSING_STATUS_WORDS = ("未找到",)

        def __init__(self, port=9255):
            super().__init__(debug_port=int(port))
            self._init_list_channel(delay=2.0)
            self.handoff = _BlockWaitSentinel()     # 见类注释③：让「等人工」有上限
            self.diag_dir = None
            # Cookie 落点由基类构造时定（riskbird_build/runtime_paths.py），这里不再
            # 事后改 _data_dir：与风鸟同一理由——基类那句裸 mkdir 早就在 super() 里跑过了。

        # ── 独占标签 ──────────────────────────────────────────
        def open_own_tab(self) -> bool:
            """先试「同一个窗口里新开标签」，实在不行才退回基类的独立窗口。

            基类优先 newWindow 是因为原工具只有两个会话、都要保持可见；本工具是
            「一个共享 Chrome 窗口 + 每站一个标签」，三条名单通道再各开一个窗口就变成
            四个窗口，最小化/前置时永远有一个杵在最前。共享 Chrome 启动时已带
            --disable-*-throttling 系列开关，同窗后台标签不会被节流，标签形式才自洽。
            """
            target_id = None
            try:
                ver = _aqc_mod.requests.get(f"{self.base_url}/json/version", timeout=5).json()
                bws = ver.get("webSocketDebuggerUrl")
            except Exception:
                bws = None
            if bws:
                for params in ({"url": self.BASE_URL}, {"url": self.BASE_URL, "newWindow": True}):
                    try:
                        r = self._send_command(bws, "Target.createTarget", params)
                        target_id = (r.get("result") or {}).get("targetId")
                        if target_id:
                            break
                    except Exception:
                        target_id = None
            if not target_id:
                return super().open_own_tab()       # 兜底走原实现（含 /json/new 那条路）
            self.pin_tab_id = target_id
            self._log(f"已占用爱企查独占标签 {str(target_id)[:8]}")
            for _ in range(8):
                time.sleep(1)
                try:
                    ws = self._get_ws_url()
                    cur = self._execute_js(ws, "(function() { return window.location.href; })()") if ws else ""
                    if cur and "aiqicha" in str(cur):
                        self._wait_page_hydrated(ws, 15)
                        self._log("爱企查页面已打开")
                        return True
                except Exception:
                    pass
            self._log("⚠ 爱企查页面未确认打开，将继续尝试")
            return True

        def _get_or_create_homepage_tab(self):
            """标签被关掉时重建自己的独占标签。

            基类兜底是「遍历全局标签找一个爱企查首页标签」，在共享 Chrome 里这一步
            可能拿到用户自己开的、甚至上一轮残留的爱企查标签，随后 pin_tab_id 仍是空，
            于是每条 CDP 命令都落到 tabs[0]（通常是 mic/1688 的页面）——典型串台事故。
            """
            if self.pin_tab_id:
                ws = self._get_ws_url()
                if ws:
                    return ws
                self.pin_tab_id = None
                self._log("  爱企查标签已不存在，重新开一个独占标签")
            return self.open_own_tab() and self._get_ws_url()

        # ── 通道入口 ──────────────────────────────────────────
        def attach(self):
            if not _sc.is_alive(self.debug_port):
                self._log("❌ 共享浏览器未就绪，爱企查通道暂缓启动")
                return False
            if self.pin_tab_id and self._get_ws_url():
                return True
            try:
                return bool(self.start_browser(headed=True, attach=True))
            except Exception as e:
                self._log(f"❌ 爱企查附加共享浏览器失败：{str(e).splitlines()[0][:140]}")
                return False

        def is_attached(self):
            """爱企查走裸 CDP（基类 self.driver 恒为 None），「接上了」= 自己的标签还在。"""
            return bool(self.pin_tab_id) and bool(self._get_ws_url())

        def search_company(self, name):
            """查之前先认领回自己的标签（同风鸟：平台弹新标签不能带跑这一路）。"""
            if not (self.pin_tab_id and self._get_ws_url()):
                self.open_own_tab()
            return super().search_company(name)

        # 等人工过验证**不再在这里覆写**：历史缺陷是这里写成
        # `return self._wait_unblock_limited(reason)`，而基类 _wait_unblock_limited
        # 又会回调 self._wait_unblock(...)，子类把这个名字覆盖了 → 两个方法互相调用
        # 直到 RecursionError。实机表现为「每次进入等验证都直接爆栈」，用户手工过完
        # 验证工具也永远看不见（2026-11-11 日志「maximum recursion depth exceeded」
        # ＋「⏱ 拦截已持续 10 分钟以上无人处理」）。节奏改由实例属性
        # block_probe_interval / block_heal_every 统一设定，走基类一条实现。

        def _wait_unblock(self, reason, max_wait=None):
            """通道顶部撞墙的限时等待：与另两路同一口径（block_wait_limit 秒）。

            返回 False 不代表放弃这一家，而是交给编排层的持续监测
            （parallel_session._watch_block_cleared：零采集请求、每 15 秒只读探测、
            不刷新页面，用户过完验证自动接着采这一家）。不在这里死等 30 分钟，
            是为了让「已盯 N 分钟」的心跳和轮次记账只长在一个地方。
            **绝不允许**写成 ``return self._wait_unblock_limited(...)``：基类
            _wait_unblock_limited 会回调 self._wait_unblock，子类再覆写回去就是
            2026-11-11 实机 RecursionError 的根因。limited 那套只服务 search_company
            内部撞墙（带 block_wait_limit 与名单移交语义），与这里是两条独立入口。
            """
            cap = int(getattr(self, "block_wait_limit", 600) or 600)
            return bool(super()._wait_unblock(reason, min(int(max_wait or cap), cap)))

        def close_tab(self):
            try:
                self.close_browser()     # attach_mode 下只关自己那一个标签
            except Exception:
                pass
            self.pin_tab_id = None

        # ── 账目 / 清洗 ───────────────────────────────────────
        def reset_run_state(self):
            ok = super().reset_run_state()
            self._blocked_at = 0.0
            self._unknown_streak = 0
            self._evidence_dumped = False
            self._cooldown_until = 0.0
            return ok

        def _to_cleaner_record(self, r):
            """爱企查记录 → 十字键（「成功」这个状态标记不进「状态」列，否则清洗
            报告里会出现一堆假异常行）。"""
            return self._base_cleaner_record(r, self.PLATFORM_NAME)


# ── 名单通道注册表（企知道排第一：它是默认勾选的那一路）──────────
LIST_CHANNEL_SLUGS = ("qzd", "riskbird", "aiqicha")
LIST_CHANNEL_AVAILABLE = {"qzd": HAS_QZD, "riskbird": HAS_RB, "aiqicha": HAS_AQC}
# 平台中文名唯一来源是待采台账模块（pending_companies.CHANNEL_LABELS）：名单 xlsx 的
# 状态列名、台账分段键、界面勾选框文字与采集器 PLATFORM_NAME 必须同源，否则
# 「界面上叫风鸟、原始表里写 riskbird、台账里另一套」这类对不上账的口径分叉。
from pending_companies import CHANNEL_LABELS as LIST_CHANNEL_LABELS  # noqa: E402


def _list_channel_factory(slug):
    """这一路的创建函数；**类本身没加载上来**时返回 None。

    两类失败必须分开（同一条「② 只剩企知道」背后是两种完全不同的账）：
      · 返回 None —— 顶层 import 就失败了，连类都没有，原因早在 import 那一步记进
        ADAPTER_ERRORS，这里不该也不便再记一遍；
      · 返回了函数、调用时抛异常 —— 文件都在，是本机环境让这一路起不来（只读卷、
        被安全软件拦、缺字体），由 build_list_channels 逐路记账。
    写成函数而不是 dict 字面量：三个类各自只在 HAS_* 为真时才存在，字典字面量会在
    import 阶段就撞 NameError。
    """
    if slug == "qzd" and HAS_QZD:
        return lambda port: QizhidaoSite(port=port)
    if slug == "riskbird" and HAS_RB:
        return lambda port: RiskbirdSite(port=port)
    if slug == "aiqicha" and HAS_AQC:
        return lambda port: AiqichaSite(port=port)
    return None


def build_list_channels(port=9255, slugs=None):
    """按需创建名单通道 {slug: 实例}：只建本机可用的平台。

    三平台共用同一份待采名单（PendingCompanyList 的 master），各自有独立游标，
    所以「100 家名单」= 每家平台都从第 1 家跑到第 100 家；采完在清洗层按公司名
    合并成一份文件，手机号/邮箱取并集（英文逗号分隔）。

    **一路创建失败只降这一路，绝不带走整个工具**（2026-11-12 Mac 实机的形状：
    风鸟构造期一个裸 mkdir 抛 OSError，异常穿过 parallel_session 与 MainWindow.__init__，
    用户看到的是「双击没反应」而不是「风鸟这一路今天用不了」）。这里的口径：
      · 记下原因（ADAPTER_ERRORS）并把该路标成不可用，让既有的「该有却没有」播报
        自然接管——界面、tooltip、自检报告说的都是同一份事实，不另写一套话术；
      · 已成功创建的通道照常返回，用户还能用剩下的路干活。
    顶层的 import 失败之所以不这样处理，是因为那时连类都没有；这里失败的是**本机环境**
    （只读卷、缺字体、被安全软件拦），本来就是逐路成立的判据。
    """
    out = {}
    for slug in (slugs or LIST_CHANNEL_SLUGS):
        factory = _list_channel_factory(slug)
        if factory is None:
            continue
        try:
            out[slug] = factory(port)
        except Exception as exc:
            short = _adapter_fail(slug, exc)
            LIST_CHANNEL_AVAILABLE[slug] = False
            try:
                print(f"⚠ {LIST_CHANNEL_LABELS.get(slug, slug)} 通道本次不可用：{short}")
            except Exception:
                pass      # --windowed 产物里 stdout 没人看，落进原因表就够了，绝不为打印再抛
    return out


