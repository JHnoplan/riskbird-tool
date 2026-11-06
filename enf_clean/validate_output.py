# -*- coding: utf-8 -*-
"""清洗结果质量校验：结构、字段规则、号码可解析性、与源数据的关联一致性。"""
import argparse
import re
import sys

import pandas as pd
import phonenumbers as pn

TEMPLATE_HEADER = ["客户名称", "企业名称", "岗位", "手机号", "固话", "邮箱", "微信号", "数据来源", "外呼备注",
                   "客户名称", "企业名称", "岗位", "手机号", "固话", "邮箱", "微信号", "数据来源", "备注"]
PHONE_COLS = ["手机号", "固话"]
EMAIL_RE = re.compile(r"^[A-Za-z0-9\u00c0-\u02af._%+\-]+@[A-Za-z0-9\u00c0-\u02af.\-]+\.[A-Za-z\u00c0-\u02af]{2,24}$")
CN_MOBILE_RE = re.compile(r"^1[3-9]\d{9}$")


BAD_PHONES, MISMATCHES = [], []


def split_multi(value):
    return [v.strip() for v in str(value).split(",") if v.strip()] if pd.notna(value) else []


def _suffix_overlap(a, b, min_len=7):
    """两段数字尾部对齐长度 >= min_len 则认为同源（用于补全国家码/区号的场景）。"""
    n = 0
    while n < min(len(a), len(b)) and a[-1 - n] == b[-1 - n]:
        n += 1
    return n >= min_len


def validate(xlsx_path, src_path):
    from openpyxl import load_workbook
    ws = load_workbook(xlsx_path, read_only=True).worksheets[0]
    header = [c.value for c in next(ws.iter_rows(min_row=1, max_row=1))]
    df = pd.read_excel(xlsx_path, dtype=str)
    src = pd.read_excel(src_path, dtype=str)
    problems = []

    # 1) 结构：列名与模板完全一致
    if header != TEMPLATE_HEADER:
        problems.append("表头与模板不一致：" + str(header))

    # 2) 字段规则
    for col in ["客户名称", "岗位", "微信号"]:
        if col in df and df[col].notna().any():
            problems.append(f"{col} 应为空（源数据无该信息），实际有 {int(df[col].notna().sum())} 个非空值")
    empty_contact = df["手机号"].isna() & df["固话"].isna() & df["邮箱"].isna()
    if int(empty_contact.sum()):
        problems.append(f"存在 {int(empty_contact.sum())} 行无任何有效联系方式")
    if df["企业名称"].isna().any():
        problems.append(f"存在 {int(df['企业名称'].isna().sum())} 行企业名称为空")
    if df["数据来源"].isna().any():
        problems.append("存在数据来源为空的行")

    # 3) 两组同构列内容一致
    first, second = df.iloc[:, 0:9].reset_index(drop=True), df.iloc[:, 9:18].reset_index(drop=True)
    second.columns = first.columns
    diff = int((first.fillna("") != second.fillna("")).any(axis=1).sum())
    if diff:
        problems.append(f"外呼备注前后两组列有 {diff} 行不一致")

    # 4) 号码可解析 + 手机/固话分列正确
    bad_phone, mobile_bad, landline_unknown = 0, 0, 0
    for col in PHONE_COLS:
        for value in df[col].dropna():
            for token in split_multi(value):
                try:
                    if CN_MOBILE_RE.match(token):
                        number = pn.parse(token, "CN")
                    else:
                        number = pn.parse(token, None if token.startswith("+") else "CN")
                except Exception:
                    bad_phone += 1
                    BAD_PHONES.append((col, token, "parse_error"))
                    continue
                if not pn.is_valid_number(number):
                    bad_phone += 1
                    BAD_PHONES.append((col, token, "invalid"))
                    continue
                cc, nat = number.country_code, pn.national_significant_number(number)
                if col == "手机号":
                    # 手机号列只允许：中国大陆手机号段 或 libphonenumber 判定为 MOBILE
                    is_cn_mobile = cc == 86 and bool(CN_MOBILE_RE.match(nat))
                    if not is_cn_mobile and pn.number_type(number) != pn.PhoneNumberType.MOBILE:
                        mobile_bad += 1
                else:
                    if cc == 86 and CN_MOBILE_RE.match(nat):
                        landline_unknown += 1

    # 5) 邮箱格式
    bad_email = 0
    for value in df["邮箱"].dropna():
        for token in split_multi(value):
            if not EMAIL_RE.match(token):
                bad_email += 1

    # 6) 与源数据的关联一致性：企业能回溯源行，且联系方式确实来自同一行
    from enf_clean.normalize import clean_company_name, normalize_text
    src_by_name = {}
    for _, r in src.iterrows():
        src_by_name.setdefault(clean_company_name(r["公司名称"]), []).append(r)
    mismatch, not_found = 0, 0
    for _, r in df.iterrows():
        name = str(r["企业名称"]).strip()
        rows = src_by_name.get(name)
        if not rows:
            not_found += 1
            continue
        pool = "".join(normalize_text(x["电话"]) for x in rows) + \
               "".join(normalize_text(x["邮箱"]) for x in rows)
        digits_pool = re.sub(r"\D", "", pool)
        for col in PHONE_COLS:
            for token in split_multi(r.get(col)):
                try:
                    number = pn.parse(token, "CN" if not token.startswith("+") else None)
                    ns = pn.national_significant_number(number)
                except Exception:
                    mismatch += 1
                    continue
                cands = {ns, ns.lstrip("0"), "0" + ns}
                if any(c and c in digits_pool for c in cands):
                    continue
                # 清洗时按国家/区域线索补全了国际前缀、区号或阿根廷 9 位移动前缀，
                # 源行里只有本地写法：此时要求尾部对齐（后 7 位以上相同）才算同源
                if not any(_suffix_overlap(ns, d) for d in re.findall(r"\d{6,}", digits_pool)):
                    mismatch += 1
                    MISMATCHES.append((name, col, token))
        for token in split_multi(r.get("邮箱")):
            if token.lower() not in pool.lower():
                mismatch += 1
                MISMATCHES.append((name, "邮箱", token))

    print("=" * 72)
    print(f"文件：{xlsx_path}")
    print(f"数据行：{len(df):,}    列数：{df.shape[1]}")
    print(f"手机号行：{int(df['手机号'].notna().sum()):,}    "
          f"固话行：{int(df['固话'].notna().sum()):,}    "
          f"邮箱行：{int(df['邮箱'].notna().sum()):,}")
    print(f"号码总数：{sum(len(split_multi(v)) for v in df['手机号'].dropna()) + sum(len(split_multi(v)) for v in df['固话'].dropna()):,}")
    print("-" * 72)
    print(f"无法通过 libphonenumber 复检的号码：{bad_phone}")
    for item in BAD_PHONES:
        print("        e.g.", item)
    print(f"手机号列中类型不是 MOBILE 的号码：{mobile_bad}")
    print(f"固话列中的中国大陆手机号：{landline_unknown}")
    print(f"格式非法邮箱：{bad_email}")
    print(f"与源文件关联不一致的联系方式：{mismatch}")
    for item in MISMATCHES:
        print("        e.g.", item)
    print(f"回不到源文件的企业名：{not_found}")
    print("-" * 72)
    if problems:
        print("发现问题：")
        for p in problems:
            print("  - " + p)
    else:
        print("结构与业务规则校验：全部通过")
    print("=" * 72)
    return 1 if (problems or bad_phone or mobile_bad or landline_unknown or bad_email) else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("xlsx")
    parser.add_argument("--src", default="/Users/zhihuiyunmac/Downloads/ENF光伏企业_去重_20261026_090035.xlsx")
    sys.exit(validate(parser.parse_args().xlsx, parser.parse_args().src))
