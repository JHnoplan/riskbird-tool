# -*- coding: utf-8 -*-
"""ingest_cleaning.py —— 入库前清洗与分区派生（write-side data quality）。

为什么放在「入库前」而不是「查询时」：
    旧实现把去重/归一/属地推导都压在读取端合并阶段，等于每次打开搜索框
    都重跑一遍清洗；上游文件永远保持脏状态，同一个错误要付 N 次代价。
    大厂数据湖（Delta Lake / Iceberg + DataWorks / Databricks）的统一口径是
    Bronze（原始 append-only）→ **Silver（清洗+标准化后才落库）**→ Gold（派生索引），
    清洗是一次性写入侧成本，读取侧只享受结果。本模块就是 Silver 那一层。

三类职责（互相独立、可单独测试）：
    1. 修复（不丢信息）：主营里混入的网页正文、日期串错进主营列、号码被空格
       拆成碎片、联系人栏填的却是公司名、多值分隔符不规范。
    2. 派生分区键：region(dom/intl) + province + industry + quality。
       属地一律复用 data_pool.record_region，保证与搜索过滤、结果统计、
       导出范围三处口径永远只有一个数字。
    3. 剔除（必须可回溯）：公司名根本不是实体的行（图片路径、URL、纯日期、
       说明性散文）、以及数据池既有门禁「电话/固话/邮箱/官网五者全无」。
       每条被剔除的记录都带原因进报告，GUI 上传前给用户看，绝不静默丢数据；
       被剔记录还整份留在 report["dropped_rows"] 里，可一键导出成
       「未入库清单」——不入库不等于消失（reject-file / dead-letter 口径）。

门禁口径（2026-11-09 用户明确指令，不得放宽）：
    数据池主表有效性 = phone/mobile/landline/email/website 五者取其一，
    **微信号不作为独立有效性证据**；判据实现只有 data_pool._is_valid_record 一份，
    本模块直接复用，避免两处规则漂移。
"""
import re
import unicodedata
from typing import Any, Dict, List, Optional, Tuple

try:
    from data_pool import (record_region, compute_quality_score, _is_valid_record,
                           REGION_RULE_VERSION)
    _HAS_POOL_CORE = True
except Exception:
    _HAS_POOL_CORE = False
    REGION_RULE_VERSION = ""

# ── 剔除原因码（GUI/日志按码归类，用户能看懂为什么少了几条）──
REASON_NO_CONTACT = "无联系方式"
REASON_NO_NAME = "缺公司名"
REASON_BAD_NAME = "公司名不是企业实体"

# 被剔记录整份保留的上限：一次导入几百万行时不能把内存吃穿。超上限只在报告里
# 记 dropped_overflow（条数），并在清单里明写「仅前 N 条」，绝不假装全给了。
DROP_ROW_CAP = 50000

# ── 公司名无效形态 ──────────────────────────────────────────────────────────
_URLISH_RE = re.compile(r"^(?:https?|ftp|www\.)", re.IGNORECASE)
_IMAGE_RE = re.compile(
    r"\.(?:png|jpe?g|gif|webp|bmp|ico|svg|pdf|docx?|xlsx?|zip|rar|7z)$", re.IGNORECASE)
_PATHISH_RE = re.compile(r"^[\w./\-]*(?:/[\w.\-]+){2,}$")
_PURE_NUM_RE = re.compile(r"^[\d\s.,\-/]+$")
_DATE_RE = re.compile(
    r"^\d{4}[-/年]\d{1,2}[-/月]?\d{0,2}日?(?:[ 　]\d{1,2}:\d{2}(?::\d{2})?)?$")
# 导航/说明性词：出现在开头基本可判定为页面残留而非公司名
_NAVI_HEADS = ("首页", "关于我们", "联系我们", "产品中心", "新闻动态", "诚聘英才",
               "留言", "返回", "更多", "说明", "备注", "排除情况", "总计", "合计",
               "序号", "列表", "目录", "无数据", "暂无")
# 栏目型「非实体」尾缀：B2B 网站的频道页/专题页名被当企业名采进来。真实数据取证
# （存量池 87,545 条全量原始名称）：以「频道」结尾 90 条（安防频道/泵阀频道/
# 表面处理频道…）、以「板块」结尾 1 条（深圳市节博会智慧板块），**零条真企业**；
# 而「商城」结尾那条「昌邑市北孟镇富丽华商城」是真实个体户——所以尾缀表只收录
# 取证零误伤的词，并且必须带组织尾缀豁免（含「公司/集团/中心…」的长名一律放行）。
_NAVI_TAILS = ("频道", "板块", "版块", "专区", "专栏", "图集", "回放", "直播")
# 栏目名就是页面导航文字，长度普遍 ≤6；组织尾缀豁免只给「长名里恰好含公司/酒店
# 等字样」的真实实体名。取证：豁免只放过 1 条「酒店频道」——它显然也是栏目名，
# 所以对短名不再豁免。
_NAVI_TAIL_SHORT_LEN = 8
# 名称门禁规则版本：Bronze 是 append-only 原始层，判据升级不可能回头改写历史
# 对象，所以名称门禁在**读取端也会重放一遍**（见 gate_valid_names）。本地缓存里
# 存下这个版本号，升级后第一次加载就作废旧总表、走全量重建自动回刷——等价于
# 大厂数据湖「规则即代码 + 视图可重放」的 backfill 替代方案。
#   1 = URL/图片/路径/纯数字/日期 + 导航前缀 + 栏目尾缀 + 句读散文 + 句结尾标点
NAME_RULE_VERSION = "1"

# 散文型长句标志（中文句读 + 无公司尾缀）
_PROSE_PUNCT_RE = re.compile(r"[，。；！？、]")
# 结尾句标点：企业名不会以句号/问号/感叹号/分号收尾，而成句的说明文字必然以此
# 收尾。真实数据取证（存量池 + 待上传文件全量原始名称）里，以「。」收尾的 7 条
# 全部是海洋展「采集进度」说明表的成句文字，一条正常企业名都没有。
# 判据只认全角句标点与分号，不认 ASCII「!?」——"Ecrowd!"、"Nabach Wir machen
# Was!" 这类欧洲品牌名就是真名；也不认 ASCII「.」——捷克 "ABCO expert s.r.o."
# 这类合法缩写以句点收尾的有上万条，判散文会成片误杀（尾缀修复仍处理它）。
# 它不看组织尾缀——旧写法「含句读且无尾缀且长度>40」会被句子里的「企业/公司」
# 字样豁免：「…重复企业及重复号码不计入。」就是这样漏进候选集的。
_SENTENCE_END_RE = re.compile(r"[。！？；;]$")
_ORG_TAIL_ALTS = (
    r"公司|集团|厂|企业|中心|研究院|研究所|学院|大学|基地|合作社|商行|门市部|"
    r"工作室|个体工商户|事业部|分公司|办事处|代表处|酒店|商场|超市|医院|学校|"
    r"inc|ltd|llc|gmbh|corp|co\b|pty|pte|s\.a|b\.v|s\.r\.l|株式会社|company")
_ORG_TAIL_RE = re.compile("(" + _ORG_TAIL_ALTS + ")", re.IGNORECASE)
# 结尾式尾缀（允许收尾括号组与引号）：区分「这个词就是实体名」与
# 「句子里恰好提到了公司/企业」——散文型说明文字几乎总会撞到前者。
# "XX工程有限公司（筹）" 这类带括注的合法实体名也必须认出来。
_ORG_TAIL_END_RE = re.compile(
    "(" + _ORG_TAIL_ALTS + r")(?:[\(（\[【][^\)）\]】]{0,16}[\)）\]】]|[\"'’])*"
    r"[ \t　]*$", re.IGNORECASE)

# 职位词（判断 title 装的是「职位」还是「真人姓名」，避免把职位当姓名外呼）
_ROLE_RE = re.compile(
    r"(经理|总监|主管|厂长|总裁|董事长|老板|先生|女士|小姐|老师|师傅|代表|"
    r"销售|采购|外贸|内销|内贸|业务|技术|工程师|助理|专员|主任|部长|"
    r"manager|sales|director|engineer|chief|mr\.?|mrs\.?|ms\.?|ceo|cto|cfo)",
    re.IGNORECASE)

# ── 主营/备注里的网页正文垃圾标记（命中即截断到该处）────────────────────────
_JUNK_MARKERS = (
    "联系方式", "公司地址:", "公司地址：", "电　话", "电 话", "手　机", "手 机",
    "版权所有", "Copyright", "copyright", "备案号", "ICP备", "网站首页", "返回上一页",
    "上一篇", "下一篇", "在线咨询", "扫一扫", "二维码", "关注我们", "地图", "公交",
    "免责声明", "版权声明", "联系我们", "关于我们", "更多详情", "点击", "验证码",
    "请输入", "Submit", "登录", "注册", "购物车", "友情链接", "热门搜索",
    "X 首页", "当前位置", "办公地址示意图",
)
_JUNK_MARK_RE = re.compile(
    "|".join(re.escape(m) for m in sorted(_JUNK_MARKERS, key=len, reverse=True)))

# ── 日期形态（错进主营/行业列的典型特征，来自真实文件「查阅日期→主营」误映射）──
_DATEISH_RE = re.compile(
    r"^\s*\d{4}[-/.年]\d{1,2}([-/.月]\d{1,2}日?)?\s*$|^\s*\d{1,2}[-/]\d{1,2}\s*$")

# ── 号码碎片：有效号码至少 6 位数字（国际短号除外，但 B2B 名单里 6 位以下必为碎片）
_MIN_PHONE_DIGITS = 6

# ── 中国省份（与 data_pool._CN_PROVINCES 同集合，另带常用简称/直辖市写法）──
_PROVINCES = (
    "北京", "天津", "上海", "重庆", "河北", "山西", "内蒙古", "辽宁", "吉林",
    "黑龙江", "江苏", "浙江", "安徽", "福建", "江西", "山东", "河南", "湖北",
    "湖南", "广东", "广西", "海南", "四川", "贵州", "云南", "西藏", "陕西",
    "甘肃", "青海", "宁夏", "新疆", "香港", "澳门", "台湾",
)

# ── 地区分区键的「无证据」哨兵值（与 INDUSTRY_UNKNOWN 同一思路）──────────────
# 为什么必须显式落一个桶而不是留空：视图按 province 列做聚合与筛选，留空的那批
# 会被聚合条件（province<>''）丢掉——实测后果就是「覆盖地区 8 个、分布加总 94 条，
# 旁边的总数却是 2396 条」，同一屏两个数字对不上。落哨兵之后
# sum(地区分布) == count(同条件总数) 恒成立，而且「未知地区」本身就是一个可筛选、
# 可导出的补录清单（它不宣称任何真实地区，含义就是「现有证据判不出地区」）。
PROVINCE_UNKNOWN = "未知地区"

# 分区键规则版本（与 data_pool.REGION_RULE_VERSION 同一防自锁思路）：持久化的
# province 是**派生结果**而不是业务事实，词表/证据链升级后旧值就是错值。没有版本
# 标记就只能「空才补」，于是第一轮窄口径写下的省份会把分区键永久冻住——实测存量
# 池里既有「判据已判国外、分区键还留着旧口径省份」的矛盾记录，必须能整体重算。
#   1 = 只认中文省份名 + 显式 country 字段
#   2 = 四维证据链（英文中国地名 / 国名归一 / 国际区号 / 国码域名）+ 哨兵桶
#       + 与属地判据的一致性闸门（见 derive_province）
PARTITION_RULE_VERSION = "2"

# ── 地名词表（Silver 层唯一一份，派生覆盖率全部取自实测取证）─────────────────
# 存量池 55,219 条里旧实现只能派出 1,075 条（1.9%），实测根因分两类：
#   · 国外记录 50,964 条地址是当地语言/英文（"C/ Balmes 297, Barcelona, Spain"），
#     旧实现只认中文省份名 + 显式 country 字段（该字段 99.99% 是空的）；
#   · 国内记录里一大半地址是出口企业的英文写法（"No.43 Bailing Rd, Quzhou,
#     Zhejiang Province"、"Xiamen Software Park"）。
# 逐信号实测可救回条数（详见 _geo_probe 取证，已随本次修复删除）：
#   英文中国地名 2,271 / 地址中国名 2,905 / 国际电话区号 17,208 / 国码域名 24,065
# 词形与 data_pool 的属地判据表（_CN_LATIN_GEO_RE / _FOREIGN_COUNTRY_RE）由
# test_ingest_store_offline 做**双向同步断言**：判据识别得到的词，分区必须能归一；
# 分区表里新增的词，判据也必须认——两张表各自演进迟早会让「地区」和「国内外」
# 两个筛选条件互相矛盾（勾了国外却筛出省份），所以用测试钉住而不是靠人记。
_LATIN_PROVINCES: Dict[str, str] = {
    # 省级拉丁写法（优先于城市名，可消解 romanization 同形歧义）
    "beijing": "北京", "tianjin": "天津", "shanghai": "上海", "chongqing": "重庆",
    "hebei": "河北", "shanxi": "山西", "shaanxi": "陕西", "neimenggu": "内蒙古",
    "inner mongolia": "内蒙古", "liaoning": "辽宁", "jilin": "吉林",
    "heilongjiang": "黑龙江", "jiangsu": "江苏", "zhejiang": "浙江",
    "anhui": "安徽", "fujian": "福建", "jiangxi": "江西", "shandong": "山东",
    "henan": "河南", "hubei": "湖北", "hunan": "湖南", "guangdong": "广东",
    "guangxi": "广西", "hainan": "海南", "sichuan": "四川", "guizhou": "贵州",
    "yunnan": "云南", "xizang": "西藏", "tibet": "西藏", "gansu": "甘肃",
    "qinghai": "青海", "ningxia": "宁夏", "xinjiang": "新疆", "taiwan": "台湾",
    "hong kong": "香港", "hongkong": "香港", "macau": "澳门", "macao": "澳门",
}
# 按省分组写「城市拉丁写法」，同形词在构建期自动识别并**两边都不认**：
# 手工写一张 city→province 平铺表迟早漏掉歧义（台州/泰州、奉贤、玉林/榆林都
# 是同一拉丁写法），而猜错的分区键比空值更坏——它会让地区筛选给出错结果。
_LATIN_CITY_GROUPS: Dict[str, Tuple[str, ...]] = {
    "上海": ("pudong", "minhang", "jiading", "baoshan", "songjiang", "qingpu",
             "chongming", "jinshan", "fengxian"),
    "河北": ("shijiazhuang", "tangshan", "baoding", "cangzhou", "qinhuangdao",
             "xingtai", "handan", "zhangjiakou", "chengde", "langfang", "hengshui"),
    "山西": ("taiyuan", "datong", "changzhi", "jinzhong", "linfen", "yuncheng"),
    "内蒙古": ("hohhot", "baotou", "ordos"),
    "辽宁": ("dalian", "shenyang", "anshan", "jinzhou", "liaoyang", "panjin",
             "yingkou", "fushun", "benxi", "chaoyang", "lingyuan", "dashiqiao"),
    "吉林": ("changchun", "siping", "songyuan"),
    "黑龙江": ("harbin", "daqing", "mudanjiang", "qiqihar", "jiamusi"),
    "江苏": ("nanjing", "suzhou", "wuxi", "changzhou", "kunshan", "nantong",
             "xuzhou", "yangzhou", "zhenjiang", "yancheng", "lianyungang",
             "huai'an", "huaian", "hai'an", "changshu", "taicang", "rudong", "jingjiang",
             "jiangyin", "liyang", "yizheng", "rugao", "qidong", "peixian",
             "dafeng", "haimen", "tongzhou", "ganyu", "fengxian", "suyou"),
    "浙江": ("hangzhou", "ningbo", "wenzhou", "yiwu", "jiaxing", "huzhou",
             "shaoxing", "jinhua", "quzhou", "zhoushan", "deqing", "yuyao",
             "cixi", "pingyang", "haining", "linhai", "yuhuan", "taizhou",
             "ruian", "yongkang", "zhuji"),
    "安徽": ("hefei", "wuhu", "bengbu", "maanshan", "anqing", "tongling",
             "chuzhou", "chizhou", "xuancheng", "huaibei", "tianchang",
             "guangde", "wuwei", "lingbi", "beiliao"),
    "福建": ("xiamen", "quanzhou", "fuzhou", "putian", "zhangzhou", "longyan",
             "sanming", "ningde"),
    "江西": ("nanchang", "jiujiang", "ganzhou", "jingdezhen", "pingxiang",
             "shangrao", "xinyu"),
    "山东": ("qingdao", "jinan", "yantai", "weihai", "weifang", "dongying",
             "linyi", "zaozhuang", "jining", "zibo", "dezhou", "binzhou",
             "liaocheng", "heze", "shouguang", "longkou", "laizhou", "qingzhou",
             "rushan", "zhaoyuan", "penglai"),
    "河南": ("zhengzhou", "luoyang", "shangqiu", "hebi", "gongyi", "xingyang",
             "kaifeng", "anyang", "xinxiang", "xuchang", "nanyang", "puyang",
             "pingdingshan", "sanmenxia", "zhoukou", "luohe", "yongcheng"),
    "湖北": ("wuhan", "yichang", "xiangyang", "jingzhou", "shiyan", "huangshi"),
    "湖南": ("changsha", "zhuzhou", "hengyang", "shaoyang", "yueyang", "changde"),
    "广东": ("guangzhou", "shenzhen", "dongguan", "foshan", "zhongshan", "zhuhai",
             "shantou", "chaozhou", "nanhai", "shunde", "sanshui", "puning",
             "huizhou", "jiangmen", "zhanjiang", "maoming", "zhaoqing",
             "shaoguan", "meizhou", "shanwei", "yangjiang", "qingyuan",
             "yunfu", "heyuan"),
    "广西": ("nanning", "liuzhou", "guilin", "chongzuo", "yulin", "beihai",
             "wuzhou"),
    "海南": ("haikou", "sanya"),
    "四川": ("chengdu", "mianyang", "deyang", "yibin", "luzhou", "nanchong",
             "panzhihua", "leshan"),
    "贵州": ("guiyang", "zunyi", "anshun"),
    "云南": ("kunming", "dali", "qujing", "yuxi", "lijiang"),
    "陕西": ("xian", "xi'an", "baoji", "xianyang", "weinan", "yulin", "ankang",
             "yan'an"),
    "甘肃": ("lanzhou", "dunhuang", "jiuquan", "tianshui", "zhangye", "qingyang"),
    "青海": ("xining",),
    "宁夏": ("yinchuan",),
    "新疆": ("urumqi", "shihezi", "korla", "kashgar"),
    "西藏": ("lasa", "lhasa", "shigatse"),
    "台湾": ("taipei", "kaohsiung", "taichung", "tainan", "taoyuan", "hsinchu",
             "changhua", "nantou", "keelung", "miaoli", "yunlin", "pingtung",
             "hualien", "taitung", "chiayi"),
    "香港": ("kowloon", "tsuen wan", "sha tin", "tsim sha tsui", "tai po"),
}


def _build_city_map() -> Tuple[Dict[str, str], frozenset]:
    """展开分组表并自动剔除同形歧义词（构建期一次算好，运行期零判断成本）。"""
    mapping: Dict[str, str] = {}
    ambiguous = set()
    for prov, cities in _LATIN_CITY_GROUPS.items():
        if prov not in _PROVINCES:
            raise ValueError("地名词表里的省份不在 _PROVINCES 里: %r" % prov)
        for city in cities:
            if city in _LATIN_PROVINCES:
                continue          # 省级写法已覆盖，城市表不重复登记
            if mapping.get(city, prov) != prov:
                ambiguous.add(city)
                continue
            mapping.setdefault(city, prov)
    for city in ambiguous:
        mapping.pop(city, None)   # 两边都不认：宁可分区为空，也不猜一个省份
    return mapping, frozenset(ambiguous)


_LATIN_CITIES, _LATIN_AMBIGUOUS_CITIES = _build_city_map()

# ── 国名归一表（同一国家在分区键里只能有一个写法）────────────────────────────
# 存量数据里同一国家常有几种写法（"Spain"/"España"/"西班牙"、"USA"/"United States"），
# 不归一就会被拆成好几个桶——数仓里叫 conformed dimension：维度成员必须先对齐，
# 后面的分布、筛选、同地区推荐才是同一个东西。认不出的写法原样返回（绝不臆造）。
# 键一律是「去重音 + 小写」后的形态（见 _fold_latin），词表覆盖 data_pool
# _FOREIGN_COUNTRY_RE 的全部词形（由测试双向核对，两张表不许各自漂移）。
_COUNTRY_TO_CN: Dict[str, str] = {
    # 美洲
    "united states": "美国", "usa": "美国", "united states of america": "美国",
    "canada": "加拿大", "mexico": "墨西哥", "brazil": "巴西", "brasil": "巴西",
    "argentina": "阿根廷", "chile": "智利", "peru": "秘鲁", "colombia": "哥伦比亚",
    "venezuela": "委内瑞拉", "ecuador": "厄瓜多尔", "uruguay": "乌拉圭",
    "paraguay": "巴拉圭", "costa rica": "哥斯达黎加", "panama": "巴拿马",
    "guatemala": "危地马拉", "honduras": "洪都拉斯", "dominican": "多米尼加",
    "dominican republic": "多米尼加", "cuba": "古巴", "jamaica": "牙买加",
    "trinidad": "特立尼达和多巴哥", "el salvador": "萨尔瓦多",
    "nicaragua": "尼加拉瓜", "bolivia": "玻利维亚",
    # 欧洲
    "germany": "德国", "deutschland": "德国", "france": "法国",
    "frankreich": "法国", "spain": "西班牙", "espana": "西班牙",
    "espagne": "西班牙", "portugal": "葡萄牙", "netherlands": "荷兰",
    "holland": "荷兰", "belgium": "比利时", "belgie": "比利时",
    "austria": "奥地利", "oostenrijk": "奥地利", "oesterrijk": "奥地利",
    "switzerland": "瑞士", "schweiz": "瑞士", "sweden": "瑞典",
    "sverige": "瑞典", "norway": "挪威", "norge": "挪威", "denmark": "丹麦",
    "danmark": "丹麦", "finland": "芬兰", "suomi": "芬兰", "poland": "波兰",
    "polska": "波兰", "czech": "捷克", "czechia": "捷克", "cesko": "捷克",
    "czech republic": "捷克", "slovakia": "斯洛伐克", "hungary": "匈牙利",
    "magyarorszag": "匈牙利", "romania": "罗马尼亚", "bulgaria": "保加利亚",
    "croatia": "克罗地亚", "serbia": "塞尔维亚", "slovenia": "斯洛文尼亚",
    "bosnia": "波黑", "montenegro": "黑山", "macedonia": "北马其顿",
    "albania": "阿尔巴尼亚", "greece": "希腊", "ellada": "希腊",
    "italy": "意大利", "italia": "意大利", "italien": "意大利",
    "ireland": "爱尔兰", "eire": "爱尔兰", "england": "英国",
    "scotland": "英国", "wales": "英国", "united kingdom": "英国", "uk": "英国",
    "britain": "英国", "great britain": "英国", "georgia": "格鲁吉亚",
    "armenia": "亚美尼亚", "azerbaijan": "阿塞拜疆", "russia": "俄罗斯",
    "rossiya": "俄罗斯", "ukraine": "乌克兰", "belarus": "白俄罗斯",
    "moldova": "摩尔多瓦", "luxembourg": "卢森堡", "malta": "马耳他",
    "cyprus": "塞浦路斯", "iceland": "冰岛", "estonia": "爱沙尼亚",
    "latvia": "拉脱维亚", "lithuania": "立陶宛",
    # 中东 / 非洲
    "turkey": "土耳其", "turkiye": "土耳其", "israel": "以色列", "jordan": "约旦",
    "lebanon": "黎巴嫩", "egypt": "埃及", "morocco": "摩洛哥",
    "algeria": "阿尔及利亚", "tunisia": "突尼斯", "libya": "利比亚",
    "nigeria": "尼日利亚", "kenya": "肯尼亚", "ghana": "加纳",
    "ethiopia": "埃塞俄比亚", "zambia": "赞比亚", "zimbabwe": "津巴布韦",
    "tanzania": "坦桑尼亚", "uganda": "乌干达", "south africa": "南非",
    "mozambique": "莫桑比克", "namibia": "纳米比亚", "senegal": "塞内加尔",
    "oman": "阿曼", "qatar": "卡塔尔", "uae": "阿联酋",
    "united arab emirates": "阿联酋", "emirates": "阿联酋",
    "saudi": "沙特阿拉伯", "saudi arabia": "沙特阿拉伯", "bahrain": "巴林",
    "kuwait": "科威特", "palestine": "巴勒斯坦",
    # 亚太
    "japan": "日本", "korea": "韩国", "south korea": "韩国",
    "republic of korea": "韩国", "singapore": "新加坡", "malaysia": "马来西亚",
    "thailand": "泰国", "vietnam": "越南", "viet nam": "越南",
    "indonesia": "印度尼西亚", "myanmar": "缅甸", "philippines": "菲律宾",
    "india": "印度", "bharat": "印度", "pakistan": "巴基斯坦",
    "bangladesh": "孟加拉国", "nepal": "尼泊尔", "srilanka": "斯里兰卡",
    "sri lanka": "斯里兰卡", "australia": "澳大利亚", "new zealand": "新西兰",
    "fiji": "斐济", "samoa": "萨摩亚", "kazakhstan": "哈萨克斯坦",
    "uzbekistan": "乌兹别克斯坦", "kyrgyzstan": "吉尔吉斯斯坦",
    "tajikistan": "塔吉克斯坦", "turkmenistan": "土库曼斯坦", "mongolia": "蒙古",
}

# 中国及其港澳台写法：这些值不产生「国家分区」，一律交给省份分区表达
_CN_COUNTRY_WORDS = frozenset({
    "中国", "中国大陆", "中华人民共和国", "cn", "china", "prc",
    "people's republic of china", "hong kong", "hongkong", "macau", "macao",
    "taiwan", "taiwan, china",
})
# 中日韩汉字（判定「地址里有没有中文成分」，用于给国名子串匹配加噪声护栏：
# 中国公司的主营/地址里出现外国国名极常见——「出口德国」不能把东莞判成德国）
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")

# ── 国际电话区号 → 国家（libphonenumber 的号码归属地思路，结构化强证据）───────
# 实测能救回 17,208 条国外记录（占判不出条数的 34%）。
# 只收**无歧义**区号：+1 覆盖北美二十多个辖区（NANP，美国/加拿大/加勒比分不清）、
# +7 覆盖俄罗斯与哈萨克斯坦，两者一律不用——分区键猜错比留空更坏。
_DIAL_TO_CN: Dict[str, str] = {
    "44": "英国", "33": "法国", "49": "德国", "39": "意大利", "34": "西班牙",
    "31": "荷兰", "32": "比利时", "43": "奥地利", "41": "瑞士", "46": "瑞典",
    "47": "挪威", "45": "丹麦", "358": "芬兰", "48": "波兰", "420": "捷克",
    "421": "斯洛伐克", "36": "匈牙利", "40": "罗马尼亚", "359": "保加利亚",
    "385": "克罗地亚", "381": "塞尔维亚", "386": "斯洛文尼亚", "30": "希腊",
    "353": "爱尔兰", "351": "葡萄牙", "380": "乌克兰", "375": "白俄罗斯",
    "373": "摩尔多瓦", "995": "格鲁吉亚", "374": "亚美尼亚", "994": "阿塞拜疆",
    "90": "土耳其", "972": "以色列", "962": "约旦", "961": "黎巴嫩",
    "970": "巴勒斯坦", "20": "埃及", "212": "摩洛哥", "213": "阿尔及利亚",
    "216": "突尼斯", "218": "利比亚", "234": "尼日利亚", "254": "肯尼亚",
    "233": "加纳", "251": "埃塞俄比亚", "263": "津巴布韦", "255": "坦桑尼亚",
    "256": "乌干达", "27": "南非", "258": "莫桑比克", "264": "纳米比亚",
    "221": "塞内加尔", "232": "塞拉利昂",
    "81": "日本", "82": "韩国", "65": "新加坡", "60": "马来西亚", "66": "泰国",
    "84": "越南", "62": "印度尼西亚", "95": "缅甸", "63": "菲律宾",
    "91": "印度", "92": "巴基斯坦", "880": "孟加拉国", "977": "尼泊尔",
    "94": "斯里兰卡", "61": "澳大利亚", "64": "新西兰", "679": "斐济",
    "993": "土库曼斯坦", "998": "乌兹别克斯坦", "996": "吉尔吉斯斯坦",
    "992": "塔吉克斯坦", "976": "蒙古", "968": "阿曼", "974": "卡塔尔",
    "971": "阿联酋", "966": "沙特阿拉伯", "973": "巴林", "965": "科威特",
    "52": "墨西哥", "55": "巴西", "54": "阿根廷", "56": "智利", "51": "秘鲁",
    "57": "哥伦比亚", "58": "委内瑞拉", "593": "厄瓜多尔", "598": "乌拉圭",
    "595": "巴拉圭", "506": "哥斯达黎加", "507": "巴拿马", "502": "危地马拉",
    "504": "洪都拉斯", "505": "尼加拉瓜", "591": "玻利维亚", "599": "库拉索",
    "354": "冰岛", "370": "立陶宛", "371": "拉脱维亚", "372": "爱沙尼亚",
    "356": "马耳他", "357": "塞浦路斯", "352": "卢森堡", "382": "黑山",
    "387": "波黑", "389": "北马其顿", "355": "阿尔巴尼亚",
}
# 中国港澳台区号单独登记：它们是省份而不是国家
_DIAL_TO_PROVINCE: Dict[str, str] = {
    "852": "香港", "853": "澳门", "886": "台湾", "86": "",
}
_DIAL_KEYS = sorted(list(_DIAL_TO_CN) + list(_DIAL_TO_PROVINCE), key=len, reverse=True)
_DIAL_RE = re.compile(r"^\s*(?:\+\s*|00)(%s)" % "|".join(re.escape(k) for k in _DIAL_KEYS))

# ── 国码域名后缀 → 国家（邮箱/官网，B2B 外贸名单里的最后一维弱证据）──────────
# 实测可救回 24,065 条（判不出条数的 45%）：.de/.uk/.au/.it/.nl 这些后缀在企业自有
# 域名里几乎是硬证据。刻意排除 .co（哥斯达黎加 vs 各国公司都爱用的 company 域）、
# .me（黑山 vs 个人站）、.io/.ai/.tv 这类通用技术域——与 data_pool「通用域不算
# 属地证据」的原则同一条。港澳台按省份登记（与区号表同一口径）。
_TLD_TO_CN: Dict[str, str] = {
    "de": "德国", "fr": "法国", "it": "意大利", "es": "西班牙", "nl": "荷兰",
    "be": "比利时", "pl": "波兰", "ch": "瑞士", "at": "奥地利", "se": "瑞典",
    "no": "挪威", "dk": "丹麦", "fi": "芬兰", "uk": "英国", "gb": "英国",
    "ie": "爱尔兰", "pt": "葡萄牙", "cz": "捷克", "sk": "斯洛伐克",
    "hu": "匈牙利", "ro": "罗马尼亚", "bg": "保加利亚", "gr": "希腊",
    "hr": "克罗地亚", "si": "斯洛文尼亚", "lt": "立陶宛", "lv": "拉脱维亚",
    "ee": "爱沙尼亚", "is": "冰岛", "ru": "俄罗斯", "ua": "乌克兰",
    "kz": "哈萨克斯坦", "tr": "土耳其", "il": "以色列", "za": "南非",
    "jp": "日本", "kr": "韩国", "sg": "新加坡", "my": "马来西亚",
    "th": "泰国", "vn": "越南", "id": "印度尼西亚", "in": "印度",
    "pk": "巴基斯坦", "bd": "孟加拉国", "au": "澳大利亚", "nz": "新西兰",
    "ca": "加拿大", "mx": "墨西哥", "br": "巴西", "ar": "阿根廷",
    "cl": "智利", "pe": "秘鲁", "ve": "委内瑞拉", "eg": "埃及",
    "sa": "沙特阿拉伯", "ae": "阿联酋", "qa": "卡塔尔", "kw": "科威特",
    "ma": "摩洛哥", "ng": "尼日利亚", "ke": "肯尼亚", "lu": "卢森堡",
    "mt": "马耳他", "cy": "塞浦路斯", "rs": "塞尔维亚", "ba": "波黑",
    "by": "白俄罗斯", "md": "摩尔多瓦",
}
_TLD_TO_PROVINCE: Dict[str, str] = {"hk": "香港", "mo": "澳门", "tw": "台湾"}

# 去重音映射（NFKD 拆不掉的连字；Lucene ASCIIFoldingFilter 同一段规则）
_FOLD_SPECIAL = str.maketrans({"ß": "ss", "æ": "ae", "œ": "oe", "ø": "o",
                               "đ": "d", "ł": "l", "ﬁ": "fi", "ﬂ": "fl",
                               "ı": "i", "ș": "s", "ț": "t"})


def _fold_latin(text: str) -> str:
    """拉丁写法归一：去结合音标 + 展开连字 + 小写。

    不做这一步，词表写多少别名都漏——数据里的地址是 "Türkiye"、"España"、
    "Cerdanyola del Vallès" 这种带重音的原文写法，而 "székelyhíd" 里藏着
    连字符和双尖音符。中文与数字原样返回，所以本函数可以安全用在整段 blob 上。
    """
    s = unicodedata.normalize("NFKD", str(text or ""))
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return s.translate(_FOLD_SPECIAL).lower()


def _word_re(words) -> "re.Pattern":
    """词表 → 整词正则（长词排在前面，避免 united states 被 us 截走一半）。"""
    parts = sorted((re.escape(str(w)) for w in words), key=len, reverse=True)
    return re.compile(r"\b(?:" + "|".join(parts) + r")\b")


_LATIN_PROVINCE_RE = _word_re(_LATIN_PROVINCES)
_LATIN_CITY_RE = _word_re(_LATIN_CITIES)
_COUNTRY_RE = _word_re(_COUNTRY_TO_CN)


def is_china_country(text: str) -> bool:
    """country 字段是不是「中国（含港澳台）」——只认这张表，不在别处再抄一份。"""
    s = str(text or "").strip()
    return bool(s) and (s in _CN_COUNTRY_WORDS or _fold_latin(s) in _CN_COUNTRY_WORDS)


def normalize_country(text: str) -> str:
    """国名归一：别名/当地语/带重音写法 → 规范中文名；认不出原样返回。"""
    s = str(text or "").strip()
    if not s:
        return ""
    folded = _fold_latin(s)
    hit = _COUNTRY_TO_CN.get(folded)
    if hit:
        return hit
    m = _COUNTRY_RE.search(folded)          # "Kingdom of Thailand" → thailand
    if m:
        return _COUNTRY_TO_CN[m.group(0)]
    if _CJK_RE.search(s):
        return s                            # 已经是中文写法，原样保留
    return s


def _country_from_text(folded: str) -> str:
    """文本里的国名：取**最后一个**匹配。

    英文/西文地址把国家写在末尾（"…Barcelona, Cataluña, Spain"），而开头往往是
    街道或城市；「Georgia, USA」这种取首个会判成格鲁吉亚，取末位才对。
    """
    hits = list(_COUNTRY_RE.finditer(folded))
    return _COUNTRY_TO_CN[hits[-1].group(0)] if hits else ""


def _geo_from_phones(rec: dict) -> str:
    """号码前缀派生（国际区号 → 国家/省份）；只看带 +/00 的写法，绝不猜裸号码。

    号码先按 data_pool._domestic_signals 的同样方式去掉分隔符再匹配：数据里
    "00 81-3-1234"、"+49 (0)30 1234" 这种写法很常见，不归一就会出现「属地判据
    认它是境外号码、分区键却派不出国家」的矛盾（勾国外筛出未知地区）。两层用同
    一个归一规则，才能用同一个号码得出同一个结论。
    """
    values: List[str] = []
    for key in ("mobiles", "phones", "landlines"):
        values.extend(str(x) for x in (rec.get(key) or []))
    values.append(str(rec.get("phone") or ""))
    for v in values:
        m = _DIAL_RE.match(re.sub(r"[\s\-().]", "", str(v).strip()))
        if not m:
            continue
        code = m.group(1)
        out = _DIAL_TO_PROVINCE.get(code) or _DIAL_TO_CN.get(code) or ""
        if out:
            return out
    return ""


def _geo_from_domains(rec: dict) -> str:
    """邮箱/官网的国码后缀派生（最后一级证据，通用域一律不认）。"""
    domains: List[str] = []
    for e in list(rec.get("emails") or []) + [str(rec.get("email") or "")]:
        s = str(e).strip().lower()
        if "@" in s:
            domains.append(s.rsplit("@", 1)[-1])
    site = str(rec.get("website") or "").strip().lower()
    if site:
        site = re.sub(r"^[a-z]+://", "", site)
        domains.append(site.split("/", 1)[0].split(":", 1)[0])
    for domain in domains:
        if not domain or "." not in domain:
            continue
        tld = domain.rsplit(".", 1)[-1].strip()
        out = _TLD_TO_PROVINCE.get(tld) or _TLD_TO_CN.get(tld) or ""
        if out:
            return out
    return ""


def derive_province(rec: dict, region: Optional[str] = None) -> str:
    """派生地区分区键（国内=省份名，国外=规范国家名）；无证据返回空串。

    最高约束是**与属地判据（data_pool.record_region）自洽**：地区分区键一旦和
    「国内外」互相矛盾，勾「国外」筛出「广东」这种结果等于当场让用户不信数据。
    所以「是不是中国的地名」由属地判据先裁决，本函数只在裁决结果之内取值：
      · 国内记录：① 地址/国家里的中文省份名（业务事实，最硬）② 中国地名的英文
        写法（省级优先于城市级，可消解同形歧义）；到此为止——国内绝不允许从外文
        地址/号码推出「某个国家」（出口企业留着欧洲销售热线是常态，猜出来的分区
        键比空值更坏）；
      · 国外记录：③ 显式 country 字段（非中国）→ 归一成规范国名 ④ 纯外文地址里
        出现的国名（地址含中文时不用：「深圳市出口德国慕尼黑」不能判成德国）
        ⑤ 号码国际区号（无歧义表）⑥ 邮箱/官网国码后缀（最弱）。
    判不出返回空串，由 partition_province 落成 PROVINCE_UNKNOWN 哨兵桶。

    词表同步约束（详见模块头注释与 test_ingest_store_offline）：本表用到的省级
    拉丁写法与港澳台城市写法必须同时存在于 data_pool._CN_LATIN_GEO_WORDS，否则
    英文地址只写 "Tainan"、"Hsinchu" 的中国公司会被属地判据推到国外，而地区又
    只能落「未知地区」——实测存量池 111 条矛盾记录就是这个形状。
    """
    addr = str(rec.get("address") or "")
    country = str(rec.get("country") or "").strip()
    blob = addr + " " + country
    if region is None:
        region = _region_of(rec)
    folded = _fold_latin(blob)
    # 号码/域名这两维在港澳台上落的是**省份**（+852/.hk/…），与国内同侧；先把
    # 结果取出来，两个分支各取自己需要的那一侧，绝不跨侧取值
    geo_contact = _geo_from_phones(rec) or _geo_from_domains(rec)
    if region != "intl":
        for p in _PROVINCES:
            if p in blob:
                return p
        m = _LATIN_PROVINCE_RE.search(folded)
        if m:
            return _LATIN_PROVINCES[m.group(0)]
        m = _LATIN_CITY_RE.search(folded)
        if m:
            return _LATIN_CITIES[m.group(0)]
        # 国内记录到此为止：港澳台号码/域名仍是省份证据，其余（+49 之类）一律
        # 不用于从外文证据里造「某个国家」——出口企业留着欧洲销售热线是常态
        return geo_contact if geo_contact in _PROVINCES else ""
    # 国外记录：中国地名证据一律让位（属地判据已按更强的境外证据否定它），
    # 只在「国家」这一维里继续找证据，找不到就交给哨兵桶
    if country and not is_china_country(country):
        return normalize_country(country)
    if not _CJK_RE.search(addr):
        hit = _country_from_text(folded)
        if hit:
            return hit
    # 港澳台是省份：属地既已判国外，最后一维也不能再给出中国省份
    # （"+852 配美国地址"这类记录本来就自相矛盾，宁可留空让哨兵桶兜住，
    # 也不能让「国外」和「台湾」同时出现在筛选结果里）
    return geo_contact if geo_contact not in _PROVINCES else ""


def _region_of(rec: dict) -> str:
    """属地口径唯一实现（data_pool.record_region）；依赖缺失时按显式字段兜底。"""
    if _HAS_POOL_CORE:
        try:
            return record_region(rec)
        except Exception:
            pass
    return "dom" if rec.get("is_domestic", True) else "intl"


def partition_province(rec: dict, region: Optional[str] = None,
                       force: bool = False) -> str:
    """地区分区键的唯一实现：已落库的值 → 派生 → PROVINCE_UNKNOWN 哨兵。

    哨兵值等同于「缺失」：视图列、清洗报告、地区筛选、同地区推荐全部引用本函数，
    判不出地区的历史值下一轮词表补强后会自动重算（不会因为落过一次哨兵就冻住）。

    force=True 用于「属地刚刚发生变化」这一种情况：此时已落库的地区是**旧属地**
    下算出来的（或直接抄自某个来源），留着它就和新的属地自相矛盾；强制走
    derive_province，其内部的一致性闸门保证「属地=国外绝不给中国省份、属地=国内
    绝不给国家名」，判不出就落哨兵。不 force 时保持原语义：用户/来源已填的地区
    原样保留（幂等，也不覆盖显式值）。
    """
    prov = str(rec.get("province") or "").strip()
    if not force and prov and prov != PROVINCE_UNKNOWN:
        return prov
    return derive_province(rec, region) or PROVINCE_UNKNOWN


_COUNTRY_NAMES = frozenset(_COUNTRY_TO_CN.values())   # 归一后的中文国名全集


def _province_conflicts(prov: str, region: str) -> bool:
    """已落库的地区分区键是否与新属地**定义上矛盾**（只在两侧都认出地名时才判）。

    这是 partition_province 一致性闸门（derive_province 内部）对「已存值」的补齐：
    闸门只作用在现算的路径上，而历史值只要非空就会被原样保留，于是「属地=国外、
    地区=广东」这类记录能一路活到总表里（实测上传后对账抓到 2 条，合并后重算时
    放大到 24 条）。只认两边都是**已知地名**的组合，像「华南」「全国」这种自由
    文本不当矛盾处理——它们不宣称省份，与属地并不冲突，删掉才是丢用户数据。
    """
    if not prov or prov == PROVINCE_UNKNOWN:
        return False
    if region == "intl":
        return prov in _PROVINCES
    return prov in _COUNTRY_NAMES


def explicit_industry(rec: dict) -> str:
    """「行业」列归一后的显式结论；空串表示这一列等同于没填。

    两种情况算没填：值为空/为哨兵；值是「私营企业 / 个体经营 / 外资企业」这类
    经济类型（归一表把它们映射成空）——它们说的是企业性质，不是行业。
    判定只在这一处写，partition_industry 与存量补齐共用同一个口径。
    """
    ind = str(rec.get("industry") or "").strip()
    if not ind or ind == INDUSTRY_UNKNOWN:
        return ""
    return (_INDUSTRY_ALIASES.get(ind.lower(), ind) or "").strip()


def partition_industry(rec: dict) -> str:
    """行业分区键的唯一实现：显式值（含枚举脏值归一）→ 关键词派生 → 哨兵。

    历史上这里有第二份写法（概览回退路径把 classify_industry 判不出的记录直接
    丢掉，视图列却落「未分类」），同一批数据在筛选面板和概览卡片里报出两个行业数。

    显式值归一后为空时必须继续往下派生，绝不能把归一结果当结论返回。历史缺陷
    正是直接返回哨兵：同一条记录第一次清洗落「未分类」，第二次清洗（此时列里
    已经是「未分类」，按缺失重算）才落「环保与水处理」，于是导入预览报的数与
    真正入库的数是两套（清洗不幂等，下游一切「先预览再落库」的承诺都失效）。
    """
    mapped = explicit_industry(rec)
    if mapped and mapped != INDUSTRY_UNKNOWN:
        return mapped
    return (classify_industry(rec.get("products", ""), rec.get("company", ""),
                              rec.get("address", ""), rec.get("source", ""))
            or INDUSTRY_UNKNOWN)


# ── 行业分类表：行业名 → 证据关键词（一次正则扫描完成归类）──────────────────
# 覆盖用户实际导入的历史数据来源（新能源/深海装备/海洋/制冷净化/机械/五金等）
INDUSTRY_KEYWORDS: Dict[str, Tuple[str, ...]] = {
    "光伏太阳能": ("光伏", "太阳能", "solar", "组件", "硅片", "电池片", "逆变器", "支架", "背板"),
    "储能与电池": ("储能", "蓄电池", "锂电池", "电池", "UPS", "不间断电源", "电芯", "充放电", "battery"),
    "风电": ("风电", "风能", "风机", "叶片", "塔筒", "wind"),
    "新能源汽车": ("新能源汽车", "电动车", "充电桩", "动力电池", "汽车电子", "EV"),
    "制冷暖通": ("制冷", "冷库", "空调", "冷机", "热泵", "通风", "净化", "除尘", "暖通", "冷凝", "压缩机"),
    "深海装备与海洋": ("深海", "海洋", "船舶", "水下", "潜水", "救捞", "海上", "港口", "码头", "海工", "养殖"),
    "泵阀管道": ("泵", "阀", "管道", "管件", "水泵", "蝶阀", "闸阀", "离心"),
    "机械与机床": ("机械", "机床", "数控", "CNC", "模具", "自动化", "减速机", "轴承", "齿轮", "焊接", "激光"),
    "电子电气": ("电子", "电气", "PCB", "连接器", "传感器", "开关", "断路器", "继电器", "线缆", "变压器", "配电"),
    "半导体": ("半导体", "芯片", "集成电路", "晶圆", "封测"),
    "照明LED": ("LED", "照明", "灯饰", "光源", "显示屏", "灯珠"),
    "化工与材料": ("化工", "涂料", "油漆", "胶粘", "催化剂", "塑料", "橡胶", "薄膜", "树脂"),
    "金属与冶金": ("不锈钢", "钢铁", "铝", "铜", "合金", "镀锌", "钢材", "管材", "金属"),
    "建材与装饰": ("建材", "水泥", "玻璃", "陶瓷", "石材", "门窗", "幕墙", "砖"),
    "环保与水处理": ("环保", "污水", "废气", "净水", "处理", "清淤", "疏通", "化粪池", "脱硫", "除尘设备"),
    "仪器仪表": ("仪器", "仪表", "检测", "计量", "流量计", "压力表", "试验"),
    "安防消防": ("安防", "消防", "救援", "报警", "监控", "门禁", "灭火"),
    "医疗器械与医药": ("医疗", "医药", "制药", "生物", "器械", "试剂", "健康"),
    "食品饮料": ("食品", "饮料", "乳品", "调味", "烘焙", "酒"),
    "农业与农机": ("农业", "农机", "种子", "化肥", "农药", "灌溉", "养殖设备"),
    "纺织与服装": ("纺织", "面料", "服装", "服饰", "纱线", "印染", "拉链"),
    "包装印刷": ("包装", "印刷", "纸箱", "标签", "soft packaging"),
    "家具与家居": ("家具", "家居", "沙发", "床垫", "橱柜", "办公桌椅"),
    "五金工具": ("五金", "工具", "紧固件", "螺丝", "扳手", "刀具"),
    "橡塑制品": ("硅胶", "密封件", "轮胎", "注塑", "橡塑"),
    "物流与会展": ("物流", "货运", "供应链", "会展", "展览", "展会", "报关", "仓储"),
    "软件与信息服务": ("软件", "信息系统", "数据", "互联网", "技术外包", "SaaS", "信息技术"),
    "工程与建筑服务": ("工程", "建筑", "施工", "装饰工程", "机电安装", "总包"),
    "科研与教育": ("研究院", "研究所", "大学", "学院", "实验室", "高校"),
    "贸易与进出口": ("贸易", "进出口", "外贸", "跨境", "批发"),
}
_INDUSTRY_ALIASES: Dict[str, str] = {
    # 表头「行业」列里出现的枚举值直接归一到标准行业名
    "私营企业": "", "有限责任公司": "", "股份有限公司": "", "个体经营": "",
    "外资企业": "", "合资企业": "", "生产企业": "", "商业服务": "",
}

# 行业分区键的「无证据」哨兵值（口径唯一定义，视图与报告都引用这一个常量）。
# 为什么必须显式写出来而不是留空：数仓里维度缺失一律给明确的 Unknown 成员，
# 留 NULL 会让「分组 / 筛选 / 报告」各自决定怎么显示空值，同一批数据在清洗
# 报告里是「未分类 3795 条」、在行业筛选里却落在空值上筛不到（历史缺陷）。
# 它不宣称任何真实行业，含义就是「现有证据判不出行业」，可直接作为补录清单。
INDUSTRY_UNKNOWN = "未分类"

# 一次编译：命中任一关键词即归类（避免 per-industry 多次全文扫描）
_PATTERN_ALIASES: Dict[str, str] = dict(_INDUSTRY_ALIASES)
for _ind, _kws in INDUSTRY_KEYWORDS.items():
    for _kw in _kws:
        _PATTERN_ALIASES[_kw.lower()] = _ind

# CJK 统一表意文字（含扩展 A 与兼容区）：这些字符没有大小写概念
_CJK_RANGES = ((0x3400, 0x4DBF), (0x4E00, 0x9FFF), (0xF900, 0xFAFF))


def _case_fold_safe(term: str) -> bool:
    """该关键词能否用「文本先转小写 + 大小写敏感匹配」代替 re.IGNORECASE。

    IGNORECASE 在大alternation上是逐位置回溯试每个分支，实测比敏感匹配慢 4 倍；
    把输入一次性转小写后用敏感模式，既快又等价——但只对「纯小写 ASCII 字母 +
    CJK + 无大小写符号」的关键词能保证严格等价。带变音符/西里尔等字母的关键词
    （Unicode 特殊折叠：ß/ss、İ/i̇、ς/σ）可能与 str.lower() 结果不同，遇到就
    整体退回 IGNORECASE，宁可慢也不改变分类结果。
    """
    for ch in term:
        if not ch.isalpha():
            continue
        if ch.isascii():
            if ch.isupper():
                return False        # 键都已 .lower()，出现大写说明来源异常
            continue
        if not any(lo <= ord(ch) <= hi for lo, hi in _CJK_RANGES):
            return False
    return True


_ALTERNATIVES = "|".join(
    re.escape(k) for k in sorted(_PATTERN_ALIASES, key=len, reverse=True))
# 输入是否可以先转小写（True 时用敏感模式，快 ~4 倍；False 时保留 IGNORECASE）
_LOWER_INPUT_MATCH = all(_case_fold_safe(k) for k in _PATTERN_ALIASES)
_INDUSTRY_RE = re.compile(_ALTERNATIVES) if _LOWER_INPUT_MATCH else re.compile(
    _ALTERNATIVES, re.IGNORECASE)

# ── 长文本入库上限：超过这个长度基本是页面正文而非主营关键词
_MAX_TEXT_LEN = 300
_MAX_COMPANY_LEN = 120       # 公司名合理上限
_MIN_COMPANY_LEN = 2


# ─────────────────────────────────────────────────────────────────────────────
#  判定
# ─────────────────────────────────────────────────────────────────────────────

def is_valid_company_name(name: str) -> bool:
    """公司名是否是「一个企业/机构实体」，用于剔除页面残留与说明行。

    判据顺序（每一步都是低成本正则，可单测）：
      空/超短/超长 → 无效；URL/图片/文件路径 → 无效；纯数字或日期 → 无效；
      以导航词开头 → 无效；以栏目尾缀收尾（短名、或整串无组织尾缀）→ 无效（频道页/专题页名）；
      以句标点收尾 → 无效（说明表成句文字）；
      超长且含句读又不带任何组织尾缀 → 视为散文，无效。
    注意：不带中文组织尾缀的海外公司名（"Blackwell Energy"）依然有效——
    尾缀只作为「散文判定」的豁免证据，不作为必需条件。
    """
    s = str(name or "").strip()
    if len(s) < _MIN_COMPANY_LEN or len(s) > _MAX_COMPANY_LEN:
        return False
    if _URLISH_RE.match(s) or _IMAGE_RE.search(s):
        return False
    if _PATHISH_RE.match(s) and "/" in s:
        return False
    if _PURE_NUM_RE.match(s):
        return False
    if _DATE_RE.match(s):
        return False
    for head in _NAVI_HEADS:
        if s.startswith(head):
            return False
    # 栏目页名以频道/板块这类尾缀收尾：整串没有任何组织尾缀，或虽含组织
    # 字样但本身就是短导航词（「酒店频道」4 字）→ 不是实体。长名里恰好带
    # 「XX酒店管理有限公司」这类组织尾缀的仍放行。
    if s.endswith(_NAVI_TAILS) and (
            len(s) <= _NAVI_TAIL_SHORT_LEN or not _ORG_TAIL_RE.search(s)):
        return False
    if (len(s) > 40 and _PROSE_PUNCT_RE.search(s)
            and not _ORG_TAIL_RE.search(s)):
        return False
    # 成句的说明文字以句标点收尾（不看组织尾缀，句子里本来就常有「公司/企业」字样）
    if _SENTENCE_END_RE.search(s):
        return False
    return True


def gate_valid_names(records: List[dict]) -> Tuple[List[dict], int]:
    """读侧名称门禁：把存量记录再过一遍 is_valid_company_name，返回 (保留, 剔除数)。

    为什么上传时已经洗过，读取端还要洗一次（Bronze→Silver 重放）：
      · Bronze 是 append-only 原始层，名称判据升级（D11 说明表、D15 栏目名）
        不可能回头改写几千个历史对象——只能让 Silver 用**今天的规则**重算；
      · 少了这一步，同一份数据会出现「新上传被拦、存量还在池里」的口径分叉，
        界面上就是「搜索能搜到安防频道、重新导入却没有」这类说不清的问题；
      · 代价可控：判据是每条一次正则链，相对合并与建索引可忽略。

    与写侧同一份判据（绝不在这里再写一套规则），剔除数交给调用方播报/记日志。
    """
    is_valid = is_valid_company_name
    kept: List[dict] = []
    dropped = 0
    for rec in records or []:
        if isinstance(rec, dict) and is_valid(rec.get("company", "")):
            kept.append(rec)
        else:
            dropped += 1
    return kept, dropped


def strip_text_junk(text: str) -> Tuple[str, bool]:
    """截断混入正文的页面垃圾，返回 (干净文本, 是否被修过)。

    真实案例：深海装备文件「主营产品」列存的是整页抓取文本
    "管道,CCTV,检测,… 联系方式 海南君若排水服务有限公司 公司地址: … 手 机:13006076383 …"
    这类文本入索引会把无关公司也匹配上（号码/地址都进了倒排），必须截断。
    """
    s = str(text or "").strip()
    if not s:
        return "", False
    original = s
    m = _JUNK_MARK_RE.search(s)
    if m:
        s = s[:m.start()].strip(" ;；,，。、\t　")
    if len(s) > _MAX_TEXT_LEN:
        # 无垃圾标记但超长：按分隔符在限额内收尾，避免半截词
        cut = s[:_MAX_TEXT_LEN]
        for sep in ("，", ",", "；", ";", "、", " "):
            idx = cut.rfind(sep)
            if idx > _MAX_TEXT_LEN * 0.6:
                cut = cut[:idx]
                break
        s = cut.strip(" ;；,，。、\t　")
    return s, (s != original)


def classify_industry(*texts: str) -> str:
    """从若干文本里归类行业名（一次扫描投票，命中词最长者胜）。

    没有证据时返回空串——绝不臆造行业标签。
    """
    blob = " ".join(str(t or "") for t in texts)[:_MAX_TEXT_LEN * 2]
    if not blob.strip():
        return ""
    lowered = _LOWER_INPUT_MATCH
    if lowered:
        blob = blob.lower()
    hits: Dict[str, int] = {}
    for m in _INDUSTRY_RE.finditer(blob):
        kw = m.group(0) if lowered else m.group(0).lower()
        ind = _PATTERN_ALIASES.get(kw, "")
        if not ind:
            continue
        # 权重：命中词长度（"光伏组件" 比 "池" 更能代表主业）
        hits[ind] = hits.get(ind, 0) + len(kw)
    if not hits:
        return ""
    return max(hits.items(), key=lambda kv: (kv[1], len(kv[0])))[0]


# ─────────────────────────────────────────────────────────────────────────────
#  记录级清洗
# ─────────────────────────────────────────────────────────────────────────────

def _repair_phones(values: List[str]) -> Tuple[List[str], int]:
    """重组被空格/符号拆碎的号码，剔除碎片。

    真实案例：phones = ['(250)', '258-8344', '(587)', '+1', '250-258-8344', ...]
    ——上游按空白切分把 "+1 (250) 258-8344" 打成 6 个碎片，直接入库后
    外呼拨的是 "(250)"。规则：数字位数 < _MIN_PHONE_DIGITS 的碎片尝试与
    相邻碎片拼接；拼完仍不足位则丢弃并计数。
    """
    out: List[str] = []
    dropped = 0
    buf: List[str] = []

    def digits_of(s: str) -> str:
        return re.sub(r"\D", "", s)

    def flush():
        nonlocal dropped
        if not buf:
            return
        # 碎片拼接结果：" (250)" + " 258-8344" → "(250) 258-8344"，
        # 去掉括号残留并归一空白，保证二次清洗幂等（不会越洗越乱）
        merged = re.sub(r"[()　]", " ", "".join(buf))
        merged = re.sub(r"\s+", " ", merged).strip(" -+/")
        d = digits_of(merged)
        if len(d) >= _MIN_PHONE_DIGITS:
            out.append(merged)
        else:
            dropped += 1
        buf.clear()

    for raw in values or []:
        s = str(raw or "").strip()
        if not s:
            continue
        d = digits_of(s)
        if len(d) >= _MIN_PHONE_DIGITS:
            flush()
            out.append(s)
        else:
            buf.append(s if not buf else " " + s)
            if len(digits_of("".join(buf))) >= _MIN_PHONE_DIGITS:
                flush()
    flush()
    # 去重保序
    uniq: List[str] = []
    seen: set = set()
    for p in out:
        key = digits_of(p)
        if key and key in seen:
            dropped += 1
            continue
        if key:
            seen.add(key)
        uniq.append(p)
    return uniq, dropped


def _repair_contacts(rec: dict) -> int:
    """修联系人：把「填成公司名的联系人」清掉，返回修复条数。

    真实案例：数据池搜索导出的 18 列文件回灌时，「客户名称」列存的是公司名，
    于是 contacts=[{"name": "苏州快可光伏电子股份有限公司", "title": "应鹏飞"}]
    ——姓名与公司互换，外呼时称呼会全错。
    """
    fixed = 0
    company = str(rec.get("company") or "").strip()
    contacts = rec.get("contacts") or []
    new_contacts: List[dict] = []
    for c in contacts:
        if not isinstance(c, dict):
            continue
        name = str(c.get("name") or "").strip()
        title = str(c.get("title") or "").strip()
        # name 本身就是个企业实体名（等于公司名，或带组织尾缀且是公司名子串）
        # → 判定为「公司名错填进联系人栏」，真人姓名多半在 title 里
        name_is_company = bool(name) and bool(company) and (
            name == company
            or (len(name) >= 8 and name in company and _ORG_TAIL_RE.search(name))
        )
        if name_is_company:
            # title 是「真人姓名」才提到 name；是职位词（销售经理/Manager…）
            # 就宁可丢弃联系人，也绝不把职位当姓名去外呼
            if title and len(title) <= 6 and not _ROLE_RE.search(title):
                new_contacts.append({"name": title, "title": ""})
            fixed += 1
            continue
        new_contacts.append({"name": name, "title": title})
    if new_contacts != contacts:
        rec["contacts"] = new_contacts
        # 平铺键同步纠正（导出「客户姓名/职位」两列的来源）
        if new_contacts:
            rec["contact"] = new_contacts[0].get("name", "")
            rec["title"] = new_contacts[0].get("title", "") or rec.get("title", "")
        else:
            rec["contact"] = ""
    return fixed


def clean_one(rec: dict) -> Tuple[Optional[dict], str, Dict[str, int]]:
    """清洗单条记录。

    返回 (记录或 None, 剔除原因, 本次修复计数)。
    None + 原因 表示这条不该入库（原因必须落到报告里，不得静默）。
    """
    if not isinstance(rec, dict):
        return None, REASON_NO_NAME, {}
    fixes: Dict[str, int] = {}

    company = str(rec.get("company") or "").strip(" \t　\"'")
    # 公司名里残留的序号/文件名前缀（历史文件常见 "1. 某某公司"）
    company = re.sub(r"^\d{1,4}[\s.、,)）\-]+", "", company)
    if company != str(rec.get("company") or ""):
        fixes["公司名去前缀"] = 1
    # 结尾句标点：先分「抓取残留」和「成句说明文字」两类，能修的绝不扔。
    # "XX有限公司。" 这类以组织尾缀收尾的是标点残留，修掉标点保留实体
    # （ASCII 句点在这里也一并清理："3M Company." → "3M Company"）；
    # "…重复企业及重复号码不计入。" 去掉标点后仍不是实体名，留给下面的
    # 名称门禁按散文剔除（见 _SENTENCE_END_RE，它不看尾缀在句中还是句尾）。
    _tail = re.search(r"[。.！?？；;]+[ \t　]*$", company)
    if _tail:
        _core = company[:_tail.start()].strip(" \t　\"'")
        if _core and _ORG_TAIL_END_RE.search(_core):
            company = _core
            fixes["公司名结尾标点修正"] = 1
    rec["company"] = company
    if not company:
        return None, REASON_NO_NAME, {}
    if not is_valid_company_name(company):
        return None, REASON_BAD_NAME, {}
    rec["company_norm"] = rec.get("company_norm") or _normalize(company)

    # 主营/行业/备注：截断正文垃圾、把日期串从主营里赶出去
    for field in ("products", "industry", "address"):
        val = str(rec.get(field) or "")
        if val and _DATEISH_RE.match(val):
            rec[field] = ""
            fixes["日期串错位清除"] = 1
            continue
        cleaned, changed = strip_text_junk(val)
        if changed:
            rec[field] = cleaned
            fixes["正文垃圾截断"] = 1

    # 行业：口径唯一实现在 partition_industry（显式值优先 → 关键词派生 → 哨兵）；
    # 这里只负责「派生出来了就记一笔修复」，规则本体不在这重复一遍
    raw_industry = str(rec.get("industry") or "").strip()
    industry = partition_industry(rec)
    if industry != raw_industry and industry != INDUSTRY_UNKNOWN:
        fixes["行业派生"] = 1
    rec["industry"] = industry

    # 联系方式：碎片重组 + 手机/固话重新分类
    raw_phones = [str(p) for p in (rec.get("phones") or [])]
    repaired, dropped = _repair_phones(raw_phones)
    if repaired != raw_phones:
        fixes["号码碎片重组"] = 1
        if dropped:
            fixes["无效号码碎片剔除"] = dropped
    rec["phones"] = repaired
    mobiles = [p for p in repaired if _is_mobile(p)]
    landlines = [p for p in repaired if not _is_mobile(p)]
    rec["mobiles"] = mobiles or [m for m in (rec.get("mobiles") or []) if m]
    rec["landlines"] = landlines or [l for l in (rec.get("landlines") or []) if l]

    emails = [str(e).strip() for e in (rec.get("emails") or []) if str(e).strip()]
    emails = [e for e in emails if "@" in e and len(e) <= 100]
    if emails != (rec.get("emails") or []):
        fixes["邮箱格式修正"] = 1
    rec["emails"] = emails

    n_contacts = _repair_contacts(rec)
    if n_contacts:
        # 只在真发生纠正时记账：报告里出现「联系人纠正:0」这种条目，
        # 会让用户以为清洗动过联系人（历史缺陷：每个文件都固定显示 0 处）
        fixes["联系人纠正"] = n_contacts

    # 有效性门禁（口径唯一：五者取一，不含微信）
    if _HAS_POOL_CORE:
        if not _is_valid_record(rec):
            return None, REASON_NO_CONTACT, {}
    else:  # 依赖缺失时的等价兜底（打包态导入失败也不改变判据语义）
        if not (rec.get("phones") or rec.get("mobiles") or rec.get("landlines")
                or rec.get("emails") or str(rec.get("website") or "").strip()):
            return None, REASON_NO_CONTACT, {}

    # 分区派生（属地一律走 record_region，与搜索过滤/导出范围同一口径）
    region = _region_of(rec)
    rec["region"] = region
    rec["is_domestic"] = region == "dom"
    # 判据版本标记：写回的值只有带当前版本才允许被下一轮当快捷路径读，
    # 否则属地判据升级永远落不到存量数据上（详见 data_pool.REGION_RULE_VERSION）
    rec["region_rule"] = REGION_RULE_VERSION
    # 地区分区键走唯一实现（partition_province）：判不出的落 PROVINCE_UNKNOWN，
    # 这样「地区分布加总 == 同条件总数」恒成立，且「未知地区」可直接被筛出来补录
    rec["province"] = partition_province(rec, region)
    rec["province_rule"] = PARTITION_RULE_VERSION
    if _HAS_POOL_CORE:
        rec["quality"] = compute_quality_score(rec)
    return rec, "", fixes


def enrich_partition_fields(records: List[dict]) -> Dict[str, int]:
    """存量记录补齐分区键：industry/province 只填空缺不覆盖，属地按当前判据核齐。

    为什么必须补在记录上而不是补在查询里：物化视图靠 region/industry/province
    这些**真实列**做分区裁剪与聚合下压；历史池（清洗层上线前落盘的那批）这三列
    是空的，等于「行业/省份」维度根本没进过库——在查询时现算是每次搜索重跑一遍
    分类器，百万级不可接受。补齐后随总表落盘一次，之后零成本。

    属地为什么允许覆盖：region 是**判据的输出**而不是业务事实，判据升级后旧
    输出就是错值；用 region_rule 版本标记区分「当前判据算过的值」与「历史遗留
    值」，前者不动（幂等）、后者重算。行业/省份是分类器补的空缺，不覆盖用户
    已有的值。

    与 clean_one 同一套派生函数（classify_industry / derive_province /
    record_region / compute_quality_score），不新增任何第二份规则。
    返回各派生项的补算条数，供调用方打日志/展示。
    """
    counts: Dict[str, int] = {}

    def _bump(key: str):
        counts[key] = counts.get(key, 0) + 1

    for rec in records or []:
        if not isinstance(rec, dict):
            continue
        industry = str(rec.get("industry") or "").strip()
        # 「缺失」的判定与 partition_industry 同源（explicit_industry）：空值、哨兵、
        # 以及「私营企业」这类经济类型脏值都算没填，必须能重算——否则第一次落库
        # 写进去的值会把这条记录的分区键永久冻住，词表补强也追不上历史数据
        if not explicit_industry(rec):
            new_industry = partition_industry(rec)
            if new_industry != industry:
                rec["industry"] = new_industry
                _bump("行业补派生" if new_industry != INDUSTRY_UNKNOWN
                      else "行业置未分类")
        region = _region_of(rec)
        region_changed = False
        if _HAS_POOL_CORE:
            # 属地三件套（region / is_domestic / region_rule）必须整体一致才算
            # 「已按当前判据派生过」：只比 region 会让旧判据写下的 is_domestic
            # 一直留在记录里，而缺版本标记又让下一轮无从判断该不该重算——
            # 两者合起来就是属地判据无法升级的自锁（详见 REGION_RULE_VERSION 注释）。
            if (rec.get("region") != region
                    or rec.get("is_domestic") is not (region == "dom")
                    or str(rec.get("region_rule") or "") != REGION_RULE_VERSION):
                region_changed = str(rec.get("region") or "") not in ("", region)
                rec["region"] = region
                rec["is_domestic"] = region == "dom"
                rec["region_rule"] = REGION_RULE_VERSION
                _bump("属地补派生")
        prov = str(rec.get("province") or "").strip()
        # 两种情况必须强制重算（force），否则留下定义上自相矛盾的记录：
        #   · 属地本轮变了——合并多来源取并集、或属地判据升级都会翻侧；
        #   · 已存地区与新属地本来就是矛盾组合（国外记录挂中国省份 / 国内记录挂国名）。
        # 后果是界面上勾「国外」和勾「广东」两条筛选互相抵消，这条数据永远查不出来
        # （真实取证：Renova 同时有深圳地址与 .it 域名；实测上传后对账 2 条 → 合并
        # 重算后放大到 24 条，就是因为过去只翻属地不翻地区）。
        force_rederive = region_changed or _province_conflicts(prov, region)
        if (force_rederive
                or str(rec.get("province_rule") or "") != PARTITION_RULE_VERSION
                or not prov or prov == PROVINCE_UNKNOWN):
            # 版本不齐 = 旧口径算出来的空值/哨兵一律按当前证据链补算（判据升级能落到
            # 存量数据上）；同一版本内不覆盖用户自己填的、且与属地不矛盾的地区，保持幂等
            new_prov = partition_province(rec, region, force=force_rederive)
            if new_prov != prov:
                rec["province"] = new_prov
                if region_changed:
                    _bump("地区随属地重算")
                elif _province_conflicts(prov, region):
                    _bump("地区矛盾修正")
                else:
                    _bump("省份补派生" if new_prov != PROVINCE_UNKNOWN else "地区置未知")
            else:
                rec["province"] = new_prov
        rec["province_rule"] = PARTITION_RULE_VERSION
        if _HAS_POOL_CORE:
            rec["quality"] = compute_quality_score(rec)
    return counts


def merge_reports(target: Dict[str, Any], other: Dict[str, Any]) -> Dict[str, Any]:
    """把多份清洗报告合并成一份（多文件导入 = 一次用户可见的汇总）。

    逐文件各报各的 = 用户要读十几遍同样的字段，也看不出「这一批总共
    被剔了多少」。计数类求和，映射类按键累加，样本类拼接后截断，
    被剔整份（dropped_rows）拼接后按 DROP_ROW_CAP 截断并把超出量记进
    dropped_overflow——汇总报告只能比真实数更少报，不能更多。
    target 原地更新并返回；target 为空字典时结果与 other 同构。
    """
    if not other:
        return target
    for key in ("in", "kept", "dropped", "with_phone", "with_email",
                "with_website", "dropped_overflow"):
        target[key] = int(target.get(key, 0)) + int(other.get(key, 0))
    for key in ("drop_reasons", "fixed", "region", "province", "industry"):
        merged = dict(target.get(key) or {})
        for name, count in (other.get(key) or {}).items():
            merged[name] = merged.get(name, 0) + count
        target[key] = merged
    for key in ("dropped_samples", "skipped_sheets"):
        merged = list(target.get(key) or [])
        merged.extend(other.get(key) or [])
        if key == "dropped_samples":
            merged = merged[:20]
        target[key] = merged
    rows = list(target.get("dropped_rows") or [])
    rows.extend(other.get("dropped_rows") or [])
    if len(rows) > DROP_ROW_CAP:
        target["dropped_overflow"] = int(target.get("dropped_overflow", 0)) \
            + len(rows) - DROP_ROW_CAP
        rows = rows[:DROP_ROW_CAP]
    target["dropped_rows"] = rows
    return target


def _dropped_row(rec: Any, reason: str) -> Dict[str, str]:
    """被剔记录 → 未入库清单的一行（只留「用户拿去核对」的字段，全转字符串）。

    为什么连号码、地址、主营都要带上：这四类信息决定了该行是「列映射选错了」
    还是「文件本来就没号码」——前者要回去改映射重导，后者是补采名单的输入，
    两者的下一步动作完全不同，只给公司名等于让用户自己再猜一遍。
    """
    def _j(v: Any) -> str:
        if isinstance(v, (list, tuple, set)):
            return ",".join(str(x).strip() for x in v if str(x).strip())
        return str(v or "").strip()

    if not isinstance(rec, dict):
        return {"company": _j(rec)[:120], "reason": reason}
    return {
        "company": _j(rec.get("company"))[:120],
        "reason": reason,
        "source": _j(rec.get("source"))[:120],
        "mobiles": _j(rec.get("mobiles"))[:200],
        "phones": _j(rec.get("phones"))[:200],
        "landlines": _j(rec.get("landlines"))[:200],
        "emails": _j(rec.get("emails"))[:200],
        "wechats": _j(rec.get("wechats"))[:200],
        "website": _j(rec.get("website"))[:200],
        "address": _j(rec.get("address"))[:200],
        "products": _j(rec.get("products"))[:200],
    }


def clean_records(records: List[dict]) -> Tuple[List[dict], Dict[str, Any]]:
    """批量清洗 + 汇总报告（上传前与入库前共用同一入口）。

    report 字段：
      in/kept/dropped、drop_reasons（原因→条数）、fixed（修复类型→次数）、
      region（dom/intl 条数）、industry（行业→条数）、province（省份→条数）、
      with_phone/with_email/with_website、dropped_samples（前 20 条被剔记录，可回溯）、
      dropped_rows（被剔记录**整份**，最多 DROP_ROW_CAP 条，供导出未入库清单）、
      dropped_overflow（超出上限没记进清单的被剔条数，绝不静默截断）。
    """
    kept: List[dict] = []
    report: Dict[str, Any] = {
        "in": len(records or []), "kept": 0, "dropped": 0,
        "drop_reasons": {}, "fixed": {}, "region": {"dom": 0, "intl": 0},
        "industry": {}, "province": {},
        "with_phone": 0, "with_email": 0, "with_website": 0,
        "dropped_samples": [], "dropped_rows": [], "dropped_overflow": 0,
    }
    for rec in records or []:
        cleaned, reason, fixes = clean_one(rec)
        if cleaned is None:
            report["dropped"] += 1
            report["drop_reasons"][reason] = report["drop_reasons"].get(reason, 0) + 1
            if len(report["dropped_rows"]) < DROP_ROW_CAP:
                row = _dropped_row(rec, reason)
                report["dropped_rows"].append(row)
                if len(report["dropped_samples"]) < 20:
                    report["dropped_samples"].append((row["company"][:40], reason))
            else:
                report["dropped_overflow"] += 1
            continue
        for k, v in (fixes or {}).items():
            report["fixed"][k] = report["fixed"].get(k, 0) + v
        kept.append(cleaned)
        # 属地按口径唯一实现 record_region 取（clean_one 已写 _region_key 缓存，
        # 这里只是一次字典读）：绝不用 .get(..., "dom") 这种默认值把「缺键」
        # 悄悄算成国内——缺键就该由同一个判据现场推导
        reg = _region_of(cleaned)
        report["region"][reg] = report["region"].get(reg, 0) + 1
        key = str(cleaned.get("industry") or "").strip() or INDUSTRY_UNKNOWN
        report["industry"][key] = report["industry"].get(key, 0) + 1
        # 地区分布与行业分布同一口径：判不出的也进桶（PROVINCE_UNKNOWN），
        # 否则报告里「加总」比 kept 少一大截，用户会以为清洗悄悄丢了数据
        prov = str(cleaned.get("province") or "").strip() or PROVINCE_UNKNOWN
        report["province"][prov] = report["province"].get(prov, 0) + 1
        if cleaned.get("phones") or cleaned.get("mobiles") or cleaned.get("landlines"):
            report["with_phone"] += 1
        if cleaned.get("emails"):
            report["with_email"] += 1
        if str(cleaned.get("website") or "").strip():
            report["with_website"] += 1
    report["kept"] = len(kept)
    return kept, report


def sheet_usability(sheet_records: List[dict]) -> Tuple[bool, float]:
    """判断一张工作表是否「企业名单」（用于跳过进度表/说明表）。

    真实案例：海洋展企业数.xlsx 的「采集进度」表是配置说明（目标企业数 20000、
    优先地区…），旧链路把它当企业入库，产出 "无手机号、掩码手机号、地区不明或
    非沿海、业务不匹配、重复企业及重复号码不计入。" 这种「公司」。
    判据：有效实体名占比 >= 0.5 才算数据表（小样本 <5 行时保守放行，
    交给记录级门禁兜底），返回 (是否可用, 有效占比)。
    """
    if not sheet_records:
        return False, 0.0
    good = sum(1 for r in sheet_records
               if is_valid_company_name(str(r.get("company") or "")))
    ratio = good / float(len(sheet_records))
    if len(sheet_records) < 5:
        return True, ratio
    return ratio >= 0.5, ratio


def format_report(report: Dict[str, Any], top_industries: int = 6,
                  samples: int = 8) -> str:
    """把清洗报告渲染成给用户看的中文摘要（上传前预览用）。

    包含整表跳过与被剔记录样例：被剔除的数据必须能回溯到具体是哪张表、
    哪一行，否则用户无法判断是文件本身有问题还是列映射选错了。
    """
    if not report:
        return ""
    lines: List[str] = []
    lines.append("读取 %d 条 → 可入库 %d 条，剔除 %d 条" % (
        report.get("in", 0), report.get("kept", 0), report.get("dropped", 0)))
    sk = report.get("skipped_sheets") or []
    if sk:
        lines.append("整表跳过（判定为说明表/进度表，不是企业名单）：" + "、".join(
            "%s（实体名占比 %.2f）" % (s.get("sheet", "?"),
                                       float(s.get("entity_ratio", 0)))
            for s in sk[:8]) + ("…" if len(sk) > 8 else ""))
    dr = report.get("drop_reasons") or {}
    if dr:
        lines.append("剔除原因：" + "、".join(
            "%s %d 条" % (k, v) for k, v in sorted(
                dr.items(), key=lambda kv: -kv[1])))
    fx = report.get("fixed") or {}
    if fx:
        lines.append("自动修复：" + "、".join(
            "%s %d 处" % (k, v) for k, v in sorted(
                fx.items(), key=lambda kv: -kv[1])))
    rg = report.get("region") or {}
    lines.append("属地分布：国内 %s 条 · 国外 %s 条" % (
        rg.get("dom", 0), rg.get("intl", 0)))
    prv = sorted((report.get("province") or {}).items(), key=lambda kv: -kv[1])
    if prv:
        known = [(k, v) for k, v in prv if k != PROVINCE_UNKNOWN]
        seg = "、".join("%s %d" % (k, v) for k, v in known[:top_industries])
        if len(known) > top_industries:
            seg += "…"
        unknown = dict(prv).get(PROVINCE_UNKNOWN, 0)
        if unknown:
            # 判不出地区必须说清楚是多少、为什么、怎么补：这批就是补录清单本身
            seg += ("；另有 %d 条地址/号码里判不出地区（记为「%s」，"
                    "可在筛选面板按它筛出后补地址）" % (unknown, PROVINCE_UNKNOWN))
        lines.append("地区分布：" + seg)
    ind = sorted((report.get("industry") or {}).items(), key=lambda kv: -kv[1])
    if ind:
        lines.append("行业分布：" + "、".join(
            "%s %d" % (k, v) for k, v in ind[:top_industries])
            + ("…" if len(ind) > top_industries else ""))
    lines.append("联系方式覆盖：电话 %d · 邮箱 %d · 官网 %d" % (
        report.get("with_phone", 0), report.get("with_email", 0),
        report.get("with_website", 0)))
    ds = report.get("dropped_samples") or []
    if ds and samples > 0:
        lines.append("被剔记录样例（可回溯核对列映射）：")
        lines.extend("  · %s —— %s" % (name or "(空名)", reason)
                     for name, reason in ds[:samples])
    dropped = int(report.get("dropped", 0))
    if dropped and report.get("dropped_rows") is not None:
        rows_n = len(report["dropped_rows"])
        over = int(report.get("dropped_overflow") or 0)
        seg = "被剔记录整份可导出核对：共 %d 条（「导出未入库清单」）" % (rows_n + over)
        if over:
            seg += "；受保留上限只留了前 %d 条，其余 %d 条只计数" % (rows_n, over)
        lines.append(seg)
    return "\n".join(lines)


# 未入库清单列定义：(报告键, 表头)。号码/邮箱/微信全部带上——被同一道门禁拦掉的
# 行，「列映射选错了」和「文件本来没号码」的处理动作完全不同，必须一眼能分开。
DROP_EXPORT_COLUMNS = (
    ("company", "公司名"),
    ("reason", "剔除原因"),
    ("source", "数据来源"),
    ("mobiles", "手机号"),
    ("landlines", "固话"),
    ("phones", "全部号码串"),
    ("emails", "邮箱"),
    ("wechats", "微信号"),
    ("website", "官网"),
    ("address", "地址"),
    ("products", "主营产品"),
)


def export_dropped_rows(report: Dict[str, Any], out_path: str) -> Tuple[int, str]:
    """把清洗报告里的被剔记录整份写成 Excel（未入库清单）。返回 (行数, 路径)。

    reject-file / dead-letter 口径：门禁拦下来的行必须有可交付的去处，否则
    「不静默丢数据」只是口号——报告里 20 条样例是用来判断口径的，清单才是给用户
    动手补的（改列映射重导，或者拿去喂三平台名单通道做精准补采）。
    第二个表「汇总」写清 in/kept/dropped/整表跳过的表名与实体名占比，
    让「为什么少了这些」在同一份文件里可自证。
    """
    from openpyxl import Workbook

    rows = list((report or {}).get("dropped_rows") or [])
    wb = Workbook()
    ws = wb.active
    ws.title = "未入库清单"
    ws.append([h for _, h in DROP_EXPORT_COLUMNS])
    for r in rows:
        ws.append([str((r or {}).get(k) or "") for k, _ in DROP_EXPORT_COLUMNS])

    s = wb.create_sheet("汇总")
    dropped = int((report or {}).get("dropped", 0))
    s.append(["项", "值"])
    s.append(["读取行数", int((report or {}).get("in", 0))])
    s.append(["可入库行数", int((report or {}).get("kept", 0))])
    s.append(["被剔除行数", dropped])
    s.append(["清单已列出行数", len(rows)])
    over = int((report or {}).get("dropped_overflow", 0))
    if over:
        s.append(["超出保留上限未列出行数", over])
    for name, count in sorted(((report or {}).get("drop_reasons") or {}).items(),
                              key=lambda kv: -kv[1]):
        s.append(["剔除原因：%s" % name, int(count)])
    for sk in (report or {}).get("skipped_sheets") or []:
        s.append(["整表跳过：%s" % str(sk.get("sheet") or "?"),
                  "实体名占比 %.2f" % float(sk.get("entity_ratio") or 0)])
    try:
        wb.save(out_path)
    except OSError as e:
        raise ValueError("未入库清单写入失败：%s" % e) from e
    return len(rows), out_path


# ── 内部小工具（延迟导入，避免循环依赖）───────────────────────────────────

def _normalize(name: str) -> str:
    try:
        from data_pool import normalize_company_name
        return normalize_company_name(name)
    except Exception:
        return (name or "").strip().lower()


def _is_mobile(num: Any) -> bool:
    try:
        from data_pool import _is_mobile_number
        return _is_mobile_number(num)
    except Exception:
        d = re.sub(r"\D", "", str(num or ""))
        return len(d) >= 11 and bool(re.match(r"1[3-9]\d{9}$", d[-11:]))
