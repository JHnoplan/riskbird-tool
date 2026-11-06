# -*- coding: utf-8 -*-
"""海洋展 / 台球展 / 深海装备 — 三文件批量清洗，输出客户库导入模板格式。

每个源文件 → 3 个输出：
  - 国内：全部境内记录（含手机 + 固话 + 邮箱）
  - 国内只留手机号：仅保留至少一条中国大陆手机号的记录，且手机号列只放中国手机
  - 国外：全部境外记录

核心原则：
  1. 手机号 / 固话严格分列 —— 中国手机号 (1[3-9]\\d{9}) 进手机号列，
     座机 / 400 / 800 / 境外号码进固话列，绝不混淆。
  2. 国内外判定：国家/地区字段 > 电话归属区域 > 中国手机号兜底。
  3. 所有号码必须通过 libphonenumber is_valid_number 校验，伪号不入库。
"""
import argparse
import collections
import datetime as dt
import os
import sys

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font

from enf_clean.normalize import (
    clean_company_name,
    country_to_region,
    extract_emails,
    guess_regions,
    normalize_text,
)
from enf_clean.phone import Audit, parse_phone_cell

# ─────────────────────────────────────────────────────────────────────────────
# 路径配置（可通过命令行参数覆盖）
# ─────────────────────────────────────────────────────────────────────────────
DEFAULT_CONFIG = {
    "base_dir": os.path.expanduser("~/PyCharmMiscProject/output"),
    "ocean_src": os.path.expanduser("~/Downloads/海洋展11.5数据.xlsx"),
    "billiard_src": os.path.expanduser("~/Downloads/台球展11.5数据.xlsx"),
    "deepsea_src": os.path.expanduser("~/Downloads/深海装备四关键词数据.xlsx"),
}

HEADER = [
    "客户名称", "企业名称", "岗位", "手机号", "固话", "邮箱", "微信号", "数据来源", "外呼备注",
    "客户名称", "企业名称", "岗位", "手机号", "固话", "邮箱", "微信号", "数据来源", "备注",
]


# ─────────────────────────────────────────────────────────────────────────────
# 通用工具
# ─────────────────────────────────────────────────────────────────────────────

def join_csv(values):
    return ",".join(v for v in values if v)


def dedupe(values):
    out, seen = [], set()
    for v in values:
        k = v.lower()
        if k not in seen:
            seen.add(k)
            out.append(v)
    return out


def build_remark(max_len=1200, **pairs):
    parts = []
    for label, val in pairs.items():
        val = normalize_text(val)
        if val and val.lower() not in ("nan", "null", "none"):
            parts.append(f"{label}:{val}")
    return " | ".join(parts)[:max_len]


def parse_phones(raw, hints, audit):
    """统一电话解析入口：返回 (mobiles, landlines)。"""
    text = normalize_text(raw) if raw else ""
    if not text or text.lower() in ("nan", "null", "none"):
        return [], []
    mobiles, landlines = parse_phone_cell(text, hints, audit)
    mobiles = [m for m in mobiles if m and m.display]
    landlines = [l for l in landlines if l and l.display]
    return mobiles, landlines


def safe_str(val):
    """将 pandas 值安全转为字符串，nan/None 返回 None。"""
    if val is None or pd.isna(val):
        return None
    text = str(val).strip()
    return text if text and text.lower() not in ("nan", "null", "none") else None


def build_record(row, mobiles, landlines, emails, company_name, remark):
    """构造一条模板格式记录。"""
    if not mobiles and not landlines and not emails:
        return None
    name = clean_company_name(company_name)
    if not name:
        return None
    return {
        "客户名称": "",
        "企业名称": name,
        "岗位": "",
        "手机号": join_csv(h.display for h in mobiles),
        "固话": join_csv(h.display for h in landlines),
        "邮箱": join_csv(emails),
        "微信号": "",
        "数据来源": row["_src_tag"],
        "外呼备注": remark,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 国内外判定
# ─────────────────────────────────────────────────────────────────────────────

def is_domestic(country_raw, mobiles, landlines, hints):
    """
    判定优先级：
      1. 国家/地区字段明确映射 → 按字段走
      2. 国家/地区字段非空但无法映射 → 不直接判境外，继续看电话/地址信号
      3. 国家/地区为空 → 看电话归属区域（仅当有真实信号时，不使用 CN 兆底）
      4. 仍无法判定 → 有中国手机号则境内，否则按境外处理
    """
    country = safe_str(country_raw)
    if country:
        region = country_to_region(country)
        if region:
            return region == "CN"
        # 国家字段非空但无法映射 → 不直接判境外，继续分析电话/地址信号
        # （数据源中可能是城市名、地址碎片等，不代表真的是外国企业）

    # 从电话归属区域判断（看主信号）
    phone_digits = join_csv(h.display for h in mobiles + landlines)
    if phone_digits and hints:
        # 仅当 hints 里有非兆底信号时才用 hints 判定
        # （guess_regions 在所有信号为空时会默认追加 "CN"，这个兆底不能用来判定国内）
        real_hints = [h for h in hints if h != "CN" or len(hints) == 1]
        if real_hints and real_hints[0] != "CN":
            return False
        if real_hints and real_hints[0] == "CN" and len(hints) > 1:
            return True

    # 无电话线索或无线索：有中国手机号 → 境内
    if any(m.region == "CN" for m in mobiles):
        return True

    # 最终兆底：无国家、无电话、无手机 → 境外
    return False


# ─────────────────────────────────────────────────────────────────────────────
# 文件写出
# ─────────────────────────────────────────────────────────────────────────────

def _write_xlsx(records, path):
    wb = Workbook()
    ws = wb.active
    ws.title = "客户库"
    ws.append(HEADER)
    for cell in ws[1]:
        cell.font = Font(bold=True)
        cell.alignment = Alignment(horizontal="center", vertical="center")
    for rec in records:
        block = [
            rec["客户名称"] or None, rec["企业名称"], rec["岗位"] or None,
            rec["手机号"] or None, rec["固话"] or None, rec["邮箱"] or None,
            rec["微信号"] or None, rec["数据来源"], rec["外呼备注"] or None,
        ]
        ws.append(block + block)
    widths = {
        "A": 12, "B": 34, "C": 12, "D": 24, "E": 24, "F": 30, "G": 12, "H": 20, "I": 44,
        "J": 12, "K": 34, "L": 12, "M": 24, "N": 24, "O": 30, "P": 12, "Q": 20, "R": 44,
    }
    for col, w in widths.items():
        ws.column_dimensions[col].width = w
    ws.row_dimensions[1].height = 24
    ws.freeze_panes = "A2"
    wb.save(path)


def write_files(records, out_dir, base_name, stamp):
    """写出 3 个文件：国内、国内只留手机号、国外。返回路径列表。"""
    domestic = [r for r in records if r["_domestic"]]
    foreign = [r for r in records if not r["_domestic"]]
    domestic_mobile = [
        r for r in domestic
        if any(m.region == "CN" for m in r["_mobiles"])
    ]

    os.makedirs(out_dir, exist_ok=True)
    paths = []

    # 国内
    p = os.path.join(out_dir, f"{base_name}_国内_{stamp}.xlsx")
    _write_xlsx(domestic, p)
    paths.append(("国内", p, len(domestic)))

    # 国内只留手机号
    p = os.path.join(out_dir, f"{base_name}_国内只留手机号_{stamp}.xlsx")
    _write_xlsx(domestic_mobile, p)
    paths.append(("国内只留手机号", p, len(domestic_mobile)))

    # 国外
    p = os.path.join(out_dir, f"{base_name}_国外_{stamp}.xlsx")
    _write_xlsx(foreign, p)
    paths.append(("国外", p, len(foreign)))

    return paths


# ─────────────────────────────────────────────────────────────────────────────
# 海洋展
# ─────────────────────────────────────────────────────────────────────────────

def process_ocean(audit, stats, cfg):
    src = cfg["ocean_src"]
    tag = os.path.basename(src).replace(".xlsx", "")
    print(f"\n{'='*70}")
    print(f"处理：{tag}")
    print(f"{'='*70}")

    df = pd.read_excel(src, dtype=str).where(pd.notna, None)
    records = []

    for i, row in enumerate(df.to_dict("records"), 1):
        if i % 500 == 0:
            print(f"  进度 {i}/{len(df)}", file=sys.stderr)

        country = safe_str(row.get("国家/地区"))
        address = safe_str(row.get("地址"))
        website = safe_str(row.get("官网"))

        # 邮箱（含电话列误写）
        email_cell, _ = extract_emails(row.get("邮箱"))
        phone_text = normalize_text(row.get("电话")) if row.get("电话") else ""
        emails_from_phone, phone_text_clean = extract_emails(phone_text)
        emails = dedupe(email_cell + emails_from_phone)
        stats["email_rescued"] += len(emails_from_phone)

        # 区域线索
        hints = guess_regions(country, website, address, row.get("公司名称"), emails)

        # 电话
        mobiles, landlines = parse_phones(phone_text_clean, hints, audit)
        stats["no_phone"] += int(not mobiles and not landlines)

        rec = build_record(
            dict(row, _src_tag=tag), mobiles, landlines, emails,
            row.get("公司名称"),
            build_remark(
                官网=website, 国家=country, 地址=address,
                行业分类=safe_str(row.get("行业分类")),
                精准度=safe_str(row.get("精准度分级")),
                来源=safe_str(row.get("来源展会/名录")),
            ),
        )
        if rec is None:
            stats["dropped"] += 1
            continue

        rec["_domestic"] = is_domestic(country, mobiles, landlines, hints)
        rec["_mobiles"] = mobiles
        records.append(rec)

        stats["with_mobile"] += int(bool(mobiles))
        stats["with_landline"] += int(bool(landlines))
        stats["with_email"] += int(bool(emails))
        stats["mobile_total"] += len(mobiles)
        stats["landline_total"] += len(landlines)
        stats["email_total"] += len(emails)

    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = os.path.join(cfg["base_dir"], "海洋展")
    paths = write_files(records, out_dir, "海洋展", stamp)

    domestic_n = sum(c for _, _, c in paths if "国内" in _ and "手机" not in _)
    mobile_n = sum(c for _, _, c in paths if "手机" in _)
    foreign_n = sum(c for _, _, c in paths if "国外" in _)
    print(f"  总行 {len(df)} → 有效 {len(records)} | 丢弃 {stats['dropped']}")
    print(f"  国内 {domestic_n} | 国内(只留手机) {mobile_n} | 国外 {foreign_n}")
    print(f"  手机号 {stats['mobile_total']} | 固话 {stats['landline_total']} | 邮箱 {stats['email_total']}")
    for label, p, c in paths:
        print(f"  [{label}] {p}  ({c} 行)")
    return paths


# ─────────────────────────────────────────────────────────────────────────────
# 台球展
# ─────────────────────────────────────────────────────────────────────────────

def process_billiard(audit, stats, cfg):
    src = cfg["billiard_src"]
    tag = os.path.basename(src).replace(".xlsx", "")
    print(f"\n{'='*70}")
    print(f"处理：{tag}")
    print(f"{'='*70}")

    df = pd.read_excel(src, dtype=str).where(pd.notna, None)
    # 规范化列名（去除前导空白）
    df.columns = [c.strip() for c in df.columns]
    records = []

    for i, row in enumerate(df.to_dict("records"), 1):
        if i % 500 == 0:
            print(f"  进度 {i}/{len(df)}", file=sys.stderr)

        country = safe_str(row.get("国家/地区"))
        city = safe_str(row.get("城市"))
        address = safe_str(row.get("地址"))
        website = safe_str(row.get("官网"))

        # 邮箱
        email_cell, _ = extract_emails(row.get("邮箱"))
        # 合并所有电话来源（手机号列 + 固话列 + 合计列），统一重新解析
        raw_mobile = normalize_text(row.get("手机号")) if row.get("手机号") else ""
        raw_landline = normalize_text(row.get("固话")) if row.get("固话") else ""
        raw_combined = normalize_text(row.get("电话合计")) if row.get("电话合计") else ""
        # 用合计列作为主来源（包含手机号列和固话列的全部内容）
        phone_text = raw_combined if raw_combined else f"{raw_mobile},{raw_landline}"
        # 从电话文本中抢救邮箱
        emails_from_phone, phone_text_clean = extract_emails(phone_text)
        emails = dedupe(email_cell + emails_from_phone)
        stats["email_rescued"] += len(emails_from_phone)

        # 区域线索
        company_name = safe_str(row.get("公司名")) or safe_str(row.get("英文名"))
        hints = guess_regions(country, website, address, company_name, emails)

        # 电话
        mobiles, landlines = parse_phones(phone_text_clean, hints, audit)
        stats["no_phone"] += int(not mobiles and not landlines)

        rec = build_record(
            dict(row, _src_tag=tag), mobiles, landlines, emails,
            company_name,
            build_remark(
                官网=website, 国家=country, 城市=city, 地址=address,
                品类=safe_str(row.get("品类/产品")),
                来源展会=safe_str(row.get("来源展会")),
            ),
        )
        if rec is None:
            stats["dropped"] += 1
            continue

        rec["_domestic"] = is_domestic(country, mobiles, landlines, hints)
        rec["_mobiles"] = mobiles
        records.append(rec)

        stats["with_mobile"] += int(bool(mobiles))
        stats["with_landline"] += int(bool(landlines))
        stats["with_email"] += int(bool(emails))
        stats["mobile_total"] += len(mobiles)
        stats["landline_total"] += len(landlines)
        stats["email_total"] += len(emails)

    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = os.path.join(cfg["base_dir"], "台球展")
    paths = write_files(records, out_dir, "台球展", stamp)

    domestic_n = sum(c for _, _, c in paths if "国内" in _ and "手机" not in _)
    mobile_n = sum(c for _, _, c in paths if "手机" in _)
    foreign_n = sum(c for _, _, c in paths if "国外" in _)
    print(f"  总行 {len(df)} → 有效 {len(records)} | 丢弃 {stats['dropped']}")
    print(f"  国内 {domestic_n} | 国内(只留手机) {mobile_n} | 国外 {foreign_n}")
    print(f"  手机号 {stats['mobile_total']} | 固话 {stats['landline_total']} | 邮箱 {stats['email_total']}")
    for label, p, c in paths:
        print(f"  [{label}] {p}  ({c} 行)")
    return paths


# ─────────────────────────────────────────────────────────────────────────────
# 深海装备
# ─────────────────────────────────────────────────────────────────────────────

def process_deepsea(audit, stats, cfg):
    src = cfg["deepsea_src"]
    tag = os.path.basename(src).replace(".xlsx", "")
    print(f"\n{'='*70}")
    print(f"处理：{tag}")
    print(f"{'='*70}")

    # 处理两个 sheet：有效数据 + 仅官网待补
    sheets = pd.read_excel(src, sheet_name=None, dtype=str)
    records = []

    for sheet_name in ("有效数据", "仅官网待补"):
        if sheet_name not in sheets:
            continue
        df = sheets[sheet_name].where(pd.notna, None)
        print(f"  Sheet [{sheet_name}]: {len(df)} 行")

        for i, row in enumerate(df.to_dict("records"), 1):
            if i % 500 == 0:
                print(f"    进度 {i}/{len(df)}", file=sys.stderr)

            country = safe_str(row.get("国家/地区"))
            address = safe_str(row.get("地址"))
            website = safe_str(row.get("官网"))

            # 邮箱
            email_cell, _ = extract_emails(row.get("邮箱"))
            phone_text = normalize_text(row.get("电话")) if row.get("电话") else ""
            emails_from_phone, phone_text_clean = extract_emails(phone_text)
            emails = dedupe(email_cell + emails_from_phone)
            stats["email_rescued"] += len(emails_from_phone)

            # 区域线索
            hints = guess_regions(country, website, address, row.get("公司名称"), emails)

            # 电话
            mobiles, landlines = parse_phones(phone_text_clean, hints, audit)
            stats["no_phone"] += int(not mobiles and not landlines)

            rec = build_record(
                dict(row, _src_tag=tag), mobiles, landlines, emails,
                row.get("公司名称"),
                build_remark(
                    官网=website, 国家=country, 地址=address,
                    层级=safe_str(row.get("层级")),
                    品类=safe_str(row.get("品类命中")),
                    来源=safe_str(row.get("来源渠道")),
                ),
            )
            if rec is None:
                stats["dropped"] += 1
                continue

            rec["_domestic"] = is_domestic(country, mobiles, landlines, hints)
            rec["_mobiles"] = mobiles
            records.append(rec)

            stats["with_mobile"] += int(bool(mobiles))
            stats["with_landline"] += int(bool(landlines))
            stats["with_email"] += int(bool(emails))
            stats["mobile_total"] += len(mobiles)
            stats["landline_total"] += len(landlines)
            stats["email_total"] += len(emails)

    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = os.path.join(cfg["base_dir"], "深海装备")
    paths = write_files(records, out_dir, "深海装备", stamp)

    domestic_n = sum(c for _, _, c in paths if "国内" in _ and "手机" not in _)
    mobile_n = sum(c for _, _, c in paths if "手机" in _)
    foreign_n = sum(c for _, _, c in paths if "国外" in _)
    print(f"  有效记录 {len(records)} | 丢弃 {stats['dropped']}")
    print(f"  国内 {domestic_n} | 国内(只留手机) {mobile_n} | 国外 {foreign_n}")
    print(f"  手机号 {stats['mobile_total']} | 固话 {stats['landline_total']} | 邮箱 {stats['email_total']}")
    for label, p, c in paths:
        print(f"  [{label}] {p}  ({c} 行)")
    return paths


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="展会数据批量清洗 → 客户库导入模板")
    parser.add_argument("--base-dir", default=DEFAULT_CONFIG["base_dir"],
                        help="输出根目录（默认 ~/PyCharmMiscProject/output）")
    parser.add_argument("--ocean", default=DEFAULT_CONFIG["ocean_src"],
                        help="海洋展源文件路径")
    parser.add_argument("--billiard", default=DEFAULT_CONFIG["billiard_src"],
                        help="台球展源文件路径")
    parser.add_argument("--deepsea", default=DEFAULT_CONFIG["deepsea_src"],
                        help="深海装备源文件路径")
    args = parser.parse_args()

    cfg = {
        "base_dir": args.base_dir,
        "ocean_src": args.ocean,
        "billiard_src": args.billiard,
        "deepsea_src": args.deepsea,
    }
    os.makedirs(cfg["base_dir"], exist_ok=True)

    start = dt.datetime.now()
    all_paths = []

    # 1) 海洋展
    audit1 = Audit()
    stats1 = collections.Counter()
    all_paths += process_ocean(audit1, stats1, cfg)
    print(f"  审计:\n{audit1}")

    # 2) 台球展
    audit2 = Audit()
    stats2 = collections.Counter()
    all_paths += process_billiard(audit2, stats2, cfg)
    print(f"  审计:\n{audit2}")

    # 3) 深海装备
    audit3 = Audit()
    stats3 = collections.Counter()
    all_paths += process_deepsea(audit3, stats3, cfg)
    print(f"  审计:\n{audit3}")

    elapsed = (dt.datetime.now() - start).total_seconds()

    # 汇总
    print(f"\n{'='*70}")
    print(f"全部完成！耗时 {elapsed:.1f}s")
    print(f"输出根目录：{cfg['base_dir']}")
    print(f"{'='*70}")
    for label, path, count in all_paths:
        print(f"  [{label}] {count} 行 → {path}")


if __name__ == "__main__":
    main()
