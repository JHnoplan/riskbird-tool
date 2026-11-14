# -*- coding: utf-8 -*-
"""enrich_waterfall.py —— 瀑布式多源补全（功能 A）：同一个缺失字段按「成本升序」逐级问，命中即停。

对标的是 Clay 那条被反复验证的做法（waterfall enrichment），三条直接可搬的结论：
  ① 填充率是**源的顺序**决定的，不是源的数量决定的——同一批数据、同样五个源，
     换四种顺序，账单能差 4.1 倍。所以本模块的阶段表按成本升序执行，而不是按可信度：
     免费的内存复用排在任何一次网络请求之前。
  ② 单源填充 50–60%，三到四级瀑布能到 80%+。本工具现在只有一跳（行自带官网 → 抓），
     而「行自带官网字段」在 8 个关键词站里覆盖率差别极大（平台页/店铺页根本不算官网），
     那一跳接不上的家数就是今天白丢掉的那一大截。
  ③ 瀑布出来的每个值都必须能回答「是哪一层给的」，否则下一轮不知道该重问谁、
     评分也没有可信度输入。这一层落在 field_evidence，不在本模块重复实现。

【本模块刻意不做的三件事，写在案上比口头承诺可靠】
  1) 不做「搜索引擎兜底找官网」。本机既有实测结论是：google 直连超时、
     html.duckduckgo 拒连、Bing 可达但充斥内容站噪声——把它接进交付链，得到的将是
     「一批来路不明、无法离线验证、还可能把第三方站的号码记到厂家头上」的值，
     而归属核验对搜索快照根本做不动（快照里没有正文）。要做也必须等它有过归属证据
     之后再登记层级，今天没有这一层。
  2) 不做「按邮箱规则猜手机号/座机」。那是造数据，不是补数据。
  3) 不自己开第二条工商查询通路。企知道/风鸟/爱企查三路已有退避、限流、熔断、
     台账和属地关卡（每一条都是踩出来的），瀑布要做的只是**把它们该问的家数投进
     那份现成的待采名单**——自己另写一套查询等于把那些已修过的坑重新挖一遍。

【和 website_contact 的分工】抓取、打分、停放判定、SSRF 闸、合并去重、预算与 halt
全在 website_contact 里（一处实现）；本模块只负责三件事：不花请求的跨行复用、
行自带官网不可用时的**官网发现**、以及把瀑布跑完后仍然缺的家数交给工商补采队列。
运行：纯标准库 + 两个同级模块，可离线全量验证（网络由 website_contact 的夹具接管）。
"""
from __future__ import annotations

import re

import field_evidence as _fe
import website_contact as _wc

__all__ = [
    "STAGES", "stage_no", "stage_name", "stage_tag", "stage_mark",
    "stage_chain", "company_key", "email_domains", "brand_tokens",
    "discover_site", "resolver_for", "registry_source_names", "stamp_source_evidence",
    "carry_pass", "pick_targets", "workable_count", "run_waterfall", "stage_lines",
    "snapshot_values", "diff_changes", "time_newer",
]

# 阶段表就是执行顺序（成本升序）。名字与 field_evidence 的层级 slug 同一套，
# 不是第二份命名：能在两张表里各写一遍的东西，迟早会写成两种口径。
STAGES = ("carry", "mailsite", "site", "brand", "registry")
# 阶段中文名**不在这里再存一份**：以前这里有一张 STAGE_LABELS 快照，等于同一个名字有两处
# 写法——改层级表时快照不会跟着走，界面与「补全层级」列就地分家。现在只有 stage_name
# 这一个出口，每次现查 field_evidence 那一份。
# 圈号只到这里：STAGES 多出一级时下面的 stage_mark 当场报 IndexError，
# 而不是安静地印出一个「（无编号）」——少一个编号比少一行日志更难被发现。
_CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩"


def stage_no(slug):
    """阶段序号（从 1 起），**只能**由 STAGES 的位置算出来。

    界面上写「⑤ 转工商补采」、引擎日志里写「瀑布第 3 级」的那段时间是真实发生过的：
    同一件事两套编号，用户对着日志数第 3 级，看到的却是品牌域名推导。
    """
    return STAGES.index(slug) + 1


def stage_name(slug):
    """阶段名 = 「补全层级」这一列真正落库的那个值（现查层级表，不在这里也不在任何调用点
    另起一个说法）。未知 slug 直接 KeyError：宁可当场炸，也不把没登记过的层级名印给用户。"""
    return _fe.TIER_LABEL[slug]


def stage_tag(slug):
    """「第 N 级（名字）」：日志用。序号和名字同源，才不会各数各的。"""
    return f"第 {stage_no(slug)} 级（{stage_name(slug)}）"


def stage_mark(slug):
    """「⑤ 名字」：界面用。跟 stage_tag 同源，只是换个编号写法。"""
    return f"{_CIRCLED[stage_no(slug) - 1]} {stage_name(slug)}"


def stage_chain(sep=" → "):
    """按执行顺序把五级名字串出来（界面话术与会话日志共用这一份）。"""
    return sep.join(stage_name(slug) for slug in STAGES)


# 待工商补采队列的单条上限：一次投几千家会把三路名单撑爆，而那三路的逐家查询
# 本来就要按家排队烧时间。封顶 + 在日志里说清「还剩几家没投」，比默默全投诚实。
QUEUE_LIMIT = 600
# 免费邮箱域：这些域名的邮箱属于**发件人**，不属于企业，拿它反查官网必然查到
# qq.com/163.com 这类门户（实测会把腾讯的总机当成某五金厂的电话）。
FREE_MAIL_HOSTS = {
    "qq.com", "vip.qq.com", "foxmail.com", "163.com", "126.com", "yeah.net",
    "sina.com", "sina.cn", "sohu.com", "21cn.com", "139.com", "wo.cn",
    "tom.com", "263.net", "aliyun.com", "gmail.com", "googlemail.com",
    "outlook.com", "hotmail.com", "live.com", "msn.com", "yahoo.com",
    "yahoo.cn", "ymail.com", "icloud.com", "me.com", "aol.com", "proton.me",
    "protonmail.com", "mail.com", "21cn.com", "sohu.com",
}
# 店铺子域名里的那些通用前缀：它们不是商号，拿去拼 .com 就是把
# 「shop」「www」「m」这种字母当品牌（实测会拼出 shop.com 这种根本不属于他的站）。
GENERIC_HOST_TOKENS = {
    "www", "ww1", "web", "m", "wap", "en", "cn", "com", "shop", "store", "mall",
    "mail", "email", "smtp", "pop", "vpn", "img", "images", "static", "assets",
    "cdn", "js", "css", "api", "app", "b2b", "seo", "test", "dev", "admin",
    "mobile", "list", "search", "product", "news", "blog", "geo", "home",
}
# 商号段至少 4 个字符才值得拼域名：3 位以下的缩写撞车概率太高（实测 abc.com
# 这种早期注册的域和「ABC 五金厂」毫无关系），拼回来的站必然不是那一家。
BRAND_MIN_LEN = 4
# 拉丁商号段里的「公司形式词」：它们出现在英文名尾部（CO.,LTD / COMPANY / GROUP），
# 拿去拼域名拼回来的是别人的站（ltd.com 之类），且归属核验也解释不了「为什么给他
# 家挂一个 ltd.com」——所以在产生候选时就剔掉，而不是留给下游去拒绝。
# 只登记 4 字母以上的词：短形式（co/ltd/inc）本来就不满足 BRAND_MIN_LEN。
CORPORATE_NAME_TOKENS = {
    "corp", "limited", "company", "companies", "group", "holding", "holdings",
    "incorporated", "technology", "technologies", "technique", "industrial",
    "industry", "industries", "manufacturing", "trading", "commerce",
    "electrical", "electronics", "machinery", "equipment", "materials",
    "science", "digital", "network", "solutions", "services", "international",
}
# 中国登记英文名的第一段绝大多数是**地名**（SHENZHEN HUAWEI TECHNOLOGIES CO.,LTD）。
# 地名不是商号，但它常常是候选列表里的第一个——把它降到末尾而不是删掉，理由是
# 确有「以地名注册为商号」的小公司（温州模式那种），删掉会把它们判死；但让
# shenzhen.com 排在前面的代价是每条记录先白烧一次请求再被归属核验拒绝。
GEO_NAME_TOKENS = {
    "beijing", "shanghai", "shenzhen", "guangzhou", "dongguan", "foshan",
    "hangzhou", "ningbo", "wenzhou", "suzhou", "nanjing", "qingdao", "dalian",
    "shenyang", "tianjin", "chongqing", "chengdu", "wuhan", "changsha",
    "zhengzhou", "hefei", "fuzhou", "xiamen", "quanzhou", "shantou",
    "haikou", "kunming", "nanchang", "guilin", "ningde", "changzhou",
    "taiyuan", "lanzhou", "yinchuan", "urumqi", "harbin", "changchun",
    "hebei", "henan", "shandong", "jiangsu", "zhejiang", "anhui",
    "fujian", "guangdong", "guangxi", "sichuan", "hubei", "hunan",
    "shaanxi", "shanxi", "jiangxi", "yunnan", "guizhou", "liaoning",
    "jilin", "heilongjiang", "inner", "xinjiang", "tibet", "gansu",
    "qinghai", "mongolia",
}

_CN_SUFFIX_RE = re.compile(
    r"(?:有限公司|股份有限公司|有限责任公司|集团有限公司|集团公司|集团|"
    r"有限公司分公司|分公司|子公司|企业公司|公司|厂|中心|部)$"
)
_NON_LATIN_RE = re.compile(r"[^a-z0-9]+")


# ── 同主体键：只做「同一张表里认亲」，不冒充跨平台主体识别 ──────────────
def _pool_normalizer():
    """尽量复用全仓唯一的主体归一口径（data_pool.normalize_company_name）。

    为什么是「尽量」而不是硬 import：data_pool 顶层带 pandas，瀑布在采集收尾被调用，
    那一刻不该因为一个可选的数据池模块导入失败而整轮补全停摆；离线用例也不该为了
    测一次内存复用而被迫装齐重型依赖。退回的实现只做**保守**的粗归一（去空白标点 +
    剥常见组织尾缀），它比 data_pool 那份认得更粗——认得粗的后果是少复用几家
    （漏补），而绝不是把 A 公司的号码盖到 B 公司头上（错补），方向是安全的。
    """
    global _NORMALIZER
    if _NORMALIZER is _UNSET:
        try:
            from data_pool import normalize_company_name as _n
            _NORMALIZER = _n
        except Exception:
            _NORMALIZER = None
    return _NORMALIZER


_NORMALIZER = _UNSET = object()


def company_key(rec):
    """同主体识别键（空串表示认不出来，调用方必须跳过而不是硬凑）。"""
    name = str((rec or {}).get("公司名称") or (rec or {}).get("公司名")
               or (rec or {}).get("company") or "").strip()
    if not name:
        return ""
    norm = _pool_normalizer()
    if callable(norm):
        try:
            key = str(norm(name) or "").strip()
            if key:
                return _NON_LATIN_RE.sub("", key.lower()) or key.lower()
        except Exception:
            pass
    s = re.sub(r"[\s（）()・.、，,]+", "", name)
    while True:
        t = _CN_SUFFIX_RE.sub("", s)
        if t == s:
            break
        s = t
    return s.lower()


def _value_pool(rec, field):
    return [x for x in _wc._SPLIT_RE.split(str((rec or {}).get(field) or "")) if x]


def time_newer(a, b):
    """a 这个时间是不是比 b 新（b 空＝没有；认不出格式的 a 一律不算新）。

    只比**日期**不比时刻：仓库里两种时间格式（带/不带时分秒）并存，按字符串比
    会出现「2026-11-6 18:00」小于「2026-11-07」这种字典序假结果。
    """
    da, db = _fe._to_date(a), _fe._to_date(b)
    if da is None:
        return False
    if db is None:
        return bool(str(a or "").strip())
    return da > db


def email_domains(rec):
    """行内邮箱里**属于企业自己**的域名（免费邮箱一律不参与反查）。"""
    out = []
    for part in _value_pool(rec, "邮箱"):
        for mail in _wc.EMAIL_RE.findall(str(part or "")):
            if not _wc.plausible_email(mail):
                continue
            host = mail.split("@", 1)[1].strip().lower().strip(">).,;/")
            if not host or host in FREE_MAIL_HOSTS:
                continue
            reg = _wc._reg_domain(host)
            if reg and reg not in FREE_MAIL_HOSTS and reg not in out:
                out.append(reg)
    return out


def brand_tokens(rec):
    """从详情/店铺 URL 与英文商号里取出**可能属于企业自己**的品牌段。

    深海装备那轮实机验证过这条路线的价值与风险：候选域名直连可以拿到品牌站，
    但必须做归属核验——同名假站（hirain/jonlong 那种）就是靠 `has_attr` 拦下的。
    本工具沿用同一纪律：这里只**产生候选**，采信权在 website_contact 的归属判定上。
    """
    out = []

    def add(token):
        t = _NON_LATIN_RE.sub("", str(token or "").lower())
        if len(t) >= BRAND_MIN_LEN and t not in GENERIC_HOST_TOKENS and t not in out:
            out.append(t)

    for col in ("详情URL", "公司网址", "官网"):
        url = str((rec or {}).get(col) or "").strip()
        if not url:
            continue
        host = _wc.host_of(url if "://" in url else "https://" + url)
        local = host.split(".")[0] if host else ""
        if local:
            add(local)
        # 平台子域常写成 a1b2c3.shopname.company.com 这种多级，末两级之外的段
        # 也常常就是商号（顺企网是 xxx.11467.com，八方是 xxx.b2b168.com）
        parts = [p for p in host.split(".") if p]
        if len(parts) >= 3:
            add(parts[-3])
    name = str((rec or {}).get("公司名称") or (rec or {}).get("公司名") or "")
    # 取**所有**拉丁段而不是第一段：中国登记英文名常写成「SHENZHEN HUAWEI
    # TECHNOLOGIES CO.,LTD」，只拿第一段就等于永远拿地名去拼域名（地名域名必然
    # 不是他家）。多出来的候选仍由 website_contact 的归属核验决定采不采信，
    # 这里的代价只是 discover_site 那边本来就已封顶的 [:2] 个候选。
    for m in re.finditer(r"[A-Za-z][A-Za-z0-9&.\-']{3,}", name):
        token = m.group(0).strip("&.-'").lower()
        if token in CORPORATE_NAME_TOKENS:
            continue
        add(token)
    # 地名段降到末尾（保留但不抢第一个候选位）：稳定分区，同一段之间先后不变
    return ([t for t in out if t not in GEO_NAME_TOKENS]
            + [t for t in out if t in GEO_NAME_TOKENS])


def discover_site(rec):
    """行自带官网字段不可用时的官网发现：返回 (候选URL, 层级slug)，认不出返回 ("", "")。

    顺序即成本与可信度的双重升序：邮箱域名（企业自己发的邮件用的域，几乎必是自家）
    排在品牌拼域名（可能撞到别人的站）前面。每个候选都只**提议**，采信与否仍由
    website_contact 抓回来后的归属核验决定——这里判不了，也不该判。
    """
    for reg in email_domains(rec):
        url = f"https://{reg}/"
        if not _wc.is_platform_host(_wc.host_of(url)) and _wc.is_public_host(_wc.host_of(url)):
            return url, "mailsite"
    for token in brand_tokens(rec)[:2]:
        for suffix in (".com", ".cn", ".com.cn"):
            host = token + suffix
            if not _wc.is_public_host(host):
                continue
            url = f"https://{host}/"
            if not _wc.is_platform_host(host):
                return url, "brand"
    return "", ""


def resolver_for(records):
    """给 website_contact 的 provider：同一张表里同域名的行共用一个结论。

    为什么要这层包装而不是直接把 discover_site 传下去：一次任务里同一主体常有几十行
    （同一店铺的不同产品），discover_site 对它们算出的候选完全相同，但
    「谁先被谁抓到」是网络层的缓存管的，这里管的只是**别再重复推导一遍**；
    另外，同注册域名的某一行已经带着可用官网时，另一行直接借用它，
    省掉一次推导并让两行的层级记成同一档（同一来源、同一可信度）。
    """
    by_domain = {}
    for rec in records or []:
        if not isinstance(rec, dict):
            continue
        site = _wc.pick_site(rec)
        if not site:
            continue
        host = _wc.host_of(site)
        reg = _wc._reg_domain(host) if host else ""
        if reg and reg not in by_domain:
            by_domain[reg] = site

    def _resolve(rec):
        for reg in email_domains(rec):
            hit = by_domain.get(reg)
            if hit:
                # 同域名另一行本来就带着官网：借那一个，比按邮箱反查更稳（那是现成的
                # 正确 URL，含路径与端口），但来源仍然是「推导」而不是行自带，
                # 所以记 mailsite 而不是 site。
                return hit, "mailsite"
        return discover_site(rec)

    return _resolve


# 三路工商名单通道的数据来源名 → 该来源的取值算哪一层。这里只写「名字对应哪一层」
# 这一件事，站名本身仍由 pending_companies.CHANNEL_LABELS 单一来源提供（下面比对时
# 取的就是那份表），避免「界面上叫风鸟、证据列里写 riskbird」这种对不上账的口径。
def registry_source_names():
    try:
        from pending_companies import CHANNEL_LABELS as _labels
        return {str(v).strip() for v in _labels.values() if str(v).strip()}
    except Exception:
        return {"企知道", "风鸟", "爱企查"}


def stamp_source_evidence(records, log=None):
    """给「本工具没动手、是通道本来就查到的」那些值补上层级：工商三路记 registry。

    为什么需要这一步而不是让 field_evidence 的兜底去判：兜底给的是「平台自带」（55 分），
    而企知道/风鸟/爱企查那三路是**按主体逐家查工商登记**得来的，是全体系最硬的一层
    （90 分）。让它们在评分里被算成同一档，等于把工具最好的数据判成最差的数据卖，
    排序也就跟着失真——那种失真不会被任何一条报错暴露，只会让用户觉得「这工具排的序没用」。
    只补没记过错层级的行（已有层级的不动），所以对重跑与续采都是幂等的。
    """
    names = registry_source_names()
    stamped = 0
    for rec in records or []:
        if not isinstance(rec, dict):
            continue
        src = str(rec.get("数据来源") or "").strip()
        if src not in names:
            continue
        if _fe.parse_evidence(rec):
            continue
        done = False
        for field in _fe.CONTACT_FIELDS:
            if str(rec.get(field) or "").strip():
                _fe.set_field_evidence(rec, field, "registry", f"工商登记（{src}）")
                done = True
        if done:
            stamped += 1
    if stamped and log:
        log(f"🏷 已给 {stamped} 行工商通道来的值标上「工商核验」层（评分按最硬的一层算）")
    return stamped


# ── 第一级：不花任何请求的跨行复用 ────────────────────────────────────
def carry_pass(records, log=None):
    """同一主体的另一行已有的值，直接补给缺的那一行（成本 0，命中率却最高）。

    这是整条瀑布里唯一**完全离线**的一级，也是今天白丢掉最多的一级：
    一张 5000 行的表里，同一家公司在顺企网/八方/爱采购各有一行、其中一行带手机
    另一行只有座机，是极常见的形状。今天这些值各归各列，导出后同一主体的行有的能打通、
    有的不能，而它们本可以都能打通。

    两条硬边界：
      · 只按**同主体键**复用，绝不按「同一域名」复用值——集团官网/行业频道页上挂着
        好几个法人主体，按域名搬值会把 A 公司的电话盖到 B 公司头上（website_contact
        当年就是为此把缓存粒度从「打分结果」改成「抓到的页面」的，同一个理由）；
      · 只追加不覆盖、走同一套 merge_values 去重（英文逗号），并且只在值真的写进去时
        登记层级与出处。
    返回 {"filled": 行数, "values": 净新增值数, "by_field": {字段: 次数}}。
    """
    log = log or (lambda m: None)
    pool = {}
    for rec in records or []:
        if not isinstance(rec, dict):
            continue
        key = company_key(rec)
        if not key:
            continue
        bucket = pool.setdefault(key, {})
        for field in _fe.CONTACT_FIELDS:
            has = False
            for value in _value_pool(rec, field):
                got = bucket.setdefault(field, [])
                vk = _wc._value_key(value)
                if vk and all(vk != _wc._value_key(x) for x in got):
                    got.append(value)
                if vk:
                    has = True
            if not has:
                continue
            at = _fe.verified_at(rec)
            seen = bucket.setdefault("_at", {})
            if at and time_newer(at, seen.get(field)):
                # 顺手记下「这一列的值最近是什么时候验的」（取同主体各行里最新的那个）：
                # 复用值的人要继承值本身的核验时间，否则被补上的那一行会带着空时间戳
                # 永远算「过期」，每一轮都被重问一次——那是把免费的一级变成烧预算的一级。
                seen[field] = at
            # 出处（层级）同样要继承：一条工商核验来的号搬到同主体另一行，它**还是**
            # 工商核验来的号。一律标成「同主体已有」会把最硬的数据判成中等数据卖，
            # 而且会让「立即跟进」闸把该直接打的电话压进培育池（2026-11-14 自查抓到）。
            donor = _fe.parse_evidence(rec).get(field)
            if donor:
                proof = _fe.parse_proof(rec).get(field, "")
                keep = bucket.setdefault("_tier", {}).get(field)
                if keep is None or _fe.TIER_SCORE.get(donor[1], 0) > _fe.TIER_SCORE.get(keep[0], 0):
                    bucket["_tier"][field] = (donor[1], proof)
    filled = 0
    new_values = 0
    by_field = {}
    for rec in records or []:
        if not isinstance(rec, dict):
            continue
        missing = _fe.missing_fields(rec)
        if not missing:
            continue
        key = company_key(rec)
        bucket = pool.get(key) or {}
        if not bucket:
            continue
        changed = []
        for field in missing:
            donors = bucket.get(field) or []
            if not donors:
                continue
            before = {_wc._value_key(x) for x in _value_pool(rec, field) if x}
            merged = _wc.merge_values(rec.get(field, ""), donors)
            after = {_wc._value_key(x) for x in _value_pool({"v": merged}, "v") if x}
            gained = after - before
            if not gained:
                continue
            rec[field] = merged
            changed.append(field)
            by_field[field] = by_field.get(field, 0) + 1
            new_values += len(gained)
            # 出处指向 donor 那一行，让用户能顺着查回去（而不是只看到一个号）
            name = str(rec.get("公司名称") or rec.get("公司名") or "")[:12]
            inherit = (bucket.get("_tier") or {}).get(field)
            if inherit and inherit[0] in _fe.TIER_SCORE:
                _fe.set_field_evidence(
                    rec, field, inherit[0],
                    f"同主体另一行（{name}）"
                    + (f"·原出处：{inherit[1]}"[:70] if inherit[1] else ""))
            else:
                _fe.set_field_evidence(rec, field, "carry", f"同主体另一行（{name}）")
            donor_at = (bucket.get("_at") or {}).get(field)
            if time_newer(donor_at, _fe.verified_at(rec)):
                _fe.stamp_verified(rec, donor_at)
        if changed:
            filled += 1
    if filled:
        log(f"🧵 瀑布{stage_tag('carry')}：{filled} 行零请求补齐，"
            + "、".join(f"{f} {n} 行" for f, n in sorted(by_field.items())))
    return {"filled": filled, "values": new_values, "by_field": by_field}


def pick_targets(records, include_stale=False, ttl_days=None, at_field="采集时间"):
    """命中即停的闸：三个字段都非空**且**都够可信的行，一次网络请求都不该再花。

    只判「非空」是不够的——那等于把「已经有一条工商核验的号」和「有一条来自
    品牌推导的号」同等对待，前者根本不必再问。这正是 Clay 那条「顺序比源重要」
    在本工具里的落点：先花免费的，再花便宜的，最后才动浏览器和配额。

    ``include_stale`` 是功能 I 的入口：勾了「到期自动重验」之后，**有值但已经过期**
    的字段同样算待办。核验时间优先取「最后核验时间」列（重验到同一个号时由
    website_contact 刷新），该列为空时退回行自带的采集时间列（历史表就是靠它自证
    出处）；两列都缺失或格式认不出来时按「不知道」处理并算到期——把未知读成
    「还新」就等于这个功能永远不生效，那是最没用的实现方式。
    """
    keep, skip, stale = [], 0, 0
    for rec in records or []:
        if not isinstance(rec, dict):
            continue
        missing = _fe.missing_fields(rec)
        confident = all(_fe.is_confident(rec, f) for f in _fe.CONTACT_FIELDS
                        if str(rec.get(f) or "").strip())
        if missing or not confident:
            keep.append(rec)
            continue
        if include_stale:
            verified = _fe.verified_at(rec, at_field)
            tiers = {f: (t or "source") for f, (l, t) in _fe.parse_evidence(rec).items()}
            if any(_fe.is_stale(f, tiers.get(f, "source"), verified, ttl_days)
                   for f in _fe.CONTACT_FIELDS if str(rec.get(f) or "").strip()):
                stale += 1
                keep.append(rec)
                continue
        skip += 1
    return keep, skip, stale


def workable_count(records):
    """这一轮**动得起来**的行数：带可用官网的 ∪ 能推出官网的 ∪ 同主体别处已有值的。

    存在的理由很具体：界面上的「能补几家」以前只按 `pick_site` 数，那是单跳时代的
    口径。勾了瀑布之后还拿它当预检查，就会出现「0 行可补，按钮拦住」而实际明明能
    补一大截——用户读到的却是「功能没生效」。判据必须跟着开关换口径，
    所以这一份和 `run_waterfall` 用同样的三个条件（同一次实现，不是第二份规则）。
    """
    recs = [r for r in (records or []) if isinstance(r, dict)]
    pool = set()
    for rec in recs:
        key = company_key(rec)
        if key and any(str(rec.get(f) or "").strip() for f in _fe.CONTACT_FIELDS):
            pool.add(key)
    n = 0
    for rec in recs:
        if _wc.pick_site(rec) or discover_site(rec)[0] or company_key(rec) in pool:
            n += 1
    return n


# ── 变更留痕（功能 I 的「diff 表」）──────────────────────────────────
# 为什么必须有这一份：到期重验与瀑布补全都是**就地改表**，改完只留一句「补了 N 个号」
# 是不够的——用户真正会问的是「哪一家的哪个号从什么变成了什么、凭什么」。而且没有
# 这份账，「值被改写」这种本不该发生的形状（本模块承诺只追加不覆盖）就会永远隐身。
def snapshot_values(records):
    """补全前的联系方式快照（按对象身份索引：记录是就地改的，按内容回头找会找错）。"""
    snap = {}
    for rec in records or []:
        if not isinstance(rec, dict):
            continue
        snap[id(rec)] = (str(rec.get("公司名称") or rec.get("公司名") or ""),
                         {f: str(rec.get(f) or "") for f in _fe.CONTACT_FIELDS})
    return snap


def diff_changes(records, snap):
    """快照 → 现值 的逐字段变更清单（报表「字段变更」那张表的数据）。"""
    rows = []
    for rec in records or []:
        if not isinstance(rec, dict):
            continue
        base = snap.get(id(rec))
        if not base:
            continue
        name, old = base
        for field in _fe.CONTACT_FIELDS:
            now = str(rec.get(field) or "")
            if now == old.get(field, ""):
                continue
            before = {_wc._value_key(x) for x in _wc._SPLIT_RE.split(old.get(field, ""))
                      if x and _wc._value_key(x)}
            after = [_wc._value_key(x) for x in _wc._SPLIT_RE.split(now) if x]
            added = [x for x in _wc._SPLIT_RE.split(now)
                     if x and _wc._value_key(x) and _wc._value_key(x) not in before]
            lost = [k for k in before if k not in after]
            got = _fe.parse_evidence(rec).get(field)
            kind = "新增" if not old.get(field, "").strip() else "补充"
            if lost:
                # 旧值消失了：这不是本模块该有的行为，落成一行异常而不是悄悄过去
                kind = "改写(异常)"
            rows.append({"企业名称": name, "字段": field, "变化": kind,
                         "旧值": old.get(field, ""), "新值": "、".join(added) or now,
                         "命中层级": (got or ("", ""))[0],
                         "补全证据": _fe.parse_proof(rec).get(field, "")})
    return rows


# ── 总入口（会话层调用的唯一入口）────────────────────────────────────
def run_waterfall(records, log=None, workers=6, budget=15 * 60, timeout=_wc.TIMEOUT,
                  stop=None, limit=1500, only_missing=False, registry_queue=None,
                  reverify=False, reverify_days=None):
    """三段跑完返回一份分档账目；任何一级出错都只影响那一级，绝不冒泡。

    ``registry_queue`` 是会话层给的可调用对象：`fn(names) -> int`（投进工商补采
    名单并返回真正收下几家）。由会话层提供而不是本模块自己去碰 pending 台账，
    理由是那份台账有接管、退避、属地关卡，全都会话层的规矩，不该在补全模块里
    被第二次实现。没传就不做第 3 级，并把这件事如实报出来。
    """
    log = log or (lambda m: None)
    recs = [r for r in (records or []) if isinstance(r, dict)]
    stats = {"total": len(recs), "carry": {}, "network": {}, "registry": {},
             "stamped": 0, "confident_skip": 0, "stale": 0, "verified": 0, "changes": []}
    if not recs:
        return stats
    # 快照必须在**任何一级动手之前**拍：漏了第 1 级（同主体复用）的变更，
    # 报表里就会出现「值变了但变更表是空的」这种最伤信任的形状。
    snap = snapshot_values(recs)

    # 先给工商三路的既有值标层，再做后面的判断：顺序反过来会让那几行带着
    # 「平台自带」55 分进评分与排序，而它们本来就是全表最硬的一档。
    try:
        stats["stamped"] = stamp_source_evidence(recs, log=log)
    except Exception as e:
        log(f"⚠ 层级标注没跑成（{type(e).__name__}）：按「平台自带」兜底计，不改值")

    try:
        stats["carry"] = carry_pass(recs, log=log)
    except Exception as e:
        log(f"⚠ 瀑布第 {stage_no('carry')} 级没跑成（{type(e).__name__}）：跳过，后面的网络级照常")
        stats["carry"] = {"error": type(e).__name__}

    keep, skip, stale = pick_targets(recs, include_stale=bool(reverify),
                                     ttl_days=reverify_days)
    stats["confident_skip"] = skip
    stats["stale"] = stale
    stats["network"] = {"eligible": 0}
    if keep:
        try:
            stats["network"] = _wc.enrich_records(
                keep, log=log, workers=workers, budget=budget, timeout=timeout,
                stop=stop, limit=limit, only_missing=only_missing,
                resolver=resolver_for(recs))
        except Exception as e:
            log(f"⚠ 瀑布网络级没跑成（{type(e).__name__}）：不影响已补到的值")
            stats["network"] = {"error": type(e).__name__}
    elif skip:
        log(f"🛑 命中即停：{skip} 行三档联系方式都已齐且可信，本轮一次请求都没花")

    try:
        stats["changes"] = diff_changes(recs, snap)
    except Exception as e:
        log(f"⚠ 变更清单没算成（{type(e).__name__}）：值已经补上了，只是这一轮的 diff 表为空")
        stats["changes"] = []
    stats["verified"] = int((stats.get("network") or {}).get("verified") or 0)

    still = [r for r in recs if _fe.missing_fields(r)]
    names = []
    for rec in still:
        name = str(rec.get("公司名称") or rec.get("公司名") or "").strip()
        if name and name not in names:
            names.append(name)
    if names and callable(registry_queue):
        batch = names[:QUEUE_LIMIT]
        try:
            took = registry_queue(batch)
        except Exception as e:
            took = 0
            log(f"⚠ 瀑布第 {stage_no('registry')} 级投名单没成功（{type(e).__name__}）：一家都不算投出去")
        stats["registry"] = {"missing": len(names), "queued": int(took or 0),
                             "over_limit": max(0, len(names) - len(batch))}
        log(f"🏛 瀑布{stage_tag('registry')}：还缺 {len(names)} 家，"
            f"已投入企知道/风鸟/爱企查队列 {stats['registry']['queued']} 家"
            + (f"（另有 {stats['registry']['over_limit']} 家超出单轮上限，下轮接着投）"
               if stats["registry"]["over_limit"] else "")
            + "；下一轮自动逐家查工商并回填")
    elif names:
        # 没接队列 ≠ 没有缺口：这一句必须说出来，否则「瀑布跑完了」会被读成
        # 「已经没有缺的了」——那是本仓库最容易被误读的一类账目。
        log(f"🏛 瀑布第 {stage_no('registry')} 级未接上：还有 {len(names)} 家缺联系方式，"
            f"但本轮没有工商补采队列可用（未勾选或通道已熔断），没有投任何一家")
        stats["registry"] = {"missing": len(names), "queued": 0}
    return stats


def stage_lines(stats):
    """把分档账目压成给人看的几行字（会话与界面共用这一份口径）。"""
    lines = []
    net = stats.get("network") or {}
    carry = stats.get("carry") or {}
    reg = stats.get("registry") or {}
    if carry.get("filled"):
        lines.append(f"· {stage_name('carry')}：{carry['filled']} 行零请求补齐"
                     f"（净新增 {carry.get('values', 0)} 个值）")
    if net.get("eligible"):
        lines.append(f"· 网络级：{net['eligible']} 家去问过"
                     f"（补到 {net.get('ok', 0)} 家、净新增 {net.get('new_values', net.get('added', 0))} 个值，"
                     f"缓存已有 {net.get('cached', 0)} 行）")
    tiers = net.get("tiers") or {}
    if tiers:
        lines.append("· 命中层级：" + "、".join(
            # 这里可能混进没登记过的来源名（tiers 的键来自数据本身），所以留一个原样兜底，
            # 但认得出的那几个一定出现层级表里的写法。
            f"{_fe.TIER_LABEL.get(k, k)} {v}" for k, v in
            sorted(tiers.items(), key=lambda kv: -kv[1])))
    if stats.get("confident_skip"):
        lines.append(f"· 命中即停：{stats['confident_skip']} 行早已齐且可信，未花请求")
    if stats.get("stale"):
        lines.append(f"· 到期重验：{stats['stale']} 行按字段 TTL 判定已过期，本轮重新问过")
    if stats.get("verified"):
        lines.append(f"· 复核确认：{stats['verified']} 行刷新了「最后核验时间」"
                     f"（同一批号在 TTL 之内不会再被重问）")
    changes = stats.get("changes") or []
    if changes:
        rewrote = sum(1 for c in changes if str(c.get("变化") or "").startswith("改写"))
        lines.append(f"· 字段变更：{len(changes)} 处已记入报表「字段变更」表"
                     + (f"（其中 {rewrote} 处旧值消失，属异常形状，请核对）" if rewrote else ""))
    if reg:
        lines.append(f"· {stage_name('registry')}：缺 {reg.get('missing', 0)} 家 → 投入 {reg.get('queued', 0)} 家")
    if net.get("reasons"):
        top = sorted(net["reasons"].items(), key=lambda kv: -kv[1])[:3]
        lines.append("· 为什么没出：" + "、".join(f"{k} {v}" for k, v in top))
    if net.get("budget_hit"):
        lines.append(f"· 预算到点：{net['budget_hit']} 家本轮没轮到，下次收尾自动接着做")
    return lines
