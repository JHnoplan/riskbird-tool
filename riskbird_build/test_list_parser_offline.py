"""离线验证列表页卡片解析器（用实机保存的 debug_rb_list_full.html，不联网不耗额度）。

用 lxml + Selenium 接口桩（element.text = innerText 语义）驱动真实的
RiskBirdCrawlerV2._parse_list_cards / _match_card，断言字段与实机页面一致。
"""
from lxml import html as lhtml

from riskbird_crawler_v2 import RiskBirdCrawlerV2

DOC = lhtml.parse("debug_rb_list_full.html").getroot()


class El:
    """Selenium  WebElement 的最小桩：只用到 text / get_attribute / find_elements"""

    def __init__(self, node):
        self._n = node

    @property
    def text(self):
        raw = "".join(self._n.itertext())
        return " ".join(raw.split())

    def get_attribute(self, name):
        return self._n.get(name)

    def find_elements(self, _by, xpath):
        return [El(x) for x in self._n.xpath(xpath)]


class Driver:
    def find_elements(self, _by, xpath):
        return [El(x) for x in DOC.xpath(xpath)]


c = RiskBirdCrawlerV2()
c.driver = Driver()

cards = c._parse_list_cards()
print(f"解析到卡片 {len(cards)} 张")
for card in cards[:3]:
    print("  ", card)

assert cards, "未解析到任何卡片"

t = c._match_card(cards, "深圳市腾讯计算机系统有限公司")
assert t is not None, "精确匹配失败"
print("\n目标卡片:", t)
assert t["状态"] == "在营", t["状态"]
assert t["法定代表人"] == "马化腾", t["法定代表人"]
assert "6500.0万" in t["注册资本"], t["注册资本"]
assert t["成立日期"] == "1998-11-11", t["成立日期"]
assert t["统一社会信用代码"] == "91440300708461136T", t["统一社会信用代码"]
assert t["电话"] == "86013388", t["电话"]
assert t["邮箱"] == "wangqiulan@sinochem.com", t["邮箱"]
assert t["官网"] == "https://www.qq.com/", t["官网"]
assert "腾讯大厦" in t["地址"], t["地址"]
assert t["entid"] == "AerjZTfkSh0", t["entid"]
assert "/ent/" in t["详情页URL"], t["详情页URL"]

# 未找到场景：不能把不相干卡片当成目标公司（防止错数据入库）
assert c._match_card(cards, "完全不存在的公司名字XYZ有限公司") is None
# 宁缺勿错：一字之差的另一家主体不得被当作目标
assert c._match_card(cards, "深圳市腾讯计算机系统有限公") is None, "缺字应判未找到"
assert c._name_match("苏州德兰智能装备有限公司", "苏州德星云智能装备有限公司") is False
assert c._name_match("华为技术有限公司", "华为技术有限公司西安分公司") is False
# 可接受的格式差异
assert c._name_match("深圳市腾讯计算机系统有限公司 ", "深圳市腾讯计算机系统有限公司")
assert c._name_match("某某科技有限公司", "某某科技有限公司(深圳)")
assert c._name_match("某某科技有限公司（深圳）", "某某科技有限公司(深圳)")

# 新誉列表页（实机保存）：目标卡片必须命中母公司，不能拉到分公司
DOC = lhtml.parse("debug_rb_xinyu_list.html").getroot()
xinyu = c._parse_list_cards()
print("\n新誉页卡片数:", len(xinyu), [x["公司名"] for x in xinyu][:4])
assert xinyu, "新誉列表页未解析到卡片"
xy = c._match_card(xinyu, "新誉轨道交通科技有限公司")
assert xy is not None and xy["公司名"] == "新誉轨道交通科技有限公司", xy
assert xy["统一社会信用代码"] == "91320412755875315A", xy
assert xy["电话"] == "13815037273" and xy["邮箱"] == "nug@newunited.com.cn", xy
assert xy["法定代表人"] == "周灵" and "50000.0万" in xy["注册资本"], xy
assert xy["状态"] == "在营" and xy["官网"] == "www.newunitedrt.com", xy
print("新誉目标卡片:", xy)

# 额度墙判据
WALL = ("今日查询额度已用完，明日0点自动恢复50次查询额度，"
        "如需立即查询，请充值获取更多查询额度>>")
assert c._is_quota_wall(WALL) is True
assert c._is_quota_wall("深圳市腾讯计算机系统有限公司 在营 法定代表人：马化腾") is False

# 列表页合并 + 兜底判定
r = c._blank_result("深圳市腾讯计算机系统有限公司")
c._merge_card(r, t)
assert r["数据来源"] == "风鸟" and r["采集路径"] == "列表页"
assert r["法定代表人"] == "马化腾"

empty = c._blank_result("X有限公司")
assert empty["状态"] == ""

# 频率限流与额度墙必须分开判（两者处置方式完全不同）
assert c._is_throttled("您的操作过于频繁，请稍后再试") is True
assert c._is_throttled("为您找到 0 条相关结果") is False
assert c._is_throttled("深圳市腾讯计算机系统有限公司 在营") is False
assert c._is_quota_wall("您的操作过于频繁，请稍后再试") is False
assert "为您找到 0" in RiskBirdCrawlerV2.EMPTY_RESULT_KEYS

# 退避档位递增且封顶 90 秒，成功后逐步回到正常节奏
c._throttle_hits, c._backoff = 0, 0
seq = []
for _ in range(6):
    c._note_throttle()
    seq.append(c._backoff)
assert seq == [10, 25, 45, 90, 90, 90], seq
c._note_success()
assert c._backoff == 80 and c._throttle_hits == 0

# ── 三层平台限制必须分开判：详情额度墙 / 模糊查询额度墙 / 临时频率限流 ──
# 文案取自实机撞墙页 debug_rb_fuzzy_wall.html
FUZZY_WALL = ("查公司 查询 导出记录 筛选条件 重置筛选 为您找到 0 条相关结果 访问已达上限 "
              "提示 您今日模糊查询次数已达上限，如有疑问，请通过意见反馈与我们取得联系")
assert c._is_fuzzy_wall(FUZZY_WALL) is True
assert c._is_fuzzy_wall("深圳市腾讯计算机系统有限公司 在营") is False
assert c._is_quota_wall(FUZZY_WALL) is False        # 模糊查询墙不是详情额度墙
assert c._is_throttled(FUZZY_WALL) is False         # 也不是几分钟就解除的频率限流
assert c._is_fuzzy_wall(WALL) is False              # 详情额度墙不能误判成模糊查询墙

# ── 脏文档判定：driver.get 未落地时界面还停在上一家，绝不能读它的卡片 ──
from pathlib import Path
from urllib.parse import quote as _q

TARGET = "新誉轨道交通科技有限公司"


def _listu(kw):
    return "https://www.riskbird.com/search/company?keyword=" + _q(kw) + "&timestamp=1"


class Nav:
    """只带地址栏的驱动桩（find_elements 返回空，模拟“还没有渲染出卡片”）"""

    payload = None

    def __init__(self, url):
        self.current_url = url
        self.got = []

    def get(self, u):
        self.got.append(u)

    def find_elements(self, _b, xp):
        return []

    def execute_async_script(self, script, api, key):
        return "200|" + json_dumps(self.payload)


import json as _json


def json_dumps(obj):
    return _json.dumps(obj, ensure_ascii=False)


c.driver = Nav(_listu("苏州德兰智能装备有限公司"))
assert c._nav_is_stale(TARGET) is True          # 地址栏还是上一家 → 判脏
assert c._short_url().startswith("keyword=苏州德兰")
c.driver = Nav(_listu(TARGET))
assert c._nav_is_stale(TARGET) is False         # 地址栏就是本次目标 → 放行
c.driver = Nav("https://www.riskbird.com/")
assert c._nav_is_stale(TARGET) is True          # 根本没到搜索页
c.driver = Nav("https://www.riskbird.com/search/company?timestamp=1")
assert c._nav_is_stale(TARGET) is False         # 无正向证据时不误杀正常页

# ── 详情页文档身份：entid 不符就不能解析（避免采到别家公司） ──
c.driver = Nav("https://www.riskbird.com/ent/X.html?entid=abc123")
assert c._detail_doc_ok("abc123") is True
assert c._detail_doc_ok("zzz999") is False
c.driver = Nav(_listu(TARGET))
assert c._detail_doc_ok("abc123") is False

# ── 本地缓存：同一家公司重跑不再消耗模糊查询次数 ──
import tempfile as _tf
import time as _time

c.use_cache = True
c._card_cache = {}
c._cache_dirty = False
c._cache_file = Path(_tf.mkdtemp()) / "cache.json"
c._cache_put("深圳市腾讯计算机系统有限公司", dict(t))
assert c._cache_get(" 深圳市腾讯计算机系统有限公司 ") is not None, "归一化后应命中缓存"
c._save_card_cache()
assert c._cache_file.exists()
c._card_cache = {}
c._load_card_cache()
assert c._cache_get("深圳市腾讯计算机系统有限公司") is not None, "重启后应能从磁盘恢复缓存"
c._cache_put("X有限公司", {"公司名": "X有限公司", "状态": "在营"})
assert c._cache_get("X有限公司") is None, "缺联系方式的卡片不得进缓存（否则会跳过详情页补齐）"
c._card_cache["y"] = {"ts": _time.time() - 40 * 86400, "card": {"公司名": "Y"}}
c._cache_dirty = True
c._save_card_cache()
c._card_cache = {}
c._load_card_cache()
assert "y" not in c._card_cache, "超过 TTL 的缓存应被丢弃"

# ── 模糊查询撞墙后的降级出口：补全接口（响应结构取自实机 probe） ──
Nav.payload = {"data": {"list": [
    {"entName": "新誉轨道交通科技有限公司", "ENTNAME": "新誉轨道交通科技有限公司",
     "ENTSTATUS": "在营", "entid": "lZXTespYacJ", "uniscid": "91320412755875315A"},
    {"entName": "新誉轨道交通科技有限公司唐山分公司", "ENTSTATUS": "在营", "entid": "branch"},
]}}
c.driver = Nav(_listu(TARGET))
res = c._blank_result(TARGET)
ok, entid, ename = c._collect_via_suggest(TARGET, res)
assert ok is True and entid == "lZXTespYacJ" and ename == TARGET, (ok, entid, ename)
assert res["统一社会信用代码"] == "91320412755875315A", res["统一社会信用代码"]
assert "模糊查询额度用尽待重试" in res["状态"] and "在营" in res["状态"], res["状态"]
assert res["采集路径"].startswith("补全接口"), res["采集路径"]
assert "entid=lZXTespYacJ" in res["搜索结果URL"], res["搜索结果URL"]
assert not res["电话"] and not res["邮箱"], "补全接口确实给不了联系方式"
assert c.driver.got == [], "已在风鸟同源页，不应再切页（不会出现‘在首页搜索’）"

# 建议名不是目标公司时必须拒写（宁缺勿错）
Nav.payload = {"data": {"list": [
    {"entName": "苏州德星云智能装备有限公司", "ENTSTATUS": "在营", "entid": "wrong"}]}}
DELAN = "苏州德兰智能装备有限公司"
c.driver = Nav(_listu(DELAN))
res2 = c._blank_result(DELAN)
ok2, _, _ = c._collect_via_suggest(DELAN, res2)
assert ok2 is False and res2["公司名"] == DELAN and res2["状态"] == "", res2

# 恢复解析驱动桩，后续如需扩展断言仍可用
print("\n✅ 列表页解析器离线断言全部通过")
