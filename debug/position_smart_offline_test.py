# -*- coding: utf-8 -*-
"""岗位智能匹配离线断言：dept_only 改道、compose_position 合成、岗位族归类、
清洗层跨站回捞/岗位族进备注、b2b168「联系人是X」形态。"""
import sys, os
sys.path.insert(0, "/Users/jhnoplan/Desktop/PythonDemo/b2bcollect_build")
from cdp_base import (dept_only, norm_dept, compose_position, dept_from_email,
                      dept_from_company, position_family, parse_contact_field)
import customer_library_cleaner as cl
from b2b168_crawler import B2b168Crawler
from mic_crawler import MicCrawler
from aipage_crawler import AipageCrawler

ok = 0
def check(name, cond):
    global ok
    assert cond, f"FAIL: {name}"
    ok += 1
    print(f"  ✓ {name}")

print("── 1) dept_only：纯部门改道 ──")
check("客服部→客服部", dept_only("客服部") == "客服部")
check("销售部→销售部", dept_only("销售部") == "销售部")
check("网络销售→电商部", dept_only("网络销售") == "电商部")
check("张三丰不作部门", dept_only("张三丰") == "")
check("公司名不作部门", dept_only("深圳市XX有限公司销售部") == "")  # 长串>8
check("空串", dept_only("") == "")

print("── 2) compose_position：部门+称谓合成 ──")
check("销售+经理→销售经理", compose_position("销售", "经理") == "销售经理")
check("市场+经理→市场经理", compose_position("市场", "经理") == "市场经理")
check("完整岗位沿用", compose_position("市场部经理", "") == "市场部经理")
check("裸部门保部门", compose_position("销售", "") == "销售部")

print("── 3) 邮箱/公司名旁证 ──")
check("sales@→销售部", dept_from_email("sales@zcgm.cn") == "销售部")
check("export@→外贸部", dept_from_email("export@a.com") == "外贸部")
check("info@无线索", dept_from_email("info@a.com") == "")
check("公司名尾巴销售部", dept_from_company("东莞XX有限公司销售部") == "销售部")

print("── 4) position_family 岗位族归类 ──")
check("外贸经理→外贸", position_family("外贸经理")[0] == "外贸")
check("裸经理→管理/销售未细分", position_family("经理")[0] == "管理/销售（未细分）")
check("销售+邮箱→销售(岗位)", position_family("销售", email="x@y.com")[0] == "销售")
check("无岗位但有sales邮箱→销售(邮箱)", position_family("", email="sales@a.com") == ("销售部"[0:0] or "销售", "邮箱"))
check("公司名尾巴→销售(公司名)", position_family("", company="XX公司销售部") == ("销售", "公司名"))

print("── 5) 清洗层：跨站岗位回捞 + 岗位族进备注 ──")
# 证据一致（同联系人）→回捞进岗位列
r1 = {"公司名": "A公司", "联系人": "王经理", "岗位": "销售经理", "电话": "13800001111", "数据来源": "八方资源网"}
r2 = {"公司名": "A公司", "联系人": "李四", "岗位": "", "电话": "13900002222", "数据来源": "顺企网"}
merged, dup = cl.merge_records([r1, r2])
row = cl.make_row(merged[0]["fields"], merged[0]["sources"])
check("联系人首源为准", row["客户名称"] == "王经理" or row["客户名称"] == "李四")
# 同联系人跨站补岗位
r3 = {"公司名": "B公司", "联系人": "张三", "岗位": "", "电话": "1380000", "数据来源": "八方"}
r4 = {"公司名": "B公司", "联系人": "张三", "岗位": "厂长", "电话": "1390000", "数据来源": "顺企网"}
m2, _ = cl.merge_records([r3, r4])
row2 = cl.make_row(m2[0]["fields"], m2[0]["sources"])
check("同联系人跨站回捞岗位", row2["岗位"] == "厂长")
# 证据不一致→挂备注
r5 = {"公司名": "C公司", "联系人": "魏经理", "岗位": "经理", "电话": "138", "数据来源": "八方"}
r6 = {"公司名": "C公司", "联系人": "陈生", "岗位": "销售总监", "电话": "139", "数据来源": "11467"}
m3, _ = cl.merge_records([r5, r6])
row3 = cl.make_row(m3[0]["fields"], m3[0]["sources"])
check("主岗位同源(经理)", row3["岗位"] == "经理")
check("异源岗位进备注不丢", "销售总监" in row3["备注"] and "岗位(跨站来源)" in row3["备注"])

print("── 6) 清洗层：岗位族进备注（无明写岗位但有旁证）──")
r7 = {"公司名": "D公司", "联系人": "赵月梅", "岗位": "", "电话": "138",
      "邮箱": "sales@zcgm.cn", "数据来源": "爱采购"}
m4, _ = cl.merge_records([r7])
row4 = cl.make_row(m4[0]["fields"], m4[0]["sources"])
check("sales邮箱→备注岗位族:销售", "岗位族:销售" in row4["备注"])
# 有明写岗位时不重复写岗位族
r8 = dict(r7); r8["岗位"] = "销售经理"
m5, _ = cl.merge_records([r8])
row5 = cl.make_row(m5[0]["fields"], m5[0]["sources"])
check("有明写岗位不写岗位族", "岗位族" not in row5["备注"] and row5["岗位"] == "销售经理")

print("── 7) b2b168「联系人是X」形态 ──")
html = "<html><body><ul><li>联 系 人 是 孟凤环 。（经理）</li><li>电 话：0755-88886666</li></ul></body></html>"
data = B2b168Crawler.__new__(B2b168Crawler)._parse_contact_page(html)
check("『联系人是X』抽到姓名", data.get("联系人") == "孟凤环")
check("『联系人是X』抽出岗位经理", data.get("岗位") == "经理")

print("── 8) mic/aipage 纯部门改道 ──")
rec = {}
MicCrawler.__new__(MicCrawler)._parse_contact(
    '<meta name="description" content="中国制造网，佛山尚成夹芯板有限公司，联系人：客服部，联系电话：13360326817">', rec)
check("mic 客服部不当联系人", rec.get("联系人") != "客服部")
check("mic 客服部归岗位", rec.get("岗位") == "客服部")
ap = AipageCrawler.__new__(AipageCrawler)
out = ap._parse_shop({"cardInfo": {"contactName": "销售部", "phoneNumber": "13800001111"},
                      "entInfo": {"fullname": "测试有限公司"}}, {})
check("aipage 销售部归岗位", out.get("岗位") == "销售部" and not out.get("联系人"))

print(f"\n全部通过：{ok} 项")
