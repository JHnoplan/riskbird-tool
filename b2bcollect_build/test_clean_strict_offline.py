# -*- coding: utf-8 -*-
"""test_clean_strict_offline.py —— 功能⑤「严格按关键词清洗」离线回归（判据/账目/界面三层）。

用户诉求原话：「清洗数据加多个选择，正常清洗和严格按照关键词清洗（看下是在 GUI 有个
勾选还是点击清洗才出现哪个更好，结合全局功能，参考大厂处理方式）」。选型结论与全部
契约都在这一个文件里钉住，改任何一层都会在这里红：

  A. 判据层（cdp_base.strict_keyword_filter / relevance_hit）
     · 两档只差「判不出相关性时放不放弃」，判定本体必须同一份实现——搜索类站点给
       「主营产品」，名单通道（企知道/爱企查/风鸟）只有公司名 + 工商文本，证据字段
       漏掉经营范围/所属行业/简介就是**把一整条通道凭空清零**；
     · 英文整词带边界（"LED" 不许命中 "BoilED"）、多空格排版仍算命中；
     · 行自带「关键词」列优先参与判定；没有任务关键词时只按行自带词判，判不出的进
       unknown（绝不当成「命中」）；非字典行原样放行，不炸整轮清洗。
  B. 会话账目层（ParallelSession._strict_gate / _strict_report）
     · 留 + 剔 + 未判 = 总行数，账必须闭合；
     · 按「数据来源」分账；**某一路被剔到一条不剩必须单独点名**——1688 恒 0 那次就
       是栽在「汇总数看不出一路归零」；
     · 没勾选时原样放行、一账不记（正常档零改动，这是默认档不许有的回归）；
     · 一行都没判时不编造账目。
  C. 界面档位层（b2bcollect_gui）
     · 做成**常驻勾选框**而不是点清洗时弹窗：洗完的数据从四个出口流出去（自动保存的
       两份表、导出、云展导入客户库、手动重洗），弹窗只拦得住一个，另外三个照旧按
       上次档位跑——同一批数据两种口径是最难解释的账；
     · 生效值落在数据（self._clean_strict）而不是控件上，控件在不在（档位差异）都由
       同一份判据说话；唯一写入口 _set_clean_strict 同时管值、控件、会话三处；
     · 没有任何关键词依据时勾不上：当场回弹 + 弹窗 + 日志三处一致，不许「以为筛过了」；
     · 快照记档位、续采回填能勾回来；老快照缺键时保持当前档位（不静默降档）；
     · 边界诚实：「清洗数据」按钮洗的是外部表格（没有关键词来源），同步数据池传的是
       原始记录，两者都不受严格档影响——文案不许把不管它的出口说成管。

全程 offscreen、不联网、不启 Chrome；状态落点钉在临时目录（假数据绝不进开发机真实索引）。
运行：python test_clean_strict_offline.py
"""
import os
import sys
import tempfile
import types
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent))

# 隔离必须钉在**真正生效的那一级**（tool_config.state_file），钉桌面目录是空操作：
# 老写法把假任务写进开发机真实的 _采集任务索引.json，自己绿、隔壁套件被判红。
_STATE_SANDBOX = Path(tempfile.mkdtemp(prefix="strict_state_"))
os.environ["B2B_STATE_DIR"] = str(_STATE_SANDBOX)
_DESK_SANDBOX = Path(tempfile.mkdtemp(prefix="strict_desk_"))

_passed = 0
_failed = 0


def _assert(condition, label):
    global _passed, _failed
    if condition:
        _passed += 1
        print(f"  ok   {label}")
    else:
        _failed += 1
        print(f"  FAIL {label}")


from cdp_base import strict_keyword_filter, relevance_hit          # noqa: E402
from parallel_session import ParallelSession                        # noqa: E402
import task_registry as _tasks                                      # noqa: E402

_assert(str(_tasks.index_path()).startswith(str(_STATE_SANDBOX)),
        f"隔离生效：任务索引落在沙箱（{os.path.dirname(str(_tasks.index_path()))}）")

# ── A. 判据层 ────────────────────────────────────────────────
print("A. 判据层：relevance_hit / strict_keyword_filter")
_assert(relevance_hit("太阳能路灯", "中山市照明有限公司") is False, "公司名里没有词→不算命中")
_assert(relevance_hit("太阳能路灯", "中山明亮太阳能路灯工程公司") is True, "公司名带词→命中")
_assert(relevance_hit("Oil Works", "Some Oil   Works Ltd") is True, "英文整词带多空格→命中")
_assert(relevance_hit("LED", "BoilED") is False, "英文整词边界不误伤（ED 嵌在词里不算）")

KW = ["太阳能路灯"]
R = [
    {"公司名称": "A市明亮太阳能路灯厂", "数据来源": "马可波罗"},                             # 0 名字带词→留
    {"公司名称": "A市照明贸易公司", "主营产品": "太阳能路灯、庭院灯", "数据来源": "马可波罗"},   # 1 主营带词→留
    {"公司名": "B市某某贸易公司", "经营范围": "销售：太阳能路灯", "数据来源": "企知道"},          # 2 工商文本带词→留
    {"公司名": "C市某某食品公司", "经营范围": "食品销售", "数据来源": "企知道"},                 # 3 无证据→剔
    {"公司名": "D市庭院灯厂", "关键词": "庭院灯", "数据来源": "风鸟"},                          # 4 行自带词命中→留
    {"公司名": "E市五金贸易公司", "数据来源": "爱企查"},                                        # 5 只有名字且没词→剔
    {"公司名称": "F市智慧照明科技公司", "关键词": "办公软件", "数据来源": "1688"},               # 6 自带词不命中→剔
    "不是字典的行",                                                                              # 7 原样放行
]


def _nm(xs):
    return sorted((str(x.get("公司名称") or x.get("公司名") or x) if isinstance(x, dict)
                   else str(x)) for x in xs)


kept, dropped, unknown = strict_keyword_filter(R, KW)
_assert(_nm(kept) == _nm([R[0], R[1], R[2], R[4], R[7]]), "留下来的行正是那五行")
_assert(_nm(dropped) == _nm([R[3], R[5], R[6]]), "剔掉的行正是那三行")
_assert(unknown == [], "有任务关键词时名单行同样参与判定（不再一律算「没得判」）")

# 证据字段这一条是**通道级**判据：名单通道没有主营产品字段，只取前一组就把这一路清零。
_evidence = ("主营产品", "主营商品", "标题", "经营范围", "所属行业", "简介")
_cdpsrc = Path(__file__).resolve().parent.joinpath("cdp_base.py").read_text(encoding="utf-8")
_blk = _cdpsrc[_cdpsrc.index("def strict_keyword_filter"):
               _cdpsrc.index("def strict_keyword_filter") + 2600]
_assert(all(k in _blk for k in _evidence),
        "严格档证据字段同时覆盖搜索类与工商文本两组：" + "、".join(_evidence))

k2, d2, u2 = strict_keyword_filter(R, [])
_assert(len(k2) == 2 and len(d2) == 1 and len(u2) == 5,
        f"没有任务关键词时只按行自带关键词判：留 {len(k2)}、剔 {len(d2)}、未判 {len(u2)}")
lg = []
strict_keyword_filter(R, KW, log=lg.append)
_assert(bool(lg) and "剔掉 3 行" in lg[0], "log 出账一句：" + (lg[0] if lg else "（没出账）"))
lg2 = []
strict_keyword_filter(R, KW, log=None)
_assert(lg2 == [], "不传 log 时不打印（账目由会话统一并句，避免两处各报一遍）")

# ── B. 会话账目层 ────────────────────────────────────────────
print("B. 会话账目层：_strict_gate / _strict_report")
fake = types.SimpleNamespace(clean_strict=True, clean_keywords=list(KW),
                             _last_keywords=["户外灯具"], _strict_stats=None)
acc = {"dropped": 0, "unknown": 0, "kept": 0, "total": 0}
survived = ParallelSession._strict_gate(fake, R, acc)
_assert(len(survived) == len(kept), f"闸门放行 {len(survived)} 行（与判据层口径一致）")
_assert(acc["total"] == len(R) and acc["kept"] + acc["dropped"] + acc["unknown"] == acc["total"],
        "账目闭合：留 + 剔 + 未判 = 总行数")
_assert(acc["judged"] is True, "有可比的关键词")
_assert(set(acc["by_source"]) == {"马可波罗", "企知道", "风鸟", "爱企查", "1688", "未知来源"},
        "分来源账目按「数据来源」拆开：" + "、".join(sorted(acc["by_source"])))
_assert(acc["by_source"]["企知道"] == {"kept": 1, "dropped": 1, "unknown": 0},
        f"企知道这一路留 1 剔 1：{acc['by_source']['企知道']}")

lines = []
ParallelSession._strict_report(fake, acc, lines.append)
_assert(any("清洗严格档" in x for x in lines), "汇总一句：" + (lines[0] if lines else "（没出账）"))
_assert(any("严格档分来源" in x for x in lines), "分来源逐句（多于一个来源时才展开）")
_assert(any("公司名样本" in x for x in lines), "剔掉的样本名看得见（用户能核对是哪几家）")

fake2 = types.SimpleNamespace(clean_strict=False, clean_keywords=[], _last_keywords=[],
                              _strict_stats=None)
acc2 = {"dropped": 0, "unknown": 0, "kept": 0, "total": 0}
_assert(ParallelSession._strict_gate(fake2, R, acc2) is R and acc2["total"] == 0,
        "没勾选时闸门原样放行、一账不记（正常清洗零改动）")

# 一路被整条清空必须点名：汇总数掩盖一路归零，正是 1688 恒 0 被漏过去的形状。
wiped = [{"公司名": f"某某贸易{i}", "经营范围": "日用百货", "数据来源": "企知道"}
         for i in range(6)]
fake3 = types.SimpleNamespace(clean_strict=True, clean_keywords=list(KW), _last_keywords=[],
                              _strict_stats=None)
acc3 = {"dropped": 0, "unknown": 0, "kept": 0, "total": 0}
ParallelSession._strict_gate(fake3, wiped, acc3)
out3 = []
ParallelSession._strict_report(fake3, acc3, out3.append)
_assert(any("剔到一条不剩" in x and "企知道" in x for x in out3),
        "一路归零单独点名：" + next((x for x in out3 if "剔到一条不剩" in x), "（没出账）")[:70])
fake4 = types.SimpleNamespace(clean_strict=True, clean_keywords=[], _last_keywords=[],
                              _strict_stats=None)
out4 = []
ParallelSession._strict_report(fake4, {"dropped": 0, "unknown": 0, "kept": 0, "total": 0},
                               out4.append)
_assert(out4 == [], "一行都没判时不编造账目")

# ── C. 界面档位层 ────────────────────────────────────────────
print("C. 界面档位层（offscreen 真 MainWindow）")
import b2bcollect_gui as G                                          # noqa: E402
from PyQt5.QtWidgets import QApplication, QMessageBox                # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)
notes = []


def _capture_box(*args, **kwargs):
    # 标题与正文都收进来：断言要挑「哪一句提示」，只看正文会让「弹了但说错了话」蒙混过关
    notes.append(" | ".join(str(x) for x in args[1:3]))
    return QMessageBox.Ok


QMessageBox.information = staticmethod(_capture_box)
QMessageBox.warning = staticmethod(_capture_box)
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.No)
G.MainWindow._get_desktop_dir = lambda self: str(_DESK_SANDBOX)
w = G.MainWindow()
w.show()
app.processEvents()
logs = []
w._on_log = lambda m: logs.append(str(m))

# 控件恒在：档位是数据口径开关，不是可选功能入口（以前整行只在扩词/历史任务存在时才加入）
_assert(hasattr(w, "chk_strict") and w.chk_strict is not None, "清洗档位勾选框已建出")
_assert(w.chk_strict.parent() is not None and w.chk_strict.isVisibleTo(w),
        "档位勾选框挂在常驻可见的那一行（不随档位差异消失）")
_assert(w.chk_strict.isChecked() is False, "默认正常清洗（严格档会剔数据，必须用户明确表示）")
_assert(w.session.clean_strict is False, "默认值已下发到会话")
_assert(w.chk_strict.toolTip(), "档位勾选框带说明（两档区别不是靠猜）")
_tip = w.chk_strict.toolTip()
_assert("四个出口" in _tip or "管四个出口" in _tip, "tooltip 写清管哪四个出口")
_assert("经营范围" in _tip, "tooltip 说清证据字段（名单通道只有工商文本可依据）")
_assert("外部表格" in _tip, "tooltip 写明边界：外部表格智能清洗不受这一档影响")

w.kw_text.setPlainText("")
w.session._last_keywords = []
w.session.crawlers.clear()
w.session.channels.clear()
w.chk_strict.setChecked(True)
app.processEvents()
_assert(w.chk_strict.isChecked() is False and w._clean_strict is False
        and w.session.clean_strict is False, "没依据时勾不上：控件回弹、生效值与会话三处都留在正常档")
_assert(any("暂时开不了严格档" in n for n in notes), "回弹时给了确定提示（不静默失败）")
_assert(any("严格档开不了" in m for m in logs),
        "日志里也留了一句：" + next((m for m in logs if "严格档开不了" in m), "")[:40])
notes.clear()
logs.clear()

w.kw_text.setPlainText("太阳能路灯、庭院灯")
w.chk_strict.setChecked(True)
app.processEvents()
_assert(w.chk_strict.isChecked() is True and w._clean_strict is True, "填上关键词后能勾上")
_assert(w.session.clean_strict is True, "勾选即时下发会话（不必重启采集）")
_assert(w.session.clean_keywords == ["太阳能路灯", "庭院灯"],
        "关键词现解析并下发：" + str(w.session.clean_keywords))
# 现解析而不是读 self.keywords：改了框里的词没点「开始采集」时，按旧词清洗等于
# 拿用户已经删掉的词去剔他的数据。
w.kw_text.setPlainText("庭院灯")
w._sync_clean_strict()
_assert(w.session.clean_keywords == ["庭院灯"], "改框里的词后同步会话即取新词（不残留旧词）")

# 唯一写入口：绕过控件直接调 _set_clean_strict，控件与数据仍一致
w._set_clean_strict(False)
app.processEvents()
_assert(w.session.clean_strict is False and w._clean_strict is False
        and w.chk_strict.isChecked() is False, "取消勾选同样三处一致（唯一写入口）")

snap = w._params_snapshot()
_assert(snap.get("clean_strict") is False, "快照里记着档位")
w._apply_params({"clean_strict": True})
app.processEvents()
_assert(w.chk_strict.isChecked() is True and w._clean_strict is True
        and w.session.clean_strict is True, "续采回填把档位勾回来并同步会话")
before = w.chk_strict.isChecked()
w._apply_params({"delay_between": 7.0})          # 老快照：没有 clean_strict 键
app.processEvents()
_assert(w.chk_strict.isChecked() is before, "老快照缺键时保持当前档位（不静默降档）")

# 出口接线：四个出口都先同步档位；外部表格那条**故意不接**（接了就是制造错觉）
_gsrc = Path(G.__file__).read_text(encoding="utf-8")


def _synced_before(text, fn_name):
    """判据：方法体内，**第一次真调用** session.clean_records 之前必须出现 _sync_clean_strict()。

    只找代码行、跳过注释行：docstring 里提到「clean_records」是常事（那里在解释口径），
    按裸词去找会把「先同步档位」误判成「后同步」——判据自己得先不误伤。
    """
    i = text.index(f"def {fn_name}")
    body = text[i:i + 4000].splitlines()
    call = sync = -1
    for no, ln in enumerate(body):
        s = ln.strip()
        if s.startswith("#"):
            continue
        if call < 0 and "self.session.clean_records(" in s:
            call = no
        if sync < 0 and s.startswith("self._sync_clean_strict()"):
            sync = no
        if call >= 0 and sync >= 0:
            break
    return call >= 0 and sync >= 0 and sync < call


# 判据自己的反向对照：顺序写反、根本没写、只在注释里提过，三种都必须判红
_bad_order = ('def _clean_session_data(self):\n'
              '    """跑一遍 clean_records 落盘"""\n'
              '    rows = self.session.snapshot_results()\n'
              '    clean_rows = self.session.clean_records(rows)\n'
              '    self._sync_clean_strict()\n')
_missing = ('def _on_export(self):\n    rows = self.session.snapshot_results()\n'
            '    clean_rows = self.session.clean_records(rows)\n')
_only_comment = ('def _on_export(self):\n    # self._sync_clean_strict()（写在注释里不算）\n'
                 '    clean_rows = self.session.clean_records(rows)\n')
_assert(not _synced_before(_bad_order, "_clean_session_data"), "反向对照：档位同步排在清洗之后→判红")
_assert(not _synced_before(_missing, "_on_export"), "反向对照：出口漏同步→判红")
_assert(not _synced_before(_only_comment, "_on_export"), "反向对照：只在注释里提同步→判红")

_assert(_synced_before(_gsrc, "_clean_session_data"), "手动重洗出口先同步档位")
_assert(_synced_before(_gsrc, "_on_export"), "导出出口先同步档位")
_assert("_sync_clean_strict()" in _gsrc[_gsrc.index("def _start_yzd_import"):
                                        _gsrc.index("def _start_yzd_import") + 2500],
        "云展导入客户库出口先同步档位")
_assert("_sync_clean_strict()" not in _gsrc[_gsrc.index("def _on_clean_now"):
                                             _gsrc.index("def _on_clean_now") + 2500],
        "「清洗数据」按钮（外部表格）不同步档位：那些表没有关键词来源，接上就是骗人有口径")
_assert("def _write_cleaned" in _gsrc and "clean_records" in _gsrc[
    _gsrc.index("def _write_cleaned"):_gsrc.index("def _write_cleaned") + 700],
        "自动保存的《清洗后入库数据》经 session.clean_records（会话上那一份档位管得到它）")

# ── 收尾：趁 QApplication 还活着把窗口拆掉（顺序与 b2bcollect_gui.main 的 finally 同一条）──
# 套件从不调 app.exec_()，也就绕过了生产那段有序收尾；而生产那段存在的理由正是「让解释器
# 自己拆 Qt 对象会撞上退出 SIGSEGV」（线上实测抓到过栈）。这里实测同样 6 次崩 1 次，且崩在
# 打印汇总**之后**——批跑只看退出码，全绿也会被判成一次失败。
# 走 hide() 而不是 close()：close 会进生产 closeEvent（等线程、弹确认框），本套件测的不是
# 退出那条路；hide 之后把引用清掉，refcount 当场让 sip 删掉 C++ 对象，此时 app 仍在。
w.hide()
w = None
app.processEvents()

print("\n" + "=" * 60)
total = _passed + _failed
print((f"全部通过 ✅ ({_passed}/{total})") if _failed == 0
      else f"通过 {_passed}/{total}，失败 {_failed} ❌")
print("=" * 60)
sys.exit(1 if _failed else 0)
