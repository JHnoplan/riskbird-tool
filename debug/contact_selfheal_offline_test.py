# -*- coding: utf-8 -*-
"""本轮修复的离线断言：重复称呼、公司名 SEO 尾巴、顺企网部门标签/主营尾巴、
种子库空联系人自愈（ct/rt）。全部为纯函数或内存态，不访问网络。"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "b2bcollect_build"))

from cdp_base import (parse_contact_field, strip_name_marketing_tail,  # noqa: E402
                      CdpSiteCrawler, normalize_spaces, is_placeholder_number, clean_address)
from gys_crawler import GysCrawler                                    # noqa: E402
from qihuo11467_crawler import Qihuo11467Crawler                       # noqa: E402
import customer_library_cleaner as cl                                  # noqa: E402

ok = 0


def check(name, cond):
    global ok
    assert cond, f"FAIL: {name}"
    ok += 1
    print(f"  ✓ {name}")


# ── 1) 重复称呼合并：b2b168 平台把「先生」写两遍（实测 contact.aspx「联系人：徐先生先生」）
for raw, want in (("徐先生先生", "徐先生"), ("李女士 女士", "李女士"),
                  ("王经理经理", "王经理"), ("张先生先生先生", "张先生")):
    name, pos = parse_contact_field(raw)
    check(f"重复称呼合并 {raw!r} → {want}", name == want)

# 反引用只缩「同一个词重复」，不能把「徐总经理」缩成「徐总」
n1, p1 = parse_contact_field("徐总经理")
check(f"「徐总经理」不被缩写成「徐总」（{n1}/{p1}）", n1 == "徐总经理" and p1 == "总经理")
n2, p2 = parse_contact_field("李先生")
check(f"两字称呼不缩成单字（{n2}）", n2 == "李先生")

# ── 2) 公司名 SEO 尾巴（gys 实测 53/116 行带「（XX厂家）」）
dirty = "东莞市新金阳光电科技有限公司（光伏连接器接头厂家）"
check("剥掉「（…厂家）」尾巴", strip_name_marketing_tail(dirty) == "东莞市新金阳光电科技有限公司")
check("剥掉「（…制造商）」尾巴",
      strip_name_marketing_tail("河北某业有限公司（光伏支架制造商）") == "河北某业有限公司")
check("地名括号不动（东方鑫鸿（北京）科技有限公司）",
      strip_name_marketing_tail("东方鑫鸿（北京）科技有限公司") == "东方鑫鸿（北京）科技有限公司")
check("括号在中间不动", strip_name_marketing_tail("奇迹多（江西）数字技术有限公司") == "奇迹多（江西）数字技术有限公司")
check("None 安全", strip_name_marketing_tail(None) == "")

# 清洗层同口径：已入库的脏名不必重采也能洗回来
row = cl.make_row({"公司名": dirty, "联系人": "王先生", "电话": "13800001111",
                   "状态": "成功", "官网": "https://x.gys.cn/"}, ["供应商网"])
check(f"清洗行企业名同步剥尾巴（{row['企业名称']}）",
      row["企业名称"] == "东莞市新金阳光电科技有限公司")

# ── 3) 顺企网：部门标签放联系人（实测合肥晶晨 contact.asp「市场部：杨经理」）
q = Qihuo11467Crawler()
html = ('<ul class="codl"><li>手　机：139054022033</li>'
        '<li>市场部：杨经理</li>'
        '<li>地　址：安徽省合肥市某路1号</li></ul>')
d = q._parse_contact(html)
# 岗位取部门标签「市场部」与人名后缀「经理」合成后的完整岗位「市场经理」
# （compose_position 的设计目标：只取一边会丢「市场」或丢「经理」）。
check(f"顺企网部门标签识别为联系人（{d.get('联系人')}/{d.get('岗位')}）",
      d.get("联系人") == "杨经理" and d.get("岗位") == "市场经理")
check("部门标签值是公司名时不造出假联系人",
      not q._parse_contact('<ul class="codl"><li>市场部：合肥市晶晨电子有限公司</li></ul>').get("联系人"))

# ── 4) 顺企网主营产品粘公司名+地址尾巴（实测 184/227 行）
raw_prod = "太阳能照明灯等光伏应用产品 金尚新能源科技股份有限公司 地址：山东省济南市"
cut = q._clean_products(raw_prod)
check(f"主营产品砍掉公司名/地址尾巴（{cut}）", cut == "太阳能照明灯等光伏应用产品")
check("干净主营不动", q._clean_products("光伏支架,光伏导轨") == "光伏支架,光伏导轨")
check("空值安全", q._clean_products("") == "")

# ── 5) 种子库空联系人自愈：ct/rt 决定「命中即跳过」还是「本轮补采」
g = GysCrawler()
g.incremental = True
rec_no = {"公司名称": "甲有限公司", "memberId": "aaa", "联系人": "", "状态": "成功"}
rec_yes = {"公司名称": "乙有限公司", "memberId": "bbb", "联系人": "朱先生", "状态": "成功"}
g._seen_store = {}
g._commit_to_store(rec_yes)
g._commit_to_store(rec_no)
k_no = "gys:aaa"
check("取到联系人 → ct=1 且不记 rt",
      g._seen_store["gys:bbb"]["ct"] == 1 and "rt" not in g._seen_store["gys:bbb"])
check("联系人取空 → ct=0 且 rt 递增",
      g._seen_store[k_no]["ct"] == 0 and g._seen_store[k_no]["rt"] == 1)
g._commit_to_store(rec_no)
check("第二次仍空 → rt=2", g._seen_store[k_no]["rt"] == 2)
g._commit_to_store(rec_no)
check("重试超过上限后 rt 继续累加但不越权锁全", g._seen_store[k_no]["rt"] == 3)

check("rt 未用尽 → 不锁定（下轮重采补联系人）", not g._entry_locked({"at": "x", "st": "成功", "ct": 0, "rt": 1}))
check("rt 用尽 → 锁定（站点确未公开，不再白跑）", g._entry_locked({"at": "x", "st": "成功", "ct": 0, "rt": 2}))
check("ct=1 → 锁定", g._entry_locked({"at": "x", "st": "成功", "ct": 1}))
check("旧版无 ct 的条目按已完整处理（避免一次重跑重访上千家）",
      g._entry_locked({"at": "x", "st": "成功"}))
check("非字典脏条目不炸", g._entry_locked(123))

g._seen_store = {"gys:aaa": {"at": "x", "st": "成功", "ct": 0, "rt": 1},
                 "gys:bbb": {"at": "x", "st": "成功", "ct": 1},
                 "gys:ccc": {"at": "x", "st": "成功"}}
check("_locked_keys 只放行已完整/重试耗尽者",
      g._locked_keys() == {"gys:bbb", "gys:ccc"})

# 瞬时状态（待重试/访问异常）不写种子库 → 天然会在下轮重采
g._seen_store = {}
g._commit_to_store({"公司名称": "丙有限公司", "memberId": "ccc", "联系人": "",
                    "状态": "详情页访问异常(待重试)"})
check("「待重试」非终态，绝不入种子库锁死", g._seen_store == {})

# ── 6) 清洗层入库前最后一道闸：客户名称写成号码/公司名 ────────
# 实测当前表 5 行爱采购/MIC 把手机号当联系人带进了清洗表（13590614380 等）。
for dirty, clean_val in (("18962536677", ""), ("13590614380", ""),
                        ("沈阳微特通用技术开发有限公司", ""),
                        ("徐先生先生", "徐先生"), ("王 经理", "王经理"),
                        ("朱先生13051353683", "朱先生"), ("杨经理", "杨经理")):
    got = cl._clean_contact(dirty)
    check(f"清洗层联系人校验 {dirty!r} → {got!r}", got == clean_val)

row_num = cl.make_row({"公司名": "苏州市浮能光伏科技有限公司", "联系人": "18962536677",
                       "电话": "18962536677", "状态": "成功"}, ["爱采购"])
check(f"纯号码联系人退回公司名（{row_num['客户名称']}）",
      row_num["客户名称"] == "苏州市浮能光伏科技有限公司")
row_dup = cl.make_row({"公司名": "铄盾新能源有限公司", "联系人": "徐先生先生",
                       "岗位": "经理", "电话": "13800001111", "状态": "成功"}, ["八方资源网"])
check(f"清洗层合并重复称呼（{row_dup['客户名称']}）", row_dup["客户名称"] == "徐先生")

# ── 7) 占位假号与地址按钮尾巴 ────────────────────────
check("顺企网 0515 80000000 判为占位假号", is_placeholder_number("0515 80000000"))
check("4 个连 0 的真实号段不误杀", (not is_placeholder_number("18710000805")
                              and not is_placeholder_number("15800005185")))
mob, land = cl.split_phones("0515 80000000 / 0515-82807298 / 15345275298")
check(f"清洗拆号时丢弃全 0 假号（{land} {mob}）",
      all("80000000" not in x for x in land) and "0515-82807298" in land and "15345275298" in mob)
check("MIC 地址尾部剔掉「公司主页」",
      clean_address("辽宁省 沈阳市 大东区 文官街50-1号 公司主页") == "辽宁省 沈阳市 大东区 文官街50-1号")
check("MIC 地址尾部剔掉导航按钮文案",
      clean_address("广东省 广州市 黄埔区 瑞和路39号F1栋201房 查看更多 相关产品 产品索引 举报")
      == "广东省 广州市 黄埔区 瑞和路39号F1栋201房")
check("「（注册地址）」工商后缀不误剔",
      clean_address("无锡市梁溪区柏庄北路128号（注册地址）") == "无锡市梁溪区柏庄北路128号（注册地址）")
row_addr = cl.make_row({"公司名": "沈阳微特通用技术开发有限公司", "地址": "辽宁省 沈阳市 大东区 文官街50-1号 公司主页",
                       "电话": "15204016273", "状态": "成功"}, ["中国制造网"])
check(f"备注里的地址也剥掉按钮词（{row_addr['备注']}）",
      "公司主页" not in row_addr["备注"] and "文官街50-1号" in row_addr["备注"])

print(f"\n全部 {ok} 项断言通过 ✅")
