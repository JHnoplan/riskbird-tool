# -*- coding: utf-8 -*-
"""取证：b2b168 商铺页「联系人」为何大面积为空——是真无数据还是解析遗漏。
对照取样：缺联系人的随机子域商铺 vs 有联系人的常规商铺，逐页 dump 含「联系/姓名/手机/热线/法人」的上下文行。
"""
import re
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "b2bcollect_build"))
from b2b168_crawler import B2b168Crawler, _CONTACT_NAME_RE, _NAME_CUT_RE  # noqa: E402

CACHE = Path(__file__).parent / "probe_contact_cache"
CACHE.mkdir(exist_ok=True)

# 缺联系人（本轮产出）+ 有联系人（对照）
MISSING = [
    "https://yzbovj8n2l1xoh5ph2.b2b168.com/",   # 廊坊市亮晟：无联系人无地址
    "https://shmrhu.cn.b2b168.com/",            # 上海予贵合：无联系人有地址
    "https://igt9ykijs4w9bc6.b2b168.com/",      # 邯郸巨源能：无联系人无地址
    "https://cs4m4zmhozjbk4.b2b168.com/",       # 辉腾能源：无联系人无地址
    "https://gtqgdprqajt1.b2b168.com/",         # 临沂双亚：无联系人无地址
]
HIT = [
    "https://tllxsbzidj9793.b2b168.com/",       # 铜陵领芯：有联系人 朱本理
    "https://kyzc199.b2b168.com/",              # 保定科易智创：有联系人 周
]

KEYWORDS = ("联系人", "联 系 人", "姓名", "手机", "电话", "热线", "邮箱", "地址", "法人", "代表", "联系")


def dump(c, url, tag):
    html = c._fetch("GET", url, tries=2)
    if not html:
        print(f"  !! 取不到页面 {url}")
        return None
    (CACHE / f"{tag}.html").write_text(html, encoding="utf-8")
    visible = c._visible_text(html)
    lines = [l.strip() for l in visible.splitlines() if l.strip()]
    hits = [l for l in lines if any(k in l for k in KEYWORDS)]
    print(f"\n### {tag}  {url}")
    print(f"  可见文本 {len(visible)} 字，行数 {len(lines)}，含关键词行 {len(hits)}")
    for l in hits[:14]:
        print(f"    | {l[:110]}")
    m = _CONTACT_NAME_RE.search(visible)
    print(f"  _CONTACT_NAME_RE → {m.group(1)[:20]!r}" if m else "  _CONTACT_NAME_RE → 未命中")
    ld = re.findall(r'<script[^>]+ld\+json[^>]*>(.*?)</script>', html, re.S | re.I)
    print(f"  JSON-LD 块 {len(ld)} 个")
    for b in ld[:2]:
        print("    ", b.strip()[:300].replace("\n", " "))
    # 页面里指向联系方式子页/接口的线索
    for pat in [r'href="([^"]*(?:contact|home\.aspx|about|lianxi)[^"]*)"',
                r'(?:src|href)="([^"]*(?:GetContact|contact\.ashx|Phone|MemberInfo)[^"]*)"']:
        found = sorted(set(re.findall(pat, html, re.I)))[:6]
        if found:
            print(f"  线索 {pat[:28]}… → {found}")
    return html


c = B2b168Crawler(port=9255)
print("=" * 70)
print("A. 缺联系人的商铺（本轮产出为空）")
for u in MISSING:
    dump(c, u, "miss_" + re.split(r"//", u)[1].split(".")[0][:18])
print("\n" + "=" * 70)
print("B. 对照组：本轮成功取到联系人的商铺")
for u in HIT:
    dump(c, u, "hit_" + re.split(r"//", u)[1].split(".")[0][:18])
print("\n" + "=" * 70)
print("C. 缺联系人商铺的 home.aspx（关于我们）兜底页")
for u in MISSING[:3]:
    host = re.split(r"//", u)[1]
    dump(c, f"https://{host.rstrip('/')}/home.aspx", "about_" + host.split(".")[0][:18])
