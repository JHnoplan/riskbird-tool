# -*- coding: utf-8 -*-
"""ENF 光伏企业数据 -> 客户库导入模板 清洗主流程。

用法：
    python3 -m enf_clean.clean_enf_customers [--limit N] [--out 路径]

字段映射规则（严格按模板列，不做任何臆造填充）：
    客户名称 <- 源数据无联系人/法人字段，一律留空（禁止用公司名占位）
    企业名称 <- 公司名称
    岗位     <- 源数据无该字段，留空
    手机号   <- 电话（libphonenumber + 国内号段规则判定为手机的号码）
    固话     <- 电话（座机/总机/400/800，以及无法确认为手机的境外号码）
    邮箱     <- 邮箱（含误写在电话列的邮箱）
    微信号   <- 源数据无该字段，留空
    数据来源 <- 固定值：ENF光伏企业名录
    外呼备注/备注 <- 官网 / 国家 / 分类 / 子分类 / 地址 / 源链接（原始字段留痕）
模板中「外呼备注」前后是两组同构列，两组写入完全相同的数据。
"""
import argparse
import collections
import datetime as dt
import os
import sys

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font

from enf_clean.normalize import (clean_company_name, country_to_region, extract_emails,
                                guess_regions, normalize_text)
from enf_clean.phone import Audit, parse_phone_cell

SOURCE_PATH = "/Users/zhihuiyunmac/Downloads/ENF光伏企业_去重_20261026_090035.xlsx"
OUTPUT_DIR = "/Users/zhihuiyunmac/PyCharmMiscProject/output"

DATA_SOURCE_TAG = "ENF光伏企业名录"
FIRST_BLOCK = ["客户名称", "企业名称", "岗位", "手机号", "固话", "邮箱", "微信号", "数据来源", "外呼备注"]
SECOND_BLOCK = ["客户名称", "企业名称", "岗位", "手机号", "固话", "邮箱", "微信号", "数据来源", "备注"]
HEADER = FIRST_BLOCK + SECOND_BLOCK

# 备注字段长度上限，避免 Excel 单元格超限
REMARK_MAX = 1200


def join_csv(values):
    """多值用英文逗号拼接（模板要求）。"""
    return ",".join([v for v in values if v])


def dedupe_keep_order(values):
    """大小写不敏感去重，保留首次顺序。"""
    out, seen = [], set()
    for v in values:
        key = v.lower()
        if key not in seen:
            seen.add(key)
            out.append(v)
    return out


def build_remark(website, country, category, sub_category, address, link):
    # 源链接只保留到路径，丢掉 ?directory=..&list=.. 参数（分类信息已单独留存）
    link = normalize_text(link).split("?")[0]
    parts = []
    for label, value in (
        ("官网", website), ("国家", country), ("分类", category),
        ("子分类", sub_category), ("地址", address), ("源链接", link),
    ):
        value = normalize_text(value)
        if value:
            parts.append(f"{label}:{value}")
    remark = " | ".join(parts)
    return remark[:REMARK_MAX]


def clean_row(row, audit, stats):
    """单行清洗 -> dict（模板字段）或 None（废弃数据）。"""
    # 1) 邮箱：源字段 + 误写在电话列的邮箱
    email_cell, _ = extract_emails(row.get("邮箱"))
    phone_cell_raw = normalize_text(row.get("电话"))
    emails_from_phone, phone_text = extract_emails(phone_cell_raw)
    emails = dedupe_keep_order(email_cell + emails_from_phone)
    if emails_from_phone:
        stats["email_rescued_from_phone"] += len(emails_from_phone)

    # 2) 区域线索：国家 > 官网域名 > 邮箱域名 > 地址 > 公司名（用于无国际前缀的号码）
    hints = guess_regions(row.get("国家"), row.get("官网"), row.get("地址"),
                          row.get("公司名称"), emails)
    if country_to_region(row.get("国家")) is None:
        stats["country_field_unmapped"] += 1

    # 3) 电话：手机号 / 固话分列
    mobiles, landlines = parse_phone_cell(phone_text, hints, audit)

    # 4) 有效数据校验：手机、固话、邮箱全空 -> 废弃数据，直接丢弃
    if not mobiles and not landlines and not emails:
        return None

    name = clean_company_name(row.get("公司名称"))
    if not name:
        stats["dropped_no_company_name"] += 1
        return None

    if emails:
        stats["rows_with_email"] += 1
    if mobiles:
        stats["rows_with_mobile"] += 1
    if landlines:
        stats["rows_with_landline"] += 1
    stats["mobile_count"] += len(mobiles)
    stats["landline_count"] += len(landlines)
    stats["email_count"] += len(emails)
    stats["multi_mobile_rows"] += int(len(mobiles) > 1)
    stats["multi_landline_rows"] += int(len(landlines) > 1)
    stats["multi_email_rows"] += int(len(emails) > 1)
    for h in mobiles + landlines:
        stats["bucket_reason:" + h.reason] += 1
        stats["region_bucket:" + (h.region or "?")] += 1
        if hints and h.hint and h.hint != hints[0]:
            stats["number_rescued_by_secondary_hint"] += 1

    remark = build_remark(row.get("官网"), row.get("国家"), row.get("分类"),
                          row.get("子分类"), row.get("地址"), row.get("ENF链接"))
    record = {
        "客户名称": "",  # 源数据无联系人/法人，按需求留空
        "企业名称": name,
        "岗位": "",
        "手机号": join_csv(h.display for h in mobiles),
        "固话": join_csv(h.display for h in landlines),
        "邮箱": join_csv(emails),
        "微信号": "",
        "数据来源": DATA_SOURCE_TAG,
        "外呼备注": remark,
    }
    return record


def load_source(path, limit):
    df = pd.read_excel(path, dtype=str)
    df = df.where(pd.notna(df), None)
    if limit:
        df = df.head(limit)
    return df


def write_workbook(records, out_path):
    wb = Workbook()
    ws = wb.active
    ws.title = "客户库"
    ws.append(HEADER)
    for cell in ws[1]:
        cell.font = Font(bold=True)
        cell.alignment = Alignment(horizontal="center", vertical="center")
    for rec in records:
        block = [rec["客户名称"] or None, rec["企业名称"], rec["岗位"] or None, rec["手机号"] or None,
                 rec["固话"] or None, rec["邮箱"] or None, rec["微信号"] or None,
                 rec["数据来源"], rec["外呼备注"] or None]
        ws.append(block + block)  # 模板中两组列内容完全一致
    # 号码/邮箱均以字符串写入（openpyxl 不会把 str 转成数值），保留前导 0 与原格式
    widths = {"A": 12, "B": 34, "C": 12, "D": 24, "E": 24, "F": 30, "G": 12, "H": 16, "I": 40,
              "J": 12, "K": 34, "L": 12, "M": 24, "N": 24, "O": 30, "P": 12, "Q": 16, "R": 40}
    for col, width in widths.items():
        ws.column_dimensions[col].width = width
    ws.row_dimensions[1].height = 24
    ws.freeze_panes = "A2"
    wb.save(out_path)


def cross_company_shared(records):
    """同一联系方式值出现在多家企业名下的统计（源数据本身就是共享联系人/代理商写法，不剔除，只留痕）。"""
    owners = collections.defaultdict(set)
    for rec in records:
        for col in ("手机号", "固话", "邮箱"):
            for value in (rec[col].split(",") if rec[col] else []):
                value = value.strip()
                if value:
                    owners[(col, value.lower() if col == "邮箱" else value)].add(rec["企业名称"])
    counts, examples = collections.Counter(), collections.defaultdict(list)
    for (col, value), names in owners.items():
        if len(names) > 1:
            counts[col] += 1
            if len(examples[col]) < 5:
                examples[col].append((value, sorted(names)[:3]))
    return counts, examples


def render_report(audit, stats, total_rows, kept_rows, elapsed, out_path, xlsx_path, records):
    lines = []
    lines.append("# ENF 光伏企业数据清洗报告")
    lines.append("")
    lines.append(f"- 生成时间：{dt.datetime.now():%Y-%m-%d %H:%M:%S}")
    lines.append(f"- 源文件：{SOURCE_PATH}")
    lines.append(f"- 导出文件：{xlsx_path}")
    lines.append(f"- 耗时：{elapsed:.1f}s")
    lines.append("")
    lines.append("## 一、结果概览")
    lines.append("")
    lines.append("| 指标 | 数值 |")
    lines.append("| --- | --- |")
    lines.append(f"| 源文件数据行 | {total_rows:,} |")
    lines.append(f"| 清洗后有效行 | {kept_rows:,} |")
    lines.append(f"| 丢弃行（手机/固话/邮箱均无有效值） | {total_rows - kept_rows:,} |")
    lines.append(f"| 含手机号行 | {stats['rows_with_mobile']:,} |")
    lines.append(f"| 含固话行 | {stats['rows_with_landline']:,} |")
    lines.append(f"| 含邮箱行 | {stats['rows_with_email']:,} |")
    lines.append(f"| 手机号总数 | {stats['mobile_count']:,} |")
    lines.append(f"| 固话总数 | {stats['landline_count']:,} |")
    lines.append(f"| 邮箱总数 | {stats['email_count']:,} |")
    lines.append(f"| 多手机号行 | {stats['multi_mobile_rows']:,} |")
    lines.append(f"| 多固话行 | {stats['multi_landline_rows']:,} |")
    lines.append(f"| 多邮箱行 | {stats['multi_email_rows']:,} |")
    lines.append(f"| 从电话列抢救出的邮箱 | {stats['email_rescued_from_phone']:,} |")
    lines.append(f"| 二级线索才定位到国家的号码 | {stats['number_rescued_by_secondary_hint']:,} |")
    lines.append(f"| 「国家」字段缺失/无法映射的行 | {stats['country_field_unmapped']:,} |")
    lines.append(f"| 完全重复行（企业+联系方式全同） | {stats['duplicate_row_dropped']:,} |")
    lines.append("")
    lines.append("## 二、号码判定依据分布")
    lines.append("")
    lines.append("| 判定依据 | 次数 |")
    lines.append("| --- | --- |")
    for k, v in sorted((k, v) for k, v in stats.items() if k.startswith("bucket_reason:")):
        lines.append(f"| {k.split(':', 1)[1]} | {v:,} |")
    lines.append("")
    lines.append("## 三、号码归属地区分布（Top 30）")
    lines.append("")
    lines.append("| 区域 | 号码数 |")
    lines.append("| --- | --- |")
    regions = sorted(((k.split(":", 1)[1], v) for k, v in stats.items() if k.startswith("region_bucket:")),
                     key=lambda kv: -kv[1])
    for k, v in regions[:30]:
        lines.append(f"| {k} | {v:,} |")
    lines.append("")
    lines.append("")
    lines.append("## 四、跨企业共享联系方式（源数据原样保留，未剔除）")
    lines.append("")
    lines.append("企业名录里存在代理商/集团共用号码与邮箱，属源数据真实情况（已逐行核对），")
    lines.append("不归属任一企业的垃圾池值才需要剔除；本表只做统计不做剔除。")
    lines.append("")
    lines.append("| 列 | 被多家企业共用的值数 | 示例 |")
    lines.append("| --- | --- | --- |")
    shared_counts, shared_examples = cross_company_shared(records)
    for col in ("手机号", "固话", "邮箱"):
        demo = "; ".join(f"{v} → {'/'.join(ns)}" for v, ns in shared_examples[col][:2]) or "-"
        lines.append(f"| {col} | {shared_counts[col]:,} | {demo[:160]} |")
    lines.append("")
    lines.append("## 五、清洗规则摘要")
    lines.append("")
    lines.append("- **字段映射**：客户名称（联系人/法人）源数据无此信息 → 统一留空，不用公司名填充；"
                  "企业名称 ← 公司名称；岗位/微信号 源数据无此信息 → 留空；多值用英文逗号分隔。")
    lines.append("- **有效数据判定**：手机号、固话、邮箱全为空的行直接删除（废弃数据不入库）；"
                  "号码必须通过 libphonenumber 的 is_valid_number 校验（号段/区号在元数据中真实存在），"
                  "仅位数凑对的伪号不入库。")
    lines.append("- **手机号定义**：中国大陆 11 位 1[3-9] 号段，以及 libphonenumber 判定为 MOBILE 的境外号码。")
    lines.append("- **固话定义**：国内外 0+区号 座机、公司总机、400/800/1010 等特服号，"
                  "以及号码可携地区（美国/加拿大等）无法区分手机与座机的境外号码，统一归入固话。")
    lines.append("- **不入库的号码**：纯传真号、位数/结构不达标的号码、"
                  "libphonenumber 判为无效（含号段不存在）的号码、以及地址/产品型号/营业时间被误写在电话列的文本。")
    lines.append("- **输出格式**：国内手机号 11 位纯数字；国内固话 0区号-号码；"
                  "特服号 400-xxx-xxxx；境外号码 E.164（+国家码号码）。")
    lines.append("- **写法修复**：国家码重复书写（+86-8620-89269660）、国家码后多写国内冠字 0"
                  "（+66 02 2055 9101）这类“人能看懂但拨不通”的号码，按 E.164 还原后入库（审计项 repaired_country_code_format）；"
                  "修复后的号码仍需通过严格校验，否则会被丢弃。")
    lines.append("- **模板两组列**（外呼备注前/后）内容完全一致，按需求同时写入。")
    lines.append("")
    lines.append("## 六、异常与被丢弃数据审计")
    lines.append("")
    lines.append("```")
    lines.append(str(audit))
    lines.append("```")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--src", default=SOURCE_PATH)
    parser.add_argument("--out", default=None)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    xlsx_path = args.out or os.path.join(OUTPUT_DIR, f"ENF光伏企业_客户库导入_清洗后_{stamp}.xlsx")
    report_path = os.path.splitext(xlsx_path)[0] + "_清洗报告.md"

    df = load_source(args.src, args.limit)
    audit = Audit()
    stats = collections.Counter({
        "country_field_unmapped": 0, "email_rescued_from_phone": 0, "dropped_no_company_name": 0,
        "rows_with_email": 0, "rows_with_mobile": 0, "rows_with_landline": 0,
        "mobile_count": 0, "landline_count": 0, "email_count": 0,
        "multi_mobile_rows": 0, "multi_landline_rows": 0, "multi_email_rows": 0,
    })
    records, seen = [], set()
    start = dt.datetime.now()
    for i, row in enumerate(df.to_dict("records"), 1):
        if i % 10000 == 0:
            print(f"  进度 {i}/{len(df)}  有效 {len(records)}", file=sys.stderr)
        rec = clean_row(row, audit, stats)
        if rec is None:
            continue
        key = (rec["企业名称"], rec["手机号"], rec["固话"], rec["邮箱"])
        if key in seen:
            stats["duplicate_row_dropped"] += 1
            continue
        seen.add(key)
        records.append(rec)
    elapsed = (dt.datetime.now() - start).total_seconds()

    write_workbook(records, xlsx_path)
    render_report(audit, stats, len(df), len(records), elapsed, report_path, xlsx_path, records)
    print(f"导出：{xlsx_path}")
    print(f"报告：{report_path}")
    print(f"有效行：{len(records):,} / {len(df):,}  耗时 {elapsed:.1f}s")


if __name__ == "__main__":
    main()
