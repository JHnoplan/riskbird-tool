# -*- coding: utf-8 -*-
"""离线核查供应商网：对 13:56 抓到的 12 家真实商铺页 + 联系页，
逐家输出「页面实际是否含联系人（独立扫描为准）」vs「当前解析器取到了什么」。
不联网，避免在 IP 处于限流区时反复取页加重风控。
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "b2bcollect_build"))
from bs4 import BeautifulSoup                       # noqa: E402
from gys_crawler import GysCrawler                  # noqa: E402

CACHE = Path(__file__).parent / "probe_gys_cache"
c = GysCrawler()

SUBS = sorted({p.name.split("_", 1)[1].replace(".html", "")
               for p in CACHE.glob("shop_*.html")})

# 独立真值判据：可见文本里出现「联系人/姓名/职　位/职务」标签，或 linkman/position class
LABEL_SCAN = re.compile(r"(联系人|联\s*系\s*人|姓\s*名|职\s*位|职务|岗\s*位)")


def ground_truth(html):
    t = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
    t = re.sub(r"<[^>]+>", "\n", t)
    t = t.replace("\u3000", " ").replace("&nbsp;", " ")
    lines = [l.strip() for l in t.split("\n") if l.strip()]
    hits = []
    for i, l in enumerate(lines):
        if LABEL_SCAN.fullmatch(l) or LABEL_SCAN.search(l):
            ctx = " | ".join(lines[i:i + 2])[:70]
            hits.append(ctx)
    soup = BeautifulSoup(html, "lxml")
    cls = [el.get_text(" ", strip=True)[:30] for k in ("linkman", "position")
           for el in soup.find_all(class_=k)]
    return hits[:4], cls


print(f"{'子域':22} {'页面实际(独立扫描)':38} {'解析器:联系人/岗位/手机':34}")
shop_hit = shop_got = contact_hit = contact_got = 0
for sub in SUBS:
    for kind in ("shop", "contact"):
        p = CACHE / f"{kind}_{sub}.html"
        if not p.exists():
            continue
        html = p.read_text(encoding="utf-8", errors="ignore")
        hits, cls = ground_truth(html)
        d = c._parse_company_page(html, sub)
        truth = (hits[0] if hits else "") + (" cls=" + ";".join(cls) if cls else "")
        parsed = f"{d.get('联系人','-')}/{d.get('岗位','-')}/{d.get('手机') or d.get('电话') or '-'}"
        has_truth = bool(hits or cls)
        has_got = bool(d.get("联系人"))
        print(f"{sub:22} [{kind:7}] {'有' if has_truth else '无'} {truth:34.34} -> {parsed}")
        if kind == "shop":
            shop_hit += has_truth
            shop_got += has_got
        else:
            contact_hit += has_truth
            contact_got += has_got
print(f"\n商铺页：实际有联系人 {shop_hit}/{len(SUBS)}，解析取到 {shop_got}")
print(f"联系页：实际有联系人 {contact_hit}/{len(SUBS)}，解析取到 {contact_got}")
