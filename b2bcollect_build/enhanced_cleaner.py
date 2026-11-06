# -*- coding: utf-8 -*-
"""增强清洗模块：基于 enf_clean（libphonenumber + 多层区域推断）把外部名单洗成三份文件。

输出：
  - 国内：全部境内记录（手机 + 固话 + 邮箱全保留）
  - 国内只留手机号：境内记录中至少有一条中国手机号的，手机号列只放中国手机
  - 国外：全部境外记录

技术栈：
  · 电话解析：Google libphonenumber（enf_clean.phone），通过 is_valid_number 硬门槛
  · 区域推断：enf_clean.normalize.guess_regions，五层优先级
    国家字段 > 官网域名TLD > 邮箱域名 > 地址关键词/文字系统 > 公司名后缀
  · 号码分类：中国手机号 1[3-9]\\d{9} → mobile；libphonenumber MOBILE 类型 → mobile；
    固话/400/800/境外无法确认手机 → landline；传真一律丢弃
  · 与现有 customer_library_cleaner 管线无缝衔接：列匹配 / 合并口径完全复用，
    只替换电话解析与区域判定环节

与现有 split_phones 的关系：
  现有正则拆分作为兜底（处理分组书写 139 0806 0502、区号空格连写等中国特色格式），
  enf_clean libphonenumber 解析作为增强（处理国际前缀、多语言标签、脏格式修复等），
  两者取并集，绝不丢号。
"""
import collections
import datetime as dt
import os
import re
import sys

# ── enf_clean 核心模块（项目根目录下 enf_clean/ 包）──
# 容错导入：如果 enf_clean 不在 sys.path 里（比如从 b2bcollect_build/ 目录直接运行），
# 自动把项目根目录加进去，确保打包态（PyInstaller）也能找到。
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from enf_clean.normalize import (
    normalize_text, extract_emails, guess_regions,
    country_to_region, clean_company_name,
)
from enf_clean.phone import Audit, parse_phone_cell

# ── 复用现有清洗管线（列匹配 / 合并 / 模板行构建）──
import customer_library_cleaner as _cleaner

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill, Border, Side

# 18 列双区块表头（与《客户库数据导入模板》完全一致）
HEADER = [
    "客户名称", "企业名称", "岗位", "手机号", "固话", "邮箱", "微信号", "数据来源", "外呼备注",
    "客户名称", "企业名称", "岗位", "手机号", "固话", "邮箱", "微信号", "数据来源", "备注",
]

# 中国手机号正则（与 yzd_client.PHONE_RE 同一口径）
PHONE_RE = re.compile(r"^1[3-9]\d{9}$")


# ─────────────────────────────────────────────────────────────────────────────
#  辅助函数
# ─────────────────────────────────────────────────────────────────────────────

# ── 外国公司名后缀：分强/弱两级（词边界匹配，避免误匹配子串）──
# 强外国后缀：明确非中国企业的法律形式（中国公司不会用 Inc./LLC/GmbH 注册）
# 去掉 ab/as/ag/oy/mbh/sa 等 2~3 字母缩写——常见英文单词/缩写，误匹配率高。
_STRONG_FOREIGN_SUFFIX_PATTERNS = [
    "gmbh", r"s\.a\.r\.l", r"s\.r\.l", r"s\.p\.a",
    "llc", "inc", "corp",
    r"b\.v", r"n\.v", r"s\.r\.o",
    r"sp\. z o\.o", "a/s",
    "pty", "pte", "kk", "株式会社", "pvt",
]
_STRONG_FOREIGN_SUFFIX_RE = re.compile(
    r"(?<!\w)(?:" + "|".join(_STRONG_FOREIGN_SUFFIX_PATTERNS) + r")(?!\w)",
    re.IGNORECASE,
)

# 弱外国后缀：Ltd./Corp./PLC 等——中国出口企业英文名也常用（"Co., Ltd."）
# 仅当无中国手机号时作为辅助信号，不单独作为判外依据。
_WEAK_FOREIGN_SUFFIX_PATTERNS = [
    "ltd", "plc",
]
_WEAK_FOREIGN_SUFFIX_RE = re.compile(
    r"(?<!\w)(?:" + "|".join(_WEAK_FOREIGN_SUFFIX_PATTERNS) + r")(?!\w)",
    re.IGNORECASE,
)

# 向后兼容：合并正则（供 _is_foreign_suffix 使用）
_FOREIGN_SUFFIX_RE = re.compile(
    r"(?<!\w)(?:" + "|".join(
        _STRONG_FOREIGN_SUFFIX_PATTERNS + _WEAK_FOREIGN_SUFFIX_PATTERNS
    ) + r")(?!\w)",
    re.IGNORECASE,
)

# 垃圾公司名模式：图片路径 / 文件路径 / 脚本残留
_JUNK_NAME_PATTERNS = re.compile(
    r"^/uploads/"           # 典型爬虫图片路径 /uploads/medium_xxx.png
    r"|^https?://"          # URL
    r"|\.png$|\.jpg$|\.jpeg$|\.gif$|\.svg$|\.webp$"  # 图片扩展名
    r"|^<img\s"             # HTML img 标签残留
    r"|^<script"            # 脚本标签残留
    r"|\bonerror\b"         # 事件属性残留
    , re.IGNORECASE,
)


def _is_foreign_suffix(company_name):
    """公司名是否含外国法律形式后缀（强/弱合并检测）。"""
    return bool(_FOREIGN_SUFFIX_RE.search(company_name))


def _has_strong_foreign_suffix(company_name):
    """公司名是否含强外国后缀（Inc./LLC/GmbH/S.R.L.等，中国企业不会用）。"""
    return bool(_STRONG_FOREIGN_SUFFIX_RE.search(company_name))


def _has_weak_foreign_suffix(company_name):
    """公司名是否含弱外国后缀（Ltd./PLC，中国出口企业常用 Co., Ltd.）。"""
    return bool(_WEAK_FOREIGN_SUFFIX_RE.search(company_name))


def _is_junk_company_name(name):
    """公司名是否为垃圾数据（图片路径 / URL / HTML 残留）。"""
    if not name:
        return True
    return bool(_JUNK_NAME_PATTERNS.search(name))


def _build_remark(fields, max_len=180):
    """从原始字段构造外呼备注（官网/国家/地址/状态），供外呼人员参考。"""
    parts = []
    for label, key in (("官网", "官网"), ("国家", "国家"), ("地址", "地址"), ("状态", "状态")):
        val = _cleaner.normalize_text(fields.get(key, ""))
        if val and val.lower() not in ("nan", "null", "none", "存续", "在业", "开业", "正常"):
            parts.append(f"{label}:{val[:50]}")
    own = _cleaner.normalize_text(fields.get("备注", ""))
    if own:
        parts.append(own)
    return " | ".join(parts)[:max_len]


def _is_domestic(country_raw, enf_mobiles, enf_landlines, hints, company_name="",
                 address=None, all_emails=None):
    """国内外判定：多信号加权投票 + 强/弱外国后缀分层。

    设计思想（国际数据治理惯例）：
    1. 强外国后缀（Inc./LLC/GmbH/S.R.L./A/S/KK/株式会社等）是中国公司绝不会
       使用的法律形式。若无中国手机号，即使地址在中国也判境外——这些是中国
       企业的海外子公司或纯外国公司。
    2. 弱外国后缀（Ltd./PLC）在中国出口企业英文名中极常见（"Co., Ltd."），
       不作为独立判外依据，仅辅助投票。
    3. 中国手机号是铁证：外国公司不会有中国员工手机号，强制判境内。
    4. 直接检查地址/邮箱域名是否有 CN 证据，不依赖 hints 位置推断。
    """
    from enf_clean.normalize import _region_from_address, _region_from_emails

    country = _cleaner.safe_str(country_raw) if hasattr(_cleaner, 'safe_str') else \
        (country_raw.strip() if country_raw and str(country_raw).strip().lower() not in ("nan", "null", "none") else None)

    # ── 1) 国家/地区字段明确映射（最可靠信号，一票定音）──
    if country:
        region = country_to_region(country)
        if region:
            return region == "CN"

    # ── 2) 多信号加权投票 ──
    cn_score = 0
    foreign_score = 0

    has_cn_mobile = any(m.region == "CN" for m in enf_mobiles)
    has_cn_landline = any(l.region == "CN" for l in enf_landlines)

    # 中国手机号 → 强境内证据（外国公司几乎不会有中国员工手机号）
    if has_cn_mobile:
        cn_score += 3

    # 中国固话 → 弱境内证据（外国公司可能在中国设办事处有固话）
    if has_cn_landline:
        cn_score += 1

    # 公司名后缀信号（分强/弱两级）
    has_strong = company_name and _has_strong_foreign_suffix(company_name)
    has_weak = company_name and _has_weak_foreign_suffix(company_name)

    if has_strong:
        foreign_score += 3  # 强外国后缀：高权重
    elif has_weak:
        foreign_score += 1  # 弱外国后缀：低权重（中国出口企业常用）

    # hints 里的非 CN 信号（地址关键词 / 官网域名 / 邮箱域名给出国提示）
    non_cn_hints = [h for h in hints if h != "CN"]
    foreign_score += len(non_cn_hints)

    # 直接检查地址和邮箱域名是否有真实 CN 证据
    has_real_cn_evidence = (
        cn_score > 0
        or _region_from_address(address) == "CN"
        or _region_from_emails(all_emails or []) == "CN"
    )

    # ── 3) 综合判定 ──
    if cn_score >= 3:
        # 有中国手机号 → 境内（铁证，无论什么后缀）
        return True

    # ★ 强外国后缀 + 无中国手机号 → 境外
    # 中国公司不会用 Inc./LLC/GmbH 注册；这些是海外子公司或纯外国公司。
    # 即使地址在中国（如 "Trina Solar (US) Inc." 有上海地址），也判境外。
    if has_strong and not has_cn_mobile:
        return False

    if cn_score >= 2:
        # 中国固话 + 至少一个其他 CN hint → 境内
        return True

    if cn_score >= 1 and foreign_score <= 1:
        # 有中国固话但外国信号不多（≤1）→ 默认境内
        return True

    if foreign_score >= 2 and not has_real_cn_evidence:
        # 有外国信号（弱后缀+hints）且无任何中国电话证据 → 境外
        return False

    if cn_score == 0 and foreign_score >= 1 and not has_real_cn_evidence:
        # 无任何中国电话证据，有外国信号，无真实中国证据 → 境外
        return False

    # 最终兜底：中国 B2B 数据默认境内
    return True


def _safe_str(val):
    """pandas NaN / None → None，否则 strip 后返回。"""
    if val is None:
        return None
    text = str(val).strip()
    return text if text and text.lower() not in ("nan", "null", "none") else None


# ─────────────────────────────────────────────────────────────────────────────
#  主入口
# ─────────────────────────────────────────────────────────────────────────────

def enhanced_clean_three_files(paths, output_dir, log=print):
    """外部数据文件 → 列匹配 → 合并 → libphonenumber 深度清洗 → 三文件输出。

    返回 (result_dict, report_list)：
      result_dict: {"国内": (path, count), "国内只留手机号": (path, count), "国外": (path, count)}
      report_list: 与 clean_external_records 同格式的逐表报告
    """
    # ── 1) 现有管线：读文件 + 智能列匹配 + 记录归一化 ──
    records, report = _cleaner.clean_external_records(paths, log=log)
    bad = [r for r in report if r.get("error")]
    if not records:
        detail = "\n".join(f"· {r['file']}：{r['error']}" for r in bad) or "· 所选文件里没有数据行"
        log(f"⚠ 没读到任何数据：{detail}")
        return {}, report

    # ── 2) 按 (公司名, 联系人) 合并（外部名单口径：一家公司多个联系人不压成一条）──
    merged, dup, invalid = _cleaner.merge_external_records(records)
    log(f"📊 合并完成：{len(records)} 条原始 → {len(merged)} 条（重复 {dup}，无效 {invalid}）")

    # ── 3) 逐条深度清洗：enf_clean libphonenumber 电话解析 + 五层区域推断 ──
    audit = Audit()
    stats = collections.Counter()
    classified = []   # [(template_row_dict, is_domestic_bool, mobiles_list)]

    for item in merged:
        fields = item["fields"]
        sources = item["sources"]

        company = _cleaner._get_field(fields, "公司名")
        if not company:
            continue
        company = clean_company_name(company)
        if not company or _is_junk_company_name(company):
            stats["dropped_junk_name"] += 1
            continue

        website = _cleaner._get_field(fields, "官网")
        address = _cleaner._get_field(fields, "地址")
        country_raw = _cleaner._get_field(fields, "国家")
        # 不再用地址回退国家字段：地址含"海南""美国工业村"等省/地名时，
        # country_to_region 会误判为 CN/US 成为 _is_domestic 的强信号。
        # guess_regions 内部仍会通过 _region_from_address(address) 分析地址。

        # ── 邮箱（含从电话列抢救误写的邮箱）──
        email_cell, _ = extract_emails(_cleaner._get_field(fields, "邮箱"))
        phone_raw = _cleaner.normalize_text(_cleaner._get_field(fields, "电话"))
        emails_from_phone, phone_text = extract_emails(phone_raw)
        all_emails = []
        seen_emails = set()
        for e in email_cell + emails_from_phone:
            k = e.lower()
            if k not in seen_emails:
                seen_emails.add(k)
                all_emails.append(e)

        # ── 区域线索：国家 > 官网域名 > 邮箱域名 > 地址关键词/文字系统 > 公司名后缀 ──
        hints = guess_regions(country_raw, website, address, company, all_emails)

        # ── 电话解析：双引擎并集 ──
        # 引擎 A：现有正则拆分（处理分组书写、区号空格连写、+86 前缀等中国特色格式）
        std_mobiles, std_landlines = _cleaner.split_phones(_cleaner._get_field(fields, "电话"))

        # 引擎 B：enf_clean libphonenumber（处理国际号码、多语言标签、脏格式修复）
        enf_mobiles, enf_landlines = [], []
        if phone_text and phone_text.lower() not in ("nan", "null", "none"):
            enf_mobiles, enf_landlines = parse_phone_cell(phone_text, hints, audit)

        # 合并：标准结果打底，增强结果补充（按展示值去重）
        final_mobiles = list(std_mobiles)
        final_landlines = list(std_landlines)
        existing_display = set(final_mobiles) | set(final_landlines)
        for h in enf_mobiles:
            if h.display and h.display not in existing_display:
                final_mobiles.append(h.display)
                existing_display.add(h.display)
        for h in enf_landlines:
            if h.display and h.display not in existing_display:
                final_landlines.append(h.display)
                existing_display.add(h.display)

        # 防御性过滤：从 final_landlines 中剔除日期格式（YYYYMMDD / YYYYMMDDHHMMSS）
        final_landlines = [
            v for v in final_landlines
            if not re.match(r'^\d{8}(\d{6})?$', re.sub(r'\D', '', v))
               or not (1900 <= int(re.sub(r'\D', '', v)[:4]) <= 2100)
        ]

        # 四项联系方式全空 → 不入库（与现有 build_external_rows 同一口径）
        if not final_mobiles and not final_landlines and not all_emails:
            wechat = _cleaner.normalize_text(_cleaner._get_field(fields, "微信号"))
            if not wechat:
                stats["dropped_no_contact"] += 1
                continue

        # ── 构建模板行（复用现有 make_row 保证格式一致）──
        row = _cleaner.make_row(fields, sources)
        # 用增强解析结果覆盖电话/邮箱列（make_row 的结果作为兜底）
        row["企业名称"] = company
        row["手机号"] = ",".join(final_mobiles)
        row["固话"] = ",".join(final_landlines)
        if all_emails:
            row["邮箱"] = ",".join(all_emails)
        row["外呼备注"] = _build_remark(fields)
        row["备注"] = row["外呼备注"]

        # ── 国内外判定（公司名后缀作为无电话时的关键区分信号）──
        domestic = _is_domestic(
            country_raw, enf_mobiles, enf_landlines, hints,
            company_name=company, address=address, all_emails=all_emails,
        )

        classified.append((row, domestic, enf_mobiles))

        # ── 统计 ──
        stats["total"] += 1
        stats["with_mobile"] += int(bool(final_mobiles))
        stats["with_landline"] += int(bool(final_landlines))
        stats["with_email"] += int(bool(all_emails))
        stats["mobile_total"] += len(final_mobiles)
        stats["landline_total"] += len(final_landlines)
        stats["email_total"] += len(all_emails)
        stats["domestic"] += int(domestic)
        stats["foreign"] += int(not domestic)

    if not classified:
        log("⚠ 清洗后没有可入库的记录")
        return {}, report

    # ── 4) 分类输出：国内 / 国内只留手机号 / 国外 ──
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    os.makedirs(output_dir, exist_ok=True)

    domestic_rows = [row for row, dom, _ in classified if dom]
    # 「国内只留手机号」：检查最终写入「手机号」列的显示值（final_mobiles），
    # 而非仅 enf_mobiles——标准引擎找到的中国手机号也要算在内。
    domestic_mobile_rows = [
        row for row, dom, _ in classified
        if dom and any(PHONE_RE.search(t.strip()) for t in (row.get("手机号") or "").split(",") if t.strip())
    ]
    foreign_rows = [row for row, dom, _ in classified if not dom]

    result = {}
    for label, rows, suffix in [
        ("国内", domestic_rows, "国内"),
        ("国内只留手机号", domestic_mobile_rows, "国内只留手机号"),
        ("国外", foreign_rows, "国外"),
    ]:
        path = os.path.join(output_dir, f"清洗后_{suffix}_{stamp}.xlsx")
        _write_xlsx(rows, path, log)
        result[label] = (path, len(rows))

    # ── 5) 汇总日志 ──
    log(f"\n{'='*60}")
    log(f"🧹 增强清洗完成（libphonenumber + 五层区域推断）")
    log(f"   有效记录 {stats['total']} | 丢弃 {stats['dropped_no_contact']}（无任何联系方式）"
        f"{(' | 垃圾公司名 ' + str(stats.get('dropped_junk_name', 0)) + ' 条') if stats.get('dropped_junk_name') else ''}")
    log(f"   国内 {len(domestic_rows)} | 国内(只留手机号) {len(domestic_mobile_rows)} | 国外 {len(foreign_rows)}")
    log(f"   手机号 {stats['mobile_total']} | 固话 {stats['landline_total']} | 邮箱 {stats['email_total']}")
    log(f"   审计摘要：")
    for line in str(audit).split("\n")[:12]:
        log(f"     {line}")
    log(f"{'='*60}")

    return result, report


# ─────────────────────────────────────────────────────────────────────────────
#  Excel 写出（与现有 write_clean_excel 同一格式风格）
# ─────────────────────────────────────────────────────────────────────────────

def _write_xlsx(rows, filepath, log=print):
    """写出分类后的 Excel 文件，18 列双区块结构。"""
    wb = Workbook()
    ws = wb.active
    ws.title = "客户库"

    header_font = Font(bold=True, size=11, color="FFFFFF")
    header_fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
    thin = Border(*[Side(style="thin")] * 4)

    ws.append(HEADER)
    for cell in ws[1]:
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = thin

    widths = {
        "A": 16, "B": 34, "C": 12, "D": 24, "E": 24, "F": 30,
        "G": 12, "H": 16, "I": 44,
        "J": 16, "K": 34, "L": 12, "M": 24, "N": 24, "O": 30,
        "P": 12, "Q": 16, "R": 44,
    }
    for col_letter, w in widths.items():
        ws.column_dimensions[col_letter].width = w
    ws.row_dimensions[1].height = 24
    ws.freeze_panes = "A2"

    for row in rows:
        block = [
            row.get("客户名称") or None,
            row.get("企业名称", ""),
            row.get("岗位") or None,
            row.get("手机号") or None,
            row.get("固话") or None,
            row.get("邮箱") or None,
            row.get("微信号") or None,
            row.get("数据来源", ""),
            row.get("外呼备注") or None,
        ]
        ws.append(block + block)

    for r in range(2, ws.max_row + 1):
        for c in range(1, len(HEADER) + 1):
            ws.cell(row=r, column=c).alignment = Alignment(vertical="top", wrap_text=True)
            ws.cell(row=r, column=c).border = thin

    tmp = filepath + ".tmp"
    wb.save(tmp)
    os.replace(tmp, filepath)
    log(f"  📄 {os.path.basename(filepath)}（{len(rows)} 条）")


# ─────────────────────────────────────────────────────────────────────────────
#  离线自测
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import tempfile
    import pandas as pd

    tmp_dir = tempfile.mkdtemp(prefix="enhanced_clean_test_")
    test_xlsx = os.path.join(tmp_dir, "测试数据.xlsx")

    # 构造测试数据：含国内手机、国内固话、国外号码
    # 注意：不能加"国家"/"region"列（会被列匹配误映射为"联系人"）
    # 外国公司靠官网域名/邮箱/地址/公司名后缀识别
    df = pd.DataFrame([
        {"公司名": "深圳光能科技有限公司", "电话": "13800138000", "邮箱": "sales@guangneng.com",
         "官网": "www.guangneng.com", "地址": "深圳市南山区科技园"},
        {"公司名": "上海海洋设备有限公司", "电话": "021-58889999", "邮箱": "info@ocean-sh.com",
         "地址": "上海市浦东新区"},
        {"公司名": "German Solar GmbH", "电话": "+49 30 12345678", "邮箱": "info@germansolar.de",
         "官网": "www.germansolar.de", "地址": "Berlin, Germany"},
        {"公司名": "北京新能源集团", "电话": "010-88886666; 13912345678", "邮箱": "bj@xinnengyuan.com",
         "地址": "北京市朝阳区"},
        {"公司名": "Tokyo Marine Co., Ltd.", "电话": "+81-3-1234-5678",
         "邮箱": "info@tokyomarine.jp", "地址": "東京都中央区"},
        {"公司名": "空号测试公司", "电话": "", "邮箱": "", "地址": ""},
    ])
    df.to_excel(test_xlsx, index=False)

    print(f"测试文件：{test_xlsx}")
    print(f"输出目录：{tmp_dir}")
    print()

    result, report = enhanced_clean_three_files([test_xlsx], tmp_dir, log=print)

    print("\n── 结果验证 ──")
    for label, (path, count) in result.items():
        print(f"  {label}: {count} 条 → {os.path.basename(path)}")

    # 基本断言
    assert result["国内"][1] >= 2, f"国内应至少 2 条（深圳+上海+北京），实际 {result['国内'][1]}"
    assert result["国外"][1] >= 2, f"国外应至少 2 条（German+Tokyo），实际 {result['国外'][1]}"
    assert result["国内只留手机号"][1] >= 1, f"国内手机应至少 1 条，实际 {result['国内只留手机号'][1]}"
    print("\n✅ 全部断言通过")
