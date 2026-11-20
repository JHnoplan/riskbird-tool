# -*- coding: utf-8 -*-
"""取证二：b2b168 商铺 /contact.aspx 与移动端 /m/ 页是否携带「联系人」姓名。
决定这些空联系人是站点未公开（免费注册商铺），还是我们少访了一个页面。
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "b2bcollect_build"))
from b2b168_crawler import B2b168Crawler, _CONTACT_NAME_RE  # noqa: E402

CACHE = Path(__file__).parent / "probe_contact_cache"
CACHE.mkdir(exist_ok=True)

SHOPS = [
    ("miss", "https://yzbovj8n2l1xoh5ph2.b2b168.com"),
    ("miss", "https://gtqgdprqajt1.b2b168.com"),
    ("miss", "https://shmrhu.cn.b2b168.com"),
    ("hit", "https://kyzc199.b2b168.com"),
]
SUFFIX = ["", "/contact.aspx", "/m/", "/m/contact.aspx", "/list/contact.html", "/gs.html"]

c = B2b168Crawler(port=9255)
for kind, base in SHOPS:
    host = base.split("//")[1]
    for suf in SUFFIX:
        url = base + suf
        html = c._fetch("GET", url, tries=1)
        if not html:
            print(f"[{kind}] {url}\n    → 空/404")
            continue
        visible = c._visible_text(html)
        lines = [l.strip() for l in re.split(r"[\n\r]+", visible) if l.strip()]
        blob = " ".join(lines)
        m = _CONTACT_NAME_RE.search(visible)
        # 联系人可能写作「联系人：X」「X经理」「姓名(X)」等，逐个探测
        probes = {
            "联系人": re.findall(r"联\s*系\s*人\s*[：:]?\s*([\u4e00-\u9fa5·A-Za-z]{1,10})", visible),
            "姓名": re.findall(r"姓\s*名\s*[：:]?\s*([\u4e00-\u9fa5·]{1,10})", visible),
            "称谓": re.findall(r"([\u4e00-\u9fa5]{1,3}(?:经理|先生|女士|厂长|总监|老板))", visible),
            "手机": re.findall(r"1[3-9]\d{9}", visible)[:3],
        }
        print(f"[{kind}] {url}")
        print(f"    体积 {len(visible)} 字  _CONTACT_NAME_RE→{m.group(1)[:12]!r}" if m
              else f"    体积 {len(visible)} 字  _CONTACT_NAME_RE→未命中")
        for k, v in probes.items():
            print(f"    {k}: {v if v else '—'}")
        (CACHE / f"page_{host.replace('.', '_')}_{suf.strip('/').replace('/', '_') or 'root'}.html").write_text(html, encoding="utf-8")
