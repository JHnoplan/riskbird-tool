"""离线验证「打开网页要加载很久」这条链路的修复：本地 chromedriver 优先，不每次联网问 Selenium Manager。

覆盖（全程不起浏览器、不联网，webdriver.Chrome 用假函数顶掉）：
1. 驱动查找顺序：与当前 Chrome 主版本一致的本地驱动排最前，随 exe 打包的次之，旧版本垫底；
2. 建会话必须先试本地驱动，一次命中就不该再有 Selenium Manager；
3. 本地驱动版本不匹配时换下一个，而不是当场失败；
4. 本地全落空才回退 Selenium Manager，日志要说清这一次为什么会慢；连 SM 也失败时报一句人话；
5. 打包版（_MEIPASS）与 exe 同目录的驱动都要认（老判据指向项目内目录，exe 里永不成立）；
6. 分阶段耗时读数：不到 3 秒不写日志，且不重设起点（慢要能累加到最后那一步）；
7. 交付路径检查：随包的两个 Selenium 爬虫里不能再有裸 webdriver.Chrome(...)。
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

    def __call__(self, service=None, options=None):
        path = service.executable_path if service is not None else "<selenium-manager>"
        self.calls.append(path)
        if path in self.fail_for:
            raise RuntimeError(self.fail_for[path])
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
    FakeService.stopped = []


def make_tree(tmp: Path):
    """造一个假的 Selenium Manager 缓存目录：里面躺着好几个历史版本的驱动"""
    cache = tmp / "cache" / "selenium" / "chromedriver"
    for ver in ("120.0.6099.109", "141.0.7390.122", "142.0.7444.55"):
        d = cache / "plat" / ver
        d.mkdir(parents=True, exist_ok=True)
        (d / EXE).write_text("x", encoding="utf-8")
    return cache


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
    assert any("本地驱动不可用" in m for m in logs), logs
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
    for fname in ("qizhidao_crawler.py", "riskbird_crawler_v2.py"):
        src = Path(fname).read_text(encoding="utf-8")
        bare = [ln.strip() for ln in src.split("\n") if re.search(r"webdriver\.Chrome\(", ln)]
        assert not bare, f"{fname} 还在自己建会话：{bare}"
        assert "import chrome_driver" in src and "chrome_driver.create(" in src, fname
    print("⑦ 企知道/风鸟都改走「本地驱动优先」的建会话入口 ✓")
finally:
    for k in [k for k in sys.modules if k.startswith("selenium")]:
        sys.modules.pop(k, None)
    sys.modules.update(_real_selenium)
    sys.frozen = False
    sys._MEIPASS = ""

print("CHROME_DRIVER_OFFLINE_OK")
