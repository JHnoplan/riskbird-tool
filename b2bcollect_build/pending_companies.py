# -*- coding: utf-8 -*-
"""待采集公司名单：企知道补采通道的中转站（b2bcollect 六通道体系的第六路）。

两个入口：
  ① 任务开始时用户主动上传的公司名单文件（seed，识别出的公司名全部入列）；
  ② 关键词采集被清洗口径剔掉的「废数据」（手机号/固话/邮箱/微信号四项全空）——
     这些公司名白白丢掉太可惜，交给名单通道按名补联系方式，变废为宝。
一个出口：名单通道逐家 pop 公司名补采（名单通道，不参与关键词并行）。

**一份名单、多个平台各自从头采**（企知道 / 风鸟 / 爱企查三通道并行）：
  master（名单主体，用户可见可改的单一 xlsx）与 channel（每个平台的采集进度）是两回事。
  用户名单 100 家 ⇒ 三个平台各自都要从第 1 家跑到第 100 家，谁也不能因为「别的平台
  跑过了」就跳过——三家各有独家号码，正是要靠重复覆盖把联系方式并全（采完在清洗层
  按公司名合并、手机号/邮箱取并集）。所以 tried/collected/cursor 全部**按平台分账**，
  而待采集公司名单.xlsx 始终只有一份。

每个平台三个台账（防死循环、防重复采，这是本模块存在的全部理由）：
  master      全部出现过的公司名（seed ∪ 废数据回流），顺序即各平台的采集顺序；
  tried       该平台已经送查过的公司——**无论查没查到都不再入列**。否则「查到但仍无
              联系方式 → 又被清洗成废数据 → 再入列」会无限循环烧请求；
  collected   该平台已采到联系方式的公司；
  cursor      该平台在 master 上的游标（单调前进）。每平台各一条，所以「从头开始采」
              与「续采接续」是同一套语义：游标停在哪儿就从哪儿接着走。

并发：采集线程（写废数据）/ 三个平台线程（各自 pop）/ 保存线程（flush）共用，全锁保护；
落盘一律 tmp+os.replace 原子替换（进程被杀/磁盘满不会把上一份好名单写成半个坏文件）。
台账落盘为 v2 按平台分段格式，旧版单通道 v1 台账读入时自动迁移给企知道（不丢进度）。
"""
import json
import os
import threading
import time
from collections import deque
from pathlib import Path

LIST_FILENAME = "待采集公司名单.xlsx"
LEDGER_FILENAME = "pending_ledger.json"
LEDGER_VERSION = 2

# 名单通道平台：企知道（默认）/ 风鸟 / 爱企查。三平台共享同一份 master 名单，
# 各自按自己的 tried/collected/cursor 从头走完整份名单。
DEFAULT_CHANNEL = "qzd"
CHANNEL_LABELS = {"qzd": "企知道", "riskbird": "风鸟", "aiqicha": "爱企查"}


def channel_label(slug: str) -> str:
    return CHANNEL_LABELS.get(str(slug or ""), str(slug or ""))


def extract_company_names(path, log=print):
    """从用户选择的名单文件里识别公司名（xlsx/xls/csv/txt，格式按字节头判）。
    列定位三级判据（对齐企知道工具的选列思路，但不弹窗——后台通道要的是稳不是交互）：
    ① 表头含「公司/企业/单位名称」语义直接命中；② 否则按列内容抽样打分取最高；
    ③ 单列文件（txt/单列 csv）整列当公司名。识别后去重保序、剔非公司串。"""
    import customer_library_cleaner as _cleaner
    p = Path(path)
    ext = p.suffix.lower()
    grid = []
    # 分派看字节头不看后缀：本机实测有「内容是旧版 OLE、后缀写 .xlsx」的展会名单，
    # 按后缀走 openpyxl 会抛 BadZipFile，按 txt 兜底更会把二进制乱码当公司名
    kind = _cleaner.excel_kind(str(p))
    if kind == _cleaner.XLS_KIND:
        tb = _cleaner.read_xls_tables(str(p))
        if tb:
            grid = [list(r) for r in tb[0][1]]
    elif kind == _cleaner.XLSX_KIND:
        from openpyxl import load_workbook
        wb = load_workbook(str(p), data_only=True)   # 不用 read_only：实测会静默截行
        ws = wb.active
        for row in ws.iter_rows(values_only=True):
            grid.append(["" if c is None else str(c) for c in row])
        wb.close()
    elif ext == ".csv":
        import csv
        raw = p.read_bytes()
        text = None
        for enc in ("utf-8-sig", "gbk", "utf-8"):
            try:
                text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            text = raw.decode("utf-8", errors="ignore")
        for row in csv.reader(text.splitlines()):
            grid.append(list(row))
    elif ext in (".xlsx", ".xlsm", ".xls"):
        # 后缀写着 Excel、字节头两头都不像：文件坏了或被人存成 HTML/纯文本。
        # 绝不能掉进下面的 txt 分支把二进制乱码当公司名读一遍
        raise ValueError(f"「{p.name}」不是真正的 Excel（扩展名与实际格式不符），"
                         f"请用 Excel/WPS 打开后另存为 .xlsx 再选")
    else:  # txt 等：一行一个公司名
        for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
            grid.append([line])

    if not grid:
        return []
    width = max(len(r) for r in grid)
    grid = [r + [""] * (width - len(r)) for r in grid]

    def col_score(ci, header=""):
        vals = [r[ci].strip() for r in grid[1:] if r[ci].strip()] if header else \
               [r[ci].strip() for r in grid if r[ci].strip()]
        if not vals:
            return 0.0
        sample = vals[:60]
        hit = sum(1 for v in sample if _cleaner._is_company(v))
        return hit / len(sample)

    header = grid[0]
    best_ci, best, best_sem = -1, 0.0, False
    for ci in range(width):
        h = header[ci].strip() if ci < len(header) else ""
        sem = 0.45 if any(k in h for k in ("公司", "企业", "单位名称", "商户")) else 0.0
        score = sem + 0.55 * col_score(ci, header=h)
        if score > best:
            best, best_ci, best_sem = score, ci, bool(sem)
    if best_ci < 0 or best < 0.35:
        return []
    # 是否跳过首行：只有命中列的首格确实是表头才跳。
    # ① 语义命中但首格本身就是公司名（如「XX有限公司」含“公司”二字）→ 是数据不是表头；
    # ② 无表头的 xlsx/txt 首行也是数据，跳了就丢第一家。
    first_val = (header[best_ci] or "").strip()
    header_literal = first_val in ("公司名", "公司名称", "企业名称", "单位名称", "名称",
                                  "公司", "企业", "商户名称", "客户名称")
    skip_first = (best_sem and not _cleaner._EXT_COMPANY_TAIL_RE.search(first_val)) \
        or header_literal
    body = grid[1:] if skip_first else grid
    names, seen = [], set()
    for r in body:
        v = (r[best_ci] or "").strip()
        if not v or v == "None" or v in seen:
            continue
        if not _cleaner._is_company(v):
            continue
        seen.add(v)
        names.append(v)
    return names


class ChannelQueue:
    """单个平台（企知道/风鸟/爱企查）在 master 名单上的独立采集进度。

    三平台并行 = 三条互不干扰的 ChannelQueue 走同一份 master：A 家采过不代表 B 家
    可以跳过（各家有独家号码，正是要靠重复覆盖把联系方式并全），所以 tried/collected
    全部按平台分账，游标也各走各的。

    记账三态（沿用旧版语义，只是从「一份」变成「每平台一份」）：
      tried      本平台已送查过的名字——查没查到都不再重复送查（防死循环烧请求）；
      collected  本平台已采到联系方式的名字（pop 命中直接跳过并记 tried）；
      retry      本轮「根本没查成」的名字（未登录 / 被拦 / 额度用尽 / 链路异常）：
                 排在最前优先补采且**不记 tried**，与 mark_result(False) 的丢弃严格区分。
    """

    def __init__(self, slug, owner):
        self.slug = str(slug or DEFAULT_CHANNEL)
        self.label = channel_label(self.slug)
        self._owner = owner
        self._lock = owner._lock
        self._tried = set()
        self._collected = set()
        self._retry = deque()
        self._in_retry = set()
        self._cursor = 0
        self._todo = 0          # master 中尚未 tried 的数量（O(1) 计数，避免全表扫描）

    # ── 内部（调用方持锁）────────────────────────────────────
    def _mark_tried(self, name):
        """记「本平台查过了」。只有确实挂在 master 上的名字才扣待办计数，
        避免手工删表/异常回执把计数减成负数。"""
        if name in self._tried:
            return
        self._tried.add(name)
        if name in self._owner._in_master:
            self._todo = max(0, self._todo - 1)
        self._owner._dirty = True

    def _take(self):
        """取下一个候选名字（不做探针判断）：retry 队首优先，再沿游标跳过已 tried。"""
        while self._retry:
            name = self._retry.popleft()
            self._in_retry.discard(name)
            if name in self._tried:       # retry 期间被别的路径记了 tried：不重复送查
                continue
            return name
        master = self._owner._names
        while self._cursor < len(master):
            name = master[self._cursor]
            self._cursor += 1
            if name in self._tried:
                continue
            return name
        return None

    def resync(self):
        """master 或 tried 批量变化后重建游标/计数（bind 读盘后调用一次即可）。"""
        with self._lock:
            master = self._owner._names
            self._todo = sum(1 for n in master if n not in self._tried)
            i = 0
            while i < len(master) and master[i] in self._tried:
                i += 1
            self._cursor = i
            self._retry = deque(n for n in self._retry if n not in self._tried)
            self._in_retry = set(self._retry)

    # ── 出列 / 回执 ──────────────────────────────────────────
    def pop(self):
        """取下一家待补采公司。命中 collected / 关键词通道探针的直接跳过（不浪费请求）。
        pop 即记 tried：查不到就丢弃，绝不回列（防死循环）。无活可干返回 None。"""
        with self._lock:
            while True:
                name = self._take()
                if name is None:
                    return None
                self._owner._dirty = True
                if name in self._collected:
                    self._mark_tried(name)
                    continue
                probe = self._owner._collected_probe
                if probe:
                    try:
                        if probe(name):
                            self._collected.add(name)
                            self._mark_tried(name)
                            continue
                    except Exception:
                        pass
                self._mark_tried(name)
                return name

    def mark_result(self, name, found):
        """本平台补采回执：查到→记 collected（结果进主结果集参与清洗合并）；
        查不到→只留在 tried（直接丢弃，用户原话「查不出的数据直接丢弃」）。"""
        with self._lock:
            name = str(name or "").strip()
            if not name:
                return
            self._mark_tried(name)
            if found:
                self._collected.add(name)
            self._owner._flush_ledger()

    def requeue(self, name):
        """把「这一家根本没真正查成」的公司放回队首要重排（区别于查不到的丢弃）。
        与 tried 解耦：从 tried 移除、待办计数加回，下轮 pop 第一个就是它。
        已 collected 的不 requeue（已采到，无需重来）。"""
        with self._lock:
            name = str(name or "").strip()
            if not name or name in self._in_retry or name in self._collected:
                return False
            was_tried = name in self._tried
            self._tried.discard(name)
            if was_tried and name in self._owner._in_master:
                self._todo += 1
            self._retry.append(name)
            self._in_retry.add(name)
            self._owner._dirty = True
            self._owner._flush_ledger()
            return True

    def has_work(self):
        with self._lock:
            return bool(self._retry) or self._todo > 0

    def counts(self):
        with self._lock:
            return {"pending": self._todo, "tried": len(self._tried),
                    "collected": len(self._collected), "retry": len(self._retry),
                    "master": len(self._owner._names)}

    def snapshot(self):
        """剩余待采（本平台口径）：retry 在前，其后按 master 顺序取未 tried 的。"""
        with self._lock:
            out = list(self._retry)
            seen = set(out)
            for n in self._owner._names[self._cursor:]:
                if n in self._tried or n in seen:
                    continue
                seen.add(n)
                out.append(n)
            return out

    def status_of(self, name):
        """单个名字在本平台的进度文案（写名单表的状态列用）。"""
        with self._lock:
            if name in self._collected:
                return "已采到"
            if name in self._in_retry:
                return "待重试"
            if name in self._tried:
                return "已查过"
            return "待采集"


class PendingCompanyList:
    """任务级待采名单：一份 master（用户可见可改）+ 每平台独立进度。

    bind() 之前所有写操作只记内存（允许先 seed 后绑定任务文件夹）。
    **扁平 API 默认作用于企知道通道**（DEFAULT_CHANNEL），旧调用点与回归断言语义不变；
    多平台并行时用 queue(slug) 拿该平台的独立队列，各自的 tried/collected/游标互不干扰。
    """

    def __init__(self):
        self._lock = threading.RLock()
        self._names = []          # master 顺序 = 每个平台共同的采集顺序（第 1 家 → 第 N 家）
        self._in_master = set()
        self._channels = {}       # slug -> ChannelQueue（只登记真正用过的平台）
        self._dir = None
        self._collected_probe = None     # 可选回调：关键词通道是否已采到该公司
        self._dirty = False
        self._unbound_seed = []          # bind 之前进来的公司名，bind 时落盘

    # ── 平台通道 ─────────────────────────────────────────────
    def queue(self, slug=DEFAULT_CHANNEL):
        """取（或惰性建）某平台的采集队列。新建时按当前 master 初始化待办计数。"""
        with self._lock:
            slug = str(slug or DEFAULT_CHANNEL)
            ch = self._channels.get(slug)
            if ch is None:
                ch = self._channels[slug] = ChannelQueue(slug, self)
                ch.resync()
            return ch

    def channels(self):
        with self._lock:
            return [self.queue(s) for s in sorted(self._channels)]

    # ── 绑定任务文件夹 ────────────────────────────────────────
    def bind(self, session_dir):
        with self._lock:
            self._dir = Path(session_dir)
            self._dir.mkdir(parents=True, exist_ok=True)
            self._load_ledger()
            self._load_xlsx()
            added = 0
            if self._unbound_seed:
                added = self._extend(self._unbound_seed, "seed")
                self._unbound_seed = []
            for ch in self._channels.values():
                ch.resync()     # 台账里的 tried 与读回的 master 对齐后再开跑
            self.flush()
            return added

    @property
    def xlsx_path(self):
        return self._dir / LIST_FILENAME if self._dir else None

    @property
    def is_bound(self):
        """是否已绑定任务文件夹（绑定后废数据才能落盘到待采名单）。"""
        with self._lock:
            return self._dir is not None

    @property
    def ledger_path(self):
        return self._dir / LEDGER_FILENAME if self._dir else None

    def set_collected_probe(self, fn):
        with self._lock:
            self._collected_probe = fn

    # ── 入列 ─────────────────────────────────────────────────
    def seed(self, names, source="名单文件"):
        """用户名单整份入列（任务开始时调用）。"""
        with self._lock:
            if self._dir is None:
                self._unbound_seed.extend(names)
                return len(names)
            n = self._extend(names, source)
            self.flush()
            return n

    def add_waste(self, names):
        """清洗剔掉的废数据公司名入列（自动保存线程调用）。tried/collected 里的不收。"""
        with self._lock:
            n = self._extend(names, "waste")
            if n:
                self.flush()
            return n

    def _extend(self, names, source):
        """把新公司名并进 master（已在册的不重复加）。
        已建通道各自 +1 待办，但**该平台已 tried 的名字不加计数**——否则「先采过、
        后又作为废数据回流」会把 pending 虚增成永远采不完。"""
        added = 0
        for raw in names or []:
            name = str(raw or "").strip()
            if not name or name in self._in_master:
                continue
            self._in_master.add(name)
            self._names.append(name)
            added += 1
        if added:
            for ch in self._channels.values():
                ch._todo += sum(1 for n in self._names[-added:] if n not in ch._tried)
            self._dirty = True
        return added

    # ── 出列 / 回执（默认通道扁平 API）───────────────────────
    def pop(self, slug=None):
        """默认通道（企知道）取下一家待补采公司；多平台并行请显式传 slug。"""
        return self.queue(slug or DEFAULT_CHANNEL).pop()

    def mark_result(self, name, found, slug=None):
        self.queue(slug or DEFAULT_CHANNEL).mark_result(name, found)

    def requeue(self, name, slug=None):
        return self.queue(slug or DEFAULT_CHANNEL).requeue(name)

    def has_work(self, slug=None):
        return self.queue(slug or DEFAULT_CHANNEL).has_work()

    def counts(self, slug=None):
        """默认通道口径（旧调用点/回归断言语义不变）。三平台总览用 channel_counts()。"""
        return self.queue(slug or DEFAULT_CHANNEL).counts()

    def channel_counts(self, slugs=None):
        """{平台: 三态计数}，只列真正登记过的通道；master 总数挂在 "__master__"。"""
        with self._lock:
            chans = self._channels
            if slugs:
                chans = {s: self.queue(s) for s in slugs if s in chans}
            out = {s: ch.counts() for s, ch in chans.items()}
            out["__master__"] = {"pending": len(self._names), "master": len(self._names),
                                 "tried": 0, "collected": 0, "retry": 0}
            return out

    def snapshot(self, slug=None):
        return self.queue(slug or DEFAULT_CHANNEL).snapshot()

    def master(self):
        """名单主体（只读快照）：三平台共同的采集顺序。"""
        with self._lock:
            return list(self._names)

    # ── 落盘 / 读盘 ──────────────────────────────────────────
    def flush(self):
        """名单 xlsx + 台账 json 原子落盘。保存线程收尾/停止时调用。"""
        with self._lock:
            if self._dir is None:
                return False
            self._flush_xlsx()
            self._flush_ledger()
            self._dirty = False
            return True

    def _flush_xlsx(self):
        """名单表 = master 全量（三平台共同底稿）+ 每个平台一列进度。
        不再像旧版那样「查过就从表里消失」：一份表要能同时回答三个平台各自跑到哪儿，
        而且各平台游标就是 master 下标——行序稳定性比视觉清爽更重要（进度列代替删行）。"""
        try:
            from openpyxl import Workbook
            wb = Workbook()
            ws = wb.active
            ws.title = "待采集公司名单"
            chans = [self.queue(s) for s in sorted(self._channels)]
            ws.append(["公司名"] + [f"{ch.label}状态" for ch in chans])
            for name in self._names:
                ws.append([name] + [ch.status_of(name) for ch in chans])
            tmp = str(self.xlsx_path) + ".tmp"
            wb.save(tmp)
            os.replace(tmp, str(self.xlsx_path))
        except PermissionError:
            pass    # 名单被 Excel 打开着：本轮跳过，下轮补上（不阻断采集）
        except Exception:
            pass

    def _flush_ledger(self):
        """台账 v2：按平台分段存 tried/collected（master 存 xlsx，不重复存第二份）。
        retry 队列刻意不落盘：它是「本轮根本没查成」的临时态，这些名字都没记 tried，
        重启后游标扫到它必然再采一次——不会漏，也就不需要额外的持久化口径。"""
        try:
            payload = {"version": LEDGER_VERSION,
                       "at": time.strftime("%Y-%m-%d %H:%M:%S"),
                       "channels": {ch.slug: {"tried": sorted(ch._tried),
                                              "collected": sorted(ch._collected)}
                                    for ch in self._channels.values()}}
            tmp = str(self.ledger_path) + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False)
            os.replace(tmp, str(self.ledger_path))
        except Exception:
            pass

    def _load_ledger(self):
        try:
            data = json.loads(self.ledger_path.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        if not isinstance(data, dict):
            data = {}
        blocks = data.get("channels")
        if not isinstance(blocks, dict):
            # v1 单通道台账 → 整段进度迁移给企知道：老任务续跑不会把查过的重采一遍
            blocks = {DEFAULT_CHANNEL: {"tried": data.get("tried", []),
                                        "collected": data.get("collected", [])}}
        for slug, blk in blocks.items():
            if not isinstance(blk, dict):
                continue
            ch = self.queue(slug)
            ch._tried = {str(x or "").strip() for x in (blk.get("tried") or [])
                         if str(x or "").strip()}
            ch._collected = {str(x or "").strip() for x in (blk.get("collected") or [])
                             if str(x or "").strip()}

    def _load_xlsx(self):
        """续跑同一任务文件夹时把名单读回（用户也可能手工往表里加公司）。"""
        if not self.xlsx_path or not self.xlsx_path.exists():
            return
        try:
            from openpyxl import load_workbook
            wb = load_workbook(str(self.xlsx_path), data_only=True)
            ws = wb.active
            names = []
            for row in ws.iter_rows(values_only=True):
                v = "" if not row or row[0] is None else str(row[0]).strip()
                if v and v != "公司名":
                    names.append(v)
            wb.close()
            self._extend(names, "reload")
        except Exception:
            pass
