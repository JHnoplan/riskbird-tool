# -*- coding: utf-8 -*-
"""local_scan.py —— 全盘扫描本机表格，把「符合条件且没传过」的数据找出来入池。

为什么需要这一层（2026-11-11 用户需求）：过去只有「导入历史数据」，要用户自己在
访达里把 Excel 一个个挑出来。本机实测 295 个表格（桌面 261 + 下载 34），其中 201
个在「采集的数据汇总」里，而且是同一批数据的**十几个时间戳快照**
（``深海装备潜客_海洋装备46_客户1`` 12 个版本、``新能源网企业采集_累积`` 10 个版本）。
全挑一遍等于把同一份数据传十几遍。

四条链路各自的大厂对标（判据都只在一处定义，别处只调用）：

1. **发现层**＝备份/索引工具的目录遍历：靠**标记式剪枝**决定目录进不进
   （有 ``pyvenv.cfg`` 就是虚拟环境、以 ``.`` 开头就是隐藏目录、``.app`` 是包），
   而不是硬编码一串目录名。实测 ``ocr_env / new_ocr_env / clean_ocr_env`` 三个
   虚拟环境里各躺着 33 个 site-packages 测试表格，按名字黑名单永远漏。
   压缩包也在发现范围内（zip / tar 系）：本机的 Excel 有很多是打包发的，
   只认散文件等于「手动导入传得进去、全盘扫描看不见」。展开走
   **内容寻址缓存**（``archive_cache/<哈希前2>/<哈希>.xlsx``）：同内容只落盘一次、
   包内路径不参与落盘所以 zip slip 无路可走、缓存随时可清而下次原样再生成。
2. **台账**＝内容寻址（rsync / restic 的 fingerprint 思路）：按 **sha256** 认文件，
   改名、挪目录、另存一份「_副本」都不会重复上传；**散文件与包内成员用的是同一个
   指纹**，所以一份表既散着放、又装在包里，也只会传一次。size+mtime 未变时复用已存指纹。
3. **合格判定**＝ETL schema discovery：只读表头 + 前 N 行做列映射。整表读实测
   6.2MB 要 10.1 秒、3.35 万行要 17.8 秒，200 个文件全读要 24 分钟，扫描阶段绝不能
   这么干。列匹配仍调用 ``customer_library_cleaner`` 的同一套
   ``detect_header_row`` / ``match_columns``，**不另写一份规则**。
   ⚠ 但只读（流式）模式对来源不可控的 xlsx 会**静默提前终止**（历史事故：协会员工
   名单只读模式只取到 8 行，非只读实际 474 行）。所以轻量版一旦「什么都没读到」
   就必须回落到权威版 ``preview_excel_mapping`` 再判一次，绝不拿截断结果下「不合格」
   结论——那会把好文件静默漏掉，比慢 20 秒严重得多。
4. **去重与冗余**＝分三层，前两层的判据是「可证明的」，第三层只做提示：
   · 指纹台账：这堆字节以前整份传过 → 自动跳过（可证明无损）。同一份内容在
     **本次清单里出现多份**（复制到不同目录、另存一份改名）也走同一判据：
     字节完全相同 ⇒ 解析结果必然相同，所以只勾头一份，其余标 ``dup_content``
     并指明代表在哪（git blob / restic / rsync 的批内去重是同一条不变量）
   · 同族快照：同一目录、去掉时间戳后同名的序列 → 只默认勾最新那份（可解释、
     可一键勾回，兄弟文件绝不隐藏）
   · 池内比对：按 ``company_norm`` 与本地物化视图 diff，**只把「抽样里有多少行
     池里已有」如实显示**，不据此自动取消勾选——采样看不到的行可能有新增，
     漏传是丢数据，多传只是多一个原始文件（读取端按公司名归一合并，不产生脏数据）
5. **勾选资格＝状态的同源投影**（``ELIGIBLE_STATUSES``）：界面上的「只选待入库 /
   全选 / 反选」和默认勾选、上传前的重复提醒，全都读同一个常量。2026-11-11 实测
   缺陷：三个按钮各写一份判据，「全选/反选」把同内容副本、同系列旧版、不合格、
   读不了全部勾上，68 万行重复数据差点一次传完——去重做在扫描层、绕过它却只要
   一次点击，等于没做。批量操作不许跨越资格边界；用户逐行勾副本/旧版是允许的
   （他比工具更知道自己为什么要重传），但上传前必须把代价说清楚。

本模块**只读不写不传**：写台账只发生在上传成功之后（GUI 调 ``Ledger.mark_ingested``），
上传本身完全复用既有的 ``read_records_from_excel`` → ``SyncDialog`` → ``upload_session``。
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import shutil
import tarfile
import time
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Tuple

# 后缀白名单与格式判据永远跟导入链路同源：扫描层认的文件类型 = 真上传能吃的类型，
# 多一份少一份都会出现「扫得到却传不上」或「扫不到其实能传」
from data_pool import (IMPORT_EXCEL_EXTS, IMPORT_ARCHIVE_EXTS,
                       IMPORT_ZIP_EXTS, IMPORT_TAR_EXTS,
                       IMPORT_COMPRESS_EXTS,
                       IMPORT_ARCHIVE_UNSUPPORTED,
                       ARC_UNSAFE_NOTE, ZIP_LINK_NOTE, is_zip_symlink,
                       DataPoolManager, _fix_zip_filename,
                       normalize_company_name, _sheet_usability)

# ── 发现层参数（写成常量便于回归断言与日后调参）───────────────────────────

# 默认扫描根目录：用户真正放数据的三个地方。整卷扫 ``/`` 会把系统卷、网络挂载、
# Time Machine 本地快照都拖进来，收益为零且随时卡死。
DEFAULT_ROOT_NAMES = ("Desktop", "Documents", "Downloads")

MAX_DEPTH = 8                       # 实测有效数据都在 6 层以内
MAX_FILE_BYTES = 64 * 1024 * 1024   # 更大的表基本是日志/图片型工作簿（16MB 的
#                                     体脂秤 BUG 文档就是典型：读 3.3 秒、0 条合格）
MIN_FILE_BYTES = 512                # 几十字节的多半是空壳或写坏的占位文件
MAX_CANDIDATE_FILES = 4000          # 单次扫描收集上限，触顶就如实报「被截断」
MAX_DIRS_WALKED = 20000             # 目录遍历上限，防失控

# ── 压缩包发现与展开参数（2026-11-11 新增：全盘扫描必须进得去压缩包）────────
# 上限存在的原因是「扫一个包」的代价由不得用户控制：一个 40MB 的包可能装着
# 4GB 的东西。超出的部分一律**如实记账**而不是静少不解（少了要能说出来）。
MAX_ARCHIVE_BYTES = 256 * 1024 * 1024      # 整包体积上限（比单表宽松：包必然更大）
# 包的下限跟散表**不能**共用：MIN_FILE_BYTES=512 是为了跳过空壳占位文件，而一个
# 只装着几十行 CSV 的真包完全可能只有 300 字节——拿散表的门槛卡包，会把真数据
# 静默挡在门外（正是这一整套改造最不该再犯的那个错）。空 zip 本体就有 22 字节。
MIN_ARCHIVE_BYTES = 22
ARCHIVE_MEMBER_MAX_BYTES = MAX_FILE_BYTES  # 单个成员沿用散文件的体积上限
MAX_ARCHIVE_EXPAND_BYTES = 512 * 1024 * 1024   # 单包累计解开后最多读多少
MAX_ARCHIVE_MEMBERS = 600                  # 单包最多取多少个表格成员
MAX_ARCHIVE_DEPTH = 3                      # 包内套包层数（同手动导入口径）
# 展开缓存总量上限：超了按最久没用到逐出。内容寻址意味着逐出**不丢信息**，
# 下次扫到同一个成员会原样再生成（git 的 object cache / Bazel 的输出缓存同理）
ARCHIVE_CACHE_MAX_BYTES = 2 * 1024 * 1024 * 1024
ARCHIVE_CACHE_DIRNAME = "archive_cache"

PRUNE_DIR_NAMES = {
    "node_modules", "__pycache__", "site-packages", "library",
    ".trash", "time machine", "dist-info", "venv", "env",
}
PRUNE_DIR_SUFFIXES = (".app", ".bundle", ".framework", ".plugin", ".backup",
                      "_backup", "_old")
VENV_MARKER = "pyvenv.cfg"          # 有这个文件就是虚拟环境，认标记不认名字

# 采样行数：够覆盖「前几行是标题块、真表头在第 5~10 行」的常见形状
QUICK_SAMPLE_ROWS = 200
# 整表复核的体积上限：超过就不做复核（实测 6.2MB 整表读 10.1 秒、3.35 万行 17.8 秒，
# 一个 60MB 的文件能把一次扫描拖成十几分钟）。超上限的文件标「待复核」并照勾。
FULL_CONFIRM_MAX_BYTES = 20 * 1024 * 1024

# 状态常量（界面文案、台账、断言三处共用，避免各写一份字符串）
ST_NEW = "new"                     # 新文件且合格 → 默认勾选
ST_INGESTED = "ingested"           # 指纹显示整份传过 → 默认不勾
ST_FAMILY = "family_sibling"       # 同族快照的非代表 → 默认不勾
ST_INVALID = "invalid"             # 认不出公司名 / 没有联系方式 → 不勾
ST_UNREADABLE = "unreadable"       # 读不了（加密/损坏/格式不支持）→ 不勾
ST_UNCONFIRMED = "unconfirmed"     # 采样判不合格但没做整表复核（大文件）→ 照勾
# 同一份内容（sha256 完全一致）在本次清单里出现了多份：只勾先出现的那一份代表。
# 内容寻址去重必须覆盖**单次扫描内部**，否则「同一文件复制到两个目录」会被当成
# 两份不同数据一起上传（git blob / restic / rsync 的批内去重都是同一条不变量）。
ST_DUP = "dup_content"             # 与清单内另一份字节完全相同 → 不勾，指向那一份

# 「读不了 / 不合格」是**否定结论**：它们只对下结论时的读取能力成立，不对文件字节成立。
NEGATIVE_STATUSES = (ST_INVALID, ST_UNREADABLE)

# 勾选资格的唯一判据（2026-11-11 实机缺陷：界面上的「全选/反选」逐行无条件翻转勾选，
# 把同内容副本、同系列旧版、不合格、读不了全部勾上，265 个文件 68 万行一起往上传——
# 扫描层辛苦做的三层去重被一个按钮整体绕过，而界面按钮的判据是抄出来的第二份）。
# · ELIGIBLE：默认勾、且**批量操作允许勾**的那两类；
# · DUPLICATE：内容层面已被另一份代表覆盖 → 只有用户逐行确认才允许勾；
# · BAD：判定不能入库 → 同上，允许逐行勾（用户比工具懂），但不许批量勾。
ELIGIBLE_STATUSES = (ST_NEW, ST_UNCONFIRMED)
DUPLICATE_STATUSES = (ST_INGESTED, ST_DUP, ST_FAMILY)
BAD_STATUSES = (ST_INVALID, ST_UNREADABLE)

# 读取与判定能力的版本号，写进台账；能力一变，旧否定结论必须作废（2026-11-11 实机缺陷：
# 加完 .xls 字节头分派后，真实台账仍按内容指纹复用升级前记下的「读不了」，把一份 1623
# 条的展会名单永久判死，另有两份空表被误报成「读不了」而不是「不合格」）。文件没变、
# 变的是我们的读法——构建系统的缓存键一直把编译器版本算在内，防的就是这件事。
# ⚠ 改动 read_tables/excel_kind/judge_quality/_sheet_usability 等任何会影响
#   「这个文件读得出什么」的代码时必须把这一行往后加一号，否则老结论会继续冒充新结论。
#   反过来说：**只扩大发现范围不在此列**（2026-11-11 加入压缩包展开就没加号）——
#   包里的成员以前根本没被看到过，不存在需要作废的旧结论；把没变的事情写成
#   「读取能力已更新」会让界面说谎，用户会以为自己的文件被动过。
CAPABILITY = "2026-11-11.2"

# 入库有效性门禁的字段口径（唯一真源是 data_pool._is_valid_record：
# 「电话/邮箱/官网三者取一」，微信与地址**不算**独立证据）。扫描阶段若把只有
# 微信/地址的文件判成合格，上传时会被门禁整行剔掉，界面就成了
# 「预估 500 条 → 实际入库 0 条」，所以这里必须跟门禁完全一致。
GATE_CONTACT_FIELDS = ("电话", "邮箱", "官网")

# 不合格原因常量（界面文案、复核判据、测试断言三处共用，字符串不许各写一份）
NO_SHEET = "没有读到带数据的表格（空文件、只有表头，或图片型工作簿）"
NO_COMPANY = "没认出「公司名」列——数据池按公司名合并去重，缺它无法入库"
NO_CONTACT = "没有电话/邮箱/官网任何一种联系方式，按入库门禁会被整行剔除"
NO_ROWS = "表格里没有数据行"
NO_ENTITY = "整表有效公司名占比过低（判定为说明表/进度表，不是企业名单）"
# 压缩包展开层的「我进去了，但里面没有表格」——与手动导入链路同一句口径
# （data_pool.expand_import_files 里那句「压缩包里没有找到能导入的 Excel/CSV 文件」），
# 两条入口对同一件事说两种话，用户就没办法判断到底是哪一步的问题。
NO_ARCHIVE_TABLE = "压缩包里没有找到能导入的 Excel/CSV 文件"
# 展示时排在「需要用户动手的情况」后面的**正常现象**：包不是坏，只是里面本来就没有
# 企业名单（自家 dist 包、chromedriver.zip、IPSW、源码包都是这一类）；带符号链接成员
# 也一样——那是打包工具的正常产物，不是少了数据。它们仍然一条不少地留在 notes 里，
# 只是不许把「加密取不出」「路径不安全」「包损坏」挤到屏幕外面去。
# 第二项必须是**填好数字的版本**：归族比较的是「句子形状」，模板里的 ``{n}`` 不是数字。
BENIGN_ARCHIVE_NOTES = (NO_ARCHIVE_TABLE, ZIP_LINK_NOTE.format(n=0))
# 归族用的「句子形状」判据：把数字抽掉后同形的原因算同一类（「包里 3 个成员是符号
# 链接」与「包里 12 个成员是符号链接」是同一件事，只是不同包）。
_NOTE_NUM_RE = re.compile(r"\d+")
# 族标题专用的替换：整段「数字 + 前后空白」一起换成「若干」，不留空洞
# （「包里 12 个成员…」直接替换会得到「包里 若干 个成员…」，界面读起来像漏了字）
_NOTE_LABEL_RE = re.compile(r"\s*\d+\s*")
# 「太大了所以没处理」只允许一种说法：包内成员在**读之前**（按包申报的大小）和
# **读完之后**（按真实字节数）两个位置都会撞上这条线，两处各写一句话，界面就会出现
# 同一个包里两种解释。
TOO_BIG_NOTE = (f"成员超过 {ARCHIVE_MEMBER_MAX_BYTES // 1048576}MB，扫描不处理")

# 权威链路说「这张表就是空的」的那几种原文（用来把「空表」和「读不动」分开）：
# 混成「读不了」会让用户以为文件坏了，而实际只是没有数据
_EMPTY_SHEET_RE = re.compile(r"没有读到任何工作表|没有数据行|没有读到任何数据")


def default_roots() -> List[str]:
    """默认扫描根目录（只返回真实存在的）。"""
    home = Path.home()
    return [str(home / n) for n in DEFAULT_ROOT_NAMES
            if (home / n).is_dir()]


def _forbidden_dirs() -> List[str]:
    """本工具自己的产物目录：扫到会把导出的表格再当历史数据传一遍。"""
    out: List[str] = []
    try:
        out.append(str(DataPoolManager._default_cache_dir()))
    except Exception:
        pass
    try:
        from site_adapters import _writable_data_dir
        out.append(str(_writable_data_dir()))
    except Exception:
        pass
    return [os.path.realpath(p) for p in out if p]


def _prune(dirname: str) -> bool:
    """这个子目录该不该整个跳过。"""
    if dirname.startswith("."):
        return True
    low = dirname.lower()
    if low in PRUNE_DIR_NAMES:
        return True
    return any(low.endswith(s) for s in PRUNE_DIR_SUFFIXES)


def iter_tables(roots: Optional[Iterable[str]] = None,
                 should_stop: Optional[Callable[[], bool]] = None,
                 on_progress: Optional[Callable[[str, int], None]] = None,
                 ) -> Tuple[List[dict], List[dict], dict]:
    """遍历根目录收集候选表格与候选压缩包。

    返回 ``([表格], [压缩包], 统计)``。压缩包单列一份是因为**它还不是可读的表格**：
    必须先 ``expand_archives`` 把成员取出来才能判定和上传（2026-11-11 需求：本机很多
    Excel 是装在压缩包里发的，以前发现层直接按后缀过滤掉，等于「手动导入传得进去、
    全盘扫描看不见」——扫不全比扫得慢严重得多）。
    统计逐项如实记账：走了多少目录、剪了多少、权限被拒几处、是否被上限截断——
    「扫全了没有」必须能被回答。
    """
    roots = [r for r in (roots or default_roots()) if os.path.isdir(r)]
    forbidden = _forbidden_dirs()
    files: List[dict] = []
    archives: List[dict] = []
    stats = {"found": 0, "dirs_walked": 0, "dirs_pruned": 0, "denied": 0,
             "skipped_by_name": 0, "truncated": "", "roots": list(roots),
             "archives_found": 0, "archives_unsupported": 0}

    def _denied(err: OSError) -> None:
        stats["denied"] += 1

    for root in roots:
        for dirpath, dirnames, filenames in os.walk(root, onerror=_denied,
                                                    followlinks=False):
            if should_stop and should_stop():
                stats["truncated"] = "已取消"
                return files, archives, stats
            stats["dirs_walked"] += 1
            if stats["dirs_walked"] > MAX_DIRS_WALKED:
                stats["truncated"] = f"目录数触顶 {MAX_DIRS_WALKED}，后面没扫到"
                return files, archives, stats
            rel_depth = os.path.relpath(dirpath, root).count(os.sep) + 1
            if rel_depth > MAX_DEPTH:
                dirnames[:] = []
                continue
            keep = []
            for d in dirnames:
                full = os.path.join(dirpath, d)
                if (_prune(d) or os.path.realpath(full) in forbidden
                        or os.path.exists(os.path.join(full, VENV_MARKER))):
                    stats["dirs_pruned"] += 1
                    continue
                keep.append(d)
            dirnames[:] = keep

            for fn in filenames:
                # ``~$xxx.xlsx`` 是 Office 打开文件时的锁文件，不是数据
                if fn.startswith("~$") or fn.startswith("."):
                    stats["skipped_by_name"] += 1
                    continue
                low = fn.lower()
                if os.path.splitext(fn)[1].lower() in IMPORT_EXCEL_EXTS:
                    kind = "table"
                elif _is_archive_name(low):
                    kind = "archive"
                elif low.endswith(IMPORT_ARCHIVE_UNSUPPORTED):
                    # rar/7z 认得出来但解不开：记账后在汇总里明说，不能让用户
                    # 以为「扫过了，没有数据」（那是把能力边界说成数据边界）
                    stats["archives_unsupported"] += 1
                    continue
                else:
                    continue
                p = os.path.join(dirpath, fn)
                try:
                    st = os.stat(p)
                except OSError:
                    stats["denied"] += 1
                    continue
                if kind == "archive":
                    # 包比单表宽松：上限 256MB（一个包 100MB 很正常，但 NAS 上的整机
                    # 备份包不是「本地表格」）；下限只用 22 字节——装着一份小 CSV 的
                    # 真包完全可能小于散表的 512 字节门槛，拿那个门槛卡包会静默漏数据
                    if not (MIN_ARCHIVE_BYTES <= st.st_size <= MAX_ARCHIVE_BYTES):
                        continue
                    archives.append({"path": p, "size": st.st_size,
                                     "mtime": int(st.st_mtime)})
                    stats["archives_found"] = len(archives)
                else:
                    if not (MIN_FILE_BYTES <= st.st_size <= MAX_FILE_BYTES):
                        continue
                    files.append({"path": p, "size": st.st_size,
                                  "mtime": int(st.st_mtime)})
                # 边发现边记账：任何一处提前 return（取消/触顶）时 found 都是真值
                stats["found"] = len(files)
                if len(files) + len(archives) >= MAX_CANDIDATE_FILES:
                    stats["truncated"] = f"文件数触顶 {MAX_CANDIDATE_FILES}，" \
                                         f"可能还有没扫到的"
                    return files, archives, stats
            if on_progress and stats["dirs_walked"] % 40 == 0:
                on_progress(f"正在浏览目录…已发现 {len(files)} 个表格"
                            f"、{len(archives)} 个压缩包", stats["dirs_walked"])
    return files, archives, stats


# ── 压缩包展开：内容寻址缓存，把「包里的表」变成「能读能传的表」────────────
#
# 为什么落盘而不是原地读：判定链路（``quick_preview`` / 权威 ``read_records_from_excel``）
# 和上传链路吃的都是**真实文件路径**。给包内成员在缓存目录里落一份可寻址的实体，
# 两条链路一行都不用改就能复用——这正是手动导入 ``expand_import_files`` 早就在做
# 的事（staging 思路），区别只在于扫描的产物要活得比一次函数调用久。
#
# 落盘文件名 = 内容的 sha256（git object / restic chunk 的同一条不变量），带来三件
# 免费的好事：
#   · 同内容在 10 个包里出现，只落盘 1 次，扫描与上传都只做 1 次；
#   · 包内路径完全不影响落盘路径，zip slip（``../../.ssh/authorized_keys`` 式成员名）
#     在结构上就无路可走，不需要再靠一串校验去堵；
#   · 缓存可以随便清、随便逐出：下次扫到同一个成员，原样再生成，不存在「缓存坏了
#     数据就不对」这种状态。
#
# 为什么**没有**再建一层「某个包展开过哪些成员」的结果缓存（2026-11-11 实测后决定）：
# 本机三个根目录下共 27 个压缩包、合计 440MB，展开一次的耗时是秒级——多一层持久
# 索引就多一处失效判断（包改了、缓存被逐出了、成员名重了），换来的收益不值得。
# 判据仍然只有一份：成员的指纹就是内容的 sha256，与散放的同一份内容完全相等，所以
# 「同一个 excel 散着放一份、包里放一份、手动导入过又从包扫出来」都只会被传一次。

def archive_cache_dir() -> Path:
    """包内成员的落盘目录（在数据池缓存目录下，已被发现层黑名单排除，
    所以缓存里的表格不会被下一次扫描当成新数据再传一遍）。"""
    return Path(DataPoolManager._default_cache_dir()) / ARCHIVE_CACHE_DIRNAME


def _archive_suffix_of(low_name: str) -> str:
    """包内嵌套包落盘时保留**完整**压缩后缀（``a.tar.gz`` 不是 ``a.gz``）。

    ``os.path.splitext`` 只会给到 ``.gz``，名字一变，``_walk_archive`` 按后缀分派
    时就读不出它原本是什么；后缀清单仍用同源常量，长后缀优先匹配。
    """
    for group in (IMPORT_TAR_EXTS, IMPORT_ZIP_EXTS, IMPORT_COMPRESS_EXTS):
        for s in sorted(group, key=len, reverse=True):
            if low_name.endswith(s):
                return s
    return os.path.splitext(low_name)[1]


def _is_archive_name(low_name: str) -> bool:
    """这个（小写）文件名是不是一个要尝试展开的压缩包。"""
    return low_name.endswith(IMPORT_ARCHIVE_EXTS)


def _stage_blob(data: bytes, ext: str, cache: Path) -> Tuple[str, str]:
    """按内容落盘 → ``(指纹, 真实路径)``。同内容只写一次，写坏不算成功。"""
    digest = hashlib.sha256(data).hexdigest()
    target = cache / digest[:2] / (digest + ext)
    if target.exists() and target.stat().st_size == len(data):
        return "sha256:" + digest, str(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + f".tmp{os.getpid()}")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, target)       # 原子替换：崩在中间也不会留下半截文件
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    return "sha256:" + digest, str(target)


def _member_mtime(info_mtime) -> int:
    """成员自己的时间戳（没有就用包的时间戳兜底）。

    同族快照靠 mtime 选代表：包里两份 ``客户_20261101/_20261109`` 若都拿包的
    mtime，谁新就没法判了，所以必须用成员内部时间。
    """
    if not info_mtime:
        return 0
    if isinstance(info_mtime, tuple):        # zip: (年,月,日,时,分,秒) 本地时间
        try:
            return int(datetime(*info_mtime).timestamp())
        except (ValueError, OSError):
            return 0
    return int(info_mtime)                   # tar: mtime 秒数


def _walk_archive(arch_path: str, prefix: str, out: List[dict],
                  notes: List[Tuple[str, str]], budget: List[int],
                  cache: Path, depth: int, seen: set,
                  mtime_fallback: int,
                  should_stop: Optional[Callable[[], bool]],
                  as_zip: bool, origin: str = "") -> None:
    """递归展开一个包，把里面的表格成员取出来（包中包继续递归）。

    ``out`` 收成员 dict，``notes`` 收「这个包为什么没能全取出来」。所有失败都只
    记账不中断：一个坏包不该让整次扫描白跑（与手动导入同一处置口径）。

    ``as_zip`` 由调用方按**原始成员名**给出，不看落盘文件名：嵌套包在缓存里叫
    ``<sha256>.zip.part``（.part 是为了跟它解出来的内容目录不撞名），拿后缀分派会
    把一个好好的内层包误判成「压缩包已损坏」——实测冒烟就是这样把 2 层包全丢掉的。

    ``origin`` 是**顶层包在磁盘上的真实路径**，递归时原样往下传（内层包落盘名是哈
    希，拿它当「文件在哪」端给用户等于说谎）。成员自己的 ``path`` 也只是缓存副本，
    用户要找回这份数据，唯一有用的坐标就是 origin + logical。
    """
    if depth > MAX_ARCHIVE_DEPTH:
        notes.append((prefix, f"压缩包套了超过 {MAX_ARCHIVE_DEPTH} 层，"
                              "请先手动解压再导入"))
        return
    try:
        if as_zip:
            _walk_zip(arch_path, prefix, out, notes, budget, cache, depth, seen,
                      mtime_fallback, should_stop, origin)
        else:
            _walk_tar(arch_path, prefix, out, notes, budget, cache, depth, seen,
                      mtime_fallback, should_stop, origin)
    except zipfile.BadZipFile:
        notes.append((prefix, "压缩包已损坏，读不出来"))
    except tarfile.TarError:
        notes.append((prefix, "压缩包已损坏，读不出来"))
    except (EOFError, OSError) as e:
        notes.append((prefix, f"压缩包读不出来：{str(e)[:60]}"))


def _emit_member(raw: bytes, arc: str, prefix: str, out: List[dict],
                 notes: List[Tuple[str, str]], budget: List[int],
                 cache: Path, member_mtime: int,
                 origin: str = "") -> bool:
    """一个表格成员 → 指纹、落盘、登记。返回 False 表示预算用尽，调用方该停手。

    所有超出约束的情况都在这里如实记账（界面会逐条说给用户听），绝不静悄悄少取。
    """
    if len(raw) > ARCHIVE_MEMBER_MAX_BYTES:
        notes.append((f"{prefix}/{arc.rsplit('/', 1)[-1]}", TOO_BIG_NOTE))
        return True
    budget[0] -= len(raw)
    if budget[0] < 0:
        notes.append((prefix, f"包里表格解开后超过 "
                              f"{MAX_ARCHIVE_EXPAND_BYTES // 1048576}MB，"
                              "后面的没有展开"))
        return False
    ext = os.path.splitext(arc)[1].lower() or ".xlsx"
    try:
        fp, staged = _stage_blob(raw, ext, cache)
    except OSError as e:
        # 磁盘写不下/没权限属于「这一刻的环境」，不是包内容的错：记账但不下结论
        notes.append((f"{prefix}/{arc.rsplit('/', 1)[-1]}",
                      f"展开到缓存时失败：{str(e)[:60]}"))
        return True
    out.append({"path": staged, "size": len(raw), "mtime": member_mtime,
                "fp": fp, "logical": f"{prefix}/{arc}",
                "archive": prefix, "origin": origin, "in_archive": True})
    return True


def _archive_cap_hit(out: List[dict], prefix: str,
                    notes: List[Tuple[str, str]]) -> bool:
    """成员数是否触顶。触顶只记一次账就停手（每个剩余成员各记一条会把界面刷满）。"""
    if len(out) < MAX_ARCHIVE_MEMBERS:
        return False
    notes.append((prefix, f"一个包里的表格超过 {MAX_ARCHIVE_MEMBERS} 个，"
                          "后面的没有展开"))
    return True


def _arc_ok(arc: str) -> bool:
    """包内成员路径是否可用（``..`` 段与绝对路径一律拒收）。

    落盘走内容寻址（文件名就是内容哈希），本来就没有「按包内路径写出去」这一步，
    zip slip 在结构上无路可走——冒烟实测确认逃逸成员没有在任何目录里落地。但
    **展示名**直接取自包内路径：把 ``../../../../.ssh/x`` 原样端上界面，既不诚实
    （用户会以为工具在自己机器上建了这串目录），也是把外部可控字符串往 UI 里塞。
    所以这里拒收并如实记账，与手动导入链路 ``expand_import_files`` 同一条安全基线。
    """
    return not (arc.startswith("/") or ".." in arc.split("/"))


def _walk_zip(arch_path: str, prefix: str, out: List[dict],
              notes: List[Tuple[str, str]], budget: List[int], cache: Path,
              depth: int, seen: set, mtime_fallback: int,
              should_stop: Optional[Callable[[], bool]],
              origin: str = "") -> None:
    # 符号链接成员单独计数、遍历结束合并成一条：它的内容是「另一个文件的路径」而
    # 不是数据本身（实测本机 app 包里 ``Frameworks/base_library.zip`` 就是 29 字节
    # 的 ``../Resources/base_library.zip``）。以前把它当包去开，报的是「压缩包已损
    # 坏」——那是给一个好文件扣罪名；跟着路径去开又等于让包外可控字符串当坐标，与
    # 发现层 ``followlinks=False`` 同一条线。所以：不打开、不落地、但如实说一句。
    links = [0]

    def _flush_links() -> None:
        if links[0]:
            notes.append((prefix, ZIP_LINK_NOTE.format(n=links[0])))

    try:
        with zipfile.ZipFile(arch_path) as zf:
            for info in zf.infolist():
                if should_stop and should_stop():
                    return
                if _archive_cap_hit(out, prefix, notes):
                    return
                if info.is_dir():
                    continue
                name = _fix_zip_filename(info)     # 包内中文名的乱码还原
                arc = name.replace("\\", "/")
                pure = arc.split("/")[-1]
                if not _arc_ok(arc):
                    notes.append((f"{prefix}/{pure}", ARC_UNSAFE_NOTE))
                    continue
                # macOS 压缩自带元数据、隐藏文件：不是真数据（与手动导入同一条判据）
                if (arc.startswith("__MACOSX/") or "/__MACOSX/" in arc
                        or pure.startswith(".") or pure.startswith("._")):
                    continue
                low = pure.lower()
                is_excel = low.endswith(IMPORT_EXCEL_EXTS)
                is_nested = low.endswith(IMPORT_ZIP_EXTS + IMPORT_TAR_EXTS
                                         + IMPORT_COMPRESS_EXTS)
                if not (is_excel or is_nested):
                    continue
                if is_zip_symlink(info):
                    links[0] += 1
                    continue
                if info.file_size > ARCHIVE_MEMBER_MAX_BYTES:
                    # 先看包自己申报的大小，能挡住 99% 的巨型成员（不必把它整个读进
                    # 内存再说「太大了」）；申报值可以造假，所以下面读的时候仍留上限
                    notes.append((f"{prefix}/{pure}", TOO_BIG_NOTE))
                    continue
                if info.flag_bits & 0x1:
                    notes.append((f"{prefix}/{pure}",
                                  "包里有加密码的成员，取不出来"))
                    continue
                try:
                    with zf.open(info) as src:
                        raw = src.read(ARCHIVE_MEMBER_MAX_BYTES + 1)
                except (NotImplementedError, RuntimeError, zipfile.BadZipFile,
                        OSError) as e:
                    notes.append((f"{prefix}/{pure}",
                                  f"这个成员取不出来：{str(e)[:60]}"))
                    continue
                mts = _member_mtime(info.date_time) or mtime_fallback
                if is_excel:
                    if not _emit_member(raw, arc, prefix, out, notes, budget,
                                        cache, mts, origin):
                        return
                else:
                    # 包中包：本体也按内容寻址落盘（同一个小包在 10 个地方出现只递归
                    # 一次），成员展示名继续往下叠一层路径，用户看得见它从哪来。
                    # 叠的是**完整包内路径**而不是 basename：内层包在包里还有一层目录
                    # 时（资料.tar.gz/资料/内层.zip），只报 basename 会把那层目录吃掉，
                    # 用户按着清单去包里找就找不到——展示路径必须能原样走回去。
                    nested_ext = _archive_suffix_of(low)
                    try:
                        _, nested_path = _stage_blob(raw, nested_ext + ".part", cache)
                    except OSError as e:
                        notes.append((f"{prefix}/{pure}",
                                      f"展开嵌套包失败：{str(e)[:60]}"))
                        continue
                    if nested_path in seen:
                        continue
                    seen.add(nested_path)
                    _walk_archive(nested_path, f"{prefix}/{arc}", out, notes,
                                  budget, cache, depth + 1, seen, mts,
                                  should_stop,
                                  as_zip=low.endswith(IMPORT_ZIP_EXTS),
                                  origin=origin)
                    if budget[0] < 0:
                        return
    finally:
        _flush_links()


def _walk_tar(arch_path: str, prefix: str, out: List[dict],
              notes: List[Tuple[str, str]], budget: List[int], cache: Path,
              depth: int, seen: set, mtime_fallback: int,
              should_stop: Optional[Callable[[], bool]],
              origin: str = "") -> None:
    # 链接成员与 _walk_zip 同一条处置：不打开、不落地、合并成一条实话
    links = [0]

    def _flush_links() -> None:
        if links[0]:
            notes.append((prefix, ZIP_LINK_NOTE.format(n=links[0])))

    try:
        with tarfile.open(arch_path, "r:*") as tf:
            for m in tf:
                if should_stop and should_stop():
                    return
                if _archive_cap_hit(out, prefix, notes):
                    return
                if m.issym() or m.islnk():
                    links[0] += 1
                    continue
                if not m.isreg():       # 目录/设备文件/稀疏成员一律不落盘
                    continue
                raw_arc = m.name.replace("\\", "/")
                if not _arc_ok(raw_arc):
                    notes.append((f"{prefix}/{raw_arc.split('/')[-1]}",
                                  ARC_UNSAFE_NOTE))
                    continue
                arc = raw_arc.lstrip("/")
                pure = arc.split("/")[-1]
                if ("/__MACOSX/" in f"/{arc}" or pure.startswith(".")
                        or pure.startswith("._")):
                    continue
                low = pure.lower()
                is_excel = low.endswith(IMPORT_EXCEL_EXTS)
                is_nested = low.endswith(IMPORT_ZIP_EXTS + IMPORT_TAR_EXTS
                                         + IMPORT_COMPRESS_EXTS)
                if not (is_excel or is_nested):
                    continue
                if m.size > ARCHIVE_MEMBER_MAX_BYTES:
                    notes.append((f"{prefix}/{pure}", TOO_BIG_NOTE))
                    continue
                src = tf.extractfile(m)
                if src is None:
                    notes.append((f"{prefix}/{pure}", "这个成员取不出来"))
                    continue
                try:
                    with src:
                        raw = src.read(ARCHIVE_MEMBER_MAX_BYTES + 1)
                except (OSError, tarfile.TarError) as e:
                    notes.append((f"{prefix}/{pure}",
                                  f"这个成员取不出来：{str(e)[:60]}"))
                    continue
                mts = _member_mtime(m.mtime) or mtime_fallback
                if is_excel:
                    if not _emit_member(raw, arc, prefix, out, notes, budget,
                                        cache, mts, origin):
                        return
                else:
                    nested_ext = _archive_suffix_of(low)
                    try:
                        _, nested_path = _stage_blob(raw, nested_ext + ".part", cache)
                    except OSError as e:
                        notes.append((f"{prefix}/{pure}",
                                      f"展开嵌套包失败：{str(e)[:60]}"))
                        continue
                    if nested_path in seen:
                        continue
                    seen.add(nested_path)
                    # 同 _walk_zip：嵌套前缀用完整包内路径，别吃掉中间那层目录
                    _walk_archive(nested_path, f"{prefix}/{arc}", out, notes,
                                  budget, cache, depth + 1, seen, mts,
                                  should_stop,
                                  as_zip=low.endswith(IMPORT_ZIP_EXTS),
                                  origin=origin)
                    if budget[0] < 0:
                        return
    finally:
        _flush_links()


def expand_archives(archives: List[dict],
                    cache_dir: Optional[str] = None,
                    should_stop: Optional[Callable[[], bool]] = None,
                    on_progress: Optional[Callable[[str, int], None]] = None,
                    ) -> Tuple[List[dict], List[Tuple[str, str]], dict]:
    """把发现的压缩包展开成可判定、可上传的表格成员。

    返回 ``([成员], [(包名, 没取全的原因)], 统计)``。成员的 ``path`` 是真实落盘文件
    （上传链路直接可读），``logical`` 是「包名/包内路径」（界面与台账展示用），
    ``fp`` 已经算好（不用再哈希一遍）。
    """
    cache = Path(cache_dir) if cache_dir else archive_cache_dir()
    members: List[dict] = []
    notes: List[Tuple[str, str]] = []
    seen: set = set()
    st = {"archives": len(archives), "expanded": 0, "members": 0,
          "empty": 0, "failed": 0, "nested": 0}
    cache.mkdir(parents=True, exist_ok=True)
    for i, a in enumerate(archives, start=1):
        if should_stop and should_stop():
            break
        prefix = os.path.basename(a["path"])
        before_m = len(members)
        before_n = len(notes)
        budget = [MAX_ARCHIVE_EXPAND_BYTES]
        try:
            _walk_archive(a["path"], prefix, members, notes, budget, cache,
                          0, seen, int(a.get("mtime") or 0), should_stop,
                          as_zip=prefix.lower().endswith(IMPORT_ZIP_EXTS),
                          origin=a["path"])
        except Exception as e:
            # 单个包炸掉不许拖垮整次扫描（但它必须被说出来，不能被当成「没数据」）
            notes.append((prefix, f"展开时出错：{str(e)[:80]}"))
            st["failed"] += 1
        if len(members) > before_m:
            st["expanded"] += 1
        else:
            st["empty"] += 1
            # 一个表格里都没有、而且上面没记下任何原因（包是好的，只是里面没表格）：
            # 必须说出来。不然汇总里只写「压缩包 N 个」，用户会以为工具进去看过、
            # 结论是「没有数据」，而真实情况可能只是它压根没往里看。
            if len(notes) == before_n:
                notes.append((prefix, NO_ARCHIVE_TABLE))
        if on_progress:
            on_progress(f"正在展开压缩包…{i}/{len(archives)}（{prefix[:30]}）", i)
    st["members"] = len(members)
    st["nested"] = len(seen)      # 递归进去过的嵌套包个数（按内容去重后）
    # 嵌套层里同一个约束可能被反复触顶，重复记账对用户没有信息量
    uniq = list(dict.fromkeys((n, r) for n, r in notes))
    return members, uniq, st


def prune_archive_cache(cache_dir: Optional[str] = None,
                        keep: Optional[Iterable[str]] = None,
                        max_bytes: Optional[int] = None) -> int:
    """把展开缓存压回上限，返回删掉的字节数（LRU，但本批正在用的绝不删）。

    缓存里放的是「包内容的另一份拷贝」，不是唯一副本：删了下次还能原样再生成。
    所以清理可以激进一点（磁盘占满比多花两秒解压难受），唯一红线是本次扫描
    清单里还在引用的那些文件——删掉它们等于让用户点「上传勾选项」时读不到文件。
    """
    cache = Path(cache_dir) if cache_dir else archive_cache_dir()
    limit = ARCHIVE_CACHE_MAX_BYTES if max_bytes is None else max_bytes
    keep_real = {os.path.realpath(p) for p in (keep or [])}
    if not cache.is_dir():
        return 0
    entries = []
    total = 0
    for dirpath, _dirnames, filenames in os.walk(cache):
        for fn in filenames:
            p = os.path.join(dirpath, fn)
            try:
                sz = os.path.getsize(p)
                mt = os.path.getmtime(p)
            except OSError:
                continue
            total += sz
            entries.append((mt, sz, p))
    if total <= limit:
        return 0
    freed = 0
    for mt, sz, p in sorted(entries):           # 最久没用的先逐出
        if total - freed <= limit:
            break
        if os.path.realpath(p) in keep_real:
            continue                            # 本批清单正在用：绝不删
        # 嵌套包本体（``.part``）也照样逐出：它和成员一样是内容寻址的产物，删掉
        # 只是下次要重新解一遍外层包，不改变任何结论。以前给它开了豁免，等于给
        # 缓存留了一条**只进不出**的口子——一堆包中包能把缓存顶到无限大，而这条
        # 口子的理由只是省一次解压，不值得拿磁盘去换。
        try:
            os.remove(p)
            freed += sz
        except OSError:
            pass
    return freed


# ── 台账：内容寻址，认字节不认文件名 ──────────────────────────────────────

def fingerprint(path: str) -> str:
    """流式 sha256（大文件不把整份读进内存）。"""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


class Ledger:
    """本机入池台账：``{指纹 → 什么时候传过、传了几条}``。

    落盘在数据池缓存目录旁的 ``local_scan_ledger.json``（与 master.json 同处，
    清缓存时一起清掉，语义一致）。写盘走「临时文件 + os.replace」原子替换，
    崩在中间也不会留下半截 JSON 把下次启动带走。

    台账里两类条目的可信期**不一样**，这是刻意分开的：
      · ``ingested``——「某时刻真的上传成功过」，是事实，跨能力版本长期有效；
      · ``invalid`` / ``unreadable``——是我们对文件内容的一种**解读**，只在
        ``CAPABILITY`` 那一版读法下成立。加载时版本不符就把这批丢掉重判，
        否则旧读法的失败会一直冒充新读法的结论（2026-11-11 实机：一份 1623 条
        的展会名单在 .xls 已能读之后，仍被旧「读不了」结论按在清单外）。
    """

    VERSION = 1

    def __init__(self, cache_dir: Optional[str] = None):
        base = Path(cache_dir or DataPoolManager._default_cache_dir())
        self.dir = base
        self.file = base / "local_scan_ledger.json"
        self.entries: Dict[str, dict] = {}     # 指纹 → 条目
        self.path_fp: Dict[str, str] = {}      # 绝对路径 → 指纹（复用哈希）
        self.loaded = False
        # 本次因「能力版本变了」而被作废的否定结论条数（界面据此如实说明为什么
        # 上次说读不了的文件这次又有了结论，不让用户对着凭空消失的标记猜）
        self.reset_negatives = 0

    def load(self) -> "Ledger":
        self.entries, self.path_fp = {}, {}
        self.reset_negatives = 0
        try:
            raw = json.loads(self.file.read_text(encoding="utf-8"))
            # 能力版本不一致（含老台账根本没写这一项）时，「读不了/不合格」都不再
            # 可信：那可能是旧读法读不出来的格式，也可能是旧归类口径给错了名字。
            # ingested 不在这个范围里——它记的是「某年某月真的上传成功过」这个事实，
            # 与读法无关，作废它只会把同一批数据再往池子里灌一遍。
            cap_ok = raw.get("capability") == CAPABILITY
            for e in raw.get("files") or []:
                fp = e.get("fp")
                if not fp:
                    continue
                if not cap_ok and e.get("status") in NEGATIVE_STATUSES:
                    self.reset_negatives += 1
                    continue
                self.entries[fp] = e
                for p in e.get("paths") or []:
                    self.path_fp[p] = fp
        except FileNotFoundError:
            pass
        except Exception:
            # 台账读坏绝不能阻止扫描：当空台账重来。最坏结果是多传一次
            # （读取端按公司名合并，不产生脏数据）；坏文件留档便于事后追查。
            try:
                self.file.rename(self.file.with_name(
                    self.file.stem + ".bad.json"))
            except Exception:
                pass
        self.loaded = True
        return self

    def save(self) -> bool:
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            payload = {"version": self.VERSION,
                       "capability": CAPABILITY,
                       "updated": time.strftime("%Y-%m-%dT%H:%M:%S"),
                       "files": list(self.entries.values())}
            tmp = self.file.with_name(self.file.name + ".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False),
                           encoding="utf-8")
            os.replace(tmp, self.file)
            return True
        except Exception:
            return False

    def entry(self, fp: str) -> Optional[dict]:
        return self.entries.get(fp)

    def ingested(self, fp: str) -> bool:
        """只有「真的传成功过」才算传过：不合格/读不了也写在台账里，
        但状态不是 ingested，绝不能伪装成已入库（那等于永久跳过一份没进池的文件）。"""
        e = self.entries.get(fp)
        return bool(e) and e.get("status") == ST_INGESTED

    def _touch(self, fp: str, path: str, size: int, mtime: int) -> dict:
        e = self.entries.get(fp)
        if e is None:
            e = {"fp": fp, "size": size, "mtime": mtime, "paths": []}
            self.entries[fp] = e
        e["size"], e["mtime"] = size, mtime
        if path not in e["paths"]:
            e["paths"] = (e["paths"] + [path])[-8:]   # 只留最近 8 个路径
        return e

    def fp_for(self, path: str, size: int, mtime: int) -> Tuple[str, bool]:
        """取文件指纹；size+mtime 与台账一致才复用旧哈希（构建系统增量思路）。

        返回 ``(指纹, 是否来自缓存)``。mtime 变了必须重算——内容寻址的正确性全押
        在哈希上，用「时间戳变了但没重算」糊过去会把改过的文件当旧的跳过。
        """
        cached = self.path_fp.get(path)
        if cached:
            e = self.entries.get(cached) or {}
            if e.get("size") == size and e.get("mtime") == mtime:
                return cached, True
        fp = fingerprint(path)
        self._touch(fp, path, size, mtime)
        self.path_fp[path] = fp
        return fp, False

    def mark_ingested(self, items: List[dict], batch: str = "") -> int:
        """上传成功后记账。``items`` 用扫描项（含 path/fp/size/mtime）。

        只允许在 ``upload_session`` 真的返回成功后调用：把「打算传」写成「已传过」
        会让下次扫描永久跳过一个其实没进池的文件——那是最坏的一种丢数据。
        """
        n = 0
        for f in items:
            fp = f.get("fp")
            if not fp:
                continue
            e = self._touch(fp, f["path"], f.get("size", 0), f.get("mtime", 0))
            e["status"] = ST_INGESTED
            e["rows_kept"] = int(f.get("rows_kept") or 0)
            e["rows_in"] = int(f.get("rows_in") or 0)
            e["ts"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            if batch:
                e["batch"] = batch
            n += 1
        self.save()
        return n

    def mark_skipped(self, items: List[dict], status: str = ST_INVALID,
                     reason: str = "") -> int:
        """把「不合格/读不了」也记一笔，省得下次扫描再花 3 秒重读同一个大文件。

        状态明确写成 invalid/unreadable，``ingested()`` 只认 ingested，
        所以这条永远不会把没传的东西说成传过了。文件内容一改，指纹就换，
        照样会被重新评估。

        ``cacheable=False`` 的项一律跳过（环境抖动不是文件的错，见
        ``_transient_io_error``）；返回的条数也只算真记下去的那些，别让统计虚报。
        """
        n = 0
        for f in items:
            fp = f.get("fp")
            if not fp or f.get("cacheable", True) is False:
                continue
            e = self._touch(fp, f["path"], f.get("size", 0), f.get("mtime", 0))
            e["status"] = status
            e["reason"] = (reason or f.get("reason") or "")[:200]
            e["ts"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            n += 1
        if n:
            self.save()
        return n


# ── 合格判定：轻量采样 + 「读不到就回落权威版」────────────────────────────

def _quick_tables_raw(path: str, max_rows: int) -> List[Tuple[str, list, bool]]:
    """流式取每个工作表的前 ``max_rows`` 行 → ``[(表名, 行, 是否可能没读全)]``。

    ``可能没读全`` 只在「正好读满 max_rows」时为真——读不满说明迭代器自己到了
    结尾，但只读模式的历史事故恰恰是**提前**到结尾，所以这个信号不可靠，
    调用方必须靠「什么都没读到就回落权威版」兜底，而不是信它。

    旧版 .xls（OLE 复合文档，本机实测有一份 2019 展会名单）在这里**直接返回空**：
    openpyxl 读不了它（抛 BadZipFile → 被定性成「文件读不了」＝白丢一份名单），
    而 xlrd 没有对等的流式截断读法。返回空正好触发 ``quick_preview`` 既有的
    「采样读不到就回落 ``preview_excel_mapping`` 权威版」这条路——权威版按字节头
    分派、已能读 .xls。与其在这里再写一套只读采样，不如让判定口径只有一份。
    """
    from customer_library_cleaner import (
        _cell_text, excel_kind, XLS_KIND,
    )
    ext = os.path.splitext(path)[1].lower()
    if ext == ".csv":
        import csv as _csv
        import io
        raw = Path(path).read_bytes()[: 4 * 1024 * 1024]      # 只取前 4MB
        text = None
        for enc in ("utf-8-sig", "gb18030", "utf-8", "latin-1"):
            try:
                text = raw.decode(enc)
                break
            except Exception:
                continue
        if text is None:
            raise ValueError("CSV 编码识别失败")
        rows = [[_cell_text(c) for c in r]
                for r in _csv.reader(io.StringIO(text))]
        rows = [r for r in rows if any(c for c in r)]
        return [("（CSV）", rows[:max_rows], len(rows) >= max_rows)]

    if excel_kind(path) == XLS_KIND:
        return []
    from openpyxl import load_workbook
    wb = load_workbook(path, read_only=True, data_only=True)
    out: List[Tuple[str, list, bool]] = []
    try:
        for ws in wb.worksheets:
            got = []
            for r in ws.iter_rows(values_only=True):
                got.append([_cell_text(v) for v in r])
                if len(got) >= max_rows:
                    break
            got = [r for r in got if any(c for c in r)]
            if len(got) >= 2:
                out.append((ws.title, got, len(got) >= max_rows))
    finally:
        try:
            wb.close()
        except Exception:
            pass
    return out


def _build_preview(sheet_name: str, rows: list,
                   may_be_partial: bool) -> dict:
    """把采样行喂给**同一套**列匹配函数，产出与 preview_excel_mapping 同形的项。"""
    from customer_library_cleaner import (detect_header_row, match_columns,
                                          _cell_text)
    headers, data_rows = detect_header_row(rows)
    if not any(headers):
        headers = [""] * max((len(r) for r in rows), default=0)
    while len(headers) < max((len(r) for r in rows), default=0):
        headers.append("")
    mapping, _identified, _skipped = match_columns(headers, data_rows)
    full_mapping = {i: mapping.get(i, "") for i in range(len(headers))}
    ncol = max([len(headers)] + [len(r) for r in data_rows]) if data_rows \
        else len(headers)
    samples: Dict[int, List[str]] = {i: [] for i in range(ncol)}
    for row in data_rows:
        for i in range(ncol):
            if len(samples[i]) >= 3:
                continue
            v = _cell_text(row[i]) if i < len(row) else ""
            if v:
                samples[i].append(v)
    # 表级可用性（说明表/进度表拦截）走权威链路同一个 _sheet_usability：
    # 海洋展「采集进度」配置表被当企业名单灌进数据池就是它防住的。
    # 只喂它真正会读的 company 字段，不在此复刻任何判据。
    col_company = next((i for i, f in full_mapping.items() if f == "公司名"),
                       None)
    usable, ratio = True, 1.0
    if col_company is not None:
        probe = [{"company": _cell_text(r[col_company])}
                 for r in data_rows
                 if col_company < len(r) and _cell_text(r[col_company])]
        if probe:
            usable, ratio = _sheet_usability(probe)
    return {
        "sheet": sheet_name,
        "headers": list(headers),
        "mapping": full_mapping,
        "unmapped": [i for i, f in full_mapping.items() if not f],
        "samples": samples,
        "total_rows": len(data_rows),
        # 采样读满过 = 后面还有行没看，行数只能等真读时再报，界面据此标「约」
        "rows_partial": may_be_partial,
        "usable": usable,
        "entity_ratio": round(float(ratio), 3),
        "quick": True,
    }


def quick_preview(path: str,
                  max_rows: int = QUICK_SAMPLE_ROWS) -> Tuple[List[dict], str]:
    """轻量列映射预览；返回 ``(预览, 数据来源标记)``。

    ``quick``＝采样读到了内容；``full``＝采样什么都没读到，已回落到权威版
    ``DataPoolManager.preview_excel_mapping``（整表读，慢但不会因只读模式截断
    而把好文件误判成不合格）。
    """
    previews: List[dict] = []
    try:
        for sheet_name, rows, partial in _quick_tables_raw(path, max_rows):
            previews.append(_build_preview(sheet_name, rows, partial))
    except Exception:
        previews = []
    if previews:
        return previews, "quick"
    # 采样读不出任何内容：可能是真没数据，也可能是只读模式提前终止。
    # 分不清就用权威版再看一眼——多花十几秒换一个「不误杀文件」的确定性。
    try:
        return DataPoolManager.preview_excel_mapping(path), "full"
    except Exception as e:
        # 权威版明说「没有工作表/没有数据行」= 这就是个空表，不是读不动。
        # 必须翻成空预览交给 judge_quality 定性成 NO_SHEET：实测缺陷是只有表头
        # 的表格曾被标成「读不了」，用户会以为文件坏了，其实内容就是空的。
        # 「读不了」和「没有数据」是两件事，混在一起就等于误导。
        if _EMPTY_SHEET_RE.search(str(e)):
            return [], "full"
        raise


def _sheet_fields(pv: dict) -> Tuple[set, set]:
    """一张表的映射拆成（认出的字段集合, **有实际值**的字段集合）。

    只看列名会高估：说明文档里「邮箱」列可能整列空白。用 samples 判断有无值
    ——preview 的 samples 每列存前 3 个非空值，全空列必然是空列表，quick 与
    full 两条来源在这一项上形状相同，不必各写一套。
    """
    mapping = pv.get("mapping") or {}
    samples = pv.get("samples") or {}
    got = {f for f in mapping.values() if f}
    filled = {mapping[i] for i, f in mapping.items()
              if f and (samples.get(i) or [])}
    return got, filled


def judge_quality(previews: List[dict]) -> Tuple[bool, str, int, List[str]]:
    """按列映射判断文件值不值得入池 → ``(合格?, 原因, 采样行数, 命中字段)``。

    判据与上传门禁 ``data_pool._is_valid_record`` 同口径，且**按表**判：
    必须有一张表同时认得出「公司名」列和「电话/邮箱/官网」中的任一种（微信、
    地址不算独立证据），并且这两类列在采样行里真的有值。

    历史缺陷（2026-11-11 实测抓出）：按文件取并集时，接口文档的 Overview 表
    有公司名、另一张 check_email 表有邮箱，并集判定「合格」；而
    ``read_records_from_excel`` 是逐表转记录的——Overview 那 20 行永远凑不出
    联系方式，454 行整份被剔成「无联系方式」，实际入库 0 条。界面就会显示
    「预估 454 条 → 上传时报无可上传数据」。表级配套才是权威链路的真实形状。
    """
    if not previews:
        return False, NO_SHEET, 0, []
    fields: List[str] = []
    rows = 0
    paired_rows = 0
    for pv in previews:
        got, filled = _sheet_fields(pv)
        fields.extend(f for f in sorted(got) if f not in fields)
        n = int(pv.get("total_rows") or 0)
        rows += n
        if "公司名" in filled and any(f in filled for f in GATE_CONTACT_FIELDS):
            paired_rows += n
    if "公司名" not in fields:
        return (False, NO_COMPANY, rows, fields)
    if not any(f in fields for f in GATE_CONTACT_FIELDS):
        return (False, NO_CONTACT, rows, fields)
    if rows <= 0:
        return False, NO_ROWS, rows, fields
    # 表级门禁与权威链路同一判据：**所有**工作表都被判成说明表才算这个文件没用
    # （多表文件里只要有一张是企业名单就该入库，与 read_records_from_excel
    # 的「usable 的表留、不可用的表记 skipped_sheets」完全一致）
    checked = [pv for pv in previews if "usable" in pv]
    if checked and not any(pv.get("usable") for pv in checked):
        worst = min((float(pv.get("entity_ratio") or 0) for pv in checked),
                    default=0.0)
        return (False, f"{NO_ENTITY}：最高占比只有 {worst:.2f}", rows, fields)
    if paired_rows <= 0:
        # 字段都认到了，但没有一张表能把公司名和联系方式配成同一行
        return (False, f"{NO_CONTACT}：没有任何一张表能同时给出公司名与联系方式",
                rows, fields)
    return True, "", rows, fields


def needs_full_confirm(reason: str) -> bool:
    """这个「不合格」结论是不是**取决于解析深度**，必须整表复核才能说死。

    实测依据（2026-11-11，下载目录 25 个文件逐个比对）：只读前 200 行的采样
    **从不**多认字段，但会**少认**——``光伏储能专题总表_副本`` 整表多认出「国家」
    列、``b2b168_companies`` 整表多认出「邮箱」列。少认可选列无所谓（真读时按整表
    识别，数据不丢），但少认到「公司名/联系方式/实体占比」就会把好文件判成不合格、
    静默漏传，那是最坏的一种错误。所以凡结论取决于解析深度的，一律拿权威整表链路
    复核一次（``read_records_from_excel`` 能读出记录就是合格）。
    「没有数据行/空文件」这类采样与整表看到的是同一件事，不必复核。
    """
    return reason.startswith((NO_COMPANY, NO_CONTACT, NO_ENTITY))


def confirm_by_full_read(path: str) -> Tuple[bool, str, int]:
    """用权威整表链路给「不合格」结论一次翻案机会 → ``(合格?, 原因, 可入库条数)``。

    判据就是真上传时真正会走的那条路：读得出记录 = 能入库；读不出 = 确实没用。
    只在采样判不合格的文件上调用（本机 293 个里 29 个），成本可控。
    """
    rep: dict = {}
    try:
        recs = DataPoolManager.read_records_from_excel(path, report_out=rep)
    except Exception as e:
        return False, _friendly_read_error(e), 0
    if recs:
        return True, "", int(rep.get("kept") or len(recs))
    sk = rep.get("skipped_sheets") or []
    if sk:
        ratio = max((float(s.get("entity_ratio") or 0) for s in sk),
                    default=0.0)
        return False, f"{NO_ENTITY}：最高占比只有 {ratio:.2f}", 0
    dr = rep.get("drop_reasons") or {}
    if dr:
        top = sorted(dr.items(), key=lambda kv: -kv[1])[0][0]
        return False, f"整表按入库门禁一条都留不下（最多剔除原因：{top}）", 0
    return False, NO_ROWS, 0


# ── 池内比对：只如实提示，不据此自动取消勾选 ───────────────────────────────

def _contact_tokens(rec: dict) -> set:
    """把一条记录的可检索联系方式压成集合（比对用，不改任何数据）。

    号码只留数字末 11 位：池里历史数据把 ``+34 688 841 394`` 存成了
    ``['+34','688','841','394','+34 688 841 394']`` 这种碎片形态，按原样比对会把
    「其实已有」判成「新增」，界面就会满屏假警报。
    """
    out: set = set()
    for key in ("phones", "mobiles", "landlines", "emails", "wechats"):
        v = rec.get(key)
        vals = v if isinstance(v, list) else ([v] if v else [])
        for s in vals:
            t = str(s or "").strip()
            if not t:
                continue
            if key == "emails":
                out.add("e:" + t.lower())
            else:
                digits = re.sub(r"\D", "", t)
                if len(digits) >= 5:
                    out.add("n:" + digits[-11:])
    site = str(rec.get("website") or "").strip()
    if site:
        dom = re.sub(r"^(https?://)?(www\.)", "", site).split("/")[0].lower()
        if dom:
            out.add("w:" + dom)
    return out


class PoolIndex:
    """本地物化视图的 ``company_norm → 联系方式集合`` 索引。

    只从内存总表构建，**不发起任何 OSS 请求**（守住「有缓存时秒开」那条不变量）。
    总表拿不到就整体降级为「不比对」——比对不了就照常勾选，绝不因为索引缺失
    而漏传文件。
    """

    def __init__(self, mgr: Optional[DataPoolManager] = None):
        self.norm_contacts: Dict[str, set] = {}
        self.ready = False
        self.size = 0
        self.error = ""
        try:
            mgr = mgr or DataPoolManager()
            master = mgr.get_merged_master() or []
            for r in master:
                norm = r.get("company_norm") or normalize_company_name(
                    r.get("company") or "")
                if not norm:
                    continue
                self.norm_contacts.setdefault(norm, set()).update(
                    _contact_tokens(r))
            self.size = len(self.norm_contacts)
            self.ready = self.size > 0
        except Exception as e:
            self.error = str(e)[:160]

    def sample_diff(self, previews: List[dict]) -> Tuple[int, int]:
        """看采样行里的公司名有多少池里还没有 → ``(采样公司数, 池里没有的家数)``。

        只作提示用：采样看不到的行可能有新增，所以**不据这个数自动取消勾选**。
        """
        if not self.ready:
            return 0, 0
        seen = 0
        missing = 0
        for pv in previews or []:
            col = next((i for i, f in (pv.get("mapping") or {}).items()
                        if f == "公司名"), None)
            if col is None:
                continue
            for name in (pv.get("samples") or {}).get(col) or []:
                norm = normalize_company_name(str(name))
                if not norm:
                    continue
                seen += 1
                if norm not in self.norm_contacts:
                    missing += 1
        return seen, missing


# ── 同族快照归组 ──────────────────────────────────────────────────────────

# 工具自己导出的阶段后缀：同一会话目录里「原始 / 清洗后入库 / 国内只留手机号」
# 是同一批数据的三个加工阶段，不是三份不同数据（都传等于传三遍）
STAGE_SUFFIXES = ("_原始", "_清洗后入库数据", "_清洗后数据", "_国内只留手机号")
# 「副本」类后缀：另存一份本身就是同一份数据，不剥掉就会各算一个同族、各传一遍
COPY_SUFFIX_RE = re.compile(r"[\(（]?[ _]?(?:副本|副本\d*|copy|copy\d*|的副本)[\)）]?$",
                            re.IGNORECASE)
# 时间戳后缀：年月日必段 + **可选**的时间段。时间段必须放到 1~6 位，
# 实测缺陷（2026-11-11）：只认 6 位时，「CDSCA展商名单_补完_20261111_1032.xlsx」
# 这种 4 位（HHMM）命名的同一系列快照归不到一族，两份都被默认勾上 → 同一批数据
# 传两遍；反过来把位数放宽到「任意数字」又会把「海洋装备46」这类编号吃掉，
# 所以时间段只允许出现在**完整日期之后**。
_TS_RE = re.compile(r"[_\-\s](?:19|20)\d{2}[-_ ]?\d{2}[-_ ]?\d{2}"
                    r"(?:[-_ ]?\d{1,6})?(?=\.\w+$|$)")


def family_key(path: str) -> str:
    """同族键 = 目录 + 去掉时间戳、阶段后缀与「副本」字样的文件名（小写）。

    只在**同一目录**内归组：跨目录同名是不同任务的可能性很大，合并代表会误伤。
    """
    base = os.path.basename(path)
    stem, _ext = os.path.splitext(base)
    stem = _TS_RE.sub("", stem)
    changed = True
    while changed:
        changed = False
        for s in STAGE_SUFFIXES:
            if stem.endswith(s):
                stem = stem[:-len(s)]
                changed = True
        new = COPY_SUFFIX_RE.sub("", stem)
        if new != stem:
            stem, changed = new, True
    stem = stem.strip("_- （）() ")
    return f"{os.path.dirname(path)}|{stem.lower()}"


def pick_family_representatives(items: List[dict]) -> Dict[str, str]:
    """每个同族选一个代表 → ``{同族键 → 代表路径}``。

    取 mtime 最新（时间戳快照序列的语义就是「越新越全」），并列取体积更大的。
    """
    best: Dict[str, Tuple[int, int, str]] = {}
    for it in items:
        k = it["family"]
        rank = (int(it.get("mtime") or 0), int(it.get("size") or 0))
        cur = best.get(k)
        if cur is None or rank > (cur[0], cur[1]):
            best[k] = (rank[0], rank[1], it["path"])
    return {k: v[2] for k, v in best.items()}


# ── 编排：一次扫描的全部判定 ─────────────────────────────────────────────

def scan_all(roots: Optional[Iterable[str]] = None,
             ledger: Optional[Ledger] = None,
             pool_index: Optional[PoolIndex] = None,
             should_stop: Optional[Callable[[], bool]] = None,
             on_progress: Optional[Callable[[str, int, int], None]] = None,
             skip_unreadable_ledger: bool = True,
             ) -> Tuple[List[dict], dict]:
    """扫描 → 展开压缩包 → 指纹 → 合格判定 → 同族归组 → 池内比对，产出逐项清单。

    ``on_progress(文案, 已完成, 总数)``。返回 ``([扫描项], 统计)``；扫描项字段：
    ``path/display/dir/size/mtime/fp/fp_cached/status/reason/est_rows/fields/
    pool_seen/pool_missing/checked/family/dup_of``；压缩包成员另有
    ``logical``（``包名/包内路径``，界面展示与同族归组都用它）和 ``origin``（包
    的真实位置），此时 ``path`` 指向按内容寻址的缓存文件（上传链路直接读它）。

    三重去重在这一层各管一段，互不重叠：台账指纹（跨次扫描，含**本次清单内部**
    同内容副本 → ``dup_content``）、同族快照（同目录同系列多个版本）、
    池内比对（只作提示，不改勾选）。

    顺序是刻意排的：**先靠台账把传过的整批挡掉**（零成本），再花 IO 去解析剩下的。
    实测本机 295 个文件里 200 多个是历史快照，第一遍之后重复内容全部命中台账，
    第二遍扫描的解析量接近零。
    """
    ledger = ledger or Ledger().load()
    t0 = time.time()
    files, archives, stats = iter_tables(
        roots, should_stop=should_stop,
        on_progress=(lambda msg, n: on_progress(msg, n, 0) if on_progress else None)
        if on_progress else None)
    # 压缩包成员先展开成「可判定、可上传的真文件」。顺序：散文件在前、包内成员在后
    # ——同一份内容如果散着也放了一份，让散的那份当代表（用户看得见、能自己核对的
    # 原件，优先于工具自己解出来的缓存副本）。
    def _mk(f: dict, fp: str, status: str, reason: str) -> dict:
        return _mk_item(f["path"], f["size"], f["mtime"], fp, status, reason,
                        logical=f.get("logical", ""), origin=f.get("origin", ""))

    arch_notes: List[Tuple[str, str]] = []
    if archives and not (should_stop and should_stop()):
        members, arch_notes, arch_stats = expand_archives(
            archives, cache_dir=archive_cache_dir(), should_stop=should_stop,
            on_progress=(lambda msg, n: on_progress(msg, 0, 0)
                         if on_progress else None) if on_progress else None)
        files = files + members
        stats["archive"] = arch_stats
        stats["archive_members"] = len(members)
        stats["archive_notes"] = arch_notes
    else:
        stats["archive"] = {"archives": len(archives), "expanded": 0,
                            "members": 0, "empty": 0, "failed": 0, "nested": 0}
        stats["archive_members"] = 0
        stats["archive_notes"] = arch_notes
    items: List[dict] = []
    # 指纹 → 本清单里该内容的头一份（代表）。字节完全相同 ⇒ 解析结果必然相同，
    # 所以副本既不用重复解析（省 IO），也不该和代表一起上传（省一次重复内容）。
    fp_items: Dict[str, dict] = {}
    for f in files:
        if should_stop and should_stop():
            stats["truncated"] = "已取消"
            break
        path, size, mtime = f["path"], f["size"], f["mtime"]
        # 压缩包成员的指纹在展开时就已算好（内容寻址落盘的哈希就是它），不必
        # 再把缓存文件读一遍去求同一个值
        try:
            fp, cached = ((f["fp"], False) if f.get("fp")
                          else ledger.fp_for(path, size, mtime))
        except Exception as e:
            items.append(_mk(f, "", ST_UNREADABLE, f"算指纹失败：{str(e)[:80]}"))
            continue
        if ledger.ingested(fp):
            e = ledger.entry(fp) or {}
            it = _mk(f, fp, ST_INGESTED,
                     f"这份内容已在 {e.get('ts', '以前')} 传过"
                     f"（{e.get('rows_kept', '?')} 条）")
            items.append(it)
            fp_items.setdefault(fp, it)
            continue
        if skip_unreadable_ledger:
            e = ledger.entry(fp) or {}
            if e.get("status") == ST_UNREADABLE:
                it = _mk(f, fp, ST_UNREADABLE, e.get("reason") or "上次就读不了")
                items.append(it)
                fp_items.setdefault(fp, it)
                continue
            if e.get("status") == ST_INVALID:
                # 不合格也留了痕：直接沿用结论，不再花 IO 重读大文件
                it = _mk(f, fp, ST_INVALID, e.get("reason") or "不符合入库条件")
                items.append(it)
                fp_items.setdefault(fp, it)
                continue
        ref = fp_items.get(fp)
        if ref is not None:
            # 同一份内容在本次清单里已经有一份了：结论照抄，不再读第二遍
            it = _mk(f, fp, ref["status"], ref["reason"])
            it["est_rows"] = ref.get("est_rows", 0)
            it["fields"] = list(ref.get("fields") or [])
            it["preview_source"] = ref.get("preview_source", "quick")
            it["dup_of"] = ref["path"]
            items.append(it)
            continue
        try:
            previews, src = quick_preview(path)
        except Exception as e:
            it = _mk(f, fp, ST_UNREADABLE, _friendly_read_error(e))
            if _transient_io_error(e):
                # 环境抖动（没权限/文件被移走/句柄吃紧）：如实说「这次读不了」，
                # 并且**不写台账**——写进去就把一次抖动固化成了永久罪名。
                it["cacheable"] = False
                it["reason"] = it["reason"] + "（本次环境故障，未记账，下次扫描重判）"
            items.append(it)
            fp_items.setdefault(fp, it)
            continue
        ok, reason, rows, fields = judge_quality(previews)
        status = ST_NEW if ok else ST_INVALID
        if not ok and src == "quick" and needs_full_confirm(reason):
            # 采样只看了前 200 行，少认一列就可能把好文件判成不合格（=静默漏传）。
            # 这类结论一律拿权威整表链路翻案一次；复核本身读不动就沿用采样结论，
            # 绝不因为复核失败把一个能读的文件升级成「读不了」。
            # 大文件复核一次要几十秒，不硬来：标成「待复核」并**照勾**，
            # 让真上传的那一步去定夺——宁可多问一句，不静默丢一个文件。
            if size > FULL_CONFIRM_MAX_BYTES:
                status = ST_UNCONFIRMED
                reason = (reason + "（文件超过 "
                          f"{FULL_CONFIRM_MAX_BYTES // 1048576}MB，"
                          "扫描没做整表复核，已按待复核先勾上）")
            else:
                ok2, reason2, rows2 = confirm_by_full_read(path)
                src = "full"
                if ok2:
                    status, reason = ST_NEW, ""
                    rows = rows2 or rows
                else:
                    reason = reason2
        it = _mk(f, fp, status, reason)
        fp_items[fp] = it
        it["est_rows"] = rows
        it["fields"] = fields
        it["rows_partial"] = any(p.get("rows_partial") for p in previews or [])
        it["preview_source"] = src
        if status == ST_NEW and src == "full" and not ok:
            # 采样判不合格、整表复核翻案成功：字段列表是采样阶段的结果，可能不全，
            # 如实标注，别让界面把「采样没认出来」显示成「这文件没有这些列」
            it["note"] = "整表复核后确认可入库（采样阶段列识别没看全）"
        if status == ST_NEW and pool_index is not None:
            it["pool_seen"], it["pool_missing"] = pool_index.sample_diff(
                previews)
        items.append(it)
        if on_progress:
            # 用展示名而不是 path：包内成员的 path 是内容寻址的缓存文件名（一串哈希），
            # 进度条上刷哈希等于什么都没说
            on_progress(f"正在识别 {it['display'][:30]}…", len(items), len(files))

    # 同族归组：在「新且合格」与「待复核」之间选代表，已传过的不参与（本来就不勾）。
    # 待复核排在合格之后：同系列里有一份已经确认能用，就绝不再拿一份没复核的去传。
    # 批内副本不参与选代表：它们的信息量与代表完全相同，掺进来会把真代表挤掉
    # （两份同内容互相挤，结果一份都不勾 = 静默丢数据）。
    cands = [i for i in items
             if i["status"] in ELIGIBLE_STATUSES and not i.get("dup_of")]
    reps = pick_family_representatives(
        [i for i in cands if i["status"] == ST_NEW] or cands)
    for i in cands:
        if reps.get(i["family"]) != i["path"]:
            i["status"] = ST_FAMILY
            i["reason"] = "同目录同系列里已选了更新的一份做代表"
    # 副本的最终结论跟在代表上：代表被勾 → 副本标「同内容副本」不勾；
    # 代表因为别的原因不勾（同系列更新的一份、不合格、读不了）→ 副本沿用同一结论，
    # 绝不允许出现「代表没勾、副本却勾着」把同一批数据换个路径再传一遍。
    for i in items:
        ref = i.get("dup_of")
        if not ref:
            continue
        rep = fp_items.get(i["fp"]) or {}
        if rep.get("status") in ELIGIBLE_STATUSES:
            i["status"] = ST_DUP
            i["reason"] = (f"与「{rep.get('display') or os.path.basename(ref)}」"
                           "字节完全相同（同一份内容只传一次，勾那一份就行）")
        else:
            i["status"] = rep.get("status", i["status"])
            i["reason"] = rep.get("reason", i["reason"])
    for i in items:
        i["checked"] = i["status"] in ELIGIBLE_STATUSES

    # 不合格与读不了的结论写进台账（不写 ingested），下次扫描不再重复解析
    ledger.mark_skipped([i for i in items if i["status"] == ST_INVALID],
                        ST_INVALID)
    ledger.mark_skipped([i for i in items if i["status"] == ST_UNREADABLE],
                        ST_UNREADABLE)
    # 展开缓存压回上限。本次清单还在引用的那些文件绝不删（删了用户点上传就读不到）
    prune_archive_cache(keep=[i["path"] for i in items if i.get("logical")])

    stats.update({
        "found": len(files),
        "found_loose": len(files) - stats["archive_members"],
        "items": len(items),
        "by_status": _count_status(items),
        "seconds": round(time.time() - t0, 1),
        "ledger_entries": len(ledger.entries),
        # 能力版本升级后作废的旧否定结论数：界面要能回答「上次说读不了的文件去哪了」
        "negatives_reset": getattr(ledger, "reset_negatives", 0),
        "pool_ready": bool(pool_index and pool_index.ready),
        "pool_size": pool_index.size if pool_index else 0,
    })
    return items, stats


def _count_status(items: List[dict]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for i in items:
        out[i["status"]] = out.get(i["status"], 0) + 1
    return out


def _mk_item(path: str, size: int, mtime: int, fp: str,
             status: str, reason: str, logical: str = "",
             origin: str = "") -> dict:
    """一条扫描结论。

    ``logical``/``origin`` 只给压缩包成员用：成员的 ``path`` 是按内容寻址的缓存文件
    （``archive_cache/ab/<sha256>.xlsx``），拿它当展示名用户只会看到一串哈希。展示、
    同族归组、tooltip 一律用逻辑路径（``包名/包内目录/表名.xlsx``）与包的真实位置，
    ``path`` 保持「可直接打开读写的真身」——上传链路吃的就是它。
    """
    shown = logical or path
    # 包内成员的「所在目录」要能真的被找到：拿包在磁盘上的真实目录去接逻辑路径的
    # 剩余部分（``/Users/x/包/外层.zip/内层.zip``），这样这一列读起来是一条完整可
    # 回溯的位置，而不是「外层.zip/内层.zip」这种让人不知道去哪儿开的孤儿字符串。
    shown_dir = os.path.dirname(shown) or "."
    if logical and origin:
        shown_dir = os.path.join(os.path.dirname(origin),
                                 os.path.dirname(logical)) or "."
    return {
        "path": path,
        "display": os.path.basename(shown),
        "dir": shown_dir,
        "logical": logical,
        "origin": origin,
        "size": size,
        "mtime": mtime,
        "fp": fp,
        "status": status,
        "reason": reason,
        "est_rows": 0,
        "fields": [],
        "pool_seen": 0,
        "pool_missing": 0,
        "checked": status in ELIGIBLE_STATUSES,
        "family": family_key(shown),
        "dup_of": "",           # 批内同内容副本指向的代表路径（空=不是副本）
        "rows_partial": False,
        "preview_source": "quick",
        "note": "",
        # False = 这次失败是环境抖动，结论不许写进台账（见 _transient_io_error）
        "cacheable": True,
    }


def _friendly_read_error(e: Exception) -> str:
    """把底层异常翻成用户能看懂的一句话（原文照抄只会让人以为是自己弄坏了文件）。"""
    s = str(e)
    low = s.lower()
    if "xlrd" in low:
        # .xls 现在能读，唯一还可能失败的是打包环境缺 xlrd——给的是用户能自助的那条路
        return "环境缺 xlrd 模块，读不了旧版 .xls（请用 Excel/WPS 另存为 .xlsx 再传）"
    if "encrypted" in low or "password" in low or "加密" in s:
        return "文件被加密/设了打开密码，读不了"
    if "zip" in low or "archive" in low or "not a zip" in low:
        return "文件损坏或不是真正的 Excel（扩展名与实际格式不符）"
    if "permission" in low or "denied" in low or "权限" in s:
        return "没有读取权限（系统隐私设置里未授权该目录）"
    return "读不了：" + (s.splitlines()[0][:100] if s else e.__class__.__name__)


# 环境层面的故障码：这些错跟文件内容无关，是「这一刻的机器」不给读。
_TRANSIENT_ERRNOS = {
    getattr(errno, name) for name in
    ("EMFILE", "ENFILE", "ENOMEM", "EAGAIN", "EIO", "EBUSY")
    if hasattr(errno, name)
}


def _transient_io_error(e: Exception) -> bool:
    """这个失败是「这一刻的环境」造成的、还是文件本身就这样？

    只有后者才允许写进台账当长期结论。区别在于可复现性：一个设了打开密码的 xlsx
    明天还是打不开，记成「读不了」省下一次解析；而句柄耗尽（EMFILE）、目录被临时
    占用、扫到一半文件被移走这类，下一次扫描结论就变了——把它缓存起来等于把一次
    抖动固化成永久罪名，而且界面会拿着旧罪名让用户以为文件坏了。
    """
    if isinstance(e, (PermissionError, FileNotFoundError, IsADirectoryError,
                      InterruptedError, BlockingIOError, TimeoutError)):
        return True
    return isinstance(e, OSError) and getattr(e, "errno", None) in _TRANSIENT_ERRNOS


def human_size(n: int) -> str:
    """字节数转人话（界面与日志共用，别处不再各写一份 1024 换算）。"""
    step = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if step < 1024 or unit == "GB":
            return f"{step:.0f}{unit}" if unit == "B" else f"{step:.1f}{unit}"
        step /= 1024.0
    return f"{n}B"


def summary_line(stats: dict) -> str:
    """一行汇总（对话框底部、日志和测试断言都读这一句，保证口径一致）。"""
    by = stats.get("by_status") or {}
    parts = [f"发现 {stats.get('found', 0)} 个表格",
             f"待入库 {by.get(ST_NEW, 0)}",
             f"传过 {by.get(ST_INGESTED, 0)}",
             f"同系列旧版 {by.get(ST_FAMILY, 0)}",
             f"同内容副本 {by.get(ST_DUP, 0)}",
             f"不合格 {by.get(ST_INVALID, 0)}",
             f"读不了 {by.get(ST_UNREADABLE, 0)}"]
    if by.get(ST_UNCONFIRMED):
        parts.append(f"待复核 {by[ST_UNCONFIRMED]}")
    # 压缩包这一路必须单独说出来：用户问「压缩包里那些 Excel 扫不扫」时，
    # 界面要能拿数字回答，而不是让人猜工具到底进没进去
    arch = stats.get("archive") or {}
    if arch.get("archives"):
        parts.append(f"压缩包 {arch['archives']} 个（取出表格 "
                     f"{stats.get('archive_members', 0)} 个）")
    notes = stats.get("archive_notes") or []
    if notes:
        parts.append(f"⚠ {len(notes)} 个压缩包/成员没有全部取出"
                     "（清单下方按原因合并列出，点「查看明细」逐条核对）")
    if stats.get("archives_unsupported"):
        parts.append(f"⚠ {stats['archives_unsupported']} 个 rar/7z 暂不支持解压")
    trunc = stats.get("truncated") or ""
    if trunc:
        parts.append(f"⚠ {trunc}")
    # 池内比对没做成必须明说：否则用户看到「待入库 N」会以为 N 里全是池里没有的
    note = stats.get("pool_note") or ""
    if note:
        parts.append(f"ℹ {note}")
    return "｜".join(parts) + f"｜用时 {stats.get('seconds', 0)} 秒"


def accepted_types_text() -> str:
    """界面文案里的「支持哪些后缀」从白名单变量拼出来，绝不手写第二份清单。

    手写的那份会在白名单变化时悄悄说谎（用户看到「只认 .xlsx/.xlsm/.csv」，
    实际 .xls 也照样扫得到）——判据只允许有一份，展示只是它的投影。
    """
    return (" / ".join(IMPORT_EXCEL_EXTS)
            + "，以及压缩包（" + " / ".join(IMPORT_ARCHIVE_EXTS)
            + "）里面的这些文件")


def _note_family(reason: str) -> str:
    """原因归族：把数字抽掉后同形的算同一件事（「包里 3 个成员…」＝「包里 12 个…」）。"""
    return _NOTE_NUM_RE.sub("#", reason)


def _note_label(reason: str) -> str:
    """归族后的**族标题**：把具体数字换成「若干」，不拿某一条的数字替整组说话。"""
    return _NOTE_LABEL_RE.sub("若干", reason).strip()


def archive_notes_grouped(notes: List[Tuple[str, str]]
                          ) -> List[Tuple[str, List[Tuple[str, str]]]]:
    """把「哪些包没取全、为什么」按**原因**归族 → ``[(族标题, [(包名, 原文)...])]``。

    为什么必须归族：本机实测 17 条记账里 15 条是同一句「压缩包里没有找到能导入的
    Excel/CSV 文件」——那是自家 dist 包、chromedriver.zip、IPSW 镜像的正常现象，不是
    故障。把它们和「包损坏」「加密取不出来」「路径不安全」平铺在同一个列表里，真正的
    问题会被重复的噪音淹没；告警淹在噪音里等于没有告警。

    族标题只在**整族原文完全相同**时才直接用那句话；同族但句子带不同数字时标题说
    「若干」，具体数字留给逐条明细——合并是为了好读，不许为了好读替用户改数字。
    排序把**异常原因放前面、正常现象放最后**（同类按影响包数从多到少），所以用户
    先看到的一定是「需要他动手的事」。
    """
    groups: Dict[str, List[Tuple[str, str]]] = {}
    order: List[str] = []
    for name, reason in notes:
        key = _note_family(reason)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append((name, reason))
    benign = {_note_family(r) for r in BENIGN_ARCHIVE_NOTES}
    keys = sorted(order, key=lambda k: (0 if k not in benign else 1,
                                        -len(groups[k])))
    out: List[Tuple[str, List[Tuple[str, str]]]] = []
    for k in keys:
        entries = groups[k]
        uniq = {r for _n, r in entries}
        out.append((entries[0][1] if len(uniq) == 1
                    else _note_label(entries[0][1]), entries))
    return out


def archive_notes_text(notes: List[Tuple[str, str]], limit: int = 4) -> str:
    """把「哪些包没取全、为什么」压成一句人话（界面与日志共用一份格式）。

    同一句话在多个包上重复时合并成「N 个包：原因（如 a.zip、b.zip）」；同族但各自
    数字不同的，宁可逐条点名的数字也不合并——那是给用户核对用的，不是排版用的。
    """
    if not notes:
        return ""
    lines: List[str] = []
    for title, entries in archive_notes_grouped(notes):
        texts = {r for _n, r in entries}
        names = [n for n, _r in entries]
        if len(texts) > 1:                      # 数字不同：一条一个数，不许合并
            lines.extend(f"{n}：{r}" for n, r in entries)
        elif len(names) >= 3:
            eg = "、".join(os.path.basename(n) for n in names[:2])
            lines.append(f"{len(names)} 个包：{title}（如 {eg}）")
        elif len(names) == 2:
            lines.append(f"{names[0]}、{names[1]}：{title}")
        else:
            lines.append(f"{names[0]}：{title}")
    head = "；".join(lines[:limit])
    more = f"；……另有 {len(lines) - limit} 类情况" if len(lines) > limit else ""
    return (f"压缩包没有全部取出的情况（共 {len(notes)} 条，"
            "逐条明细点右侧「查看明细」）：" + head + more)


def archive_notes_detail(notes: List[Tuple[str, str]],
                         per_reason: int = 0) -> str:
    """逐条明细（明细窗口用）：按原因分组，把每个组里的包名与原文都列出来。

    默认**一条不截断**：这个窗口存在的唯一理由就是「用户要核对到底少了哪些」，拿上限
    把它切短等于把承诺又收回一半。真要限量由调用方显式传 ``per_reason``，而且截断时
    只说「未列出」，不许指向一个并不存在的日志。
    """
    if not notes:
        return ""
    out = ["每一条都是「这个包里确实有东西我没拿出来」，按原因归类："]
    for title, entries in archive_notes_grouped(notes):
        out.append(f"· {title}（{len(entries)} 个）")
        many = len({r for _n, r in entries}) > 1
        shown = entries if not per_reason else entries[:per_reason]
        for n, r in shown:
            out.append(f"    {n}：{r}" if many else f"    {n}")
        if len(entries) > len(shown):
            out.append(f"    ……另有 {len(entries) - len(shown)} 个未列出")
    return "\n".join(out)
