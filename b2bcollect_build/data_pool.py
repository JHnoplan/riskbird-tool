# -*- coding: utf-8 -*-
"""data_pool.py —— 团队数据池管理器（上传 / 增量合并 / 缓存）。

架构（lakehouse + 增量物化视图思想）：
  · 上传层 append-only：每个用户只写自己的 OSS 路径
    （users/{username}/upload_{ts}_{uuid}.json），文件名带 uuid 短后缀，
    同一用户多设备同时上传也不撞名；上传层不去重（原始层保持不可变）
  · 读取端增量合并：搜索时 list_keys 与本地 consumed_keys（已消费文件
    游标）求 diff，只下载新文件 → 与本地总表增量合并 → 增量扩充倒排
    索引；没有新文件时不重新下载、不重新合并（百万级也秒开）
  · meta 分片：每用户只写 users/{username}/meta.json（旧版共享
    master_meta.json 的读-改-写竞态已消除），统计端聚合全部分片

去重时机（合并口径）：
  · 上传层不去重；读取端合并时按 company_norm（去法律后缀 + 去空白
    + 小写）分组去重
  · 同组多条记录：联系方式取并集（手机号去重、邮箱小写去重）
  · source 拼接、source_time 取最新；合并幂等，重复合并结果一致
"""
import getpass
import json
import os
import re
import shutil
import stat
import tarfile
import tempfile
import threading
import time
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from oss_pool_client import OSSPoolClient, OSSPoolError
# 交付档位：缓存目录放在哪儿由档位定（见 edition.pool_cache_dir）。这里只问投影
# 函数、不自己判档位——路径策略只许有一份，两个模块各判一次迟早对不上。
# 与 GUI 一样硬导入：档位模块缺失就必须当场炸，不许退成 pro 把内部功能发出去。
import edition

# 智能去重引擎（可选依赖：打包态可能找不到）
try:
    from dedup_engine import compute_duplicate_score  # noqa: F401  探测可用性
    _HAS_DEDUP = True
except ImportError:
    _HAS_DEDUP = False

# 合并/去重口径（可选依赖；pool_merge 顶层不反向导入本模块，无循环风险）
try:
    from pool_merge import dedup_exact, merge_duplicates_fast
    _HAS_MERGE = True
except ImportError:
    _HAS_MERGE = False

# 入库前清洗（Silver 层）与本地物化视图：一律**函数内延迟导入**——
# ingest_cleaning / pool_store 顶层反向依赖本模块（复用属地与门禁判据），
# 在 data_pool 导入期加载会成环并把它们降级成无 core 模式。
# 注：sync_optimizer 的下载重试职责已下沉到 oss_pool_client（超时自适应 +
# 指数退避），传输层重试只保留一份实现。

# ── 标准化字段定义（上传/导出统一口径）─────────────────────────────────────
# 用户指定的 11 个必选字段（按用户给定顺序），另加「数据来源」用于追溯
STANDARD_FIELDS = {
    "contact":    "客户姓名",       # 联系人
    "title":      "职位",          # 职位/岗位
    "company":    "公司名",       # 公司全称
    "phone":      "电话",          # 手机号（主号码）
    "email":      "邮箱",          # 电子邮箱
    "landline":   "固话",          # 固定电话
    "website":    "官网",          # 公司官网
    "products":   "主营",          # 主营产品/服务
    "industry":   "行业",          # 所属行业
    "address":    "公司地址",     # 详细地址
    "country":    "国家",          # 所在国家
    "source":     "数据来源",     # 数据来源（追溯用，附加）
}
EXPORT_HEADER = list(STANDARD_FIELDS.values())  # 导出表头顺序（12 列）

# ── 数据质量评分 ─────────────────────────────────────────────────────────

def compute_quality_score(record: dict) -> int:
    """计算记录完整度评分（0-100 分）。
    
    11 个字段按重要性加权，填了得分：
    - 公司名 20 分（必填，数据池去重依据）
    - 联系人 15 分
    - 电话 15 分
    - 邮箱 10 分
    - 官网 10 分
    - 地址 10 分
    - 行业 5 分
    - 主营 5 分
    - 国家 5 分
    - 数据来源 3 分
    - 采集时间 2 分
    
    Args:
        record: 数据池标准记录 dict
    
    Returns:
        质量分 0-100
    """
    weights = {
        "company": 20,
        "contacts": 15,
        "phones": 15,
        "emails": 10,
        "website": 10,
        "address": 10,
        "industry": 5,
        "products": 5,
        "country": 5,
        "source": 3,
        "source_time": 2,
    }
    score = 0
    for field, weight in weights.items():
        value = record.get(field)
        # 判断字段是否有值（非空字符串、非空列表）
        if value:
            if isinstance(value, list):
                if len(value) > 0:
                    score += weight
            elif isinstance(value, str) and value.strip():
                score += weight
            elif not isinstance(value, (list, str)):
                # 其他类型（如 dict）非 None 即得分
                score += weight
    return score

# ── 公司名规范化（去法律形式后缀，用于合并去重）──────────────────────────────

# 中文法律形式后缀（按长度降序匹配，避免"有限责任公司"被"有限公司"先截断）
_CN_SUFFIXES = [
    "有限责任公司", "股份有限公司", "有限公司",
    "集团有限公司", "集团公司", "集团",
    "有限责任公司分公司", "有限公司分公司",
]
_CN_SUFFIX_RE = re.compile(
    r"(?:" + "|".join(re.escape(s) for s in _CN_SUFFIXES) + r")\s*$"
)

# 英文法律形式后缀（词边界匹配，不误伤中文公司名里的英文字母）
_EN_SUFFIX_RE = re.compile(
    r"\s*,?\s*"
    r"(?:inc(?:orporated)?|llc|l\.l\.c|corp(?:oration)?|"
    r"ltd(?:\.?)|plc|gmbh|s\.a(?:\.r\.l)?|"
    r"s\.r\.l|s\.p\.a|b\.v|n\.v|pty\s+ltd|pte\s+ltd|kk|株式会社|"
    r"s\.r\.o|sp\.?\s+z\s+o\.o|a/s|"
    r"co(?:mpany)?(?:\s*,?\s*ltd(?:\.?))?|"
    r"l\.p)"
    r"(?:\.?)\s*$",
    re.IGNORECASE,
)

# 清理残留的特殊字符（括号、尾部标点）
_TRAILING_CHARS_RE = re.compile(r"[\s()（）\[\]【】,，.。\-_]+$")
_MULTI_WS_RE = re.compile(r"\s+")


def normalize_company_name(name: str) -> str:
    """公司名规范化：去法律后缀 → 去特殊字符 → 小写 → 去空白。

    用于合并去重的 key，不用于展示。
    示例：
      "常州天合光能有限公司"       → "常州天合光能"
      "Trina Solar Co., Ltd."     → "trina solar"
      "ESTEK SOLAR ENERGY LLC"    → "estek solar energy"
    """
    if not name:
        return ""
    text = name.strip()
    # 循环去后缀（处理"正泰集团股份有限公司"→"正泰集团"→"正泰"的叠加情况）
    for _ in range(3):
        prev = text
        text = _CN_SUFFIX_RE.sub("", text)
        text = _EN_SUFFIX_RE.sub("", text)
        if text == prev:
            break
    # 去尾部特殊字符
    text = _TRAILING_CHARS_RE.sub("", text)
    # 空白归一 + 小写
    text = _MULTI_WS_RE.sub(" ", text).strip().lower()
    return text


# ── 数据池管理器 ─────────────────────────────────────────────────────────────

# ── 国内/国外归属推导（多信号加权投票）─────────────────────────────
# 存量 OSS 数据可能没有 is_domestic 字段，读侧按记录自身证据推导。
# 权重口径对齐清洗侧实践：手机强于固话（外企办事处座机不得一票定性），
# 地址是噪声最高的字段（中国公司地址内嵌外国国名很常见），只给弱权重；
# 境内强证据与境外强证据打平时默认境内（历史数据以国内采集为主）。
_CN_PROVINCES = (
    "北京", "天津", "上海", "重庆", "河北", "山西", "辽宁", "吉林", "黑龙江",
    "江苏", "浙江", "安徽", "福建", "江西", "山东", "河南", "湖北", "湖南",
    "广东", "广西", "海南", "四川", "贵州", "云南", "陕西", "甘肃", "青海",
    "西藏", "台湾", "内蒙古", "宁夏", "新疆", "香港", "澳门",
)
# 中文组织尾缀（属地证据用）。历史缺陷：这里曾复用 _CN_SUFFIXES 这个名字，
# 把 normalize_company_name 的同名后缀表整体覆盖掉——两处语义必须两个名字。
_CN_ORG_SUFFIXES = ("有限公司", "股份", "集团", "厂", "研究所", "中心", "合作社",
                    "个体工商户", "工作室", "公司")
_CN_MOBILE_RE = re.compile(
    r"(?<!\d)1(?:3\d|4[5-9]|5[0-35-9]|6[2567]|7[0-8]|8\d|9[015-9])\d{8}(?!\d)")
_CN_LANDLINE_RE = re.compile(r"(?<!\d)0\d{2,3}-?\d{7,8}(?!\d)")
# 中国的国际冠码：港澳台号码与国际长途写法（"+852 2861 8222"、"00 886-2-2322"）
# 在本工具口径里都是**境内**证据。此前只把 +86/0086 排除在境外号码之外，于是
# 中国台湾、香港公司拿的是境外分，而分区键用同一号码能派出「台湾/香港」——
# 勾「国外」筛出「台湾」就是两个筛选条件互相矛盾。前缀一律去分隔符后匹配。
_GREATER_CN_DIAL_RE = re.compile(r"^(?:\+|00)(?:86|852|853|886)\d+")
# 港澳台登记域后缀：与 _TLD_TO_PROVINCE（ingest_cleaning）同一口径，两层各自解释
# 同一批后缀就会得出互相矛盾的结论，所以这张表也只在判据里出现一次
_GREATER_CN_CC_TLDS = (".hk", ".mo", ".tw")
# 属地判据版本。派生出来的 is_domestic 会被写回记录并持久化，如果推导时把
# 「记录里已有的 is_domestic」无条件当证据，就会形成**自锁**：第一轮按当时的
# 判据写死，之后判据再怎么修都读到自己写的旧值。实测后果——存量 master.json
# 里 66,893 条全部带 is_domestic=True（判据补齐前留下的），补强外文地址/国码
# 域名/外国尾缀三维证据后属地依然一动不动。
# 因此显式值只在「带当前版本标记」时才作快捷路径；无标记或旧标记一律重算。
#   2 → 3：境外号码证据补齐——除 "+…" 之外还认国际长途冠码 00（"0049…"、
#          "00 81-3-1234"），并先去分隔符再判前缀。不升版本的话，存量记录里
#          判据补齐前写死的 is_domestic=True 会一直被当成快捷路径读，
#          地区分区键（靠属地选证据链）也永远拿不到新判据。
REGION_RULE_VERSION = "3"
# 中日韩汉字字符类（历史缺陷：曾写成 any(c in name for c in "\u4e00\u9fff")，
# 那是在找「一」和「鿿」两个字面字，不是范围——中文公司名的属地证据因此永不加分）
_CJK_CHAR_RE = re.compile(r"[\u4e00-\u9fff]")
# 中文地址形态（省/市/区/县 + 街/路/道/号/幢/室）：只给弱权重，用于
# 「常州市青阳东路639号」这种不含省名的纯市级地址
_CN_ADDR_SHAPE_RE = re.compile(r"[省市区县路街道坊巷弄号楼幢室苑庄]")
# ── 境外证据（历史缺陷补齐）──
# 这三维（外文地址、国外 ccTLD 邮箱、外国组织尾缀）原本**完全缺失**，后果是
# 存量池 66,893 条里 36,432 条走到「零证据默认国内」，其中 35,699 条的地址是
# 瑞士 Mühlackerstrasse、意大利 Contrada Blocco、美国 US Route 22 这类纯外文
# 街道——全被标成"国内"，界面勾「国外」只剩 1 条。属地过滤在存量数据上等于没
# 有。注释里那句"历史数据以国内采集为主"是猜测，实测不成立，按实测证据补判据。
_LATIN_RUN_RE = re.compile(r"[A-Za-zÀ-ÿ]")
# 常见国家/地区 code top-level domain（不含 .com/.net/.org 这类通用域，它们
# 国内外都在用，加了会把中国外贸公司一路带进国外）
_FOREIGN_EMAIL_TLDS = (
    ".de", ".fr", ".it", ".es", ".nl", ".be", ".pl", ".ch", ".at", ".se",
    ".no", ".dk", ".fi", ".uk", ".gb", ".ie", ".pt", ".cz", ".sk", ".hu",
    ".ro", ".bg", ".gr", ".hr", ".si", ".lt", ".lv", ".ee", ".is", ".ru",
    ".ua", ".kz", ".tr", ".il", ".za", ".jp", ".kr", ".sg", ".my", ".th",
    ".vn", ".id", ".in", ".pk", ".bd", ".au", ".nz", ".ca", ".mx", ".br",
    ".ar", ".cl", ".pe", ".co", ".ve", ".eg", ".sa", ".ae", ".qa", ".kw",
    ".ma", ".ng", ".ke", ".lu", ".mt", ".cy", ".rs", ".ba", ".by", ".md",
)
# 外国组织尾缀。注意 "Co., Ltd." 不算：中国出口企业的英文名就是这么写的，
# 把它当境外证据会把整批国内制造商推到"国外"去（与上面 ccTLD 不列 .com 同理）
_FOREIGN_ORG_RE = re.compile(
    r"\b(?:gmbh|mbh|kg|ohg|ag|s\.?r\.?l\.?|s\.?r\.?o\.?|a\.?s\.?|d\.?o\.?o\.?"
    r"|b\.?v\.?|n\.?v\.?|s\.?a\.?r\.?l\.?|s\.?a\.?s\.?|e\.?u\.?r\.?l\.?|spa|"
    r"oyj|ab|pjs|pty|tbk|berhad|limitada|sp\.?\s?z\.?\s?o\.?\s?o\.?"
    r"|llc|inc|corp|corporation|plc)\b[.,]?\s*$", re.I)


# 境外国名（英文为主，兼顾当地语常见写法）：地址里出现就是**最强**境外证据，
# 与显式 country 字段同级。这一维原本是缺的，于是「18883577020」这种北美
# 免费电话（1 + 区号 888）被 _CN_MOBILE_RE 当成中国手机号、和外文地址的
# 境外分打平，按"打平判国内"落成了国内（实测池里 405 条卡在这里）。
# 一致性约束：本表必须与 ingest_cleaning._COUNTRY_TO_CN 的键集合完全相等——属地
# 判据在这里、国名归一在那边，两张表一旦各自漂移，就会出现「属地=国外但地区分区
# 键=未知地区」这类自相矛盾的记录（分区键比空值更坏的另一种形态）。词表本身是纯
# 数据、无循环依赖，由 test_ingest_store_offline 的双向差集断言钉死。
_FOREIGN_COUNTRY_WORDS: Tuple[str, ...] = (
    "albania", "algeria", "argentina", "armenia", "australia", "austria",
    "azerbaijan", "bahrain", "bangladesh", "belarus", "belgie", "belgium",
    "bharat", "bolivia", "bosnia", "brasil", "brazil", "britain",
    "bulgaria", "canada", "cesko", "chile", "colombia", "costa rica",
    "croatia", "cuba", "cyprus", "czech", "czech republic", "czechia",
    "danmark", "denmark", "deutschland", "dominican", "dominican republic", "ecuador",
    "egypt", "eire", "el salvador", "ellada", "emirates", "england",
    "espagne", "espana", "estonia", "ethiopia", "fiji", "finland",
    "france", "frankreich", "georgia", "germany", "ghana", "great britain",
    "greece", "guatemala", "holland", "honduras", "hungary", "iceland",
    "india", "indonesia", "ireland", "israel", "italia", "italien",
    "italy", "jamaica", "japan", "jordan", "kazakhstan", "kenya",
    "korea", "kuwait", "kyrgyzstan", "latvia", "lebanon", "libya",
    "lithuania", "luxembourg", "macedonia", "magyarorszag", "malaysia", "malta",
    "mexico", "moldova", "mongolia", "montenegro", "morocco", "mozambique",
    "myanmar", "namibia", "nepal", "netherlands", "new zealand", "nicaragua",
    "nigeria", "norge", "norway", "oesterrijk", "oman", "oostenrijk",
    "pakistan", "palestine", "panama", "paraguay", "peru", "philippines",
    "poland", "polska", "portugal", "qatar", "republic of korea", "romania",
    "rossiya", "russia", "samoa", "saudi", "saudi arabia", "schweiz",
    "scotland", "senegal", "serbia", "singapore", "slovakia", "slovenia",
    "south africa", "south korea", "spain", "sri lanka", "srilanka", "suomi",
    "sverige", "sweden", "switzerland", "tajikistan", "tanzania", "thailand",
    "trinidad", "tunisia", "turkey", "turkiye", "turkmenistan", "uae",
    "uganda", "uk", "ukraine", "united arab emirates", "united kingdom", "united states",
    "united states of america", "uruguay", "usa", "uzbekistan", "venezuela", "viet nam",
    "vietnam", "wales", "zambia", "zimbabwe",
)


def _alt_re(words) -> "re.Pattern":
    """整词交替正则：长词排在前面，避免 "czech" 抢掉 "czech republic"。"""
    body = "|".join(re.escape(w) for w in sorted(set(words), key=lambda x: (-len(x), x)))
    return re.compile(r"\b(?:%s)\b" % body, re.I)


_FOREIGN_COUNTRY_RE = _alt_re(_FOREIGN_COUNTRY_WORDS)
# 中国地名的拉丁写法：出口企业的英文地址就长这样（"…Guangming District,
# Shenzhen, Guangdong"），没有这一维它们会被外文地址形态一路推到国外。
# 收词口径（与 ingest_cleaning 的地区分区键同源，两条都在测试里钉住）：
#   ① 全部省级拉丁写法必须在这里——判据不认省份、分区键却按省份归类，就会
#      产出「属地=国外 + 地区=甘肃」这种互相矛盾的记录（实测 55,219 条里 111 条）；
#   ② 港澳台的城市写法必须在这里：这些地址只会写 "Tainan"、"Hsinchu" 而不会写
#      "Taiwan"，缺了就等于把中国台湾的公司推到国外；
#   ③ 其余只收外贸高频、且与外文地名/人名无同形冲突的城市。县一级的小地名
#      一律**不放**在这里（"Dali"、"Haimen" 既是云南大理/江苏海门也是外国地名），
#      它们只参与分区的城市级归并，由 ingest_cleaning.derive_province 在已判国内
#      的前提下才使用，不参与本表判据。
_CN_LATIN_GEO_WORDS: Tuple[str, ...] = (
    # 省级（必须覆盖 ingest_cleaning._LATIN_PROVINCES 的键集合）
    "beijing", "tianjin", "shanghai", "chongqing", "hebei", "shanxi", "shaanxi",
    "neimenggu", "inner mongolia", "liaoning", "jilin", "heilongjiang", "jiangsu",
    "zhejiang", "anhui", "fujian", "jiangxi", "shandong", "henan", "hubei",
    "hunan", "guangdong", "guangxi", "hainan", "sichuan", "guizhou", "yunnan",
    "xizang", "tibet", "gansu", "qinghai", "ningxia", "xinjiang", "taiwan",
    "hong kong", "hongkong", "macau", "macao",
    # 台湾/香港城市（英文地址只写城市名的那一类）
    "taipei", "kaohsiung", "taichung", "tainan", "taoyuan", "hsinchu", "changhua",
    "nantou", "keelung", "miaoli", "yunlin", "pingtung", "hualien", "taitung",
    "chiayi", "kowloon", "tsuen wan", "sha tin", "tsim sha tsui", "tai po",
    # 外贸高频城市/地名（无同形冲突）
    "guangzhou", "shenzhen", "dongguan", "foshan", "zhongshan", "hangzhou",
    "ningbo", "wenzhou", "nanjing", "suzhou", "wuxi", "changzhou", "kunshan",
    "qingdao", "jinan", "yantai", "weihai", "dalian", "shenyang", "xiamen",
    "quanzhou", "wuhan", "changsha", "chengdu", "shijiazhuang", "xi'an", "xian",
    "baoji", "hefei", "nanchang", "zhengzhou", "harbin", "changchun", "hohhot",
    "baotou", "lasa", "lhasa", "shigatse", "xining", "yinchuan", "urumqi", "shihezi",
    "haikou", "sanya", "nanning", "liuzhou", "guiyang", "kunming", "lanzhou",
    "dunhuang", "shantou", "chaozhou", "zhuhai", "huizhou", "jiangmen", "yiwu",
    "prc", "china",
)
_CN_LATIN_GEO_RE = _alt_re(_CN_LATIN_GEO_WORDS)


def _email_cc_tld(emails) -> str:
    """取邮箱域名末尾的国家码 TLD（小写、带点），通用域返回空。"""
    for e in emails or []:
        s = str(e).strip().lower()
        if "@" not in s:
            continue
        domain = s.rsplit("@", 1)[-1]
        if domain.endswith(_FOREIGN_EMAIL_TLDS):
            for tld in _FOREIGN_EMAIL_TLDS:
                if domain.endswith(tld):
                    return tld
    return ""


def _site_cc_tld(url) -> str:
    """官网域名的国家码 TLD（与邮箱同一张表、同一个"通用域不算证据"原则）。"""
    s = str(url or "").strip().lower()
    if not s:
        return ""
    s = re.sub(r"^[a-z]+://", "", s)
    s = s.split("/", 1)[0].split("?", 1)[0]
    s = s.split(":", 1)[0]
    if s.count(".") < 1:
        return ""
    return _email_cc_tld(["x@" + s])


def _foreign_address_shape(addr: str) -> bool:
    """地址是「成串外文 + 拉丁词」形态且没有任何中文成分。

    只认「无中文 + 拉丁字母足够多」这一条硬事实，不去穷举各国街道词：词形表
    永远漏（本次修复前的缺陷就是压根没有这一维），而「地址里一个字中文都没有」
    是可以在意大利/瑞士/日本罗马字/哈萨克西里尔各种写法上都成立的判据。
    """
    if not addr:
        return False
    if _CJK_CHAR_RE.search(addr):
        return False
    return len(_LATIN_RUN_RE.findall(addr)) >= 8


def _domestic_signals(rec: dict) -> Tuple[int, int]:
    """返回 (境内得分, 境外得分)。证据分级加权，禁止单条弱信号定性。"""
    dom = intl = 0
    name = str(rec.get("company") or "")
    country = str(rec.get("country") or "").strip()
    if country:
        if country in ("中国", "中国大陆", "CN", "cn", "China", "china"):
            dom += 3
        else:
            intl += 3
    addr = str(rec.get("address") or "")
    if any(p in addr for p in _CN_PROVINCES):
        dom += 2          # 地址含中国省份：中等证据
    elif "中国" in addr:
        dom += 1
    elif _CJK_CHAR_RE.search(addr) and _CN_ADDR_SHAPE_RE.search(addr):
        # 纯中文地址且带「市区路号」形态（如「常州市青阳东路639号」不含省名）
        dom += 1
    if _CN_LATIN_GEO_RE.search(addr):
        # 中国地名的英文写法（出口企业英文地址）：与中文省份同权重
        dom += 2
    if _FOREIGN_COUNTRY_RE.search(addr):
        # 地址里直接出现国名：与显式 country 同级的最强境外证据
        intl += 3
    # 手机（强）与固话（弱）：境内外公司都可能留中国座机，不能固话一票定性
    phones = list(rec.get("mobiles") or []) + list(rec.get("phones") or [])
    landlines = list(rec.get("landlines") or [])
    blob_p = " ".join(str(x) for x in phones)
    blob_l = " ".join(str(x) for x in landlines + [rec.get("phone") or ""])
    if _CN_MOBILE_RE.search(blob_p):
        dom += 2
    elif _CN_LANDLINE_RE.search(blob_l):
        dom += 1
    # 国外手机：+ 或 00 开头且非 +86/0086，或 4~15 位纯数字且非中国号段形态
    foreign_mobile = False
    cn_intl_dial = False
    for p in phones + [rec.get("phone") or ""]:
        # 先去掉号码里常见的分隔符再判前缀：此前只认「+49 151…」这种 + 开头的
        # 写法，而各国国际长途冠码 00 才是主流（"0049151234567"、"00 81-3-1234"）。
        # 后果是这批号码在属地判据里丢掉境外证据、被默认判成国内，而分区键
        # （ingest_cleaning.derive_province）用同一批号码能派出国家——两个筛选
        # 条件互相矛盾（勾「国内」的地区分布里出现德国）。判据必须一起补。
        s = re.sub(r"[\s\-().]", "", str(p).strip())
        if _GREATER_CN_DIAL_RE.match(s):
            # +86/+852/+853/+886 一律是中国号码（含港澳台）：既不是境外证据，
            # 本身就是境内强证据。港澳台此前被当成境外号码，是「国外+台湾」
            # 这类矛盾记录的直接来源。
            cn_intl_dial = True
            continue
        if s.startswith(("+", "00")) and len(re.sub(r"\D", "", s[2:])) >= 6:
            foreign_mobile = True
            break
        if re.match(r"^\d{4,15}$", s) and not _CN_MOBILE_RE.match(s) and not s.startswith("0"):
            foreign_mobile = True
            break
    if cn_intl_dial:
        dom += 2
    if foreign_mobile:
        # 中文企业形态名下降权：国内固话经常只留 7~8 位本地号（不带区号），
        # 「纯数字且不以 0 开头」的正则形态会把它们一律当成国外号码
        intl += 1 if _CJK_CHAR_RE.search(name) else 2
    email_dom = email_intl = gc_cc_tld = False
    for e in list(rec.get("emails") or []) + [rec.get("email") or ""]:
        s = str(e).strip().lower()
        if "@" not in s:
            continue
        # 企业自有域名邮箱不指示属地；中国公司几乎不用 gmail 类邮箱，
        # 但外贸公司也有 gmail —— 故 gmail 只给弱权重。.hk/.mo/.tw 是中国的特别
        # 行政区/省份 code（与 _GREATER_CN_DIAL_RE 同一口径），登记在境外一侧就会
        # 造出「国外 + 香港」这种两个筛选条件互相矛盾的记录。
        if s.endswith(_GREATER_CN_CC_TLDS):
            gc_cc_tld = True
        elif s.endswith((".cn", ".com.cn", ".net.cn", ".org.cn")):
            email_dom = True
        elif s.endswith(("@gmail.com", "@yahoo.com", "@hotmail.com", "@outlook.com",
                        "@aol.com", "@yandex.ru")):
            email_intl = True
        elif _email_cc_tld([s]):
            # 国家码后缀的邮箱（name@firma.de / name@azienda.it）：境外弱证据
            email_intl = True
    if email_dom:
        dom += 1
    if email_intl:
        intl += 1
    website = str(rec.get("website") or "").lower()
    # 只有 .cn 系官网算境内证据；.com 等通用域名国内外都用，加分会压过境内强证据
    if any(website.endswith(ext) or (".cn/" in website) for ext in
           (".cn", ".com.cn", ".net.cn", ".org.cn")):
        dom += 1
    elif website.endswith(_GREATER_CN_CC_TLDS):
        gc_cc_tld = True
    elif _site_cc_tld(website):
        # 国码域名官网（trina.com.cn 已归境内；solarmarkt.de / arred.it 归境外）
        intl += 1
    if gc_cc_tld:
        # 港澳台 ccTLD 与 +852/+853/+886 号码同权重：都是直接登记在特别行政区/
        # 省份的强属地证据（"1A Rua de São Paulo, Macau" 这类葡文写法光看地址形态
        # 一定被判国外，只有登记域后缀能把它拉回来）
        dom += 2
    if _CJK_CHAR_RE.search(name) and any(s in name for s in _CN_ORG_SUFFIXES):
        # 中文公司名 + 中国组织尾缀（"××有限公司"）：境内**强**证据。
        # 此前只给 2 分里的 1 分，被"7 位无区号本地固话"误判成国外号顶掉
        # （实测「镇江东方电热智能装备有限公司」因此落到国外）。
        dom += 2
    elif _CJK_CHAR_RE.search(name):
        dom += 1
    elif name and not _CJK_CHAR_RE.search(name) and _FOREIGN_ORG_RE.search(name.strip()):
        # 外国组织尾缀（GmbH / s.r.o. / A.Nüesch AG / Inc / B.V.）：境外弱证据。
        # 明确**不含** "Co., Ltd."——中国出口企业英文名就是这个尾缀，列进去会把
        # 整批国内制造商推出国外。
        intl += 1
    if _foreign_address_shape(addr) and not _CJK_CHAR_RE.search(name) \
            and not _CN_LATIN_GEO_RE.search(addr):
        # 外文地址形态：境外**强**证据。两个前提都在收边界——
        # 「公司名不含中文」防中国出口企业的英文地址；「地址里没有中国地名」
        # 防 "No.93 Tayuan Road, Suzhou, China" 这种**用英文写的中国地址**被
        # 当成境外证据（那类记录此前全靠境内境外打平才没判错，等于是偶然对）。
        intl += 2
    return dom, intl


def _infer_is_domestic(rec: dict) -> bool:
    """推导记录是否国内公司（存量无 is_domestic 字段的读侧兜底）。

    判据：country 最优先；否则多信号加权，境内分 >= 境外分 判国内。
    注意：与清洗侧同口径——单条弱信号（地址里的异域词形、外企座机）
    不得否定公司名后缀+中国手机+中文地址这类强证据组合。

    零证据的兜底方向按实测数据定，不再凭"历史以国内为主"猜测：补上外文
    地址/国码域名/外国尾缀三维之后，池里仍走到零证据的记录已经极少，此时
    公司名语言是唯一剩下的信号（中文名→国内、纯外文名→国外），两者都没有
    才回落国内。历史缺陷：这里无条件 return True，把 35,699 条纯外文地址的
    记录标成国内，占全池 53%，「只看国外」筛选因此只剩 1 条结果。
    """
    v = rec.get("_region_ver")
    if v is not None:
        return v is True
    explicit = rec.get("is_domestic")
    if explicit is not None and str(rec.get("region_rule") or "") != REGION_RULE_VERSION:
        # 旧判据留下的派生值（或压根没打过标记的历史值）不构成证据：不按当前
        # 版本标记写入的一律重算，判据升级才能真正落到存量数据上
        explicit = None
    if explicit is not None:
        result = bool(explicit)
    else:
        dom, intl = _domestic_signals(rec)
        if dom == 0 and intl == 0:
            name = str(rec.get("company") or "").strip()
            result = (not name) or bool(_CJK_CHAR_RE.search(name))
        else:
            result = dom >= intl
    rec["_region_ver"] = result   # 就地缓存，重复判定零成本
    return result


def record_region(rec: dict) -> str:
    """记录属地键："dom" / "intl"（带 _region_key 就地缓存，搜索热路径零重复推导）。"""
    ck = rec.get("_region_key")
    if ck in ("dom", "intl"):
        return ck
    k = "dom" if _infer_is_domestic(rec) else "intl"
    rec["_region_key"] = k
    return k


class DataPoolManager:
    """团队数据池：上传本次采集 → OSS → 搜索时增量合并。

    meta 已分片为 users/{username}/meta.json（旧共享 master_meta.json
    有读-改-写竞态，已废弃；OSS 上的旧文件保留无害，不再读写）。
    """

    # 单个上传对象记录数上限（约 9MB JSON）：超过就分片，
    # 弱网下失败只重传单片而不是整批几十 MB 重来
    SHARD_RECORD_LIMIT = 20000

    # 剔除标记（删除向量）对象前缀：与 users/ 并列的一等公民目录，
    # 每个用户只写自己的分片，读取端全量消费（见 TOMBSTONE_PREFIX 注释）
    TOMBSTONE_PREFIX = "tombstones/"

    def __init__(self, oss_client: Optional[OSSPoolClient] = None,
                 cache_dir: Optional[str] = None):
        self.oss = oss_client or OSSPoolClient()
        self.username = self._get_username()
        self._cache_dir = Path(cache_dir or self._default_cache_dir())
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._master_cache: Optional[List[dict]] = None
        self._master_cache_time: float = 0
        # 已合并进本地总表的 OSS 上传文件 key 集合（增量合并游标）
        self._consumed_keys: set = set()
        # 剔除标记：已下载的 key 集合 + 条目列表 + company_norm 快速判定集合
        self._tomb_keys: set = set()
        self._tomb_entries: List[dict] = []
        self._tomb_norms: set = set()
        self._tomb_loaded = False
        # OSS list_keys 短时缓存（同批次操作内复用，避免重复网络调用）
        self._oss_keys_cache: Dict[str, List[str]] = {}
        self._oss_keys_cache_time: float = 0
        # 本地物化视图（Gold 层）：句柄懒建，建不起来就不再反复尝试
        self._store = None
        self._store_off = False
        # 总表内容指纹 / 视图已对齐到的指纹（None = 未知，需要重算或重建）
        self._master_fp: Optional[Tuple[int, int]] = None
        self._view_fp: Optional[Tuple[int, int]] = None
        # 倒排索引解析结果缓存：((mtime_ns, size), 解析后的 dict)
        self._index_cached: Optional[Tuple[Tuple[int, int], dict]] = None

    # ── 用户名 ──

    @staticmethod
    def _get_username() -> str:
        """自动获取电脑用户名（getpass.getuser 跨平台兼容）。"""
        try:
            name = getpass.getuser()
            # 清理：只保留字母数字下划线中文
            name = re.sub(r"[^\w\u4e00-\u9fff]", "_", name).strip("_")
            return name or "unknown"
        except Exception:
            return "unknown"

    @staticmethod
    def _default_cache_dir() -> str:
        """默认本地缓存目录：内部版放桌面，对外档位放应用数据目录。

        对外档位要是沿用桌面路径，用户桌面上就会凭空长出一个叫「数据池缓存」的
        文件夹（里面有本工具的缓存与同步留痕）——界面把入口藏得再好，这个文件夹
        等于直接把功能写在桌面上，而且外部用户完全不知道它能不能删。
        """
        desktop = Path.home() / "Desktop"
        return edition.pool_cache_dir(str(desktop / "数据池缓存"))

    # ── 本地物化视图（Gold 层：SQLite + FTS5）─────────────────────────────

    def store(self):
        """物化视图句柄；FTS5 缺失/缓存目录不可写时永久返回 None（老链路继续服务）。"""
        if self._store is None and not self._store_off:
            try:
                from pool_store import PoolStore
                st = PoolStore(str(self._cache_dir))
                if not st.usable():
                    self._store_off = True
                    return None
                self._store = st
            except Exception:
                self._store_off = True
                return None
        return self._store

    def master_fingerprint(self) -> Optional[Tuple[int, int]]:
        """当前内存总表的内容指纹（一次算好复用；总表任何变动都会作废）。"""
        if self._master_cache is None:
            return None
        if self._master_fp is None:
            from pool_store import fingerprint_records
            self._master_fp = fingerprint_records(self._master_cache)
        return self._master_fp

    def _invalidate_view(self):
        """总表变了：作废指纹与「视图已对齐」状态，等下一次后台 sync。"""
        self._master_fp = None
        self._view_fp = None

    def _set_master(self, master: List[dict]):
        """总表唯一赋值入口：换表即作废指纹与视图对齐状态。

        指纹/视图状态依赖「当前总表内容」，散在各处赋值必然出现某条路径忘了
        失效——拿旧视图当新数据查询是静默错误。收成一个入口后，任何换表路径
        都自动保守（视图作废 → 后台重建），不会静默用错版本。

        剔除标记在这里再过一遍：磁盘缓存里的 master.json 可能是「打标记之前」
        写的，只有挂在赋值入口才能保证已缓存的总表也认当前的删除向量
        （幂等：全量重建/增量路径已经洗过，这里第二次是空操作）。
        """
        self._master_cache = self._apply_tombstones(master)
        self._master_cache_time = time.time()
        self._invalidate_view()

    # ── 剔除标记（删除向量 / tombstone）──────────────────────────────────
    #
    # 为什么不能直接删 OSS 上的数据文件：Bronze 是 append-only 原始层，一个
    # 对象里混着多次采集（历史缺陷就是这么出现的：两条联调测试数据进了生产池，
    # 传错之后**完全没有撤回手段**）。改写它等于拿读-改-写去赌并发（共享
    # master_meta.json 当年就是这么丢数据的），而且原始层一旦被改写，审计与
    # 回溯同时失效。
    # 工业界的统一答案（Delta Lake deletion vector / Iceberg position delete /
    # Cassandra tombstone）：**只追加不可撤回的标记，读取端用当前标记重放**。
    # 这里与名称门禁的读侧重放（_gate_names_on_read）是同一套机制，因此：
    #   · 不必 bump Silver 版本号触发全量回刷——标记在总表赋值入口生效，
    #     「缓存先建、标记后打」的旧缓存同样会被洗掉；
    #   · 标记对象本身也是 append-only，每个用户只写自己目录下的分片，
    #     多端永不互相覆盖。

    def _tombstone_state_path(self) -> Path:
        return self._cache_dir / "tombstones.json"

    def _load_tombstone_state(self) -> None:
        """从本地磁盘认回已知的剔除标记（不碰网络，幂等）。"""
        if self._tomb_loaded:
            return
        self._tomb_loaded = True
        try:
            data = json.loads(self._tombstone_state_path().read_text(encoding="utf-8"))
        except Exception:
            return
        if not isinstance(data, dict):
            return
        self._tomb_keys = {str(k) for k in (data.get("keys") or [])}
        entries = [e for e in (data.get("entries") or []) if isinstance(e, dict)]
        self._tomb_entries = entries
        self._tomb_norms = {str(e.get("company_norm") or "")
                            for e in entries if e.get("company_norm")}

    def _save_tombstone_state(self) -> None:
        """标记状态原子落盘（tmp + replace），坏一个文件不能把整个池子拖死。"""
        try:
            payload = json.dumps({"keys": sorted(self._tomb_keys),
                                  "entries": self._tomb_entries},
                                 ensure_ascii=False)
            p = self._tombstone_state_path()
            tmp = p.with_suffix(".json.tmp")
            tmp.write_text(payload, encoding="utf-8")
            os.replace(tmp, p)
        except Exception:
            pass

    def _apply_tombstones(self, records: Optional[List[dict]]) -> List[dict]:
        """按当前删除向量过滤一份记录列表（纯内存，不打网络）。"""
        self._load_tombstone_state()
        norms = self._tomb_norms
        if not norms or not records:
            return records or []
        kept = []
        for rec in records:
            if not isinstance(rec, dict):
                kept.append(rec)
                continue
            norm = rec.get("company_norm") or normalize_company_name(
                str(rec.get("company") or ""))
            if norm and norm in norms:
                continue
            kept.append(rec)
        if len(kept) != len(records):
            import logging
            logging.getLogger(__name__).info(
                "🗑 剔除标记重放：%d 条里按删除向量去掉 %d 条",
                len(records), len(records) - len(kept))
        return kept

    def _resync_master_with_tombstones(self) -> int:
        """删除向量变新了而内存总表还是旧的：就地重洗并落盘。返回被去掉的条数。"""
        master = self._master_cache
        if not master or not self._tomb_norms:
            return 0
        kept = self._apply_tombstones(master)
        if len(kept) == len(master):
            return 0
        self._set_master(kept)
        # 索引与总表必须同源重建（倒排记的是下标，删行即整体前移）
        try:
            index = self._build_inverted_index(self._master_cache)
            self._save_local_state(self._master_cache, index,
                                   self._consumed_keys)
        except Exception:
            pass
        return len(master) - len(kept)

    def refresh_tombstones(self, force: bool = False) -> int:
        """拉取全部剔除标记对象，返回本轮**新认到**的条目数（0 = 无变化）。

        失败分级与 _download_records 同一口径：对象不存在/解析失败这类永久错误
        推进游标（否则一个坏标记文件会把每轮同步都卡住），瞬时错误留在原地重试。
        网络整体不可用时静默沿用本地状态——剔不掉是滞后的问题，读不到池子是
        可用性事故，方向必须选保守的一侧。
        """
        self._load_tombstone_state()
        try:
            keys = sorted(k for k in
                          self._list_keys_cached(self.TOMBSTONE_PREFIX)
                          if k.endswith(".json"))
        except Exception:
            return 0
        fresh = [k for k in keys if force or k not in self._tomb_keys]
        if not fresh:
            return 0
        permanent = ("对象不存在", "JSON 解析失败")
        added = 0
        keys_new = 0
        for key in fresh:
            try:
                data, _ = self.oss.download_json(key)
            except OSSPoolError as e:
                if any(p in str(e) for p in permanent):
                    # 永久坏文件：认了，别再重试（不认就会每轮同步都撞一次）
                    if key not in self._tomb_keys:
                        self._tomb_keys.add(key)
                        keys_new += 1
                continue
            except Exception:
                continue          # 瞬时错误：留在原地，下一轮重试
            entries = (data or {}).get("entries") if isinstance(data, dict) else None
            if not isinstance(entries, list):
                if key not in self._tomb_keys:
                    self._tomb_keys.add(key)
                    keys_new += 1
                continue
            known = {str(e.get("company_norm") or "")
                     for e in self._tomb_entries if isinstance(e, dict)}
            for e in entries:
                if not isinstance(e, dict) or not e.get("company_norm"):
                    continue
                if key not in self._tomb_keys:
                    self._tomb_keys.add(key)
                    keys_new += 1
                if str(e["company_norm"]) in known:
                    continue
                self._tomb_entries.append(e)
                known.add(str(e["company_norm"]))
                added += 1
        # 游标也要落盘：只在本轮真的新增了条目才保存，会把「已经认过的标记文件」
        # 忘掉，下一轮又去下载一遍——内容没变所以结果正确，但白烧网络
        if added or keys_new:
            self._tomb_norms = {str(e.get("company_norm"))
                                for e in self._tomb_entries
                                if e.get("company_norm")}
            self._save_tombstone_state()
        return added

    def tombstone_companies(self, names: Iterable[str],
                            reason: str = "") -> dict:
        """把这些企业从数据池的**读取口径**里剔除（Bronze 原始层不动）。

        返回 {"marked": 打标企业数, "removed": 本机总表去掉的记录数,
              "filename": 标记对象文件名}
        """
        entries: List[dict] = []
        seen: set = set()
        for raw in names:
            nm = str(raw or "").strip()
            if not nm:
                continue
            norm = normalize_company_name(nm)
            if not norm or norm in seen:
                continue
            seen.add(norm)
            entries.append({
                "company": nm,
                "company_norm": norm,
                "reason": str(reason or "").strip()[:200],
                "actor": self.username,
                "time": datetime.now().isoformat(timespec="seconds"),
            })
        if not entries:
            raise OSSPoolError("没有可用的企业名，剔除至少要指定一个")
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"delete_{ts}_{uuid.uuid4().hex[:8]}.json"
        key = f"{self.TOMBSTONE_PREFIX}{self.username}/{filename}"
        self.oss.upload_json_atomic(key, {
            "username": self.username,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "entry_count": len(entries),
            "entries": entries,
        })
        # 立刻对本机生效，不等下一轮同步（用户点完剔除还搜得到是同一种脏数据）
        known = {str(e.get("company_norm") or "") for e in self._tomb_entries}
        for e in entries:
            if e["company_norm"] not in known:
                self._tomb_entries.append(e)
                known.add(e["company_norm"])
        self._tomb_keys.add(key)
        self._tomb_norms = {str(e.get("company_norm")) for e in self._tomb_entries
                            if e.get("company_norm")}
        self._save_tombstone_state()
        removed = self._resync_master_with_tombstones()
        return {"marked": len(entries), "removed": removed,
                "filename": filename, "key": key}

    def tombstone_list(self) -> List[dict]:
        """当前已知的剔除标记（按时间倒序，给界面如实展示）。"""
        self._load_tombstone_state()
        return sorted(self._tomb_entries,
                      key=lambda e: str(e.get("time") or ""), reverse=True)

    def find_for_tombstone(self, keyword: str, limit: int = 300) -> List[dict]:
        """按名字关键字找可剔除对象（附联系方式摘要供人核对）。

        剔除是不可撤回的动作，界面上必须让用户看到「这条到底是不是我要删的」，
        所以这里给的是总表里的真实聚合结果，不是输入框的回显。
        已经打过标记的企业**也必须出现在结果里**（总表重放后已经没有它们了）：
        否则用户搜一遍「没找到」，会以为标记没生效而再打一遍。
        """
        kw = str(keyword or "").strip()
        if not kw:
            return []
        norm_kw = normalize_company_name(kw)
        hay = kw.lower()
        cap = max(1, int(limit))
        out: List[dict] = []
        got_norms: set = set()
        # 先列「已经剔过的」，再补总表里还能剔的：标记对象总量极小（一家一条），
        # 而关键字命中的在册企业可能有几百条——反过来排会让已剔的那几条被上限挤掉，
        # 用户就看不出「这家我已经处理过了」。
        self._load_tombstone_state()
        for e in self._tomb_entries:
            cn = str(e.get("company_norm") or "")
            nm = str(e.get("company") or "")
            if not cn or cn in got_norms:
                continue
            if hay not in nm.lower() and not (norm_kw and norm_kw in cn):
                continue
            out.append({
                "company": nm, "company_norm": cn, "phones": [], "emails": [],
                "website": "", "source": "已剔除（本机总表里已无此记录）",
                "source_user": str(e.get("actor") or ""),
                "reason": str(e.get("reason") or ""),
                "tombstoned": True,
            })
            got_norms.add(cn)
            if len(out) >= cap:
                return out
        for rec in (self._master_cache or []):
            nm = str(rec.get("company") or "")
            cn = str(rec.get("company_norm") or "")
            if cn in got_norms:
                continue
            if hay not in nm.lower() and not (norm_kw and norm_kw in cn):
                continue
            out.append({
                "company": nm,
                "company_norm": cn,
                "phones": list(rec.get("phones") or [])[:3],
                "emails": list(rec.get("emails") or [])[:2],
                "website": str(rec.get("website") or ""),
                "source": str(rec.get("source") or ""),
                "source_user": str(rec.get("source_user") or ""),
                "reason": "",
                "tombstoned": bool(cn and cn in self._tomb_norms_set()),
            })
            got_norms.add(cn)
            if len(out) >= cap:
                break
        return out

    def _tomb_norms_set(self) -> set:
        self._load_tombstone_state()
        return self._tomb_norms

    def sync_store(self, progress_cb=None) -> bool:
        """让视图与当前总表同源：已对齐则零成本，否则单事务全量重建。

        **只给后台线程调用**——重建是十万条量级的动作，压在点击路径上就是卡顿；
        搜索路径只看 sync_store 的结果（store_ready），不自己触发重建。
        """
        st = self.store()
        fp = self.master_fingerprint()
        if st is None or fp is None or not self._master_cache:
            return False
        if self._view_fp == fp:
            return True
        if st.is_current(fp):
            self._view_fp = fp
            return True
        if st.rebuild(self._master_cache, fp, progress_cb):
            self._view_fp = fp
            return True
        self._view_fp = None
        return False

    def store_ready(self) -> bool:
        """视图当前可用来查询：纯状态判断，不触发任何构建、不等磁盘。"""
        if self._store is None or self._view_fp is None:
            return False
        return self._view_fp == self._master_fp

    def search_candidates(self, query: str, regions: Optional[List[str]] = None,
                          filters: Optional[dict] = None,
                          limit: int = 3000) -> Optional[List[dict]]:
        """从视图取候选记录：全文召回 + 分区裁剪 + 筛选下压全部在 SQL 里完成。

        返回 None 表示视图不可用或未对齐 → 调用方必须回退倒排索引/全表扫描，
        绝不能把「视图还没建」当成「库里没有」。候选规模有界（默认 3000）：
        BM25 决定谁进精排集合，最终相关度仍由 CompanyMatcher 用同一批 token
        精确算分——两级检索（召回/精排）是搜索引擎的标准形态。
        """
        if not self.store_ready():
            return None
        try:
            return self._store.search(query, regions=regions, filters=filters,
                                      limit=limit)
        except Exception:
            return None

    def store_count(self, query: str = "", regions: Optional[List[str]] = None,
                    filters: Optional[dict] = None) -> Optional[int]:
        """池内命中总数（「共 N 条」）：count(*) 在库内算，不把记录读进 Python。

        与 search_candidates 走同一个 _resolve_expr 召回决策，界面上「找到 N 条」
        与「池内共 M 条」因此永远出自同一次判定，不会互相打脸。
        返回 None = 视图不可用 → 调用方不许把 None 显示成 0 条。
        """
        if not self.store_ready():
            return None
        try:
            return self._store.count(query, regions=regions, filters=filters)
        except Exception:
            return None

    def store_count_capped(self, query: str = "",
                           regions: Optional[List[str]] = None,
                           filters: Optional[dict] = None,
                           cap: Optional[int] = None
                           ) -> Optional[Tuple[int, bool]]:
        """界面用的封顶计数，返回 (条数, 是否精确)。

        与 store_count 唯一的区别是数到 cap 就收尾：百万级宽词的精确计数是秒级
        （100 万行 / 22.4 万命中，同一台机器两次实测 2.08s 与 4.18s；封顶 10 万
        只要 174ms），而界面这两个总数只是给用户判断「范围大不大」，不需要精确。
        返回 None = 视图不可用；exact=False 时调用方必须把「以上」说出来。
        """
        if not self.store_ready():
            return None
        try:
            # cap 留空由 store 在调用点读 COUNT_CAP（常量只在这一处生效，便于压测/回归改档）
            return self._store.count_capped(query, regions=regions,
                                            filters=filters, cap=cap)
        except Exception:
            return None

    def store_facets(self, column: str, query: str = "",
                     regions: Optional[List[str]] = None,
                     limit: int = 80,
                     filters: Optional[dict] = None
                     ) -> Optional[List[Tuple[str, int]]]:
        """维度分布（省份/行业/来源/上传者），聚合在库内完成，不读记录进 Python。

        filters 必须一路带到 SQL：概览卡片与分布条形图同屏出现，分布按「不带
        筛选」统计、旁边的数字按「带筛选」统计，就是同一屏两套口径（历史上
        真就这么接的线，勾选筛选后分布数字与总数互相打脸）。
        """
        if not self.store_ready():
            return None
        try:
            return self._store.facets(column, query=query, regions=regions,
                                      filters=filters, limit=limit)
        except Exception:
            return None

    def store_stats(self) -> Optional[Dict[str, Any]]:
        """全库概览（条数/来源/上传者/属地分布），视图未就绪返回 None。"""
        if not self.store_ready():
            return None
        try:
            return self._store.stats()
        except Exception:
            return None

    def reach_summary(self) -> Optional[Dict[str, int]]:
        """联系方式覆盖度：能直接外呼的行 vs 只有官网/地址的线索行。

        优先 Gold 视图（一条带 flags 的 count 在库内跑完，不读记录进 Python）；
        视图不在才扫内存总表。两条路径判据同一条（pool_matcher.contact_flags 非零），
        否则「概览写的可外呼数」与「勾了只看有联系方式筛出的数」会互相打脸。
        返回 None = 既没视图也没总表，调用方不许把 None 显示成 0 家。
        """
        st = self.store_stats()
        if st and "reachable" in st:
            total = int(st.get("total") or 0)
            reach = int(st.get("reachable") or 0)
            return {"total": total, "reachable": reach, "lead_only": total - reach}
        if not self._master_cache:
            return None
        try:
            from pool_matcher import contact_flags
        except Exception:
            return None      # 判据实现只有一份，缺了就宁可不报，不抄一份宽松的
        total = len(self._master_cache)
        reach = sum(1 for r in self._master_cache if contact_flags(r))
        return {"total": total, "reachable": reach, "lead_only": total - reach}

    # ── 上传 ──

    @staticmethod
    def _normalize_partition_fields(records: List[dict]) -> List[dict]:
        """批量补齐记录级派生字段：属地（is_domestic/region）+ 行业/省份/质量分。

        国内外链路贯通双保险：写侧（upload_session）显式落字段，
        存量无该字段的记录在 OSS 往返层与本地缓存层各自入口补齐，
        下游（搜索/统计/导出）永远拿到显式值；下划线运行时键
        （_region_key/_region_ver）不得写进 OSS/磁盘。

        行业/省份/质量分同样**落进记录本身**（Silver 层
        ingest_cleaning.enrich_partition_fields，只填空缺不覆盖已有值）：
        物化视图要拿它们当分区列做裁剪与聚合，靠查询时现算等于每次搜索
        重跑一遍分类器。清洗层导入失败（打包态缺依赖）时属地仍按本函数
        归一，其余字段保持原样——降级不改变判据语义。
        """
        try:
            from ingest_cleaning import enrich_partition_fields
            enrich_partition_fields(records)
        except Exception:
            pass
        for rec in records:
            if not isinstance(rec, dict):
                continue
            region = record_region(rec)
            rec["is_domestic"] = region == "dom"
            rec["region"] = region
            # 打上判据版本：下一轮读到这个值才允许走快捷路径，否则重算。
            # 少了这一步，属地判据永远无法在存量数据上升级（自锁缺陷）。
            rec["region_rule"] = REGION_RULE_VERSION
            rec.pop("_region_key", None)
            rec.pop("_region_ver", None)
        return records

    # 旧名保留为别名（历史调用方/离线回归仍引用），语义完全相同
    _normalize_is_domestic = _normalize_partition_fields

    def upload_session(self, records: List[dict], tag: str = "",
                       progress_callback=None,
                       report_out: Optional[dict] = None) -> str:
        """上传本次采集记录到 users/{username}/upload_{ts}_{uuid}[_p{i}of{n}].json。

        records: 已标准化的记录列表（由 normalize_row / read_records_from_excel 产出）
        tag: 可选备注标签（写入文件名方便识别）
        progress_callback: 可选，每步完成后调用 callback(step_text)。
        report_out: 传入空 dict 则填充清洗报告（与 Excel 导入同一口径）。
        返回上传的文件名（分片时返回「首片名 等 N 个文件」）。

        并发安全（append-only 原始层）：
          · 文件名带 uuid 短后缀——同一用户名在多台设备上同时上传也不
            会撞名互相覆盖（秒级时间戳同秒必重复）
          · 原子上传（临时 key → copy → delete），搜索端读不到半写文件
          · 上传层不去重：各写各的原始文件；去重发生在读取端合并时
            （按 company_norm 分组取并集，幂等，重复上传不会产生脏数据）

        数据质量（Bronze→Silver 口径，2026-11-10 起前移到写侧）：
          · 清洗在这里统一执行一次：爬虫路径与 Excel 导入路径共用同一入口，
            无效实体名/正文垃圾/号码碎片在入库前就被修掉或剔除并可回溯
          · 有效性门禁（电话/固话/邮箱/官网五取一，不含微信）由清洗复用，
            绝不在两处各写一份规则

        分片（百万级）：
          · 单对象上限 SHARD_RECORD_LIMIT 条——几十万条一体 PUT 是一个几十
            MB 的原子请求，弱网下失败即整批重来；分片后单片失败只重传该片，
            读取端本来就按文件并列消费（append-only），分片对下游透明
        """
        # ── 入库前清洗（含有效性门禁）──
        # 这一步先播报再执行：界面过去只说「正在上传 N 条」，用户看不出上传
        # 之前还有一道整理与筛除，等结果条数比提交数少才发现被洗过
        original_count = len(records)
        if progress_callback and original_count:
            progress_callback(f"正在整理 {original_count} 条记录"
                              f"（修正格式、派生国内外与行业、去掉无效行）…")
        records, report = _clean_records(records, label="上传前清洗")
        if report_out is not None:
            report_out.update(report)
        if progress_callback and report.get("dropped"):
            progress_callback(f"整理完成：{report.get('in', original_count)} 条里 "
                              f"{report['dropped']} 条不符合入库条件，已跳过")
        if not records:
            reasons = report.get("drop_reasons") or {}
            detail = "、".join("%s %d 条" % (k, v)
                              for k, v in sorted(reasons.items(),
                                                 key=lambda kv: -kv[1]))
            raise OSSPoolError(
                f"{original_count} 条记录清洗后无可上传数据"
                + (f"（剔除明细：{detail}）" if detail else "，全部为无效数据")
            )
        # 国内外归属显式落字段（读侧分区过滤的数据基础），
        # 同时清掉 record_region 写在记录上的运行时缓存键，不得进 OSS
        records = DataPoolManager._normalize_partition_fields(records)
        now = datetime.now()
        ts = now.strftime("%Y%m%d_%H%M%S")
        suffix = f"_{tag}" if tag else ""
        batch = uuid.uuid4().hex[:8]

        limit = max(1, int(self.SHARD_RECORD_LIMIT))
        shards = [records[i:i + limit] for i in range(0, len(records), limit)]
        total = len(shards)

        filenames: List[str] = []
        for idx, shard in enumerate(shards, start=1):
            part = f"_p{idx}of{total}" if total > 1 else ""
            filename = f"upload_{ts}_{batch}{part}{suffix}.json"
            key = f"users/{self.username}/{filename}"
            if progress_callback and total > 1:
                progress_callback(f"正在上传第 {idx}/{total} 批"
                                  f"（{len(shard)} 条）…")
            payload = {
                "username": self.username,
                "timestamp": now.isoformat(timespec="seconds"),
                "record_count": len(shard),
                "shard": idx,
                "shard_count": total,
                "records": shard,
            }
            self.oss.upload_json_atomic(
                key, payload,
                progress_callback=None if total > 1 else progress_callback)
            filenames.append(filename)

        label = (filenames[0] if total == 1
                 else f"{filenames[0]} 等 {total} 个文件")
        # 更新自己的 meta 分片（后台线程，不阻塞主上传流程）
        # 统计更新失败不影响数据正确性，下次上传会自动补上
        threading.Thread(
            target=self._bg_update_meta,
            args=(label, len(records)),
            daemon=True,
        ).start()
        return label

    def upload_from_excel(self, excel_path: str) -> Tuple[str, int]:
        """从清洗后的 Excel 文件读取并上传。

        返回 (filename, record_count)。
        """
        records = self.read_records_from_excel(excel_path)
        if not records:
            raise OSSPoolError(f"文件无可导入的记录: {excel_path}")
        filename = self.upload_session(records)
        return filename, len(records)

    def user_meta_key(self) -> str:
        """当前用户的 meta 分片 key（每用户一个文件，只写自己的）。"""
        return f"users/{self.username}/meta.json"

    def _bg_update_meta(self, last_file: str, last_count: int):
        """后台线程更新 meta 分片（不阻塞主上传流程）。

        统计更新是 best-effort：失败不影响数据正确性，下次上传自动补上。
        """
        try:
            self._update_user_meta(last_file, last_count)
        except Exception:
            pass  # 后台统计更新失败静默忽略

    def _update_user_meta(self, last_file: str, last_count: int,
                          progress_callback=None):
        """写当前用户的 meta 分片（users/{username}/meta.json）。

        旧版把全部用户统计写在共享 master_meta.json 里，读-改-写三步间
        任何人上传都会互相覆盖丢统计；改为每用户只写自己的分片 key
        （分片聚合思想），天然无竞态，统计由 get_master_stats 聚合。
        分片内 total_uploads 累计仍是读-改-写——同一用户名多设备同时
        上传时统计可能少计一次，但只影响统计数字，不影响数据正确性。

        优化：首次上传时 meta 不存在，直接写入初始值（省一次下载）。
        """
        info = {"username": self.username, "total_uploads": 0, "upload_count": 0}
        meta_key = self.user_meta_key()
        try:
            old, _ = self.oss.download_json(meta_key)
            if isinstance(old, dict):
                info["total_uploads"] = int(old.get("total_uploads", 0) or 0)
                info["upload_count"] = int(old.get("upload_count", 0) or 0)
        except OSSPoolError:
            pass  # 首次上传时分片不存在，用初始值
        except (TypeError, ValueError):
            pass  # meta 损坏，用初始值
        info["total_uploads"] += last_count
        info["upload_count"] += 1
        info["last_upload"] = datetime.now().isoformat(timespec="seconds")
        info["last_file"] = last_file
        info["last_count"] = last_count
        if progress_callback:
            progress_callback("正在更新统计信息…")
        self.oss.upload_json(meta_key, info)

    # ── 合并总表 ──

    def get_merged_master(self, force_refresh: bool = False) -> List[dict]:
        """获取合并后的总表（本地缓存 + OSS 增量合并）。

        大厂秒开思想（Google Drive / Dropbox）：
          · 有本地缓存 → 立即返回（不等 OSS 网络），后台静默同步
          · 无本地缓存（首次）→ 全量下载
          · force_refresh=True → 强制全量重建
          · 网络失败降级本地缓存（可用性优先）
        """
        # 内存缓存（同一次搜索会话内不重复走网络）
        if (self._master_cache is not None
                and not force_refresh
                and (time.time() - self._master_cache_time) < 60):
            return self._master_cache

        if force_refresh:
            master = self._full_rebuild()
            self._set_master(master)
            return master

        # 无内存缓存 → 先加载本地三件套
        if self._master_cache is None:
            self._load_local_state()

        if self._master_cache is None:
            # 本地无缓存（首次使用）→ 全量下载
            master = self._full_rebuild()
            self._set_master(master)
            return master

        # 有本地缓存 → 立即返回（不等 OSS，后台由 refresh_from_oss 静默同步）
        self._master_cache_time = time.time()
        return self._master_cache

    def refresh_from_oss(self) -> Optional[List[dict]]:
        """后台静默同步：检查 OSS 增量 → 合并 → 更新本地缓存。

        调用方（UI 线程）拿到本地缓存后立即展示，再调此方法做后台同步。
        无新数据返回 None；有新数据返回合并后的总表；网络失败返回 None。
        线程安全：内部用锁保护 _master_cache 读写。
        """
        if not hasattr(self, '_refresh_lock'):
            import threading
            self._refresh_lock = threading.Lock()
        with self._refresh_lock:
            try:
                # 删除向量也要同步：别的同事剔了脏数据，本机只认缓存里的旧标记
                # 就会继续搜到它。列表成本一次 ListObjects（对象极少），可忽略。
                self.refresh_tombstones()
                removed = self._resync_master_with_tombstones()
                upload_keys = self._list_upload_keys()
            except OSSPoolError:
                return None  # 网络失败，静默跳过
            new_keys = [k for k in upload_keys if k not in self._consumed_keys]
            if not new_keys:
                if removed:
                    # 没有新增数据但剔掉了记录：把结果如实返回，调用方据此刷新界面
                    return self._master_cache
                return None  # 无增量
            new_records, durable = self._download_records(new_keys)
            master = self._apply_increment(new_records, new_keys, durable)
            return master

    def _full_rebuild(self) -> List[dict]:
        """全量重建：下载全部用户文件合并 + 全量构建索引与游标。"""
        try:
            self.refresh_tombstones()          # 删除向量先就位，再参与合并
            upload_keys = self._list_upload_keys()
            records, durable = self._download_records(upload_keys)
            master = self._apply_tombstones(self._merge_records(records))
        except OSSPoolError as e:
            # 全量下载失败：尝试用本地缓存兑底。这里必须容忍判据版本过期的缓存
            # ——离线时给用户看旧规则洗出的数据，远好于把「网络不通」表现为
            # 「数据池没有数据」；联网后的下一次加载会自动按新版本重建回刷。
            if self._load_local_state(allow_stale_rules=True):
                return self._master_cache
            raise OSSPoolError(f"无法获取数据池（网络错误: {e}）")
        index = self._build_inverted_index(master)
        self._save_local_state(master, index, self._consumed_keys | durable)
        return master

    def _apply_increment(self, new_records: List[dict],
                         new_keys: List[str],
                         durable_keys: Optional[set] = None) -> List[dict]:
        """把增量记录合并进本地总表，并增量扩充倒排索引。

        游标只推进 durable_keys（下载成功或永久性坏文件）；瞬时失败的 key
        留在原地等下一轮重试，绝不因为一次抖动就永久丢弃整批数据。

        关键不变量：倒排索引记录的是「总表下标」，只有旧组位置保持不变时
        才能增量追加。_merge_records 里的模糊归并可能把两条旧组合并成一条，
        使旧记录下标整体前移——所以这里**显式校验**前 len(old) 位的
        company_norm 序列是否与旧表一致（一次字符串比较，代价可忽略），
        不再拿「记录数相等」这种弱代理当同源性证明。
        """
        old = self._master_cache or []
        durable = set(durable_keys) if durable_keys is not None else set(new_keys)
        if not new_records:
            # 本批增量没有任何可用记录（网络中断或全是坏文件）：
            # 只把确实读过的 key 计入游标，坏文件不会反复拖慢同步
            self._save_local_state(old, self._load_inverted_index(),
                                   self._consumed_keys | durable)
            return old

        new_norms = set()
        for rec in new_records:
            norm = rec.get("company_norm") or normalize_company_name(
                rec.get("company", ""))
            if norm:
                new_norms.add(norm)

        old_norms = [rec.get("company_norm") for rec in old]
        merged = self._apply_tombstones(self._merge_records(list(old) + list(new_records)))

        # 增量索引（索引与旧表不同源时退回全量重建）
        index = self._load_inverted_index()
        positions_stable = (len(merged) >= len(old)
                            and [rec.get("company_norm")
                                 for rec in merged[:len(old)]] == old_norms)
        if (not index or index.get("record_count") != len(old)
                or not positions_stable):
            index = self._build_inverted_index(merged)
        else:
            # 只给「被新数据触及的组」追加 token：合并是取并集，文本只增不减，
            # 因此旧组位置不漂移，追加即可等价于重建
            from pool_matcher import search_fields, tokenize
            tokens = index.setdefault("tokens", {})
            dirty = set()
            for i, rec in enumerate(merged):
                if rec.get("company_norm") not in new_norms:
                    continue  # 未被触及的旧组：位置与内容不变
                for token in set(tokenize(" ".join(search_fields(rec)))):
                    tokens.setdefault(token, []).append(i)
                    dirty.add(token)
            # 每个热 token 只排一次重，绝不在追加时用「i not in lst」逐项查重：
            # 那种写法对 postings 长度是线性的，热 token（如 6.6 万条的「光伏」）
            # 直接让增量同步退化成 O(n²)——实测一片 68,935 条的存量对象把一轮
            # 增量拖到 5 分钟以上。旧 postings 升序、本轮 i 也升序，但被触及的旧组
            # 位置可能插在新段之前，所以对**本轮被改过的** token 各去重排序一次。
            for token in dirty:
                lst = tokens[token]
                lst[:] = sorted(set(lst))
            index["record_count"] = len(merged)

        self._save_local_state(merged, index,
                               self._consumed_keys | set(new_keys))
        # 内存总表跟着增量走：不同步的话下一轮「同源校验」会拿旧表长度
        # 去校验新索引，把明明同源的索引判成不可信
        view_live = self._view_fp is not None
        self._set_master(merged)
        self._advance_view(merged, new_norms, positions_stable, view_live)
        return merged

    def _advance_view(self, merged: List[dict], touched_norms: set,
                      positions_stable: bool, was_live: bool) -> bool:
        """视图本来在线时，用 upsert 把它推到最新总表；推不动就作废等后台重建。

        was_live 为假（首次运行 / 上次构建失败 / 刚作废）时这里什么都不做——
        构建是后台 sync_store 的职责，不该由同步顺手触发一次十万条量级的重建。

        positions_stable 为假说明模糊归并动过旧组位置（两条旧合成一条），
        主键集合本身变了，upsert 表达不了「某组消失」，只能整表重建。
        """
        if not was_live:
            return False
        st = self.store()
        if st is None:
            self._invalidate_view()
            return False
        from pool_store import fingerprint_records
        fp = fingerprint_records(merged)
        self._master_fp = fp
        if self._view_fp == fp:
            return True               # 这批增量没有改变任何内容，视图照旧可用
        if not positions_stable or not touched_norms:
            self._view_fp = None
            return False
        touched = [rec for rec in merged if rec.get("company_norm") in touched_norms]
        self._view_fp = fp if (touched and st.upsert(touched, fp)) else None
        return self._view_fp == fp

    def _list_keys_cached(self, sub_prefix: str) -> List[str]:
        """带 30s TTL 缓存的 list_keys（同一批次操作内避免重复网络调用）。

        OSS ListObjects 是低频变更的目录树查询，30s 内结果不会变化；
        get_merged_master + get_master_stats 同批次调用时共享同一份结果，
        网络请求从 2~N 次降到 1 次。
        """
        now = time.time()
        if (sub_prefix in self._oss_keys_cache
                and now - self._oss_keys_cache_time < 30):
            return self._oss_keys_cache[sub_prefix]
        keys = self.oss.list_keys(sub_prefix)
        self._oss_keys_cache[sub_prefix] = keys
        self._oss_keys_cache_time = now
        return keys

    def _list_upload_keys(self) -> List[str]:
        """列出所有用户上传文件的 key（meta 分片等非 upload 文件自动排除）。

        显式排序：合并是顺序敏感的（组内 base 取首现、_first_nonempty 取首个非空），
        所以喂给合并的记录流必须有**规范全序**。OSS 分页拼接结果不保证跨请求稳定，
        排序后同一批 Bronze 对象在任何一次重建里都按同一顺序被消费。
        """
        all_keys = self._list_keys_cached("users/")
        return sorted(k for k in all_keys if k.endswith(".json") and "/upload_" in k)

    def _download_records(self, keys: List[str]) -> Tuple[List[dict], set]:
        """下载一批上传文件里的记录，返回 (记录列表, 可推进游标的 key 集合)。

        性能：ThreadPoolExecutor 并行下载（首次全量加载从 O(n*RTT) 降到
        O(n/workers*RTT)），文件多时自适应加线程。

        失败分级（修历史缺陷：所有异常一律 return [] 并被计入 consumed_keys，
        一次网络抖动就会让整批增量文件被永久跳过、其中数据再也进不了本地总表）：
          · 永久性失败（对象不存在 / JSON 解析失败）→ 跳过并推进游标，
            避免坏文件把同步卡死在同一个位置
          · 瞬时性失败（连接/超时/5xx，已由客户端重试过）→ 不推进游标，
            下一轮同步自动重试

        确定性（修 D14：并行下载用 as_completed 装配，记录顺序 = 网络完成时序）：
        合并层顺序敏感（组内 base 取首现、_first_nonempty 取首个非空值、显式值投票
        同数时取先出现者），输入顺序漂→同一批 Bronze 数据两次全量重建给出不同的
        industry/province/quality（实测 128 组），增量合并与全量重建也不收敛。
        这里保持**并行下载**（耗时仍由最慢对象决定），只把**装配**改成按 keys
        规范序取结果：同一快照重放逐条等价，compaction 与 full rebuild 等价。
        """
        if not keys:
            return [], set()

        permanent = ("对象不存在", "JSON 解析失败")

        def download_one(key: str):
            """返回 (records, durable)；durable=False 表示本轮不推进该 key。"""
            try:
                data, _ = self.oss.download_json(key)
            except OSSPoolError as e:
                msg = str(e)
                return [], any(p in msg for p in permanent)
            except Exception:
                return [], False
            if isinstance(data, dict):
                recs = data.get("records") or []
            elif isinstance(data, list):
                recs = data
            else:
                recs = []
            return (recs if isinstance(recs, list) else []), True

        workers = min(8, max(4, len(keys) // 4))
        all_records: List[dict] = []
        durable_keys: set = set()
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {k: executor.submit(download_one, k) for k in keys}
            # 按 keys 的规范序装配（不是按完成序）：下载仍然并行
            for key in keys:
                recs, durable = futures[key].result()
                all_records.extend(recs)
                if durable:
                    durable_keys.add(key)
        # Bronze→Silver 读侧重放：名称门禁在合并前再过一遍。上传时洗过一次并不
        # 够——Bronze 是 append-only 原始层，判据升级（说明表拦截、栏目名拦截）无法
        # 回头改写历史对象，只在写侧加规则会让存量脏数据永久留在池里，出现
        # 「新导入被拦、老数据还能搜到」的口径分叉。同一份判据、同一处实现。
        all_records = _gate_names_on_read(all_records)
        # 存量兼容：早期上传文件无 is_domestic，入口统一推导补全
        return DataPoolManager._normalize_partition_fields(all_records), durable_keys

    def get_master_stats(self) -> Dict[str, Any]:
        """返回总表统计信息。

        优先从本地缓存计算（秒开，不走 OSS 网络）；
        本地缓存缺失时才回退 OSS meta 分片聚合（并行 4 线程）。
        """
        # 优先：从本地缓存计算（零网络延迟）
        local = self._compute_local_stats()
        if local is not None:
            return local

        # 回退：OSS meta 分片聚合（并行下载）
        empty = {"users": 0, "total_uploads": 0,
                 "last_update": "", "user_names": []}
        try:
            all_keys = self._list_keys_cached("users/")
        except OSSPoolError:
            return empty
        meta_keys = [k for k in all_keys if k.endswith("/meta.json")]
        if not meta_keys:
            return empty

        def _dl_meta(key: str) -> Optional[dict]:
            try:
                data, _ = self.oss.download_json(key)
                return data if isinstance(data, dict) else None
            except (OSSPoolError, Exception):
                return None

        users: Dict[str, dict] = {}
        total = 0
        last = ""
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = {executor.submit(_dl_meta, k): k for k in meta_keys}
            for future in as_completed(futures):
                data = future.result()
                if not data:
                    continue
                name = str(data.get("username")
                           or futures[future].split("/")[-2])
                users[name] = data
                try:
                    total += int(data.get("total_uploads", 0) or 0)
                except (TypeError, ValueError):
                    pass
                lu = str(data.get("last_upload", "") or "")
                if lu > last:
                    last = lu
        return {
            "users": len(users),
            "total_uploads": total,
            "last_update": last,
            "user_names": sorted(users),
        }

    def _compute_local_stats(self) -> Optional[Dict[str, Any]]:
        """概览统计：优先物化视图（几条 group by 在库内跑完），否则内存总表遍历。

        返回 None 表示本地缓存不可用，调用方应回退 OSS。

        口径修正：以前把 source（**数据来源**：网站/文件名）当成上传者去重，
        界面显示成"N 位用户"——几千个来源就是"N 千位用户"。上传者一律看
        source_user，来源另算 sources，两者各归各的语义。
        """
        if not self._master_cache:
            return None
        st = self.store_stats()
        if st is not None:
            names = [str(u) for u in (st.get("source_users") or [])]
            return {
                "users": max(int(st.get("users") or 0), 1),
                "total_uploads": int(st.get("total") or 0),
                "sources": int(st.get("sources_count") or 0),
                "last_update": str(st.get("last_update") or ""),
                "user_names": names or ["本地缓存"],
                "region": st.get("region") or {},
                "from_view": True,
            }
        users: set = set()
        sources: set = set()
        last = ""
        for rec in self._master_cache:
            u = str(rec.get("source_user", "") or "").strip()
            if u:
                users.add(u)
            src = str(rec.get("source", "") or "").strip()
            if src:
                sources.add(src)
            ts = str(rec.get("source_time", "") or "")
            if ts > last:
                last = ts
        return {
            "users": max(len(users), 1),
            "total_uploads": len(self._master_cache),
            "sources": len(sources),
            "last_update": last,
            "user_names": sorted(users) if users else ["本地缓存"],
            "from_view": False,
        }

    def _load_local_state(self, allow_stale_rules: bool = False) -> bool:
        """加载本地缓存三件套中的 master 与 consumed_keys 游标。

        索引随 master 成对保存，这里不加载——由 _load_inverted_index
        按需加载并做同源校验（record_count 与内存总表一致才可信）。
        新鲜度不再靠时间戳判断（旧版 _is_cache_stale 每次多一次
        master_meta.json 下载，且共享 meta 会被并发覆盖丢数据），
        增量路径用 list_keys diff consumed_keys 游标保证。

        allow_stale_rules=False（默认）时校验判据版本：本地总表是用旧规则洗出来
        的，直接作废让上层走全量重建，判据升级才能自动回刷到存量数据。
        只在「网络已经失败、只剩这份缓存可用」的兜底路径上传 True——
        宁可给用户看旧规则洗出的数据，也不要在离线时把池子清空。
        """
        cache_file = self._cache_dir / "master.json"
        if not cache_file.exists():
            return False
        if not allow_stale_rules and not self._cache_rules_current():
            return False
        try:
            master = json.loads(cache_file.read_text(encoding="utf-8"))
            if not isinstance(master, list):
                return False
            # 存量兼容：旧版缓存三件套可能无 is_domestic，加载即补全
            self._set_master(DataPoolManager._normalize_partition_fields(master))
            self._consumed_keys = self._read_consumed_keys()
            return True
        except Exception:
            return False

    def _cache_rules_current(self) -> bool:
        """本地缓存是否由当前 Silver 判据生成（版本文件缺失/不符都算过期）。

        旧版本客户端留下的缓存没有 rule_version.json —— 判成过期，第一次升级
        后自动做一次全量重建，之后长期零成本。
        """
        try:
            data = json.loads(
                (self._cache_dir / "rule_version.json").read_text(encoding="utf-8"))
            return isinstance(data, dict) and data.get("silver") == _silver_rule_version()
        except Exception:
            return False

    def _read_consumed_keys(self) -> set:
        """读已消费的 OSS 上传文件 key 集合（增量合并的游标）。"""
        p = self._cache_dir / "consumed_keys.json"
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return {str(k) for k in data}
        except Exception:
            pass
        return set()

    def _save_local_state(self, master: List[dict],
                          index: Optional[dict], consumed: set):
        """把总表 + 倒排索引 + consumed_keys 成对写到本地磁盘。"""
        try:
            (self._cache_dir / "master.json").write_text(
                json.dumps(master, ensure_ascii=False),
                encoding="utf-8",
            )
            if index is not None:
                (self._cache_dir / "inverted_index.json").write_text(
                    json.dumps(index, ensure_ascii=False),
                    encoding="utf-8",
                )
            (self._cache_dir / "consumed_keys.json").write_text(
                json.dumps(sorted(consumed), ensure_ascii=False),
                encoding="utf-8",
            )
            # 判据版本与总表成对落盘：这份 master 只在「全量重建」或「版本已校验
            # 的缓存 + 过门禁的增量」之后才会被写，所以盖当前版本是成立的断言
            (self._cache_dir / "rule_version.json").write_text(
                json.dumps({"silver": _silver_rule_version()}, ensure_ascii=False),
                encoding="utf-8",
            )
            self._consumed_keys = set(consumed)
        except Exception:
            pass  # 缓存写入失败不影响主流程

    @staticmethod
    def _build_inverted_index(master: List[dict]) -> dict:
        """构建倒排索引：token → [record_index, ...]。

        可索引字段一律取 pool_matcher.search_fields（口径唯一实现）：这里以前
        自己列了「公司名+主营+地址+联系人」，比评分侧少了国家/官网/来源/职位/
        号码/邮箱/微信——同一份数据会出现「索引说没命中、评分说命中」的静默漏召回，
        而且 FTS5 视图与倒排索引两条链路的召回集还会互相不一致。
        搜索时只需查索引即可定位候选记录，无需全量扫描（百万级 30s → <1s）。
        """
        from pool_matcher import search_fields, tokenize
        index: Dict[str, List[int]] = {}
        for idx, rec in enumerate(master):
            for token in set(tokenize(" ".join(search_fields(rec)))):
                if token not in index:
                    index[token] = []
                index[token].append(idx)
        return {"tokens": index, "record_count": len(master)}

    def _load_inverted_index(self) -> Optional[dict]:
        """从磁盘加载倒排索引（解析结果按文件版本缓存）。

        索引与 master.json 由 _save_local_state 成对写入；仅当内存中的
        _master_cache 已加载且记录数与索引一致时才可信——不同源立即返回
        None（调用方回退全量扫描），绝不能用错位索引去定位候选。

        缓存键用文件的 (mtime, size)：索引是几 MB 到几百 MB 的 JSON，旧实现
        每次搜索都重解析一遍（实测 5.9MB 0.40s，还跑在 UI 线程上），而同一份
        索引在一次会话里被反复问几十次。落盘即版本变化，版本没变就复用解析结果。
        """
        cache_index = self._cache_dir / "inverted_index.json"
        if not cache_index.exists():
            return None
        if self._master_cache is None:
            return None  # 总表未加载，无法校验同源性
        try:
            stat = cache_index.stat()
            key = (stat.st_mtime_ns, stat.st_size)
            cached = getattr(self, "_index_cached", None)
            if cached and cached[0] == key:
                data = cached[1]
            else:
                data = json.loads(cache_index.read_text(encoding="utf-8"))
                self._index_cached = (key, data)
            if (data.get("tokens")
                    and data.get("record_count") == len(self._master_cache)):
                return data
        except Exception:
            pass
        return None

    def search_by_index(self, query: str, top_k: int = 100) -> Optional[List[int]]:
        """用倒排索引快速定位候选记录索引（不评分，只找候选）。

        返回候选记录的 index 列表（去重后）；索引缺失、总表未加载或与索引
        不同源时返回 None，调用方应回退全量扫描。
        """
        index_data = self._load_inverted_index()
        if not index_data:
            return None

        from pool_matcher import tokenize
        query_tokens = tokenize(query)
        if not query_tokens:
            return []

        token_map = index_data.get("tokens", {})
        candidate_set: set = set()
        for token in query_tokens:
            candidate_set.update(token_map.get(token, []))

        # 空命中仍返回 None（而不是空列表）：磁盘上的索引可能是**旧字段集**
        # 版本建的（少索引了号码/邮箱/微信等），此时"索引说没有"不能当成"没有"，
        # 必须让调用方回退全量扫描。真·无命中由物化视图路径负责给出空结果。
        candidates = sorted(candidate_set)
        return candidates or None

    @staticmethod
    def _merge_records(records: List[dict]) -> List[dict]:
        """合并同一实体记录（联系方式取并集，绝不丢号）。

        两段式，口径唯一实现见 pool_merge：
          1. dedup_exact —— 按 company_norm 一次哈希归并（线性）
          2. merge_duplicates_fast —— 跨组模糊归并：阻塞键分桶后只在候选块内
             调 dedup_engine.compute_duplicate_score，把 O(n²) 降到
             O(n·块大小)（实测 1600 条 122s → 0.21s，命中对完全一致）。
             阈值 0.85 不变：高精度优先，宁可漏合不可误合。

        pool_merge 缺失时**只做有效性门禁、不合并**：重复只是看起来多余，
        错误的合并口径才会真丢数据——降级方向必须选前者。
        """
        if not records:
            return []
        if not _HAS_MERGE:
            return _rederive_after_merge(
                _filter_invalid_records(records, label="合并后终检"))

        merged = dedup_exact(records)
        if _HAS_DEDUP and len(merged) > 1:
            try:
                merged = merge_duplicates_fast(merged, threshold=0.85)
            except Exception:
                pass  # 模糊归并失败时回退到精确合并结果

        # ── 数据质量门禁：合并后终检，清除历史垃圾数据 ──
        return _rederive_after_merge(
            _filter_invalid_records(merged, label="合并后终检"))

    # ── 记录标准化 ──

    @staticmethod
    def normalize_row(row: dict) -> Optional[dict]:
        """从清洗后的 18 列行格式转为标准记录格式。

        18 列 = 国内 9 列 + 国外 9 列（双区块），取有数据的那一侧。
        返回 None 表示该行无可提取的公司名。
        """
        # 国内 9 列
        dom_company = (row.get("客户名称") or row.get("企业名称") or "").strip()
        # 国外 9 列（列名带 .1 后缀）
        foreign_company = (
            row.get("客户名称.1") or row.get("企业名称.1") or ""
        ).strip()

        company = dom_company or foreign_company
        is_domestic = bool(dom_company)

        if not company:
            return None

        # 提取联系方式（两侧都取，合并去重）
        phones = _split_field(row.get("手机号", "")) + _split_field(row.get("手机号.1", ""))
        mobiles = _split_field(row.get("手机号", "")) + _split_field(row.get("手机号.1", ""))
        landlines = _split_field(row.get("固话", "")) + _split_field(row.get("固话.1", ""))
        emails = _split_field(row.get("邮箱", "")) + _split_field(row.get("邮箱.1", ""))
        wechats = _split_field(row.get("微信号", "")) + _split_field(row.get("微信号.1", ""))

        # 去重
        phones = list(dict.fromkeys(p for p in phones if p))
        mobiles = list(dict.fromkeys(m for m in mobiles if m))
        landlines = list(dict.fromkeys(l for l in landlines if l))
        emails = list(dict.fromkeys(e for e in emails if e))
        wechats = list(dict.fromkeys(w for w in wechats if w))

        # 联系人
        contact_name = (row.get("岗位") or "").strip()
        contacts = [{"name": contact_name, "title": ""}] if contact_name else []

        # 备注/外呼备注
        remark = (row.get("外呼备注") or row.get("备注") or row.get("备注.1") or "").strip()
        # 从备注中提取主营（格式 "主营:xxx"）
        products_from_remark = ""
        if "主营:" in remark:
            pm = re.search(r'主营:([^｜]+)', remark)
            if pm:
                products_from_remark = pm.group(1).strip()

        # 从备注中提取官网（格式 "官网:xxx"）
        website_from_remark = ""
        if "官网:" in remark:
            wm = re.search(r'官网:([^｜]+)', remark)
            if wm:
                website_from_remark = wm.group(1).strip()

        # 从备注中提取国家/地址
        address_from_remark = ""
        if "地址:" in remark:
            am = re.search(r'地址:([^｜]+)', remark)
            if am:
                address_from_remark = am.group(1).strip()

        # 数据来源（大厂 Data Provenance 标准：统一清洗逻辑）
        source = DataPoolManager._resolve_source(
            row.get("数据来源") or row.get("数据来源.1") or "",
            website_from_remark,
            "",  # 清洗行路径无文件名，兜底为空
        )

        return {
            "company": company,
            "company_norm": normalize_company_name(company),
            "contacts": contacts,
            "title": contact_name,  # 岗位/职位
            "phones": phones + landlines,  # 合并所有电话（评分用）
            "mobiles": mobiles,
            "landlines": landlines,
            "emails": emails,
            "wechats": wechats,
            "website": website_from_remark,
            "products": products_from_remark,
            "industry": "",  # 清洗后 18 列格式不含行业
            "address": address_from_remark,  # 清洗后 18 列格式的地址在备注里
            "country": "",  # 清洗后 18 列格式不含国家
            "source": source,
            "source_user": DataPoolManager._get_username_static(),
            "source_time": datetime.now().isoformat(timespec="seconds"),
            "is_domestic": is_domestic,
        }

    @staticmethod
    def raw_record_to_standard(raw_rec: dict) -> Optional[dict]:
        """将爬虫原始记录直接转为数据池标准格式（12 项字段全覆盖）。

        原始记录字段名（各站 _base_rec 统一口径）：
          公司名称, 联系人, 岗位, 电话, 手机, 邮箱, 地址, 主营产品/主营商品,
          经营模式, 所在区域, 公司网址, 详情URL, 数据来源, 采集时间, 状态

        标准格式字段（12 项）：
          contact(客户姓名), title(职位), company(公司名), phone(电话),
          email(邮箱), landline(固话), website(官网), products(主营),
          industry(行业), address(公司地址), country(国家), source(数据来源)
        """
        company = (raw_rec.get("公司名称") or "").strip()
        if not company:
            return None

        # ── 联系人 + 职位 ──
        contact_name = (raw_rec.get("联系人") or "").strip()
        title = (raw_rec.get("岗位") or "").strip()
        contacts = []
        if contact_name or title:
            contacts = [{"name": contact_name, "title": title}]

        # ── 电话分类（手机 vs 固话）──
        mobile_raw = (raw_rec.get("手机") or "").strip()
        tel_raw = (raw_rec.get("电话") or "").strip()
        mobiles = _split_field(mobile_raw)
        tels_raw = _split_field(tel_raw)
        # 电话列可能混入手机号（商家把手机写在「电话」栏），按形态分类
        mobiles_set = set(mobiles)
        landlines = []
        for t in tels_raw:
            digits = re.sub(r"\D", "", t)
            if re.fullmatch(r"1[3-9]\d{9}", digits) and t not in mobiles_set:
                mobiles.append(t)
                mobiles_set.add(t)
            elif t:
                landlines.append(t)
        mobiles = list(dict.fromkeys(m for m in mobiles if m))
        landlines = list(dict.fromkeys(l for l in landlines if l))

        # ── 邮箱 ──
        emails = _split_field(raw_rec.get("邮箱") or "")
        emails = list(dict.fromkeys(e for e in emails if e))

        # ── 官网（公司网址 > 详情URL）──
        website = (raw_rec.get("公司网址") or raw_rec.get("详情URL") or "").strip()

        # ── 主营产品 ──
        products = (raw_rec.get("主营产品") or raw_rec.get("主营商品") or "").strip()

        # ── 行业 ──
        industry = (raw_rec.get("行业") or raw_rec.get("所属行业") or "").strip()

        # ── 地址（地址 + 所在区域 合并）──
        addr = (raw_rec.get("地址") or "").strip()
        region = (raw_rec.get("所在区域") or "").strip()
        if region and region not in addr:
            address = f"{region} {addr}".strip() if addr else region
        else:
            address = addr

        # ── 国家（多信号推断）──
        country = (raw_rec.get("国家") or "").strip()
        if not country:
            # 从所在区域/地址推断国内
            if region or addr:
                country = "中国"
            else:
                # 从公司名后缀推断
                if re.search(r'(Inc\.|LLC|GmbH|Ltd\.|Co\.,\s*Ltd|S\.A\.|Pty|Corp)', company):
                    country = ""  # 海外公司，但具体国家无法确定
                else:
                    country = "中国"  # 默认国内

        # ── 数据来源（大厂 Data Provenance 标准：统一清洗逻辑）──
        source = DataPoolManager._resolve_source(
            raw_rec.get("数据来源") or "",
            website,  # 公司网址/详情URL
            "",       # 爬虫路径无文件名，兜底为空
        )

        # ── 国内外判定 ──
        is_domestic = country == "中国"
        if not is_domestic:
            # 中国手机号是铁证
            if mobiles and any(re.fullmatch(r"1[3-9]\d{9}", re.sub(r"\D", "", m)) for m in mobiles):
                is_domestic = True
                country = "中国"
            # 国内邮箱域名
            elif emails and any(re.search(r'@(qq|163|126|sina|sohu|aliyun|139)\.', e.lower()) for e in emails):
                is_domestic = True
                country = "中国"

        return {
            "company": company,
            "company_norm": normalize_company_name(company),
            "contacts": contacts,
            "title": title,
            "phones": mobiles + landlines,
            "mobiles": mobiles,
            "landlines": landlines,
            "emails": emails,
            "wechats": _split_field(raw_rec.get("微信号") or ""),
            "website": website,
            "products": products,
            "industry": industry,
            "address": address,
            "country": country,
            "source": source,
            "source_user": DataPoolManager._get_username_static(),
            "source_time": raw_rec.get("采集时间") or datetime.now().isoformat(timespec="seconds"),
            "is_domestic": is_domestic,
        }

    @staticmethod
    def _get_username_static() -> str:
        try:
            name = getpass.getuser()
            return re.sub(r"[^\w\u4e00-\u9fff]", "_", name).strip("_") or "unknown"
        except Exception:
            return "unknown"

    @staticmethod
    def _extract_domain_from_url(url: str) -> str:
        """从 URL 中提取主域名（用于智能数据溯源）。

        大厂数据溯源（Data Provenance）标准：当数据缺少显式来源字段时，
        从公司网址/官网 URL 提取域名作为来源。

        示例：
          https://hjgcadmin.cn.b2b168.com/ → b2b168.com
          https://wangminhong.cn.made-in-china.com/ → made-in-china.com
          https://www.made-in-china.com/... → made-in-china.com
          https://example.com.cn/path → example.com.cn
          http://1688.com → 1688.com
          https://b2b.bjx.com.cn → b2b.bjx.com.cn
        """
        if not url:
            return ""

        url = url.strip()
        # 去除协议头
        url = re.sub(r'^https?://', '', url)
        # 去除路径
        url = url.split('/')[0]
        # 去除端口
        url = url.split(':')[0]

        # 匹配域名模式
        domain_match = re.search(
            r'([a-zA-Z0-9][-a-zA-Z0-9]*\.)+(com(\.cn)?|cn|net|org)', url
        )
        if not domain_match:
            return url

        full_domain = domain_match.group(0)
        parts = full_domain.split('.')

        # Case 1: B2B 平台模式 —— {任意子域名}.cn.{平台}.{tld}
        # 必须在 ccTLD 判定之前检查，因为 test.cn.b2b.bjx.com.cn 中 .cn. 是子域名标记
        # e.g., hjgcadmin.cn.b2b168.com → b2b168.com
        # e.g., wangminhong.cn.made-in-china.com → made-in-china.com
        # e.g., test.cn.b2b.bjx.com.cn → b2b.bjx.com.cn
        if len(parts) >= 4:
            for i in range(1, len(parts) - 1):
                if parts[i] == 'cn':
                    # 找到中间的 .cn.，返回其后所有部分
                    return '.'.join(parts[i + 1:])

        # Case 2: 以 .com.cn / .net.cn / .org.cn 结尾的中国 ccTLD
        # e.g., b2b.bjx.com.cn → 保持原样（这是平台的真实域名）
        if (len(parts) >= 3 and parts[-1] == 'cn'
                and parts[-2] in ('com', 'net', 'org')):
            # 仅去除 www/m/en 等常见子域名前缀
            if len(parts) >= 4 and parts[0] in ('www', 'm', 'en'):
                return '.'.join(parts[1:])
            return full_domain

        # Case 3: 常见子域名前缀（www / m / en / cn）
        # e.g., www.made-in-china.com → made-in-china.com
        # e.g., cn.made-in-china.com → made-in-china.com
        if len(parts) >= 3 and parts[0] in ('www', 'm', 'en', 'cn'):
            return '.'.join(parts[1:])

        # Case 4: 普通域名，直接返回
        return full_domain

    @staticmethod
    def _clean_source_name(filename: str) -> str:
        """从文件名提取清洁的数据来源域名。

        大厂数据溯源（Data Lineage）清洁度标准：来源字段必须记录真实来源平台，
        不得包含内部编号、日期、人名等元数据。

        示例：
          ZHY20260721CJb2b.bjx.com.cn黄芷茵.xlsx → b2b.bjx.com.cn
          ZHY20260729CJca800.com蔡超.xlsx → ca800.com
          20261109_1688.com_张三.xlsx → 1688.com
          深海装备潜客_客户1_20261109_171742.xlsx → 深海装备潜客
          （中文名的「来源」是有意义的首段，绝不是抠出来的日期数字）

        策略：
          1. 去除常见前缀（团队代码+日期+采集标记）
          2. 提取域名部分（含 .com/.cn/.net 等 TLD）
          3. 去除尾部人名和扩展名
          4. 没域名时：中文名取有意义的首段，纯英文名才抠字母数字串
        """
        if not filename:
            return filename

        # 只去除已知的文件扩展名（.xlsx/.xls/.csv/.tar.gz/.tgz），不盲目用 splitext
        # 避免把 "1688.com" 这样的域名误切为 ("1688", ".com")
        name = filename
        for ext in ('.tar.gz', '.tgz', '.xlsx', '.xls', '.csv'):
            if name.lower().endswith(ext):
                name = name[:-len(ext)]
                break

        # 策略 1：去除前缀模式（字母+数字+字母，如 ZHY20260721CJ）
        # 匹配：2-5 个字母 + 6-8 个数字 + 0-2 个字母
        name = re.sub(r'^[A-Za-z]{2,5}\d{6,8}[A-Za-z]{0,2}', '', name)

        # 策略 2：去除日期下划线前缀（如 20261109_）
        name = re.sub(r'^\d{8}_', '', name)

        # 策略 3：提取域名（查找含 TLD 的部分）
        # 匹配：xxx.com.cn / xxx.com / xxx.net / xxx.cn 等
        domain_match = re.search(r'([a-zA-Z0-9][-a-zA-Z0-9]*\.)+(com(\.cn)?|cn|net|org|com?\.?[a-z]{0,2})', name)
        if domain_match:
            domain = domain_match.group(0)
            # 去除域名后紧跟的中文人名（非域名部分）
            return domain

        # 策略 4：没有域名时的取名
        # 老实现无论中不中文，都从全名里抠「第一个字母数字串」，中文文件名于是被
        # 抠成一个日期残片：本机 97 个待入库文件实测有 32 个的「数据来源」变成
        # 20261110 / 10414 这种纯数字——溯源信息整个丢掉，按来源筛选时全都糊在
        # 一起，等于这批数据从此不知道是哪来的。中文名绝不能按字母数字抠。
        if re.search(r"[\u4e00-\u9fff]", name):
            for seg in re.split(r"[_\-–—\s]+", name):
                s = seg.strip()
                if not s or re.fullmatch(r"[\d年月日:：.、\-–—_\s]+", s):
                    continue      # 纯日期/时间戳/编号段不是来源
                return s
            return name

        simple_match = re.search(r'[a-zA-Z0-9][-a-zA-Z0-9.]*[a-zA-Z0-9]', name)
        if simple_match:
            result = simple_match.group(0)
            # 去除尾部的中文人名（如果有的话）
            result = re.sub(r'[\u4e00-\u9fff]+$', '', result)
            return result

        # 兜底：返回原文件名（去扩展名）
        return name

    @staticmethod
    def _resolve_source(source_raw: str, website_url: str, source_name: str) -> str:
        """统一的数据来源清洗逻辑（3 层优先级）。

        大厂 Data Provenance 标准：所有入库路径必须共用同一套来源清洗逻辑，
        确保无论数据从哪条链路进入，来源字段的清洁度一致。

        优先级：
          1. 数据自带的「数据来源」字段（清洗掉内部编号/人名等元数据）
          2. 从「官网/公司网址」列提取域名（内容溯源）
          3. 从文件名提取（兜底方案）

        Args:
            source_raw: 数据自带的「数据来源」字段原始值
            website_url: 「官网/公司网址」列的值
            source_name: 文件名（已清洁或原始值）

        Returns:
            清洁的数据来源字符串
        """
        # Priority 1: 数据自带的「数据来源」字段
        source_raw = (source_raw or "").strip()
        if source_raw:
            if ' / ' in source_raw:
                # 多来源拼接（去重保序）
                sources = [s.strip() for s in source_raw.split(' / ')]
                cleaned_sources = []
                for s in sources:
                    if '.xlsx' in s.lower() or re.match(r'^[A-Z]{2,5}\d{6,8}', s):
                        cleaned_sources.append(DataPoolManager._clean_source_name(s))
                    else:
                        cleaned_sources.append(s)
                seen = set()
                return ' / '.join(
                    s for s in cleaned_sources if not (s in seen or seen.add(s))
                )
            elif '.xlsx' in source_raw.lower() or re.match(r'^[A-Z]{2,5}\d{6,8}', source_raw):
                return DataPoolManager._clean_source_name(source_raw)
            else:
                return source_raw
        else:
            # Priority 2: 从「官网/公司网址」列提取域名
            website_url = (website_url or "").strip()
            if website_url:
                domain = DataPoolManager._extract_domain_from_url(website_url)
                if domain and '.' in domain:
                    return domain

            # Priority 3: 兜底用文件名（如果看起来脏则清洗）
            source_name = (source_name or "").strip()
            if not source_name:
                return ""
            if '.xlsx' in source_name.lower() or re.match(r'^[A-Z]{2,5}\d{6,8}', source_name):
                return DataPoolManager._clean_source_name(source_name)
            return source_name

    @staticmethod
    def read_records_from_excel(
            filepath: str,
            mapping_override: Optional[Dict[str, Dict[int, str]]] = None,
            report_out: Optional[dict] = None) -> List[dict]:
        """从任意格式的 Excel/CSV 文件读取 → 清洗 → 返回可入库的标准记录。

        使用 customer_library_cleaner 的智能列匹配引擎（表头打分 60% + 内容打分 40%），
        自动识别公司名、电话、邮箱、联系人等字段，适配所有 Excel 格式。

        mapping_override: 预览对话框（ImportPreviewDialog）确认后的最终列映射
            {sheet_name: {列下标: 规范字段 or ""}}；""=该列不导入。
            提供了映射的 sheet 完全按 override 走（跳过自动识别），
            未提供的 sheet 继续走自动识别。
        report_out: 传入空 dict 则填充清洗报告（读取/剔除/修复/分区分布 +
            被剔样本），GUI 在真正上传前展示给用户确认，绝不静默丢数据。

        两道表级/记录级防线（Bronze→Silver 落地口径，详见 ingest_cleaning）：
          · 表级：整表实体名占比过低 → 判定为说明表/进度表，整表不入库
            （历史缺陷：海洋展「采集进度」配置表被当企业名单灌进数据池）
          · 记录级：公司名不是企业实体（图片路径/URL/纯日期/正文散文）或
            五类联系方式全无 → 剔除并计数
        """
        from customer_library_cleaner import (
            read_tables, detect_header_row, match_columns,
            records_from_table, _cell_text,
        )

        # 后缀只判「是不是我们认得的表格文件」；真实格式由 read_tables 看字节头分派
        # （本机实测：内容是旧版 OLE、后缀却写 .xlsx 的展会名单，按后缀走必丢）
        ext = os.path.splitext(filepath)[1].lower()
        if ext not in IMPORT_EXCEL_EXTS:
            raise OSSPoolError(f"不支持的文件格式: {ext or '无后缀'}")

        try:
            tables = read_tables(filepath)
        except (FileNotFoundError, ValueError) as e:
            raise OSSPoolError(str(e))
        except Exception as e:
            raise OSSPoolError(f"读取文件失败: {e}")

        if not tables:
            raise OSSPoolError("文件中没有读到任何工作表或数据行")

        all_records: List[dict] = []
        skipped_sheets: List[Tuple[str, float]] = []
        source_name = DataPoolManager._clean_source_name(os.path.basename(filepath))

        for sheet_name, rows in tables:
            if not rows:
                continue
            headers, data_rows = detect_header_row(rows)
            if not any(headers):
                headers = [""] * max((len(r) for r in rows), default=0)
            while len(headers) < max((len(r) for r in rows), default=0):
                headers.append("")

            override_map = (mapping_override or {}).get(sheet_name)
            if override_map is not None:
                # 用户在预览界面确认/修正过这张表：完全按最终映射走
                mapping = {int(k): str(v or "") for k, v in override_map.items()}
                if not any(mapping.values()):
                    continue  # 整表被设为不导入
            else:
                mapping, identified, skipped = match_columns(headers, data_rows)
                if not mapping:
                    continue  # 这张表没认到任何字段，跳过

            # 用映射把原始行转成规范字段记录
            raw_records = records_from_table(headers, data_rows, mapping, source_name)

            # 转为数据池标准格式（逐表累积，便于表级说明表拦截）
            sheet_records: List[dict] = []
            for raw in raw_records:
                company = (raw.get("公司名") or "").strip()
                if not company:
                    continue

                # 电话拆分：只按分号/逗号拆（records_from_table 多列拼成"；"分隔），
                # 不按空格拆——国际号码 "+34 688 841 xxx" 中间有空格
                phone_raw = raw.get("电话", "")
                phones = [p.strip() for p in re.split(r"[;；,，]+", phone_raw) if p.strip()]
                # 手机/固话分类（导出时「电话」「固话」两列各归各位）
                mobiles = [p for p in phones if _is_mobile_number(p)]
                landlines = [p for p in phones if not _is_mobile_number(p)]

                # 联系人 + 岗位合并
                contact_name = (raw.get("联系人") or "").strip()
                contact_title = (raw.get("岗位") or "").strip()
                contacts = []
                if contact_name:
                    contacts.append({"name": contact_name, "title": contact_title})

                # 地址：优先用识别到的地址字段，其次用备注
                address = (raw.get("地址") or "").strip()
                remark = (raw.get("备注") or "").strip()
                if not address and remark:
                    address = remark

                # 国家（导出「国家」列 + 属地判据里权重最高的一条证据）
                # 注意：这里**不预设 is_domestic**——历史缺陷是导入路径只看
                # country 列就定性，绕过了 record_region 的多信号投票，
                # 于是 country 列为空的海外名单（如 made-in-china 导出）
                # 全被标成国内。属地判据只允许 record_region 一份实现。
                country = (raw.get("国家") or "").strip()

                # 数据来源智能溯源（大厂 Data Provenance 标准）：
                # 统一调用 _resolve_source，三条入库路径共用同一套清洗逻辑
                source_clean = DataPoolManager._resolve_source(
                    raw.get("数据来源", ""),
                    raw.get("官网", ""),
                    source_name,
                )

                sheet_records.append({
                    "company": company,
                    "company_norm": normalize_company_name(company),
                    "contacts": contacts,
                    "phones": phones,
                    "mobiles": mobiles,
                    "landlines": landlines,
                    "emails": [e.strip() for e in re.split(r"[;；,，]+", raw.get("邮箱", "")) if e.strip()],
                    "wechats": [w.strip() for w in re.split(r"[;；,，]+", raw.get("微信号", "")) if w.strip()],
                    "website": (raw.get("官网") or "").strip(),
                    "address": address,
                    "products": (raw.get("主营") or "").strip(),
                    "industry": (raw.get("行业") or "").strip(),
                    "source": source_clean,
                    "source_user": DataPoolManager._get_username_static(),
                    "source_time": datetime.now().isoformat(timespec="seconds"),
                    "country": country,
                })

            # 表级门禁：整表有效实体名占比过低 → 说明表/进度表，不入库
            usable, ratio = _sheet_usability(sheet_records)
            if not usable:
                skipped_sheets.append((sheet_name, ratio))
                continue
            all_records.extend(sheet_records)

        # ── Silver 层：清洗 + 分区派生（region/province/industry/quality）──
        kept, report = _clean_records(all_records, label="Excel 导入")
        if report_out is not None:
            report_out.update(report)
            report_out["skipped_sheets"] = [
                {"sheet": name, "entity_ratio": round(ratio, 3)}
                for name, ratio in skipped_sheets]
        return kept

    @staticmethod
    def preview_excel_mapping(filepath: str) -> List[dict]:
        """解析 Excel/CSV，返回每个工作表的列→字段映射预览（不产出记录）。

        大厂 ETL（Informatica / Power Query / DataWorks）的 schema
        mapping 预览思想：自动识别结果先给用户过目、可修正后再落地，
        把「错列入库后才发现」提前到入库之前。
        返回列表，每项：
          {
            "sheet": 工作表名,
            "headers": [列名, ...],
            "mapping": {列下标: 规范字段 or ""},   # 自动识别结果
            "unmapped": [未识别列下标, ...],
            "samples": {列下标: [前 3 个非空样例值, ...]},
            "total_rows": 数据行数,
          }
        """
        from customer_library_cleaner import (
            read_tables, detect_header_row, match_columns, _cell_text,
        )

        # 与 read_records_from_excel 同一套后缀判据，真实格式交给字节头分派
        ext = os.path.splitext(filepath)[1].lower()
        if ext not in IMPORT_EXCEL_EXTS:
            raise OSSPoolError(f"不支持的文件格式: {ext or '无后缀'}")

        try:
            tables = read_tables(filepath)
        except (FileNotFoundError, ValueError) as e:
            raise OSSPoolError(str(e))
        except Exception as e:
            raise OSSPoolError(f"读取文件失败: {e}")

        if not tables:
            raise OSSPoolError("文件中没有读到任何工作表或数据行")

        previews: List[dict] = []
        for sheet_name, rows in tables:
            if not rows:
                continue
            headers, data_rows = detect_header_row(rows)
            if not any(headers):
                headers = [""] * max((len(r) for r in rows), default=0)
            while len(headers) < max((len(r) for r in rows), default=0):
                headers.append("")

            mapping, _identified, _skipped = match_columns(headers, data_rows)
            full_mapping = {i: mapping.get(i, "") for i in range(len(headers))}
            unmapped = [i for i, f in full_mapping.items() if not f]

            # 每列取前 3 个非空样例值（帮用户判断"这列装的是什么"）；
            # 所有列都集满后立即跳出（历史总表 6.8 万行，避免全表空转）
            ncol = max(
                [len(headers)] + [len(r) for r in data_rows]
            ) if data_rows else len(headers)
            samples: Dict[int, List[str]] = {i: [] for i in range(ncol)}
            remaining = ncol
            for row in data_rows:
                if remaining <= 0:
                    break
                for i in range(ncol):
                    if len(samples[i]) >= 3:
                        continue
                    v = _cell_text(row[i]) if i < len(row) else ""
                    if v:
                        samples[i].append(v)
                        if len(samples[i]) == 3:
                            remaining -= 1

            previews.append({
                "sheet": sheet_name,
                "headers": list(headers),
                "mapping": full_mapping,
                "unmapped": unmapped,
                "samples": samples,
                "total_rows": len(data_rows),
            })
        return previews


# ── 辅助函数 ─────────────────────────────────────────────────────────────────

# ── 批量导入展开（多选文件 / 压缩包）──────────────────────────────────────

# 能导入的表格文件后缀。
# .xls（Excel 97-2003 / WPS 旧存法）在本机实测里以两种形态出现：真 .xls 后缀，
# 以及「内容是 OLE 却改名 .xlsx」（2019 上海展名单）。后者按后缀拒读会白丢一整份
# 名单，所以格式判定统一交给 customer_library_cleaner.excel_kind 看字节头，这里
# 只负责「哪些后缀值得进候选清单」——扫描层与压缩包展开层共用这一份，不分叉。
IMPORT_EXCEL_EXTS = (".xlsx", ".xlsm", ".xls", ".csv")
# 能自动展开的压缩包后缀。这一份清单同时服务三条链路：手动导入的展开、全盘扫描的
# 发现层、界面「支持哪些格式」的文案（2026-11-11 用户需求：很多 Excel 是装在压缩包里
# 的，扫描层进不去就等于扫不全）。以前这些后缀散在 expand_import_files 的 if 分支里
# 写死，扫描层想复用只能自己抄一份——抄的那份必然会在某天说谎。
IMPORT_ZIP_EXTS = (".zip",)
IMPORT_TAR_EXTS = (".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tar.xz")
# 单文件压缩（不是容器）：只在「包里套包」的递归识别时算，顶层选择不收
IMPORT_COMPRESS_EXTS = (".gz", ".bz2", ".xz")
IMPORT_ARCHIVE_EXTS = IMPORT_ZIP_EXTS + IMPORT_TAR_EXTS
# 认得出但解不开（需要商用授权/额外解码器）：必须如实告诉用户为什么跳过
IMPORT_ARCHIVE_UNSUPPORTED = (".rar", ".7z")
# 压缩包解压总大小防护：超过就停止展开（防恶意小包解开后撑爆磁盘）
_ZIP_BOMB_LIMIT = 2 * 1024 * 1024 * 1024  # 2GB


def _fix_zip_filename(info: zipfile.ZipInfo) -> str:
    """修复 zip 条目名里的中文乱码（按 7-Zip/WinRAR 同一条判别顺序试）。

    zip 规范只在「名字是 UTF-8」时才有 0x800 标志位，但大量打包工具（macOS 的
    ditto/命令行 zip、Python zipfile 的某些调用、各类脚本）写 UTF-8 名字时**不打
    这个标志**，Python 于是按 cp437 解码得到 ``σÅ░τÉâµò░µì«`` 这种乱码；Windows 自带
    压缩工具则是 GBK 名字同样不带标志位，得到 ``鍙扮悆鏁版嵁`` 那种乱码。两种都要认，
    而且要**先试 UTF-8 再试 GBK**：GBK 是双字节宽表，UTF-8 的字节序列拿去配 GBK 大多
    能配成功（配出来正是上面那种乱码），先试 GBK 就会把好的判坏（实测本机
    ``11.10台球数据.zip`` 同包两种乱码并存，根因就在这里）。
    """
    name = info.filename
    if info.flag_bits & 0x800:  # UTF-8 标志位，名字本身没问题
        return name
    try:
        raw = name.encode("cp437")     # 还原 Python 解码前的原始字节
    except UnicodeEncodeError:
        return name                     # 不是 cp437 解出来的，别乱改
    for enc in ("utf-8", "gbk"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return name


# 符号链接成员：内容不是数据，而是「另一个文件的路径」那一串字符（PyInstaller 打的
# app 包里 ``Frameworks/base_library.zip`` 实测就是 29 字节的 ``../Resources/...``）。
# 跟着这串路径去开文件等于让包内可控字符串当坐标，把它当数据读又会报「文件已损坏」
# ——两样都不许。全盘扫描与手动导入两条链路共用这一条判据与这一句实话。
ZIP_LINK_NOTE = "包里 {n} 个成员是指向别处的符号链接（内容只是一串路径），已跳过"

# 包内路径逃逸（zip slip）的记账文案：两条链路共用一句，同一个意思只有一种说法
ARC_UNSAFE_NOTE = "包内路径不安全（含 .. 或绝对路径），已跳过"


def is_zip_symlink(info: "zipfile.ZipInfo") -> bool:
    """zip 条目是不是符号链接（外置属性里的模式位说了算，不看文件名）。"""
    return bool(stat.S_ISLNK(info.external_attr >> 16))


def _iter_zip_excel(zip_path: str, staging: Path, display_prefix: str,
                    out: List[Tuple[str, str]],
                    skipped: List[Tuple[str, str]],
                    budget: List[int], depth: int = 0):
    """递归展开压缩包里的全部表格文件（包里的压缩包也一并扫描）。

    out 追加 (解压后路径, 展示名)；skipped 追加 (展示名, 跳过原因)；
    budget=[剩余可解压字节数] 防压缩炸弹。单个条目失败只记账不中断。
    """
    if depth > 3:
        skipped.append((display_prefix, "压缩包套了超过 3 层，请先手动解压再导入"))
        return
    try:
        zf = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile:
        skipped.append((os.path.basename(zip_path), "压缩包已损坏，读不出来"))
        return
    except OSError as e:
        skipped.append((os.path.basename(zip_path), f"压缩包读不出来：{e}"))
        return
    links = [0]

    def _flush_links() -> None:
        if links[0]:
            skipped.append((display_prefix, ZIP_LINK_NOTE.format(n=links[0])))

    try:
        with zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                if is_zip_symlink(info):
                    # 链接成员的内容是一串路径，不是数据：不跟着它去开文件，也不许
                    # 把它当坏包报错（全盘扫描那条链路同一处置）
                    links[0] += 1
                    continue
                name = _fix_zip_filename(info)
                arc = name.replace("\\", "/")
                pure = arc.split("/")[-1]
                # macOS 压缩时自带的元数据目录和隐藏文件：不是真数据
                if (arc.startswith("__MACOSX/") or "/__MACOSX/" in arc
                        or pure.startswith(".") or pure.startswith("._")):
                    continue
                low = pure.lower()
                # zip/tar 的解压目标都带 display_prefix 子目录：两个压缩包内部
                # 路径撞名（都有 data/数据.xlsx）时不会互相覆盖、也不会把同一
                # 文件导入两次
                target = (staging / display_prefix / arc).resolve()
                # 防解压路径逃逸（安全基线）：包内含 .. 段或绝对路径的成员一律
                # 拦截记账；resolve + startswith 兜底其余奇异路径
                if (arc.startswith("/") or ".." in arc.split("/")
                        or not str(target).startswith(str(staging.resolve()) + os.sep)):
                    skipped.append((f"{display_prefix}/{pure}", ARC_UNSAFE_NOTE))
                    continue
                budget[0] -= info.file_size
                if budget[0] < 0:
                    skipped.append((display_prefix,
                                    "压缩包解开后超过 2GB，只导入了前面的部分"))
                    return
                if low.endswith(IMPORT_ZIP_EXTS + IMPORT_TAR_EXTS
                                + IMPORT_COMPRESS_EXTS):
                    # 本体落盘加 .part 后缀：嵌套包的名字要留给它解出来的内容目录
                    # （否则同名文件与同名目录撞车，mkdir 必败）
                    real_target = target.with_name(target.name + ".part")
                    try:
                        real_target.parent.mkdir(parents=True, exist_ok=True)
                        with zf.open(info) as src, open(real_target, "wb") as dst:
                            shutil.copyfileobj(src, dst)
                    except (OSError, NotImplementedError, RuntimeError,
                            zipfile.BadZipFile):
                        # NotImplementedError：zip 里的 AES 加密成员，标准库读不了
                        skipped.append((f"{display_prefix}/{pure}",
                                        "包里的压缩包读不出来"))
                        continue
                    if low.endswith(IMPORT_ZIP_EXTS):
                        _iter_zip_excel(str(real_target), staging,
                                        f"{display_prefix}/{pure}",
                                        out, skipped, budget, depth + 1)
                    else:
                        # zip 里套 tar（Linux 打包常见）：以前只认 .zip，这种成员被静默
                        # 忽略＝扫不到但其实能传；现在两族递归互认，口径不分叉
                        _iter_tar_excel(str(real_target), staging,
                                        f"{display_prefix}/{pure}",
                                        out, skipped, budget, depth + 1)
                elif low.endswith(IMPORT_EXCEL_EXTS):
                    try:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        with zf.open(info) as src, open(target, "wb") as dst:
                            shutil.copyfileobj(src, dst)
                    except (OSError, NotImplementedError, RuntimeError,
                            zipfile.BadZipFile):
                        skipped.append((f"{display_prefix}/{pure}",
                                        "读不出来（文件可能已损坏或加了密码）"))
                        continue
                    out.append((str(target), f"{display_prefix}/{pure}"))
                # 其余后缀（pdf/图片/文档等）不属于表格数据，静默忽略
    finally:
        _flush_links()


def _iter_tar_excel(tar_path: str, staging: Path, display_prefix: str,
                    out: List[Tuple[str, str]],
                    skipped: List[Tuple[str, str]],
                    budget: List[int], depth: int = 0):
    """递归展开 tar/tar.gz 包里的全部表格文件（防护与 zip 同一套）。

    只提取常规文件成员（isreg）：软/硬链接永不落盘，链接攻击天然免疫；
    成员路径仍走 resolve + startswith 逃逸校验。包内套 zip/tar 一并递归。
    """
    if depth > 3:
        skipped.append((display_prefix, "压缩包套了超过 3 层，请先手动解压再导入"))
        return
    try:
        tf = tarfile.open(tar_path, "r:*")
    except tarfile.TarError:
        skipped.append((os.path.basename(tar_path), "压缩包已损坏，读不出来"))
        return
    except OSError as e:
        skipped.append((os.path.basename(tar_path), f"压缩包读不出来：{e}"))
        return
    links = [0]
    with tf:
        for m in tf:
            arc = m.name.replace("\\", "/").lstrip("/")
            pure = arc.split("/")[-1]
            if m.issym() or m.islnk():
                # 链接成员永不落盘（链接攻击天然免疫），但和 zip 一样要说一句，
                # 不能让它消失在「已展开」的计数里
                links[0] += 1
                continue
            if not m.isreg():  # 目录/设备文件/稀疏成员不落盘
                continue
            if ("/__MACOSX/" in f"/{arc}" or pure.startswith(".")
                    or pure.startswith("._")):
                continue
            low = pure.lower()
            target = (staging / display_prefix / arc).resolve()
            # 同 zip：.. 段/绝对路径一律拦截，resolve + startswith 兜底
            if (arc.startswith("/") or ".." in arc.split("/")
                    or not str(target).startswith(str(staging.resolve()) + os.sep)):
                skipped.append((f"{display_prefix}/{pure}", ARC_UNSAFE_NOTE))
                continue
            budget[0] -= m.size
            if budget[0] < 0:
                if links[0]:
                    skipped.append((display_prefix, ZIP_LINK_NOTE.format(n=links[0])))
                skipped.append((display_prefix,
                                "压缩包解开后超过 2GB，只导入了前面的部分"))
                return
            nested_zip = low.endswith(IMPORT_ZIP_EXTS)
            nested_tar = low.endswith(IMPORT_TAR_EXTS + IMPORT_COMPRESS_EXTS)
            if nested_zip or nested_tar:
                # 同 zip 嵌套：本体加 .part 后缀，名字留给内容目录
                real_target = target.with_name(target.name + ".part")
                try:
                    real_target.parent.mkdir(parents=True, exist_ok=True)
                    src = tf.extractfile(m)
                    if src is None:
                        raise tarfile.TarError("读不出这个成员")
                    with src, open(real_target, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                except (OSError, tarfile.TarError):
                    skipped.append((f"{display_prefix}/{pure}", "包里的压缩包读不出来"))
                    continue
                if nested_zip:
                    _iter_zip_excel(str(real_target), staging, f"{display_prefix}/{pure}",
                                    out, skipped, budget, depth + 1)
                else:
                    _iter_tar_excel(str(real_target), staging, f"{display_prefix}/{pure}",
                                    out, skipped, budget, depth + 1)
            elif low.endswith(IMPORT_EXCEL_EXTS):
                try:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    src = tf.extractfile(m)
                    if src is None:
                        raise tarfile.TarError("读不出这个成员")
                    with src, open(target, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                except (OSError, tarfile.TarError):
                    skipped.append((f"{display_prefix}/{pure}",
                                    "读不出来（文件可能已损坏）"))
                    continue
                out.append((str(target), f"{display_prefix}/{pure}"))
    if links[0]:
        skipped.append((display_prefix, ZIP_LINK_NOTE.format(n=links[0])))


def expand_import_files(paths: List[str]) -> Tuple[
        List[Tuple[str, str]], List[Tuple[str, str]], Optional[str]]:
    """把用户选中的文件/压缩包展开成可导入的表格文件清单。

    批处理作业的 staging 思想：压缩包（zip / tar / tar.gz 等）先解到
    临时目录（不动原包），解出来的文件与用户直接选的文件走同一条
    解析链路；单个文件失败只记账不中断整批。
    返回 ([(文件路径, 展示名), ...], [(名称, 跳过原因), ...], 临时目录)：
    临时目录非 None 时调用方用完必须 shutil.rmtree 清理。
    """
    excel_pairs: List[Tuple[str, str]] = []
    skipped: List[Tuple[str, str]] = []
    staging: Optional[str] = None

    for raw in paths:
        p = Path(raw)
        name = p.name
        low = p.suffix.lower()
        low_name = name.lower()
        # 注意 tar 系后缀要用全名判断：Path("a.tar.gz").suffix 是 ".gz"
        if low_name.endswith(IMPORT_TAR_EXTS):
            if staging is None:
                staging = tempfile.mkdtemp(prefix="pool_import_")
            before = len(excel_pairs)
            _iter_tar_excel(str(p), Path(staging), name,
                            excel_pairs, skipped, [_ZIP_BOMB_LIMIT])
            if len(excel_pairs) == before:
                skipped.append((name, "压缩包里没有找到能导入的 Excel/CSV 文件"))
        elif low == ".zip":
            if staging is None:
                staging = tempfile.mkdtemp(prefix="pool_import_")
            before = len(excel_pairs)
            _iter_zip_excel(str(p), Path(staging), name,
                            excel_pairs, skipped, [_ZIP_BOMB_LIMIT])
            if len(excel_pairs) == before:
                skipped.append((name, "压缩包里没有找到能导入的 Excel/CSV 文件"))
        elif low in (IMPORT_ARCHIVE_UNSUPPORTED):
            skipped.append((name, "暂时不支持这种压缩格式，请先解压，再选里面的 Excel 文件"))
        elif low in IMPORT_EXCEL_EXTS:
            excel_pairs.append((str(p), name))
        else:
            skipped.append((name, "不是能导入的文件格式（需要 Excel/CSV 或 zip 压缩包）"))

    return excel_pairs, skipped, staging


_MOBILE_TAIL_RE = re.compile(r"^1[3-9]\d{9}$")


def _is_mobile_number(num: Any) -> bool:
    """手机号判定：剥非数字后取末 11 位锚定匹配 1[3-9]xxxxxxxxx。

    兼容 "+8613800138000"/"86-13800138000" 等带国家码写法；绝不能用 search
    找 11 位子串，否则连写号码会把固话误判成手机。
    """
    if not num:
        return False
    digits = re.sub(r"\D", "", str(num))
    return len(digits) >= 11 and bool(_MOBILE_TAIL_RE.match(digits[-11:]))


def _is_valid_record(rec: dict) -> bool:
    """判断记录是否有效（大厂数据质量门禁）。

    有效性硬判据：每条记录必须至少含「电话 / 邮箱 / 官网」三者之一。
    仅有地址/公司名而无任何联系方式的记录视为垃圾数据，不得入库。

    对标大厂 Data Quality Gate：
      - Google Data Quality: 入湖数据必须通过 validity check
      - Alibaba DataWorks: 数据质量规则包含「联系方式非空」约束
    """
    # 检查联系方式列表
    if rec.get("phones") or rec.get("mobiles") or rec.get("landlines"):
        return True
    if rec.get("emails"):
        return True
    # 检查官网
    website = str(rec.get("website") or "").strip()
    if website:
        return True
    # 检查平铺的 phone/email 字段（兼容不同入口的字段名）
    phone = str(rec.get("phone") or "").strip()
    if phone:
        return True
    email = str(rec.get("email") or "").strip()
    if email:
        return True
    return False


def _gate_names_on_read(records: List[dict]) -> List[dict]:
    """读侧名称门禁（Bronze→Silver 重放），判据实现只在 ingest_cleaning 有一份。

    清洗模块不可用（打包态缺依赖）时**原样返回**：读侧多留几条脏数据只是噪声，
    在这里误删真实企业则是数据事故——降级方向必须选保守的一侧。
    """
    if not records:
        return records
    try:
        from ingest_cleaning import gate_valid_names
        kept, dropped = gate_valid_names(records)
    except Exception:
        return records
    if dropped:
        import logging
        logging.getLogger(__name__).info(
            "🧹 存量名称门禁重放：%d 条里剔除 %d 条非企业实体名（页面栏目名/说明行）",
            len(records), dropped)
    return kept


def _silver_rule_version() -> str:
    """当前 Silver 判据版本指纹（属地 + 分区键 + 名称门禁），写进本地缓存。

    为什么版本号要落盘：读侧重放只在**重新下载 Bronze** 时才起作用，而本地缓存
    三件套是旧规则洗出来的结果——不做版本校验，升级一条判据后老用户永远读到旧
    总表，修复对他们不可见（历史缺陷：只改写侧入口，存量脏数据把修复自锁）。
    版本不一致 → 缓存作废 → 全量重建自动回刷；网络不可用时仍允许用旧缓存兜底
    （可用性优先，下一轮联网自愈）。
    """
    parts = [REGION_RULE_VERSION]
    try:
        from ingest_cleaning import NAME_RULE_VERSION, PARTITION_RULE_VERSION
        parts += [NAME_RULE_VERSION, PARTITION_RULE_VERSION]
    except Exception:
        parts.append("no-cleaning-module")
    return "|".join(str(p) for p in parts)


def _clean_records(records: List[dict], label: str = "") -> Tuple[List[dict], dict]:
    """入库前清洗（Silver 层唯一入口），返回 (可入库记录, 清洗报告)。

    延迟导入 ingest_cleaning：该模块顶层反向依赖 data_pool（复用属地/门禁
    判据），在 data_pool 导入期加载会成环。
    ingest_cleaning 不可用时降级为「只跑有效性硬门禁」——宁可少洗一层修复，
    也不能让无联系方式的垃圾数据入库，也不能因为缺模块就整体上传失败。
    """
    try:
        from ingest_cleaning import clean_records
    except Exception:
        return _filter_invalid_records(records, label=label or "清洗降级门禁"), {}
    kept, report = clean_records(records or [])
    if report.get("dropped"):
        import logging
        logging.getLogger(__name__).info(
            "🧹 %s：读取 %d 条 → 可入库 %d 条，剔除 %d 条（%s）",
            label or "入库清洗", report.get("in", 0), report.get("kept", 0),
            report.get("dropped", 0),
            "、".join("%s %d" % (k, v)
                      for k, v in sorted(report.get("drop_reasons", {}).items(),
                                         key=lambda kv: -kv[1])))
    return kept, report


def _sheet_usability(sheet_records: List[dict]) -> Tuple[bool, float]:
    """表级可用性判定（说明表/进度表拦截）。依赖缺失时保守放行。"""
    if not sheet_records:
        return False, 0.0
    try:
        from ingest_cleaning import sheet_usability
        return sheet_usability(sheet_records)
    except Exception:
        return True, 1.0


def _rederive_after_merge(records: List[dict]) -> List[dict]:
    """合并出口的派生列兜底核齐（region / is_domestic / province / industry / quality）。

    正职在 pool_merge.merge_group：它在**并集之后**判属地、按同一个属地派生地区、
    并打上判据版本标记。这里再走一遍 `_normalize_partition_fields` 是纵深防御，
    因为历史上出过「同一个合并函数里两套口径各算一半」的事故：
    「Renova」一条同时有深圳地址与 renovasystems.it 域名，属地判到 intl、
    地区分区键仍是广东——界面上勾「国外」和勾「广东」两个条件互相抵消，
    这条数据永远查不出来（实测上传后对账抓到 2 条、按并集证据重判后放大到 24 条，
    正是「只翻属地、不翻地区」的形状）。

    覆盖三种 merge_group 管不到的情况：
      · 打包态缺 pool_merge（降级路径压根不合并，记录带着各自来源的旧标记进来）
      · 历史池里按旧判据写下的值（版本标记不齐 → 这里按当前证据链重算）
      · 运行时缓存键（_region_key/_region_ver）不得写进总表与 OSS
    已按当前判据核齐的记录在这里是**零改动**（幂等），所以不会逼着每轮同步
    把整表重判一遍。
    """
    return DataPoolManager._normalize_partition_fields(records)


def _filter_invalid_records(records: List[dict], label: str = "") -> List[dict]:
    """过滤无效记录，返回有效记录列表。

    日志输出过滤数量，便于用户感知数据质量门禁。
    """
    if not records:
        return records
    valid = [r for r in records if _is_valid_record(r)]
    filtered = len(records) - len(valid)
    if filtered > 0:
        import logging
        logger = logging.getLogger(__name__)
        logger.info(
            f"🛡️ 数据质量门禁{f'（{label}）' if label else ''}："
            f"{len(records)} 条记录中过滤掉 {filtered} 条无联系方式垃圾数据，"
            f"保留 {len(valid)} 条有效记录"
        )
    return valid


def _split_field(value: Any) -> List[str]:
    """把逗号/分号分隔的字段值拆成列表。"""
    if not value:
        return []
    text = str(value).strip()
    if not text:
        return []
    parts = re.split(r"[,;，；\s]+", text)
    return [p.strip() for p in parts if p.strip()]
