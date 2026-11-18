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
运行：纯标准库 + 五个同级模块（website_contact / field_evidence / site_discovery /
exchange_master / text_shape），可离线全量验证（网络由 website_contact 的夹具接管）。
"""
from __future__ import annotations

import re

import field_evidence as _fe
import website_contact as _wc
# 披露主数据层（exchange 级的数据侧）：它只在**本地**按名称对齐，逐行一个包都不发。
# 归一口径由本模块交出去（align_key），不在那边再写一份——见那里的说明。
import exchange_master as _em
# 发现层只在**函数里**惰性回引本模块（_brand_tokens），所以这里可以顶层导入而无环。
# 为什么不留两份推导：同一个商号在两条路上算出两个域名，日志上就永远说不清哪一家该为
# 哪个候选负责。
import site_discovery as _sd
# 把一句话收成一行只许走 text_shape 的把手：本级写日志里的公司名时一度直接
# `name[:16]`，而公司名是个词不是句子——切完就是「中国船舶集团第七〇五研究」那种半截名字
# （交付链硬门 30a 与套件 23b 钉的正是这一族形状）。
import text_shape as _ts

__all__ = [
    "STAGES", "stage_no", "stage_name", "stage_tag", "stage_mark",
    "stage_chain", "company_key", "align_key", "needs_work",
    "email_domains", "brand_tokens",
    "discover_site", "resolver_for", "registry_source_names", "stamp_source_evidence",
    "exchange_pass", "master_match_for", "disclosed_site_for",
    "carry_pass", "pick_targets", "workable_count", "run_waterfall", "stage_lines",
    "snapshot_values", "diff_changes", "time_newer",
]

# 阶段表就是执行顺序（成本升序）。名字与 field_evidence 的层级 slug 同一套，
# 不是第二份命名：能在两张表里各写一遍的东西，迟早会写成两种口径。
# exchange 紧跟 carry：它和 carry 一样**一次请求都不花**（本地主数据字典命中），
# 而 mailsite/site/brand 每一级都要掏一次外网。把免费的一级排在烧请求的那几级
# 之前，是 Clay 那条「顺序决定账单」在本工具里唯一需要重新排的地方。
# search（搜索反查）排在 brand 之后、registry 之前：同为成本 2 但可信度更低（候选来自
# 引擎结果页，必须抓回正文过归属核验才算数），而它比「投工商队列」便宜一整档。
STAGES = ("carry", "exchange", "mailsite", "site", "brand", "search", "registry")
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


def stage_chain(sep=" → ", stages=None):
    """按执行顺序把各级名字串出来（界面话术与会话日志共用这一份，个数不手抄）。"""
    return sep.join(stage_name(slug) for slug in (STAGES if stages is None else stages))


# 要**额外花外网配额**才会走的那几级。这个名单只在这里记一次：界面上那一条勾选、
# 会话上的开关、以及「本轮到底试过了哪几种途径」那句解释，全部从这一份算。
# 为什么不能把「几种途径都试过」那句话留在调用点手写：上一轮它写的是「五种」并把
# 五级翻译成白话句子，层级表多一级时那句话就变成当众报数报错（而它偏偏不在层级名
# 硬门的扫描范围里）。现在更进一步：不仅要数对，还要**只数真的试过的那几级**——
# 没开现网反查时说「六种都试过」，是一句看不出来的谎。
EXPENSIVE_STAGES = ("search",)


def active_stages(expensive_lookup=False):
    """本轮真的会走的那几级：没开现网反查时，那一级一次都不会问，就不能算进话里。"""
    if expensive_lookup:
        return STAGES
    return tuple(s for s in STAGES if s not in EXPENSIVE_STAGES)


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
def _row_name(rec):
    """这一行的企业名（三个历史列名按优先级取第一个非空）。口径只在这里定一次。"""
    return str((rec or {}).get("公司名称") or (rec or {}).get("公司名")
               or (rec or {}).get("company") or "").strip()


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
    name = _row_name(rec)
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


# ── 主数据对齐口径（``exchange`` 级与 exchange_master 之间唯一的契约）────────
def align_key(name):
    """组织名 → (核心名, 括号里的别名, 是否机构类)：交给 :mod:`exchange_master` 对齐用的口径。

    实现直接委派给 exchange_master 里那三个机械件，因为**尾缀词表只能有一个登记点**：
    调用点再抄一份，就会出现「同一个名字两条路算出两个键」，那时错配与漏配会同时存在
    而日志里只能看到其中一种。

    刻意**不复用 company_key**（它认的是「同一张表里像同一家」，剥 集团/厂/中心 只是多并
    几行，方向安全），也不复用 site_discovery.brand_core（它认的是「搜索查询要宽」，会把
    「怡星机电」与「怡星贸易」抹成「怡星」）：主数据对齐要求反过来——剥下的每一个字都
    不能改变主体是谁，否则就把子公司的号挂到母公司头上（真实名录打回过
    「中国船舶集团有限公司」这一例，判据与词表都在 exchange_master 的尾缀表里）。
    """
    return _em.name_core(name), _em.paren_abbr(name), _em.is_institution(name)


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
        raw = str(token or "").lower()
        bits = [b for b in re.split(r"[_\-.]", raw) if b]
        # 尾巴是 TLD 词就丢掉这一段，而不是跟前面粘成一坨：上一版用 _NON_LATIN_RE 直接
        # 删分隔符，`yixingjd_com` 于是变成 `yixingjdcom`——一个「看着像域名、实际不存在」
        # 的形状，白付一次 DNS 加一次抓取，还可能撞上别人的站。
        if len(bits) >= 2 and bits[-1] in _wc.TLD_TAIL_WORDS:
            bits = bits[:-1]
        # 连字符是合法域名字符，必须留着：`mingliang-led` 粘成 `mingliangled` 之后
        # 就永远拼不回它真正的站（mingliang-led.com）。
        t = re.sub(r"[^a-z0-9\-]+", "", "-".join(bits)).strip("-")
        if len(t) < BRAND_MIN_LEN or t in GENERIC_HOST_TOKENS or t in out:
            return
        if sum(c.isdigit() for c in t) * 2 >= len(t):
            # 数字占一半以上是平台自发编号而不是商号（a1b2c3 / x12345 / shop001）。
            # 用占比而不是「有几个数字」：后者会误杀 51job、360buy 这种真字号。
            return
        out.append(t)

    for col in ("详情URL", "公司网址", "官网"):
        url = str((rec or {}).get(col) or "").strip()
        if not url:
            continue
        host = _wc.host_of(url if "://" in url else "https://" + url)
        parts = [p for p in str(host or "").split(".") if p]
        if len(parts) < 2:
            continue
        # 平台命中必须取**最长**一条：PLATFORM_HOSTS 里同时有 hc360.com 和
        # qy.hc360.com，取短的那条会把 x.qy.hc360.com 的租户段算成 qy。
        plat = ""
        for cand in _wc.PLATFORM_HOSTS:
            if host.endswith("." + cand) and len(cand) > len(plat):
                plat = cand
        if plat:
            # 平台租户：商号在平台注册域**左边**，而且可能不止一级，所以逐个交给
            # add() 过滤而不是猜哪一级是商号：hapstar.cn.b2b168.com 的租户标签自己
            # 带点（商号是 hapstar、下一段 cn 是 TLD），按点数位取 parts[-3] 会拿到
            # `cn` ——v16 那个 flagship 形状恰好会被数点法取错。
            for label in parts[:len(parts) - len(plat.split("."))]:
                add(label)
        else:
            # 自有域名：商号 = 注册域的第一段。注册后缀的取法不另列一份表，直接用
            # `_wc._reg_domain`（它已经管了 com.cn/gov.cn/co.uk 这类双段后缀）。
            # 上一版取「最左标签」，于是 www.brand.com、shop.acme.com、www.hapstar.cn
            # 两个取位都落在子域前缀上（www/shop 被 GENERIC 拦掉）——实测整行一个
            # 商号段都产不出，那是那轮「品牌域名推导」只出 28 家的原因之一。
            add(_wc._reg_domain(host).split(".")[0])
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
    """行自带官网字段不可用时的官网发现：返回 **一串** (候选URL, 层级slug)，认不出返回空列表。

    为什么从「一个」改成「一串」：旧口径只取第一个候选，而第一个候选恰恰是最可能
    拍到别人家的那一个（hapstar 这种字号，.com 与 .cn 是两个不相干的主）——归属核验
    一拒就整行作废，后面本来能成的候选白白被拒。`website_contact.enrich_records` 早就
    支持按候选序试到抽到值为止（单行有个数与墙钟上限双封顶），这里不交一串过去就是卡在那儿。

    为什么这里**一个 DNS 都不发**（2026-11-18 那次整窗转圈的根因就在这里）：本函数被
    两条路调用，一条是工作线程里的真跑，另一条是**界面线程**上的可补家数预览
    （`workable_count`）。旧写法每个候选都叫一次 `is_public_host`，而它默认会查 DNS，
    且 `socket.getaddrinfo` 没有 timeout 参数——一个达不到的域能卡住调用线程十几秒，
    预览 900 行就是十几分钟的不响应。可达性判定交给批量的并发预检通道
    （`preflight_hosts`，超时 + 磁盘缓存），这里只做不需要网络的形状判定。
    """
    out, seen = [], set()

    def push(url, tier):
        url = str(url or "").strip()
        if not url or url in seen:
            return
        seen.add(url)
        out.append((url, tier))

    for reg in email_domains(rec):
        # allow_lookup=False：只看字面量是不是内网/保留地址，不在这里碰 DNS
        if (not _wc.is_platform_host(reg)
                and _wc.is_public_host(reg, allow_lookup=False)):
            push(f"https://{reg}/", "mailsite")
    # 发现层的四条零网络通道（子域反推 / 同段另一 TLD / 商号拼域名 / 拼音）：
    # 顺序即可信序，封顶由 website_contact 的 MAX_CANDIDATES_PER_ROW 接住。
    for url, tier in _sd.local_candidates(rec):
        push(url, tier)
    return out


def resolver_for(records, log=None, site_of=None):
    """给 website_contact 的 provider：只交**零成本**候选，同一张表里同域名的行共用一个结论。

    为什么要这层包装而不是直接把 discover_site 传下去：一次任务里同一主体常有几十行
    （同一店铺的不同产品），discover_site 对它们算出的候选完全相同，但
    「谁先被谁抓到」是网络层的缓存管的，这里管的只是**别再重复推导一遍**；
    另外，同注册域名的某一行已经带着可用官网时，另一行直接借用它，
    省掉一次推导并让两行的层级记成同一档（同一来源、同一可信度）。

    ``site_of``（可选，``fn(rec) -> url|""``）：交易所披露主数据里那一家**自己报给监管的
    网址**，由 :func:`disclosed_site_for` 提供。把它放在第一位而不是丢着不用，理由是
    这一行本来就要抓（披露联系方式没覆盖到的那几个字段仍然缺）：同一个名额里
    用「主体自报的域名」替掉「猜出来的域名」，既不多花一个请求，又比归属核验
    拒一轮再试第二个便宜。两个刻意的约束：
      · 层级记 ``site`` 不记 ``exchange``——值确实是页面上抽出来的，可信分该跟
        页面抽取同一档（82），披露贡献的是域名；域名来源那一事实在
        :func:`stage_lines` 里单独说一句，不假装没发生过；
      · **不进 escalate 名额**：要花钱的那一级另有自己的配额（见 :func:`escalator_for`），
        这里只动免费候选的排序，不挤占后面的家数。

    这一层**永远不出现网反查**：它被界面线程的可补家数预览间接调用（见
    :func:`discover_site` 的说明），在这里加一次网络动作就是把 2026-11-18 那次
    整窗转圈改回来。要花钱的那一级只走 :func:`escalator_for`；``site_of`` 走的
    :func:`master_match_for` 也不读盘、不建索引（盘上没热好的索引时返回空串）。
    """
    log = log or (lambda m: None)
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
        # 同一个注册域只占一个候选位：单行预算只有 MAX_CANDIDATES_PER_ROW 个名额，
        # 同一个站占两个位就等于后面那一路拼音/商号候选整行拿不到（这一条原先
        # 只在「借来的同域名官网」那一种形状上生效，同一个理由不该只写一半）。
        taken = []
        out = []

        def push(url, tier):
            url = str(url or "").strip()
            if not url:
                return
            reg = _wc._reg_domain(_wc.host_of(url) or "")
            if reg and reg in taken:
                return
            if reg:
                taken.append(reg)
            out.append((url, tier))

        if callable(site_of):
            try:
                push(site_of(rec), "site")
            except Exception:
                # 主数据层出错不该把整条发现路带走：回到今天「只按零成本候选排」的形状
                pass
        for reg in email_domains(rec):
            hit = by_domain.get(reg)
            if hit:
                # 同域名另一行本来就带着官网：借那一个，比按邮箱反查更稳（那是现成的
                # 正确 URL，含路径与端口），但来源仍然是「推导」而不是行自带，
                # 所以记 mailsite 而不是 site。
                push(hit, "mailsite")
                break
        for url, tier in discover_site(rec):
            push(url, tier)
        return out
    return _resolve


def escalator_for(expensive=(), log=None):
    """现网反查的**惰性** provider：只在一家公司免费候选全空手时才被问一次。

    为什么不在 resolver_for 里把 SERP 候选直接拼在后面（先试过、已否决的形状）：
    两条实测理由，不是口味问题。
      · resolver 是在抓页**之前**一次性算完的（website_contact 的 cand 预备阶段），
        那时谁能在第一个候选就抽到值还没发生——预先花的 SERP 配额要为大量
        「根本不用花」的行买单，而整轮预算到点时那些钱连一行结果都换不回来。
      · 零网络候选一家最多交 4 个（site_discovery.LOCAL_MAX_CANDIDATES），而单行
        只取前 3 个（website_contact.MAX_CANDIDATES_PER_ROW）。按「剩余名额」追加的
        结果是：凡是能拼出免费候选的行 room 恒为负，这一问永远轮不到；而拼不出
        候选的行本来就没几个——层级表里多了一级、实际一个包都不发，正是本仓库
        记过账的「定义了没接线＝功能原地隐身」。
    所以追加那一级在免费候选**之外**另拿名额（website_contact.ESCALATE_MAX_PER_ROW），
    管住它成本的是三重闸：空手而归才问、单行墙钟照旧共享、通道自带令牌桶与熔断。

    ``expensive`` 为空时返回 None：下游连一次函数调用都不会发生——「没开」与
    「开了但一家都没反出来」在账上是两件事，把 None 写成空函数就会把前者读成后者。
    """
    if not expensive:
        return None
    log = log or (lambda m: None)
    told = []

    def _escalate(rec):
        try:
            return _sd.discover_expensive(rec, log=log, use=expensive)
        except Exception as e:
            # 出错不能静默：这一级是本轮唯一会主动出外网的地方，它整级坏掉时
            # 「反查 0 家」与「从来没问过」完全同形，那是最难查的一类回归。
            # 只说一次：一轮几百行，逐行刷同一句话会把真正的采集日志冲掉。
            if not told:
                told.append(1)
                log(f"⚠ 现网反查这一级出错（{type(e).__name__}: {e}）：本轮后面的家都不再问，"
                    f"只按零成本候选的结论收尾")
            return []
    return _escalate


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
        # 层级名走 _fe.TIER_LABEL 现查：这里手抄一个「工商核验」，改天层级表换叫法，
        # 引擎日志还在念旧名字，而「补全层级」那一列落的是新名字——用户按日志找不到列值。
        log(f"🏷 已给 {stamped} 行工商通道来的值标上「{_fe.TIER_LABEL['registry']}」层（评分按最硬的一层算）")
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


# ── 第二级：交易所披露主数据（零请求，给的是主体自报的值）───────────────
def master_match_for(rec):
    """这一行在披露主数据里对到的主体（对不到/还没就位返回 None）。

    三件事在函数体里而不是在调用方判：**不读盘、不出网、不建索引**。这一句会被
    界面线程的可补家数预览间接调到（:func:`workable_count`），也会在每一行抓页前被
    问一下（:func:`disclosed_site_for`）；那一份盘有两万多条，解析加建键实测两秒多
    ——为一句预览去读它，就是 2026-11-18 那个转圈另开一个入口。索引没热好时返回
    None 就是「本级今天没话说」，而不是「这家没上市」：两种形状在
    :func:`exchange_pass` 的账里分开（那里才允许建索引）。
    """
    if _em.index() is None:
        return None
    name = _row_name(rec)
    if not name:
        return None
    try:
        return _em.match(name, align_key)[0]
    except Exception:
        # 主数据层出错永远不该把后面的级带走（逐行不静默：账目里那一行会少一个命中，
        # 而出错计数在 exchange_pass 的 asked-matched 差里看得见）
        return None


def disclosed_site_for(rec):
    """对到的主体自己披露的公司网址（拿不到返回空串）。口径与联系方式同一道闸：
    平台域、内网域、形状不合格的都不算（见 exchange_master.disclosed_site）。"""
    return str((master_match_for(rec) or {}).get("web") or "").strip()


def _disclosed_proof(ent, grade, as_of):
    """「补全证据」那一句：谁报的、哪个市场、按哪一档对上的、名录哪个版本。

    分隔一律用「·」，**绝不用竖线**：层级证据列在解析时按 ``[｜|]`` 切段
    （field_evidence.parse_proof），证据串里出现一个竖线就会把一句话切成两段
    互相认不出的东西——那是「数据自己跟自己对不上账」里最难查的一种。
    """
    codes = _em.entity_codes(ent)
    code = (f"{codes[0]}等{len(codes)}个市场" if len(codes) > 1
            else (codes[0] if codes else ""))
    bits = [x for x in (code, str(ent.get("name") or ""),
                        _em.GRADE_LABEL.get(grade, grade)) if x]
    return ("东方财富F10披露（" + "·".join(bits) + "）"
            + (f" 名录 {as_of}" if as_of else ""))


def exchange_pass(records, log=None, allow_fetch=None, include_stale=False,
                  ttl_days=None, stop=None, budget=_em.FETCH_BUDGET_S):
    """拿表里的公司名去**本地主数据**里对齐公开披露的联系方式（逐行零请求）。

    与 :func:`carry_pass` 同属成本 0 那两档，但值的来源完全不同：carry 复用本表已有的
    值，这一级拿的是主体自己报给监管的号（年报/招股书口径）。三条边界：
      · **只补不覆盖**：走同一套 merge_values + 净新增差集，旧号一个都不丢（它可能是
        唯一的真号）；只在对到的那一行确实写了值时才记层级；
      · **宁缺毋滥**：对齐交给 exchange_master.match 的三档闸 + 名录侧唯一主体，被闸
        拦下的行计入 ``rejected``（与 ``unmatched`` 分账：一个是「名录里有名字像的但不
        敢认」，一个是「名录里根本没这家」，下一步处置完全不同）；
      · **时间不倒拨**：「最后核验时间」只取名录版本日期，且只在比行内现有时间新时
        才刷新（披露数据常常比去年那轮采集更旧，拿它盖掉新时间就是把新数据判成旧数据）。

    本级**不自己往日志里报账**（除了拉取那几句）：账目统一由 :func:`stage_lines` 渲染，
    而边采边补那一路只渲染报表不渲染逐行——两处各说一遍同一件事是日志里出现
    两个不同数字的开端。

    返回：``ensure`` 那七键（present/count/as_of/age_days/partial/fetched/reason）+ 本级
    自己的 asked/matched/filled/values/by_field/by_grade/unmatched/rejected/
    rejected_examples/with_site。count 是**主体家数**不是记录条数（同一个口径）。
    """
    log = log or (lambda m: None)
    out = {"present": False, "count": 0, "as_of": "", "age_days": None,
           "partial": False, "fetched": False, "reason": "",
           "asked": 0, "matched": 0, "filled": 0, "values": 0,
           "by_field": {}, "by_grade": {}, "unmatched": 0, "rejected": 0,
           "rejected_examples": [], "with_site": 0}
    recs = [r for r in (records or []) if isinstance(r, dict)]
    try:
        got = _em.ensure(align=align_key, allow_fetch=allow_fetch, log=log,
                         stop=stop, budget=budget)
    except Exception as e:
        out["reason"] = f"主数据层出错（{type(e).__name__}）：这一级本轮不参与"
        return out
    for k in ("present", "count", "as_of", "age_days", "partial", "fetched", "reason"):
        out[k] = got.get(k, out[k])
    if not out["present"]:
        return out
    for rec in recs:
        if not needs_work(rec, include_stale=include_stale, ttl_days=ttl_days)[0]:
            continue
        name = _row_name(rec)
        if not name:
            continue
        out["asked"] += 1
        try:
            ent, grade, why = _em.match(name, align_key)
        except Exception as e:
            out["rejected"] += 1
            if len(out["rejected_examples"]) < 3:
                out["rejected_examples"].append(
                    f"{_ts.short_reason(name, 24)}：对齐出错（{type(e).__name__}）")
            continue
        if ent is None:
            if grade == _em.REJECTED:
                out["rejected"] += 1
                if len(out["rejected_examples"]) < 3:
                    out["rejected_examples"].append(
                        f"{_ts.short_reason(name, 24)}：{why}")
            else:
                out["unmatched"] += 1
            continue
        out["matched"] += 1
        out["by_grade"][grade] = out["by_grade"].get(grade, 0) + 1
        if str(ent.get("web") or "").strip():
            out["with_site"] += 1
        proof = _disclosed_proof(ent, grade, out["as_of"])
        changed = []
        for field in _fe.CONTACT_FIELDS:
            donors = (ent.get("fields") or {}).get(field) or []
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
            out["by_field"][field] = out["by_field"].get(field, 0) + 1
            out["values"] += len(gained)
            _fe.set_field_evidence(rec, field, "exchange", proof)
        if changed:
            out["filled"] += 1
            if time_newer(out["as_of"], _fe.verified_at(rec)):
                _fe.stamp_verified(rec, out["as_of"])
    return out


def pick_targets(records, include_stale=False, ttl_days=None, at_field="采集时间"):
    """命中即停的闸：三个字段都非空**且**都够可信的行，一次网络请求都不该再花。

    只判「非空」是不够的——那等于把「已经有一条工商核验的号」和「有一条来自
    品牌推导的号」同等对待，前者根本不必再问。这正是 Clay 那条「顺序比源重要」
    在本工具里的落点：先花免费的，再花便宜的，最后才动浏览器和配额。

    逐行判据本身抽到 :func:`needs_work`（与 :func:`exchange_pass` 共用一份），本函数
    只负责计数与分堆；``include_stale`` 是功能 I 的入口，语义逐字不变：
    勾了「到期自动重验」之后，**有值但已经过期**的字段同样算待办（核验时间优先取
    「最后核验时间」列，该列为空时退回行自带的采集时间列，两列都认不出来时按
    「不知道」处理并算到期——把未知读成「还新」就等于这个功能永远不生效）。
    """
    keep, skip, stale = [], 0, 0
    for rec in records or []:
        if not isinstance(rec, dict):
            continue
        want, because_stale = needs_work(rec, include_stale=include_stale,
                                         ttl_days=ttl_days, at_field=at_field)
        if not want:
            skip += 1
            continue
        if because_stale:
            stale += 1
        keep.append(rec)
    return keep, skip, stale


def needs_work(rec, include_stale=False, ttl_days=None, at_field="采集时间"):
    """这一行还要不要再问：``(要不要问, 是不是因为到期才要问)``。

    这一份判据是整条瀑布唯一的「谁还需要问」入口：网络级（:func:`pick_targets`）与
    零成本的披露级（:func:`exchange_pass`）必须用同一个问法，否则会出现「免费那一面
    已经把它当完事了、烧请求那一面又把它排进队」这种两边数字对不上的账。
    为什么不是「缺哪个字段」而是「这一行要不要问」：披露一级给的是整家主体的
    披露包（电话+手机+邮箱一起），逐字段排队在这里没意义，而行级口径两边一致。
    """
    if not isinstance(rec, dict):
        return False, False
    missing = _fe.missing_fields(rec)
    confident = all(_fe.is_confident(rec, f) for f in _fe.CONTACT_FIELDS
                    if str(rec.get(f) or "").strip())
    if missing or not confident:
        return True, False
    if include_stale:
        verified = _fe.verified_at(rec, at_field)
        tiers = {f: (t or "source") for f, (l, t) in _fe.parse_evidence(rec).items()}
        if any(_fe.is_stale(f, tiers.get(f, "source"), verified, ttl_days)
               for f in _fe.CONTACT_FIELDS if str(rec.get(f) or "").strip()):
            return True, True
    return False, False


def workable_count(records):
    """这一轮**动得起来**的行数：带可用官网的 ∪ 能推出官网的 ∪ 同主体别处已有值的
    ∪ 披露主数据能对上主体的（仅当索引已经热好）。

    存在的理由很具体：界面上的「能补几家」以前只按 `pick_site` 数，那是单跳时代的
    口径。勾了瀑布之后还拿它当预检查，就会出现「0 行可补，按钮拦住」而实际明明能
    补一大截——用户读到的却是「功能没生效」。判据必须跟着开关换口径，
    所以这一份和 `run_waterfall` 用同样的四个条件（同一次实现，不是第二份规则）。

    第四条为什么必须加上「索引已热」这个前提：这一句跑在**界面线程**上，而为了它
    去解析两万多条主数据、建一遍索引（实测两秒多）就是把一个预览数字变成新的卡死
    源头——预览少算那几家不会错事（按钮照旧能点、真跑时那一行仍然会被补），
    而多一次两秒的卡顿用户当天就会报上来。
    """
    recs = [r for r in (records or []) if isinstance(r, dict)]
    pool = set()
    for rec in recs:
        key = company_key(rec)
        if key and any(str(rec.get(f) or "").strip() for f in _fe.CONTACT_FIELDS):
            pool.add(key)
    warm = _em.index() is not None
    n = 0
    for rec in recs:
        if (_wc.pick_site(rec) or discover_site(rec) or company_key(rec) in pool
                or (warm and master_match_for(rec) is not None)):
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
                  reverify=False, reverify_days=None,
                  shop_lookup=False, serp_lookup=False, master_fetch=None):
    """三段跑完返回一份分档账目；任何一级出错都只影响那一级，绝不冒泡。

    ``registry_queue`` 是会话层给的可调用对象：`fn(names) -> int`（投进工商补采
    名单并返回真正收下几家）。由会话层提供而不是本模块自己去碰 pending 台账，
    理由是那份台账有接管、退避、属地关卡，全都会话层的规矩，不该在补全模块里
    被第二次实现。没传就不做工商核验那一级，并把这件事如实报出来。

    ``shop_lookup`` / ``serp_lookup``：要不要开**现网反查**那一级（商铺子域 / 搜索引擎）。
    默认都关：这一级花的是真外网配额与限流额度，必须由界面显式勾选，不由本模块替用户决定。
    开着时也只在一行免费候选全空手时才问（:func:`escalator_for`），且只在
    工作线程里发生——预览那条路（:func:`workable_count`）一个包都不发。
    本级开了与否、问了家数、拿到候选家数，全部记在 ``stats["escalate"]`` 与网络级账目里。

    ``master_fetch``：披露主数据盘上还没有时，允不允许**为了拉全量而出网**（None ＝按
    exchange_master.AUTO_FETCH）。它是本级唯一一个会出网的动作，且一次性、分页、有预算
    与停止位。默认允许，因为不开就永远用不上；拉一次之后每 90 天才可能再拉，而拉失败
    后的 6 小时内不重试（exchange_master.RETRY_GAP_S，防的就是「每 60 秒重开一轮全量」）。
    离线用例靠注入假 fetcher 与显式传 False 把这一级当本地字典测。
    """
    log = log or (lambda m: None)
    recs = [r for r in (records or []) if isinstance(r, dict)]
    stats = {"total": len(recs), "carry": {}, "exchange": {}, "network": {},
             "registry": {}, "stamped": 0, "confident_skip": 0, "stale": 0,
             "verified": 0, "escalate": {"channels": []}, "changes": []}
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
        log(f"⚠ 层级标注没跑成（{type(e).__name__}）：按「{_fe.TIER_LABEL['source']}」兜底计，不改值")

    try:
        stats["carry"] = carry_pass(recs, log=log)
    except Exception as e:
        log(f"⚠ 瀑布第 {stage_no('carry')} 级没跑成（{type(e).__name__}）：跳过，后面的网络级照常")
        stats["carry"] = {"error": type(e).__name__}

    # 披露级必须在 pick_targets **之前**：命中的行带着 86 分进「命中即停」那道闸，而
    # 那道闸就是为省请求存在的。顺序反过来就是先烧完外网配额、再把已经补齐的行
    # 当成「本轮补到」记账（而本级本身一个请求都不花）。
    try:
        stats["exchange"] = exchange_pass(
            recs, log=log, allow_fetch=master_fetch,
            include_stale=bool(reverify), ttl_days=reverify_days, stop=stop)
    except Exception as e:
        log(f"⚠ 瀑布第 {stage_no('exchange')} 级没跑成（{type(e).__name__}）：跳过，后面的级照常")
        stats["exchange"] = {"error": type(e).__name__, "present": False}

    keep, skip, stale = pick_targets(recs, include_stale=bool(reverify),
                                     ttl_days=reverify_days)
    stats["confident_skip"] = skip
    stats["stale"] = stale
    stats["network"] = {"eligible": 0}
    # 现网反查通道按 STAGES/_CHANNELS 的同一份顺序拼：先 shop 后 serp（前者是平台登记的
    # 自有域名，证据更硬也不吃搜索引擎限流）。顺序写反不会错但会让人重新推一遍谁更可信。
    expensive = tuple(
        ch for ch, on in (("shop", shop_lookup), ("serp", serp_lookup)) if on)
    # 开没开、开了哪几路，先记进账目本身（而不是只存在局部变量里）：没开时本级也要
    # 能在 stage_lines 里说自己没动，否则「瀑布跑完了」会被读成「所有途径都用过了」。
    stats["escalate"]["channels"] = list(expensive)
    if keep:
        try:
            stats["network"] = _wc.enrich_records(
                keep, log=log, workers=workers, budget=budget, timeout=timeout,
                stop=stop, limit=limit, only_missing=only_missing,
                resolver=resolver_for(recs, log=log, site_of=disclosed_site_for),
                escalate=escalator_for(expensive, log=log))
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
    # 披露级自己说一段，而且**四种形状各说各的**：对上了并写了值 / 对上了但没新值 /
    # 一家都没对上 / 本级根本没参与。最后那一种必须把「为什么」说出来（没文件、
    # 拉取失败、重试闸拦下、口径没接上），否则下一级日志里那一行「名录 0 家」会被
    # 读成「全表没一家上市」——而它真正说的是今天一个包都没发。
    ex = stats.get("exchange") or {}
    if ex.get("error"):
        lines.append(f"· {stage_name('exchange')}：这一级出错（{ex['error']}），"
                     f"本轮一个披露值都没取，后面的级照常")
    elif ex.get("present"):
        bf = "、".join(f"{f} {n} 行" for f, n in sorted((ex.get("by_field") or {}).items()))
        head = (f"· {stage_name('exchange')}：名录 {ex.get('count', 0)} 家"
                f"（{ex.get('as_of') or '版本未知'}）→ 问过 {ex.get('asked', 0)} 行、"
                f"对上 {ex.get('matched', 0)} 行")
        if ex.get("filled"):
            head += (f"，{ex['filled']} 行零请求补齐（净新增 {ex.get('values', 0)} 个值"
                     + (f"：{bf}" if bf else "") + "）")
        elif ex.get("matched"):
            head += "，对上的行本来就有这些号（没写新值）"
        else:
            head += "，一家都没对上"
        lines.append(head)
        if ex.get("rejected"):
            eg = "；".join((x[:40] + "…" if len(x) > 40 else x)
                           for x in (ex.get("rejected_examples") or [])[:2])
            lines.append(f"· 防错配闸拦下：{ex['rejected']} 行（名录里有名字像的但不敢认，"
                         f"需人工认一下）" + (f"例：{eg}" if eg else ""))
        if ex.get("unmatched"):
            lines.append(f"· 名录里没有：{ex['unmatched']} 行（多为非上市/非挂牌主体，"
                         f"照旧走后面的级）")
        if ex.get("with_site"):
            lines.append(f"· 披露域名：{ex['with_site']} 行把它排在候选第一位"
                         f"（域名来自主体自报、值仍从页面抽取，层级记「{stage_name('site')}」）")
        if ex.get("partial"):
            lines.append("· 主数据只有半截（拉取到点或被停止），下一轮接着补")
    elif ex:
        lines.append(f"· {stage_name('exchange')}：本轮没参与"
                     f"（{ex.get('reason') or '没问过这一级'}）")
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
    # 现网反查那一级要自己说一句话，而且**三种形状各说各的**：没开、开了但一次都没问、
    # 真的问了几个。把它们都当「没产出」混在一起报，用户就无从判断这一级到底生效没有：
    # 而这一级恰好是最容易被误读成「勾了就等于多一层免费保险」的那一个。
    esc = stats.get("escalate") or {}
    if esc.get("channels"):
        asked = int(net.get("escalate_ask") or 0)
        if asked:
            lines.append(
                f"· 现网反查（{'/'.join(esc['channels'])}）：{asked} 家是免费候选全空手才去反查的，"
                f"其中拿到候选 {int(net.get('escalate_hit') or 0)} 家"
                + (f"（反查出错 {net['escalate_err']} 家，已按前面几级的结论收尾）"
                   if net.get("escalate_err") else ""))
        else:
            lines.append("· 现网反查：本轮开了，但没有一行走到「免费候选全空手」这一步，"
                         "一个额外请求都没花")
    elif stats.get("network") and net.get("eligible"):
        lines.append("· 现网反查：本轮没开（要多花外网配额才会开），"
                     "只按零成本候选的结论收尾")
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
