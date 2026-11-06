"""province_filter.py —— 省份精准采集：全省份数据表 + 属地匹配 + 各站参数表。

设计（对齐大厂分平台能力差异化的做法）：
  ① 参数直筛：站方原生就有地区筛选参数的，直接把省份拼进搜索 URL，由服务端筛，
     最准且零额外请求。实测（2026-11-04 探针，debug/probe_region_params*.py）：
       · 中国制造网 comProvince=<英文名>   （A/B：各60卡、重合2/60、两省交集0）
       · 马可波罗     &ae=<省份拼音>        （A/B：广东∩全国重合2/26；山西 shanxi／陕西 shanxii）
       · 1688         &province=<简体短名>  （A/B：公司搜 17 vs 20 家、重合3；结果名 20/20 广东系）
  ② 数据层过滤：站方没有地区参数的（跨国采购网/八方资源网/顺企网/爱采购/麦麦B2B，
     实测搜索结果页 0 处地区筛选痕迹），用记录里已有的「所在区域/地址/公司名」做属地
     判定，明确不是本省的丢弃（fail-closed：判不出省的也丢弃——用户要的是「精准」，
     混进外省商家比少几条更伤）；判定只用下表，绝不模糊猜。
  ③ 参数直筛站一律**再拿本地数据核一遍**（trust but verify），因为「站方筛好了」这个
     前提本身会在改版那天静默失效，而失效的样子是「整表标着广东、实际各省都有」——
     用户第一眼就能看出来的那种事故。核验强度按实测证据分两档（PARAM_VERIFY）：
       · lenient（mic/makepolo，A/B 实锤有效）：只砍「明确指向外省」的漏网记录，
         判不出属地的保留（站方已筛过，本地再杀就是误杀）；
       · strict（1688）：2026-11-04 客户那轮 91 条 1688 记录里，「地址+公司名」能确认
         属广东的只有 12 条，42 条明确是外省、31 条无任何属地信号（全部来自
         `src=zhanwai` 的站外 SEM 落地页——那个页面既不吃 keywords 也不吃 province，
         搜「海洋能」能回义乌饰品厂）。所以 1688 与数据层站同口径：miss/unknown 都砍。

属地判定规则（match_province）：先把文本里的行政区划尾巴去掉（广东省→广东、
深圳市→深圳），再拿「本省短名/简称 + 下辖全部地级市名」做子串命中：
  · 只命中目标省 → match；只命中别的省 → miss；
  · 同时命中两个省（如「深圳江西商会」）或谁都没命中 → unknown（按 ②口径处置）。

判定文本（record_region_text）除区域/地址外**一定带上公司名**：中国大陆公司名开头就是
登记地的行政区划（工商强制），是比页面标注更硬的属地信号。实测那一轮 300 条
「页面没标省份、无法确认」的记录里，13 条真广东（如「深圳市深海诺科技有限公司」
「佛山安可锚链有限公司」——地址栏整栏为空）靠公司名救回，另 194 条由「判不出」
变成「确认外省」，账目才诚实。
"""
import re

# 34 个省级行政区（显示顺序固定：国家官方区划顺序，港澳台排在末尾）
PROVINCES = [
    "北京", "天津", "河北", "山西", "内蒙古", "辽宁", "吉林", "黑龙江",
    "上海", "江苏", "浙江", "安徽", "福建", "江西", "山东", "河南",
    "湖北", "湖南", "广东", "广西", "海南", "重庆", "四川", "贵州",
    "云南", "西藏", "陕西", "甘肃", "青海", "宁夏", "新疆",
    "香港", "澳门", "台湾",
]
OTHER_LABEL = "其他/未知"   # 兜底项：只要参数站没有港澳台之外的数据，一般用不到

# ── 参数直筛站的省份参数值表（全部经 2026-11-04 探针实机核验来源）──
# 马可波罗：页面地区列表 a 标签 id（search_alljs 的 search_page 拼 &ae=点击元素id）
MAKEPOLO_PINYIN = {
    "北京": "beijing", "天津": "tianjin", "河北": "hebei", "山西": "shanxi",
    "内蒙古": "neimenggu", "辽宁": "liaoning", "吉林": "jilin", "黑龙江": "heilongjiang",
    "上海": "shanghai", "江苏": "jiangsu", "浙江": "zhejiang", "安徽": "anhui",
    "福建": "fujian", "江西": "jiangxi", "山东": "shandong", "河南": "henan",
    "湖北": "hubei", "湖南": "hunan", "广东": "guangdong", "广西": "guangxi",
    "海南": "hainan", "重庆": "chongqing", "四川": "sichuan", "贵州": "guizhou",
    "云南": "yunnan", "西藏": "xizang", "陕西": "shanxii", "甘肃": "gansu",
    "青海": "qinghai", "宁夏": "ningxia", "新疆": "xinjiang",
    "香港": "xianggang", "澳门": "aomen", "台湾": "taiwan",
}
# 中国制造网：搜索页内嵌 JSON 的官方分组值表（Shanxi=山西、Shaanxi=陕西；
# 站表里 Heibei 是站方笔误，实际请求用 Hebei 有效——A/B 实测即是 Hebei）
MIC_EN = {
    "北京": "Beijing", "天津": "Tianjin", "河北": "Hebei", "山西": "Shanxi",
    "内蒙古": "InnerMongolia", "辽宁": "Liaoning", "吉林": "Jilin", "黑龙江": "Heilongjiang",
    "上海": "Shanghai", "江苏": "Jiangsu", "浙江": "Zhejiang", "安徽": "Anhui",
    "福建": "Fujian", "江西": "Jiangxi", "山东": "Shandong", "河南": "Henan",
    "湖北": "Hubei", "湖南": "Hunan", "广东": "Guangdong", "广西": "Guangxi",
    "海南": "Hainan", "重庆": "Chongqing", "四川": "Sichuan", "贵州": "Guizhou",
    "云南": "Yunnan", "西藏": "Tibet", "陕西": "Shaanxi", "甘肃": "Gansu",
    "青海": "Qinghai", "宁夏": "Ningxia", "新疆": "Xinjiang",
    "香港": "Hongkong", "澳门": "Macao", "台湾": "Taiwan",
}
# 参数直筛站（slug）：这些站选中省份后把省份拼进搜索 URL（服务端筛）
# gongchang：单省时用 areaid 参数（已实机核实），多省走数据层过滤
PARAM_SITES = {"mic", "makepolo", "1688", "gongchang"}
# 参数直筛站的本地核验强度（口径见模块 docstring ③）：
#   lenient = 只砍 miss（确认外省）；strict = miss/unknown 都砍；未登记的参数站默认 strict。
# gongchang 用 lenient：站方 areaid 参数已核实有效，且公司名开头的地级市是强属地信号。
PARAM_VERIFY = {"mic": "lenient", "makepolo": "lenient", "1688": "strict",
                "gongchang": "lenient"}

# ── 数据层匹配的属地词表：每省 = 短名 + 简称 + 全部地级行政区名 ──
# 短名自带唯一性；单字简称（贵/广/黑）歧义大，一律不入表。
_CITY_TABLE = {
    "河北": ["冀", "石家庄", "唐山", "秦皇岛", "邯郸", "邢台", "保定", "张家口", "承德", "沧州", "廊坊", "衡水"],
    "山西": ["晋", "太原", "大同", "阳泉", "长治", "晋城", "朔州", "晋中", "运城", "忻州", "临汾", "吕梁"],
    "内蒙古": ["呼和浩特", "包头", "乌海", "赤峰", "通辽", "鄂尔多斯", "呼伦贝尔", "巴彦淖尔", "乌兰察布", "兴安", "锡林郭勒", "阿拉善"],
    "辽宁": ["辽", "沈阳", "大连", "鞍山", "本溪", "丹东", "锦州", "营口", "阜新", "辽阳", "盘锦", "铁岭", "朝阳", "葫芦岛"],
    "吉林": ["吉", "长春", "四平", "辽源", "通化", "白山", "松原", "白城", "延边"],
    "黑龙江": ["哈尔滨", "齐齐哈尔", "鸡西", "鹤岗", "双鸭山", "大庆", "伊春", "佳木斯", "七台河", "牡丹江", "黑河", "绥化", "大兴安岭"],
    "江苏": ["苏", "南京", "无锡", "徐州", "常州", "苏州", "南通", "连云港", "淮安", "盐城", "扬州", "镇江", "泰州", "宿迁"],
    "浙江": ["浙", "杭州", "宁波", "温州", "嘉兴", "湖州", "绍兴", "金华", "衢州", "舟山", "台州", "丽水", "义乌"],
    "安徽": ["皖", "合肥", "芜湖", "蚌埠", "淮南", "马鞍山", "淮北", "铜陵", "安庆", "黄山", "滁州", "阜阳", "宿州", "六安", "亳州", "池州", "宣城"],
    "福建": ["闽", "福州", "厦门", "莆田", "三明", "泉州", "漳州", "南平", "龙岩", "宁德"],
    "江西": ["赣", "南昌", "景德镇", "萍乡", "九江", "新余", "鹰潭", "赣州", "吉安", "宜春", "抚州", "上饶"],
    "山东": ["鲁", "济南", "青岛", "淄博", "枣庄", "东营", "烟台", "潍坊", "济宁", "泰安", "威海", "日照", "临沂", "德州", "聊城", "滨州", "菏泽"],
    "河南": ["豫", "郑州", "开封", "洛阳", "平顶山", "安阳", "鹤壁", "新乡", "焦作", "濮阳", "许昌", "漯河", "三门峡", "南阳", "商丘", "信阳", "周口", "驻马店", "济源"],
    "湖北": ["鄂", "武汉", "黄石", "十堰", "宜昌", "襄阳", "鄂州", "荆门", "孝感", "荆州", "黄冈", "咸宁", "随州", "恩施"],
    "湖南": ["湘", "长沙", "株洲", "湘潭", "衡阳", "邵阳", "岳阳", "常德", "张家界", "益阳", "郴州", "永州", "怀化", "娄底", "湘西"],
    "广东": ["粤", "广州", "深圳", "珠海", "汕头", "佛山", "韶关", "湛江", "肇庆", "江门", "茂名", "惠州", "梅州", "汕尾", "河源", "阳江", "清远", "东莞", "中山", "潮州", "揭阳", "云浮", "顺德"],
    "广西": ["桂", "南宁", "柳州", "桂林", "梧州", "北海", "防城港", "钦州", "贵港", "玉林", "百色", "贺州", "河池", "来宾", "崇左"],
    "海南": ["琼", "海口", "三亚", "儋州", "三沙"],
    "重庆": ["渝", "万州"],
    "四川": ["川", "蜀", "成都", "自贡", "攀枝花", "泸州", "德阳", "绵阳", "广元", "遂宁", "内江", "乐山", "南充", "眉山", "宜宾", "广安", "达州", "巴中", "雅安", "资阳", "阿坝", "甘孜", "凉山"],
    "贵州": ["贵阳", "六盘水", "遵义", "安顺", "毕节", "铜仁", "黔西南", "黔东南", "黔南"],
    "云南": ["滇", "昆明", "曲靖", "玉溪", "保山", "昭通", "丽江", "普洱", "临沧", "楚雄", "红河", "文山", "西双版纳", "大理", "德宏", "怒江", "迪庆"],
    "西藏": ["藏", "拉萨", "日喀则", "昌都", "林芝", "山南", "那曲", "阿里"],
    "陕西": ["陕", "西安", "铜川", "宝鸡", "咸阳", "渭南", "延安", "汉中", "榆林", "安康", "商洛"],
    "甘肃": ["甘", "陇", "兰州", "嘉峪关", "金昌", "白银", "天水", "武威", "张掖", "平凉", "酒泉", "庆阳", "定西", "陇南", "甘南", "临夏"],
    "青海": ["青", "西宁", "海东", "海北", "黄南", "果洛", "玉树", "海西", "德令哈", "格尔木"],
    "宁夏": ["宁", "银川", "石嘴山", "吴忠", "固原", "中卫"],
    "新疆": ["新", "乌鲁木齐", "克拉玛依", "吐鲁番", "哈密", "昌吉", "博尔塔拉", "巴音郭楞", "阿克苏", "喀什", "和田", "伊犁", "塔城", "阿勒泰", "石河子", "阿拉尔", "图木舒克", "五家渠", "库尔勒", "奎屯", "霍尔果斯"],
    "香港": ["港"],
    "澳门": ["澳"],
    "台湾": ["台", "台北", "新北", "桃园", "台中", "台南", "高雄", "基隆"],
}
# 「海南」与「海北/海西/海东」（青海）不冲突：词表整词命中，且多省同时命中按 unknown 处置
_SHORT_STRIP_RE = re.compile(
    r"(维吾尔自治区|壮族自治区|回族自治区|特别行政区|自治区|自治州|地区|省|市)")

def _build_tokens():
    """省 -> 判定词集合；同时反查每个词归属哪些省（歧义词标成跨省）。"""
    tokens, owner = {}, {}
    for pv in PROVINCES:
        s = {pv} | set(_CITY_TABLE.get(pv, []))
        tokens[pv] = s
        for t in s:
            owner.setdefault(t, set()).add(pv)
    return tokens, owner

PROVINCE_TOKENS, _TOKEN_OWNER = _build_tokens()


def _norm_variants(text):
    """产出 (原样, 剥掉行政区划尾巴) 两种形态：「深圳市」→「深圳」才能命中词表。"""
    raw = re.sub(r"\s+", "", str(text or ""))
    stripped = _SHORT_STRIP_RE.sub("", raw)
    return raw, stripped


def _parse_provinces(province):
    """统一省份输入为列表：支持单省字符串、逗号分隔多省、列表。
    空输入返回空列表（= 全国不限，调用方应跳过过滤）。"""
    if isinstance(province, (list, tuple)):
        return [str(p).strip() for p in province if str(p).strip()]
    s = str(province or "").strip()
    if not s:
        return []
    if "," in s:
        return [p.strip() for p in s.split(",") if p.strip()]
    return [s]


def match_province(province, text):
    """返回 "match" / "miss" / "unknown"（口径见模块 docstring）。
    支持多省：province 可以是单省字符串、逗号分隔多省、或列表——
    命中任一目标省即 match，全部未命中且有明确外省信号即 miss。"""
    targets = _parse_provinces(province)
    if not targets:
        return "unknown"
    raw, stripped = _norm_variants(text)
    if not stripped:
        return "unknown"
    hits = set()
    for pv, toks in PROVINCE_TOKENS.items():
        if any((t in raw) or (t and t in stripped) for t in toks):
            hits.add(pv)
    if not hits:
        return "unknown"
    # 多省场景：命中任一目标省即 match
    if hits & set(targets):
        return "match"
    # 全部命中结果都不在目标省集合内 → 明确 miss
    if len(hits) == 1:
        return "miss"
    # 多省命中但都不是目标省 → 跨省歧义，判 unknown
    return "unknown"


def param_value(site_slug, province, kind):
    """参数直筛站的省份参数值。kind: pinyin(makepolo) / en(mic) / cn(1688)。

    返回 None = 该站没有这个省份的原生选项（如港澳台在部分站不存在），
    调用方必须退回数据层过滤或不过滤，绝不带着错参数硬采。"""
    table = {"pinyin": MAKEPOLO_PINYIN, "en": MIC_EN}.get(kind)
    if table is not None:
        return table.get(province)
    return province if province in PROVINCES else None


def uses_param_filter(site_slug):
    return str(site_slug or "") in PARAM_SITES


def param_verify_policy(site_slug):
    """参数直筛站的本地核验档位：lenient / strict（默认 strict，宁可错杀不可污染）。"""
    return PARAM_VERIFY.get(str(site_slug or "").lower(), "strict")


# 属地判定要读的字段：区域/地址是页面标注，公司名是工商登记级的硬性属地信号。
# 「公司名」两种写法都收：CDP 各站的记录用「公司名称」，清洗/企知道侧用「公司名」。
_REGION_FIELDS = ("所在区域", "地址", "公司名称", "公司名")


def record_region_text(rec):
    """记录 → 属地判定文本（见模块 docstring 关于公司名的说明）。"""
    if not isinstance(rec, dict):
        return str(rec or "")
    return " ".join(str(rec.get(k) or "") for k in _REGION_FIELDS)


def judge_record(site_slug, province, rec):
    """省份精准采集的入库判定。返回 (verdict, keep)：
    verdict ∈ match/miss/unknown，keep = 这条是否入库。
    支持多省：province 可以是单省字符串、逗号分隔多省、或列表——
    命中任一目标省即 match，按该站核验策略决定 keep。"""
    targets = _parse_provinces(province)
    if not targets:
        return "unknown", True
    text = record_region_text(rec)
    # 逐省判定，取最优结果（任一 match 即 match，全部 miss 即 miss）
    best_verdict = "unknown"
    for pv in targets:
        v = match_province(pv, text)
        if v == "match":
            best_verdict = "match"
            break
        if v == "miss" and best_verdict != "match":
            best_verdict = "miss"
    if uses_param_filter(site_slug) and param_verify_policy(site_slug) == "lenient":
        return best_verdict, best_verdict != "miss"
    return best_verdict, best_verdict == "match"


def region_label(province, text):
    """从判定文本里摘出如实的属地短语：命中目标省就写「省＋可识别到的市」（广东·深圳），
    只命中省写「省」，判不出则返回空串。用于**核验通过后**回填所在区域，
    与旧行为「拿已选省份臆测填空」的区别是：这里的值永远有页面证据支撑。
    支持多省：province 可以是逗号分隔多省或列表，每个命中的省各出一个标签，
    用「/」拼接（如「广东·深圳/浙江·杭州」）。"""
    targets = _parse_provinces(province)
    if not targets:
        return ""
    raw, stripped = _norm_variants(text)
    if not raw:
        return ""
    labels = []
    for pv in targets:
        if match_province(pv, text) != "match":
            continue
        city = ""
        for c in _CITY_TABLE.get(pv, []):
            if len(c) < 2:
                continue     # 单字简称（粤/苏/浙）不进标签
            pos = raw.find(c)
            if pos >= 0 and (not city or pos < raw.find(city)):
                city = c
        labels.append(f"{pv}·{city}" if city else pv)
    return "/".join(labels) if labels else ""
