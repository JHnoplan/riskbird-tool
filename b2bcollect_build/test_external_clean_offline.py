# -*- coding: utf-8 -*-
"""「清洗数据·上传表格」离线回归：任意 Excel/CSV → 智能匹配列名 → 入库模版格式。

不触网、不开浏览器，纯跑 customer_library_cleaner 的外部清洗链路与 GUI 挂点。
运行：python test_external_clean_offline.py
"""
import os
import sys
import tempfile

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

# ── 8. 写盘对齐入库模版（write_clean_excel 带 template_path 也能出 9 列格式） ──
out = os.path.join(TMP, "清洗后入库数据.xlsx")
C.write_clean_excel(rows8, out + ".tmp", template_path="", log=lambda m: None)
os.replace(out + ".tmp", out)
from openpyxl import load_workbook  # noqa: E402
ws = load_workbook(out).active
hdr = [c.value for c in ws[1]][:9]
check("输出表头前 9 列符合模版",
      hdr == ["客户名称", "企业名称", "岗位", "手机号", "固话", "邮箱", "微信号", "数据来源", "外呼备注"], hdr)

print(f"\n全部通过：{len(PASS)} 组断言")
