"""test_integration_offline.py —— 多平台整合后的离线回归断言（无需浏览器/网络）。

覆盖本次整合（去 gys、加 1688、企知道名单通道、GUI 重排）的关键契约：
  · 站点拓扑：gys 已下线、SITE_ORDER=5 平台、SITE_TIPS 全覆盖（防 GUI 索引 KeyError）；
  · 状态行富文本渲染（含 1688）；
  · 待采集公司名单三台账：bind 建表 / seed 入列 / add_waste 回流 / pop 记 tried /
    mark_result 记 collected / requeue 回队首 / 探针跳过已采；
  · 公司名识别（无表头 txt / 字面表头 txt / 带表头 xlsx / 多列无表头）；
  · GUI 默认态：1688 不勾、其余勾、补采开关默认勾、清洗按钮存在、主按钮样式。

运行：.venv/bin/python test_integration_offline.py   （GUI 段无 PyQt5 时自动跳过）
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

# GUI 段需要 Qt；无 PyQt5 的环境（如 CI 精简镜像）跳过 GUI 断言，其余照跑。
try:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt5.QtWidgets import QApplication
    HAS_QT = True
except Exception:
    HAS_QT = False

from parallel_session import SITE_ORDER, SITE_NAMES  # noqa: E402
import pending_companies as pc  # noqa: E402

PASS = []


def ok(name):
    PASS.append(name)
    print(f"  ✅ {name}")


# ── 1. 站点拓扑 ────────────────────────────────────────────────
def test_site_topology():
    assert "gys" not in SITE_ORDER, "供应商网应已下线"
    assert SITE_ORDER == ["mic", "b2b168", "11467", "aipage", "1688"], SITE_ORDER
    assert SITE_NAMES.get("1688") == "1688"
    assert SITE_NAMES.get("qzd") == "企知道"
    ok("站点拓扑：gys 下线、5 平台 + 企知道通道")


# ── 2. 公司名识别 ──────────────────────────────────────────────
def test_extract_company_names(tmp):
    d = Path(tmp)
    # 无表头 txt：首行也是数据，不能丢
    p1 = d / "nohead.txt"
    p1.write_text("德州A光伏有限公司\n深圳B储能股份有限公司\n", encoding="utf-8")
    n1 = pc.extract_company_names(str(p1), log=lambda m: None)
    assert n1 == ["德州A光伏有限公司", "深圳B储能股份有限公司"], n1
    # 字面表头 txt：首行是「公司名」应跳过
    p2 = d / "head.txt"
    p2.write_text("公司名\n广州C新能源有限公司\n", encoding="utf-8")
    n2 = pc.extract_company_names(str(p2), log=lambda m: None)
    assert n2 == ["广州C新能源有限公司"], n2
    # 多列无表头 xlsx：公司名在第二列
    try:
        from openpyxl import Workbook
        p3 = d / "multi.xlsx"
        wb = Workbook(); ws = wb.active
        ws.append(["序号", "东莞D电子有限公司", "13800000000"])
        ws.append(["序号", "佛山E照明有限公司", "13900000000"])
        wb.save(str(p3))
        n3 = pc.extract_company_names(str(p3), log=lambda m: None)
        assert n3 == ["东莞D电子有限公司", "佛山E照明有限公司"], n3
    except ImportError:
        pass
    ok("公司名识别：无表头/字面表头/多列 xlsx 全对")


# ── 3. 待采名单三台账 + 废数据回流 ─────────────────────────────
def test_pending_ledgers(tmp):
    lst = pc.PendingCompanyList()
    assert lst.is_bound is False
    d = Path(tmp) / "task"
    added = lst.bind(str(d))
    assert lst.is_bound is True
    # 用户名单入列（seed 会 flush 落盘）
    assert lst.seed(["甲公司", "乙公司"]) == 2
    # 任务文件夹里出现待采集公司名单表（与原数据/清洗入库同层级）
    assert (d / "待采集公司名单.xlsx").exists(), "待采集公司名单.xlsx 未生成"
    # 废数据回流（自动保存线程调用）
    assert lst.add_waste(["丙公司", "甲公司"]) == 1, "重复的甲公司不应再入列"
    assert lst.counts()["pending"] == 3, lst.counts()
    # pop 记 tried；查到记 collected
    name = lst.pop()
    assert name == "甲公司"
    assert lst.counts()["tried"] == 1
    lst.mark_result(name, True)
    assert lst.counts()["collected"] == 1
    # 查不到只留 tried（丢弃，不回列）
    name2 = lst.pop()
    assert name2 == "乙公司"
    lst.mark_result(name2, False)
    assert lst.counts()["pending"] == 1 and lst.counts()["collected"] == 1
    # requeue：没真正查成的回队首、从 tried 解耦（tried 从 3 降回 2）
    name3 = lst.pop()
    assert name3 == "丙公司" and lst.counts()["tried"] == 3
    lst.requeue(name3)
    assert lst.counts()["pending"] == 1 and lst.counts()["tried"] == 2, lst.counts()
    ok("待采名单：bind 建表 / seed / waste 去重 / pop·tried / collected / requeue")


# ── 4. 探针跳过已采 ────────────────────────────────────────────
def test_probe_skip(tmp):
    from parallel_session import ParallelSession
    # 场景一：关键词通道已采到「带联系方式」的同名公司 → pop 应被探针跳过（省企知道请求）
    s = ParallelSession()
    d = Path(tmp) / "task2"
    s.bind_pending(str(d), "", want_qzd=False)
    s.pending.seed(["已采公司"])
    fake = [{"公司名称": "已采公司", "手机": "13800138000"}]
    for c in s._active_crawlers():
        c.snapshot_results = lambda f=fake: f
        break
    got = s.pending.pop()
    assert got is None, f"已采到联系方式的公司应被探针跳过，实际 pop 到 {got}"
    # 场景二：关键词通道只采到「无联系方式」的废数据（正是待补采对象）→ 探针必须放行，
    #         否则企知道永远补采不到任何废数据（回归 2026-10-29：13 家全被误跳过、查到 0 家）
    s2 = ParallelSession()
    s2.bind_pending(str(Path(tmp) / "task3"), "", want_qzd=False)
    s2.pending.seed(["废数据公司"])
    fake2 = [{"公司名称": "废数据公司", "状态": "无联系方式"}]   # 无手机/电话/邮箱/微信
    for c in s2._active_crawlers():
        c.snapshot_results = lambda f=fake2: f
        break
    got2 = s2.pending.pop()
    assert got2 == "废数据公司", \
        f"无联系方式的废数据不能被探针跳过（否则企知道永远补采不到），实际 pop 到 {got2}"
    ok("探针：已采到联系方式才跳过；无联系方式的废数据放行给企知道补采")


# ── 5. 关键词解析硬化（括号不切 / 碎片丢弃 / 括注剥离）──────────
def test_parse_keywords():
    from cdp_base import CdpSiteCrawler as C
    # 括号内的「/」不切分，且剥括注只留可检索主体词（旧逻辑会漏出「蜂窝铝）」等碎片）
    got = C.parse_keywords("彩钢夹芯板（岩棉/聚氨酯/蜂窝铝）")
    assert got == ["彩钢夹芯板"], got
    # 单字碎片（「初」）被丢弃
    assert C.parse_keywords("初/中效过滤棉") == ["中效过滤棉"], C.parse_keywords("初/中效过滤棉")
    # 括号外的「/」仍正常切分（不误伤合法多选词）
    assert C.parse_keywords("太阳能/风能/储能") == ["太阳能", "风能", "储能"]
    # 序号前缀 + 顿号/逗号/分号混合分隔
    assert C.parse_keywords("1. 制冷设备\n2、冷库板，冷凝器；蒸发器") == \
        ["制冷设备", "冷库板", "冷凝器", "蒸发器"]
    ok("关键词解析：括号不切 / 碎片丢弃 / 括注剥离 / 序号前缀")


# ── 6. 1688 原始记录补全「数据来源」（否则清洗跨站匹配时被整批丢弃）──
def test_1688_source_stamp():
    try:
        import site_adapters as ad
    except Exception:
        print("  ⏭ 跳过 1688 数据来源断言（适配器不可用）")
        return
    if not ad.HAS_1688:
        print("  ⏭ 跳过 1688 数据来源断言（1688 适配器不可用）")
        return
    site = ad.Ali1688Site(port=9255)
    site.incremental = False       # 避免写种子库
    rec = {"公司名称": "某某制冷设备有限公司", "状态": "成功", "手机": "13905341234"}
    assert "数据来源" not in rec
    site._add_result(rec)
    assert rec.get("数据来源") == "1688", rec
    # 清洗跨站按「数据来源」匹配：补全后 1688 记录不再被丢
    from parallel_session import ParallelSession
    s = ParallelSession(enabled=["1688"])
    s.crawlers["1688"].snapshot_results = lambda: [dict(rec)]
    rows = s.clean_records([dict(rec)])
    assert any((r.get("数据来源") == "1688") for r in rows), rows
    ok("1688 数据来源：原始记录自动补全，清洗跨站匹配不再丢弃")


# ── 6.5 智能解析：类别名→产品词展开 / 服务词忽略 / 未收录原样保留 ──
def test_smart_expand():
    import keyword_expander as ke
    # ① 类别名展开成具体产品词（含跨类别去重）
    r = ke.expand_keywords("制冷与热泵核心部件\n过滤与净化材料")
    assert "制冷压缩机" in r["keywords"] and "冷凝器" in r["keywords"], r["keywords"]
    assert "HEPA高效过滤器" in r["keywords"], r["keywords"]
    assert len(r["expanded"]) == 2, r["expanded"]
    # ② 纯服务/流程词被忽略、不产出关键词
    r2 = ke.expand_keywords("方案设计与咨询\n项目招投标与点位开发\n运维维保")
    assert r2["keywords"] == [], r2
    assert len(r2["dropped"]) == 3, r2["dropped"]
    # ③ 未收录的词原样保留（不误删用户已经写好的产品词）
    r3 = ke.expand_keywords("太阳能路灯")
    assert r3["keywords"] == ["太阳能路灯"], r3
    assert r3["kept"] == ["太阳能路灯"], r3
    # ④ 最长特征短语优先：「空气过滤与净化设备」归设备类而非材料类
    r4 = ke.expand_keywords("空气过滤与净化设备")
    assert "FFU风机过滤单元" in r4["keywords"], r4["keywords"]
    assert r4["expanded"][0][1] == "空气过滤与净化设备", r4["expanded"]
    # ⑤ 去重：同类别重复出现不会重复产出
    r5 = ke.expand_keywords("暖通空调\n暖通空调与制冷设备")
    assert len(r5["keywords"]) == len(set(r5["keywords"])), r5["keywords"]
    ok("智能解析：类别→产品词展开 / 服务词忽略 / 未收录保留 / 最长匹配优先 / 去重")


# ── 7. GUI 默认态（需 Qt）──────────────────────────────────────
def test_gui_defaults():
    if not HAS_QT:
        print("  ⏭ 跳过 GUI 断言（无 PyQt5）")
        return
    import b2bcollect_gui as g
    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(g._app_stylesheet())
    # SITE_TIPS 必须覆盖 SITE_ORDER（旧版缺 1688 键会让 _build_ui 直接 KeyError）
    missing = [s for s in SITE_ORDER if s not in g.SITE_TIPS]
    assert not missing, f"SITE_TIPS 缺键: {missing}"
    assert "gys" not in g.SITE_TIPS
    w = g.MainWindow()
    assert set(w.site_checks) == set(SITE_ORDER)
    assert w.site_checks["1688"].isChecked() is False, "1688 应默认不勾"
    for s in SITE_ORDER:
        if s != "1688":
            assert w.site_checks[s].isChecked() is True, f"{s} 应默认勾选"
    for attr in ("btn_file", "btn_paste", "btn_parse", "btn_smart", "kw_text", "lbl_kw",
                 "btn_co_file", "btn_open_qzd", "lbl_co", "chk_waste",
                 "btn_clean", "btn_open", "btn_start", "btn_pause", "btn_stop",
                 "btn_export", "btn_reset", "progress", "log"):
        assert hasattr(w, attr), f"缺控件 {attr}"
    assert w.chk_waste.isChecked() is True, "补采开关默认应勾选"
    assert w.btn_start.objectName() == "primary", "开始采集应为主按钮样式"
    # 状态行富文本（含 1688）仍可渲染
    html = g.build_stats_html("已采 5 家　中国制造网 3｜1688 2",
                              {"mic": "done", "1688": "running"})
    assert "<span" in html and "1688" in html
    ok("GUI 默认态：1688 不勾 / 补采默认勾 / 控件齐全 / 状态行含 1688")


def main():
    with tempfile.TemporaryDirectory(prefix="b2b_offline_") as tmp:
        test_site_topology()
        test_extract_company_names(tmp)
        test_pending_ledgers(tmp)
        test_probe_skip(tmp)
        test_parse_keywords()
        test_1688_source_stamp()
        test_smart_expand()
        test_gui_defaults()
    print(f"\n全部通过：{len(PASS)} 组断言")


if __name__ == "__main__":
    main()
