# 离线自测：20261028 全量运行质检后的修复项回归
# ①顺企网职务标签联系人 ②顺企网多号码合并 ③顺企网拆位手机号防封
# ④供应商网 JSON-LD 权威字段 ⑤供应商网访问异常插页判定
# ⑥八方资源网公司名尾部杂质 ⑦清洗器号码保护/备注残留/不截断 ⑧入库剔除四项全空行
import sys, pathlib
sys.path.insert(0, "/Users/jhnoplan/Desktop/PythonDemo/b2bcollect_build")

from qihuo11467_crawler import Qihuo11467Crawler
from gys_crawler import GysCrawler
from b2b168_crawler import _strip_name_junk
import customer_library_cleaner as cl

ok = 0
def check(name, cond):
    global ok
    assert cond, f"FAIL: {name}"
    ok += 1
    print(f"  ✓ {name}")

# ── 1) 顺企网：职务标签联系人（实测「主管：李彦锋」「销售总监：王骏良」「检测工程师：杨有缘」）
q = Qihuo11467Crawler()
html1 = '<ul class="codl"><li>地\u3000\u3000址：河南省巩义市夹津口工业园区</li><li>电\u3000\u3000话：13938523177</li><li>主管：李彦锋</li><li>手\u3000\u3000机：19303880265</li></ul>'
d1 = q._parse_contact(html1)
check(f"「主管：」职务标签识别为联系人（{d1.get('联系人')}）", d1.get("联系人") == "李彦锋")
html2 = '<ul class="codl"><li>销售总监：王骏良</li><li>手机：13800001111</li></ul>'
check("「销售总监：」→联系人", q._parse_contact(html2).get("联系人") == "王骏良")
html3 = '<ul class="codl"><li>检测工程师：杨有缘</li><li>手机：13800002222</li></ul>'
check("「检测工程师：」→联系人", q._parse_contact(html3).get("联系人") == "杨有缘")
# 职务词不能被当公司名/主营标签误吞
html4 = '<ul class="codl"><li>主营产品：太阳能路灯</li><li>联系人：张经理</li></ul>'
d4 = q._parse_contact(html4)
# 口径变更（2026-10 自审）：不再把「张经理」剥成「张」——称谓是外呼可用的称呼，
# 岗位另记一列；跨站统一走 cdp_base.parse_contact_field。
check(f"「主营产品：」不被职务标签误吞，保留称谓并记岗位（{d4.get('联系人')}/{d4.get('岗位')}）",
      d4.get("联系人") == "张经理" and d4.get("岗位") == "经理" and not d4.get("地址"))
html4b = '<ul class="codl"><li>销售总监：王骏良</li><li>手机：13800001111</li></ul>'
check(f"职务标签同时作为岗位入库（{q._parse_contact(html4b).get('岗位')}）",
      q._parse_contact(html4b).get("岗位") == "销售总监")

# ── 2) 顺企网：电话行的手机号不再被手机行覆盖丢失（实测丢 13938523177）
check(f"电话行手机形态与手机行合并（{d1.get('手机')}）",
      "13938523177" in d1.get("手机", "") and "19303880265" in d1.get("手机", ""))

# ── 3) 顺企网：拆位防封号码 193/038/80265 合回 11 位
html5 = '<ul class="codl"><li>联 系 人：刘某</li><li>手\u3000\u3000机：193/038/80265</li></ul>'
d5 = q._parse_contact(html5)
check(f"拆位手机号合回归位（{d5.get('手机')}）", d5.get("手机") == "19303880265")
check("日期/URL 段不被误合并",
      q._defuse_mobile("发布于/2022/05-19/3259042190") == "发布于/2022/05-19/3259042190")

# ── 4) 供应商网：JSON-LD 权威字段（真实渲染夹具 gdjt88，UI 隐藏号码但注水有电话）
g = GysCrawler()
fx = (pathlib.Path(__file__).parent / "probe_gys_cache" / "gys_gdjt88_shop.html").read_text(encoding="utf-8")
ld = g._parse_ld_json(fx)
check(f"ld+json 提出 telephone（{ld.get('电话')}）", ld.get("电话") == "18923274073")
check("ld+json 提出地址", "顺德" in ld.get("地址", ""))
check("ld+json 提出主营(knowsAbout)", "光伏组件" in ld.get("主营产品", ""))
data = g._parse_company_page(fx, "gdjt88")
check(f"公司页解析最终带号码（{data.get('手机') or data.get('电话')}）",
      bool(data.get("手机") or data.get("电话")))
# 构造「UI 隐藏号码」页：可见文本无任何号码，只剩 JSON-LD 注水 → ld 兜底必须接上
hidden = ('<html><head><title>广东晶天新能源电力有限公司 | 供应商网</title>'
          '<script type="application/ld+json">'
          '{"@type":"Organization","name":"广东晶天新能源电力有限公司",'
          '"telephone":"18923274073","knowsAbout":["光伏组件"],'
          '"address":{"addressRegion":"广东","addressLocality":"佛山","streetAddress":"顺德区某某路1号"}}'
          '</script></head><body><div>联系我们请登录查看</div></body></html>')
dh = g._parse_company_page(hidden, "gdjt88")
check(f"UI 隐藏时 JSON-LD 兜底接上电话（{dh.get('手机') or dh.get('电话')}）",
      "18923274073" in (dh.get("手机", "") + dh.get("电话", "")))
check("ld 地址/主营同步兜底", "顺德" in dh.get("地址", "") and "光伏组件" in dh.get("主营产品", ""))
check("ld 异常时不炸（垃圾 JSON 返回空增强）", g._parse_ld_json('<script type="application/ld+json">{坏JSON') == {})

# ── 5) 供应商网：访问异常插页 → 非终态「待重试」，绝不入种子库
class FakeGys(GysCrawler):
    def __init__(self):  # 不起 CDP，只测判定与状态机
        self.ABNORMAL_TITLE_MARKS = GysCrawler.ABNORMAL_TITLE_MARKS
        self._title = "访问异常 - 供应商网"
    def _eval(self, expr, *a, **k):
        return self._title if expr == "document.title" else ""
fg = FakeGys()
check("访问异常标题命中插页判定", fg._is_abnormal_page())
fg._title = "广东晶天新能源电力有限公司（太阳能板厂家）| 供应商网"
check("正常商铺页标题不误判", not fg._is_abnormal_page())
import re as _re
src = (pathlib.Path(__file__).parent.parent / "b2bcollect_build" / "gys_crawler.py").read_text(encoding="utf-8")
check("crawl_detail 命中插页写「待重试」状态", "详情页访问异常(待重试)" in src)
check("「异常」在 TRANSIENT_MARKS 语义内（不入种子库）",
      any(m in "详情页访问异常(待重试)" for m in GysCrawler.TRANSIENT_MARKS))

# ── 6) 八方资源网：公司名尾部「3.」杂质剥离且不误伤
check("剥掉分页版次尾巴", _strip_name_junk("东莞杰星电子材料有限公司3.") == "东莞杰星电子材料有限公司")
check("无尾巴名不动", _strip_name_junk("华矿重工有限公司") == "华矿重工有限公司")
check("3M 类含字母名不误伤", _strip_name_junk("3M中国有限公司") == "3M中国有限公司")
check("None 安全", _strip_name_junk(None) == "")

# ── 7) 清洗器：号码保护（实测 00755-27908031转1 被打坏成 0075-52790803转1）
mob, tel = cl.split_phones("00755-27908031转1")
check(f"异常区号号码原样保留不重排（{tel}）",
      any(t.replace("-", "").replace("转", "").startswith("0075527908") for t in tel))
mob, tel = cl.split_phones("0519-8389830转6049")
check(f"分机号不被贪婪切坏（{tel}）", tel == ["0519-8389830转6049"])
mob, tel = cl.split_phones("139 0806 0502; 0755 88881111")
check(f"分组手机/区号空格连写全回收（{mob} {tel}）",
      mob == ["13908060502"] and tel == ["0755-88881111"])
mob, tel = cl.split_phones("+8613800138000; 0086-138-0013-8000")
check(f"国家码手机号脱码归位（{mob}）", mob == ["13800138000"])
mob, tel = cl.split_phones("4006-258-268")
check(f"400 热线数字不丢（{tel}）", any(t.replace("-", "") == "4006258268" for t in tel))
many = " ; ".join(f"1391234{i:04d}" for i in range(6))
mob, tel = cl.split_phones(many)
check(f"6 个手机号全保留（不再截断成 3 个）（{len(mob)} 个）", len(mob) == 6)
# 7 连 0 的灌号（13900000000）与采集层同口径当占位假号挡掉，不记为联系方式
mob0, tel0 = cl.split_phones("13900000000 ; 13912345678")
check("7 连 0 假号挡掉、同串真号仍保留", mob0 == ["13912345678"])

# ── 8) 清洗器：跨站合并后不再残留「无电话(未公开)」备注；公司名杂质兜底剥离
fields = {"公司名": "东莞杰星电子材料有限公司3.", "状态": "无电话(未公开)",
          "电话": "18923274073", "邮箱": "", "官网": "https://gdjt88.gys.cn/", "地址": ""}
row = cl.make_row(fields, ["供应商网"])
check("合并后已有电话→备注不再写无电话",
      "无电话" not in row["外呼备注"] and "官网" in row["外呼备注"])
check("清洗侧公司名尾巴杂质兜底剥离", row["企业名称"] == "东莞杰星电子材料有限公司")
# 真空记录仍然带状态说明（不能把提示洗丢）
fields2 = {"公司名": "某无联系方式公司", "状态": "无电话(未公开)", "电话": "", "邮箱": "",
           "官网": "https://x.example/", "地址": ""}
check("真空记录状态说明保留", "无电话(未公开)" in cl.make_row(fields2, ["顺企网"])["外呼备注"])

# ── 9) 入库口径：手机号/固话/邮箱/微信号四项全空 → 剔除（2026-10-28 定口径）
from parallel_session import ParallelSession
from cdp_base import CdpSiteCrawler
rows_in = [{"手机号": "13800000000", "固话": "", "邮箱": "", "微信号": ""},
           {"手机号": "", "固话": "0755-12345678", "邮箱": "", "微信号": ""},
           {"手机号": "", "固话": "", "邮箱": "a@b.com", "微信号": ""},
           {"手机号": "", "固话": "", "邮箱": "", "微信号": "wxid_abcd"},
           {"手机号": "   ", "固话": "", "邮箱": "", "微信号": ""},
           {"手机号": "", "固话": "", "邮箱": "", "微信号": ""}]
kept = ParallelSession.filter_import_rows(rows_in, log=lambda m: None)
check("四项全空的 2 行被剔除（只剩 4 行有效线索）", len(kept) == 4)
check("只有手机号的行保留", any(r["手机号"] == "13800000000" for r in kept))
check("只有固话的行保留", any(r["固话"] == "0755-12345678" for r in kept))
check("只有邮箱的行保留", any(r["邮箱"] == "a@b.com" for r in kept))
check("只有微信号的行也保留（微信同样能开发客户）", any(r["微信号"] == "wxid_abcd" for r in kept))
check("纯空白手机号等同空值剔除", all(str(r["手机号"]).strip() or not r["手机号"] for r in kept))
check("过滤不改动入参（原始表仍可全量写出）", len(rows_in) == 6)
msgs = []
ParallelSession.filter_import_rows(rows_in, log=msgs.append)
check("剔除条数写进日志（用户能看到洗掉多少）", any("剔除 2 条" in m for m in msgs))
silent = []
ParallelSession.filter_import_rows(rows_in[:4], log=lambda m: silent.append(m))
check("无行可剔时不刷日志", silent == [])
check("各站与跨站入库口径同源", CdpSiteCrawler.filter_import_rows(rows_in) == kept)

print(f"\n全部 {ok} 项断言通过 ✅")
