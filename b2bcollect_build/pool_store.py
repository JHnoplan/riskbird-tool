# -*- coding: utf-8 -*-
"""pool_store.py —— 数据池本地物化视图（Gold 层）：SQLite + FTS5。

定位（三层数据湖在单机桌面工具里的落地）：
    Bronze  OSS users/*/upload_*.json        只追加、永不改写的原始上传层
    Silver  ingest_cleaning.clean_records    入库前清洗 + 分区键派生（属地/行业/省份）
    Gold    pool_store.PoolStore              本地物化视图：全文索引 + 分区裁剪 + 聚合下压

为什么是这个架构（对齐业界主流做法，而非自造轮子）：
  · 搜索引擎层交给 SQLite FTS5（倒排索引、BM25 打分、游标分页都是库内成熟实现），
    等价于 Elasticsearch/OpenSearch 的角色，但零部署、零依赖、可随包分发；
  · 分区键（region/industry/province）在写入端物化成真实列，查询时变成 SQL 谓词，
    等价于 Hive/Iceberg 的 partition pruning 与 Parquet 的 min/max 跳过；
  · 统计与筛选项（联系方式/规模/新鲜度）下压成 count/group by，
    等价于 OLAP 的 aggregate pushdown——不再把百万条 JSON 读进内存再遍历；
  · 全量重建在**单个事务**里完成：WAL 给读者快照隔离，重建期间搜索照常命中旧数据，
    提交瞬间原子切换，等价于物化视图的 swap-partition 刷新；
  · 任何一步失败（sqlite 编译期没 FTS5、磁盘只读、库损坏）都返回「不可用」，
    调用方自动回退 JSON + 倒排索引的老链路，功能不会因为优化层缺失而变差。

口径纪律（谁说了算）：
  · 属地 = data_pool.record_region（唯一实现，本模块只在写入端调它一次）
  · 有效性门禁 = data_pool._is_valid_record
  · 可检索字段 = pool_matcher.search_fields（倒排索引、FTS 列、相关性判据共用）
  · 联系方式/规模/新鲜度位标记 = pool_matcher.contact_flags / scale_bits / source_ts
  本模块**不复制任何判定规则**，只存这些函数的结果；离线回归里有
  「SQL 下压 == Python 过滤」的等价性断言兜底，改任何一侧都会立刻红。
"""
import json
import os
import re
import sqlite3
import threading
import time
import zlib
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

from pool_matcher import (
    FLAG_EMAIL, FLAG_MOBILE, FLAG_PHONE, FLAG_WECHAT,
    SCALE_LARGE, SCALE_MEDIUM, SCALE_SMALL,
    contact_flags, expand_query, scale_bits, search_fields, source_ts, tokenize,
)

# 视图结构版本：列语义变化时 +1，旧库自动判定为过期并重建
#   1 → 2：industry 列改为「永远非空」（判不出行业落 ingest_cleaning
#           .INDUSTRY_UNKNOWN 哨兵值），旧库里那一列还留着空串，
#           按行业筛选与清洗报告会对不上，必须整体重建一次
#   2 → 3：province 列改为「永远非空 + 支持英文/当地语地址」——派生词表补齐
#           英文中国地名、国名归一、国际区号、国码域名四维证据，判不出的落
#           PROVINCE_UNKNOWN 哨兵；同时「地区」筛选谓词从 loc LIKE 子串改成
#           province 等值（可走 idx_province，百万级不再全表扫）。
#           旧库那一列是按 0.3% 覆盖率算出来的空值/原始国名，不重建就等于
#           地区筛选、地区分布、同地区推荐全都还在旧口径上，必须整体重建一次
SCHEMA_VERSION = 3

# 每批写库的记录数（太大内存抖动，太小 executemany 开销大）
_BATCH = 2000

# 重建进度回调的最小条数：低于它每批都回调反而拖慢重建（也是「两万条以上
# 才会走到 progress_cb」的门槛——小语料测试永远覆盖不到，改这里要看 rebuild 的契约）
_PROGRESS_MIN_RECORDS = 20000

# 界面总数展示的封顶档（Elasticsearch track_total_hits 的默认思路，取值放宽到
# 十万）：精确数完百万级宽词的命中要 2s 量级，而用户拿这个数只为了判断
# 「这个范围大不大、要不要收紧筛选」——超过十万就已经是「很大」，精度没意义。
# 报数时必须连同「是否封顶」一起报，见 count_capped。
COUNT_CAP = 100000

# FTS 列权重：公司名 > 主营 > 其它字段（地址/号码/来源等）
_BM25_WEIGHTS = (6.0, 3.0, 1.0)

_BUSY_TIMEOUT_S = 15.0

# 主表列顺序（写入端唯一一份，rebuild/upsert 共用，少一列立刻报错而不是静默错位）
# 注意：这里不再有 loc 列。旧版「地区」筛选是 loc LIKE 子串（地址+国家小写副本），
# 它既走不了索引（百万级全表扫），又与 province 分区键是两套口径（同一屏两个数字
# 对不上、勾国外能筛出省份）。2026-11-10 起统一按 province 等值筛选，子串副本删除。
_COMPANY_COLS = ("company_norm, company, region, province, industry, country,"
                 " source, source_user, source_time, source_ts, flags,"
                 " scale_bits, quality, payload")
_N_COLS = len(_COMPANY_COLS.split(","))
_PH = ",".join("?" * _N_COLS)                    # 不带 id 的插入
_PH_WITH_ID = ",".join("?" * (_N_COLS + 1))      # 全量重建按顺序带 id

# 筛选面板「企业规模」→ scale_bits 位（与 pool_matcher.scale_bits 同源）
_SCALE_BIT = {"large": SCALE_LARGE, "medium": SCALE_MEDIUM, "small": SCALE_SMALL}

# 允许聚合下压的维度（同时也是分区键），白名单防 SQL 拼接面扩大
_FACET_COLUMNS = {"region", "province", "industry", "country", "source",
                  "source_user", "quality"}


@lru_cache(maxsize=32768)
def _tokenize_dedup(text: str) -> str:
    """列内 token 去重后写入，结果按文本缓存。

    为什么去重：BM25 的词频项因此退化为 0/1——可以接受且是有意为之，物化视图的
    rank 只用来决定「哪些候选进入精排集合」与排序截断，最终相关度由 CompanyMatcher
    用同一批 token 精确算 overlap/Jaccard 决定；去重同时把 FTS 索引体积压下一块。

    为什么缓存：分词是重建里最贵的一步（实测 20μs/列/条），而同一份采集数据里
    三段文本的重复率很高（历史池 66,893 条里主营列只有 1 种取值、时间列只有 6 种），
    按文本缓存能把这些完全重复的分词直接省掉。maxsize 有界，避免百万条时缓存吃内存。
    """
    if not text:
        return ""
    return " ".join(dict.fromkeys(tokenize(text)))


def _quote_token(tok: str) -> str:
    """把分词结果转成 FTS5 安全字面量（双引号包裹，内部引号翻倍）。"""
    return '"' + tok.replace('"', '""') + '"'


def match_tokens(query: str, expanded: bool = True) -> List[str]:
    """检索 token（去重、保序）。expanded=False 时只用原查询切出来的词。

    召回侧与评分侧必须同源：CompanyMatcher 用 expand_query 的同义词打分，
    候选集完全不认同义词就会出现「评分认同期词、索引不召回」的漏召回；
    但同义词也不能无脑当主召回——实测业务词表里「光伏」会扩出「储能/电池/
    电缆」，400 条语料里搜「光伏」直接召回 300 条。所以分成两层用（见
    PoolStore._resolve_expr）：原词层优先，原词层空了才用同义词层兜底。
    """
    if not query:
        return []
    tokens: List[str] = []
    seen: set = set()
    terms = expand_query(query) if expanded else (query,)
    for term in terms:
        for tok in tokenize(term):
            if tok and tok not in seen:
                seen.add(tok)
                tokens.append(tok)
    return tokens


def match_expr(query: str, max_tokens: int = 64, expanded: bool = True) -> str:
    """查询串 → FTS5 MATCH 表达式（多字 token 的 OR，全部带引号防表达式注入）。

    只用**多字 token**（中文 bigram、英文词、数字段），对齐 Elasticsearch 的
    CJK bigram 分词实践。中文单字在语料里几乎是万能词（能/器/公司/有限），
    带上它会把召回放大到全表两成——实测 66,893 条里「光伏」命中 13,312 条，
    有界候选集（精排集合）形同虚设，而且乱输一串字也能凑出几十条命中。
    查询本身切不出多字 token（单字查询，如「光」「阀」）时自动退回单字，
    所以「单字查询」不依赖任何放宽分支就能正常工作。

    expanded=False 得到原词层表达式，True 得到同义词层；哪一层生效由
    _resolve_expr 用 EXISTS 探针决定，四个读入口共用同一次决策。
    """
    tokens = match_tokens(query, expanded=expanded)
    if not tokens:
        return ""
    multi = [t for t in tokens if len(t) >= 2]
    if multi:
        tokens = multi
    if len(tokens) > max_tokens:
        tokens = tokens[:max_tokens]   # 超长查询只取前若干 token（防表达式爆炸）
    return " OR ".join(_quote_token(t) for t in tokens)


def fingerprint_records(records: Sequence[dict]) -> Tuple[int, int]:
    """总表内容指纹 (条数, crc32)。

    用 crc32 而不是内置 hash()：后者受 PYTHONHASHSEED 随机化，跨进程不可比。
    指纹覆盖「顺序 + 公司主键 + 采集时间 + 质量分 + 属地」——增量合并真正改动
    某组内容时这些值必变；分块累加避免为 100 万条拼一个巨型字符串。
    """
    crc = 0
    buf: List[str] = []
    for rec in records:
        if not isinstance(rec, dict):
            continue
        buf.append("\x1e".join([
            str(rec.get("company_norm") or ""),
            str(rec.get("source_time") or ""),
            str(rec.get("quality") or ""),
            str(rec.get("region") or rec.get("is_domestic") or ""),
        ]))
        if len(buf) >= 4096:
            crc = zlib.crc32("\x1f".join(buf).encode("utf-8", "ignore"), crc)
            buf = []
    if buf:
        crc = zlib.crc32("\x1f".join(buf).encode("utf-8", "ignore"), crc)
    return len(records), crc & 0xFFFFFFFF


@lru_cache(maxsize=1)
def _rule_version() -> str:
    """属地判据版本（延迟取，与 data_pool 同一份常量，不复制数字）。"""
    try:
        from data_pool import REGION_RULE_VERSION
        return str(REGION_RULE_VERSION)
    except Exception:
        return ""


def _derive_region(rec: dict) -> str:
    """属地键：只用 data_pool.record_region（口径唯一）。"""
    try:
        from data_pool import record_region
        return record_region(rec)
    except Exception:
        if "region" in rec:
            return rec["region"] if rec.get("region") in ("dom", "intl") else "dom"
        return "dom" if rec.get("is_domestic", True) else "intl"


def _derive_province(rec: dict, region: Optional[str] = None) -> str:
    """地区分区列：口径全部在 ingest_cleaning.partition_province（唯一实现）。

    不在视图层再写一份地名规则：视图列、清洗报告、地区筛选、同地区推荐必须是
    同一个字符串，两处各写一份迟早分叉（历史缺陷是分布加总与旁边总数对不上）。
    """
    try:
        from ingest_cleaning import partition_province
        return partition_province(rec, region)
    except Exception:
        return str(rec.get("province") or "").strip()


def _derive_industry(rec: dict) -> str:
    """行业分区列：口径全部在 ingest_cleaning.partition_industry（唯一实现）。

    不在这里另写一个「未分类」字面量：视图列、清洗报告、行业筛选必须是同一个
    字符串，两处各写一份迟早会分叉——分叉的结果是「报告说有 3795 条未分类，
    按未分类筛选却一条都筛不到」。
    """
    try:
        from ingest_cleaning import partition_industry
        return partition_industry(rec)
    except Exception:
        return str(rec.get("industry") or "").strip()


def _derive_quality(rec: dict) -> int:
    try:
        from data_pool import compute_quality_score
        return int(compute_quality_score(rec))
    except Exception:
        return 0


class PoolStore:
    """数据池本地物化视图（SQLite + FTS5）。

    线程模型：GUI 线程读、后台线程写。每个线程一条连接（sqlite3 连接不能跨线程），
    写操作串行化到一把锁上；WAL 让"边写边读"成立。
    """

    FILE_NAME = "pool_store.db"

    def __init__(self, cache_dir):
        self._dir = Path(str(cache_dir))
        self._path = self._dir / self.FILE_NAME
        self._local = threading.local()
        self._write_lock = threading.Lock()
        self._fts_ok: Optional[bool] = None
        self._last_error = ""
        self._meta_cache: Dict[str, str] = {}
        self._meta_cache_at = 0.0

    # ── 基本状态 ──

    @property
    def path(self) -> str:
        return str(self._path)

    @property
    def last_error(self) -> str:
        return self._last_error

    @property
    def exists(self) -> bool:
        return self._path.exists()

    def fts_available(self) -> bool:
        """当前 SQLite 编译期是否带 FTS5（打进去的包只依赖标准库，一般都有）。"""
        if self._fts_ok is None:
            try:
                conn = sqlite3.connect(":memory:")
                try:
                    conn.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
                    self._fts_ok = True
                finally:
                    conn.close()
            except Exception as e:
                self._fts_ok = False
                self._last_error = f"FTS5 不可用: {e}"
        return bool(self._fts_ok)

    def usable(self) -> bool:
        """Gold 层是否可用：有 FTS5 且缓存目录可写。"""
        if not self.fts_available():
            return False
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            probe = self._dir / ".pool_store.write"
            probe.write_text("1", encoding="utf-8")
            probe.unlink()
            return True
        except Exception as e:
            self._last_error = f"缓存目录不可写: {e}"
            return False

    # ── 连接管理 ──

    def _connect(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            return conn
        conn = sqlite3.connect(str(self._path), timeout=_BUSY_TIMEOUT_S)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA temp_store=MEMORY")
        conn.execute("PRAGMA cache_size=-65536")          # 64MB 页缓存
        conn.execute("PRAGMA mmap_size=268435456")        # 256MB mmap，读大 payload 少 syscall
        conn.execute(f"PRAGMA busy_timeout={int(_BUSY_TIMEOUT_S * 1000)}")
        self._local.conn = conn
        return conn

    def close(self):
        """关掉当前线程的连接（其它线程的连接随线程结束回收）。"""
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
            self._local.conn = None

    # ── 元数据 ──

    def readable(self) -> bool:
        """视图可读：文件存在且带有效 meta（空库/半成品库一律视为不可用）。

        读路径全部走这道闸门——sqlite3.connect 会顺手创建空文件，若不加闸门，
        一个被探测出来的空库会让上层以为「库里一条都没有」而不是「视图还没建」，
        后者才该回退老链路。
        """
        if not self._path.exists():
            return False
        return bool(self.meta().get("record_count"))


    @staticmethod
    def _create_meta(conn: sqlite3.Connection):
        conn.execute("CREATE TABLE IF NOT EXISTS meta("
                     "k TEXT PRIMARY KEY, v TEXT)")

    # 二级索引列：分区裁剪 + 点查 + 排序，全部覆盖在便宜的列上
    _INDEX_COLUMNS = ("company_norm", "region", "industry", "province",
                      "source", "quality", "source_ts")

    @classmethod
    def _create_indexes(cls, conn: sqlite3.Connection):
        for col in cls._INDEX_COLUMNS:
            conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{col} "
                         f"ON company({col})")

    @classmethod
    def _create_tables(cls, conn: sqlite3.Connection, with_indexes: bool = True):
        conn.execute("""
            CREATE TABLE IF NOT EXISTS company(
              id INTEGER PRIMARY KEY,
              company_norm TEXT,
              company TEXT, region TEXT, province TEXT, industry TEXT,
              country TEXT, source TEXT, source_user TEXT,
              source_time TEXT, source_ts REAL,
              flags INTEGER, scale_bits INTEGER, quality INTEGER,
              payload TEXT)
        """)
        conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS company_fts "
                     "USING fts5(name_txt, prod_txt, other_txt)")
        if with_indexes:
            cls._create_indexes(conn)

    def meta(self) -> Dict[str, str]:
        """读 meta 表（60s 内存缓存：同一批 UI 操作里反复问版本不必再打磁盘）。"""
        now = time.time()
        if self._meta_cache and now - self._meta_cache_at < 60:
            return self._meta_cache
        data: Dict[str, str] = {}
        if self._path.exists():
            try:
                conn = self._connect()
                for k, v in conn.execute("SELECT k, v FROM meta"):
                    data[str(k)] = str(v)
            except Exception as e:
                self._last_error = f"读 meta 失败: {e}"
        self._meta_cache, self._meta_cache_at = data, now
        return data

    def _write_meta(self, conn: sqlite3.Connection, mapping: Dict[str, Any]):
        conn.executemany("INSERT INTO meta(k, v) VALUES(?, ?) "
                         "ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                         [(str(k), str(v)) for k, v in mapping.items()])

    def invalidate_meta_cache(self):
        self._meta_cache = {}
        self._meta_cache_at = 0.0

    def is_current(self, fingerprint: Optional[Tuple[int, int]]) -> bool:
        """库内数据是否就是这批总表（指纹含条数与内容 crc32）。"""
        if not fingerprint or not self._path.exists():
            return False
        count, crc = fingerprint
        m = self.meta()
        try:
            return (int(m.get("schema_version", "0")) == SCHEMA_VERSION
                    and int(m.get("record_count", "-1")) == count
                    and int(m.get("fingerprint", "-1")) == crc)
        except (TypeError, ValueError):
            return False

    # ── 行派生（写入端一次性算清所有分区列与下压列）──

    @staticmethod
    def _row(rec: dict) -> tuple:
        region = _derive_region(rec)
        province = _derive_province(rec, region)
        industry = _derive_industry(rec)
        quality = _derive_quality(rec)
        # 派生出来的分区键回写进 payload（只改副本，绝不动调用方传入的原记录）：
        # 列与记录必须同源，否则「按列裁剪能命中、按返回记录看又没有这个
        # 行业/省份」两套说法；历史池里缺 industry 的记录，其派生行业同时
        # 进入可检索文本（search_fields 取 products + industry），搜品类词才
        # 能召回自己归类到该品类的公司。
        if (rec.get("region") != region
                or rec.get("is_domestic") is not (region == "dom")
                or (province and rec.get("province") != province)
                or (industry and rec.get("industry") != industry)
                or rec.get("quality") != quality):
            rec = dict(rec)
            rec["region"] = region
            rec["is_domestic"] = region == "dom"
            rec["quality"] = quality
            # 属地是判据的输出，随附版本标记一起物化：payload 回到上层后被
            # 再次推导能识别出「这是当前判据算的」，不必重算，也不会因为
            # 缺标记就把值当成历史遗留而反复刷新（详见 REGION_RULE_VERSION）
            rec["region_rule"] = _rule_version()
            if province:
                rec["province"] = province
            if industry:
                rec["industry"] = industry
        name_txt, prod_txt, other_txt = search_fields(rec)
        # 列值与回写进 payload 的值必须来自同一次派生（此前 quality 在这一行里
        # 算了两遍：payload 用局部变量、列用第二次调用，派生规则一旦带状态就分叉）
        return (
            str(rec.get("company_norm") or ""),
            str(rec.get("company") or ""),
            region,
            province,
            industry,
            str(rec.get("country") or ""),
            str(rec.get("source") or ""),
            str(rec.get("source_user") or ""),
            str(rec.get("source_time") or ""),
            source_ts(rec.get("source_time")),
            contact_flags(rec),
            scale_bits(rec.get("company")),
            quality,
            json.dumps(rec, ensure_ascii=False, default=str),
        ), (name_txt, prod_txt, other_txt)

    @staticmethod
    def _tok(text: str) -> str:
        """列内 token 去重后写入（结果按文本缓存，见 _tokenize_dedup）。"""
        return _tokenize_dedup(text)

    # ── 构建 / 增量 ──

    def rebuild(self, records: Sequence[dict],
                fingerprint: Optional[Tuple[int, int]] = None,
                progress_cb=None) -> bool:
        """全量重建：单事务内 DROP + 重建 + 灌数，读者全程看旧快照，提交即原子切换。

        失败返回 False（库保持原样，调用方继续用老链路）。

        progress_cb 的契约是 **两个整数** `progress_cb(done, total)`，而且只在
        条数达到 _PROGRESS_MIN_RECORDS 时才回调（小池子每批都回调反而拖慢重建），
        最后一次一定是 `progress_cb(total, total)`，尾批不会再停在 98%。
        两条都必须记住：两万条以上才会踩到回调这条路，测试用的小语料永远踩不到；
        回调里抛异常会让整个重建回滚、视图永远建不起来——把 Qt 的
        `signal[str].emit` 直接当 progress_cb 传进来就是这种情况（emit 收到两个
        int 直接 TypeError），调用方必须传一个自己整理文案的适配函数。
        """
        if not self.usable():
            return False
        if fingerprint is None:
            fingerprint = fingerprint_records(records)
        t0 = time.time()
        try:
            with self._write_lock:
                conn = self._connect()
                conn.execute("BEGIN IMMEDIATE")
                # 先保证 meta 在（全新库时 DROP 之后再建 meta 会来不及写版本），
                # 再整表重建 company/fts：DROP + CREATE 在同一事务里，
                # 读者事务看到的是提交前的旧表，切换是原子的
                self._create_meta(conn)
                conn.execute("DELETE FROM meta")   # 清掉上一版的时间戳等杂项，避免残留
                conn.execute("DROP TABLE IF EXISTS company_fts")
                conn.execute("DROP TABLE IF EXISTS company")
                self._create_tables(conn, with_indexes=False)

                batch_main: List[tuple] = []
                batch_fts: List[tuple] = []
                total = len(records)
                for rid, rec in enumerate(records, start=1):
                    if not isinstance(rec, dict):
                        continue
                    main, (name_txt, prod_txt, other_txt) = self._row(rec)
                    batch_main.append((rid,) + main)
                    batch_fts.append((rid, self._tok(name_txt), self._tok(prod_txt),
                                      self._tok(other_txt)))
                    if len(batch_main) >= _BATCH:
                        conn.executemany(
                            f"INSERT INTO company(id, {_COMPANY_COLS}) VALUES({_PH_WITH_ID})", batch_main)
                        conn.executemany(
                            "INSERT INTO company_fts(rowid, name_txt, prod_txt,"
                            " other_txt) VALUES(?,?,?,?)", batch_fts)
                        batch_main, batch_fts = [], []
                        if progress_cb and total >= _PROGRESS_MIN_RECORDS:
                            progress_cb(min(rid, total), total)
                if batch_main:
                    conn.executemany(
                        f"INSERT INTO company(id, {_COMPANY_COLS}) VALUES({_PH_WITH_ID})", batch_main)
                    conn.executemany(
                        "INSERT INTO company_fts(rowid, name_txt, prod_txt,"
                        " other_txt) VALUES(?,?,?,?)", batch_fts)
                    batch_main, batch_fts = [], []
                    # 尾批不足 _BATCH 时循环里不会再回调：不补这一下，界面就永远
                    # 停在 98%（66,893 条真实池实测末条进度 98%），进度条没走完
                    # 会让用户以为卡住了
                    if progress_cb and total >= _PROGRESS_MIN_RECORDS:
                        progress_cb(total, total)

                # 索引在灌数之后一次建：省掉 7 棵 B 树在几十万次插入里的逐次维护
                self._create_indexes(conn)

                count = conn.execute("SELECT count(*) FROM company").fetchone()[0]
                self._write_meta(conn, {
                    "schema_version": SCHEMA_VERSION,
                    "record_count": count,
                    "fingerprint": fingerprint[1],
                    "source_count": fingerprint[0],
                    "built_at": datetime.now().isoformat(timespec="seconds"),
                    "built_in_ms": int((time.time() - t0) * 1000),
                })
                conn.execute("COMMIT")
            self.invalidate_meta_cache()
            # WAL 检查点：把重建结果收敛回主库文件，避免 -wal 长期膨胀
            try:
                self._connect().execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except Exception:
                pass
            return True
        except Exception as e:
            self._last_error = f"重建失败: {e}"
            try:
                self._connect().execute("ROLLBACK")
            except Exception:
                pass
            return False

    def ensure(self, records: Sequence[dict],
               fingerprint: Optional[Tuple[int, int]] = None,
               progress_cb=None) -> bool:
        """让视图与这批总表同源：已是最新就零成本返回，否则全量重建。"""
        if not self.usable():
            return False
        if fingerprint is None:
            fingerprint = fingerprint_records(records)
        if self.is_current(fingerprint):
            return True
        return self.rebuild(records, fingerprint, progress_cb)

    def _norm_ids(self, conn: sqlite3.Connection,
                  norms: Sequence[str]) -> Dict[str, int]:
        """company_norm → rowid（覆盖索引点查，按 500 一片避开变量数上限）。"""
        out: Dict[str, int] = {}
        uniq = [n for n in dict.fromkeys(norms) if n]
        for i in range(0, len(uniq), 500):
            chunk = uniq[i:i + 500]
            out.update({str(n): int(rid) for rid, n in conn.execute(
                "SELECT id, company_norm FROM company WHERE company_norm IN (%s)"
                % ",".join("?" * len(chunk)), chunk)})
        return out

    def upsert(self, records: Sequence[dict],
               fingerprint: Optional[Tuple[int, int]] = None) -> bool:
        """按 company_norm 增量覆盖：传入的每条记录就是该组的最新完整状态。

        合并（联系方式取并集）由调用方用 pool_merge 做完再交给这里，视图不
        自己发明合并口径。库里同 norm 的旧行整行替换、FTS 行删后重插，任何
        时刻都不会有同一个组的两份文本；没传进来的记录一行都不动——不需要
        全表重算，也不会因为记录顺序变化让任何派生索引错位。

        fingerprint 应传「合并后完整总表」的指纹：视图版本号由此与总表对齐，
        下一轮才能零成本判定 is_current。不传则保守置为失效（-1），下一轮
        自动全量校验——宁可多重建一次，绝不让视图顶着假的有效版本号骗人。
        """
        if not records or not self.usable() or not self.readable():
            return False
        try:
            with self._write_lock:
                conn = self._connect()
                conn.execute("BEGIN IMMEDIATE")
                prepared: List[Tuple[str, tuple, tuple]] = []
                for rec in records:
                    if not isinstance(rec, dict):
                        continue
                    main, (name_txt, prod_txt, other_txt) = self._row(rec)
                    if not main[0]:
                        continue    # 无主键的记录无法定位，只能由全量重建兜底
                    prepared.append((main[0], main, (self._tok(name_txt),
                                                     self._tok(prod_txt),
                                                     self._tok(other_txt))))
                if not prepared:
                    conn.execute("COMMIT")
                    return True

                known = self._norm_ids(conn, [n for n, _, _ in prepared])
                upd = [main[1:] + (known[norm],)
                       for norm, main, _ in prepared if norm in known]
                if upd:
                    conn.executemany(
                        "UPDATE company SET company=?, region=?, province=?,"
                        " industry=?, country=?, source=?, source_user=?,"
                        " source_time=?, source_ts=?, flags=?, scale_bits=?,"
                        " quality=?, payload=? WHERE id=?", upd)
                new_norms = [norm for norm, _, _ in prepared if norm not in known]
                if new_norms:
                    conn.executemany(
                        f"INSERT INTO company({_COMPANY_COLS}) VALUES({_PH})",
                        [main for norm, main, _ in prepared if norm not in known])
                # FTS：先删这批 rowid 的旧 postings，再按最新 token 重插
                ids = dict(known)
                ids.update(self._norm_ids(conn, new_norms))
                conn.executemany("DELETE FROM company_fts WHERE rowid=?",
                                 [(rid,) for rid in ids.values()])
                conn.executemany(
                    "INSERT INTO company_fts(rowid, name_txt, prod_txt, other_txt)"
                    " VALUES(?,?,?,?)",
                    [(ids[norm],) + toks for norm, _, toks in prepared
                     if norm in ids])

                count = conn.execute("SELECT count(*) FROM company").fetchone()[0]
                version = dict(fingerprint=fingerprint[1],
                               source_count=fingerprint[0],
                               record_count=count) if fingerprint else {
                    "record_count": count, "fingerprint": "-1"}
                version["updated_at"] = datetime.now().isoformat(timespec="seconds")
                self._write_meta(conn, version)
                conn.execute("COMMIT")
            self.invalidate_meta_cache()
            return True
        except Exception as e:
            self._last_error = f"增量更新失败: {e}"
            try:
                self._connect().execute("ROLLBACK")
            except Exception:
                pass
            return False

    def delete_norms(self, norms: Sequence[str]) -> bool:
        """删除指定组（门禁剔除的记录要走这里，否则视图里留僵尸行）。

        不更新 fingerprint 语义：删除同样让指纹失效（置 -1），由调用方在
        下一轮带上新总表指纹的 upsert/rebuild 来恢复版本标记。
        """
        todo = [str(n) for n in dict.fromkeys(norms) if n]
        if not todo or not self._path.exists():
            return False
        try:
            with self._write_lock:
                conn = self._connect()
                conn.execute("BEGIN IMMEDIATE")
                for i in range(0, len(todo), 500):
                    chunk = todo[i:i + 500]
                    ids = [rid for (rid,) in conn.execute(
                        "SELECT id FROM company WHERE company_norm IN (%s)"
                        % ",".join("?" * len(chunk)), chunk)]
                    if not ids:
                        continue
                    conn.executemany("DELETE FROM company_fts WHERE rowid=?",
                                     [(rid,) for rid in ids])
                    conn.executemany("DELETE FROM company WHERE id=?",
                                     [(rid,) for rid in ids])
                count = conn.execute("SELECT count(*) FROM company").fetchone()[0]
                self._write_meta(conn, {"record_count": count, "fingerprint": "-1"})
                conn.execute("COMMIT")
            self.invalidate_meta_cache()
            return True
        except Exception as e:
            self._last_error = f"删除失败: {e}"
            try:
                self._connect().execute("ROLLBACK")
            except Exception:
                pass
            return False

    # ── 查询 ──

    @staticmethod
    def _like(arg: str) -> str:
        """把用户输入转成 LIKE 模式（转义 % 和 _，配 ESCAPE '\\'）。"""
        out = arg.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        return f"%{out}%"

    def _where(self, regions: Optional[Sequence[str]] = None,
               filters: Optional[dict] = None,
               expr: str = "") -> Tuple[List[str], List[Any]]:
        """把筛选参数编译成 SQL 谓词（判据全部来自 pool_matcher 的位标记列）。

        「地区」按 province 分区键**等值**筛选：值直接来自同一列的聚合分布
        （facets），所以「分布里某个地区 N 条」与「按该地区筛出 N 条」必然是
        同一个数；等值还能走 idx_province，百万级不再是 loc LIKE 的全表扫。
        英文地址（"…Zhejiang Province"）从此也能按「浙江」筛到——旧的子串口径
        只认地址里出现的中文省名，这类记录一条都筛不出来。
        """
        clauses: List[str] = []
        params: List[Any] = []
        if expr:
            clauses.append("company_fts MATCH ?")
            params.append(expr)
        sel = [r for r in (regions or []) if r in ("dom", "intl")]
        if len(sel) == 1:
            clauses.append("c.region = ?")
            params.append(sel[0])
        flt = filters or {}
        region = flt.get("region") or ""
        if region and region != "全部":
            clauses.append("c.province = ?")
            params.append(str(region))
        want = 0
        if flt.get("has_mobile"):
            want |= FLAG_MOBILE
        if flt.get("has_email"):
            want |= FLAG_EMAIL
        if flt.get("has_wechat"):
            want |= FLAG_WECHAT
        if flt.get("has_phone"):
            want |= FLAG_PHONE
        if want:
            clauses.append("c.flags & ? = ?")
            params.extend([want, want])
        if flt.get("has_contact"):
            # 「有任何一种联系方式」：展商名录里只有官网/地址的线索行不是错误数据，
            # 但也不能和用户拿来外呼的名单混在一起——是否要看由用户自己勾。
            # 判据与 pool_matcher.apply_filters 的 contact_flags(rec) != 0 同一条。
            clauses.append("COALESCE(c.flags, 0) <> 0")
        scale = flt.get("scale") or ""
        bit = _SCALE_BIT.get(scale)
        if bit:
            clauses.append("c.scale_bits & ? = ?")
            params.extend([bit, bit])
        days = int(flt.get("freshness_days") or 0)
        if days > 0:
            now = flt.get("now") or datetime.now()
            # (now - dt).days <= days  ⟺  ts > now - (days+1)*86400（整日截断等价）
            clauses.append("c.source_ts > ?")
            params.append(now.timestamp() - (days + 1) * 86400)
        industry = flt.get("industry") or ""
        if industry and industry != "全部":
            clauses.append("c.industry = ?")
            params.append(industry)
        source = flt.get("source") or ""
        if source and source != "全部":
            clauses.append("c.source = ?")
            params.append(source)
        min_quality = flt.get("min_quality")
        if min_quality is not None:
            clauses.append("c.quality >= ?")
            params.append(int(min_quality))
        return clauses, params

    def _probe(self, expr: str, regions: Optional[Sequence[str]] = None,
               filters: Optional[dict] = None) -> bool:
        """这套谓词到底有没有命中（EXISTS 语义：LIMIT 1，不数全表）。"""
        if not self.readable() or not expr:
            return False
        clauses, params = self._where(regions, filters, expr)
        sql = "SELECT 1 FROM company c JOIN company_fts ON company_fts.rowid = c.id"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " LIMIT 1"
        try:
            return self._connect().execute(sql, params).fetchone() is not None
        except Exception as e:
            self._last_error = f"探活失败: {e}"
            return False

    def _resolve_expr(self, query: str, regions: Optional[Sequence[str]] = None,
                      filters: Optional[dict] = None) -> str:
        """定稿本次用的 MATCH 表达式：原词层有命中就绝不用同义词层。

        两层是递进关系：搜「光伏」时池里已有 13k 条字面命中，就不该再把只有
        「储能/电池/电缆」的公司算进命中数（业务词表里这几个词互为同义词，
        无脑扩展会让任意查询都召回全表两成）；原词层零命中才用同义词层兜底，
        这时用户要的就是「没有原词、但属于同一品类」的公司。

        检索/计数/聚合/遍历四个入口都过这里，保证「共 N 条」、翻页结果与概览
        数字来自同一次召回决策——两侧口径不一致是搜索类问题最难排查的形态。
        """
        literal = match_expr(query, expanded=False)
        if not literal:
            return ""
        expanded = match_expr(query, expanded=True)
        if expanded == literal:
            return literal
        return literal if self._probe(literal, regions, filters) else expanded

    def search(self, query: str, regions: Optional[Sequence[str]] = None,
               filters: Optional[dict] = None, limit: int = 100,
               offset: int = 0) -> List[dict]:
        """全文 + 分区裁剪 + 筛选下压，返回记录（按 BM25 相关度）。"""
        rows = self._fetch(query, regions, filters, limit, offset)
        out = []
        for row in rows:
            try:
                out.append(json.loads(row[0]))
            except (TypeError, ValueError, IndexError):
                continue
        return out

    def _fetch(self, query: str, regions, filters, limit, offset,
               columns: str = "c.payload") -> List[tuple]:
        if not self.readable():
            return []
        expr = self._resolve_expr(query, regions, filters)
        clauses, params = self._where(regions, filters, expr)
        sql = ("SELECT %s FROM company c" % columns)
        if expr:
            sql += " JOIN company_fts ON company_fts.rowid = c.id"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        if expr:
            sql += (" ORDER BY bm25(company_fts, ?, ?, ?) LIMIT ? OFFSET ?")
            params.extend(list(_BM25_WEIGHTS) + [max(1, int(limit)), max(0, int(offset))])
        else:
            # 浏览态（不填关键词）把「有联系方式的」排在前面，再按完整度、新鲜度。
            # 池子从 6 万涨到 8 万这次带进来 1.3 万条只有官网/地址的展商线索，它们
            # 字段填得多（质量分 50~75）却没有一个号码，按原排序会把真正能外呼的
            # 公司挤出第一页。相关度检索不受这里影响（填了词仍按 BM25）。
            sql += (" ORDER BY (COALESCE(c.flags, 0) <> 0) DESC,"
                    " c.quality DESC, c.source_ts DESC LIMIT ? OFFSET ?")
            params.extend([max(1, int(limit)), max(0, int(offset))])
        try:
            conn = self._connect()
            return conn.execute(sql, params).fetchall()
        except Exception as e:
            self._last_error = f"查询失败: {e}"
            return []

    def count(self, query: str = "", regions: Optional[Sequence[str]] = None,
              filters: Optional[dict] = None) -> Optional[int]:
        """命中条数（分页/导出前的「共 N 条」，不必把记录读进内存）。

        这是**精确**计数：宽词在百万级里要为它扫完全部命中（实测 100 万行、
        22.4 万命中 = 2.08s）。界面文案那两个总数改用 count_capped()，
        导出/分页这类必须知道真实条数的地方继续用这里。
        """
        if not self.readable():
            return None
        expr = self._resolve_expr(query, regions, filters)
        clauses, params = self._where(regions, filters, expr)
        sql = "SELECT count(*) FROM company c"
        if expr:
            sql += " JOIN company_fts ON company_fts.rowid = c.id"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        try:
            return int(self._connect().execute(sql, params).fetchone()[0])
        except Exception as e:
            self._last_error = f"计数失败: {e}"
            return None

    def count_capped(self, query: str = "",
                     regions: Optional[Sequence[str]] = None,
                     filters: Optional[dict] = None,
                     cap: Optional[int] = None) -> Optional[Tuple[int, bool]]:
        """封顶计数，返回 (条数, 是否精确)——Elasticsearch track_total_hits 的做法。

        只数到 cap 就收尾（SQL 里 LIMIT cap+1），百万级宽词从秒级降到几十毫秒。
        超过 cap 时返回 (cap, False)：**调用方必须把「以上」说出来**，把封顶值
        当精确值报就是报假数。视图不可用返回 None（与 count 同一口径，调用方
        据此回退，绝不把 None 当 0）。
        cap 省略时在调用点读 COUNT_CAP（写成默认参数会在 def 时绑定常量，测试
        改常量就没用了）。
        """
        if not self.readable():
            return None
        cap = max(1, int(COUNT_CAP if cap is None else cap))
        expr = self._resolve_expr(query, regions, filters)
        clauses, params = self._where(regions, filters, expr)
        inner = "SELECT 1 FROM company c"
        if expr:
            inner += " JOIN company_fts ON company_fts.rowid = c.id"
        if clauses:
            inner += " WHERE " + " AND ".join(clauses)
        inner += " LIMIT ?"
        sql = "SELECT count(*) FROM (" + inner + ")"
        try:
            n = int(self._connect().execute(sql, params + [cap + 1]).fetchone()[0])
        except Exception as e:
            self._last_error = f"封顶计数失败: {e}"
            return None
        return (cap, False) if n > cap else (n, True)

    def iter_records(self, query: str = "", regions: Optional[Sequence[str]] = None,
                     filters: Optional[dict] = None,
                     page: int = 2000) -> Iterator[dict]:
        """流式遍历命中记录（导出全部结果用，恒定内存占用）。"""
        if not self.readable():
            return
        expr = self._resolve_expr(query, regions, filters)
        clauses, params = self._where(regions, filters, expr)
        sql = "SELECT c.payload FROM company c"
        if expr:
            sql += " JOIN company_fts ON company_fts.rowid = c.id"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY c.id"
        try:
            cur = self._connect().execute(sql, params)
        except Exception as e:
            self._last_error = f"遍历失败: {e}"
            return
        while True:
            chunk = cur.fetchmany(max(100, int(page)))
            if not chunk:
                break
            for (payload,) in chunk:
                try:
                    yield json.loads(payload)
                except (TypeError, ValueError):
                    continue

    def facets(self, column: str, query: str = "",
               regions: Optional[Sequence[str]] = None,
               filters: Optional[dict] = None, limit: int = 60) -> List[Tuple[str, int]]:
        """聚合下压：某一维度的取值分布（筛选项/概览卡片，全表扫在库内完成）。"""
        if column not in _FACET_COLUMNS:
            raise ValueError(f"不支持的聚合列: {column}")
        if not self.readable():
            return []
        expr = self._resolve_expr(query, regions, filters)
        clauses, params = self._where(regions, filters, expr)
        sql = f"SELECT c.{column} AS v, count(*) FROM company c"
        if expr:
            sql += " JOIN company_fts ON company_fts.rowid = c.id"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " GROUP BY v ORDER BY count(*) DESC LIMIT ?"
        params.append(int(limit))
        try:
            rows = self._connect().execute(sql, params).fetchall()
        except Exception as e:
            self._last_error = f"聚合失败: {e}"
            return []
        return [((str(v) if v else ""), int(n)) for v, n in rows if v]

    def stats(self) -> Dict[str, Any]:
        """全库概览：几条 group by 在库内完成，不把任何记录读进 Python。

        键名与 DataPoolManager.get_master_stats 对齐：
          users / source_users 是**上传者**去重（记录里的 source_user），
          sources 是**数据来源**去重（网站/文件名）——两者语义不同，
          历史上本地统计把 source 当用户数报给界面，显示成"N 位用户"是错的。
        """
        if not self.readable():
            return {}
        out: Dict[str, Any] = {}
        try:
            conn = self._connect()
            out["total"] = conn.execute("SELECT count(*) FROM company").fetchone()[0]
            out["region"] = {str(v): int(n) for v, n in conn.execute(
                "SELECT region, count(*) FROM company GROUP BY region")}
            # 联系方式覆盖度：只有官网/地址的「线索行」和能直接外呼的行必须分开报。
            # 池子从 6 万涨到 8 万时带进来 1.3 万条线索行，只报总数会让用户以为
            # 「8 万家都能打电话」，实际差着一截。判据与 flags 位、apply_filters 同源。
            out["reachable"] = conn.execute(
                "SELECT count(*) FROM company WHERE COALESCE(flags, 0) <> 0").fetchone()[0]
            out["lead_only"] = out["total"] - out["reachable"]
            out["mobile"] = conn.execute(
                "SELECT count(*) FROM company WHERE flags & ? = ?",
                (FLAG_MOBILE, FLAG_MOBILE)).fetchone()[0]
            out["email"] = conn.execute(
                "SELECT count(*) FROM company WHERE flags & ? = ?",
                (FLAG_EMAIL, FLAG_EMAIL)).fetchone()[0]
            out["wechat"] = conn.execute(
                "SELECT count(*) FROM company WHERE flags & ? = ?",
                (FLAG_WECHAT, FLAG_WECHAT)).fetchone()[0]
            out["landline"] = conn.execute(
                "SELECT count(*) FROM company WHERE flags & ? = ?",
                (FLAG_PHONE, FLAG_PHONE)).fetchone()[0]
            out["industries"] = [(str(v), int(n)) for v, n in conn.execute(
                "SELECT industry, count(*) FROM company WHERE industry<>''"
                " GROUP BY industry ORDER BY count(*) DESC LIMIT 30")]
            out["provinces"] = [(str(v), int(n)) for v, n in conn.execute(
                "SELECT province, count(*) FROM company WHERE province<>''"
                " GROUP BY province ORDER BY count(*) DESC LIMIT 60")]
            out["countries"] = [(str(v), int(n)) for v, n in conn.execute(
                "SELECT country, count(*) FROM company WHERE country<>''"
                " GROUP BY country ORDER BY count(*) DESC LIMIT 60")]
            out["sources"] = [(str(v), int(n)) for v, n in conn.execute(
                "SELECT source, count(*) FROM company WHERE source<>''"
                " GROUP BY source ORDER BY count(*) DESC LIMIT 60")]
            # 来源**总类数**（sources 只给前 60 名，界面要报"共 N 个来源"）
            out["sources_count"] = conn.execute(
                "SELECT count(DISTINCT source) FROM company WHERE source<>''"
            ).fetchone()[0]
            out["source_users"] = [str(v) for (v,) in conn.execute(
                "SELECT DISTINCT source_user FROM company WHERE source_user<>''"
                " ORDER BY source_user")]
            out["users"] = len(out["source_users"])
            out["last_update"] = conn.execute(
                "SELECT max(source_time) FROM company").fetchone()[0] or ""
        except Exception as e:
            self._last_error = f"统计失败: {e}"
        return out

    def find_company(self, q_norm: str, q_lower: str) -> Optional[dict]:
        """公司名点查（精确 → 前缀 → 包含），替代 detect_company_name 的三次全表扫描。

        优先级与 Python 版严格一致；空 q_norm 不做精确/前缀匹配
        （"公司"这类通用词规范化后为空，命中脏记录是错的）。
        """
        if not self.readable():
            return None
        try:
            conn = self._connect()
            if q_norm:
                row = conn.execute("SELECT payload FROM company WHERE company_norm=?",
                                   (q_norm,)).fetchone()
                if row:
                    return json.loads(row[0])
                if len(q_norm) >= 3:
                    row = conn.execute(
                        "SELECT payload FROM company WHERE company_norm >= ?"
                        " AND company_norm < ? ORDER BY company_norm LIMIT 1",
                        (q_norm, q_norm + "\uffff")).fetchone()
                    if row:
                        return json.loads(row[0])
            if len(q_lower) >= 4:
                row = conn.execute(
                    "SELECT payload FROM company WHERE lower(company) LIKE ?"
                    " ESCAPE '\\' LIMIT 1", (self._like(q_lower),)).fetchone()
                if row:
                    return json.loads(row[0])
        except Exception as e:
            self._last_error = f"公司名点查失败: {e}"
        return None

    def get(self, company_norm: str) -> Optional[dict]:
        if not company_norm or not self.readable():
            return None
        try:
            row = self._connect().execute(
                "SELECT payload FROM company WHERE company_norm=?",
                (company_norm,)).fetchone()
            return json.loads(row[0]) if row else None
        except Exception:
            return None

    def norms(self) -> List[str]:
        """全部 company_norm（一致性自检/集合 diff 用，只走覆盖索引）。"""
        if not self.readable():
            return []
        try:
            return [str(n) for (n,) in self._connect().execute(
                "SELECT company_norm FROM company ORDER BY id")]
        except Exception as e:
            self._last_error = f"取 norm 列表失败: {e}"
            return []

    def drop(self):
        """删掉视图文件（数据目录迁移/一致性自检失败时重建前的清理）。"""
        self.close()
        for suffix in ("", "-wal", "-shm"):
            p = Path(str(self._path) + suffix)
            try:
                if p.exists():
                    p.unlink()
            except OSError:
                pass
        self.invalidate_meta_cache()
