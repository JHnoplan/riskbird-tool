"""外部数据智能清洗离线自测：列名对不上也能靠内容认、脏值清洗、多联系人不被压掉。

覆盖「清洗数据」按钮的全链路：造几种真实世界最常见的“别人给的表”
（列名五花八门 / 无表头 / 手机号被 Excel 存成数字 / CSV 逗号分隔 / 备注列），
读进来 → 智能匹配列 → 清洗 → 出模板行，逐步断言。
"""
import os

import customer_library_cleaner as C

TMP = "/tmp/external_clean_test"
os.makedirs(TMP, exist_ok=True)


def _write_xlsx(path, sheets):
    """sheets: [(表名, [[单元格…], …])]"""
    from openpyxl import Workbook

    wb = Workbook()
    wb.remove(wb.active)
    for name, rows in sheets:
        ws = wb.create_sheet(title=name)
        for r in rows:
            ws.append(r)
    wb.save(path)


# ── 1. 列名五花八门：靠表头别名 + 内容双重判定 ──
messy = os.path.join(TMP, "其他渠道名单.xlsx")
_write_xlsx(messy, [("Sheet1", [
    ["序号", "客户名称", "单位名称", "手机", "电子邮箱", "公司网站", "所在地区", "注册资本", "备注"],
    [1, "张伟", "深圳市蓝海科技有限公司", 13800138000, "zhang@lanhai.com", "www.lanhai.com",
     "广东省深圳市南山区科技园路1号", "500万", "老客户转介绍"],
    [2, "李娜", "广州天诚集团股份有限公司", "020-88886666转120", "lina@tc.com.cn", "",
     "广州市天河区体育西路103号", "1000万", ""],
    [3, "", "苏州华宇精密机械有限公司", "13912345678;0512-66667777", "hua@hy.com", "",
     "苏州市工业园区", "200万", ""],
    [4, "王强", "深圳市蓝海科技有限公司", "13700137000", "", "", "", "500万", "第二个联系人"],
])])

records, report = C.clean_external_records([messy], log=print)
assert len(records) == 4, f"应读到 4 行，实际 {len(records)}"
info = report[0]
ident = {kv.split("→")[0].strip("“”"): kv.split("→")[1] for kv in info["identified"]}
print("列识别结果:", ident)
assert set(ident) >= {"客户名称", "单位名称", "手机", "电子邮箱", "备注"}, ident
assert ident["客户名称"] == "联系人" and ident["单位名称"] == "公司名", ident
assert ident["备注"] == "备注", "备注列也要被识别"
assert "序号" not in ident and "注册资本" not in ident, \
    "序号/注册资本这类列不能被认成入库字段"

rows = C.build_external_rows(records, log=print)
by_company = {}
for r in rows:
    by_company.setdefault(r["企业名称"], []).append(r)
print("\n清洗行:")
for r in rows:
    print(r)

assert len(rows) == 4, f"同名公司不同联系人必须各自保留，实际 {len(rows)}"
lanhai = by_company["深圳市蓝海科技有限公司"]
assert len(lanhai) == 2, "同一家公司的两个联系人不能被压成一条"
zhang = next(r for r in lanhai if r["客户名称"] == "张伟")
assert zhang["手机号"] == "13800138000", f"数字类型的手机号不能被读成 1.38001e+10：{zhang['手机号']}"
assert zhang["岗位"] == "", "联系人不是法定代表人，岗位不能瞎补「法定代表人」"
assert zhang["数据来源"].startswith("外部导入:其他渠道名单"), zhang["数据来源"]
assert "老客户转介绍" in zhang["外呼备注"], zhang["外呼备注"]
assert "www.lanhai.com" in zhang["外呼备注"]
assert "地址:广东省深圳市南山区" in zhang["外呼备注"]
tc = by_company["广州天诚集团股份有限公司"][0]
assert tc["固话"] == "020-88886666转120", tc["固话"]
assert tc["客户名称"] == "李娜"
hy = by_company["苏州华宇精密机械有限公司"][0]
assert hy["手机号"] == "13912345678" and hy["固话"] == "0512-66667777", (hy["手机号"], hy["固话"])
# 没填联系人的行：客户名称回退成公司名（模板要求客户名称非空）
assert hy["客户名称"] == "苏州华宇精密机械有限公司"

# ── 2. 无表头：整列内容判定，公司/电话/邮箱各归各位 ──
bare = os.path.join(TMP, "无表头名单.xlsx")
_write_xlsx(bare, [("Sheet1", [
    ["北京宏远建筑工程有限公司", "13512345678", "hong@beiyuan.cn"],
    ["上海立达贸易有限公司", "021-55556666", "li@lida.com"],
])])
records2, report2 = C.clean_external_records([bare], log=print)
rows2 = C.build_external_rows(records2, log=print)
assert len(rows2) == 2, rows2
assert {r["企业名称"] for r in rows2} == {"北京宏远建筑工程有限公司", "上海立达贸易有限公司"}
assert all(r["手机号"] or r["固话"] for r in rows2), "电话列要按内容认出来"
assert all(r["邮箱"] for r in rows2), "邮箱列要按内容认出来"
assert report2[0]["rows"] == 2

# ── 3. 「客户名称」列装的是公司：内容赢过表头别名，不能当联系人 ──
cname = os.path.join(TMP, "客户名称是公司.xlsx")
_write_xlsx(cname, [("Sheet1", [
    ["客户名称", "联系电话", "邮箱"],
    ["杭州新艺装饰工程有限公司", "13600001111", "hi@xinyi.com"],
    ["成都天府食府餐饮管理有限公司", "028-87776666", "tf@shifu.cn"],
])])
records3, _ = C.clean_external_records([cname], log=print)
rows3 = C.build_external_rows(records3, log=print)
assert len(rows3) == 2
for r in rows3:
    assert r["企业名称"] == r["客户名称"], f"公司名当成联系人了：{r}"
    assert r["岗位"] == ""

# ── 4. CSV（GBK + 分号分隔）与多工作表 ──
csv_path = os.path.join(TMP, "名单.csv")
with open(csv_path, "w", encoding="gbk", newline="") as fh:
    fh.write("企业名称;联系人;职位;手机号码;邮箱\n")
    fh.write("重庆两江智能科技有限公司;赵敏;销售经理;13456789012;zhao@liangjiang.com\n")
    fh.write("西安丝路数码有限公司;孙磊;技术总监;13300001111;sun@silu.cn\n")
records4, report4 = C.clean_external_records([csv_path], log=print)
rows4 = C.build_external_rows(records4, log=print)
assert len(rows4) == 2, rows4
assert rows4[0]["客户名称"] == "赵敏" and rows4[0]["岗位"] == "销售经理", rows4[0]
assert rows4[0]["手机号"] == "13456789012"

# ── 5. 多文件 + 一个坏文件不影响其他文件 ──
old_xls = os.path.join(TMP, "旧格式.xls")
with open(old_xls, "w", encoding="utf-8") as fh:
    fh.write("fake")
records5, report5 = C.clean_external_records([old_xls, messy, csv_path], log=print)
assert report5[0]["error"], "坏文件要记录错误而不是抛出来"
assert len(records5) == len(records) + len(records4), \
    f"坏文件被跳过后其他文件仍要全部读入：{len(records5)}"
got = [r["file"] for r in report5]
assert got == ["旧格式.xls", "其他渠道名单.xlsx", "名单.csv"], got

# ── 6. 空公司名的行剔除；与云展 9 列上传口径一致 ──
junk = os.path.join(TMP, "有空行.xlsx")
_write_xlsx(junk, [("Sheet1", [
    ["企业名称", "联系电话"],
    ["", "13800000000"],
    [None, None],
    ["正常公司有限公司", "13900001111"],
])])
records6, report6 = C.clean_external_records([junk], log=print)
assert report6[0]["rows"] == 2, report6[0]        # 有电话号码的那空行会被读进来，但入库时会被剔掉
assert report6[0]["identified"] == ["“企业名称”→公司名", "“联系电话”→电话"], report6[0]["identified"]
rows6 = C.build_external_rows(records6, log=print)
assert len(rows6) == 1 and rows6[0]["企业名称"] == "正常公司有限公司", rows6

# 清洗结果能直接喂给云展 9 列上传文件构造（列名对得上）
import yzd_client as Y

out = os.path.join(TMP, "清洗后客户库入库数据_test.xlsx")
tpl = C.locate_template()
assert C.write_clean_excel(rows, out, template_path=tpl, log=print) == out
assert os.path.getsize(out) > 0
import openpyxl

ws = openpyxl.load_workbook(out).active
head = [c.value for c in ws[1]]
assert "客户名称" in head and "企业名称" in head and "手机号" in head, head
row2 = {head[i]: (c.value or "") for i, c in enumerate(ws[2])}
assert row2["企业名称"] == "深圳市蓝海科技有限公司"

deduped = Y.dedupe_rows(rows, log=print)
assert len(deduped) == len(rows), "同公司不同联系人不是重复行，不能被批内去重吃掉"
up_path = os.path.join(TMP, "yzd_upload_test.xlsx")
written = Y.write_import_workbook(deduped, up_path, log=print)
assert written == len(rows), written
uws = openpyxl.load_workbook(up_path).active
assert [c.value for c in uws[1]] == list(Y.IMPORT_HEADERS)

print("\nALL ASSERTS PASSED")
