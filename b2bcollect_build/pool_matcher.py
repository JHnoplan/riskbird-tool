# -*- coding: utf-8 -*-
"""pool_matcher.py —— 采购商搜索引擎（本地模糊匹配 + DeepSeek 语义精排）。

搜索流程：
  1. 本地模糊匹配（毫秒级，全量扫描）：
     查询分词 → 对每条记录算分（公司名重叠×0.4 + 主营重叠×0.3
     + 联系方式完整度×0.15 + 数据新鲜度×0.15）→ 返回 top 50
  2. DeepSeek 语义精排（秒级，可选）：
     把 top 50 候选发给 DeepSeek，让 AI 判断供应链相关性 → 返回 top 20 + 理由
  3. 降级：API 失败/无 key 时自动回退到纯本地结果

设计取舍：
  · 中文分词用字符 bigram（不装 jieba，PyInstaller 包体积友好）
  · 英文按空白/标点切分 + 小写
  · DeepSeek prompt 角色设定为「B2B 供应链分析师」，返回结构化 JSON
"""
import json
import os
import re
import time
from datetime import datetime, timedelta
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple

try:
    from data_pool import normalize_company_name
except ImportError:
    def normalize_company_name(name: str) -> str:
        return (name or "").strip().lower()

# 搜索优化器（可选依赖：打包态可能找不到）
try:
    from search_optimizer import get_search_optimizer
    _HAS_OPTIMIZER = True
except ImportError:
    _HAS_OPTIMIZER = False

# 用户反馈存储（可选依赖：用于搜索加权）
try:
    from feedback_store import get_feedback_store
    _HAS_FEEDBACK = True
except ImportError:
    _HAS_FEEDBACK = False

# ── 停用词（中文虚词 + 英文虚词，不参与匹配打分）────────────────────────────

_STOP_WORDS = frozenset(
    "的 了 和 与 或 是 在 有 及 等 中 为 以 对 从 而 到 由 被 把 让 给 着 过 "
    "也 不 没 更 最 很 这 那 其 之 一 上 下 左 右 前 后 里 外 大 小 多 少 些 "
    "个 家 公司 集团 企业 工厂 制造 科技 技术 实业 贸易 商贸 国际".split()
)

_EN_STOP = frozenset(
    "the a an and or of in to for is are was were be been being "
    "this that these those it its company co ltd inc corp".split()
)

# ── 分词器 ────────────────────────────────────────────────────────────────────

# 中文字符（含中文标点过滤）
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
# 英文单词
_EN_WORD_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9\-]{1,}")
# 数字
_NUM_RE = re.compile(r"\d+")

# ── 搜索意图扩展：行业同义词 + 地理同义词 + 多义词消歧 ────────────────────

INDUSTRY_SYNONYMS = {
    # ── 新能源与环保 ──
    "光伏": ["太阳能", "solar", "光伏组件", "逆变器", "储能", "电池片", "硅片", "光伏支架", "光伏电缆"],
    "风电": ["风能", "wind", "风力发电", "风机", "风电叶片", "风力塔筒"],
    "新能源汽车": ["电动车", "EV", "electric vehicle", "充电桩", "动力电池", "锂电", "汽车电子"],
    "储能": ["储能电池", "蓄电池", "UPS", "不间断电源", "电化学储能", "压缩空气储能"],
    "环保": ["环保设备", "污水处理", "废气处理", "除尘", "脱硫脱硝", "净水"],
    # ── 电子与半导体 ──
    "半导体": ["芯片", "chip", "semiconductor", "集成电路", "IC", "晶圆", "封测"],
    "电子": ["电子元器件", "电容", "电阻", "二极管", "PCB", "电路板", "连接器", "传感器"],
    "LED": ["照明", "lighting", "显示屏", "灯珠", "光源", "亮化"],
    "通信": ["5G", "光纤", "通信设备", "基站", "路由器", "交换机", "天线"],
    # ── 机械与工业 ──
    "机械": ["机械设备", "机床", "CNC", "数控", "模具", "自动化设备"],
    "制冷": ["热泵", "空调", "冷库", "冷凝器", "蒸发器", "压缩机", "冷水机", "冷水机组"],
    "液压": ["液压系统", "液压缸", "液压泵", "液压阀", "液压管件"],
    "焊接": ["焊接设备", "焊机", "激光焊接", "电阻焊", "焊接材料", "焊丝"],
    "泵阀": ["水泵", "阀门", "离心泵", "隔膜泵", "球阀", "闸阀", "蝶阀"],
    "仪器仪表": ["仪表", "测量仪器", "检测仪器", "温度计", "压力表", "流量计"],
    # ── 材料与化工 ──
    "化工": ["化工原料", "化学制品", "涂料", "油漆", "胶粘剂", "催化剂"],
    "塑料": ["塑料制品", "塑料薄膜", "塑料管材", "注塑", "吹塑", "PE", "PP", "PVC"],
    "橡胶": ["橡胶制品", "轮胎", "密封件", "橡胶管", "硅胶", "天然橡胶"],
    "钢铁": ["钢材", "钢板", "钢管", "型钢", "不锈钢", "镀锌", "螺纹钢"],
    "有色金属": ["铝材", "铜材", "铝合金", "铜合金", "锌合金", "钛合金"],
    "建材": ["建筑材料", "水泥", "玻璃", "陶瓷", "石材", "砖瓦"],
    "纺织": ["纺织品", "面料", "布料", "纱线", "纤维", "印染"],
    "包装": ["包装材料", "纸箱", "塑料包装", "金属包装", "标签", "印刷"],
    # ── 消费品与轻工 ──
    "卫浴": ["洁具", "陶瓷", "马桶", "淋浴", "水龙头", "浴室柜", "面盆"],
    "家具": ["家居", "沙发", "床垫", "办公家具", "定制家具", "板式家具"],
    "服装": ["服饰", "成衣", "面料", "辅料", "拉链", "纽扣"],
    "食品": ["食品加工", "饮料", "调味品", "乳制品", "休闲食品", "烘焙"],
    "医药": ["制药", "医疗器械", "药品", "中药材", "保健品", "生物制药"],
    "农业": ["农产品", "种子", "化肥", "农药", "农机", "灌溉"],
    # ── 行业通用词 ──
    "制造": ["生产", "加工", "制造", "工厂", "生产厂"],
    "贸易": ["进出口", "外贸", "跨境", "批发", "经销", "代理商"],
    "科技": ["技术", "研发", "高科技", "创新", "技术型"],
}

GEOGRAPHY_SYNONYMS = {
    # ── 国内省份 + 主要城市 ──
    "江苏": ["南京", "苏州", "无锡", "常州", "徐州", "南通", "扬州", "镇江", "泰州", "盐城", "淮安", "连云港", "宿迁"],
    "广东": ["广州", "深圳", "东莞", "佛山", "珠海", "惠州", "中山", "江门", "汕头", "潮州", "肇庆", "韶关"],
    "浙江": ["杭州", "宁波", "温州", "绍兴", "嘉兴", "金华", "台州", "湖州", "衢州", "丽水", "舟山"],
    "山东": ["济南", "青岛", "烟台", "潍坊", "临沂", "威海", "淄博", "济宁", "泰安", "德州"],
    "上海": ["浦东", "松江", "嘉定", "奉贤", "金山"],
    "北京": ["朝阳", "海淀", "丰台", "通州", "大兴", "昌平"],
    "天津": ["滨海", "武清", "宝坻", "静海"],
    "福建": ["福州", "厦门", "泉州", "漳州", "莆田", "龙岩"],
    "河北": ["石家庄", "唐山", "保定", "廊坊", "沧州", "邯郸", "邢台"],
    "河南": ["郑州", "洛阳", "新乡", "南阳", "许昌", "焦作"],
    "湖北": ["武汉", "宜昌", "襄阳", "荆州", "黄冈", "十堰"],
    "湖南": ["长沙", "株洲", "湘潭", "衡阳", "常德", "岳阳"],
    "安徽": ["合肥", "芜湖", "蚌埠", "马鞍山", "安庆", "滁州"],
    "四川": ["成都", "绵阳", "德阳", "宜宾", "泸州", "南充"],
    "重庆": ["渝北", "江北", "沙坪坝", "九龙坡", "巴南"],
    "辽宁": ["沈阳", "大连", "鞍山", "营口", "丹东"],
    "吉林": ["长春", "吉林", "四平"],
    "黑龙江": ["哈尔滨", "大庆", "齐齐哈尔"],
    "江西": ["南昌", "赣州", "九江", "景德镇", "上饶"],
    "陕西": ["西安", "宝鸡", "咸阳", "渭南"],
    "山西": ["太原", "大同", "长治", "临汾"],
    "广西": ["南宁", "柳州", "桂林", "梧州"],
    "云南": ["昆明", "曲靖", "大理", "玉溪"],
    "贵州": ["贵阳", "遵义", "六盘水"],
    "甘肃": ["兰州", "天水", "酒泉"],
    "内蒙古": ["呼和浩特", "包头", "鄂尔多斯"],
    "新疆": ["乌鲁木齐", "克拉玛依"],
    "海南": ["海口", "三亚"],
    "宁夏": ["银川"],
    "青海": ["西宁"],
    "西藏": ["拉萨"],
    # ── 海外 B2B 高频国家/地区 ──
    "美国": ["USA", "United States", "America", "加州", "德州", "纽约", "佛罗里达", "伊利诺伊"],
    "德国": ["Germany", "Deutschland", "慕尼黑", "柏林", "汉堡", "斯图加特"],
    "日本": ["Japan", "东京", "大阪", "名古屋", "横滨"],
    "韩国": ["Korea", "首尔", "釜山", "仁川"],
    "印度": ["India", "孟买", "德里", "班加罗尔", "金奈"],
    "越南": ["Vietnam", "胡志明", "河内", "海防"],
    "泰国": ["Thailand", "曼谷", "清迈"],
    "马来西亚": ["Malaysia", "吉隆坡", "槟城"],
    "印尼": ["Indonesia", "雅加达", "泗水"],
    "菲律宾": ["Philippines", "马尼拉"],
    "巴西": ["Brazil", "圣保罗", "里约"],
    "墨西哥": ["Mexico", "墨西哥城"],
    "英国": ["UK", "United Kingdom", "伦敦", "伯明翰"],
    "法国": ["France", "巴黎", "里昂"],
    "意大利": ["Italy", "米兰", "罗马"],
    "西班牙": ["Spain", "马德里", "巴塞罗那"],
    "荷兰": ["Netherlands", "阿姆斯特丹", "鹿特丹"],
    "波兰": ["Poland", "华沙"],
    "土耳其": ["Turkey", "伊斯坦布尔"],
    "俄罗斯": ["Russia", "莫斯科", "圣彼得堡"],
    "沙特": ["Saudi", "利雅得", "吉达"],
    "阿联酋": ["UAE", "迪拜", "阿布扎比"],
    "澳大利亚": ["Australia", "悉尼", "墨尔本"],
    "加拿大": ["Canada", "多伦多", "温哥华"],
}

# ── 多义词消歧表 ──────────────────────────────────────────────────────────
# key = 容易有歧义的搜索词，value = [(消歧选项, 对应注入的关键词)]
_AMBIGUOUS_TERMS = {
    "苹果": [("水果/农业", "水果 苹果 果园 农业 种植"), ("科技/电子", "苹果 Apple 手机 电脑 数码")],
    "钻石": [("珠宝/饰品", "钻石 珠宝 首饰 饰品 钻戒"), ("工业/切割", "钻石 磨料 切割 砂轮 钻头")],
    "捷豹": [("汽车/配件", "捷豹 汽车 配件 整车"), ("包装机械", "捷豹 打包机 包装机")],
    "长城": [("汽车/零部件", "长城 汽车 零部件 SUV"), ("建材/装饰", "长城 建材 装饰 板材")],
    "凤凰": [("自行车/电动车", "凤凰 自行车 电动车 两轮"), ("光伏/新能源", "凤凰 光伏 新能源")],
    "熊猫": [("食品/零食", "熊猫 食品 零食 饼干"), ("电子/音响", "熊猫 音响 电子 功放")],
    "海鸥": [("手表/钟表", "海鸥 手表 钟表 机芯"), ("光伏/能源", "海鸥 光伏 太阳能")],
    "骆驼": [("电池/蓄电池", "骆驼 电池 蓄电池 电瓶"), ("服装/户外", "骆驼 服装 户外 冲锋衣")],
    "三叶": [("机械制造", "三叶 机械 制造 设备"), ("制药/化工", "三叶 制药 化工")],
    "光明": [("食品/乳业", "光明 食品 乳业 牛奶"), ("电气/照明", "光明 电气 照明 LED")],
}


def detect_ambiguity(query: str) -> Optional[List[tuple]]:
    """检测查询是否命中多义词，返回消歧选项列表或 None。

    Returns:
        [("水果/农业", "水果 苹果 果园"), ("科技/电子", "苹果 Apple 手机")]
        或 None（无歧义）
    """
    q = query.strip()
    for term, options in _AMBIGUOUS_TERMS.items():
        if term in q:
            return options
    return None


def detect_industry(query: str) -> Optional[str]:
    """检测查询最可能属于哪个行业，返回行业名或 None。

    策略：查询词（或其子串）命中某个行业同义词 key 或其 synonym，
    取匹配最长的那个行业（越长越精确）。
    """
    q = query.strip().lower()
    best_match = None
    best_len = 0
    for industry, synonyms in INDUSTRY_SYNONYMS.items():
        candidates = [industry.lower()] + [s.lower() for s in synonyms]
        for c in candidates:
            if c in q and len(c) > best_len:
                best_len = len(c)
                best_match = industry
    return best_match


def expand_query(query: str) -> List[str]:
    """扩展搜索词（行业同义词 + 地理同义词）。
    
    Args:
        query: 原始搜索词，如 "光伏"
    
    Returns:
        扩展后的搜索词列表，如 ["光伏", "太阳能", "solar", "光伏组件", ...]
        去重且保留原始 query
    """
    expanded = [query]
    query_lower = query.lower()
    
    # 行业同义词扩展
    for key, synonyms in INDUSTRY_SYNONYMS.items():
        if key.lower() in query_lower or any(s.lower() in query_lower for s in synonyms):
            expanded.extend(synonyms)
    
    # 地理同义词扩展
    for key, synonyms in GEOGRAPHY_SYNONYMS.items():
        if key.lower() in query_lower or any(s.lower() in query_lower for s in synonyms):
            expanded.extend(synonyms)
    
    # 去重保序
    seen = set()
    result = []
    for term in expanded:
        term_lower = term.lower()
        if term_lower not in seen:
            seen.add(term_lower)
            result.append(term)
    
    return result


def tokenize(text: str) -> List[str]:
    """把混合文本切分为 token 列表（中文 bigram + 英文词 + 数字段）。

    示例：
      "光伏组件制造商" → ["光伏", "伏组", "组件", "件制", "制造", "造商"]
      "Solar Panel Co." → ["solar", "panel"]
      "13800138000" → ["13800138000"]
    """
    if not text:
        return []
    tokens: List[str] = []
    text_lower = text.lower()

    # 1. 提取英文单词（先于中文，避免中英文混杂）
    en_words = _EN_WORD_RE.findall(text_lower)
    for w in en_words:
        if w not in _EN_STOP and len(w) >= 2:
            tokens.append(w)

    # 2. 提取数字段（手机号、区号等）
    numbers = _NUM_RE.findall(text)
    for n in numbers:
        if len(n) >= 4:  # 只保留有意义的长数字
            tokens.append(n)

    # 3. 中文 bigram（相邻两字一组，覆盖词组匹配）
    cjk_chars = _CJK_RE.findall(text)
    for i in range(len(cjk_chars) - 1):
        bigram = cjk_chars[i] + cjk_chars[i + 1]
        if bigram not in _STOP_WORDS:
            tokens.append(bigram)
    # 单字也加入（匹配短关键词）
    for ch in cjk_chars:
        if ch not in _STOP_WORDS:
            tokens.append(ch)

    return tokens


def _jaccard(tokens_a: List[str], tokens_b: List[str]) -> float:
    """两组 token 的 Jaccard 相似度（0~1）。"""
    if not tokens_a or not tokens_b:
        return 0.0
    set_a = set(tokens_a)
    set_b = set(tokens_b)
    inter = len(set_a & set_b)
    union = len(set_a | set_b)
    return inter / union if union else 0.0


def _token_overlap(query_tokens: List[str], target_tokens: List[str]) -> float:
    """查询 token 在目标中的命中率（0~1）。

    比 Jaccard 更适合搜索场景：查询很短但命中了关键 token 就应该高分。
    """
    if not query_tokens:
        return 0.0
    target_set = set(target_tokens)
    hits = sum(1 for t in query_tokens if t in target_set)
    return hits / len(query_tokens)


# ── 记录级判据（口径唯一实现）──────────────────────────────────────────────
# 筛选面板的四类勾选、企业规模、数据新鲜度同时被两处消费：
#   1) CompanyMatcher.apply_filters —— 内存列表上的 Python 过滤
#   2) pool_store —— 物化视图里预算成整数列，查询时下压成 SQL WHERE
# 判定规则只写在这里一份；物化视图只存「这些函数的结果」，
# 任何一侧改规则，另一侧自动同口径（离线回归有等价性断言兜底）。

_TIME_FORMATS = ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d")

FLAG_MOBILE = 1   # 有手机（mobiles 或 phones——爬虫常把手机写进电话列）
FLAG_EMAIL = 2
FLAG_WECHAT = 4
FLAG_PHONE = 8    # 有固话（phones 或 landlines）

_SCALE_TOKENS = {
    "large": ("集团", "股份", "控股", "国际"),
    "medium": ("有限公司",),
    "small": ("厂", "个体", "工作室", "店"),
}
SCALE_LARGE = 1
SCALE_MEDIUM = 2
SCALE_SMALL = 4
_SCALE_BIT = {"large": SCALE_LARGE, "medium": SCALE_MEDIUM, "small": SCALE_SMALL}


@lru_cache(maxsize=8192)
def _parse_time_cached(text: str) -> Optional[datetime]:
    """按字面量缓存解析结果：同一批上传的 source_time 只有几个不同值，命中率接近 100%。

    strptime 是重建/过滤里最贵的单点（实测 40μs/条），缓存后基本从成本表里消失。
    返回的 datetime 在所有调用点都只读（比较、取 .timestamp()），共享对象安全。
    """
    for fmt in _TIME_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def parse_source_time(time_str: str) -> Optional[datetime]:
    """解析 source_time（三种历史格式），无法解析返回 None。"""
    if not time_str:
        return None
    return _parse_time_cached(str(time_str)[:19])


def contact_flags(rec: dict) -> int:
    """联系方式存在性位标记（与筛选面板四项勾选同源）。"""
    bits = 0
    if rec.get("mobiles") or rec.get("phones"):
        bits |= FLAG_MOBILE
    if rec.get("emails"):
        bits |= FLAG_EMAIL
    if rec.get("wechats"):
        bits |= FLAG_WECHAT
    if rec.get("phones") or rec.get("landlines"):
        bits |= FLAG_PHONE
    return bits


def scale_bits(company: str) -> int:
    """企业规模位标记：按公司名形态启发，一条记录可同时命中多档。"""
    text = str(company or "")
    bits = 0
    for name, bit in (("large", SCALE_LARGE), ("medium", SCALE_MEDIUM),
                      ("small", SCALE_SMALL)):
        if any(tok in text for tok in _SCALE_TOKENS[name]):
            bits |= bit
    return bits


def source_ts(time_str: str) -> float:
    """source_time 的 Unix 秒（解析不出给 0）。

    物化视图存这个值，查询时按 cutoff 下压。与 Python 侧
    `(now - dt).days <= freshness_days` 严格等价：天数差按整日截断，
    故 cutoff = now - (freshness_days + 1) * 86400 且取开区间。
    """
    dt = parse_source_time(time_str)
    if dt is None:
        return 0.0
    try:
        return dt.timestamp()
    except (OSError, OverflowError, ValueError):
        return 0.0


def search_fields(rec: dict) -> Tuple[str, str, str]:
    """记录的可检索文本三段：(公司名, 主营/行业, 其它字段)。

    口径唯一实现——倒排索引、SQLite FTS5 的三个列、文本相关性判据全部
    从这里取字段。任何一侧自行增删字段都会造成「索引说命中、评分说没命中」
    的静默漏召回，这类缺陷比崩溃更难发现。
    """
    name = str(rec.get("company") or "")
    prod = " ".join([str(rec.get("products") or ""), str(rec.get("industry") or "")])
    parts = [str(rec.get("address") or ""), str(rec.get("country") or ""),
             str(rec.get("website") or ""), str(rec.get("source") or ""),
             str(rec.get("title") or "")]
    for key in ("mobiles", "phones", "landlines", "emails", "wechats"):
        vals = rec.get(key)
        if vals:
            parts.append(" ".join(str(v) for v in vals))
    for c in (rec.get("contacts") or []):
        if isinstance(c, dict):
            parts.append(str(c.get("name") or ""))
    return name, prod, " ".join(p for p in parts if p)


def text_hit(query_tokens: List[str], rec: dict) -> bool:
    """查询 token 是否在记录可检索字段里出现（公司名/主营已在评分里算过，这里只补其它字段）。"""
    if not query_tokens:
        return False
    _name, _prod, other = search_fields(rec)
    if not other:
        return False
    other_set = set(tokenize(other))
    return any(t in other_set for t in query_tokens)


# ── 搜索引擎 ──────────────────────────────────────────────────────────────────

class CompanyMatcher:
    """采购商搜索引擎：本地模糊 + DeepSeek 语义。"""

    # 评分权重
    W_NAME = 0.40       # 公司名匹配
    W_PRODUCT = 0.30    # 主营产品匹配
    W_CONTACT = 0.15    # 联系方式完整度
    W_FRESH = 0.15      # 数据新鲜度

    def __init__(self, deepseek_api_key: str = ""):
        self._api_key = deepseek_api_key or self._load_api_key()

    @staticmethod
    def _load_api_key() -> str:
        """复用 deepseek_expander 的 config.json 读取逻辑。"""
        try:
            from deepseek_expander import load_api_key
            return load_api_key() or ""
        except ImportError:
            pass
        # 兜底直接读 config.json
        cfg_path = os.path.join(os.path.dirname(__file__), "config.json")
        try:
            cfg = json.load(open(cfg_path, encoding="utf-8"))
            return (cfg.get("deepseek_api_key") or "").strip()
        except Exception:
            return ""

    # ── 本地模糊匹配 ──

    def search_local(self, query: str, master: List[dict],
                     top_k: int = 50, progress_cb=None) -> List[dict]:
        """本地模糊搜索：对 master 全量扫描，按综合评分降序返回 top_k。

        每条结果附加 _score / _score_detail 字段供展示。
        progress_cb: 可选回调，用于向前端发送思考步骤。
        """
        if not query or not master:
            return []

        if progress_cb:
            progress_cb(f"💭 用户输入了「{query}」，我来分析一下...")

        # 搜索意图扩展：把同义词合并到 query_tokens
        expanded_terms = expand_query(query)
        if progress_cb and len(expanded_terms) > 1:
            progress_cb(f"")
            progress_cb(f"🔍 这个词可能和以下相关：")
            # 展示同义词扩展（最多显示 5 个）
            synonyms_display = expanded_terms[1:6]
            for syn in synonyms_display:
                progress_cb(f"   • {syn}")
            if len(expanded_terms) > 6:
                progress_cb(f"   • ...等 {len(expanded_terms)-1} 个相关词")

        query_tokens = []
        for term in expanded_terms:
            query_tokens.extend(tokenize(term))
        tokens_before = len(query_tokens)
        query_tokens = list(set(query_tokens))  # 去重
        
        if not query_tokens:
            return []

        if progress_cb:
            progress_cb(f"")
            progress_cb(f"📊 我会从这几个维度评估：")
            progress_cb(f"   • 公司名是否匹配（权重 40%）")
            progress_cb(f"   • 主营产品是否相关（权重 30%）")
            progress_cb(f"   • 联系方式是否完整（权重 15%）")
            progress_cb(f"   • 数据是否最新（权重 15%）")
            progress_cb(f"")
            progress_cb(f"🔎 正在 {len(master)} 家公司中筛选...")

        now = datetime.now()
        scored: List[Tuple[float, dict]] = []

        for rec in master:
            # 公司名 token
            company_tokens = tokenize(rec.get("company", ""))
            # 主营产品 token
            product_tokens = tokenize(rec.get("products", ""))

            # 1. 公司名匹配分
            name_score = max(
                _token_overlap(query_tokens, company_tokens),
                _jaccard(query_tokens, company_tokens),
            )

            # 2. 主营产品匹配分
            product_score = max(
                _token_overlap(query_tokens, product_tokens),
                _jaccard(query_tokens, product_tokens) * 0.8,
            )

            # 3. 联系方式完整度（有手机+邮箱+微信 = 满分）
            has_mobile = bool(rec.get("mobiles"))
            has_phone = bool(rec.get("phones"))
            has_email = bool(rec.get("emails"))
            has_wechat = bool(rec.get("wechats"))
            contact_score = sum([has_mobile, has_phone, has_email, has_wechat]) / 4.0

            # 4. 数据新鲜度（30天内满分，线性衰减到180天）
            fresh_score = self._calc_freshness(rec.get("source_time", ""), now)

            # 综合分
            total = (
                name_score * self.W_NAME
                + product_score * self.W_PRODUCT
                + contact_score * self.W_CONTACT
                + fresh_score * self.W_FRESH
            )

            # 5. 用户反馈加权（可选）
            company_name = rec.get("company", "")
            if _HAS_FEEDBACK and total > 0:
                fb_store = get_feedback_store()
                total = fb_store.apply_feedback_weight(company_name, total)

            # 6. 文本相关性下限：全字段都没命中查询 token 的记录不算结果。
            #    历史缺陷：门槛只有 total > 0.05，而联系方式完整度 + 数据新鲜度
            #    两项合计权重 0.3——任何一条资料齐全的公司都能靠它俩越过门槛，
            #    于是搜任何词都返回一屏不相干公司（用户看到的"结果很噪声"）。
            if (name_score <= 0.0 and product_score <= 0.0
                    and not text_hit(query_tokens, rec)):
                continue

            if total > 0.05:  # 过滤极低分（几乎不相关）
                result = dict(rec)
                result["_score"] = round(total, 4)
                result["_score_detail"] = {
                    "name": round(name_score, 3),
                    "product": round(product_score, 3),
                    "contact": round(contact_score, 3),
                    "fresh": round(fresh_score, 3),
                }
                scored.append((total, result))

        # 按分数降序
        scored.sort(key=lambda x: x[0], reverse=True)
        results = [item for _, item in scored[:top_k]]
        if progress_cb:
            progress_cb(f"")
            progress_cb(f"✅ 找到了 {len(results)} 家最相关的公司")
            # 展示前 3 名的匹配原因
            if results:
                progress_cb(f"")
                progress_cb(f"🏆 相关度最高的 {min(3, len(results))} 家：")
                for i, r in enumerate(results[:3], 1):
                    company = r.get("company", "")[:25]
                    score = r.get("_score", 0)
                    detail = r.get("_score_detail", {})
                    progress_cb(f"   {i}. {company} (相关度 {score:.0%})")
                    reasons = []
                    if detail.get("name", 0) > 0.3:
                        reasons.append("公司名匹配")
                    if detail.get("product", 0) > 0.3:
                        reasons.append("主营匹配")
                    if detail.get("contact", 0) > 0.5:
                        reasons.append("联系方式完整")
                    if detail.get("fresh", 0) > 0.5:
                        reasons.append("数据较新")
                    if reasons:
                        progress_cb(f"      → {', '.join(reasons)}")
        return results

    @staticmethod
    def _calc_freshness(time_str: str, now: datetime) -> float:
        """数据新鲜度评分（0~1）。30天内=1.0，180天=0.0，线性衰减。"""
        dt = parse_source_time(time_str)
        if dt is None:
            return 0.3    # 无时间信息或格式不认识：给基础分
        days = (now - dt).days
        if days <= 30:
            return 1.0
        elif days >= 180:
            return 0.0
        return 1.0 - (days - 30) / 150.0

    # ── 高级筛选与排序 ──

    @staticmethod
    def apply_filters(results: List[dict], filters: dict) -> List[dict]:
        """对搜索结果应用多维筛选条件。

        filters 支持的键：
          - regions: List[str]    国内外多选（"dom"/"intl"，空/缺失=不限）
          - region: str           地区筛选（按 province 分区键等值，与物化视图
                                  pool_store._where 同一条判据：英文地址派生出
                                  「浙江」的记录，两侧都必须能按浙江筛到）
          - has_mobile: bool      必须有手机号
          - has_email: bool       必须有邮箱
          - has_wechat: bool      必须有微信
          - has_phone: bool       必须有固话
          - has_contact: bool     至少有一种联系方式（手机/固话/邮箱/微信任一）
          - freshness_days: int   数据新鲜度（N 天内，0=全部）
          - scale: str            企业规模（"large"/"medium"/"small"/""）
          - now: datetime         基准时刻（默认当前时间；物化视图 SQL 下压与
                                  Python 过滤做等价性断言时两侧必须同一时刻）
        """
        if not filters:
            return results
        filtered = []
        # "now" 只用于把两侧（SQL 谓词与 Python 判定）钉在同一时刻做对账，
        # 界面从不传它，日常行为与既往完全一致
        now = filters.get("now") or datetime.now()
        # 地区筛选的判据实现只有 ingest_cleaning.partition_province 一份（与
        # 物化视图 province 列同源）；这里只负责在依赖缺失时降级回旧的宽口径
        _region_wanted = str(filters.get("region") or "")
        _partition_province = None
        if _region_wanted and _region_wanted != "全部":
            try:
                from ingest_cleaning import partition_province as _pp
                _partition_province = _pp
            except Exception:
                _partition_province = None
        # 国内外多选（与搜索前口径一致：优先 is_domestic 显式值，
        # 存量缺失时按多信号推导兜底）
        regions_sel = [r for r in (filters.get("regions") or []) if r in ("dom", "intl")]
        _rec_region = None
        if regions_sel and len(regions_sel) < 2:
            try:
                from data_pool import record_region
                _rec_region = record_region
            except Exception:
                _rec_region = None

        def _region_of(rec: dict) -> str:
            if _rec_region is not None:
                return _rec_region(rec)
            v = rec.get("is_domestic")
            if v is None:
                country = str(rec.get("country") or "").strip()
                if country and country not in ("中国", "中国大陆"):
                    return "intl"
                addr = str(rec.get("address") or "")
                if any(p in addr for p in ("江苏", "广东", "浙江", "山东", "上海",
                                            "北京", "福建", "河北", "四川", "安徽")):
                    return "dom"
                v = True   # 零信号默认国内（与写侧口径一致）
            return "dom" if v else "intl"

        for rec in results:
            # 国内外多选筛选（单选时才过滤；全选等于不限，免循环判断）
            if regions_sel and len(regions_sel) == 1 and _region_of(rec) != regions_sel[0]:
                continue
            # 地区筛选（按 province 分区键等值，与 pool_store._where 同一条判据）
            if _region_wanted and _region_wanted != "全部":
                if _partition_province is not None:
                    if _partition_province(rec) != _region_wanted:
                        continue
                else:
                    # 降级（打包态清洗层导入失败）才用旧的地址子串：口径变宽，
                    # 但绝不改变「国内外/联系方式/新鲜度」这些其它判据
                    addr = (str(rec.get("address") or "") + " "
                            + str(rec.get("country") or "")).lower()
                    if _region_wanted.lower() not in addr:
                        continue
            # 联系方式筛选（判据与物化视图下压列同源：contact_flags）
            want = 0
            if filters.get("has_mobile"):
                want |= FLAG_MOBILE
            if filters.get("has_email"):
                want |= FLAG_EMAIL
            if filters.get("has_wechat"):
                want |= FLAG_WECHAT
            if filters.get("has_phone"):
                want |= FLAG_PHONE
            if want and (contact_flags(rec) & want) != want:
                continue
            # 「只看有联系方式」：与物化视图 COALESCE(flags,0) <> 0 同一条判据
            # （两条路径口径不一致时，勾了框的结果会随视图是否就绪而变，最难排查）
            if filters.get("has_contact") and not contact_flags(rec):
                continue
            # 数据新鲜度（N 天内；无法解析或无时间信息的记录一律排除）
            freshness_days = filters.get("freshness_days", 0)
            if freshness_days > 0:
                dt = parse_source_time(rec.get("source_time", ""))
                if dt is None or (now - dt).days > freshness_days:
                    continue
            # 企业规模（判据与物化视图下压列同源：scale_bits）
            scale = filters.get("scale", "")
            if scale and scale != "全部":
                bit = _SCALE_BIT.get(scale)
                if bit and not (scale_bits(rec.get("company", "")) & bit):
                    continue
            filtered.append(rec)
        return filtered

    @staticmethod
    def apply_sort(results: List[dict], sort_key: str) -> List[dict]:
        """对搜索结果应用排序。

        sort_key:
          - "score"       相关度降序（默认）
          - "name_az"     公司名 A-Z
          - "fresh"       数据新鲜度降序
          - "contact"     联系方式完整度降序
        """
        if not sort_key or sort_key == "score":
            return sorted(results, key=lambda r: r.get("_score", 0), reverse=True)
        if sort_key == "name_az":
            return sorted(results, key=lambda r: r.get("company", "").lower())
        if sort_key == "fresh":
            def _fresh_key(r):
                ts = r.get("source_time", "")
                if not ts:
                    return ""
                return ts
            return sorted(results, key=_fresh_key, reverse=True)
        if sort_key == "contact":
            def _contact_key(r):
                return (len(r.get("mobiles", [])) + len(r.get("emails", []))
                        + len(r.get("wechats", [])) + len(r.get("phones", [])))
            return sorted(results, key=_contact_key, reverse=True)
        return results

    # ── 倒排索引加速搜索 ──

    def search_with_index(self, query: str, master: List[dict],
                          candidate_indices: List[int],
                          top_k: int = 50, progress_cb=None) -> List[dict]:
        """用倒排索引候选集做评分搜索（只评候选，不评全量）。

        candidate_indices: 由 DataPoolManager.search_by_index() 返回的候选索引
        progress_cb: 可选回调，用于向前端发送思考步骤。
        """
        if not query or not master or not candidate_indices:
            return []

        if progress_cb:
            progress_cb(f"⚡ 先按关键词挑出 {len(candidate_indices)} 家相关的公司，"
                        f"再逐家算匹配度")

        # 搜索意图扩展：把同义词合并到 query_tokens
        expanded_terms = expand_query(query)
        query_tokens = []
        for term in expanded_terms:
            query_tokens.extend(tokenize(term))
        query_tokens = list(set(query_tokens))  # 去重
        
        if not query_tokens:
            return []

        # 使用优化器的令牌缓存（如果可用）
        optimizer = get_search_optimizer() if _HAS_OPTIMIZER else None
        if optimizer:
            # 绑定当前总表：换库时自动作废令牌缓存（本模块不建任何倒排）
            optimizer.initialize(master, tokenize)

        now = datetime.now()
        scored: List[Tuple[float, dict]] = []

        for idx in candidate_indices:
            if idx >= len(master):
                continue
            rec = master[idx]
            
            # 使用缓存分词（如果有优化器）
            if optimizer:
                company_tokens = optimizer.get_tokens(rec.get("company", ""), tokenize)
                product_tokens = optimizer.get_tokens(rec.get("products", ""), tokenize)
            else:
                company_tokens = tokenize(rec.get("company", ""))
                product_tokens = tokenize(rec.get("products", ""))

            name_score = max(
                _token_overlap(query_tokens, company_tokens),
                _jaccard(query_tokens, company_tokens),
            )
            product_score = max(
                _token_overlap(query_tokens, product_tokens),
                _jaccard(query_tokens, product_tokens) * 0.8,
            )

            has_mobile = bool(rec.get("mobiles"))
            has_phone = bool(rec.get("phones"))
            has_email = bool(rec.get("emails"))
            has_wechat = bool(rec.get("wechats"))
            contact_score = sum([has_mobile, has_phone, has_email, has_wechat]) / 4.0
            fresh_score = self._calc_freshness(rec.get("source_time", ""), now)

            total = (
                name_score * self.W_NAME
                + product_score * self.W_PRODUCT
                + contact_score * self.W_CONTACT
                + fresh_score * self.W_FRESH
            )

            # 用户反馈加权（可选）
            company_name = rec.get("company", "")
            if _HAS_FEEDBACK and total > 0:
                fb_store = get_feedback_store()
                total = fb_store.apply_feedback_weight(company_name, total)

            if total > 0.05:
                result = dict(rec)
                result["_score"] = round(total, 4)
                result["_score_detail"] = {
                    "name": round(name_score, 3),
                    "product": round(product_score, 3),
                    "contact": round(contact_score, 3),
                    "fresh": round(fresh_score, 3),
                }
                scored.append((total, result))

        scored.sort(key=lambda x: x[0], reverse=True)
        results = [item for _, item in scored[:top_k]]
        if progress_cb:
            progress_cb(f"✅ 筛选完成，找到 {len(results)} 家最相关的公司")
        return results

    # ── 公司名检测 + 智能匹配 ──

    @staticmethod
    def detect_company_name(query: str, master: List[dict],
                            store=None) -> Optional[dict]:
        """检测查询是否为公司名，如果是则返回匹配的公司记录。

        检测策略（优先级从高到低）：
        1. 精确匹配（去后缀后）
        2. 前缀匹配（查询是某个公司名的前缀，规范化后长度 >=3）
        3. 包含匹配（查询包含在公司名中且长度 >=4）

        百万级：传 store（pool_store.PoolStore）时走索引点查，三次全表扫描
        变成 O(log n)；store 查不到（数据版本不一致/库里没有）仍回退扫描，
        绝不因为物化视图不可用就漏判公司名。
        """
        if not query:
            return None
        q_norm = normalize_company_name(query)
        q_lower = query.strip().lower()
        if not master and store is None:
            return None

        if store is not None:
            try:
                hit = store.find_company(q_norm, q_lower)
            except Exception:
                hit = None
            if hit is not None:
                return hit
            if not master:
                return None

        # 一趟扫描同时记下三种命中的第一名（旧实现是三趟全量扫描）
        best_exact = best_prefix = best_contains = None
        want_contains = len(q_lower) >= 4
        want_prefix = len(q_norm) >= 3
        for rec in master:
            # 规范化后为空（查询本身就是"公司/有限公司"这类通用词）时不做
            # 精确匹配——否则会把任意一条 company_norm 为空的脏记录当成公司名命中
            if q_norm and best_exact is None and rec.get("company_norm") == q_norm:
                best_exact = rec
                break
            cn = rec.get("company_norm") or ""
            if best_prefix is None and want_prefix and cn.startswith(q_norm):
                best_prefix = rec
            if (best_contains is None and want_contains
                    and q_lower in (rec.get("company") or "").lower()):
                best_contains = rec
            if best_prefix is not None and best_contains is not None:
                break

        return best_exact or best_prefix or best_contains

    def search_smart(self, query: str, master: List[dict],
                     candidate_indices: Optional[List[int]] = None,
                     top_k: int = 20,
                     progress_cb=None) -> List[dict]:
        """智能搜索：自动识别查询类型，采用最佳匹配策略。

        流程：
        1. 检测是否为公司名 → 如果是，提取主营/行业，用 AI 分析上下游
        2. 如果是关键词 → 用倒排索引 + 本地评分
        3. DeepSeek 精排（可选）
        """
        if not query or not master:
            return []

        # ── 多义词消歧检查 ──
        ambiguity = detect_ambiguity(query)
        if ambiguity and progress_cb:
            options_text = "、".join(f"「{opt}」" for opt, _ in ambiguity)
            progress_cb(f"⚠️ 这个词可能有多种含义：{options_text}")
            progress_cb(f"💡 我会综合所有含义来搜索，如果结果不准确可以换个更具体的词")

        # ── 行业意图检测 ──
        industry = detect_industry(query)
        if industry and progress_cb:
            progress_cb(f"🏭 识别到行业：{industry}")

        # 检测是否为公司名（在数据库中）
        company_rec = self.detect_company_name(query, master)

        if company_rec:
            # 公司名查询：分析该公司主营，找相关采购商/供应商
            if progress_cb:
                progress_cb(f"🏢 识别到公司名「{company_rec['company']}」")
                progress_cb(f"🔍 正在分析这家公司的产业链关系...")
            return self._search_by_company(company_rec, master, query,
                                           candidate_indices, top_k,
                                           progress_cb)
        else:
            # 检测查询是否看起来像公司名（即使不在数据库中）
            looks_like_company = self._looks_like_company_name(query)
            
            if looks_like_company:
                # 看起来是公司名但不在库中：用 AI 分析这家公司是做什么的
                if progress_cb:
                    progress_cb(f"🏢 这看起来是一家公司名，让我分析一下...")
                # 构造一个合成的公司记录，让 AI 来分析
                synthetic_rec = {
                    "company": query,
                    "products": "",  # 未知，让 AI 分析
                    "address": "",
                }
                return self._search_by_company(synthetic_rec, master, query,
                                               candidate_indices, top_k,
                                               progress_cb)
            else:
                # 关键词查询：倒排索引 + 本地评分
                if progress_cb:
                    progress_cb(f"🔑 这是一个关键词搜索")
                    progress_cb(f"📊 正在评估候选公司的相关度...")
                if candidate_indices:
                    # 非空候选集：只评候选（None/空列表都回退全量，
                    # 避免漏掉未参与建索引的字段）
                    results = self.search_with_index(query, master, candidate_indices, top_k=50,
                                                     progress_cb=progress_cb)
                else:
                    results = self.search_local(query, master, top_k=50,
                                                progress_cb=progress_cb)
                if not results:
                    return []
                # DeepSeek 精排
                return self._deepseek_rerank(query, results, top_k, progress_cb)

    @staticmethod
    def _looks_like_company_name(query: str) -> bool:
        """检测查询是否看起来像公司名（即使不在数据库中）。
        
        判断依据：包含典型的公司名后缀词。
        """
        query = query.strip()
        # 公司名常见后缀
        company_suffixes = [
            "集团", "公司", "厂", "企业", "制造", "工业", "科技", "技术",
            "实业", "贸易", "商贸", "国际", "投资", "控股", "股份",
        ]
        # 如果查询包含这些后缀，且长度合理（3-30字符），就认为像公司名
        if 3 <= len(query) <= 30:
            return any(suffix in query for suffix in company_suffixes)
        return False

    def _search_by_company(self, company_rec: dict, master: List[dict],
                           query: str, candidate_indices: Optional[List[int]],
                           top_k: int, progress_cb) -> List[dict]:
        """按公司名搜索：分析该公司主营，找上下游相关公司。"""
        company_name = company_rec.get("company", "")
        products = company_rec.get("products", "")
        address = company_rec.get("address", "")

        # 行业意图检测：给 AI 额外的行业上下文
        detected_industry = detect_industry(company_name) or detect_industry(query)
        industry_hint = ""
        if detected_industry:
            industry_hint = f"\n行业提示：根据名称判断，这家公司可能属于「{detected_industry}」行业。"

        # 构造 AI prompt：分析该公司需要什么采购商/供应商
        # 关键改进：明确告知 AI 这是中国 B2B 数据库，避免歧义
        prompt = f"""你是一位资深的中国 B2B 供应链分析师。

重要背景：这是一个中国制造业企业数据库，包含大量中国本土的制造业公司。

目标公司：{company_name}
已知主营产品：{products or '未知（请根据公司名称推断）'}
公司地址：{address or '未知'}{industry_hint}

请分析：
1. 这家公司最可能是做什么的？（优先选择中国企业的解释，避免国外品牌歧义）
2. 它的上游供应商通常是哪些类型的公司？
3. 它的下游采购商通常是哪些类型的公司？
4. 请给出 3-5 个**核心产品/行业关键词**，用于在数据库中查找与该公司有业务往来的上下游企业。
   - 关键词要求：只写核心产品词或行业词（如"卫浴""洁具""陶瓷""电缆"），不要带"供应商""经销商""采购"等修饰词
   - 关键词要通用，能在数据库中匹配到相关公司

请返回 JSON 格式：
{{
  "business": "主营业务描述（一句话）",
  "upstream": "上游供应商类型（一句话）",
  "downstream": "下游采购商类型（一句话）",
  "keywords": ["关键词1", "关键词2", "关键词3"]
}}

只返回 JSON，不要其他文字。"""

        # 尝试用 DeepSeek 分析
        ai_analysis = None
        if self._api_key:
            try:
                if progress_cb:
                    progress_cb(f"")
                    progress_cb(f"🤖 正在让 AI 分析这家公司的业务...")
                ai_analysis = self._call_deepseek_json(prompt)
            except Exception:
                pass

        # 用关键词搜索
        keywords = []
        if ai_analysis and isinstance(ai_analysis, dict):
            keywords = ai_analysis.get("keywords", [])
            business = ai_analysis.get("business", "")
            upstream = ai_analysis.get("upstream", "")
            downstream = ai_analysis.get("downstream", "")
            
            if progress_cb:
                progress_cb(f"")
                progress_cb(f"💡 AI 分析结果：")
                progress_cb(f"   这家公司主要是做：{business}")
                progress_cb(f"")
                progress_cb(f"   🔼 它的上游供应商通常是：{upstream}")
                progress_cb(f"   🔽 它的下游采购商通常是：{downstream}")
                progress_cb(f"")
                if keywords:
                    progress_cb(f"   🔑 所以我用这些词帮你找相关公司：")
                    for kw in keywords[:5]:
                        progress_cb(f"      • {kw}")

        # 如果没有 AI 结果，用主营产品作为关键词
        if not keywords and products:
            keywords = [products[:20]]
            if progress_cb:
                progress_cb(f"   🔑 使用主营产品作为关键词：{products[:20]}")
        if not keywords:
            keywords = [query]

        # 用关键词搜索候选
        all_candidates = []
        seen_norms = set()
        
        # 关键改进：过滤不合理的关键词（带"供应商""经销商"等修饰词的）
        filtered_keywords = []
        for kw in keywords:
            # 跳过带修饰词的关键词
            if any(stop in kw for stop in ["供应商", "经销商", "采购", "分销", "代理"]):
                continue
            filtered_keywords.append(kw)
        
        # 如果过滤后没有关键词，用公司名的核心部分
        if not filtered_keywords:
            # 提取公司名的核心词（去掉"集团""公司"等后缀）
            core_name = company_name
            for suffix in ["集团", "公司", "厂", "企业", "股份", "控股"]:
                core_name = core_name.replace(suffix, "")
            if len(core_name) >= 2:
                filtered_keywords = [core_name[:10]]  # 取前10个字符
        
        if progress_cb and filtered_keywords != keywords:
            progress_cb(f"   💡 已优化关键词（去掉了带修饰词的）")
        
        for kw in filtered_keywords:
            if candidate_indices:
                results = self.search_with_index(kw, master, candidate_indices, top_k=30)
            else:
                results = self.search_local(kw, master, top_k=30)
            for r in results:
                norm = r.get("company_norm", "")
                # 排除目标公司自身
                if norm == company_rec.get("company_norm", ""):
                    continue
                if norm not in seen_norms:
                    seen_norms.add(norm)
                    r["_ai_context"] = {
                        "target_company": company_name,
                        "business": ai_analysis.get("business", "") if ai_analysis else "",
                        "matched_keyword": kw,
                    }
                    all_candidates.append(r)

        if progress_cb:
            progress_cb(f"")
            progress_cb(f"📊 一共找到 {len(all_candidates)} 家可能相关的公司")
        
        # 关键改进：如果结果为 0，尝试更宽泛的搜索
        if not all_candidates and filtered_keywords:
            if progress_cb:
                progress_cb(f"")
                progress_cb(f"🔍 精确搜索没有找到结果，尝试更宽泛的搜索...")
            # 用第一个关键词的前 2 个字做宽泛搜索
            broad_kw = filtered_keywords[0][:2]
            if candidate_indices:
                broad_results = self.search_with_index(broad_kw, master, candidate_indices, top_k=20)
            else:
                broad_results = self.search_local(broad_kw, master, top_k=20)
            all_candidates.extend(broad_results)
            if progress_cb:
                progress_cb(f"   用「{broad_kw}」宽泛搜索找到 {len(broad_results)} 家")

        # 按评分排序
        all_candidates.sort(key=lambda x: x.get("_score", 0), reverse=True)

        # DeepSeek 精排
        if all_candidates and self._api_key:
            if progress_cb:
                progress_cb(f"")
                progress_cb(f"🤖 正在让 AI 分析每家公司的供应链关系...")
            rerank_prompt = self._build_company_match_prompt(
                company_name, products, ai_analysis, all_candidates[:30]
            )
            try:
                ai_results = self._call_deepseek(rerank_prompt)
                company_to_candidate = {c["company"]: c for c in all_candidates}
                reranked = []
                for ai_item in ai_results:
                    rec = company_to_candidate.get(ai_item.get("company", ""))
                    if rec:
                        rec["_score"] = ai_item.get("score", 50) / 100.0
                        rec["_ai_reason"] = ai_item.get("reason", "")
                        rec["_ai_relationship"] = ai_item.get("relationship", "")
                        reranked.append(rec)
                reranked.sort(key=lambda x: x.get("_score", 0), reverse=True)
                
                if progress_cb:
                    progress_cb(f"")
                    progress_cb(f"✅ AI 分析完成！找到 {len(reranked[:top_k])} 家最相关的公司")
                    
                    # 关键改进：如果 AI 精排结果太少（< 3 家），回退到本地评分结果
                    if len(reranked) < 3:
                        if progress_cb:
                            progress_cb(f"")
                            progress_cb(f"💡 AI 精排结果较少，结合本地评分补充更多相关公司...")
                        # 合并 AI 结果和本地评分结果
                        ai_companies = {r.get("company", "") for r in reranked}
                        # 从原始候选中补充（排除 AI 已返回的）
                        for candidate in all_candidates[:top_k]:
                            if candidate.get("company", "") not in ai_companies:
                                # 保留本地评分
                                candidate["_ai_reason"] = "本地评分匹配"
                                candidate["_ai_relationship"] = "相关"
                                reranked.append(candidate)
                                if len(reranked) >= top_k:
                                    break
                        reranked.sort(key=lambda x: x.get("_score", 0), reverse=True)
                        if progress_cb:
                            progress_cb(f"   已补充到 {len(reranked[:top_k])} 家")
                    
                    # 展示结果摘要
                    if reranked:
                        progress_cb(f"")
                        progress_cb(f"📋 相关公司列表：")
                        for i, r in enumerate(reranked[:min(5, len(reranked))], 1):
                            company = r.get("company", "")[:30]
                            relationship = r.get("_ai_relationship", "")
                            reason = r.get("_ai_reason", "")[:50]
                            score = r.get("_score", 0)
                            
                            # 用通俗易懂的方式展示关系
                            rel_emoji = {
                                "上游供应商": "🔼",
                                "下游采购商": "🔽",
                                "同行": "🏢",
                                "不相关": "❌",
                                "相关": "✅"
                            }.get(relationship, "❓")
                            
                            progress_cb(f"   {i}. {company}")
                            if relationship:
                                progress_cb(f"      {rel_emoji} {relationship}")
                            if reason:
                                progress_cb(f"      💬 {reason}")
                
                return reranked[:top_k]
            except Exception:
                pass

        if progress_cb:
            progress_cb(f"")
            progress_cb(f"✅ 找到 {len(all_candidates[:top_k])} 家相关公司")
        return all_candidates[:top_k]

    def _build_company_match_prompt(self, company_name: str, products: str,
                                    ai_analysis: Optional[dict],
                                    candidates: List[dict]) -> str:
        """构造公司名匹配的 DeepSeek prompt。"""
        business_desc = ""
        if ai_analysis:
            business_desc = (
                f"\n该公司主营业务：{ai_analysis.get('business', '')}\n"
                f"上游供应商类型：{ai_analysis.get('upstream', '')}\n"
                f"下游采购商类型：{ai_analysis.get('downstream', '')}"
            )

        cand_list = []
        for c in candidates[:30]:
            cand_list.append({
                "company": c.get("company", ""),
                "products": c.get("products", "")[:60],
                "address": c.get("address", "")[:40],
            })

        return f"""你是 B2B 供应链分析师。

目标公司：{company_name}
主营产品：{products or '未知'}{business_desc}

请分析以下候选公司与目标公司的供应链关系：

{json.dumps(cand_list, ensure_ascii=False, indent=2)}

返回 JSON 数组，每个元素包含：
- "company": 公司名（必须与候选列表完全一致）
- "score": 0-100 相关度评分
- "reason": 一句话说明关系
- "relationship": "上游供应商" / "下游采购商" / "同行" / "不相关"

评分标准：
- 只要有一定关联性（产品、行业、地域相近）就给 20-40 分
- 产品直接相关或供应链上下游给 40-70 分
- 高度匹配的核心供应商/采购商给 70-100 分
- 完全不相关的给 0-10 分

请返回 score >= 20 的公司，按 score 降序，只返回 JSON 数组。"""

    def _call_deepseek_json(self, prompt: str) -> Any:
        """调用 DeepSeek 并返回解析后的 JSON（任意结构）。"""
        import requests as req
        url = "https://api.deepseek.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": "deepseek-chat",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
            "max_tokens": 1000,
        }
        resp = req.post(url, headers=headers, json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"].strip()
        content = re.sub(r"^```(?:json)?\s*", "", content)
        content = re.sub(r"\s*```$", "", content)
        start = content.find("{")
        end = content.rfind("}")
        if start >= 0 and end > start:
            content = content[start:end + 1]
        return json.loads(content)

    def _deepseek_rerank(self, query: str, candidates: List[dict],
                         top_k: int, progress_cb) -> List[dict]:
        """DeepSeek 精排（通用关键词搜索场景）。"""
        if not self._api_key:
            return candidates[:top_k]
        if progress_cb:
            progress_cb(f"")
            progress_cb(f"🤖 正在调用 AI 深度分析 {len(candidates)} 家候选公司...")
            progress_cb(f"   AI 会分析每家公司的：")
            progress_cb(f"   • 主营业务与搜索词的关联度")
            progress_cb(f"   • 在供应链中的位置（上游/下游/同行）")
            progress_cb(f"   • 与搜索意图的匹配理由")
        prompt = self._build_match_prompt(query, candidates)
        try:
            ai_results = self._call_deepseek(prompt)
        except Exception as e:
            if progress_cb:
                progress_cb(f"⚠️ AI 分析失败，使用本地结果: {str(e)[:50]}")
            return candidates[:top_k]
        if not ai_results:
            return candidates[:top_k]
        if progress_cb:
            progress_cb(f"")
            progress_cb(f"✅ AI 分析完成，找到 {len(ai_results)} 家最相关的公司")
            # 展示 AI 分析的前 3 名及理由
            if ai_results:
                progress_cb(f"")
                progress_cb(f"💡 AI 认为最相关的：")
                for i, ai_item in enumerate(ai_results[:3], 1):
                    company = ai_item.get("company", "")[:25]
                    score = ai_item.get("score", 0)
                    reason = ai_item.get("reason", "")[:40]
                    relationship = ai_item.get("relationship", "")
                    progress_cb(f"   {i}. {company} ({score}分)")
                    if relationship:
                        progress_cb(f"      → {relationship}")
                    if reason:
                        progress_cb(f"      → {reason}")
        company_to_candidate = {c["company"]: c for c in candidates}
        reranked = []
        for ai_item in ai_results:
            rec = company_to_candidate.get(ai_item.get("company", ""))
            if rec: 
                rec["_score"] = ai_item.get("score", 0) / 100.0
                rec["_ai_reason"] = ai_item.get("reason", "")
                rec["_ai_relationship"] = ai_item.get("relationship", "")
                reranked.append(rec)
        reranked.sort(key=lambda x: x.get("_score", 0), reverse=True)
        return reranked[:top_k]

    # ── DeepSeek 语义精排 ──

    def search_with_deepseek(self, query: str, master: List[dict],
                             top_k: int = 20,
                             progress_cb=None) -> List[dict]:
        """DeepSeek 语义匹配（向后兼容，内部调用 search_smart）。"""
        return self.search_smart(query, master, top_k=top_k,
                                 progress_cb=progress_cb)

    @staticmethod
    def _build_match_prompt(query: str, candidates: List[dict]) -> str:
        """构造 DeepSeek 匹配 prompt（供应链分析师角色）。"""
        # 候选摘要（控制 token 数）
        cand_list = []
        for c in candidates[:30]:  # 最多发 30 条给 AI
            cand_list.append({
                "company": c.get("company", ""),
                "products": c.get("products", "")[:60],
                "source": c.get("source", ""),
                "address": c.get("address", "")[:40],
            })

        return f"""你是一位资深的 B2B 供应链分析师，精通全球产业链上下游关系。

用户正在寻找与以下描述相关的下游采购商或上游供应商：

【用户查询】{query}

以下是从企业数据库中初步筛选的候选公司，请分析每家公司与用户查询的业务相关性：

{json.dumps(cand_list, ensure_ascii=False, indent=2)}

请返回一个 JSON 数组，每个元素包含：
- "company": 公司名（必须与候选列表中的完全一致）
- "score": 0-100 的相关度评分（100=完美匹配，50=部分相关，0=不相关）
- "reason": 一句话说明匹配原因（如"主营光伏组件，是用户的直接下游采购商"）
- "relationship": "下游采购商" / "上游供应商" / "同行竞争者" / "不相关"

要求：
1. 只返回 score >= 30 的公司
2. 按 score 降序排列
3. 只返回 JSON 数组，不要其他文字或 markdown 标记
4. company 字段必须与候选列表中的公司名完全一致

请分析并返回 JSON 数组："""

    def _call_deepseek(self, prompt: str) -> List[dict]:
        """调用 DeepSeek API，返回解析后的 JSON 数组。"""
        import requests as req

        url = "https://api.deepseek.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": "deepseek-chat",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,  # 低温度 → 更确定性
            "max_tokens": 2000,
        }

        resp = req.post(url, headers=headers, json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        content = data["choices"][0]["message"]["content"].strip()

        # 提取 JSON 数组（兼容 markdown 代码块包裹）
        content = re.sub(r"^```(?:json)?\s*", "", content)
        content = re.sub(r"\s*```$", "", content)

        # 找到第一个 [ 和最后一个 ]
        start = content.find("[")
        end = content.rfind("]")
        if start >= 0 and end > start:
            content = content[start:end + 1]

        results = json.loads(content)
        if not isinstance(results, list):
            return []

        # 校验每条结果格式
        valid = []
        for item in results:
            if isinstance(item, dict) and "company" in item:
                item.setdefault("score", 0)
                item.setdefault("reason", "")
                item.setdefault("relationship", "")
                valid.append(item)
        return valid
