# -*- coding: utf-8 -*-
"""取证：供应商网商铺页 + /contact-information/ 联系页，量化「当前解析取到 vs 页面实际有」。
本轮产出 12 家 gys 记录全部无联系人、其中 2 家连电话都没有，逐家核对是真无还是漏。
纯 HTTP 取样（curl 实测商铺静态子域可直接访问），低频防封。
"""
import re
import sys
import time
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "b2bcollect_build"))
import requests                                        # noqa: E402
from gys_crawler import GysCrawler                     # noqa: E402

CACHE = Path(__file__).parent / "probe_gys_cache"
CACHE.mkdir(exist_ok=True)

SUBS = ["dongfangxinhong", "aosienhuanjing", "newsunpv", "maqixiang123456",
        "guangzhouht", "mingxuanxny", "shengantaigang", "qizhexinneng",
        "jinruidajin", "shhzsj", "tjcjtgt", "18911269529"]

HDRS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Accept-Language": "zh-CN,zh;q=0.9"}

NAME_LABEL = re.compile(r"联系人\s*[：:]\s*")
# 联系页明文形如「联系人：朱先生，联系电话：13051353683」
CONTACT_TXT = re.compile(r"联系人\s*[：:]\s*([\u4e00-\u9fa5·A-Za-z]{1,12})")


def get(url):
    try:
        r = requests.get(url, headers=HDRS, timeout=25)
        if r.status_code == 200 and r.text:
            r.encoding = r.apparent_encoding or "utf-8"
            return r.text
        return f"__HTTP_{r.status_code}__"
    except Exception as e:
        return f"__ERR__{str(e)[:60]}"


def dom_contact(html):
    """DOM 结构取联系人：gys 商铺把标签和值拆在 <label>联系人：</label><span><em>张女士</em>
    与 <dt>联系人：</dt><dd>张女士</dd> 两处，逐行文本匹配天然取不到。"""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "lxml")
    names = []
    for lab in soup.find_all(string=NAME_LABEL.search):
        nxt = lab.find_next(string=lambda t: t and t.strip())
        if nxt:
            names.append(nxt.strip())
    for dt in soup.find_all(["dt", "label", "span", "th"]):
        if NAME_LABEL.match(dt.get_text(strip=True)):
            sib = dt.find_next_sibling()
            if sib:
                names.append(sib.get_text(strip=True))
    return [n for n in dict.fromkeys(names) if 1 <= len(n) <= 12]


c = GysCrawler()
rows = []
for i, sub in enumerate(SUBS, 1):
    url = f"https://{sub}.gys.cn/"
    html = get(url)
    if html.startswith("__"):
        print(f"[{i}] {sub} 商铺页取失败：{html[:60]}")
        continue
    (CACHE / f"shop_{sub}.html").write_text(html, encoding="utf-8")
    cur = c._parse_company_page(html, sub)
    dom_names = dom_contact(html)
    text_names = CONTACT_TXT.findall(re.sub(r"<[^>]+>", " ", html))
    ld = c._parse_ld_json(html)
    cp = get(url.rstrip("/") + "/contact-information/")
    cp_names = cp_phones = cp_mails = []
    if not cp.startswith("__") and len(cp) > 2000:
        (CACHE / f"contact_{sub}.html").write_text(cp, encoding="utf-8")
        cvt = re.sub(r"<[^>]+>", " ", cp)
        cp_names = CONTACT_TXT.findall(cvt)
        cp_phones = re.findall(r"1[3-9]\d{9}", cvt)[:3]
        cp_mails = re.findall(r"[\w.+-]+@[\w-]+\.[\w.]+", cvt)[:2]
    rows.append({
        "sub": sub,
        "现在解析.联系人": cur.get("联系人", ""),
        "现在解析.手机": cur.get("手机", ""), "现在解析.电话": cur.get("电话", ""),
        "现在解析.邮箱": cur.get("邮箱", ""),
        "页面实际有.联系人DOM": dom_names, "页面实际有.联系人文本": text_names,
        "联系页.联系人": cp_names, "联系页.手机": cp_phones, "联系页.邮箱": cp_mails,
        "ld电话": ld.get("电话", ""),
    })
    print(f"[{i}/{len(SUBS)}] {sub}: 现在={cur.get('联系人') or '空'} 页面DOM={dom_names} "
          f"联系页={cp_names}/{cp_phones}")
    time.sleep(4.5)

print("\n" + "=" * 78)
miss_contact = [r for r in rows if not r["现在解析.联系人"]]
recoverable = [r for r in miss_contact
               if r["页面实际有.联系人DOM"] or r["页面实际有.联系人文本"] or r["联系页.联系人"]]
print(f"取样 {len(rows)} 家：当前解析取到联系人 {len(rows) - len(miss_contact)} 家，缺 {len(miss_contact)} 家")
print(f"其中「页面/联系页其实有联系人」= {len(recoverable)} 家 → 真无数据 {len(miss_contact) - len(recoverable)} 家")
no_phone = [r for r in rows if not (r["现在解析.手机"] or r["现在解析.电话"])]
print(f"当前无号码 {len(no_phone)} 家：{[(r['sub'], r['ld电话'], r['联系页.手机']) for r in no_phone]}")
print(json.dumps(rows, ensure_ascii=False, indent=1))
