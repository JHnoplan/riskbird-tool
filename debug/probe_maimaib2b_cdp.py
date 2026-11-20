#!/usr/bin/env python3
# 麦麦B2B（maimaib2b.com）接入前实测：全站被 Cloudflare JS 挑战挡住（裸 HTTP 403 +
# 「Just a moment...」5.8KB 薄壳），但 robots.txt 与 sitemap 可裸读。本脚本借工具专用的
# 共享 Chrome（调试端口 9255）过盾，验三件事：
#   1) 店铺页 /Shop/<id>/ 的公司名称/联系人/手机电话是不是免登录明文（登记表要这三项）
#   2) 站内搜索/分类列表有没有总量与翻页，能翻到多少家
#   3) 过盾后的 Cookie 能不能拿去裸 HTTP 复用（决定采集器走 CDP 还是走裸 HTTP）
import json
import re
import sys
import time
import urllib.parse
import urllib.request

import requests
import websocket

sys.path.insert(0, "/Users/jhnoplan/Desktop/PythonDemo/b2bcollect_build")
import shared_chrome as sc  # noqa: E402  直接复用工具自己的共享 Chrome 标签分配逻辑

PORT = sc.PORT
CACHE = "/Users/jhnoplan/Desktop/PythonDemo/debug/probe_maimaib2b_cache"
MOBILE_RE = re.compile(r"1[3-9]\d{9}")
TEL_RE = re.compile(r"0\d{2,3}-?\d{7,8}")
MAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")


def tabs():
    return requests.get(f"http://127.0.0.1:{PORT}/json/list", timeout=5).json()


def new_tab(url="about:blank"):
    return sc.open_tab(PORT, url)


def cdp(ws_url, method, params=None, timeout=25, sid=None):
    ws = websocket.create_connection(ws_url, timeout=timeout)
    try:
        mid = 1
        payload = {"id": mid, "method": method, "params": params or {}}
        if sid:
            payload["sessionId"] = sid
        ws.send(json.dumps(payload))
        end = time.time() + timeout
        while time.time() < end:
            raw = ws.recv()
            if not raw:
                continue
            msg = json.loads(raw)
            if msg.get("id") == mid:
                return msg
        return {}
    finally:
        try:
            ws.close()
        except Exception:
            pass


def evaluate(ws_url, expr, timeout=25):
    m = cdp(ws_url, "Runtime.evaluate",
           {"expression": expr, "returnByValue": True, "awaitPromise": True}, timeout)
    return (m.get("result", {}).get("result", {}) or {}).get("value")


def main():
    shop_ids = re.findall(r"/Shop/(\d+)/", open(f"{CACHE}/sitemap.xml", encoding="utf-8").read())
    print(f"缓存 sitemap 里店铺 id {len(shop_ids)} 个，区间 {shop_ids[-1]}–{shop_ids[0]}")

    t = new_tab("https://www.maimaib2b.com/")
    if not t:
        print("共享 Chrome 没开新标签，先确认 9255 可用")
        return
    ws = t["webSocketDebuggerUrl"]
    print("开标签过 Cloudflare…", end=" ", flush=True)
    html = ""
    for i in range(24):
        time.sleep(2.5)
        html = evaluate(ws, "document.documentElement.outerHTML") or ""
        title = evaluate(ws, "document.title") or ""
        if html and "moment" not in title.lower() and len(html) > 30000:
            print(f"通过（第 {i + 1} 次轮询，len={len(html)}，title={title[:40]}）")
            break
    else:
        print(f"仍未通过：len={len(html)} title={evaluate(ws, 'document.title')!r}")
        return

    print("=" * 72)
    print("① 店铺页字段（连开 6 家）")
    for sid in shop_ids[:6]:
        url = f"https://www.maimaib2b.com/Shop/{sid}/"
        cdp(ws, "Page.navigate", {"url": url})
        time.sleep(4.0)
        for _ in range(8):
            rdy = evaluate(ws, "document.readyState")
            if rdy == "complete":
                break
            time.sleep(1.0)
        h = evaluate(ws, "document.documentElement.outerHTML") or ""
        txt = evaluate(ws, "document.body.innerText") or ""
        chal = "moment" in (evaluate(ws, "document.title") or "").lower()
        print(f"\n  --- {url}  len={len(h)}  挑战中={chal}")
        for lab in ("公司名称", "联系人", "手机", "电话", "邮箱", "地址", "主营产品", "经营模式"):
            hit = re.search(lab + r"[：:\s]*([^\n|<]{0,40})", txt)
            print(f"      {lab:<6} -> {(hit.group(1).strip() if hit else '(标签都没出现)')[:40]}")
        print(f"      明文手机 {MOBILE_RE.findall(txt)[:3]}  "
              f"固话 {TEL_RE.findall(txt)[:3]}  "
              f"邮箱 {MAIL_RE.findall(txt)[:2]}")
        if "查看联系方式" in txt or "登录" in txt[:400]:
            print("      ⚠ 页面出现「查看联系方式」或登录提示，需确认是否免登录可见")
        open(f"{CACHE}/cdp_shop_{sid}.html", "w", encoding="utf-8").write(h)

    print("=" * 72)
    print("② 首页结构：搜索入口 / 分类 / 是否标称总量")
    cdp(ws, "Page.navigate", {"url": "https://www.maimaib2b.com/"})
    time.sleep(4.0)
    h = evaluate(ws, "document.documentElement.outerHTML") or ""
    open(f"{CACHE}/cdp_home.html", "w", encoding="utf-8").write(h)
    txt = evaluate(ws, "document.body.innerText") or ""
    print("  首页文本长度", len(txt), "| 前 200 字：", re.sub(r"\s+", " ", txt[:200]))
    print("  页内链接总数", len(re.findall(r'href="', h)))
    for pat, lab in ((r'Search[^"\s]{0,40}', "搜索类 URL"), (r'Shop/\d+/', "店铺类 URL"),
                     (r'(?:Company|Qiye|Supply|Pro|News)[^"\s/]{0,30}/', "栏目类 URL")):
        hits = [x for x in dict.fromkeys(re.findall(pat, h)) if x][:10]
        print(f"  {lab}: {hits}")
    print("  首页文字里的数量表述：",
          re.findall(r"([\d,]+\s*(?:家|条|个|万\+?))", txt)[:12])
    print("  表单框 placeholder：", re.findall(r'placeholder="([^"]{0,20})"', h)[:6])

    print("=" * 72)
    print("③ Cookie 能不能裸 HTTP 复用（决定走 CDP 还是裸 HTTP）")
    ck = cdp(ws, "Network.getCookies", {"urls": ["https://www.maimaib2b.com/"]})
    cookies = ck.get("result", {}).get("cookies", [])
    jar = "; ".join(f"{c['name']}={c['value']}" for c in cookies)
    ua = evaluate(ws, "navigator.userAgent")
    print("  拿到 cookie：", [c["name"] for c in cookies])
    for name in ("cf_clearance", "PHPSESSID", "ASP.NET_SessionId"):
        if any(c["name"] == name for c in cookies):
            print(f"  ✔ 含 {name}")
    req = urllib.request.Request("https://www.maimaib2b.com/Shop/%s/" % shop_ids[0],
                                 headers={"User-Agent": ua, "Cookie": jar,
                                          "Accept-Language": "zh-CN,zh;q=0.9"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            body = r.read().decode("utf-8", "ignore")
        print(f"  → 带 Cookie 裸 HTTP 请求店铺页：http=200 len={len(body)} "
              f"{'过盾成功（可裸 HTTP）' if len(body) > 30000 else '仍是挑战薄壳（必须走 CDP）'}")
    except urllib.error.HTTPError as e:
        print(f"  → 带 Cookie 裸 HTTP：http={e.code}（{'必须走 CDP 渲染' if e.code == 403 else '再看'}）")
    cdp(ws, "Target.closeTarget", {"targetId": t["id"]})


if __name__ == "__main__":
    main()
