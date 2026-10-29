# -*- coding: utf-8 -*-
"""离线自测：2026-10-28「供应商网/八方资源网联系人为空」全面核查后的修复回归。

覆盖取证结论：
  ① 跨站联系人/岗位统一口径（cdp_base.parse_contact_field）+ 占位假号判定
  ② 供应商网：标签与值拆在两个 DOM 节点（<label>联系人：</label><span><em>朱先生</em>、
     <dt>联系人：</dt><dd>）→ 结构化提取（真实页夹具 12 家）
  ③ 供应商网：/contact-information/ 联系页补访口径（真实页夹具 12 家）
  ④ 八方资源网：联系人只在 /contact.aspx 与 /m/contact.aspx（真实页夹具 8 页），
     且必须滤掉「电　话：000-000000」「传　真：暂未填写」占位假号
  ⑤ 岗位贯通：采集记录 → _to_cleaner_record → 清洗表列 / 原始表列
  ⑥ 跨站合并：岗位与联系人同源、次要联系人归集到备注（不再静默丢弃）
"""
import sys
import glob
import os

sys.path.insert(0, "/Users/jhnoplan/Desktop/PythonDemo/b2bcollect_build")

from cdp_base import parse_contact_field, is_placeholder_number, normalize_spaces  # noqa: E402
from gys_crawler import GysCrawler, _is_platform_email                              # noqa: E402
from b2b168_crawler import B2b168Crawler                                           # noqa: E402
import customer_library_cleaner as cl                                              # noqa: E402
import parallel_session as ps                                                       # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
GYS_CACHE = os.path.join(HERE, "probe_gys_cache")
B2B_CACHE = os.path.join(HERE, "probe_contact_cache")

ok = 0


def check(name, cond):
    global ok
    assert cond, f"FAIL: {name}"
    ok += 1
    print(f"  ✓ {name}")


# ── 1) 联系人/岗位统一口径 ────────────────────────────────────
CASES = [
    # (原始值, 期望联系人, 期望岗位)  全部取自实机页面原文
    ("朱先生", "朱先生", ""),                       # gys 商铺页 <dd>（只有称谓：不能剥成「朱」）
    ("王国民 先 生 （经理）", "王国民", "经理"),      # b2b168 contact.aspx（全角空格+两个 span）
    ("王国民先生  （经理）", "王国民", "经理"),       # b2b168 /m/contact.aspx
    ("周经理", "周经理", "经理"),                    # 八方/顺企网「姓名： 周经理」（不再剥成「周」）
    ("牛总先生  （经理）", "牛总", "经理"),
    ("魏经理女士", "魏经理", "经理"),
    ("张厂长", "张厂长", "厂长"),
    ("王经理推销勿扰", "王经理", "经理"),             # 爱采购营销尾巴必须截断
    ("18962536677", "", ""),                        # 爱采购把号码填进联系人列 → 作废
    ("steven", "steven", ""),                       # MIC 英文名
    ("未提供", "", ""), ("暂无", "", ""), ("李先生13800001111", "李先生", ""),
    ("东莞市光明电器有限公司", "", ""),               # 公司名误入联系人列 → 作废
    ("先生", "", ""), ("经理", "", ""),               # 只剩称谓/光缀词 → 作废
]
for raw, want_name, want_pos in CASES:
    got = parse_contact_field(raw)
    check(f"parse_contact_field({raw!r}) → {got}", got == (want_name, want_pos))

check("全 0 占位假号 000-000000 判为占位", is_placeholder_number("000-000000"))
check("「暂未填写」判为占位", is_placeholder_number("暂未填写"))
check("QQ 000000 判为占位", is_placeholder_number("000000"))
check("灌填的 11111111 判为占位", is_placeholder_number("11111111"))
check("真实手机号不判为占位", not is_placeholder_number("13805492842"))
check("真实固话不判为占位", not is_placeholder_number("0755-27225780"))
# 全角空格先归一成普通空格，中文之间的空格再合拢（「姓　　名」→「姓名」），
# 目的只是清掉 \u3000/&nbsp; 这类不可见字符，不留断裂。
check("全角空格归一（姓\\u3000\\u3000名： → 姓名：）", normalize_spaces("姓\u3000\u3000名：") == "姓名：")
check("英文/数字名内部空格保留", normalize_spaces("John  Smith") == "John Smith")
check("nbsp 与全角空格一并压掉", normalize_spaces("李\xa0先生　经理") == "李先生经理")
check("gys 平台客服邮箱被拦", _is_platform_email("kefu@gys.cn") and not _is_platform_email("sales@aosien.com"))

# ── 2/3) 供应商网：真实商铺页 + 联系页夹具 ───────────────────
g = GysCrawler()
shops = sorted(glob.glob(os.path.join(GYS_CACHE, "shop_*.html")))
contacts = sorted(glob.glob(os.path.join(GYS_CACHE, "contact_*.html")))
check("gys 真实商铺页夹具齐备", len(shops) >= 12 and len(contacts) >= 12)
shop_hit = 0
for f in shops:
    sub = os.path.basename(f)[5:-5]
    d = g._parse_company_page(open(f, encoding="utf-8").read(), sub)
    if sub == "mingxuanxny":
        # 该家商铺页确实没公开联系人（实测 DOM 无「联系人」字样），不能编出来
        check("gys 商铺页真无联系人时保持为空（mingxuanxny）", not d.get("联系人"))
    else:
        shop_hit += bool(d.get("联系人"))
    if sub == "dongfangxinhong":
        check(f"gys 商铺页结构化取到联系人+岗位+手机（{d.get('联系人')}/{d.get('岗位')}/{d.get('手机')}）",
              d.get("联系人") == "朱先生" and d.get("岗位") == "市场部经理"
              and d.get("手机") == "13051353683")
        check(f"gys 商铺页地址不带「查看地图」按钮文案（{d.get('地址')!r}）",
              "查看地图" not in (d.get("地址") or ""))
check(f"gys 商铺页联系人取到 {shop_hit}/{len(shops) - 1} 家（修复前实测 0/12）",
      shop_hit == len(shops) - 1)
cp_hit = 0
for f in contacts:
    sub = os.path.basename(f)[8:-5]
    d = g._parse_company_page(open(f, encoding="utf-8").read(), sub)
    cp_hit += bool(d.get("联系人"))
    if sub == "mingxuanxny":
        check(f"gys 联系页补访救回商铺页没有的联系人（{d.get('联系人')}/{d.get('岗位')}）",
              d.get("联系人") == "李女士" and d.get("岗位") == "客服经理"
              and d.get("电话") == "022-60877773")
check(f"gys 联系页联系人取到 {cp_hit}/{len(contacts)} 家（补访后 12/12 全覆盖）",
      cp_hit == len(contacts))

# ── 4) 八方资源网：/contact.aspx 与 /m/contact.aspx 夹具 ──────
b = B2b168Crawler()
pages = sorted(glob.glob(os.path.join(B2B_CACHE, "page_*contact.aspx.html")))
check("b2b168 联系方式页夹具齐备（桌面+移动各 4）", len(pages) >= 8)
got_all = {}
for f in pages:
    host = os.path.basename(f).replace("page_", "").split("_b2b168")[0]
    d = b._parse_contact_page(open(f, encoding="utf-8").read())
    got_all.setdefault(host, []).append(d)
for host, name, pos in (("gtqgdprqajt1", "王国民", "经理"), ("yzbovj8n2l1xoh5ph2", "牛总", "经理"),
                        ("shmrhu_cn", "魏经理", "经理")):
    want = next((d for d in got_all[host] if d.get("联系人") == name), None)
    check(f"b2b168 {host} 联系方式页取到联系人 {name}（岗位 {pos}）",
          want is not None and want.get("岗位") == pos)
    if want:
        check(f"b2b168 {host} 占位假号 000-000000 不入电话列（电话={want.get('电话', '')!r}）",
              "000-000000" not in str(want.get("电话", "")) and "000-000000" not in str(want.get("手机", "")))
mobile_pages = [d for host, lst in got_all.items() for d in lst if d.get("邮箱")]
check(f"b2b168 邮箱随联系方式页一并补采（{len(mobile_pages)} 页取到邮箱）", len(mobile_pages) >= 3)

# ── 5) 岗位贯通：记录 → 清洗字段 / 表头 ───────────────────────
rec = {"关键词": "光伏组件", "公司名称": "临沂双亚照明电器有限公司", "联系人": "王国民",
       "岗位": "经理", "电话": "", "手机": "13805492842", "邮箱": "3022212375@qq.com",
       "地址": "山东省临沂市兰山区", "状态": "成功", "数据来源": "八方资源网"}
ck = B2b168Crawler()._to_cleaner_record(rec)
check(f"采集记录岗位透传进清洗字段（{ck.get('岗位')}）", ck.get("岗位") == "经理")
check("原始表头含岗位列", "岗位" in ps.RAW_HEADERS)
check("单站导出表头含岗位列", "岗位" in B2b168Crawler.EXPORT_HEADERS)
row = cl.make_row({k: v for k, v in ck.items() if k != "数据来源"} | {"公司名": rec["公司名称"],
                                "数据来源": "八方资源网", "官网": "https://x.b2b168.com/"}, ["八方资源网"])
check(f"清洗行「客户名称/岗位」落位（{row['客户名称']}/{row['岗位']}）",
      row["客户名称"] == "王国民" and row["岗位"] == "经理")

# ── 6) 跨站合并：岗位同源 + 次要联系人归集 ─────────────────────
recs = [
    {"公司名": "河北某新能源有限公司", "联系人": "陈", "岗位": "经理", "电话": "13700001111",
     "数据来源": "八方资源网"},
    {"公司名": "河北某新能源有限公司", "联系人": "黄双华", "电话": "0310-8888888",
     "数据来源": "供应商网"},
    {"公司名": "河北某新能源有限公司", "联系人": "黄先生", "岗位": "销售",
     "数据来源": "顺企网"},
]
merged, dup = cl.merge_records(recs)
check("跨站按公司名合并为一条", len(merged) == 1 and dup == 2)
mf = merged[0]["fields"]
check(f"主联系人保留首源、岗位与它同源（{mf.get('联系人')}/{mf.get('岗位')}）",
      mf.get("联系人") == "陈" and mf.get("岗位") == "经理")
row2 = cl.make_row(mf, merged[0]["sources"])
check(f"次要联系人归集进备注不丢弃（{row2['备注']}）",
      "黄双华" in row2["备注"] and "黄先生" in row2["备注"])
check("合并后号码取并集", "13700001111" in row2["手机号"] and "0310-8888888" in row2["固话"])

print(f"\n全部 {ok} 项断言通过 ✅")
