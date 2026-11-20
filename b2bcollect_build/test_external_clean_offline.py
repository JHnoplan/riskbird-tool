# -*- coding: utf-8 -*-
"""「清洗数据·上传表格」离线回归：任意 Excel/CSV → 智能匹配列名 → 入库模版格式。

不触网、不开浏览器，纯跑 customer_library_cleaner 的外部清洗链路与 GUI 挂点。
运行：python test_external_clean_offline.py
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import customer_library_cleaner as C  # noqa: E402

TMP = tempfile.mkdtemp(prefix="b2bclean_")
PASS = []


def _write_xlsx(path, sheets):
    from openpyxl import Workbook
    wb = Workbook()
    first = True
    for name, rows in sheets:
        ws = wb.active if first else wb.create_sheet()
        ws.title = name
        first = False
        for r in rows:
            ws.append(r)
    wb.save(path)


def check(name, cond, detail=""):
    assert cond, f"{name} 断言失败：{detail}"
    PASS.append(name)
    print(f"  ✅ {name}")


# ── 1. 入库模版同款 18 列重复表头：左块有数据、右块全空 → 只吃左块且不把右块算进未使用列 ──
tpl_rows = ["客户名称", "企业名称", "岗位", "手机号", "固话", "邮箱", "微信号", "数据来源", "外呼备注",
            "客户名称", "企业名称", "岗位", "手机号", "固话", "邮箱", "微信号", "数据来源", "备注"]
tpl = os.path.join(TMP, "客户库数据导入模板_带数据.xlsx")
_write_xlsx(tpl, [("客户库", [
    tpl_rows,
    ["", "广州冷能制冷设备有限公司", "", "13800138000", "020-84445555", "sales@lengneng.com", "", "", "",
     "", "", "", "", "", "", "", "", ""],
    ["", "佛山市冰锐净化设备厂", "陈师傅", "13900139000", "", "chen@bingrui.cn", "", "", "",
     "", "", "", "", "", "", "", "", ""],
])])
records, report = C.clean_external_records([tpl], log=lambda m: None)
check("模版18列：两行数据全部读入", len(records) == 2, f"实际 {len(records)}")
info = report[0]
flat = "；".join(info["identified"])
check("模版18列：右块空列不算未使用列",
      not any("第1%d列" % i in (info["skipped"] or []) for i in range(1, 9)),
      f"skipped={info['skipped']}")
rows = C.build_external_rows(records, log=lambda m: None)
check("模版18列：入库两行且公司名正确",
      {r["企业名称"] for r in rows} == {"广州冷能制冷设备有限公司", "佛山市冰锐净化设备厂"}, rows)
check("模版18列：手机/固话分列保持", rows[0]["手机号"] == "13800138000" and rows[0]["固话"] == "020-84445555", rows[0])

# ── 2. 数据只在右块（列10-18）：has_data 过滤后右块照样被认领，不静默丢数据 ──
right = os.path.join(TMP, "数据在右块.xlsx")
_write_xlsx(right, [("客户库", [
    tpl_rows,
    ["", "", "", "", "", "", "", "", "",
     "王工", "东莞寒极温控科技有限公司", "销售经理", "13700137000", "0769-88886666", "wang@hanji.com", "", "展会名单", ""],
])])
records2, report2 = C.clean_external_records([right], log=lambda m: None)
rows2 = C.build_external_rows(records2, log=lambda m: None)
check("右块数据不丢失", len(rows2) == 1 and rows2[0]["企业名称"] == "东莞寒极温控科技有限公司", rows2)
check("右块：联系人/岗位/来源都认到",
      rows2[0]["客户名称"] == "王工" and rows2[0]["岗位"] == "销售经理"
      and "展会名单" in rows2[0]["数据来源"], rows2[0])

# ── 3. 手机号+固话两列并进「电话」再拆回两列（多列合并不丢号） ──
twocol = os.path.join(TMP, "手机固话分列.xlsx")
_write_xlsx(twocol, [("Sheet1", [
    ["企业名称", "联系人", "手机号", "固话", "邮箱"],
    ["东莞市立勤科技有限公司", "陈生", "13900139000", "0769-88881111", "chen@liqin.com"],
])])
r3, rep3 = C.clean_external_records([twocol], log=lambda m: None)
check("两列合并写进对账日志", any("两列合并" in s for s in rep3[0]["identified"]), rep3[0]["identified"])
rows3 = C.build_external_rows(r3, log=lambda m: None)
check("两列合并后手机固话都还在",
      rows3[0]["手机号"] == "13900139000" and rows3[0]["固话"] == "0769-88881111", rows3[0])

# ── 4. 四项全空剔除、仅微信号的行保留（本工具口径：微信也算联系方式） ──
wechat = os.path.join(TMP, "只有微信.xlsx")
_write_xlsx(wechat, [("Sheet1", [
    ["企业名称", "联系人", "手机号", "微信号"],
    ["佛山有名贸易公司", "阿明", "", "wx_ming123"],
    ["空壳一无公司", "", "", ""],
])])
r4, _ = C.clean_external_records([wechat], log=lambda m: None)
rows4 = C.build_external_rows(r4, log=lambda m: None)
names4 = [r["企业名称"] for r in rows4]
check("仅微信号的行保留", "佛山有名贸易公司" in names4, names4)
check("四项全空的行剔除", "空壳一无公司" not in names4, names4)

# ── 5. 工商状态列陷阱：客户名称不能写成「存续」，状态列归状态字段 ──
st = os.path.join(TMP, "状态列陷阱.xlsx")
_write_xlsx(st, [("Sheet1", [
    ["序号", "公司名", "状态", "数据来源", "法定代表人", "电话", "邮箱", "地址"],
    [1, "淄博铭丰塑业有限公司", "存续", "企知道", "于波", "0533-7402660；13365336660", "a@b.com", "山东省淄博市"],
    [2, "佛山市南海区恒发家具厂", "注销", "爱企查", "李常正", "0757-88886666", "c@d.com", "佛山市南海区"],
])])
r5, rep5 = C.clean_external_records([st], log=lambda m: None)
rows5 = C.build_external_rows(r5, log=lambda m: None)
check("状态列归位不当联系人",
      all(r["客户名称"] not in ("存续", "注销") for r in rows5), rows5)
check("法定代表人才是客户名称",
      {r["客户名称"] for r in rows5} >= {"于波"}, rows5)

# ── 6. 裸号补区号：同行唯一区号才补，多区号不猜 ──
bare = os.path.join(TMP, "裸号.xlsx")
_write_xlsx(bare, [("Sheet1", [
    ["企业名称", "电话"],
    ["苏州华宇精密机械有限公司", "66667777；0512-11112222"],
    ["跨城混合公司", "021-55556666；0755-88889999；12345678"],
])])
r6, _ = C.clean_external_records([bare], log=lambda m: None)
rows6 = C.build_external_rows(r6, log=lambda m: None)
one_area = next(r for r in rows6 if r["企业名称"] == "苏州华宇精密机械有限公司")
multi_area = next(r for r in rows6 if r["企业名称"] == "跨城混合公司")
check("同行唯一区号：裸号补全", "0512-66667777" in one_area["固话"], one_area)
check("同行多个区号：裸号保持原样不猜", "12345678" in multi_area["固话"], multi_area)

# ── 7. 无表头文件 + CSV（GBK 分号分隔） ──
nohdr = os.path.join(TMP, "无表头名单.xlsx")
_write_xlsx(nohdr, [("Sheet1", [
    ["北京宏远建筑工程有限公司", "13512345678", "hong@beiyuan.cn"],
    ["上海立达贸易有限公司", "021-55556666", "li@lida.com"],
])])
r7, rep7 = C.clean_external_records([nohdr], log=lambda m: None)
rows7 = C.build_external_rows(r7, log=lambda m: None)
check("无表头按内容认列", len(rows7) == 2 and all(r["手机号"] or r["固话"] for r in rows7), rows7)

csvp = os.path.join(TMP, "名单.csv")
with open(csvp, "w", encoding="gbk", newline="") as fh:
    fh.write("企业名称;联系人;职位;手机号码;邮箱\n")
    fh.write("重庆两江智能科技有限公司;赵敏;销售经理;13456789012;zhao@liangjiang.com\n")
r8, _ = C.clean_external_records([csvp], log=lambda m: None)
rows8 = C.build_external_rows(r8, log=lambda m: None)
check("CSV(GBK;分号)清洗正确",
      len(rows8) == 1 and rows8[0]["客户名称"] == "赵敏" and rows8[0]["岗位"] == "销售经理", rows8)

# ── 8. 写盘对齐入库接口契约（无模板文件时只出接口那 9 列，不再重复填一块） ──
out = os.path.join(TMP, "清洗后入库数据.xlsx")
C.write_clean_excel(rows8, out + ".tmp", template_path="", log=lambda m: None)
os.replace(out + ".tmp", out)
from openpyxl import load_workbook  # noqa: E402
ws = load_workbook(out).active
hdr = [c.value for c in ws[1]][:9]
# 口径真源是 yzd_client.IMPORT_HEADERS（接口6：「名称必须完全一致，否则该列不会被读取」），
# 不是客户那份 18 列双块模板。为什么2026-11-18 改这一格：旧兜底把同组 9 列填两遍（A-I 外呼块
# 第九列叫「外呼备注」、J-R 备注块第九列叫「备注」），无模板文件的那台机器上交付表就是
# 逐格相同的重复列——用户原话「导出不需要多余栏」，而下游按列名取数时同名列后一个盖前一个。
# 上传链本来就不读这份表（走 write_import_workbook 另写一份 9 列），所以这里改成接口口径
# 不会伤到入库；而**真模板存在时仍旧按模板出 18 列双块**（read_template_layout 那条路径）。
# 旧注释把那一档的覆盖指给 test_batch3_regress_offline 的 7b/7e/7f，那是错的：那几格验的全是
# 「无模板→第二块为空」，模板分支当时整个离线体系一个用例都没有。现在钉在两处：
# test_batch3_regress_offline 的 7g（三份副本逐个验双块都填同一份数据）
# 与 test_list_channels_offline 的 F 段（三路合并结果落进双块）。
check("输出表头就是入库接口那 9 列（列名与 IMPORT_HEADERS 逐字一致、不重复成双块）",
      hdr == list(__import__("yzd_client").IMPORT_HEADERS) and hdr[-1] == "备注", hdr)
hdr_all = [c.value for c in ws[1]]
check("整张表也只有 9 列（没有第二块重复栏）", len(hdr_all) == 9, hdr_all)

# ── E. 增强清洗的三份文件：列结构不许有第二份真源（2026-11-18 同类缺陷）──
# 旧 `_write_xlsx` 自己写了一份 18 列常量并 `ws.append(block + block)`，于是「清洗后_国内 /
# 国内只留手机号 / 国外」这三份在没有客户模板的机器上比《清洗后入库数据》多一整块重复栏
# ——同一个工具的两个出口对同一件事给两种列数，用户读到的就是「数据乱、导出不需要多余栏」。
# 现在三份都走 write_clean_excel 那一份真源：有模板→双块且两块相同，无模板→接口 9 列。
from openpyxl import Workbook as _WB                    # noqa: E402
import enhanced_cleaner as EC                            # noqa: E402
_src_e = os.path.join(TMP, "增强清洗输入.xlsx")
# 这一格原来用 pandas 造 xlsx 夹具。需求 5 把 pandas 从交付依赖里摘掉后它就断了：
# 本机装了 pandas（moviepy/opencv 连带）所以看不出来，按交付清单装出来的干净环境跑
# 这路回归会直接 ImportError——「只有开发机能跑的判据」等于没有判据。造表只需要
# openpyxl：它本来就是随包的导出依赖，下面读回来用的也是同一个库，不必多拉一个重量级。
_rows_e = [
    {"公司名": "深圳光能科技有限公司", "电话": "13800138000", "邮箱": "sales@guangneng.com",
     "官网": "www.guangneng.com", "地址": "深圳市南山区科技园"},
    {"公司名": "German Solar GmbH", "电话": "+49 30 12345678", "邮箱": "info@germansolar.de",
     "地址": "Berlin, Germany"},
]
_hdr_e: list = []
for _r in _rows_e:
    _hdr_e.extend(k for k in _r if k not in _hdr_e)
_wb_e = _WB()
_wb_e.active.append(_hdr_e)
for _r in _rows_e:
    _wb_e.active.append([_r.get(k, "") for k in _hdr_e])
_wb_e.save(_src_e)


def _grids_e(result):
    """把三份产出全读回来：判列结构只看真文件，不看内存里的 dict。"""
    out = {}
    for _lb, (_p, _n) in result.items():
        _g = [list(r) for r in load_workbook(_p).worksheets[0].iter_rows(values_only=True)]
        out[_lb] = (_g[0], [r for r in _g[1:] if any(r)])
    return out


_e1 = _grids_e(EC.enhanced_clean_three_files([_src_e], TMP, log=lambda m: None,
                                             template_path="")[0])
check("E1 增强清洗三份都落盘且无模板时就是接口那 9 列（与《清洗后入库数据》同口径）",
      len(_e1) == 3 and all(len(_h) == 9 and len(set(_h)) == 9 and _h[-1] == "备注"
                          for _h, _b in _e1.values()),
      {k: (len(h), len(b)) for k, (h, b) in _e1.items()})
_e2 = _grids_e(EC.enhanced_clean_three_files([_src_e], TMP, log=lambda m: None,
                                             template_path=tpl)[0])
check("E2 有客户模板时三份都是 18 列双块，而且两块逐格相同（不许只填一块）",
      all(len(_h) == 18 and _b and all([str(v or "") for v in _r[:9]] == [str(v or "") for v in _r[9:18]]
                                        for _r in _b) for _h, _b in _e2.values()),
      {k: (len(h), len(b)) for k, (h, b) in _e2.items()})
check("E3 三份文件共用一次模板定位（一份双块一份单块＝同一批数据两种形状）",
      {len(_h) for _h, _b in _e1.values()} == {9} and {len(_h) for _h, _b in _e2.values()} == {18})
# E4 把「列结构只有一份真源」钉成机器判据：交付链里再不允许出现第二份「同一组入库列名
# 写两遍」的表头字面量（上一轮的 18 列兜底、这一轮的增强清洗 HEADER 都是这么长出来的）。
import ast as _ast_e                                                 # noqa: E402
_ROOT_E = os.path.dirname(os.path.abspath(__file__))
_DUP_COLS = set(__import__("yzd_client").IMPORT_HEADERS)
_off_e, _scanned_e = [], 0
_seen_e = {"b2bcollect_build": set(), "ali1688_build": set(), "riskbird_build": set()}
for _pkg in ("b2bcollect_build", "ali1688_build", "riskbird_build"):
    for _fp in sorted(Path(_ROOT_E, "..").joinpath(_pkg).glob("*.py")):
        if _fp.name.startswith("test_"):
            continue
        _seen_e[_pkg].add(_fp.name)
        _scanned_e += 1
        _tr = _ast_e.parse(_fp.read_text(encoding="utf-8"))
        for _nd in _ast_e.walk(_tr):
            if not isinstance(_nd, (_ast_e.List, _ast_e.Tuple)):
                continue
            _vv = ["" if _e.value is None else str(_e.value) for _e in _nd.elts
                   if isinstance(_e, _ast_e.Constant)]
            # 只拿「表头形状」的字面量：至少沾到 5 个不同的入库列名且其中有重名。
            # （不然 gys_crawler 那种 (页面键, 字段名) 映射对会被误伤：它也重一个「邮箱」，
            # 但跟入库表列结构没关系——判据一旦误伤，下一个人只会把它关掉）
            if len(set(_vv) & _DUP_COLS) < 5:
                continue
            _rep = sorted({_c for _c in _vv if _c in _DUP_COLS and _vv.count(_c) > 1})
            if _rep:
                _off_e.append(f"{_pkg}/{_fp.name}:{_nd.lineno} 把 {'、'.join(_rep)} 写了两遍")
check("E4 交付链里只有一份入库表列结构（没有任何 .py 再写一份重复列名的表头字面量）",
      not _off_e, f"扫了 {_scanned_e} 份；命中：{_off_e}")

# E4b 把「判的是什么」与「看得见的范围」分成两条。旧写法把两者合在一条里：
# `_scanned_e >= 100 and not _off_e`——那个 100 是**开发机的未跟踪脚本堆**撑出来的数字，
# 不是仓库的性质：本机 150 份过得去，干净 clone 只有 81 份就必红（2026-11-20 实测，
# 与 test_waterfall_leads_offline 的 `_o_tracked_counts` 那一处同一个根）。
# 换成与机器无关的口径：三个目录都得非空，且 git 在库的顶层非 test 文件一份不许漏出扫描范围。
def _tracked_top_e(pkg):
    """git 在库的顶层非 test .py 文件名集合；读不到 git 返回 None（不许当成空集）。"""
    import subprocess
    try:
        r = subprocess.run(["git", "-C", str(Path(_ROOT_E).parent), "ls-files", "--", f"{pkg}/*.py"],
                           capture_output=True, text=True, timeout=20)
    except Exception:
        return None
    if r.returncode:
        return None
    return {ln.rsplit("/", 1)[1] for ln in r.stdout.splitlines()
            if ln.count("/") == 1 and not ln.rsplit("/", 1)[1].startswith("test_")}


_gaps_e, _git_ok_e = [], True
for _pkg, _names in _seen_e.items():
    _t = _tracked_top_e(_pkg)
    if _t is None:
        _git_ok_e = False
        continue
    _gaps_e += [f"{_pkg}/{x}" for x in sorted(_t - _names)]
check("E4b E4 的扫描范围必须真的盖住三份产物代码（每个目录非空 + git 在库的顶层文件一份不漏）："
      "视野断掉时「没命中」只是一句空话（拿绝对份数当下限等于拿某台机器的桌面当标尺）",
      all(_seen_e.values()) and (_git_ok_e is False or not _gaps_e),
      f"实扫 {  {k: len(v) for k, v in _seen_e.items()} }｜漏：{_gaps_e}｜"
      f"git 读不到＝{_git_ok_e is False}（读不到时只按「非空」这一条判）")

print(f"\n全部通过：{len(PASS)} 组断言")
