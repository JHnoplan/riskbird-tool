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

# ── 7. 电话多列合并：手机号列占了「电话」字段后，固话列不能被整列丢弃 ──
twocol = os.path.join(TMP, "手机固话分列.xlsx")
_write_xlsx(twocol, [("Sheet1", [
    ["企业名称", "客户名称", "手机号", "固话", "邮箱", "固话", ""],
    ["东莞市立勤科技有限公司", "陈生", "13900139000", "0769-88881111", "chen@liqin.com", "", ""],
    ["广州金田瑞麟净化设备公司", "黄工", "13800138000", "020-84445555", "", "", ""],
    ["佛山无名贸易公司", "", "", "", "", "", ""],            # 三空行：不能入库
    ["深圳蓝海科技有限公司", "李娜", "13700137000;13600136000", "0755-88881111", "li@lanhai.com", "", ""],
])])
records7, report7 = C.clean_external_records([twocol], log=print)
info7 = report7[0]
assert any("→电话（两列合并）" in s and "手机号" in s and "固话" in s
           for s in info7["identified"]), f"手机号/固话两列必须合并进电话字段：{info7['identified']}"
assert not any("固话" == s.strip("“”") for s in info7.get("skipped") or []), \
    f"固话列不能再被当成未使用列丢弃：{info7.get('skipped')}"
assert not any(str(s).strip() in ("", "第7列") for s in info7.get("skipped") or []), \
    f"没数据的重复表头/空列不该混进未使用列里干扰排查：{info7.get('skipped')}"

rows7 = C.build_external_rows(records7, log=print)
names7 = [r["企业名称"] for r in rows7]
assert "佛山无名贸易公司" not in names7, f"手机/固话/邮箱全空的行不该出现在入库文件：{names7}"
assert len(rows7) == 3, f"4 行原始数据应入库 3 条（剔 1 条无任何联系方式）：{rows7}"
liqin = next(r for r in rows7 if r["企业名称"] == "东莞市立勤科技有限公司")
assert liqin["手机号"] == "13900139000", liqin
assert liqin["固话"] == "0769-88881111", f"固话列要合并进来：{liqin}"
lanhai7 = next(r for r in rows7 if r["企业名称"] == "深圳蓝海科技有限公司")
assert lanhai7["手机号"] == "13700137000,13600136000", lanhai7
assert lanhai7["固话"] == "0755-88881111", lanhai7
# 列号升序拼接 → 手机号列在前，拆分后主联系人号码仍排最前
jintian7 = next(r for r in rows7 if r["企业名称"] == "广州金田瑞麟净化设备公司")
assert jintian7["手机号"] == "13800138000" and jintian7["固话"] == "020-84445555", jintian7
print("7 ✅ 电话多列合并 + 三空行剔除\n")

# ── 8. 「状态」列装的全是「存续」：不能当联系人，客户名称不能写成状态 ──
# 「存续」两三个字会被人名正则当成联系人，状态列就此抢下联系人字段，
# 整列客户名称被写成「存续」（法定代表人反而丢了）。
status_sheet = os.path.join(TMP, "带工商状态列的表.xlsx")
_write_xlsx(status_sheet, [("Sheet1", [
    ["序号", "公司名", "状态", "数据来源", "法定代表人", "电话", "邮箱", "地址"],
    [1, "淄博铭丰塑业有限公司", "存续", "企知道", "于波", "0533-7402660；13365336660", "a@b.com", "山东省淄博市临淄区产业园9#101"],
    [2, "诸暨市英能制冷配件厂", "存续", "企知道", "钱红锋", "15857520615", "", "诸暨市江藻镇银江村8号"],
    [3, "佛山市南海区恒发家具厂", "注销", "爱企查", "李常正", "0757-88886666", "c@d.com", "佛山市南海区里水镇"],
])])
records8, report8 = C.clean_external_records([status_sheet], log=print)
rows8 = C.build_external_rows(records8, log=print)
ident8 = {kv.split("→")[0].strip("“”"): kv.split("→")[1] for kv in report8[0]["identified"]}
print("8 列识别结果:", ident8)
assert ident8.get("状态") == "状态", f"状态列要归回状态字段，不能被认成联系人：{ident8}"
assert ident8.get("法定代表人") == "联系人", f"法定代表人才是联系人列：{ident8}"
for r in rows8:
    assert r["客户名称"] not in ("存续", "注销", "在业", "吊销"), f"客户名称写成状态值了：{r}"
assert rows8[0]["客户名称"] == "于波", rows8[0]
assert rows8[0]["手机号"] == "13365336660" and rows8[0]["固话"] == "0533-7402660", rows8[0]
assert rows8[2]["客户名称"] == "李常正", f"人名里的「常正」不能被当成状态词：{rows8[2]}"
print("8 ✅ 工商状态列不再冒充联系人\n")

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
