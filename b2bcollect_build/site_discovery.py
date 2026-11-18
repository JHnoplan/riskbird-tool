# -*- coding: utf-8 -*-
"""site_discovery.py —— 「按公司名/行内 URL 找官网」的补充通道（零网络推导 + 两条要节制的现网反查）。

本模块只对一件事负责：**产出候选网址**。它一个号码都不抽、一个值都不写——抽值、
证据打分、停放判定、归属核验、SSRF 闸全在 website_contact 那一处（同一条规则写两份
迟早各判各的，这是本仓库反复记过账的缺陷形态）。所以本模块产出的每个候选都必须
一路走到 `website_contact.enrich_site`，过不了归属核验就整站不采信。

【四条通道，按成本升序】
  ① 商铺子域反推（**零网络**）：八方/顺企网这类平台的租户域名经常把企业真实域名
     直接写在前缀里（`hapstar.cn.b2b168.com` → `hapstar.cn`、`yixingjd_com.11467.com`
     → `yixingjd.com`）。这是平台替企业登记过的自有域名，命中率高于任何推导；
     离线链路 v5 里它单独贡献 27% 命中，而这里比那边更省——连搜索请求都不用发，
     行内那个店铺 URL 就够了。
  ② 拼音候选（零网络生成 + DNS 预检过滤）：中文商号 → `yixing` / `hz+yixing` /
     全拼三族。离线链路实测兜底命中 52%，是「只有中文名、没有拉丁段」那一大批公司
     唯一接得上的路。读音来自 `pinyin_lite`（生成时钉死的 GB2312 全字表，零依赖）。
  ③ 八方搜索反查（一次 POST）：行内连店铺 URL 都没有的家，按商号去搜它自己的商铺页，
     再回到通道 ①。节流 + 退避 + 熔断见 `_slot`。
  ④ Bing SERP 反查（一次 GET）：按「品牌核心词 + 官网」查，结果标题过预筛闸。
     离线链路里它是主力（命中 32%）。

【为什么搜索这一层现在敢接进交付链（enrich_waterfall 文件头当年拒绝它的三条理由逐条对账）】
  · 「google 直连超时、duckduckgo 拒连」——本层只用 Bing，且**可达性由实测决定**：
    连续失败自动退避并熔断，一轮里问不出就整层停，不会拿一轮预算去撞死墙；
  · 「充斥内容站噪声」——两道闸：查询词用剥了城市前缀/行业尾缀的**品牌核心词**
    （完整公司名会被泛化到政务站与新闻站），结果再过 `ownership_in_title` 预筛
    + `NOISE_HOSTS` 域黑名单（政务/媒体/问答/文库/平台自身一律不要）；
  · 「归属核验对搜索快照做不动」——本层**从不拿快照当证据**。搜索只产出一个域名，
    值必须回到通道 ① 之后由真实官网页面抽出来并过归属核验；因此层级记 `search`
    （72 分，明显低于官网直采 82），而不是冒充直采。
被明确拒绝照搬的一条：离线链路的 `http_get` 在 SSL 失败时会 `verify=False` 再打一次。
本工具的纪律是 TLS 校验绝不关（见 website_contact.fetch_html 那段），移植时剔除。

运行：纯标准库 + website_contact（抓取出口）+ pinyin_lite（读音表），可离线全量验证
（所有网络动作都走 website_contact 的夹具）。
"""
from __future__ import annotations

import re
import threading
import time
from urllib.parse import quote

import pinyin_lite as _py
import website_contact as _wc
import field_evidence as _fe

__all__ = [
    "brand_core", "local_candidates", "shop_candidates", "serp_candidates",
    "discover_expensive", "ownership_in_title", "is_noise_host",
    "stats", "reset_state", "SHOP_TIER", "SERP_TIER", "PINYIN_TIER", "SUBDOMAIN_TIER",
]

# 层级归属：①②③ 都是「推出来/反查出来的域名」，采信与否仍由归属核验决定 → brand 档；
# ④ 的域名来自搜索引擎结果 → search 档。为什么 ③ 不给 search：它的证据形状与 ① 完全
# 一样（都是八方自己登记的租户子域），只是多花一次请求，而**层级记的是可信度不是成本**
# （成本另有 TIER_COST 那一栏，两者分开登记是 field_evidence 文件头写死的口径）。
SUBDOMAIN_TIER = "brand"
PINYIN_TIER = "brand"
SHOP_TIER = "brand"
SERP_TIER = "search"

# ── 商号核心：查询词与拼音都靠它 ────────────────────────────────
# 地名前缀：查询词里带着它，Bing 会把结果泛化到当地政务/新闻站（实测）。
CITY_LEAD_RE = re.compile(
    r'^(北京|上海|天津|重庆|广州|深圳|杭州|南京|成都|武汉|青岛|大连|宁波|厦门'
    r'|福州|苏州|沈阳|西安|济南|威海|烟台|常州|无锡|南通|泰州|徐州|盐城|连云港'
    r'|温州|台州|嘉兴|湖州|绍兴|金华|舟山|珠海|佛山|东莞|中山|汕头|湛江|茂名'
    r'|扬州|镇江|洛阳|淄博|潍坊|唐山|东营|乐清|晋江|常熟|太仓|马鞍山|芜湖|保定'
    r'|廊坊|唐山|沧州|济宁|临沂|德州|聊城|保定|湘潭|株洲|岳阳|佛山|惠州|江门'
    r'|肇庆|梅州|宁德|漳州|眉山|德阳|宜昌|襄阳|郴州|赣州|九江|宜宾|泸州|遵义'
    r'|江苏|浙江|广东|山东|福建|辽宁|河北|河南|湖北|湖南|安徽|四川|江西|陕西'
    r'|山西|吉林|黑龙江|广西|云南|贵州|甘肃|内蒙古|宁夏|新疆|青海|西藏)')
# 行业/组织尾缀：这些词进查询词只会把结果推向同行其他站，进域名候选则几乎必然撞车。
BRAND_STOP_RE = re.compile(
    r'(?:有限公司|股份有限公司|集团|股份|公司|厂|研究所|研究院|中心'
    r'|科技|技术|电子|信息|软件|数据|智能|智慧|网络|数字'
    r'|海洋|海事|船舶|航运|港口|物流|贸易|进出口|国际'
    r'|工程|装备|设备|机械|电气|机电|自动化|新能源|节能|环保'
    r'|材料|钢铁|金属|合金|电缆|线缆|电机|液压|阀门|泵业|轴承'
    r'|检测|认证|咨询|服务|管理|设计|开发|制造|配件|部件|装置|仪器|仪表)')
_ORG_TAIL_RE = re.compile(r'(股份有限公司|有限责任公司|有限公司|集团有限公司|集团|公司|厂)$')

# 噪声域：搜索结果里出现这些，一律不是企业自己的官网。
# 三类各留一份代表：政务/媒体（会把企业名当新闻标题写）、内容与问答（第三方写的企业
# 介绍页，页面上确有企业名和电话，但那是**别人登记的**）、企业与平台自身。
# 口径与 PLATFORM_HOSTS 一致：只按「等于该域或以 .域 结尾」判，不用裸子串。
NOISE_HOSTS = (
    # 搜索引擎与内容平台自身
    "bing.com", "baidu.com", "google.com", "sogou.com", "so.com", "sm.cn", "quark.cn",
    "yandex.com", "duckduckgo.com",
    # 问答/文库/百科/社媒：第三方写的页面，不是企业官网
    "zhihu.com", "douban.com", "tieba.baidu.com", "baijiahao.baidu.com", "xueqiu.com",
    "sohu.com", "163.com", "qq.com", "sina.com.cn", "sina.cn", "weibo.com", "360doc.com",
    "docin.com", "doc88.com", "taobao.com", "tmall.com", "jd.com", "pinduoduo.com",
    "linkedin.com", "facebook.com", "youtube.com", "twitter.com", "x.com",
    "qcc.com", "qixin.com", "bloomberg.com", "reuters.com",
    # 政务/媒体/展会聚合：企业名出现≠这页属于该企业
    "gov.cn", "people.com.cn", "xinhuanet.com", "chinadaily.com.cn", "cctv.com",
    "eastmoney.com", "secchina.com", "cninfo.com", "sse.com.cn", "szse.cn",
    "baike.com", "wikipedia.org", "mapbar.com", "amap.com", "bendibao.com",
)
# 后缀形态的噪声（比枚举整域稳：政务与教育域的子域成千上万）
NOISE_SUFFIX_RE = re.compile(r'\.(gov|edu|mil|int)(\.[a-z]{2})?$|\.gov\.cn$', re.I)

# 官网列名**不另列一份**：直接用 website_contact.WEBSITE_KEYS（那六列已经是
# 「公司网址/官网/企业网站/网站/店铺URL/详情URL」的并集，也是 pick_site 读的同一份）。
# 自己重列一遍的后果很具体：以后 WEBSITE_KEYS 加一列，这里就永远读不到那一列，
# 而报表上只会显示「这一家没有可用线索」。
#
# 平台租户前缀里可能出现的「域名尾巴」写法：hapstar.cn / hapstar_com / hapstar-com
_TAIL_MAP = {
    "cn": ".cn", "com": ".com", "net": ".net", "org": ".org",
    "comcn": ".com.cn", "netcn": ".net.cn", "orgcn": ".org.cn",
    "top": ".top", "vip": ".vip", "xyz": ".xyz", "cc": ".cc", "info": ".info",
    "comcn2": ".com.cn",
}
# 碎片商铺名：这些形状看着像域名，实际是平台自己发的编号（实测会反推出 l.com 这类）
_SHOP_FRAGMENT_RE = re.compile(r'^(?:[a-z]{1,3}\d{2,}|\d+|(?:www|m|wap|l|shop|vip)\d*)$')
_SUB_LABEL_RE = re.compile(r'^[a-z0-9][a-z0-9._\-]{1,58}[a-z0-9]$')


def _norm(name):
    return re.sub(r'[\s（）()\[\]【】·、,，.。\-—_]+', '', str(name or ""))


def brand_core(name):
    """剥城市前缀与行业/组织尾缀 → 品牌核心词（查询词与拼音候选都由它派生）。

    为什么剥两遍尾缀：「上海怡星机电设备有限公司」先剥组织尾得到「怡星机电设备」，
    再剥行业尾才是「怡星」——只剥一遍会让查询词带着行业词，结果页整屏都是同行。
    剥到 <2 字就**回退原名**而不是交一个单字：单字查询必然泛化（实测「星 官网」）。
    """
    s = _norm(name)
    m = CITY_LEAD_RE.match(s)
    if m:
        s = s[len(m.group(1)):]
    for _ in range(3):                     # 尾缀常常叠着写（「设备科技有限公司」）
        t = _ORG_TAIL_RE.sub('', s)
        t = BRAND_STOP_RE.sub('', t)
        if t == s:
            break
        s = t
    s = s.strip()
    return s if len(s) >= 2 else _norm(name)


# 商号拼域名的 TLD 顺序：`.cn` 排在 `.com` 前面是本轮回采账里改的一条判据——
# 2026-11-18 那轮「品牌域名推导」只出 28 家，而离线链路的实证形状是「hapstar.cn 才是
# 怡星机电的站，hapstar.com 与它无关」。中国中小企业的自有域名里 .cn/.com.cn 占比
# 明显高于全球平均，先试 .cn 少付一次「抓到别人站 → 归属不过 → 整行差点作废」的代价。
# 这条判据必须**同时**管住商号通道和拼音通道：探针发现拼音族还在按 `.com` 先出，
# 等于同一句判断只落了一半的代码——那是比没有这句判断更难查的状态。
BRAND_SUFFIXES = (".cn", ".com", ".com.cn")
# 英文登记名推出来的商号段：倒过来 `.com` 优先。这不是「判据只落了一半」，而是
# 同一个问题有两个不同的证据源：`.cn` 优先的实证来自「平台已登记的自有域名」
# （hapstar.cn）与中文商号拼音，两者都是**中文名来源**；而「SHENZHEN XX
# TECHNOLOGIES CO.,LTD」这种英文登记名基本就是外贸型企业，它们注的几乎一定是 .com。
# 一刀切会让最常见的形状先白烧一次 huawei.cn（瀑布套件里钉着的正是这一例）。
NAME_SUFFIXES = (".com", ".cn")
BRAND_SUFFIX_TRIES = 2                   # 未确证的商号段只试前两个 TLD（.com.cn 命中极低）
BRAND_MAX_TOKENS = 2                     # 商号段最多取两个（再多就是拿地名/行业词去撞）
PINYIN_MAX_CANDIDATES = 4
LOCAL_MAX_CANDIDATES = 4                 # 零网络通道一家最多交四个候选
ALT_MAX_PER_HEAD = 1                     # 确证域名只给一个回落位（同段只试最可能的另一 TLD）
EXPENSIVE_MAX_PER_ROW = 3                # 现网反查一家最多给三个候选（每个都要付一次抓页）

# 把 TLD 当后缀粘进商号段的写法：只可能来自平台标签的下划线/连字符形式
# （yixingjd_com → 分隔符被抹掉 → yixingjdcom），真实企业域名不这么登记。
# 词表**不在这里重列**：拆尾巴（brand_tokens）与拦粘连（_candidate_ok）读的是同一组字，
# 两份定义迟早分家，分家后就是一处放行另一处拦——而放行那处是要付现网请求的。
_TLD_TAIL_WORDS = _wc.TLD_TAIL_WORDS
# 编号形商号：纯数字、字母+长数字（x12345）、vip123 这类平台自发的店铺编号。
# 为什么这一条最重：`10086.cn` 是**中国移动**——它不是「白付一次请求」，而是
# 「抓得回来、页面上真的有电话、但那是运营商客服号」。归属核验有可能拦住它，
# 但把这种形状送进现网本身就不应该发生。
_JUNK_HEAD_RE = re.compile(r'^(?:\d+[a-z]{0,3}|[a-z]{1,3}\d{2,}|(?:vip|shop|mall)\d+)$')


def _head_of(host):
    """取域名商号段：`hapstar.cn` → `hapstar`。"""
    return str(host or "").lower().strip(".").split(".")[0]


def _with_suffixes(head, tries=None, suffixes=None):
    """商号段 → 按可信序拼出的候选域名（TLD 顺序只有两张表：中文名源 BRAND_SUFFIXES、
    英文名源 NAME_SUFFIXES，两处都在这里取，不允许调用方自带一份顺序）。"""
    sfx = tuple(suffixes or BRAND_SUFFIXES)
    return [head + s for s in sfx[:tries or len(sfx)]]


def _candidate_ok(host):
    """这个域名字符串值不值得付一次抓取？（平台/噪声/编号/拼接产物一律否）

    为什么必须是一道**共用**的闸而不是各通道各写各的：三条零网络通道产的都是同一种
    东西（一个待抓的域名），任何一条漏放，代价都是「真的发出一次请求并可能抓到别人的站」。
    探针实测到两种漏放：`10086.b2b168.com` → `10086.cn`（中国移动），
    `yixingjd_com.b2b168.com` → `yixingjdcom.cn`（不存在的域名形状）。
    """
    h = str(host or "").lower().strip(".")
    if not h or "." not in h:
        return False
    if not re.match(r'^[a-z0-9][a-z0-9.\-]{1,61}[a-z0-9]$', h):
        return False
    if is_noise_host(h):
        return False
    head = _head_of(h)
    if len(head) < 3 or _SHOP_FRAGMENT_RE.match(head) or _JUNK_HEAD_RE.match(head):
        return False
    for word in _TLD_TAIL_WORDS:
        # 只拦「剥完 TLD 词还剩 ≥ 3 个字符」的段：scom / ecn 这种本来就是完整字号，
        # 不能跟着一起误杀。
        if head.endswith(word) and len(head) - len(word) >= 3:
            return False
    return True


def _own_reg_domain(rec):
    """行内那个「已经不可用」的自有注册域（平台租户 URL 不算，那不是企业自己的域）。

    为什么要单独取它：发现层只在行内官网字段派不上用场时才被叫起来，所以候选里
    再出现同一个注册域就是原样重访一个刚失败过的站，白占一个名额（本层名额封顶）。
    """
    for col in _wc.WEBSITE_KEYS:
        url = _wc.norm_site((rec or {}).get(col))
        if not url:
            continue
        host = _wc.host_of(url)
        if not host or _wc.is_platform_host(host):
            continue
        return _wc._reg_domain(host)
    return ""


def _latin_from_name(rec, token):
    """这个拉丁段是从公司名（英文登记名）推出来的，还是从行内 URL 推出来的？

    TLD 顺序要按来源分档，所以必须问得出来。向同一个词表只喂名字再比一次，
    而不是在这里重写一遍“什么样的段像英文名”——自己写一份判定就会与词表分家。
    问不出来（词表降级、名字为空）按中文名源处理，那是保守的一侧。
    """
    name = str((rec or {}).get("公司名称") or (rec or {}).get("公司名") or "")
    if not name or not token:
        return False
    return token in _brand_tokens({"公司名称": name})


def _alt_tlds(doms):
    """已确证域名的**同段另一 TLD**：只在确证域名本身落空时才有意义。

    为什么还留着它：hapstar.cn 可能本轮正好不可达/停放，那 .com 是唯一还试得起的一步。
    为什么紧跟着确证域名而不是排在队尾：本层有候选预算封顶，一个永远排在额外出
    不去的候选等于没有这个候选；而同一段已被平台证实是他们的，它的另一个 TLD 比
    凭空猜的拼音族先验高得多。拓到别人家仍会被归属核验拦住。
    """
    out = []
    cap = ALT_MAX_PER_HEAD * max(1, len(doms or []))
    for dom in doms or []:
        taken = 0
        for alt in _with_suffixes(_head_of(dom)):
            # 先判后取：上一版把上限判在内层循环末尾，多个确证域名时总数会越界
            if taken >= ALT_MAX_PER_HEAD or len(out) >= cap:
                break
            if alt == dom or alt in out:
                continue
            out.append(alt)
            taken += 1
    return out


def ownership_in_title(brand, text):
    """**预筛**闸（宽进严出）：标题/上下文里至少要见到一个二字中文组或 ASCII 段。

    它只负责把「一眼就不是」的结果页丢掉省一次抓取，**不负责判定归属**——真正的
    判定是抓回页面后的 `website_contact.has_attr`（fail-closed）。两层各管一件事，
    合成一层就会出现「预筛用弱证据放过、终判又不认弱证据」的对不上账。
    """
    brand, text = str(brand or ""), str(text or "")
    if not brand or not text:
        return False
    low = text.lower()
    pairs = set(re.findall(r'[\u4e00-\u9fa5]{2}', brand))
    if any(p in text for p in pairs):
        return True
    ascii_core = re.sub(r'[^\x00-\x7f]', '', brand).strip()
    return len(ascii_core) >= 4 and ascii_core.lower() in low


def is_noise_host(host):
    """候选域是不是「一定不是企业官网」那一类（搜索引擎/政务/媒体/内容站）。"""
    h = (host or "").lower().strip(".")
    if not h:
        return True
    if _wc.is_platform_host(h):
        return True
    if any(h == d or h.endswith("." + d) for d in NOISE_HOSTS):
        return True
    return bool(NOISE_SUFFIX_RE.search(h))


# ── 通道 ①：商铺子域反推（零网络，行内 URL 就够）──────────────────
def _domain_from_labels(labels):
    """把租户前缀的标签串收成真实域名：('hapstar','cn')→hapstar.cn；('a_b_com')→a.com。

    只认**看得成域名**的形状，其余返回空串而不是硬拼一个 .com：把 `vip1234` 这种
    平台编号拼成 `vip1234.com` 去抓，撞上的必然是别人的站。
    """
    parts = [p for p in (labels or []) if p]
    if not parts:
        return ""
    head = parts[0]
    for sep in ("_", "-", "."):
        if sep in head:
            bits = [b for b in head.split(sep) if b]
            if len(bits) >= 2:
                tail = _TAIL_MAP.get("".join(bits[1:]))
                if tail and len(bits[0]) >= 3:
                    return bits[0] + tail
            head = bits[0] if bits else head
            break
    if len(parts) >= 2:
        tail = _TAIL_MAP.get(".".join(parts[1:]).replace(".", ""))
        if tail and len(parts[0]) >= 3:
            return parts[0] + tail
    return ""


def subdomain_candidates(rec):
    """从行内的店铺/详情 URL 反推企业真实官网域名（不花任何请求）。

    这是四条通道里性价比最高的一条：域名是企业在平台上**自己填的**，比任何推导都硬。
    只在 host 落在平台域名单里时才反推——自有域名 `www.hapstar.cn` 的前缀 `www`
    不是租户标签，照着切会切出 `hapstar.cn`（这一家是对的），也会把
    `img.a.com` 切成 `a.com`（那是 CDN，不是官网），所以边界必须钉在平台域上。
    """
    out = []
    for col in _wc.WEBSITE_KEYS:
        # norm_site 是仓库里已订过的那份地址校验：它会把「官网:xxx|地址:yyy」这种
        # 拼接串、带凭据的 host、非 web 端口、中文备注一律返回空。
        # 自己写 `if "://" in raw else "https://" + raw` 等于绕过这些护栏。
        url = _wc.norm_site((rec or {}).get(col))
        if not url:
            continue
        host = _wc.host_of(url)
        if not host:
            continue
        for plat in _wc.PLATFORM_HOSTS:
            if not host.endswith("." + plat):
                continue
            labels = host[: -(len(plat) + 1)].split(".")
            if not labels or labels == ["www"]:
                break
            dom = _domain_from_labels(labels)
            if dom and _candidate_ok(dom) and dom not in out:
                out.append(dom)
            # 只有「hapstar」这一段时（无尾巴可认），交给通道 ② 的商号候选去拼 TLD
            break
    return out


# ── 通道 ②：拼音候选（零网络生成）───────────────────────────────
def pinyin_hosts(name):
    """中文商号 → 候选域名列表（首字母族 + 城市前缀族 + 全拼族）。

    与离线链路的差别只有两处，都是刻意的：
      · 读音来自 `pinyin_lite` 而不是 pypinyin（零依赖 + 跨机一致，理由见生成脚本）；
      · 商号里只要有一个字读不出来（繁体/生僻扩展字），**整条候选放弃**而不是跳过那个字：
        跳过会把「上海A镠科技」拼成凭空存在的域名形状，那比没有候选更坏。
    """
    nm = _norm(name)
    if not nm:
        return []
    m = CITY_LEAD_RE.match(nm)
    city_abbr, rest = "", nm
    if m:
        cw = m.group(1)
        city_syl = _py.lazy_pinyin(cw)
        if all(_py.is_syllable(s) for s in city_syl):
            city_abbr = "".join(s[0] for s in city_syl)
        rest = nm[len(cw):]
    core = BRAND_STOP_RE.sub('', rest) or rest[:4]
    core = re.sub(r'[\d\s\-（）()]', '', core)
    if len(core) < 2:
        return []
    syl = _py.lazy_pinyin(core)
    if len(syl) != len(core) or not all(_py.is_syllable(s) for s in syl):
        return []                          # 有字读不出：不硬凑
    letters = "".join(s[0] for s in syl)
    full = "".join(syl)
    out = []
    # 三族的先后按**撞车概率**排，不按生成方便排：商号全拼（yixing.cn）是中文企业
    # 最常见的自有注册形状，两个字的首字母组合只有 676 种、早被占光，拿到必然是别人家。
    # 本层有候选封顶，族内顺序就是「哪几族能活到下游」的顺序（探针实测：旧序把
    # shyx.* 排在 yixing.* 前面，四个名额轮不到全拼族）。
    # TLD 档位数与商号通道共用 BRAND_SUFFIX_TRIES：两边不一致的话，拼音族会拿 .com.cn
    # 把候选位吃光，让确证域名的回落位（hapstar.com）根本排不进去。
    if len(full) <= 12:
        out += _with_suffixes(full, BRAND_SUFFIX_TRIES)                   # 怡星 → yixing.cn
    if city_abbr and 2 <= len(letters) <= 6:
        out += _with_suffixes(city_abbr + letters, BRAND_SUFFIX_TRIES)     # 杭州怡星 → hzyx.cn
    if 2 <= len(letters) <= 8:
        out += _with_suffixes(letters, BRAND_SUFFIX_TRIES)                # 怡星 → yx.cn（过不了形状闸）
    keep = []
    for dom in out:
        # 形状闸走共用函数：这一段以前自己写了三行（长度 + 碎片正则 + 噪声），
        # 结果就是编号形在商号通道漏放——共用一份才会一处修、三处生效。
        if _candidate_ok(dom) and dom not in keep:
            keep.append(dom)
    return keep[:PINYIN_MAX_CANDIDATES]


# ── 本地通道合流：一条请求都不发 ────────────────────────────────
def local_candidates(rec):
    """行内 URL 反推 + 拉丁商号拼域名 + 拼音候选 → [(url, 层级)]（零网络）。

    顺序就是可信序，`enrich_records` 按这个顺序试到抽到值为止：
    平台登记的自有域名（仅次于行内邮箱域，后者由 enrich_waterfall 排在更前）
    > 确证域名同段的另一 TLD > 其它拉丁商号拼域名 > 拼音。
    拼音排在最后是因为它最泛（两个字的拼音首字母只有 676 种组合，撞车率摆在那儿）；
    但它对「只有中文名」的那一大批公司是唯一接得上的路，不能省。

    为什么这里要**自己封顶**而不是把一长串交给下游截断：`enrich_records` 的
    `MAX_CANDIDATES_PER_ROW` 是「按顺序取前 N 个」，发现层要是把低可信候选堆在前面，
    高可信的那几个就会被砍掉——同一行里「哪一路该被砍」只有这里说得清。
    也正因为有封顶，可信序必须是**真**可信序：一个永远排在额外出不了的候选不是策略，
    是死代码（本轮初稿就把确证域名的回落位放在末尾，结果它永远轮不上）。
    """
    out, seen = [], set()

    def push(dom, tier):
        dom = str(dom or "").lower().strip(".")
        if not dom or dom in seen or not _candidate_ok(dom):
            return
        seen.add(dom)
        out.append((f"https://{dom}/", tier))

    evidenced = subdomain_candidates(rec)
    confirmed = {_head_of(d) for d in evidenced}
    for dom in evidenced:
        push(dom, SUBDOMAIN_TIER)
    # 同段另一 TLD 紧跟在确证域名后面：这一段已被平台证实是他们的，拼错了也是同一家
    # 的另一个站；而下面的拼音族是凭空猜，两个先验不是一个量级。
    for dom in _alt_tlds(evidenced):
        push(dom, "brand")
    name = str((rec or {}).get("公司名称") or (rec or {}).get("公司名") or "")
    own = _own_reg_domain(rec)
    for token in _brand_tokens(rec)[:BRAND_MAX_TOKENS]:
        # 平台登记值已经确证了这一段属于谁，那些段已在上面处理完，不再拿同段重复猜：
        # 离线链路的实证形状是「hapstar.cn 是怡星机电，hapstar.com 与它无关」。
        if token in confirmed:
            continue
        # URL 来源的段（mingliang-led）：它的本域就是行内那个刚失败的站，不能占位；
        # 登记名来源的段（huawei）：跟着 .com 走。两种来源的 TLD 顺序不共用一张表。
        suffixes = NAME_SUFFIXES if _latin_from_name(rec, token) else BRAND_SUFFIXES
        for dom in _with_suffixes(token, BRAND_SUFFIX_TRIES, suffixes):
            if dom == own:
                continue
            push(dom, "brand")
    if name:
        # 这里传**原名**而不是 brand_core(name)：pinyin_hosts 自己会剥城市前缀并留下
        # 城市缩写去拼 `hzyx.cn` 这一族。预先剥过就把这一族弄没了，而 v16 实测里
        # 拼音通道能到 52% 命中，这一族是重要组成。
        for dom in pinyin_hosts(name):
            push(dom, PINYIN_TIER)
    return out[:LOCAL_MAX_CANDIDATES]


# 词表降级原因：只在这里记一次，报表收尾时随通道账目一起打出来。
# 为什么不能吞掉这个异常：`brand_tokens` 的词表在 enrich_waterfall 里，而词表恰恰是
# 「这一行为什么没补上」唯一能追溯的东西。它一旦被改坏（重命名字段、抛异常），
# 静默返回空列表的表象是「品牌域名推导 0 家」，和「这轮确实一家都推不出来」完全同形，
# 用户和日志都看不出区别 —— 那是最难查的一类回归。
_DEGRADED = {}


def _brand_tokens(rec):
    """行内 URL 段与英文名段 → 可当商号用的拉丁段（地名段降到末尾）。

    这里**直接调 `enrich_waterfall.brand_tokens` 那一份**而不是另写一套：两边要的字段
    顺序与过滤强度本来同源，两份词表迟早分家，而词表恰恰是「这一行为什么没补上」
    唯一能追溯的东西。
    """
    try:
        import enrich_waterfall as _wf
    except Exception as exc:                  # 循环导入的部分加载阶段也可能走到这里
        _DEGRADED.setdefault("brand_tokens", f"导入失败：{exc}")
        return []
    fn = getattr(_wf, "brand_tokens", None)
    if fn is None:
        _DEGRADED.setdefault("brand_tokens", "enrich_waterfall.brand_tokens 不存在（被改名？）")
        return []
    try:
        return [t for t in (fn(rec) or []) if len(t) >= 4][:4]
    except Exception as exc:
        _DEGRADED.setdefault("brand_tokens", f"调用异常：{exc}")
        return []


# ── 通道 ③④：要节制的现网反查 ──────────────────────────────────
# 单飞令牌桶 + 退避 + 熔断（离线链路那条「分片短睡、限流退避」的同款结构）。
# 三个数各自的角色：
#   gap        两次请求的最小间隔；被限流 ×1.4 放大（上限 GAP_MAX），顺利则慢慢收回基线；
#   fails      连续没问到结果的次数；到 FAILS_TRIP 就把这一层整个挂起 dead_until 秒；
#   asked      本轮已经问了几家；到 round_cap 就不再问（把剩下的家数如实报出来，
#              而不是让用户以为「这一层跑过了，没有」）。
_CHANNELS = ("shop", "serp")
_BASE = {
    "shop": {"gap": 3.0, "round_cap": 400},
    "serp": {"gap": 2.0, "round_cap": 200},
}
GAP_MAX = 15.0
FAILS_TRIP = 3
DEAD_S = 120.0
# 一家最多收几个候选：每个候选都要付一次真实抓页，宁可下一轮再问也不能把一行撑爆。
SHOP_MAX_HOSTS = 2
SERP_MAX_HOSTS = 3
SERP_SCAN = 10        # 只看前 10 条：第 10 条以后的结果与这家无关的概率压倒性地高
# 结果页形状判据：只有先确定「这真的是个结果页」，「页面上没有候选」才能被当成结论。
# b_algo / b_results 是 Bing 结果块的固定容器名；JS 壳与接管页里这两个一个都不会出现。
SERP_RESULT_MARK_RE = re.compile(r'class="b_(?:algo|results)', re.I)
SERP_CHALLENGE_RE = re.compile(r'验证|输入字符|unusual traffic|are you a human|blocked', re.I)
_LOCK = threading.Lock()
_STATE = {}


def _new_state(ch):
    """一个通道的初始账本。**调用方必须已持锁**（或自己在 :func:`_st` 里拿锁）：
    `_LOCK` 不是可重入的，在已持锁的函数里调 `_st()` 会当场死锁（本项目已经在这种
    形状上碰过两次），所以这里只造对象、不碰锁。"""
    base = _BASE.get(ch) or {"gap": 3.0, "round_cap": 200}
    return {"gap": float(base["gap"]), "round_cap": int(base["round_cap"]),
            "last": 0.0, "fails": 0, "dead_until": 0.0,
            "asked": 0, "hit": 0, "limited": 0, "tripped": 0, "shell": 0,
            "skipped_cap": 0, "skipped_cold": 0, "err": 0}


def _st(ch):
    with _LOCK:
        return _STATE.setdefault(ch, _new_state(ch))


def reset_state():
    """清节流与计数（新任务开始/离线用例）。**必须**调它，否则上一轮的熔断会带进这一轮，
    表现为「昨天还出结果的搜索层今天一家都不问」，而日志里没有任何解释。"""
    with _LOCK:
        _STATE.clear()
    _DEGRADED.clear()


def _slot(ch):
    """取一个发送位。返回 False = 本轮这一家不该再问（配额用完/通道在冷却），
    不是「失败」，但**必须计入台账**：不然收尾日志里只看到「问了 N 家」，
    而这一轮本来有 M 家可问——通道被熔断这件事就只存在于读过代码的人的记忆里。"""
    st = _st(ch)
    while True:
        now = time.time()
        if now < st["dead_until"]:
            left = st["dead_until"] - now
            if left > 6.0:
                with _LOCK:                    # 熔断还没到点：这一家直接放弃，不睡在这
                    st["skipped_cold"] += 1
                return False
            time.sleep(left)
            continue
        wait = st["last"] + st["gap"] - now
        if wait > 0:
            if wait > 12.0:
                with _LOCK:
                    st["skipped_cold"] += 1
                return False
            time.sleep(wait)
            continue
        with _LOCK:
            if st["asked"] >= st["round_cap"]:
                st["skipped_cap"] += 1
                return False
            st["last"] = time.time()
            st["asked"] += 1
        return True


def _note_ok(ch):
    with _LOCK:
        st = _STATE.setdefault(ch, _new_state(ch))
        st["fails"] = 0
        base = (_BASE.get(ch) or {}).get("gap", 3.0)
        st["gap"] = max(float(base), float(st.get("gap", base)) * 0.92)


def _note_limited(ch, kind="limited"):
    """这一家没问到结果，分三型记账（kind）：

    ``limited`` 被限流/风控页（退避有效，等一等就好）；
    ``shell``   返回的不是结果页（体积够但整页没有一个结果结构，典型是 JS 壳/接管页）；
    ``err``     请求本身炸了（不可达/超时/DNS 死了）。

    为什么必须分开：这三类的正确处置并不相同（全归到限流会让一个已经死掉的通道
    每轮都被重试；全归到错误又会让「间歇风控」看起来像通道挂）。但**退避与熔断对
    三类都生效**：连续三回不管哪一型，这一层都该休一会儿，区别只留在台账里给人看。
    """
    with _LOCK:
        st = _STATE.setdefault(ch, _new_state(ch))
        key = kind if kind in ("limited", "shell", "err") else "limited"
        st[key] = int(st.get(key, 0)) + 1
        st["fails"] = int(st.get("fails", 0)) + 1
        st["gap"] = min(GAP_MAX, float(st.get("gap", 3.0)) * 1.4)
        if st["fails"] >= FAILS_TRIP:
            st["dead_until"] = time.time() + DEAD_S
            st["tripped"] = int(st.get("tripped", 0)) + 1
            st["fails"] = 0


def _noise_strip(host):
    """现网反查回来的候选域收口：返回可用的域名，不可用返回空串。

    规则一律从 `_candidate_ok` 来。这一层**只**负责返回约定（清空格/大小写），
    因为历史上这里已经写过一份“噪声 + 形状”判定，与零网络通道那份并行存在——
    两份规则一旦分家，就会出现「同一个域名本地通道拦了、搜索通道没拦」这种
    无法向用户解释的差别。
    """
    h = str(host or "").strip().lower().strip(".")
    if not h or " " in h or "@" in h or "://" in h:
        return ""
    return h if _candidate_ok(h) else ""


def _text_of(url):
    """取一页正文，返回 ``(文本, 失败原因)``（所有网络动作都走 website_contact 的夹具）。

    失败原因必须带回来：同一个「没拿到文本」，在「不可达」与「风控页」两种情形下
    的正确处置完全不同（前者该短歇复测，后者该退避），只返回空串就把这个信息丢掉了。
    """
    try:
        html, _final, why = _wc.fetch_html(url)
    except Exception as exc:
        return "", f"抓取异常:{type(exc).__name__}"
    html = html or ""
    return html, ("" if html else (why or "空响应"))


# 失败原因 → 记账分型。两个词表都故意宽一点：分错了顶多退避策略偏一偏，
# 但「把通道死了读成正常无结果」会让整层静默空跑而报表上看不出来。
_WHY_BLOCK_RE = re.compile(r'HTTP(?:401|403|406|407|429)|验证|拒绝|blocked|unusual', re.I)
_WHY_DOWN_RE = re.compile(r'不可达|超时|ConnectionError|connection|DNS|证书|'
                          r'TLS|SSL|HTTP5\d\d|域名不存在', re.I)


def _kind_of_why(why):
    """把 fetch 的失败原因归到 :func:`_note_limited` 的三型之一。"""
    s = str(why or "")
    if not s:
        return "shell"          # 200 且没报错却没正文：拿到的是一个空壳
    if _WHY_BLOCK_RE.search(s):
        return "limited"
    if _WHY_DOWN_RE.search(s):
        return "err"
    return "err"


# ── 通道 ③：八方搜索反查 ───────────────────────────────────────
SHOP_URL = "https://www.b2b168.com/Index.aspx"
SHOP_SUB_RE = re.compile(r'https?://([a-z0-9_.\-]+)\.b2b168\.com', re.I)


def shop_extract_sub(sub):
    """八方租户标签 → 真实域名：`hapstar.cn`→hapstar.cn；`yixingjd`→yixingjd.com。

    碎片过滤是这一条通道的全部风险所在：平台编号形如 `x12345`、`l`、`www3` 的租户
    拼成域名后必然指向别人手里早就注册好的站，抓到就是假匹配。
    """
    s = str(sub or "").lower().strip(".")
    if not s:
        return ""
    dom = _domain_from_labels(s.split("."))
    if dom:
        return dom
    core = s.split(".")[0]
    if len(core) < 4 or _SHOP_FRAGMENT_RE.match(core):
        return ""
    if core.startswith("vip") or core.endswith("vip"):
        return ""
    return core + ".com"


def shop_candidates(name, log=None):
    """八方站内搜索 → 租户子域 → 候选官网域名（一次 POST，带节流与熔断）。"""
    brand = brand_core(name) or _norm(name)[:4]
    if not brand:
        return []
    if not _slot("shop"):
        return []
    st = _st("shop")
    try:
        html, _final, why = _wc.fetch_html_post(SHOP_URL, {"q": brand, "act": "ComSearch", "page": 1})
    except Exception as exc:
        _note_limited("shop", _kind_of_why(f"抓取异常:{type(exc).__name__}"))
        return []
    text = html or ""
    if not text:
        _note_limited("shop", _kind_of_why(why))
        return []
    if len(text) < 3000 or "验证" in text[:400]:
        _note_limited("shop", "limited")
        return []
    found = list(SHOP_SUB_RE.finditer(text))
    if not found:
        # 整页一个 b2b168 租户子域都没有：这**不是**搜索结果页（改版/接管/空壳），
        # 不能记成「这一家在八方没有店」——后者是结论，前者是通道状况。
        _note_limited("shop", "shell")
        return []
    _note_ok("shop")
    out = []
    for m in found:
        cand = _noise_strip(shop_extract_sub(m.group(1)))
        if not cand or cand in out:
            continue
        ctx = text[max(0, m.start() - 200):m.end() + 200]
        if not ownership_in_title(brand, ctx):
            continue                        # 归属预筛：同屏文字里得见到这家的字样
        out.append(cand)
        if len(out) >= SHOP_MAX_HOSTS:
            break
    if out:
        with _LOCK:
            st["hit"] += 1
    return out



# ── 通道 ④：Bing SERP 反查 ────────────────────────────────────
SERP_URL = "https://www.bing.com/search?q=%s&setlang=zh-CN"
SERP_H2_RE = re.compile(r'<h2[^>]*>\s*<a[^>]+href="(https?://[^"]+)"[^>]*>(.*?)</a>', re.S)
SERP_MIN_BYTES = 4000                      # 比这个短就不是结果页（是拦截页/跳转壳）


def serp_candidates(name, log=None):
    """Bing 反查官网：只用品牌核心词，结果标题过预筛；返回候选域名（不抽值）。

    查询词为什么不能是完整公司名：带着「上海/有限公司」时 Bing 会把结果泛化到
    政务站与新闻站（实测），而品牌核心词 + 「官网」才是那一家自己的站。
    """
    brand = brand_core(name)
    if not brand:
        return []
    if not _slot("serp"):
        return []
    st = _st("serp")
    text, why = _text_of(SERP_URL % quote(f"{brand} 官网"))
    if not text:
        _note_limited("serp", _kind_of_why(why))
        return []
    if len(text) < SERP_MIN_BYTES or SERP_CHALLENGE_RE.search(text[:600]):
        _note_limited("serp", "limited")
        return []
    found = SERP_H2_RE.findall(text)
    if not found and not SERP_RESULT_MARK_RE.search(text):
        # 体积够、却没有一个结果结构：这是 JS 壳/接管页，不是「这一家搜不到」。
        # 本机实测过 Bing 会返回这种页：当成「正常无结果」记账，报表就会一直说
        # 「搜索层跑过了，没命中」，而真相是这一层从来没真正工作过。
        _note_limited("serp", "shell")
        return []
    _note_ok("serp")        # 能拿到结果页形状就算通道健康，哪怕这一家确实没结果
    out = []
    for url, title in found[:SERP_SCAN]:
        host = _noise_strip(_wc.host_of(url))
        if not host or host in out:
            continue
        if not ownership_in_title(brand, _wc.strip_tags(title)):
            continue
        out.append(host)
        if len(out) >= SERP_MAX_HOSTS:
            break
    if out:
        with _LOCK:
            st["hit"] += 1
    return out



def discover_expensive(rec, log=None, use=("shop", "serp")):
    """现网反查通道：返回 [(url, 层级), ...]，一家都不抽值。

    先 shop 后 serp：前者是「平台登记的自有域名」（证据更硬、也不吃搜索引擎的限流），
    后者只在前面没反出来时才问，这样同一家最多花一次 SERP。
    """
    name = str((rec or {}).get("公司名称") or (rec or {}).get("公司名") or "")
    if not name:
        return []
    out = []
    if "shop" in use:
        for host in shop_candidates(name, log=log):
            url = f"https://{host}/"
            if url not in [x[0] for x in out]:
                out.append((url, SHOP_TIER))
    if "serp" in use and len(out) < SERP_MAX_HOSTS:
        for host in serp_candidates(name, log=log):
            url = f"https://{host}/"
            if url not in [x[0] for x in out]:
                out.append((url, SERP_TIER))
    return out[:EXPENSIVE_MAX_PER_ROW]


def stats():
    """两条现网通道的账（报表「通道健康」与收尾日志共用这一份）。

    连 `_DEGRADED` 一起返回：降级是一次性的事，但它会让这一层整轮颗粒无收，
    所以它必须出现在同一份账目里，而不是散落在某个函数内部的局部变量。
    """
    with _LOCK:
        out = {ch: dict(_STATE.get(ch) or {}) for ch in _CHANNELS}
    out["degraded"] = dict(_DEGRADED)
    return out


def summary_lines():
    """把通道账目压成人话（没有这一句，「搜索层本轮没出东西」就会被读成「没有缺口」）。"""
    lines = []
    all_stats = stats()
    # 通道名里的层级词一律从注册表取，不手抄：抄一份的话，注册表改名后日志旧名、报表
    # 新名，同一个东西在两张表上叫两个名字（本仓那条扫描层级名的硬门就拦这个，
    # 而它确实量到了本函数的初稿）。
    for ch, label in (("shop", "八方商铺反查"),
                      ("serp", f"Bing {_fe.TIER_LABEL[SERP_TIER]}")):
        st = all_stats.get(ch) or {}
        if not st.get("asked"):
            continue
        tail = []
        if st.get("limited"):
            tail.append(f"被限流 {st['limited']} 次")
        if st.get("shell"):
            tail.append(f"返回的不是结果页 {st['shell']} 次")
        if st.get("err"):
            tail.append(f"访问异常 {st['err']} 次")
        if st.get("tripped"):
            tail.append(f"熔断 {st['tripped']} 回")
        if st.get("skipped_cold"):
            tail.append(f"熔断期间跳过 {st['skipped_cold']} 家")
        if st.get("skipped_cap"):
            tail.append(f"配额用完没问 {st['skipped_cap']} 家")
        lines.append(f"{label}：问 {st['asked']} 家 → 反出候选 {st['hit']} 家"
                     + ("、" + "、".join(tail) if tail else ""))
    for what, why in sorted((all_stats.get("degraded") or {}).items()):
        lines.append(f"⚠ 发现层降级：{what} —— {why}（本层候选已按空处理，补采结果会偏低）")
    return lines
