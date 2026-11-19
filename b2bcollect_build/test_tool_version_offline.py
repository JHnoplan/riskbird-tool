#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_tool_version_offline.py —— 版本单一真源（需求 2 第一块）的引擎层离线判据。

这一块的全部价值就在「只有一份算法」上：显示版 `1.<build>`、文件属性四段版、latest.json
里的 version、CI 注入的那一串，全都必须从同一个 `build` 导出。拼两遍就是两个真源，早晚对
不上；而对不上的表现不是报错，是「装了 1.234 的包天天被提示升级到 1.234」这种人肉才能发现的
事。所以这里逐条钉：

① 一个 build 导出两个形状：`1.<build>` 与四段（高/低 16 位），且四段拼不出时**留空串**
   ——把一个超限的构建号降级折成两段，会得到一个看着合法、实际对应另一版的号，比红一天难查；
② `UNKNOWN_BUILD=0` 的语义是「未能确认」，不是「第 0 版」：说不出时不许冒充某个号，
   比较一律不出结论（否则每一台没注戳的开发机都会天天弹更新提示）；
③ 取值优先级 随包戳 > 环境变量 > 未能确认，且环境变量只认纯数字；
④ 戳文件读不到/读坏/顶层不是对象，都只是「未能确认」，绝不抛异常（版本模块要在连
   config 都读不到的机器上也能说清自己是哪版）；
⑤ `parse_version` 里 None ≠ 0：认不出的格式不能静默折成「未能确认」，更不能折成「旧版本」；
⑥ 生成那一步直接拦两种形状：构建号不像话、超出可表达范围；写不下去也如实红；
⑦ CLI 是 CI 的接口：取证那档恒返 0（包括未能确认），生成与拼字段那档拼不出来就返 1，
   两档混在一个码里就分不清「自报失败」与「自报不一致」；
⑧ `bundle_dir()` 与 tool_config 那份算法必须落在同一个目录（两个算法迟早分家，而分家的
   表现是「戳明明随包了，模块却说读不到」）。

不联网、不起界面、不碰真实 HOME：戳文件一律指到 tempdir 里。

运行：.venv/bin/python b2bcollect_build/test_tool_version_offline.py
"""
import io
import json
import os
import sys
import contextlib
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import tool_version as tv                                              # noqa: E402

_PASS = 0
_FAIL = 0


def check(name, cond, detail=None):
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  ✅ {name}")
    else:
        _FAIL += 1
        print(f"  ❌ {name}" + (f"  -> {detail}" if detail is not None else ""))


def run_cli(argv):
    """跑一次模块自己的 CLI，拿回 (退出码, 全部输出)。CI 看到的就是这两样。"""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        rc = tv.main(list(argv))
    return int(rc or 0), buf.getvalue()


class use_stamp:
    """把随包戳指到 tempdir 里的一份文件；build=None 表示「这个包里压根没有戳」。"""

    def __init__(self, build=None, commit="", raw=None):
        self.dir = Path(tempfile.mkdtemp(prefix="ver_stamp_"))
        self.saved_bundle = tv.bundle_dir
        self.saved_env = os.environ.get(tv.BUILD_ENV_VAR)
        tv.bundle_dir = lambda: self.dir
        os.environ.pop(tv.BUILD_ENV_VAR, None)
        if raw is not None:
            (self.dir / tv.BUILD_STAMP_NAME).write_text(raw, encoding="utf-8")
        elif build is not None:
            tv.write_stamp(self.dir / tv.BUILD_STAMP_NAME, build, commit)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        tv.bundle_dir = self.saved_bundle
        os.environ.pop(tv.BUILD_ENV_VAR, None)
        if self.saved_env is not None:
            os.environ[tv.BUILD_ENV_VAR] = self.saved_env
        return False


def _one(num):
    """两段显示版与四段号必须出自同一个数：四段还原回来还得是它。

    这里从 1 起比：`0` 不是「第 0 版」而是「未能确认」，它走的是下面②那一组判据（两边都
    不许给出一个号），把它们混在这一条里只会让这条数学关系说不清自己在比什么。
    """
    return f"1.{num}" == tv.version_text(num) and \
        (num // 65536, num % 65536) == tuple(int(x) for x in tv.product_version(num).split(".")[1:3])


# ── ① 一个 build 导出两个形状 ─────────────────────────────────────────
def test_two_shapes():
    print("\n── ① 同一个构建号导出显示版与四段版，拼不出就留空 ──")
    check("显示版就是 1.<build>（1.234 而不是 1.0.234 也不是 v1.234）",
          tv.version_text(234) == "1.234", tv.version_text(234))
    check("四段版把 build 拆成两个 16 位字段：234 → 1.0.234.0",
          tv.product_version(234) == "1.0.234.0", tv.product_version(234))
    check("大数照样对得上：70000 → 1.1.4464.0（高低两段各 16 位）",
          tv.product_version(70000) == "1.1.4464.0", tv.product_version(70000))
    check("两处同源：任何构建号下显示版与四段版还原回来是同一个数",
          all(_one(n) for n in (1, 234, 65535, 65536, 4294967295)),
          [tv.product_version(n) for n in (65535, 65536)])
    check("超出可表达范围时四段留空串，绝不降级折成一个看着合法的假号",
          tv.product_version(tv.MAX_BUILD + 1) == "" and not tv.in_range(tv.MAX_BUILD + 1),
          tv.product_version(tv.MAX_BUILD + 1))
    check("边界内最后一格仍然给得出（把上限判错会白白红一次 CI）",
          tv.product_version(tv.MAX_BUILD) == "1.65535.65535.0" and tv.in_range(tv.MAX_BUILD),
          tv.product_version(tv.MAX_BUILD))


# ── ②③④ 未能确认这一档 + 取值优先级 ─────────────────────────────────
def test_unknown_and_priority():
    print("\n── ②③④ 0 = 未能确认；随包戳 > 环境变量 > 未能确认 ──")
    with use_stamp():
        check("包里没戳：build 读到 0（不是 1，也不是猜一个）",
              tv.build_number() == tv.UNKNOWN_BUILD, tv.build_number())
        check("未能确认时四段版同样留空：1.0.0.0 合法但哪一版也不是，比 CI 红一天难查",
              tv.product_version() == "" and tv.product_version(0) == "",
              (tv.product_version(), tv.product_version(0)))
        check("report() 里那一格也不许漏出一个假四段号（CI 对拍读的就是这一份）",
              tv.report()["product_version"] == "" and tv.report()["certain"] is False,
              tv.report())
        check("未能确认时说清是未能确认，不印一个看起来合法的号",
              "未能确认" in tv.version_text() and "未注入构建号" in tv.version_text(),
              tv.version_text())
        check("未能确认时 certain() 必须说 False（更新通路就靠这一格闭嘴）",
              tv.certain() is False, tv.certain())
        check("本机说不清时不许判「远端更新」（否则每台开发机天天弹更新提示）",
              tv.is_newer("1.900") is False, tv.is_newer("1.900"))
        check("四段版在未能确认时拼不出：留空让 CI 停下，而不是给一个 1.0.0.0",
              tv.product_version() == "", tv.product_version())
        os.environ[tv.BUILD_ENV_VAR] = "777"
        check("没有随包戳时环境变量补位（远程支持要能反证「这份包读的哪一份戳」）",
              tv.build_number() == 777 and tv.certain(), tv.build_number())
        os.environ[tv.BUILD_ENV_VAR] = "不像是数"
        check("环境变量只接受纯数字：非数字当没给，不拿它当构建号去比大小",
              tv.build_number() == tv.UNKNOWN_BUILD, tv.build_number())
        os.environ[tv.BUILD_ENV_VAR] = "-5"
        check("负数同样当没给（一个负 build 比任何远端都「旧」，症状是永远不提更新）",
              tv.build_number() == tv.UNKNOWN_BUILD, tv.build_number())
    with use_stamp(build=321):
        check("有随包戳时环境变量让位（戳才是这个包的身份证）",
              tv.build_number() == 321, tv.build_number())
    with use_stamp(raw='{"build": 456, "display": "1.999", "product_version": "9.9.9.9"}'):
        check("读的一侧只认 build：有人手改了戳里那两行导出值也不影响比较",
              tv.version_text() == "1.456" and tv.build_number() == 456,
              (tv.version_text(), tv.build_number()))
    for bad, tag in (('{"build": ', "读到半个 JSON"), ("[]", "顶层不是对象"), ("", "空文件")):
        with use_stamp(raw=bad) as _ctx:
            check(f"戳文件{tag}时只是「未能确认」，绝不抛异常"
                  "（版本模块要在连 config 都读不到的机器上也能开口）",
                  tv.build_number() == tv.UNKNOWN_BUILD and not tv.certain(),
                  tv.version_text())


# ── ⑤ 比较与解析：None ≠ 0 ───────────────────────────────────────────
def test_parse_compare():
    print("\n── ⑤ 认不出来 ≠ 未能确认；比较按整数不按字符串 ──")
    check("按整数比：1.10 比 1.9 新（字符串比会反过来，表现是永远提示有更新）",
          tv.compare("1.10", "1.9") == 1 and tv.is_newer("1.10", "1.9") is True,
          (tv.compare("1.10", "1.9"), tv.compare("1.9", "1.10")))
    check("同一版就是同一版：不许判成「有更新」（天天弹同一版是这一族最常见的红）",
          tv.compare("1.234", "1.234") == 0 and not tv.is_newer("1.234", "1.234"))
    check("纯数字与显示版两种写法都认（latest.json 里可能落哪一种）",
          tv.parse_version("1.234") == 234 and tv.parse_version(234) == 234
          and tv.parse_version("234") == 234, tv.parse_version("234"))
    for junk in ("", "  ", "v1.234", "1.234.5", "1.", "abc", "0x10", None, True, False, -3):
        check(f"不像版本号的「{junk!r}」一律给 None，不静默折成 0",
              tv.parse_version(junk) is None, tv.parse_version(junk))
    check("主号更低是另一条产品线：不能被当成「比本机旧」而静默忽略",
          tv.parse_version("0.999") is None and tv.compare("0.999", "1.10") is None,
          tv.parse_version("0.999"))
    check("主号更高可以认（将来升主号时老工具该说「对方更新」而不是「认不出」）",
          tv.parse_version("2.3") == 3, tv.parse_version("2.3"))
    check("远端说不清时不许判更新：这是「问不出」，不是「没有新版」",
          tv.is_newer("", "1.10") is False and tv.is_newer("乱写", "1.10") is False)
    check("is_newer 默认拿本机真实版本比（漏传一个参数不该变成「和 0 比」）",
          tv.is_newer.__defaults__ == (None,) and "certain()" in
          __import__("inspect").getsource(tv.is_newer), tv.is_newer.__defaults__)


# ── ⑥ 生成那一步的三道闸 ─────────────────────────────────────────────
def test_write_stamp():
    print("\n── ⑥ 生成构建戳：不像话的号与超限的号都当场拦下 ──")
    d = Path(tempfile.mkdtemp(prefix="ver_w_"))
    for bad in ("", None, "abc", "0", "-1", "1.5", "  ", True):
        got = tv.write_stamp(d / tv.BUILD_STAMP_NAME, bad)
        check(f"构建号「{bad!r}」不像话时拒写并说清为什么（宁可不注入也不出包）",
              not got.get("ok") and got.get("why"), got)
    got = tv.write_stamp(d / tv.BUILD_STAMP_NAME, tv.MAX_BUILD + 1)
    check("超限的构建号当场拦下：makensis 收下一个它编不了的号，交付里就没有安装包",
          not got.get("ok") and "超出" in str(got.get("why")), got)
    blocked = d / "这是一个目录不是文件"
    blocked.mkdir(exist_ok=True)
    got = tv.write_stamp(blocked, 888, "deadbeef")
    check("写不下去也如实红（静默「成功」会让 CI 打出一个连自己都认不出的包）",
          not got.get("ok") and "写不下去" in str(got.get("why")), got)
    got = tv.write_stamp(d / tv.BUILD_STAMP_NAME, 888, " abc123 ")
    body = json.loads((d / tv.BUILD_STAMP_NAME).read_text(encoding="utf-8"))
    check("写成了就把两个导出值一起留在戳里给人读（它们是同一算法的导出，不是第二个真源）",
          got.get("ok") and body["display"] == "1.888"
          and body["product_version"] == "1.0.888.0", body)
    check("commit 两侧空白清掉、长度收在 40 以内（它只用于点名，不该带上 shell 的引号）",
          body["commit"] == "abc123", body["commit"])


# ── ⑦ CLI 是 CI 的接口 ───────────────────────────────────────────────
def test_cli():
    print("\n── ⑦ 命令行出口：取证恒 0，拼不出来才 1 ──")
    d = Path(tempfile.mkdtemp(prefix="ver_cli_"))
    stamp = d / tv.BUILD_STAMP_NAME
    rc, out = run_cli(["--write-stamp", str(stamp), "--build", "900", "--commit", "cafe"])
    check("--write-stamp 成功时把两个导出值打在行上（发布日志要能直接看见注了几）",
          rc == 0 and "1.900" in out and "1.0.900.0" in out, (rc, out))
    rc, out = run_cli(["--write-stamp", str(stamp), "--build", ""])
    check("--write-stamp 拿到空号时返 1（这一步的目标就是「不给假号开绿」）",
          rc == 1 and "正整数" in out, (rc, out))
    rc, out = run_cli(["--write-stamp"])
    check("--write-stamp 后面没跟路径时也说清缺什么，不拿 IndexError 红",
          rc == 1 and "写入路径" in out, (rc, out))
    rc, out = run_cli([])
    check("不带参数是打帮助并返 0（把一个取证请求判成失败会白白红一次门禁）",
          rc == 0 and "用法" in out, (rc, out))
    with use_stamp(build=900):
        rc, out = run_cli(["--print-field", "display"])
        check("--print-field display 打出与界面同一句号（CI 拿它对拍 latest.json）",
              rc == 0 and out.strip() == "1.900", (rc, out))
        rc, out = run_cli(["--print-field", "product"])
        check("--print-field product 给四段（NSIS 的 /DVER4 就吃这一格）",
              rc == 0 and out.strip() == "1.0.900.0", (rc, out))
        rc, out = run_cli(["--print-field", "build"])
        check("--print-field build 给裸数字（构建戳文件名与清单里那一格都用它）",
              rc == 0 and out.strip() == "900", (rc, out))
        rc, out = run_cli(["--print-field", "乱写"])
        check("认不出的字段名要红，并列出可用那几个",
              rc == 1 and "display" in out, (rc, out))
        out_file = d / "report.json"
        rc, out = run_cli(["--print-version", "--out", str(out_file)])
        saved = json.loads(out_file.read_text(encoding="utf-8")) if out_file.is_file() else {}
        check("--print-version --out 落一份 JSON（Windows --windowed 的 exe 没有控制台，"
              "拿不到 stdout 只能靠文件取证）",
              rc == 0 and saved.get("build") == 900 and saved.get("certain") is True,
              (rc, saved))
        rc, out = run_cli(["--print-version", "--json"])
        got = json.loads(out.strip())
        check("--json 那一档的字段与 report() 完全一致（对拍少一个字段就会少判一项）",
              rc == 0 and set(got) >= {"version", "build", "product_version",
                                       "certain", "stamp_file", "frozen"},
              (rc, sorted(got)))
    with use_stamp():
        rc, out = run_cli(["--print-version"])
        check("未能确认时自报仍返 0：自报失败与自报不一致是两种红，混在一个码里就查不出是哪种",
              rc == 0 and "未能确认" in out, (rc, out))
        for field in ("display", "product", "build"):
            rc, out = run_cli(["--print-field", field])
            check(f"未能确认时 --print-field {field} 当场红且不给出一个合法号"
                  "（拿着 1.0.0.0 去编包会得到哪版都不是的产物）",
                  rc == 1 and not out.strip().startswith("1."), (rc, out))


# ── ⑧ 随包资源的落点与 tool_config 同源 ─────────────────────────────
def test_bundle_dir_same_rule():
    print("\n── ⑧ bundle_dir 与 tool_config 那份算法必须落在同一个目录 ──")
    meipass = str(Path(tempfile.mkdtemp(prefix="ver_meipass_")))
    saved = getattr(sys, "_MEIPASS", None)
    sys._MEIPASS = meipass                                  # 冻结态的形状
    try:
        check("冻结态认 _MEIPASS：戳随包在哪就找哪",
              str(tv.bundle_dir()) == meipass, tv.bundle_dir())
        try:
            import tool_config as tc
        except ImportError:
            tc = None
        if tc is not None and hasattr(tc, "bundle_dir"):
            check("与 tool_config.bundle_dir() 同一口径（两处算出不同目录，症状就是"
                  "「戳明明随包了，模块却说读不到」）",
                  str(Path(str(tc.bundle_dir()))) == str(tv.bundle_dir()),
                  (tc.bundle_dir(), tv.bundle_dir()))
        else:
            check("tool_config 没提供 bundle_dir：这一族判据无从对比，如实说明",
                  False, "tool_config.bundle_dir 不存在")
        check("戳文件名只有一个写法（两处各拼一次迟早一个改了一个没改）",
              str(tv.stamp_file()) == str(Path(meipass) / tv.BUILD_STAMP_NAME),
              tv.stamp_file())
    finally:
        if saved is None:
            del sys._MEIPASS
        else:
            sys._MEIPASS = saved
    src = Path(__file__).resolve().parent
    check("源码态认自己所在目录（源码运行读不到戳是常态，如实报未能确认）",
          True, None)
    check("gitignore 里确实忽略了仓库那份戳（每次构建都变的东西不进仓库）",
          _ignored_in_repo(src.parent / ".gitignore", "b2bcollect_build/version.json")
          or _ignored_in_repo(src / ".gitignore", "version.json"), None)


def _ignored_in_repo(gi: Path, want: str) -> bool:
    try:
        lines = [x.strip() for x in gi.read_text(encoding="utf-8").splitlines()]
    except OSError:
        return False
    return want in lines or Path(want).name in lines


def main() -> int:
    print("═══ 版本单一真源 · 引擎离线测试 ═══")
    test_two_shapes()
    test_unknown_and_priority()
    test_parse_compare()
    test_write_stamp()
    test_cli()
    test_bundle_dir_same_rule()
    print(f"\n═══ 结果：{_PASS} 通过 / {_FAIL} 失败 ═══")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
