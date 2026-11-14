"""离线验证「打开网页要加载很久」这条链路的修复：本地 chromedriver 优先，不每次联网问 Selenium Manager。

覆盖（全程不起浏览器、不联网，webdriver.Chrome 用假函数顶掉）：
1. 驱动查找顺序：与当前 Chrome 主版本一致的本地驱动排最前，随 exe 打包的次之，旧版本垫底；
2. 建会话必须先试本地驱动，一次命中就不该再有 Selenium Manager；
3. 本地驱动版本不匹配时换下一个，而不是当场失败；
4. 本地全落空才回退 Selenium Manager，日志要说清这一次为什么会慢；连 SM 也失败时报一句人话；
5. 打包版（_MEIPASS）与 exe 同目录的驱动都要认（老判据指向项目内目录，exe 里永不成立）；
6. 分阶段耗时读数：不到 3 秒不写日志，且不重设起点（慢要能累加到最后那一步）；
7. 交付路径检查：随包的两个 Selenium 爬虫里不能再有裸 webdriver.Chrome(...)；
8. PATH 去重：同一目录被挂几百遍也只算一条坏目录，配套目录不误伤；
9. 版本/连接归因分界：差在余量内不判「版本不符」，端口问不到一律判连接；
10. 附加模式：共享 Chrome 一个标签都没开时先补开空白标签（实机挂死的根因）；
11. 附加失败两类归因：不接连接原地退避重试、端口不通一句人话，两种都不联网；
12. 驱动下载源连不上时不去阻塞等 Selenium Manager，几秒内给可照做的提示；
13. 本机 CDP 探测用真 requests 跑一遍：代理劫持、接口参数用错都当场红。
"""
import sys
import tempfile
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import chrome_driver as cd  # noqa: E402

EXE = "chromedriver.exe" if sys.platform == "win32" else "chromedriver"

# ── 假 selenium：只把「建会话」这一步换成可记录的桩 ──────────────
_real_selenium = {k: v for k, v in sys.modules.items() if k.startswith("selenium")}


class FakeChrome:
    def __init__(self):
        self.calls = []
        self.fail_for = {}          # 驱动路径 → 异常（模拟版本不匹配/驱动起不来）
        self.fail_all = ""          # 所有驱动都报同一句（模拟「浏览器不接连接」）
        self.on_call = None         # 附加模式要在这里记「建会话那一刻有几个标签」

    def __call__(self, service=None, options=None):
        path = service.executable_path if service is not None else "<selenium-manager>"
        self.calls.append(path)
        if self.on_call:
            self.on_call(path)
        if path in self.fail_for:
            raise RuntimeError(self.fail_for[path])
        if self.fail_all:
            raise RuntimeError(self.fail_all)
        return types.SimpleNamespace(used=path)


class FakeService:
    stopped = []

    def __init__(self, executable_path=None):
        self.executable_path = executable_path

    def stop(self):
        FakeService.stopped.append(self.executable_path)


wd = types.ModuleType("selenium.webdriver")
fake_chrome = FakeChrome()
wd.Chrome = fake_chrome
svc = types.ModuleType("selenium.webdriver.chrome.service")
svc.Service = FakeService
mod = types.ModuleType("selenium")
mod.webdriver = wd
sys.modules["selenium"] = mod
sys.modules["selenium.webdriver"] = wd
sys.modules["selenium.webdriver.chrome"] = types.ModuleType("selenium.webdriver.chrome")
sys.modules["selenium.webdriver.chrome.service"] = svc


def reset():
    fake_chrome.calls = []
    fake_chrome.fail_for = {}
    fake_chrome.fail_all = ""
    fake_chrome.on_call = None
    FakeService.stopped = []


def make_tree(tmp: Path):
    """造一个假的 Selenium Manager 缓存目录：里面躺着好几个历史版本的驱动"""
    cache = tmp / "cache" / "selenium" / "chromedriver"
    for ver in ("120.0.6099.109", "141.0.7390.122", "142.0.7444.55"):
        d = cache / "plat" / ver
        d.mkdir(parents=True, exist_ok=True)
        (d / EXE).write_text("x", encoding="utf-8")
    return cache


# 拨开关之前先按名字记下现场，末尾按名字还回去（原来不存在的项就直接删掉，恢复成
# 「没拨过」）。为什么不用 ``sys.frozen = False`` / ``sys._MEIPASS = ""`` 收尾：把
# ``_MEIPASS`` 留成空串等于给后面任何读它的人一个 ``Path("")`` == 当前工作目录，而那
# 正是本仓库专门钉住的「运行期文件相对 CWD」那一类缺陷；``frozen`` 留成 False 也只是
# 碰巧没人读。位置元组同理不可靠：错开一位就变成「executable=None」。
_SWITCHES = ("frozen", "_MEIPASS", "executable")
_ABSENT = object()
_SWITCH_BEFORE = {k: getattr(sys, k, _ABSENT) for k in _SWITCHES}

try:
    tmp = Path(tempfile.mkdtemp())
    cache = make_tree(tmp)
    bundled = tmp / "MEIPASS"
    bundled.mkdir()
    (bundled / EXE).write_text("x", encoding="utf-8")
    beside = tmp / "dist"
    beside.mkdir()
    (beside / EXE).write_text("x", encoding="utf-8")

    cd._cache_root = lambda: cache
    cd.chrome_major = lambda ports=None: "142"
    # 联网兜底前的「下载源够不够得着」这一问必须钉死：离线测试不许真去碰外网，
    # 而「够得着」才是第 4 节想要的老路径（连不上那条单独有一节专门验，那一节用回原函数）
    _SMR_REAL = cd.sm_source_reachable
    cd.sm_source_reachable = lambda timeout=4.0: True
    # 模拟 PyInstaller 单文件运行环境：驱动在 _MEIPASS 根目录，exe 同级也允许用户自己放一份
    sys.frozen = True
    sys._MEIPASS = str(bundled)
    sys.executable = str(beside / "RiskBirdTool.exe")

    # ── 1/5. 查找顺序 ────────────────────────────────────────
    cands = cd.candidates()
    v = lambda num: str(cache / "plat" / {"142": "142.0.7444.55",
                                          "141": "141.0.7390.122",
                                          "120": "120.0.6099.109"}[num] / EXE)
    assert cands[0] == v("142"), f"和当前 Chrome 主版本一致的本地驱动最优先：{cands}"
    assert Path(cands[1]) in (bundled / EXE, beside / EXE), f"接着才是随包/exe 同目录那份：{cands[:3]}"
    assert cands.index(v("141")) < cands.index(v("120")), f"旧版本按版本从新到旧：{cands}"
    assert all(Path(c).is_file() for c in cands), cands
    print("① 本地驱动按 Chrome 主版本排序，随 exe 打包那份紧随其后 ✓")

    # ── 2. 本地一次命中，完全不碰 Selenium Manager ────────────
    reset()
    logs = []
    drv = cd.create(options=None, log=logs.append, ports=[])
    assert drv.used == cands[0], f"必须用本地那份驱动，而不是让 SM 联网现查：{drv.used}"
    assert fake_chrome.calls == [cands[0]], fake_chrome.calls
    assert any("本地 ChromeDriver" in m for m in logs), logs
    print("② 打开网页不再每次联网查驱动：本地驱动一次命中 ✓")

    # ── 3. 版本不匹配就换下一个（别把「打开网页」直接打死） ────
    reset()
    fake_chrome.fail_for[cands[0]] = "SessionNotCreatedException: only supports Chrome version 142"
    logs = []
    drv = cd.create(options=None, log=logs.append, ports=[])
    assert fake_chrome.calls[0] == cands[0] and len(fake_chrome.calls) == 2, fake_chrome.calls
    assert drv.used == fake_chrome.calls[1], drv
    assert FakeService.stopped == [cands[0]], f"失败的驱动进程要收掉：{FakeService.stopped}"
    # 归因文案要能指到下一步动作：只说「本地驱动不可用」等于让人去重装驱动，而真实原因
    # 是「这份驱动只支持 Chrome 142 以外的版本」。所以判据是「版本不符 + 两个版本号 +
    # 底层异常」三件齐全，而不是某句固定话术（话术改一个字就红一条，红了又没人看得懂）。
    _skip = [m for m in logs if "换下一个" in m]
    assert _skip, logs
    assert any("版本不符" in m and "SessionNotCreatedException" in m and "142" in m
               for m in _skip), f"版本不匹配要说清是哪两个版本不符：{_skip}"
    assert "<selenium-manager>" not in fake_chrome.calls, f"还有本地驱动可试就不该联网：{fake_chrome.calls}"
    print("③ 本地驱动版本不匹配就换下一个，失败进程也收干净 ✓")

    # ── 4. 本地全落空才回退，并且把「为什么这次会慢」说清楚 ────
    reset()
    for p in cands:
        fake_chrome.fail_for[p] = "chromedriver 起不来"
    logs = []
    drv = cd.create(options=None, log=logs.append, ports=[])
    assert drv.used == "<selenium-manager>", drv
    assert any("回退 Selenium Manager" in m for m in logs), logs
    assert any("联网" in m or "会慢" in m for m in logs), f"要说清这一次为什么会慢：{logs}"

    # 断网到连 SM 也失败：报一句人话，而不是一坨堆栈冒到界面上
    reset()
    for p in cands:
        fake_chrome.fail_for[p] = "chromedriver 起不来"
    fake_chrome.fail_for["<selenium-manager>"] = \
        "WebDriverException: Message: error sending request for url (googlechromelabs...)"
    try:
        cd.create(options=None, log=lambda m: None, ports=[])
        raise AssertionError("本地与 SM 全落空时必须抛错，让上层知道浏览器起不来")
    except RuntimeError as e:
        assert "创建浏览器会话失败" in str(e), e
    print("④ 只有本地一个都不行才联网回退，全失败也只报一句人话 ✓")

    # ── 5. 大版本余量排序（2026-11-14 实机：156 驱动连 157 浏览器成功、135 连不上）──
    # 缓存里已躺着一份差 22 个大版本的 120：它必须排到 slack(2) 以内的 141 之后，
    # 否则 create 的前三名试手会把差太远的顶进去、把随包的配套那份挤出窗口
    cd._major_cache = None
    order = cd.candidates()
    i_bundled = order.index(str(bundled / EXE))
    i141, i120 = order.index(v("141")), order.index(v("120"))
    assert order[0] == v("142"), f"与浏览器精确同主版本的排第一：{order[:4]}"
    assert i_bundled < i141, f"随包配对那份要排在相近版本 141 之前：{order[:5]}"
    assert i141 < i120, f"差 1 个版本的 141 必须排在差 22 的 120 之前：{order[:5]}"
    assert i120 >= len(order) - 3, f"明知差太远的只给最后一线机会：{order[-3:]}"
    print("⑤ 大版本差在余量内的排前面、差太远的垫底，随包配套那份紧随精确命中 ✓")

    # ── 6. 分阶段耗时读数（打开网页慢在哪一步要有数字） ────────
    import time as _t
    import dual_session as ds
    out = []
    t0 = _t.monotonic()
    assert ds._stage(out.append, "很快的一步", t0) == t0 and out == [], \
        f"不到 3 秒既不写日志也不重设起点：{out}"
    ds._stage(out.append, "慢的一步", _t.monotonic() - 5)
    assert len(out) == 1 and "⏱ 慢的一步" in out[0] and "5.0" in out[0], out
    print("⑥ 打开网页每一步有秒数读数，慢在哪一步一眼能看出来 ✓")

    # ── 7. 随包代码不再自己建会话（否则每次都要问 SM） ─────────
    import re
    # 源码红线必须按**文件位置**取，不能按 CWD：从仓库根跑批时这里读的是
    # "./qizhidao_crawler.py"，不存在 → FileNotFoundError，红法看起来像「源码丢了」。
    _here = Path(__file__).resolve().parent
    for fname in ("qizhidao_crawler.py", "riskbird_crawler_v2.py"):
        src = (_here / fname).read_text(encoding="utf-8")
        bare = [ln.strip() for ln in src.split("\n") if re.search(r"webdriver\.Chrome\(", ln)]
        assert not bare, f"{fname} 还在自己建会话：{bare}"
        assert "import chrome_driver" in src and "chrome_driver.create(" in src, fname
    print("⑦ 企知道/风鸟都改走「本地驱动优先」的建会话入口 ✓")

    # ── 8. PATH 去重：同一目录被挂几百次时，坏目录只报一次、好目录不误伤 ──
    # 本机实测 PATH 里 /usr/local/bin 重复 500+ 次；不去重的话「绕过了 N 个旧驱动目录」
    # 的日志会虚报成几百，sm_clean_path 每次 is_file 也白跑几百遍
    import os as _os
    _bad_dir = str(tmp / "PATHBAD")
    _good_dir = str(tmp / "PATHGOOD")
    Path(_bad_dir).mkdir(exist_ok=True); Path(_good_dir).mkdir(exist_ok=True)
    (Path(_bad_dir) / EXE).write_text("x", encoding="utf-8")
    (Path(_good_dir) / EXE).write_text("x", encoding="utf-8")
    _ver_before = cd.driver_version
    cd.driver_version = lambda p: ("135.0.7049.84" if _bad_dir in p
                                   else "142.0.7444.55" if _good_dir in p else "")
    _path_before = _os.environ.get("PATH", "")
    # 坏目录重复出现 3 次，模拟真实 PATH 的重复挂载
    _os.environ["PATH"] = _os.pathsep.join([_bad_dir, _good_dir, _bad_dir, "/nonexistent", _bad_dir])
    try:
        bad = cd.stale_driver_path_dirs("142")
        assert bad == [_bad_dir], f"PATH 上重复的坏目录必须去重成一条、好目录不许进来：{bad}"
        assert cd.stale_driver_path_dirs("") == [], "问不到浏览器版本时不该有任何动作（宁可不绕）"
    finally:
        _os.environ["PATH"] = _path_before
        cd.driver_version = _ver_before
    print("⑧ PATH 去重：重复挂载的旧驱动目录只认一次，配套目录不误伤 ✓")

    # ── 9. 归因范围：差在余量内不算「版本不符」（2026-11-14 实机 156 连 157 成功）──
    # 这一条是上一轮修复自己留下的新坑：「主版本不等就判 version」会把好端端的
    # 156 驱动也记成版本不符，于是 0 标签引起的连接失败被查不出真凶，还白跑一趟联网
    _msg = "Message: session not created: cannot connect to chrome at 127.0.0.1:9255"
    assert cd.classify_driver_error(_msg, "135.0.7049.84", "157") == "version", \
        "差 22 个大版本仍然要判版本不符（1688 恒 0 家那条链的修复不能退）"
    assert cd.classify_driver_error(_msg, "156.0.8078.47", "157") == "connect", \
        "差 1 个大版本（实测可用）不该判版本不符，否则日志指着好驱动说「换下一个」"
    assert cd.classify_driver_error(_msg, "154.0.1234.0", "157", pages=-1) == "connect", \
        "调试端口问不到时一律算连接问题：换驱动治不了浏览器没起来"
    assert cd.classify_driver_error("only supports Chrome version 141", "141", "157") == "version", \
        "驱动自己说了只支持某个版本，差多少都算版本不符"
    print("⑨ 版本/连接归因分界：余量内不判版本，端口不通判连接 ✓")

    # ── 10. 附加模式：共享 Chrome 一个标签都没开时，先补开再建会话 ────────────
    # 实机根因（2026-11-14 21:15 企知道通道挂死）：0 page target 时 chromedriver 报
    # 「cannot connect to chrome」或直接挂住，浏览器本身活得好好的
    import time as _time_mod
    _req_before, _sleeps = cd.requests, []
    _time_before = cd.time
    cd.time = types.SimpleNamespace(monotonic=_time_mod.monotonic,
                                    sleep=lambda s: _sleeps.append(s))

    class _Resp:
        def __init__(self, payload): self._p = payload
        def json(self): return self._p

    class _FakeSession:
        """chrome_driver 问本机端口用的是 Session，桩就要按 Session 的形状给"""

        def __init__(self, owner): self.o = owner
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def close(self): pass

        def get(self, url, timeout=None):
            assert url.endswith("/json/list"), url
            if self.o.pages < 0:
                raise OSError("拒绝连接")
            self.o.gets += 1
            return _Resp([{"type": "page"}] * self.o.pages)

        def put(self, url, timeout=None):
            assert "about:blank" in url, url
            self.o.puts.append(url)
            self.o.pages += 1
            return _Resp({"id": "TARGET"})

    class _FakeRequests:
        """只认本机调试端口的 CDP 调用，顺手记下 PUT 补标签发生了几次"""

        def __init__(self, pages=0, sm_ok=True):
            self.pages, self.sm_ok, self.puts, self.gets = pages, sm_ok, [], 0

        def Session(self): return _FakeSession(self)

        def head(self, url, timeout=None):
            if not self.sm_ok:
                raise OSError("网络不可达")
            return _Resp({})

    reset()
    frq = _FakeRequests(pages=0)
    cd.requests = frq
    _seen_pages, logs = [], []
    fake_chrome.on_call = lambda p: _seen_pages.append(frq.pages)
    opts = types.SimpleNamespace(debugger_address="127.0.0.1:9255")
    drv = cd.create(options=opts, log=logs.append, ports=[9255])
    assert drv.used == "local" or Path(drv.used).name == EXE, drv
    assert len(frq.puts) == 1, f"0 标签时必须补开且只补一次：{frq.puts}"
    assert _seen_pages and _seen_pages[0] >= 1, \
        f"补开标签要发生在建会话之前，不能带着 0 标签去撞驱动：{_seen_pages}"
    assert not any("Selenium Manager" in m for m in logs), f"补个标签就成的事不该联网：{logs}"
    print("⑩ 共享浏览器 0 标签：先补开空白标签再附加（实机挂死的那条根因）✓")

    # 已有标签时一次都不该 PUT（不许动了用户的窗口）
    reset()
    frq2 = _FakeRequests(pages=3)
    cd.requests = frq2
    cd.create(options=opts, log=lambda m: None, ports=[9255])
    assert frq2.puts == [], f"有标签就不该补开：{frq2.puts}"

    # ── 11. 附加模式连接类全败：原地退避重试，绝不联网；端口没起直接报人话 ──
    reset()
    frq3 = _FakeRequests(pages=1)        # 浏览器活着、有标签，只是那一刻不接连接
    cd.requests = frq3
    fake_chrome.fail_all = "Message: session not created: cannot connect to chrome at 127.0.0.1:9255"
    _sleeps.clear()
    try:
        cd.create(options=opts, log=lambda m: None, ports=[9255])
        raise AssertionError("附加全败时必须抛错给上层去重启 Chrome")
    except RuntimeError as e:
        assert "附加到已运行的 Chrome 失败" in str(e), e
    assert "<selenium-manager>" not in fake_chrome.calls, \
        "浏览器不接连接时联网换驱动只会凭空多等几分钟"
    assert _sleeps == [3, 6], f"连接类失败要原地退避重试一遍：{_sleeps}"

    reset()
    frq4 = _FakeRequests(pages=-1)        # 调试端口问不到（浏览器已退出/没开调试端口）
    cd.requests = frq4
    fake_chrome.fail_all = "Message: devtools listening port didn't answer"
    try:
        cd.create(options=opts, log=lambda m: None, ports=[9255])
        raise AssertionError("调试端口不通时必须抛错")
    except RuntimeError as e:
        assert "调试端口没有响应" in str(e), e
    assert "<selenium-manager>" not in fake_chrome.calls, \
        f"浏览器都不在跑，联网换驱动毫无意义：{fake_chrome.calls}"
    # 端口探测本身可能被环境误报（代理、防火墙），所以还是让本地驱动试一轮再下结论；
    # 但归因必须按端口证据走 connect，绝不说「版本不符、换驱动」
    assert fake_chrome.calls and fake_chrome.calls[0] != "<selenium-manager>", fake_chrome.calls
    print("⑪ 附加失败两类归因各归各位：不接连接原地重试，端口不通一句人话，都不联网 ✓")

    # ── 12. 下载源连不上时不去联网兜底，而是几秒内给一句能照着做的提示 ────────
    # 本机 googlechromelabs 不可达（实测 HTTP 000）：Selenium Manager 的等待是阻塞的，
    # 不做这一步预检查，界面表现就是「日志停住不动、任务挂着不结束」。
    # 这一节用回 sm_source_reachable 的原函数，验的是「真问不到 → 真不去」整条链。
    reset()
    cd.requests = _FakeRequests(pages=1, sm_ok=False)
    cd.sm_source_reachable = _SMR_REAL
    for p in cd.candidates():
        fake_chrome.fail_for[p] = "SessionNotCreatedException: only supports Chrome version 999"
    try:
        cd.create(options=None, log=lambda m: None, ports=[])
        raise AssertionError("驱动都不配套又连不上下载源时必须抛错")
    except RuntimeError as e:
        assert "驱动下载源此刻连不上" in str(e), e
        assert "配套驱动" in str(e) or ".cache" in str(e), f"要说清下一步能干什么：{e}"
    assert "<selenium-manager>" not in fake_chrome.calls, \
        f"明知连不上还去等 Selenium Manager：{fake_chrome.calls}"
    print("⑫ 下载源连不上：不去阻塞等联网，几秒内报「换驱动/升 Chrome」这句人话 ✓")

    # ── 13. 本机 CDP 探测用真 requests 走一遍 ──────────────────────────────
    # 前两节的桩挡不住两类问题：① 把 trust_env 当 request 参数传（TypeError 被
    # except 吞掉 → 判据全瞎，表现和「浏览器没起」一模一样）；② 用户挂了代理时
    # 「访问 127.0.0.1」被代理劫持。这里起一个本机 HTTP 服务当假 CDP，并把
    # HTTP_PROXY 指向一个没人听的端口，两条一起验。
    import http.server as _hs
    import json as _json
    import os as _os3
    import threading as _thr
    import requests as _real_requests

    _st = {"puts": 0, "pages": 0}

    class _CdpHandler(_hs.BaseHTTPRequestHandler):
        def _send(self, obj):
            body = _json.dumps(obj).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self._send([{"type": "page"}] * _st["pages"])

        def do_PUT(self):
            _st["puts"] += 1
            _st["pages"] += 1
            self._send({"id": "TARGET", "url": "about:blank"})

        def log_message(self, *a):
            pass

    _srv = _hs.HTTPServer(("127.0.0.1", 0), _CdpHandler)
    _port = _srv.server_address[1]
    _thr.Thread(target=_srv.serve_forever, daemon=True).start()
    cd.requests = _real_requests                    # 这一节不桩 requests
    _env_keys = ("HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy", "NO_PROXY", "no_proxy")
    _env_before = {k: _os3.environ.get(k) for k in _env_keys}
    _os3.environ["HTTP_PROXY"] = _os3.environ["ALL_PROXY"] = "http://127.0.0.1:1"   # 黑洞代理
    try:
        assert cd.cdp_pages(f"127.0.0.1:{_port}") == 0, \
            "本机端口问得到、0 标签就如实报 0（代理不许插手同机回环）"
        n = cd.ensure_attachable_pages(f"127.0.0.1:{_port}", log=lambda m: None, timeout=3)
        assert n == 1 and _st["puts"] == 1, f"补开标签必须真的 PUT 成功：{n}／{_st['puts']} 次"
        assert cd.cdp_pages(f"127.0.0.1:{_port}") == 1, "补开后应问得到 1 个标签"
    finally:
        for _k, _v in _env_before.items():
            if _v is None:
                _os3.environ.pop(_k, None)
            else:
                _os3.environ[_k] = _v
        _srv.shutdown()
        _srv.server_close()
    print("⑬ 本机 CDP 探测用真 requests：代理劫持与接口用错都当场红 ✓")

    cd.requests, cd.time, cd.sm_source_reachable = _req_before, _time_before, _SMR_REAL
finally:
    for k in [k for k in sys.modules if k.startswith("selenium")]:
        sys.modules.pop(k, None)
    sys.modules.update(_real_selenium)
    for _k, _v in _SWITCH_BEFORE.items():         # 按名字还回去，不留自造的假现场
        if _v is _ABSENT:
            sys.__dict__.pop(_k, None)
        else:
            setattr(sys, _k, _v)

print("CHROME_DRIVER_OFFLINE_OK")
