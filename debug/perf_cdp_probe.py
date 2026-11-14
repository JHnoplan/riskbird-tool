#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""perf_cdp_probe.py —— CDP 往返成本探针（多线程/性能优化的基线与复测工具）。

为什么要有这么个东西：「工具在差电脑上卡」这句话，光看代码只能猜到原因，
猜不到量级。本轮实测前先把它钉成一个可重复的数字：同一条 Runtime.evaluate，
在**当前实现**下要打几次网络往返、每次多少毫秒。

测法（刻意不造假）：
  · 自己起一个 --headless=new 的 Chrome（独立端口 + 独立临时 profile），
    不碰用户自己的浏览器，也不依赖任何站点登录——握手成本与页面内容无关；
  · 真用生产代码的入口（CdpSiteCrawler._send/_eval）发命令；
  · 只对 requests.get / websocket.create_connection 计数（不改变行为），
    这样「每条命令都要列一次标签 + 重开一次 websocket」这件事变成两个整数；
  · 报中位数与总耗时，不报平均数（一次网络抖动会把平均数拖得看不出真相）。

用法：
  python debug/perf_cdp_probe.py            # 默认 30 次
  python debug/perf_cdp_probe.py -n 60
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "b2bcollect_build"))

PORT = 9399                      # 刻意避开 9255（用户的共享 Chrome）与 9233（风鸟）
CHROME_CANDIDATES = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
)


def find_chrome() -> str:
    for c in CHROME_CANDIDATES:
        if Path(c).is_file():
            return c
    for name in ("google-chrome", "chromium", "chrome"):
        p = shutil.which(name)
        if p:
            return p
    return ""


def wait_ready(timeout=30.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/version", timeout=2) as r:
                if json.loads(r.read().decode()).get("Browser"):
                    return True
        except Exception:
            time.sleep(0.3)
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", "--calls", type=int, default=30)
    ap.add_argument("--tabs", type=int, default=0,
                    help="先开 K 个额外标签再测：/json/list 的成本是随标签数长的，"
                         "低配机器上同时挂着七八个标签才是常态")
    ap.add_argument("--busy", type=int, default=0,
                    help="测的时候起 N 个 CPU 空转进程，仿真弱核机器被抢占的情形")
    ap.add_argument("--big", action="store_true",
                    help="把被测标签换成一份 ~250KB 的 DOM，顺带量一次整页 outerHTML "
                         "（采集每家都要付这一笔）")
    ap.add_argument("--cycle", type=int, default=0,
                    help="量「真实页面周期」：_goto + 就绪轮询 + 拦检 + 取整页，"
                         "一轮要打多少条 CDP 命令、多少毫秒。跑 N 轮。"
                         "单条命令的延迟说明不了问题，一家公司要付几笔才说明得了。")
    ap.add_argument("--proxy", action="store_true",
                    help="把 HTTP_PROXY 指到一个没人监听的端口再测一遍："
                         "本机 CDP 接口（http://127.0.0.1:port/json/list）今天没设绕过，"
                         "开着系统代理/VPN 的电脑每一次列标签都要被代理转一圈")
    args = ap.parse_args()

    exe = find_chrome()
    if not exe:
        print("❌ 这台机器上找不到 Chrome/Chromium/Edge，测不了（不是脚本的问题）")
        return 2
    profile = Path(tempfile.mkdtemp(prefix="perf_cdp_profile_"))
    cmd = [exe, f"--remote-debugging-port={PORT}", f"--user-data-dir={profile}",
           "--headless=new", "--no-first-run", "--no-default-browser-check",
           "--disable-gpu", "--hide-scrollbars", "about:blank"]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            stdin=subprocess.DEVNULL)
    hogs: list[subprocess.Popen] = []
    try:
        if not wait_ready():
            print("❌ Chrome 调试端口 30 秒内没起来（可能是版本不支持 headless=new）")
            return 2

        import requests                      # noqa: F401  生产代码用的就是它
        import websocket
        import cdp_base
        import shared_chrome

        counters = {"list": 0, "handshake": 0}
        real_new = websocket.create_connection

        def count_new(*a, **kw):
            counters["handshake"] += 1
            return real_new(*a, **kw)

        websocket.create_connection = count_new

        # 「列标签」这一笔要数的不是 requests.get 本身：控制面走的是
        # shared_chrome.local_session()（不经代理 + 复用连接），按模块函数计数才对得上
        # 生产路径。旧实现里每条命令一次 _get_tabs，新实现里它只在建连时发生。
        real_tabs = cdp_base.CdpSiteCrawler._get_tabs

        def count_tabs(self):
            counters["list"] += 1
            return real_tabs(self)

        cdp_base.CdpSiteCrawler._get_tabs = count_tabs

        cr = cdp_base.CdpSiteCrawler(port=PORT)
        tabs = shared_chrome.local_session().get(
            shared_chrome.local_url(PORT, "/json/list"), timeout=4).json()
        page = next((t for t in tabs if t.get("type") == "page"), None)
        if page is None:
            print("❌ 没找到 page 类型标签（Chrome 起来了但没页面，测不了）")
            return 2
        cr.pin_tab_id = page["id"]
        cr._ws_cache = page.get("webSocketDebuggerUrl") or ""

        # ── 造现场：额外标签 + 大 DOM + CPU 抢占 ─────────────────────────
        for _ in range(max(0, args.tabs)):
            try:
                shared_chrome.local_session().put(
                    shared_chrome.local_url(PORT, "/json/new?about:blank"), timeout=4)
            except Exception:
                break
        if args.big:
            # 旧写法把整份 HTML 赋给 documentElement.outerHTML —— Chrome 里这是空操作，
            # 实测注入后取回 0 KB，等于「大 DOM」这一档根本没测到（11-14 自查发现）。
            # 改成写真页面再用 file:// 装载：DOM 规模才真的变大。
            row = "<div>" + "x" * 400 + "</div>"
            big_path = profile / "big.html"
            big_path.write_text("<html><body>" + row * 600 + "</body></html>",
                                encoding="utf-8")
            cr._cdp_navigate(big_path.as_uri())
            time.sleep(1.0)
        for _ in range(max(0, args.busy)):
            hogs.append(subprocess.Popen([sys.executable, "-c", "while True: pass"],
                                         stdout=subprocess.DEVNULL,
                                         stderr=subprocess.DEVNULL,
                                         stdin=subprocess.DEVNULL))
        if args.busy:
            time.sleep(1.5)                          # 让空转进程先把核占满

        # 先暖一次：第一次调用含 Python 侧 import/解析，不算进分布
        cr._eval("1+1")
        counters["list"] = counters["handshake"] = 0
        lat = []
        t0 = time.time()
        for _ in range(args.calls):
            s = time.perf_counter()
            cr._eval("document.title")
            lat.append((time.perf_counter() - s) * 1000.0)
        wall = time.time() - t0
        # 立刻取快照：后面还要量整页/周期/代理，那些不该算进「N 条命令」这一档的账
        lat_list, lat_hand = counters["list"], counters["handshake"]

        # 整页 outerHTML：采集每家至少一次，是「慢」的主体之一，单独量
        big_ms, big_kb = 0.0, 0
        if args.big:
            s = time.perf_counter()
            val = cr._html()
            big_ms = (time.perf_counter() - s) * 1000.0
            big_kb = len(val or "") // 1024

        lat.sort()
        med = statistics.median(lat)

        # ── 真实页面周期：一家公司要付几笔往返（单条延迟说明不了问题）────
        cycle: dict = {}
        if args.cycle:
            row = ('<div class="c"><a href="#">深圳市某某科技有限公司</a>'
                   '<span>1380000' + '1111</span><span>info@example.com</span></div>')
            fixture = profile / "list.html"
            fixture.write_text("<html><head><title>搜索结果</title></head><body>"
                               "<h1>供应商搜索结果列表</h1>" + row * 90 +
                               "</body></html>", encoding="utf-8")
            page_url = fixture.as_uri()
            seen: dict = {}
            real_send = cdp_base.CdpSiteCrawler._send

            def counted(self, method, params=None, timeout=20):
                seen[method] = seen.get(method, 0) + 1
                return real_send(self, method, params, timeout)

            cdp_base.CdpSiteCrawler._send = counted
            try:
                cr._goto(page_url)                    # 暖一轮，不进分布
                walls, per_list, per_hand = [], [], []
                for _ in range(args.cycle):
                    a, b = counters["list"], counters["handshake"]
                    s = time.perf_counter()
                    cr._goto(page_url)                # 导航 + 就绪轮询 + 拦检
                    cr._html()                        # 取整页交给解析（每家至少一次）
                    walls.append((time.perf_counter() - s) * 1000.0)
                    per_list.append(counters["list"] - a)
                    per_hand.append(counters["handshake"] - b)
                walls.sort()
                cycle = {
                    "cycles": args.cycle,
                    "cmds_per_cycle": sum(seen.values()) / args.cycle,
                    "list_per_cycle": statistics.median(per_list),
                    "handshake_per_cycle": statistics.median(per_hand),
                    "median_ms": round(statistics.median(walls), 1),
                    "p95_ms": round(walls[min(len(walls) - 1, int(len(walls) * 0.95) - 1)], 1),
                    "by_method": dict(sorted(seen.items(), key=lambda kv: -kv[1])),
                }
            finally:
                cdp_base.CdpSiteCrawler._send = real_send

        # ── 代理污染：本机 CDP 接口今天没设 no_proxy 绕过 ────────────────
        proxy: dict = {}
        if args.proxy:
            # 用一个文档保留地址（RFC 5737 TEST-NET，没人应答也不被本机网段吞掉）做代理：真实场景就是公司代理/VPN 网关被下线，
            # 或 Clash 之类的工具设了 HTTP_PROXY 但本机直连 127.0.0.1 的 CDP 接口
            # 被它接管。指 127.0.0.1:9 只会立刻 refused（反而测不出等待成本）。
            os.environ["HTTP_PROXY"] = "http://192.0.2.1:1"
            os.environ["http_proxy"] = os.environ["HTTP_PROXY"]
            fails, pw = 0, []
            try:
                for _ in range(3):                    # 每次最多 4s（_get_tabs 的 timeout），别跑久
                    s = time.perf_counter()
                    v = cr._eval("document.title")
                    pw.append((time.perf_counter() - s) * 1000.0)
                    if v is None:
                        fails += 1
            finally:
                for k in ("HTTP_PROXY", "http_proxy"):
                    os.environ.pop(k, None)
            proxy = {"ok": fails == 0, "fails": fails, "tries": len(pw),
                     "median_ms": round(statistics.median(pw), 1) if pw else None}

        p95 = lat[min(len(lat) - 1, int(len(lat) * 0.95) - 1)] if len(lat) > 1 else lat[0]
        print("──── CDP 往返成本实测（生产代码路径，本机）────")
        print(f"  现场条件            : 标签 +{args.tabs} 个、CPU 空转 {args.busy} 路、"
              f"大 DOM={'是' if args.big else '否'}")
        print(f"  控制面会话          : trust_env={shared_chrome.local_session().trust_env}"
              f"（False=本机调试端口不读 HTTP_PROXY/HTTPS_PROXY，代理/VPN 不再掺一脚）")
        print(f"  命令次数            : {args.calls} 次 Runtime.evaluate")
        print(f"  列标签 HTTP 次数    : {lat_list}   （每条命令 {lat_list/args.calls:.1f} 次）")
        print(f"  websocket 握手次数  : {lat_hand}   （每条命令 {lat_hand/args.calls:.1f} 次）")
        print(f"  单条延迟 中位/P95   : {med:.1f} ms / {p95:.1f} ms")
        print(f"  {args.calls} 条总耗时       : {wall:.2f} s")
        if args.big:
            print(f"  整页 outerHTML     : {big_ms:.1f} ms（页面 {big_kb} KB）")
        if cycle:
            print(f"  ── 真实页面周期（_goto + 就绪轮询 + 拦检 + 取整页）──")
            # 这几句话必须**由实测数推导**：优化前后「每条命令夹不夹握手」是不同的事实，
            # 把结论写死在探针里，复测时它就会开始说谎。
            hpc = cycle["handshake_per_cycle"]
            lpc = cycle["list_per_cycle"]
            print(f"  一轮 CDP 命令条数   : {cycle['cmds_per_cycle']:.1f} 条"
                  f"（其中列标签 {lpc:.1f} 次、握手 {hpc:.1f} 次）")
            print(f"  一轮耗时 中位/P95   : {cycle['median_ms']} ms / {cycle['p95_ms']} ms")
            print(f"  命令构成            : "
                  + "、".join(f"{k}×{v // max(1, cycle['cycles'])}"
                              for k, v in list(cycle["by_method"].items())[:6]))
            if hpc >= 0.8 or lpc >= 0.8:
                print(f"     ↑ 每家公司至少一轮，一轮 {cycle['cmds_per_cycle']:.0f} 条命令里"
                      f"有 {hpc:.0f} 次现开 websocket、{lpc:.0f} 次列标签 HTTP——"
                      f"这些全是协议开销，不做任何采集工作。")
            else:
                print(f"     ↑ 一轮 {cycle['cmds_per_cycle']:.0f} 条命令已全部跑在同一条持久连接上："
                      f"列标签/握手只在建连时付一次（≈{lpc:.1f}/{hpc:.1f} 次每轮），"
                      f"纯协议开销从每命令降到每连接。")
        if proxy:
            print(f"  ── 代理污染（HTTP_PROXY 指向一个不会应答的地址）──")
            print(f"  单条命令            : {'仍可用' if proxy['ok'] else '拿不到结果'}"
                  f"（{proxy['tries']} 次里 {proxy['fails']} 次返回空）"
                  + (f"，中位 {proxy['median_ms']} ms" if proxy["median_ms"] else ""))
            if proxy["ok"] and proxy.get("median_ms", 1e9) < 100:
                print("     ↑ 控制面会话 trust_env=False：本机调试端口不读 *_PROXY，"
                      "代理/VPN/抓包工具不参与。")
            else:
                print("     ↑ requests 默认读 HTTP_PROXY 环境变量：开着系统代理/VPN 的电脑，"
                      "每次列标签都要被代理转一圈，超时后被 except 吞成空列表。")
        print(f"  等一等式的意义       : 一次页面等待按 0.5s 轮询 ≈ "
              f"{int(0.5*1000/max(med,0.01))} 条命令/半秒，"
              f"10 秒就绪窗口要打 {int(10*1000/max(med,0.01))} 次往返")
        # 交给调用方存档：优化后重跑同一条命令，对比这两行就够了
        print(f"PROBE_JSON {json.dumps({'calls': args.calls, 'tabs': args.tabs, 'busy': args.busy, 'list': lat_list, 'handshake': lat_hand, 'median_ms': round(med, 2), 'p95_ms': round(p95, 2), 'wall_s': round(wall, 2), 'big_ms': round(big_ms, 2), 'big_kb': big_kb, 'cycle': cycle, 'proxy': proxy}, ensure_ascii=False)}")
        return 0
    finally:
        for h in hogs:
            h.terminate()
        for h in hogs:
            try:
                h.wait(timeout=5)
            except Exception:
                h.kill()
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()
        shutil.rmtree(profile, ignore_errors=True)


if __name__ == "__main__":
    os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    sys.exit(main())
