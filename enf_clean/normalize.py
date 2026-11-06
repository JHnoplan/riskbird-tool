# -*- coding: utf-8 -*-
"""文本规范化 / 邮箱抽取 / 国家名->ISO 区域映射。

清洗原则：
1. 先做无损规范化（NFKC、HTML 实体、零宽字符、全角、各类连接符），再做业务解析；
2. 所有解析都只认"字段级"证据，绝不把公司名等其它字段回填到联系人字段；
3. 任何被丢弃的数据都会带原因进入审计统计，保证清洗可复盘。
"""
import html
import re
import unicodedata
from urllib.parse import unquote

# ----------------------------------------------------------------------------------
# 基础文本规范化
# ----------------------------------------------------------------------------------

# 零宽字符 / 方向控制符 / BOM
_INVISIBLE = dict.fromkeys(
    map(ord, "\u200b\u200c\u200d\u2060\ufeff\u202a\u202b\u202c\u202d\u202e\u200e\u200f\u00ad"),
    None,
)
# 各类横杠/破折号统一为 ASCII '-'
_DASHES = "\u2010\u2011\u2012\u2013\u2014\u2015\u2212\ufe58\ufe63\uff0d"  # 各类连接符
_DASH_TABLE = str.maketrans({c: "-" for c in _DASHES})
# 中文标点 -> 等价 ASCII 分隔符
_PUNCT_TABLE = str.maketrans({
    "\uff0c": ",", "\uff1b": ";", "\u3001": ",", "\uff1a": ":",
    "\uff08": "(", "\uff09": ")", "\uff5c": "|", "\uff0b": "+", "\uff0f": "/", "\u3000": " ",
})
_WS_RE = re.compile(r"[ \t\u00a0\u2007\u202f]+")
_MULTI_NL_RE = re.compile(r"[\r\n]+")
_URL_ESCAPE_RE = re.compile(r"%[0-9A-Fa-f]{2}")

# 各语种 Unicode 数字 -> ASCII 数字（NFKC 不会转换印度/阿拉伯数字，必须显式映射，
# 否则伊朗/印度站的 ۰۹۲۱…、०९२१… 号码无法与源数据对齐复核）
_DIGIT_BLOCKS = (
    "\u0660\u0661\u0662\u0663\u0664\u0665\u0666\u0667\u0668\u0669"      # 阿拉伯-印度数字
    "\u06f0\u06f1\u06f2\u06f3\u06f4\u06f5\u06f6\u06f7\u06f8\u06f9"      # 波斯数字
    "\u0966\u0967\u0968\u0969\u096a\u096b\u096c\u096d\u096e\u096f"      # 天城文
    "\u09e6\u09e7\u09e8\u09e9\u09ea\u09eb\u09ec\u09ed\u09ee\u09ef"      # 孟加拉
    "\u0a66\u0a67\u0a68\u0a69\u0a6a\u0a6b\u0a6c\u0a6d\u0a6e\u0a6f"      # 古尔穆基
    "\u0c66\u0c67\u0c68\u0c69\u0c6a\u0c6b\u0c6c\u0c6d\u0c6e\u0c6f"      # 泰卢固
    "\u0ce6\u0ce7\u0ce8\u0ce9\u0cea\u0ceb\u0cec\u0ced\u0cee\u0cef"      # 卡纳达
    "\u0d66\u0d67\u0d68\u0d69\u0d6a\u0d6b\u0d6c\u0d6d\u0d6e\u0d6f"      # 马拉雅拉姆
    "\u0e50\u0e51\u0e52\u0e53\u0e54\u0e55\u0e56\u0e57\u0e58\u0e59"      # 泰
    "\u1040\u1041\u1042\u1043\u1044\u1045\u1046\u1047\u1048\u1049"      # 缅甸
    "\u17e0\u17e1\u17e2\u17e3\u17e4\u17e5\u17e6\u17e7\u17e8\u17e9"      # 高棉
)
_DIGIT_TABLE = {ord(c): ord(str(i % 10)) for i, c in enumerate(_DIGIT_BLOCKS)}


def normalize_text(value):
    """无损文本规范化：去 HTML 实体、去不可见字符、统一连接符与空白、Unicode 数字转 ASCII。"""
    if value is None:
        return ""
    text = str(value)
    if not text.strip():
        return ""
    # HTML 实体（&amp; &#039; &nbsp; 等），爬虫类数据源常见
    if "&" in text:
        text = html.unescape(text)
    # URL 转义残留（如 %3e / %C3%A9）
    if _URL_ESCAPE_RE.search(text):
        text = unquote(text)
    # 全角 -> 半角（数字、字母、标点）
    text = unicodedata.normalize("NFKC", text)
    text = text.translate(_INVISIBLE)
    text = text.translate(_PUNCT_TABLE)
    text = text.translate(_DASH_TABLE)
    text = text.translate(_DIGIT_TABLE)
    text = text.replace("\u00a0", " ")
    text = _MULTI_NL_RE.sub("\n", text)
    text = _WS_RE.sub(" ", text)
    return text.strip()


def clean_company_name(value, max_len=120):
    """公司名清洗：只压缩空白/截断噪声，不做任何补全。"""
    text = normalize_text(value)
    if not text:
        return ""
    # 爬虫抓到的页面残留（脚本、导航文案）——按首个换行/脚本标记截断
    text = re.split(r"\n|<script|function\(|Terms of Use", text)[0]
    text = _WS_RE.sub(" ", text).strip(" -\t")
    if len(text) > max_len:
        text = text[:max_len].rstrip()
    return text


# ----------------------------------------------------------------------------------
# 邮箱
# ----------------------------------------------------------------------------------

# 邮箱：允许国际化域名的本地部分（含 é ö ü 等重音字母），但整体仍需通过结构校验
_EMAIL_RE = re.compile(
    r"(?![.+\-])"
    r"[A-Za-z0-9\u00c0-\u02af._%+\-]{1,64}"
    r"@"
    r"[A-Za-z0-9\u00c0-\u02af][A-Za-z0-9\u00c0-\u02af.\-]{0,61}[A-Za-z0-9\u00c0-\u02af]"
    r"\.[A-Za-z\u00c0-\u02af]{2,24}"
)
_PLACEHOLDER_DOMAINS = {
    "example.com", "examples.com", "domain.com", "test.com", "email.com",
    "yourdomain.com", "company.com", "mail.com", "abc.com",
}


def _valid_email(email):
    local, _, domain = email.rpartition("@")
    if not local or not domain:
        return False
    if ".." in local or local.startswith(".") or local.endswith("."):
        return False
    if "__" in local:
        return False
    if any(c in email for c in ("*", "#", "×")):
        return False
    if not re.fullmatch(r"[A-Za-z0-9\u00c0-\u02af.\-]+", domain):
        return False
    labels = domain.split(".")
    if len(labels) < 2 or any(not lb or lb.startswith("-") or lb.endswith("-") for lb in labels):
        return False
    if labels[-1].isdigit() or len(labels[-1]) < 2:
        return False
    if domain.lower() in _PLACEHOLDER_DOMAINS:
        return False
    return True


def extract_emails(value):
    """从任意文本中抽取合法邮箱，返回 (邮箱列表, 去除邮箱后的剩余文本)。"""
    text = normalize_text(value)
    if not text:
        return [], ""
    found, rest = [], text
    for m in _EMAIL_RE.finditer(text):
        cand = m.group(0).strip(".,-_+ ")
        # 结尾残缺的（sales@a.com,.au）按主域名收取
        if _valid_email(cand):
            found.append(cand)
            rest = rest.replace(m.group(0), " ")
    # 大小写不敏感去重，保留首次出现顺序与原始大小写
    uniq, seen = [], set()
    for e in found:
        key = e.lower()
        if key not in seen:
            seen.add(key)
            uniq.append(e)
    return uniq, _WS_RE.sub(" ", rest).strip(" ,;|/")


# ----------------------------------------------------------------------------------
# 国家名 -> ISO-3166 alpha-2（用于 libphonenumber 的默认区域提示）
# ----------------------------------------------------------------------------------

COUNTRY_REGION = {
    "afghanistan": "AF", "albania": "AL", "algeria": "DZ", "angola": "AO", "argentina": "AR",
    "armenia": "AM", "aruba": "AW", "australia": "AU", "austria": "AT", "azerbaijan": "AZ",
    "bahamas": "BS", "bahrain": "BH", "bangladesh": "BD", "barbados": "BB", "belarus": "BY",
    "belgium": "BE", "belize": "BZ", "benin": "BJ", "bermuda": "BM", "bhutan": "BT",
    "bolivia": "BO", "bosnia and herzegovina": "BA", "botswana": "BW", "brazil": "BR",
    "brunei darussalam": "BN", "bulgaria": "BG", "burkina faso": "BF", "burundi": "BI",
    "cambodia": "KH", "cameroon": "CM", "canada": "CA", "cape verde": "CV", "cayman islands": "KY",
    "chad": "TD", "chile": "CL", "china": "CN", "colombia": "CO", "costa rica": "CR",
    "croatia": "HR", "cuba": "CU", "cyprus": "CY", "czech republic": "CZ", "czechia": "CZ",
    "côte d'ivoire": "CI", "ivory coast": "CI", "dr congo": "CD",
    "democratic republic of the congo": "CD", "congo": "CG", "the republic of congo": "CG",
    "denmark": "DK", "djibouti": "DJ", "dominica": "DM", "dominican republic": "DO",
    "ecuador": "EC", "egypt": "EG", "el salvador": "SV", "estonia": "EE", "ethiopia": "ET",
    "fiji": "FJ", "finland": "FI", "france": "FR", "gabon": "GA", "gambia": "GM",
    "georgia": "GE", "germany": "DE", "ghana": "GH", "great britain": "GB",
    "greece": "GR", "grenada": "GD", "guadeloupe": "GP", "guam": "GU", "guatemala": "GT",
    "guinea": "GN", "guyana": "GY", "haiti": "HT", "honduras": "HN",
    "hong kong": "HK", "hong kong sar": "HK", "hong kong sar china": "HK",
    "hungary": "HU", "iceland": "IS", "india": "IN", "indonesia": "ID", "iran": "IR", "iraq": "IQ",
    "ireland": "IE", "isle of man": "IM", "israel": "IL", "italy": "IT", "jamaica": "JM",
    "japan": "JP", "jersey": "JE", "jordan": "JO", "kazakhstan": "KZ", "kenya": "KE",
    "korea": "KR", "south korea": "KR", "north korea": "KP", "kosovo": "XK", "kuwait": "KW",
    "kyrgyzstan": "KG", "latvia": "LV", "lebanon": "LB", "lesotho": "LS", "liberia": "LR",
    "libya": "LY", "liechtenstein": "LI", "lithuania": "LT", "luxembourg": "LU",
    "madagascar": "MG", "malawi": "MW", "malaysia": "MY", "maldives": "MV", "mali": "ML",
    "malta": "MT", "marshall islands": "MH", "martinique": "MQ", "mauritania": "MR",
    "mauritius": "MU", "mexico": "MX", "moldova": "MD", "mongolia": "MN", "montenegro": "ME",
    "morocco": "MA", "mozambique": "MZ", "myanmar": "MM", "namibia": "NA", "nepal": "NP",
    "netherlands": "NL", "new caledonia": "NC", "new zealand": "NZ", "nicaragua": "NI",
    "nigeria": "NG", "niue": "NU", "north macedonia": "MK", "norway": "NO", "oman": "OM",
    "pakistan": "PK", "palestine": "PS", "panama": "PA", "papua new guinea": "PG",
    "paraguay": "PY", "peru": "PE", "philippines": "PH", "poland": "PL", "portugal": "PT",
    "puerto rico": "PR", "qatar": "QA", "romania": "RO", "russia": "RU", "russian federation": "RU",
    "rwanda": "RW", "réunion": "RE", "saint kitts and nevis": "KN", "saint lucia": "LC",
    "saint martin": "MF", "saint vincent and the grenadines": "VC", "san marino": "SM",
    "saudi arabia": "SA", "senegal": "SN", "serbia": "RS", "seychelles": "SC",
    "sierra leone": "SL", "singapore": "SG", "slovakia": "SK", "slovenia": "SI",
    "solomon islands": "SB", "somalia": "SO", "south africa": "ZA", "south sudan": "SS",
    "spain": "ES", "sri lanka": "LK", "sudan": "SD", "suriname": "SR", "sweden": "SE",
    "switzerland": "CH", "syrian arab republic": "SY", "syria": "SY", "taiwan": "TW",
    "taiwan, china": "TW", "tajikistan": "TJ", "tanzania": "TZ", "thailand": "TH",
    "togo": "TG", "tonga": "TO", "trinidad and tobago": "TT", "tunisia": "TN", "turkey": "TR",
    "türkiye": "TR", "turks and caicos islands": "TC", "uae": "AE", "united arab emirates": "AE",
    "uganda": "UG", "ukraine": "UA", "united kingdom": "GB", "usa": "US",
    "united states": "US", "united states of america": "US",
    "united states virgin islands": "VI", "uruguay": "UY",
    "uzbekistan": "UZ", "vanuatu": "VU", "venezuela": "VE", "vietnam": "VN",
    "yemen": "YE", "zambia": "ZM", "zimbabwe": "ZW", "åland islands": "AX",
    # ── 中文国家名 ──
    "中国": "CN", "中华人民共和国": "CN", "中国大陆": "CN",
    "中国台湾": "TW", "中国香港": "HK", "中国澳门": "MO",
    "英国": "GB", "美国": "US", "德国": "DE", "法国": "FR", "日本": "JP",
    "韩国": "KR", "新加坡": "SG", "加拿大": "CA", "澳大利亚": "AU",
    "意大利": "IT", "西班牙": "ES", "巴西": "BR", "印度": "IN",
    "俄罗斯": "RU", "墨西哥": "MX", "印尼": "ID", "印度尼西亚": "ID",
    "马来西亚": "MY", "泰国": "TH", "越南": "VN", "菲律宾": "PH",
    "巴基斯坦": "PK", "孟加拉": "BD", "土耳其": "TR", "沙特": "SA",
    "沙特阿拉伯": "SA", "阿联酋": "AE", "埃及": "EG", "尼日利亚": "NG",
    "南非": "ZA", "肯尼亚": "KE", "阿根廷": "AR", "智利": "CL",
    "哥伦比亚": "CO", "秘鲁": "PE", "委内瑞拉": "VE", "厄瓜多尔": "EC",
    "巴拿马": "PA", "哥斯达黎加": "CR", "古巴": "CU", "多米尼加": "DO",
    "危地马拉": "GT", "洪都拉斯": "HN", "尼加拉瓜": "NI", "萨尔瓦多": "SV",
    "乌拉圭": "UY", "巴拉圭": "PY", "玻利维亚": "BO",
    "荷兰": "NL", "比利时": "BE", "卢森堡": "LU", "瑞士": "CH",
    "奥地利": "AT", "瑞典": "SE", "挪威": "NO", "丹麦": "DK",
    "芬兰": "FI", "冰岛": "IS", "爱尔兰": "IE", "葡萄牙": "PT",
    "希腊": "GR", "波兰": "PL", "捷克": "CZ", "匈牙利": "HU",
    "罗马尼亚": "RO", "保加利亚": "BG", "克罗地亚": "HR", "斯洛文尼亚": "SI",
    "塞尔维亚": "RS", "乌克兰": "UA", "白俄罗斯": "BY", "格鲁吉亚": "GE",
    "亚美尼亚": "AM", "阿塞拜疆": "AZ", "哈萨克斯坦": "KZ", "乌兹别克斯坦": "UZ",
    "伊朗": "IR", "伊拉克": "IQ", "以色列": "IL", "约旦": "JO",
    "黎巴嫩": "LB", "叙利亚": "SY", "卡塔尔": "QA", "科威特": "KW",
    "阿曼": "OM", "巴林": "BH", "塞浦路斯": "CY", "马耳他": "MT",
    "斯洛伐克": "SK", "拉脱维亚": "LV", "立陶宛": "LT", "爱沙尼亚": "EE",
    "新西兰": "NZ", "缅甸": "MM", "柬埔寨": "KH", "老挝": "LA",
    "尼泊尔": "NP", "斯里兰卡": "LK", "马尔代夫": "MV", "蒙古": "MN",
    "朝鲜": "KP", "台湾": "TW", "香港": "HK", "澳门": "MO",
    "坦桑尼亚": "TZ", "加纳": "GH", "塞内加尔": "SN", "喀麦隆": "CM",
    "埃塞俄比亚": "ET", "乌干达": "UG", "莫桑比克": "MZ", "安哥拉": "AO",
    "利比里亚": "LR", "塞拉利昂": "SL", "摩洛哥": "MA", "突尼斯": "TN",
    "阿尔及利亚": "DZ", "苏丹": "SD", "卢旺达": "RW", "布隆迪": "BI",
    "乍得": "TD", "马里": "ML", "尼日尔": "NE", "布基纳法索": "BF",
    "几内亚": "GN", "贝宁": "BJ", "多哥": "TG", "科特迪瓦": "CI",
    "刚果": "CG", "刚果民主共和国": "CD",
    "牙买加": "JM", "特立尼达": "TT", "巴巴多斯": "BB",
    "百慕大": "BM", "巴哈马": "BS",
    "文莱": "BN", "格鲁吉亚": "GE",
    # ── 韩文国家名 ──
    "중국": "CN", "한국": "KR", "일본": "JP",
    "스위스": "CH", "이탈리아": "IT", "프랑스": "FR",
    "핀란드": "FI", "홍콩": "HK", "대만": "TW",
    # ── ISO 双字母代码（小写 key，因 country_to_region 内部做了 .lower()）──
    "ae": "AE", "ao": "AO", "ar": "AR", "at": "AT", "au": "AU",
    "bb": "BB", "bd": "BD", "be": "BE", "bg": "BG", "bh": "BH",
    "bi": "BI", "bj": "BJ", "bm": "BM", "bn": "BN", "bo": "BO",
    "br": "BR", "bs": "BS", "bt": "BT", "bw": "BW", "by": "BY",
    "bz": "BZ", "ca": "CA", "ch": "CH", "cl": "CL", "cm": "CM",
    "cn": "CN", "co": "CO", "cr": "CR", "cu": "CU", "cy": "CY",
    "cz": "CZ", "de": "DE", "dk": "DK", "do": "DO", "dz": "DZ",
    "ec": "EC", "ee": "EE", "eg": "EG", "es": "ES", "et": "ET",
    "fi": "FI", "fr": "FR", "ga": "GA", "gb": "GB", "ge": "GE",
    "gh": "GH", "gr": "GR", "gt": "GT", "gy": "GY", "hk": "HK",
    "hn": "HN", "hr": "HR", "hu": "HU", "id": "ID", "ie": "IE",
    "il": "IL", "in": "IN", "iq": "IQ", "ir": "IR", "is": "IS",
    "it": "IT", "jm": "JM", "jo": "JO", "jp": "JP", "ke": "KE",
    "kh": "KH", "kr": "KR", "kw": "KW", "kz": "KZ", "la": "LA",
    "lb": "LB", "lk": "LK", "lr": "LR", "lt": "LT", "lu": "LU",
    "lv": "LV", "ly": "LY", "ma": "MA", "me": "ME", "mg": "MG",
    "mk": "MK", "ml": "ML", "mm": "MM", "mn": "MN", "mo": "MO",
    "mq": "MQ", "mr": "MR", "mt": "MT", "mu": "MU", "mv": "MV",
    "mw": "MW", "mx": "MX", "my": "MY", "mz": "MZ", "na": "NA",
    "ne": "NE", "ng": "NG", "ni": "NI", "nl": "NL", "no": "NO",
    "np": "NP", "nz": "NZ", "om": "OM", "pa": "PA", "pe": "PE",
    "ph": "PH", "pk": "PK", "pl": "PL", "pr": "PR", "pt": "PT",
    "py": "PY", "qa": "QA", "ro": "RO", "rs": "RS", "ru": "RU",
    "rw": "RW", "sa": "SA", "sd": "SD", "se": "SE", "sg": "SG",
    "si": "SI", "sk": "SK", "sn": "SN", "so": "SO", "ss": "SS",
    "sv": "SV", "sy": "SY", "td": "TD", "tg": "TG", "th": "TH",
    "tj": "TJ", "tn": "TN", "tr": "TR", "tt": "TT", "tw": "TW",
    "tz": "TZ", "ua": "UA", "ug": "UG", "us": "US", "uy": "UY",
    "uz": "UZ", "ve": "VE", "vn": "VN", "ye": "YE", "za": "ZA",
    "zm": "ZM", "zw": "ZW",
}


# 中国省市写法 → CN（国家/地区列可能出现省市区而非国名）
_CN_PROVINCE_CITY_RE = re.compile(
    r"^(?:北京|天津|上海|重庆|河北|山西|辽宁|吉林|黑龙江|江苏|浙江|安徽|福建|江西|山东|"
    r"河南|湖北|湖南|广东|海南|四川|贵州|云南|陕西|甘肃|青海|台湾|广西|内蒙古|西藏|"
    r"宁夏|新疆|香港|澳门)"
    r"(?:[\u4e00-\u9fff/\-\s]*)?$"
)


def country_to_region(value):
    """国家名 -> ISO 区域码；无法识别返回 None（此时只用国际前缀判断）。"""
    raw = value
    name = normalize_text(value).lower().strip(" .")
    if not name:
        return None
    if name in COUNTRY_REGION:
        return COUNTRY_REGION[name]

    # 中文省市写法（如 "上海市-上海"、"广东/深圳市"、"天津"）→ CN
    raw_stripped = str(raw).strip()
    if _CN_PROVINCE_CITY_RE.match(raw_stripped) or raw_stripped in ("天津", "上海", "北京", "重庆"):
        return "CN"

    # 扩展格式先处理（在正则去除非标点字符之前）：
    # "United Kingdom, England, London" → 取逗号前第一段
    raw_lower = normalize_text(raw).lower().strip()
    if "," in raw_lower:
        first_part = raw_lower.split(",")[0].strip()
        if first_part in COUNTRY_REGION:
            return COUNTRY_REGION[first_part]

    # 兼容 "China (Mainland)" / "Vietnam  越南" 之类的写法
    key = re.sub(r"\s*[\(（].*?[\)）]\s*", " ", name)
    key = re.sub(r"[^a-zà-ÿ\u4e00-\u9fff\uac00-\ud7af ]", " ", key)
    key = _WS_RE.sub(" ", key).strip()
    if key in COUNTRY_REGION:
        return COUNTRY_REGION[key]

    # "South Korea Busan" → 取第一段
    first_token = key.split(" ")[0]
    if first_token in COUNTRY_REGION:
        return COUNTRY_REGION[first_token]
    # 兜底：最后一段
    last_token = key.split(" ")[-1] if " " in key else ""
    if last_token in COUNTRY_REGION:
        return COUNTRY_REGION[last_token]
    return None


# ----------------------------------------------------------------------------------
# 区域线索推断（用于不带国际前缀的号码）
# ----------------------------------------------------------------------------------

# 域名后缀 -> 区域（只取最右两级，避免 .com.cn 误判）
_TLD_REGION = {
    "cn": "CN", "com.cn": "CN", "net.cn": "CN", "org.cn": "CN", "gov.cn": "CN", "ac.cn": "CN",
    "hk": "HK", "com.hk": "HK", "mo": "MO", "tw": "TW", "com.tw": "TW", "jp": "JP", "co.jp": "JP",
    "kr": "KR", "co.kr": "KR", "sg": "SG", "com.sg": "SG", "my": "MY", "com.my": "MY",
    "in": "IN", "co.in": "IN", "net.in": "IN", "org.in": "IN", "pk": "PK", "com.pk": "PK",
    "bd": "BD", "lk": "LK", "vn": "VN", "com.vn": "VN", "th": "TH", "co.th": "TH",
    "id": "ID", "co.id": "ID", "ph": "PH", "com.ph": "PH", "kh": "KH", "mm": "MM", "np": "NP",
    "de": "DE", "at": "AT", "ch": "CH", "fr": "FR", "it": "IT", "es": "ES", "pt": "PT",
    "nl": "NL", "be": "BE", "lu": "LU", "ie": "IE", "co.uk": "GB", "uk": "GB", "ac.uk": "GB",
    "dk": "DK", "se": "SE", "no": "NO", "fi": "FI", "is": "IS", "pl": "PL", "cz": "CZ",
    "sk": "SK", "hu": "HU", "ro": "RO", "bg": "BG", "gr": "GR", "hr": "HR", "si": "SI",
    "rs": "RS", "ba": "BA", "mk": "MK", "al": "AL", "lt": "LT", "lv": "LV", "ee": "EE",
    "ua": "UA", "by": "BY", "md": "MD", "ru": "RU", "kz": "KZ", "uz": "UZ", "ge": "GE",
    "us": "US", "ca": "CA", "mx": "MX", "com.mx": "MX", "gt": "GT", "cu": "CU", "do": "DO",
    "co": "CO", "com.co": "CO", "ve": "VE", "com.ve": "VE", "ec": "EC", "com.ec": "EC",
    "pe": "PE", "com.pe": "PE", "cl": "CL", "co.cl": "CL", "ar": "AR", "com.ar": "AR",
    "uy": "UY", "com.uy": "UY", "py": "PY", "com.py": "PY", "br": "BR", "com.br": "BR",
    "bo": "BO", "ni": "NI", "cr": "CR", "pa": "PA", "sv": "SV", "hn": "HN", "pr": "PR",
    "au": "AU", "com.au": "AU", "nz": "NZ", "co.nz": "NZ", "fj": "FJ",
    "za": "ZA", "co.za": "ZA", "ng": "NG", "com.ng": "NG", "ke": "KE", "co.ke": "KE",
    "eg": "EG", "com.eg": "EG", "ma": "MA", "co.ma": "MA", "tn": "TN", "dz": "DZ",
    "il": "IL", "co.il": "IL", "ae": "AE", "co.ae": "AE", "sa": "SA", "com.sa": "SA",
    "qa": "QA", "om": "OM", "jo": "JO", "lb": "LB", "iq": "IQ", "ir": "IR", "tr": "TR",
    "com.tr": "TR", "si": "SI", "cy": "CY", "mt": "MT", "gh": "GH", "tz": "TZ", "zm": "ZM",
    "zw": "ZW", "mw": "MW", "ug": "UG", "et": "ET", "sn": "SN", "mv": "MV", "mn": "MN",
}

# 地址关键词 -> 区域（先匹配长词，避免 "Korea" 命中 "North Korea" 之类）
_ADDRESS_KEYWORDS = [
    ("p.r.c", "CN"), ("prc", "CN"), ("china", "CN"), ("中国", "CN"), ("hong kong", "HK"),
    ("taiwan", "TW"), ("japan", "JP"), ("日本", "JP"), ("korea", "KR"), ("韩国", "KR"),
    ("singapore", "SG"), ("malaysia", "MY"), ("vietnam", "VN"), ("thailand", "TH"),
    ("india", "IN"), ("印度", "IN"), ("pakistan", "PK"), ("indonesia", "ID"),
    ("germany", "DE"), ("deutschland", "DE"), ("德国", "DE"), ("france", "FR"), ("frankreich", "FR"),
    ("italy", "IT"), ("italia", "IT"), ("spain", "ES"), ("espana", "ES"), ("portugal", "PT"),
    ("netherlands", "NL"), ("holland", "NL"), ("belgium", "BE"), ("luxembourg", "LU"),
    ("united kingdom", "GB"), ("england", "GB"), ("scotland", "GB"), ("wales", "GB"), ("london", "GB"),
    ("ireland", "IE"), ("denmark", "DK"), ("sweden", "SE"), ("norway", "NO"), ("finland", "FI"),
    ("poland", "PL"), ("czech", "CZ"), ("hungary", "HU"), ("romania", "RO"), ("bulgaria", "BG"),
    ("greece", "GR"), ("austria", "AT"), ("switzerland", "CH"), ("russia", "RU"), ("ukraine", "UA"),
    ("united states", "US"), ("usa", "US"), ("u.s.a", "US"), ("美国", "US"), ("canada", "CA"),
    ("mexico", "MX"), ("brazil", "BR"), ("brasil", "BR"), ("argentina", "AR"), ("chile", "CL"),
    ("colombia", "CO"), ("peru", "PE"), ("venezuela", "VE"), ("ecuador", "EC"), ("uruguay", "UY"),
    ("australia", "AU"), ("new zealand", "NZ"), ("south africa", "ZA"), ("nigeria", "NG"),
    ("egypt", "EG"), ("morocco", "MA"), ("turkey", "TR"), ("türkiye", "TR"), ("israel", "IL"),
    ("saudi", "SA"), ("emirates", "AE"), ("qatar", "QA"), ("kuwait", "KW"), ("jordan", "JO"),
    ("lebanon", "LB"), ("iran", "IR"), ("iraq", "IQ"), ("bangladesh", "BD"), ("philippines", "PH"),
    # 日/韩主要城市：只收与中文地名不冲突的写法（实测台球展名录有地址「名古屋市港区入場1-1904」，
    # 国家列空白、纯汉字写法，关键词表和文字判定都认不出来，被 china_evidence 兜成境内证据）。
    # 故「仁川」（威海有仁川镇）、「光州」（潢川古称）、「蔚山」（河北有蔚县）这类会撞中文地名的写法一律不收。
    ("tokyo", "JP"), ("osaka", "JP"), ("nagoya", "JP"), ("sapporo", "JP"), ("yokohama", "JP"),
    ("kyoto", "JP"), ("kobe", "JP"), ("fukuoka", "JP"), ("sendai", "JP"), ("hiroshima", "JP"),
    ("北海道", "JP"), ("東京", "JP"), ("东京", "JP"), ("大阪", "JP"), ("名古屋", "JP"),
    ("京都", "JP"), ("横浜", "JP"), ("神户", "JP"), ("神戸", "JP"), ("福岡", "JP"), ("福冈", "JP"),
    ("札幌", "JP"), ("新潟", "JP"), ("千葉", "JP"), ("埼玉", "JP"), ("愛知", "JP"), ("爱知", "JP"),
    ("seoul", "KR"), ("busan", "KR"), ("首尔", "KR"), ("釜山", "KR"),
]

# 公司名后缀 -> 区域（弱信号，只在前几级无线索时使用）
_NAME_SUFFIX = [
    ("gmbh", "DE"), ("mbh", "DE"), ("sdn bhd", "MY"), ("pte ltd", "SG"), ("pty ltd", "AU"),
    ("pvt ltd", "IN"), ("private limited", "IN"), ("llp", "IN"), ("b.v.", "NL"), ("s.a.r.l", "FR"),
    ("s.r.l", "IT"), ("s.p.a", "IT"), ("sp. z o.o", "PL"), ("a/s", "DK"), ("ab", "SE"),
    ("oy", "FI"), ("kk", "JP"), ("株式会社", "JP"), ("as", "NO"), ("nv", "BE"),
    ("有限公司", "CN"), ("股份有限公司", "CN"), ("有限责任公司", "CN"),
]


def _region_from_host(host):
    host = normalize_text(host).lower()
    host = re.sub(r"^[a-z]+://", "", host).split("/")[0].split(":")[0]
    if not host or "." not in host:
        return None
    labels = host.split(".")
    for cut in (2, 1):
        if len(labels) > cut:
            suffix = ".".join(labels[-cut:])
            if suffix in _TLD_REGION:
                return _TLD_REGION[suffix]
    return None


_region_from_website = _region_from_host


def _region_from_emails(emails):
    """邮箱域名后缀也是很强的国别信号（如 navitassolar.in）。"""
    for email in emails or []:
        region = _region_from_host(email.split("@")[-1])
        if region:
            return region
    return None


# 地址文字系统本身就是国别证据：假名/日本写法 -> JP，谚文 -> KR。
# 实测必需（台球展专题总表）：地址写「新潟県新潟市中央区古町通5番町610番地 フリーダビル３F」、
# 国家/地区列空白，_ADDRESS_KEYWORDS 里没有这些写法，地址被当成无线索，
# china_evidence 又因整段是 CJK 字符判成「境内证据」，于是这家日本企业按所在地口径进了国内文件、
# 号码列空、备注写「无可拨中国大陆号码」——把日本企业当中国企业交付，客户拨不通还以为是清洗坏了。
# 判据只用无歧义的写法：县/社是中日共用字（云南、日本社团）不用；“日本”也不单独算（中国有条“日本路”），
# 所以文字判定放在关键词表之后：能从写法里认出中国地名就先按关键词表的结果走。
# 「字X」「街区」「建物」看着像日本写法，实测会命中「写字楼/八字桥/数字产业园/7街区」这类中国地址，已剔。
_KANA_RE = re.compile(r"[\u3041-\u3096\u30a1-\u30ff]")     # 平/片假名（normalize_text 已把半角假名转成全角，长音符ー也在内）
_HANGUL_RE = re.compile(r"[\u1100-\u11ff\u302f-\u3030\u3131-\u318e\ua960-\ua97f\uac00-\ud7a3\uffa0-\uffdc]")  # 谚文（韩国）
_JP_ADDRESS_RE = re.compile(r"[\u4e00-\u9fff]{1,3}県|番地|番町|丁目|都道府県")
_CN_ROAD_AFTER_JAPAN_RE = re.compile(r"日本(?=[路街道巷里弄坊苑])")     # 中国路名“日本路/日本街”，不是国名


def _region_from_address(address):
    text = normalize_text(address).lower()
    if not text:
        return None
    base = unicodedata.normalize("NFKD", text)
    base = "".join(c for c in base if not unicodedata.combining(c))
    # 中国地址里的"日本路/日本街"是路名不是国名（实测关键词表会把这种写法直接判成 JP），先剥掉再比
    base = _CN_ROAD_AFTER_JAPAN_RE.sub("", base)
    for keyword, region in _ADDRESS_KEYWORDS:
        if keyword not in base:
            continue
        # ── 中文关键词上下文过滤 ──
        # 中文国家名（如"美国""日本""印度"）出现在中国地址里时，往往是复合地名的一部分：
        # "美国工业村"（海南）、"日本路"（中国路名）、"印度红茶坊"（店名）
        # 判据：关键词前面有中文汉字 → 它嵌在更长的中文地名里，不是国名，跳过
        if any('\u4e00' <= c <= '\u9fff' for c in keyword):
            idx = base.find(keyword)
            if idx > 0 and '\u4e00' <= base[idx - 1] <= '\u9fff':
                continue
        # ── 英文关键词词边界 ──
        # 短英文国名/城市名（london/jordan/peru 等）可能恰好是长英文词的子串，
        # 要求前后是非字母数字边界，避免误匹配
        else:
            idx = base.find(keyword)
            while idx >= 0:
                before_ok = (idx == 0 or not base[idx - 1].isalnum())
                after_pos = idx + len(keyword)
                after_ok = (after_pos >= len(base) or not base[after_pos].isalnum())
                if before_ok and after_ok:
                    break
                idx = base.find(keyword, idx + 1)
            if idx < 0:
                continue
        return region             # 写法里有国名/地名，按关键词表定（日本地址写"日本"、中国地址写"中国"都在这一轮解决）
    # 本地写法里没有国名（"東京都新宿区矢来町112"整串找不到关键词）时按文字系统定
    if _KANA_RE.search(text) or _JP_ADDRESS_RE.search(text):
        return "JP"
    if _HANGUL_RE.search(text):
        return "KR"
    return None


def _region_from_name(name):
    text = normalize_text(name).lower()
    if not text:
        return None
    for suffix, region in _NAME_SUFFIX:
        if re.search(r"(?<!\w)" + re.escape(suffix) + r"(?![a-z0-9])", text):
            return region
    return None


def guess_regions(country, website, address, company_name, emails=None, limit=3):
    """按信号强度给出区域候选：国家 > 官网域名 > 邮箱域名 > 地址 > 公司名。"""
    hints, seen = [], set()
    for region in (
        country_to_region(country), _region_from_host(website), _region_from_emails(emails),
        _region_from_address(address), _region_from_name(company_name),
    ):
        if region and region not in seen:
            seen.add(region)
            hints.append(region)
    # 中国数据占比最高，作为最后兜底
    if "CN" not in seen:
        hints.append("CN")
    return hints[:limit] if len(hints) > limit else hints
