"""province_offline_test.py —— 省份精准采集离线断言（零网络）。

需求（2026-11-04 用户提出）：GUI 加「选择省份」按钮（全省份+港澳台，默认不勾），
开始采集后 8 个平台都要按省份匹配。

口径（2026-11-04 现场取证后修订）：
  历史上参数直筛站（mic=comProvince、makepolo=&ae=、1688=&province=）「交给网站筛、
  本地不二次过滤」，结果 1688 那轮入库的 91 条全部来自不吃 province 参数的站外 SEM
  页（地址写着江苏/河北/吉林…），而基类又拿已选省份把空白的「所在区域」盖章成「广东」，
  产出的是没人敢用的伪数据。现口径（trust-but-verify）：
    · 参数直筛仍然拼（提高命中率），但每条采回的结果都要用「所在区域/地址/公司名」
      本地逐条核验属地；
    · 直筛站分两档：mic/makepolo = lenient（只砍确认外省），1688 = strict（确认外省＋
      判不出都不入库）；
    · 数据层站（globalimporter / b2b168 / 11467 / aipage / maimaib2b）仍 fail-closed；
    · 绝不拿已选省份臆测回填：回填的「所在区域」必须是页面/公司名里读出来的证据
      （「广东·深圳」这种）；
    · 判不出省份的公司名回流企知道待采名单，由工商登记地址反查一次属地（不静默丢）；
    · 企知道补采通道同样有属地关卡（登记地址不属本省就不入库）。
"""
import os
import sys
import tempfile

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "b2bcollect_build"))

import province_filter as pf  # noqa: E402

PASS = FAIL = 0


def check(tag, got, want):
    global PASS, FAIL
    if got == want:
        PASS += 1
        print(f"  ok   {tag}")
    else:
        FAIL += 1
        print(f"  FAIL {tag}\n        实际={got!r}\n        期望={want!r}")


print("\n=== A 省份数据表与参数值表（34 区全量、陷阱词钉死）===")
check("省份总数 31+3", len(pf.PROVINCES), 34)
check("马可波罗拼音表覆盖全部省份", set(pf.MAKEPOLO_PINYIN) == set(pf.PROVINCES), True)
check("中国制造网英文表覆盖全部省份", set(pf.MIC_EN) == set(pf.PROVINCES), True)
check("山西=shanxi（陕西占 shanxii）", pf.MAKEPOLO_PINYIN["山西"], "shanxi")
check("陕西=shanxii（页面元素id实测）", pf.MAKEPOLO_PINYIN["陕西"], "shanxii")
check("山西MIC=Shanxi", pf.MIC_EN["山西"], "Shanxi")
check("陕西MIC=Shaanxi", pf.MIC_EN["陕西"], "Shaanxi")
check("西藏MIC=Tibet", pf.MIC_EN["西藏"], "Tibet")
check("内蒙古MIC=InnerMongolia", pf.MIC_EN["内蒙古"], "InnerMongolia")
check("马可波罗拼音值互不撞车", len(set(pf.MAKEPOLO_PINYIN.values())), len(pf.PROVINCES))
check("参数直筛站名单", sorted(pf.PARAM_SITES), ["1688", "makepolo", "mic"])
check("8 平台=7 并行站+1688", {"mic", "b2b168", "11467", "aipage", "maimaib2b",
                              "globalimporter", "makepolo", "1688"} <=
      pf.PARAM_SITES | {"b2b168", "11467", "aipage", "maimaib2b", "globalimporter"}, True)

print("\n=== B 属地判定（拿五站记录里 所在区域/地址 的真实形态逐条钉）===")
_M = [  # (省份, 文本, 期望)——文本全是各站实测字段形态
    ("广东", "广东-深圳", "match"),                 # 11467 weizhi「省-市」
    ("广东", "广东省深圳市南山区", "match"),          # b2b168/麦麦 地址
    ("广东", "深圳", "match"),                       # 只有地级市
    ("广东", "东莞大朗镇", "match"),                 # 市+镇（麦麦 _region_from 形态）
    ("内蒙古", "内蒙古-呼和浩特", "match"),           # 行政区划尾巴不伤短名
    ("黑龙江", "黑龙江 哈尔滨", "match"),
    ("江西", "浙江路126号", "miss"),                # 街道名误碰「浙江」：两行都会丢（fail-closed），仅台账措辞不同
    ("北京", "北京市东城区", "match"),
    ("重庆", "重庆市", "match"),
    ("陕西", "陕西-西安", "match"),
    ("山西", "山西-太原", "match"),
    ("湖南", "湖南长沙", "match"),                   # aipage location 连写形态
    ("湖北", "湖南长沙", "miss"),
    ("广东", "江苏南京", "miss"),
    ("广东", "深圳江西商会", "unknown"),             # 双省命中按判不出处置，绝不误归
    ("广东", "上海办事处 东莞工厂", "unknown"),
    ("青海", "青海海北州", "match"),                 # 海北不勾出别的省
    ("吉林", "吉林吉林市", "match"),
    ("广东", "", "unknown"),
    ("广东", "18800008888", "unknown"),
]
for pv, text, want in _M:
    check(f"match_province({pv!r}, {text!r})", pf.match_province(pv, text), want)

print("\n=== C 参数直筛三站的 URL 拼接 ===")
from mic_crawler import MicCrawler          # noqa: E402
from makepolo_crawler import MakepoloCrawler  # noqa: E402

_mic = MicCrawler(port=9255)
_mic.province = "广东"
u = _mic.build_search_url("泵", 1)
check("mic 拼 comProvince=Guangdong", "comProvince=Guangdong" in u, True)
check("mic 不再带 nolimit", "nolimit" in u, False)
_mic.province = ""
check("mic 未选省保持 nolimit", "comProvince=nolimit" in _mic.build_search_url("泵", 2), True)

_mp = MakepoloCrawler(port=9255)
_mp.province = "广东"
u = _mp.build_search_url("泵", 1)
check("makepolo 拼 &ae=guangdong", u.endswith("&ae=guangdong"), True)
_mp.province = "陕西"
check("makepolo 陕西=shanxii 不是 shanxi", _mp.build_search_url("泵", 1).endswith("&ae=shanxii"), True)
_mp.province = ""
check("makepolo 未选省不带 ae", "ae=" in _mp.build_search_url("泵", 1), False)

# 1688 采集器依赖 selenium：本机 .venv 有则测；站方参数值=简体短名（UTF-8 百分号编码）
try:
    sys.path.insert(0, os.path.join(BASE, "ali1688_build"))
    import ali1688_crawler as _m1688
    _a = _m1688.Ali1688Crawler.__new__(_m1688.Ali1688Crawler)
    _a.province = "广东"
    from urllib.parse import quote
    check("1688 省份后缀", _a._province_suffix(), "&province=" + quote("广东"))
    _a.province = ""
    check("1688 未选省无后缀", _a._province_suffix(), "")
except ImportError:
    print("  (跳过 1688 URL 用例：本环境无 selenium)")

print("\n=== D 数据层站的入库关卡（cdp_base._apply_province_filter）===")
from gi_crawler import GiCrawler            # noqa: E402
from qihuo11467_crawler import Qihuo11467Crawler  # noqa: E402

for cls, slug in ((GiCrawler, "globalimporter"), (Qihuo11467Crawler, "11467")):
    c = cls(port=9255)
    logs = []
    c._log = lambda m="": logs.append(str(m))
    c.province = "广东"
    ok = {"公司名称": "东莞市某某泵业有限公司", "所在区域": "广东-东莞", "关键词": "泵"}
    bad = {"公司名称": "苏州某某泵业有限公司", "所在区域": "江苏-苏州", "关键词": "泵"}
    unknown = {"公司名称": "某某泵业有限公司", "所在区域": "", "地址": "", "关键词": "泵"}
    check(f"{slug} 本省记录入库", c._add_result(ok) is None or True, True)
    check(f"{slug} 本省进 results", len(c.results), 1)
    c._add_result(bad)
    check(f"{slug} 外省被挡", len(c.results), 1)
    c._add_result(unknown)
    check(f"{slug} 判不出省也被挡(fail-closed)", len(c.results), 1)
    check(f"{slug} 确认外省丢弃计数", c._pf_dropped, 1)
    check(f"{slug} 属地未标注计数", c._pf_unknown, 1)
    check(f"{slug} 被挡的没进 kw_yield(净新增不虚报)", c.kw_yield.get("泵"), 1)
    # 判不出省份的公司不静默丢：回流外部钩子（企知道反查属地）
    got = []
    c.province_unknown_sink = lambda names: got.extend(names)
    c._add_result(unknown)
    check(f"{slug} 属地未标注回流反查队列", got, ["某某泵业有限公司"])
    check(f"{slug} 确认外省不回流（本来就是外省）", c._pf_dropped, 1)
    # 未选省：一切照旧，一条不挡
    c2 = cls(port=9255)
    c2._log = lambda m="": None
    c2._add_result(bad)
    check(f"{slug} 全国模式不误挡", len(c2.results), 1)

print("\n=== E 参数直筛站：筛了也要本地核，但不臆测回填 ===")
_mic2 = MicCrawler(port=9255)
_mic2._log = lambda m="": None
_mic2.province = "广东"
rec = {"公司名称": "某某公司", "所在区域": "", "地址": ""}
_mic2._add_result(rec)
check("mic 区域为空不再臆测盖省份（要的是证据不是巧合）", rec.get("所在区域"), "")
check("mic lenient：判不出省份仍保留（站方已筛，不拿弱信号误杀）", len(_mic2.results), 1)
rec3 = {"公司名称": "某某公司", "所在区域": "", "地址": "广东省东莞市大朗镇"}
_mic2._add_result(rec3)
check("mic 回填用页面证据而不是参数名", rec3.get("所在区域"), "广东·东莞")
rec2 = {"公司名称": "某某公司", "所在区域": "Guangdong, China"}
_mic2._add_result(rec2)
check("mic 已有区域不篡改", rec2.get("所在区域"), "Guangdong, China")
bad_mic = {"公司名称": "苏州某某泵业有限公司", "所在区域": "江苏-苏州"}
_mic2._add_result(bad_mic)
check("mic 站方筛漏了的外省记录本地拦下", len(_mic2.results), 3)
check("mic 不拿已选省份盖章（结果表里无一条伪区域）",
      [r.get("所在区域") for r in _mic2.results].count("广东"), 0)

print("\n=== E2 公司名也是属地信号（修正误杀）===")
check("仅公司名可判→match", pf.match_province("广东", "佛山安可锚链有限公司"), "match")
check("公司名外省→miss", pf.match_province("广东", "河北某某管道有限公司"), "miss")
check("无属地信号→unknown", pf.match_province("广东", "某某设备有限公司"), "unknown")
_save = {"公司名称": "深圳市深海诺科技有限公司", "所在区域": "", "地址": ""}
check("judge_record 救回只在公司名里写属地的本省公司",
      pf.judge_record("globalimporter", "广东", _save), ("match", True))

print("\n=== F 会话链路：configure(province) 下发到全部 8 站 ===")
import parallel_session as ps  # noqa: E402

sess = ps.ParallelSession(enabled=[s for s in ps.SITE_ORDER if s != "1688"] +
                          (["1688"] if "1688" in ps.SITE_CLASSES else []))
sess.configure(province="浙江")
slugs = list(sess.crawlers)
check("并行站不含企知道（用户明示除外）", "qzd" in slugs, False)
check("全部启用站 province 下发", {sl: sess.crawlers[sl].province for sl in slugs},
      {sl: "浙江" for sl in slugs})
check("含 1688（勾选才启用，本轮显式启用）",
      sess.crawlers["1688"].province if "1688" in slugs else "", "浙江")
check("企知道通道 province 同步下发（旧版只给节奏参数、属地无人把关）",
      sess.qzd.province if sess.qzd is not None else None, "浙江")
check("各站属地未标注都接上反查钩子",
      all(getattr(sess.crawlers[sl], "province_unknown_sink", None) is not None
          for sl in slugs), True)
# 不限页数不能把站方限量撞光，也不能把「不限」钳成「1 页」
sess.configure(max_pages=0)
if "1688" in slugs:
    check("1688「不限页」翻成硬上限 10 页（旧钳成 1 页是本轮只翻 8 页的根因）",
          sess.crawlers["1688"].max_pages, 10)
check("跨国采购网仍钉在站方配额 3 页（限量不受「不限」影响）",
      sess.crawlers["globalimporter"].max_pages, 3)
check("无配额站点保持真不限页", sess.crawlers["mic"].max_pages, 0)

print("\n=== F2 1688 适配器：站外 SEM 默认关 / strict 属地核验 / 不臆测回填 ===")
import site_adapters as sa  # noqa: E402

if sa.HAS_1688:
    a16 = sa.Ali1688Site(port=9255)
    a16._log = lambda m="": None
    a16.incremental = False          # 离线用例不写真实种子库
    a16.province = "广东"
    check("1688 站外 SEM 渠道默认关闭（实测既不吃 keywords 也不吃 province）",
          a16.sem_channel, False)
    a16.max_pages = 0
    check("1688 不限页→翻满 10 页而不是 1 页", a16.max_pages, 10)
    a16.max_pages = 4
    check("1688 显式页数照常生效", a16.max_pages, 4)
    sent = []
    a16.province_unknown_sink = lambda names: sent.extend(names)
    hb = {"公司名称": "河北某某管道装备有限公司", "所在区域": "", "地址": "河北省沧州市盐山县"}
    sd = {"公司名称": "深圳某某海洋科技有限公司", "所在区域": "", "地址": ""}
    nr = {"公司名称": "某某海洋装备有限公司", "所在区域": "", "地址": ""}
    for r in (hb, sd, nr):
        a16._add_result(r)
    check("1688 外省地址被本地拦下（站方 province 参数在站外页上无效）",
          len(a16.results), 1)
    check("1688 确认外省计数", a16._pf_dropped, 1)
    check("1688 属地判不出也不入库（strict）", a16._pf_unknown, 1)
    check("1688 仅公司名写属地不再被误杀",
          a16.results[0]["公司名称"], "深圳某某海洋科技有限公司")
    check("1688 回填的是页面证据而不是参数名",
          a16.results[0]["所在区域"], "广东·深圳")
    check("1688 判不出属地的转企知道反查（不静默丢）", sent, ["某某海洋装备有限公司"])
    a16.province = ""
    a16._add_result(dict(hb))
    check("1688 全国模式不误挡外省记录", len(a16.results), 2)
else:
    print("  (跳过 1688 适配器用例：本环境依赖不全)")

print("\n=== F3 企知道补采通道：工商地址属地关卡 ===")
if sa.HAS_QZD:
    q = sa.QizhidaoSite(port=9255)
    q._log = lambda m="": None
    q.province = "广东"
    gd = {"公司名": "广州某某海洋科技有限公司",
          "地址": "广州市天河区潭村路348号2102室", "电话": "020-38880000"}
    zj = {"公司名": "桐乡某某海洋科技有限公司",
          "地址": "浙江省嘉兴市桐乡市高桥街道南日路228号", "电话": "0573-88880000"}
    nf = {"公司名": "某某海洋工程有限公司", "错误": "未找到该公司"}
    for r in (gd, zj, nf):
        q._add_result(r)
    check("企知道本省记录入库", len(q.results), 2)
    check("企知道外省记录被工商地址拦下（本轮放进的那条浙江）", q._pf_dropped, 1)
    check("企知道未找到线索照旧入库（不丢线索）",
          q.results[1].get("错误"), "未找到该公司")
    check("企知道记录补上原始表「公司名称」列（旧版整列空白）",
          q.results[0].get("公司名称"), "广州某某海洋科技有限公司")
    check("企知道回填区域带城市证据", q.results[0].get("所在区域"), "广东·广州")
    q.province = ""
    q._add_result(dict(zj))
    check("企知道全国模式不误挡", len(q.results), 3)
    check("企知道未选省不臆测回填区域", q.results[2].get("所在区域", ""), "")
else:
    print("  (跳过企知道通道用例：本环境依赖不全)")

print("\n=== F4 1688 站外 SEM 渠道：打开开关后也要有双重相关性护栏 ===")
if sa.HAS_1688:
    b = sa.Ali1688Site(port=9255)
    b._log = lambda m="": None
    b.incremental = False
    b.relevance_guard = True
    b.sem_channel = True               # 本用例单独验证渠道本身（默认仍关）
    b.delay_between = 0
    b.max_pages = 3
    b._seen_members = set()
    b._early_skipped = 0
    page1 = [
        {"memberId": "b2b_good", "company": "深圳某某海洋装备有限公司",
         "subject": "海洋能装备装置", "offerId": "1"},
        {"memberId": "b2b_junk", "company": "义乌某某饰品有限公司",
         "subject": "卡通钥匙扣挂件", "offerId": "2"},
    ]

    class _D:
        page_source = "P1"        # 第一页有卡片，第二页空（翻页自然收尾）
    b.driver = _D()
    b._goto = lambda url, tries=4: True
    b._parse_list_page = lambda html: list(page1) if html == "P1" else []
    outs = list(b._iter_keyword_offers("海洋能装备"))
    check("SEM 列表早过滤砍掉无关商家（不占详情请求）",
          [o["company"] for o in outs], ["深圳某某海洋装备有限公司"])
    check("SEM 早过滤计数进账目", b._early_skipped, 1)
    check("SEM 无关商家只按「关键词#公司」作用域记，换词不误跳",
          "义乌某某饰品有限公司" in b._seen_members, False)
    check("SEM 结果带 guard（进详情前再做类目复核）", outs[0].get("guard"), True)

print("\n=== G GUI：弹窗类 + 按钮/文案/快照/续采恢复（沙箱，零网络）===")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt5.QtWidgets import QApplication  # noqa: E402
import b2bcollect_gui as gui  # noqa: E402

_app = QApplication.instance() or QApplication([])
_sandbox = tempfile.mkdtemp(prefix="prov_home_")
gui.MainWindow._get_desktop_dir = lambda self: _sandbox
_w = gui.MainWindow()
check("有「选择省份」按钮", _w.btn_province.text(), "选择省份")
check("默认全国不限", _w.selected_province, "")
check("默认文案回显", _w.lbl_province.text(), "🗺 采集范围：全国（不限省份）")
dlg = gui.ProvincePickerDialog("", _w)
check("弹窗按钮数=全国+34", len(dlg._radios), 35)
_pv = {v for v in dlg._radios.values()}
check("弹窗选项=全国+全部省份", _pv == set(pf.PROVINCES) | {""}, True)
dlg2 = gui.ProvincePickerDialog("广东", _w)
_gd = next(rb for rb, v in dlg2._radios.items() if v == "广东")
check("回显打开时选中上次省份", _gd.isChecked(), True)
_w.selected_province = "广东"
_w._refresh_province_label()
check("选后文案明显", _w.lbl_province.text(), "🗺 采集范围：广东（只采广东的企业）")
check("快照带省份", _w._params_snapshot().get("province"), "广东")
# 老快照（没 province 键）续采回填：按全国不限处理，不许崩
_w._apply_params({"delay_between": 5.0, "relevance_guard": True})
check("老快照回填=全国不限", _w.selected_province, "")
_w._apply_params({"province": "浙江"})
check("新快照回填省份", _w.selected_province, "浙江")
check("回填后文案同步", "浙江" in _w.lbl_province.text(), True)

print(f"\n===== 通过 {PASS} / 失败 {FAIL} =====")
sys.exit(1 if FAIL else 0)
