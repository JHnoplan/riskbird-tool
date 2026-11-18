#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""官网回采联系方式：数据里有官网的，就去它自己的官网把电话/邮箱补回来。

用户诉求原话：「如果数据有官网，同步去它的官网把电话和邮箱采集下来，如果数据本来
就有电话或邮箱，可以合并去重用英文逗号隔开」。本模块就是这句话的实现，但「实现得
正确」的部分全在下面的判据里，不在抓取动作里。

【为什么这是个判据问题而不是请求问题】
同一份官网补采在离线分析链路上（深海装备潜客/enrich_from_website.py +
enrich_cumulative.py）被实机打回过六次，每次都留下一条永久判据，本模块把它们全部
收在**一处实现**（同一条规则写两份迟早各判各的，这是那批脚本用注释反复强调的教训）：
  ① 证据分级：号码/邮箱出现在页面哪里是一等判据。tel:/mailto: 链接=3 分（页面作者
     明确标注这是联系方式），可见文本命中处 ±40 字有「电话/邮箱/Tel/Fax」等标签词=2 分，
     可见但无标签语境=1 分，**只存在于 <script>/<style> 里=0 分直接丢弃**。不做分级
     就会把 JS 里的日期数组/统计代码当电话回填（实测 05172743000、01041426525）。
  ② 负向语境：专利号/备案号/信用代码/邮编里的数字串不是电话（实测从
     「专利号：ZL201310628718.0」抽出两个假号，从「辽IPC备16461343416-2」抽出
     一个合法形态的假手机号），命中即降为 0 分。
  ③ 停放/被抢注/挂马劫持：域名易主后页面照样返回 200 并写着假电话邮箱（实测
     gzyhship.com 变成电竞俱乐部、sdri.com 变成待售页）。所以「200 可达 + 页面写了
     电话」不等于「这是这家公司的电话」，PARKED_RE 命中即整站不采信。
     先 unescape 实体再判：灰产标题常写成 &#39;&#x8bad; 形式，正则看不见原文。
  ④ 号码形态闸：区号族/位数/叠词/演示号/版权年，见 plausible_phone。
  ⑤ 排版盲区：一个号码被切成两个 <span>（区号后、本地号 4-4 分段处这两类真实分界）、
     区号后隔一个空格、4-3-3 写法的 400、带分机的总机只留主号——抽取器要同时扫 raw
     （属性里的 tel: 靠它）与剥标签后的纯文本，判据一点没放宽，只是看得全了。
  ⑥ 邮箱垃圾闸：example.com/test@/图片名/sentry/wixpress/纯数字 IP 域，以及
     「技术支持：某某网络」这类建站商署名邮箱。

【出口治理】补来的值只写进同名字段并按英文逗号去重合并（用户要的正是合并），
绝不覆盖成单一值；每次写入都在「官网回采」列留下可复核的账（几家/新增几个/为什么没出），
失败原因分类计数进日志——「回采了 0 家」必须能一句话说清为什么，和采集账目同一条纪律。

【安全边界】只走 http(s)；拒私网/回环/链路本地/元数据地址（.local、127.*、10.*、
169.254.169.254 等），重定向落地后复核一次——采集数据里的 URL 是**外部可控输入**，
直接交给 requests 等于让被采集方探测本机内网。单次请求有界超时、响应体封顶、不重试；
整体有预算上限与并发上限，到点就停并把剩余量说清楚。任何异常都不许冒泡影响采集主流程。
"""

from __future__ import annotations

import html as html_mod
import ipaddress
import json
import os
import re
import socket
import threading
import time
from collections import OrderedDict
from urllib.parse import urljoin, urlparse

# 命中层级/证据的单一真源（field_evidence 只依赖标准库，反向不 import 本模块，无环）。
# 为什么由本模块写证据而不是让上层回采器写完再补一列：值的可信度**只有在打分现场**
# 才说得清（第几页、有没有过归属核验、是直取还是推导），出了这一层就只剩一串号码，
# 上层再判一次就是第二份判据——那是本仓库记过账的缺陷形态。
import field_evidence as _fe

# 资源闸门（并发数由本机负载/内存与这一轮的实测超时率现算，不靠硬编码）。
# 只依赖标准库、反向不 import 本模块，无环；导入失败也不能拖垮回采——这层是治理，
# 治理模块自己出问题时正确做法是**退回固定并发**继续干活，而不是让采集整轮红。
try:
    import perf_governor as _gov
except Exception:                             # pragma: no cover - 打包漏收时的兜底
    _gov = None

# 共享闸门的名字：编排层（parallel_session.set_site_workers）改并发上限时要对齐的是
# **同一个**闸门，所以这个名字不能有第二份写法。两处各写一遍字面量时，改一处就变成
# 「界面调了档、闸门按另一个名字找不到」——设置静默不生效，日志还一句不提。
GATE_NAME = "site_enrich"


def io_workers_cap() -> int:
    """联网工作线程的硬上限（单一真源：治理层的 IO_WORKERS_MAX，它不可用时退回 16）。

    为什么函数放在这里而不是各自 `getattr` 一遍：界面上的滑块范围、编排层建几路线程、
    回采这一轮的 clamp，三处问的是同一个问题。写成三份数字时，用户把滑块拉到 16、
    回采把自己 clamp 到 8，差的那 8 路是**静默丢的**——界面上看不出来，日志里也不会有。
    """
    return int(getattr(_gov, "IO_WORKERS_MAX", 16)) if _gov is not None else 16


__all__ = [
    "norm_site", "is_platform_host", "is_public_host", "extract", "score_contacts",
    "plausible_phone", "plausible_email", "merge_values", "enrich_site",
    "enrich_records", "pick_site", "reset_caches", "WEBSITE_KEYS", "SITE_MARK",
    "RETRY_MARK", "MAX_ATTEMPTS", "needs_retry", "attempts_of", "is_transient_why",
    "GATE_NAME", "io_workers_cap", "preflight_hosts", "host_reachable", "dns_disk_flush",
    "DNS_DEAD_WHY", "fetch_html_post", "BROWSER_HEADERS", "BLOCK_CODES",
    "TLD_TAIL_WORDS", "PLATFORM_HOSTS",
]

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"}
# 被 WAF/CDN 按「请求头像不像浏览器」拦掉的站，缺的几乎从来不止 UA：Sec-Fetch-* 成套、
# 完整的 Accept、客户端提示（sec-ch-ua）都要有。只换 UA 往往照样 403，所以这里给
# **一整组**而不是一路试错——每一次试错都是每家都要付的一趟请求。
# 与 UA 保持同一 Chromium 大版本，头之间互相矛盾（UA 说 126、sec-ch-ua 说 125）本身
# 就是判黑特征，那种自相矛盾比缺头更可疑。
BROWSER_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"),
    "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,"
               "image/webp,image/apng,*/*;q=0.8"),
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
    "sec-ch-ua": '"Chromium";v="126", "Google Chrome";v="126", "Not.A/Brand";v="24"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
}
# 换浏览器头值得重试一次的状态码：都是「门开着、但不让你进」而不是「这家没这个页」。
BLOCK_CODES = (401, 403, 407)
TIMEOUT = 12                 # 单页**读数**硬超时：企业站慢，但慢到 12s 以上不如算失败
# 连接阶段单独给数（与 cdp_base.HTTP_CONNECT_TIMEOUT 同一口径，两份常量的等式由离线套件
# 对拍钉住——「同一件事两个数」正是这一族缺陷的来源）。为什么必须拆：企业官网整站连不上时
# 一个数当两个用，每次抓取都付满 12s 才回一句「不可达」，而每家站点的预算只有 45s——
# 连不上付 10s 就够了，慢页面该等的仍然等。
CONNECT_TIMEOUT = 10
MAX_BYTES = 1_200_000        # 响应体封顶：页面再大也用不到后面的内容，别让带宽拖死预算
SITE_MARK = "官网回采"       # 写进记录「官网回采」列的来源标记（可复核，不静默回填）

# 记录里可能存官网的键（各平台命名不统一，按优先级取第一个像官网的）
WEBSITE_KEYS = ("公司网址", "官网", "企业网站", "网站", "店铺URL", "详情URL")

# ── 平台域黑名单 ────────────────────────────────────────────────
# 「官网」字段经常是平台租户页（马可波罗的 xxx.jdzj.com、企知道的 qizhidao.com），
# 那上面的电话是平台客服号，不是厂家号。这类域一律不回采。
# 边界口径：只有 host 等于该域或以 ".域名" 结尾才算命中——用裸子串会把
# www.loco-china.cn（企业自有域名）这类一起误杀，这在离线链路上是实测踩过的坑。
PLATFORM_HOSTS = (
    "1688.com", "alibaba.com", "aliexpress.com", "made-in-china.com", "micc168.com",
    "qizhidao.com", "aiqicha.baidu.com", "qichacha.com", "tianyancha.com",
    "b2b168.com", "expobbs.com", "51sole.com", "coovee.com", "jdzj.com",
    "famens.com", "jinpaihuaxia.com", "dyjiazhi.com", "hi1718.com",
    "huangye88.com", "maigoo.com", "gongkong.com", "zbytb.com", "gkzhan.com",
    "b2b.hc360.com", "hc360.com", "yellowurl.cn", "11467.com", "qy.hc360.com",
    "sooxue.com", "eshop.com", "ecvv.com", "ec82.com", "tradekey.com",
    "globalsources.com", "dmread.com", "sunbuhuo.com", "nzyugong.com",
    "ydp.cncn.com", "cncn.com", "86gs.com.cn", "cnpp.cn", "zhaoshang114.com",
    "300.cn", "qianji.cn", "jinshu.cn", "smokexj.com", "linkforms.com",
)

# TLD 词表（含 `comcn` 这类三截粘连写法）：商铺标签普遍把企业自有域名登记成
# `yixingjd_com` / `hapstar-cn` 这种一段字符串，两处都要读它：
#   · `enrich_waterfall.brand_tokens` 据此把尾巴拆掉（yixingjd_com → yixingjd）；
#   · `site_discovery._candidate_ok` 据此拦下粘连产物（yixingjdcom.cn 不是域名形状）。
# 只许留一份定义：两份迟早分家，而分家的表象是「同一个候选在一处被拦、在另一处被放行」。
TLD_TAIL_WORDS = ("comcn", "netcn", "orgcn", "com", "cn", "net", "org",
                  "top", "xyz", "vip", "cc", "info", "shop", "mall")

# ── 号码/邮箱抽取（口径与离线链路一致，实测写法盲区都在这儿）────────
MOBILE_RE = re.compile(r'(?<!\d)(1[3-9]\d)[-\s]?(\d{4})[-\s]?(\d{4})(?!\d)')
# 分隔符允许 0~3 个字符：「010- 64229311」这种连字符后还空一格的排法必须能命中；
# 尾部 (?:[-转]\d{1,5})? 收分机，交校验时主号与整串都试，只留得下合法的那条。
LANDLINE_RE = re.compile(r'(?<!\d)(?:0\d{2,3}[-\s()—－–]{0,3}\d{7,8})(?:[-转]\d{1,5})?(?!\d)')
HOTLINE_RE = re.compile(r'\b([48]00)[-\s]?(\d{3})[-\s]?(\d{4})\b')
# 总机排成两段（0571 8812 3456 / 86-21-3612 9977）与 400 的 4-3-3 分段
LANDLINE_SP_RE = re.compile(r'(?<!\d)(?:\+?86[-\s]*)?(?:0?(\d{2,3}))[-\s](\d{3,4})\s(\d{3,4})(?!\d)')
HOTLINE_ALT_RE = re.compile(r'\b(400\d)[-\s]?(\d{3})[-\s]?(\d{3})(?!\d)')
EMAIL_RE = re.compile(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}')
HREF_RE = re.compile(r'(?:tel|mailto):([^"\'>\s]+)', re.I)
# 站内噪声黑名单（JS 数组/编号被误抽，人工复核定性后永久拉黑，防反复回填）
BLACKLIST_PHONES = {'069642069733', '09681746404', '06567954062', '02271999999'}
BAD_EMAIL_RE = re.compile(
    r'example\.com|test@|\.png$|\.jpg$|\.gif$|\.webp$|\.css$|\.js$|sentry|wixpress|@2x'
    r'|(?:^|@)(?:\d{1,3}\.){3}\d{1,3}|@\d+(?:\.\d+)+\.|dayuyun|alibaba\.com', re.I)
# 建站商署名：「技术支持：某某网络 400-xxx」不是厂家热线
WEB_AGENCY_RE = re.compile(r"(技术支持|网站建设|建站|designed\s*by|维护支持|推广支持|制作由|服务由)", re.I)
CONTACT_HREF_RE = re.compile(r'contact|lianxi|about|guanyu|footer|reach', re.I)
CONTACT_TEXT_RE = re.compile(r'联系我们|联系方式|关于我们|联系人|服务热线|客服中心')
# 猜路径与每站请求封顶：判不出主体/整页没号码的站，猜得越多越像「乱爬」，
# 而真实收益集中在前一两个命名上（contact / contactus 占绝大多数）。
# 分两档是必要的而不是「能多猜就多猜」：第一轮（GUESS_MAX=4）跑完仍然没出值的行，
# 才值得在**二轮深挖**里把整张命名表扫一遍——那些行是本轮唯一还有收益的对象，
# 而已经补到值的行再翻六页只会把预算从别人身上抢走（预算是整轮共享的）。
GUESS_MAX = 4
MAX_PAGES_PER_SITE = 6
CONTACT_LINKS_FIRST = 2            # 第一轮从首页链接里取几个联系/关于页
CONTACT_LINKS_DEEP = 5             # 二轮深挖取的个数（离线链路 v16 实测口径）
MAX_PAGES_PER_SITE_DEEP = 12       # 二轮单站封顶（含猜路径），与预算双闸一起管
GUESS_PATHS = ("contact.html", "contact_us.html", "contactus.html", "contact/",
               "contact-us.html", "lianxi.html", "lianxiwomen.html", "lxwm",
               "about.html", "gywm", "guanyu.html", "gsxq.html",
               # ↓ 以下只在二轮深挖里试（扩展名变体：老企业站 CMS 大量是 shtml/asp/php）
               "contact.shtml", "contact.asp", "contact.php", "contact/index.html",
               "gywm.html", "lxfw.html", "about/index.html", "guanyu/")
GUESS_PATHS_DEEP = len(GUESS_PATHS)   # 深挖 = 整张表扫完（不再只取前 GUESS_MAX 个）
# 单行最多试几个官网候选（行自带那个也算在内）。三个是权衡后的数：前两个候选
# 占了真实命中的绝大多数（邮箱域 + 主商号），第三个已是「拼音/另一个拉丁段」这一类
# 低命中候选；不设上限时，一行十几个拼音候选可以独占一分钟以上的预算，
# 而那张表后面还有几百家没轮到（预算是整轮共享的，不是按行计的）。
MAX_CANDIDATES_PER_ROW = 3
# 单行在多个候选之间共享的墙钟上限（秒）：一个候选最坏 6 页 × 12 秒，三个候选就是
# 三分钟。给行级封顶而不是指望单站 budget，因为慢站串联时单站封顶根本碰不到。
ROW_WALL_BUDGET_S = 60.0
# 追加候选（``escalate`` 那一级）在**免费候选之外**另给几个位。为什么不共用上面那个 3：
# MAX_CANDIDATES_PER_ROW 管的是「一堆推导候选里砍掉哪几个」——那是同一可信档内部的取舍，
# 而追加那一级排在免费候选**全部试完之后**，两者不在同一条序上，共用一个数就会互相挤位
# （实测形状：零网络候选本来就有 4 个，名额先被占满，追加那一级一个都轮不上＝定义了没接线）。
# 真正管住这级成本的是下面两条：单行墙钟照旧共享（ROW_WALL_BUDGET_S 到点就不试）、
# 以及「只有空手而归才问」这个前置条件本身。
ESCALATE_MAX_PER_ROW = 3

CONTACT_CTX_RE = re.compile(
    r'电话|联系方式|服务热线|手机|传真|邮箱|信箱|地址|客服|Tel|MOB|PHONE|FAX|E-?MAIL|Call|Contact', re.I)
NEG_CTX_RE = re.compile(
    r'专利|注册号|登记号|备案|著作权|信用代码|订单|编号|版本|版权|邮编|Postal|Copyright|Version|ICP'
    r'|I[CP]C\s*备|备(?!用)\s*[:：]?\s*\d{6,}|公网安备', re.I)
# 被抢注/停放/挂马劫持/灰产站特征：命中即判官网已失效，页面里的号码一律不采信
PARKED_RE = re.compile(
    r'此域名{0,6}(正在出售|出售|已暂停)|域名{0,4}(正在)?出售|正在出售|this domain (is )?(for sale|parked)'
    r'|domain for sale|coming soon|页面已暂停|站点已暂停|默认站点|apache2 ubuntu default page'
    r'|nginx 欢迎|万网已停|未备案'
    r'|the domain name|域名展示|域名交易|购买此域名|buy this domain|make an offer|域名已注册'
    r'|sedo\.com|afternic|dan\.com|godaddy\.com/park|parkingcrew|domainapps|hp\.cafe24'
    r'|该网站正在建设|site is under construction|placeholder page'
    r'|体育直播|足球直播|德甲|英超|nba.{0,4}直播|篮球直播|直播吧|开奖|博彩'
    r'|电子竞技|电竞(俱乐部|比赛|平台|官网)|投注|彩票|六合彩|棋牌|真人视讯'
    r'|开云(?=体育|官网|平台|登录|注册|入口|app|APP)|华体会|威尼斯人|新葡京|raybet|ob体育|爱游戏'
    r'|p[go]电子|首充|注册送', re.I)

_LOCK = threading.Lock()
# 同一域名在一张表里常出现多次（同一公司多条产品记录）：进程内缓存结果，
# 既省请求也保证「同一个站」在多行上给出完全一致的值。
#
# 为什么按**字节**封顶而不是按条数：一个条目存的是这个站抓到的整页 HTML
# （最多 MAX_PAGES_PER_SITE=6 页、单页封顶 MAX_BYTES=1.2MB），最坏一条就是 7MB。
# 旧口径「超过 800 条清空一次」在真实企业站上等于「最多可以攒 800×几 MB」——
# 一台 8G 的机器上，官网回采这一路就能自己把内存吃穿（本项目跑在这台机上实测
# 可用内存常年在 2~3G，还要留给 Chrome）。现在按预算淘汰最久没用的那些：
# 默认 96MB 是个**区间**：贴着最坏体量算 96 ÷ 7.2 ≈ 13 个站，按几百 KB 一页的
# 常见体量则是上百个站。命中率靠 LRU 保（同站多行连着处理），上限靠预算锁死。
#
# 为什么留 HTML 而不留「抽好的号码」：归属判定跟每一行的公司名有关（见 enrich_site
# 的注释），按域名预存判定结果会把 A 公司的电话盖到同站的 B 公司头上——那种错比
# 慢更贵。要再省内存只能省页面本身，不能省判定。
HOST_CACHE_BUDGET_BYTES = 96 * 1024 * 1024
_HOST_CACHE = OrderedDict()          # url -> (pages, dead_why)，按最近使用排序
_HOST_CACHE_BYTES = [0]              # 可变盒子：模块级计数，避免每次写入都 global 声明
# DNS 解析结论按域缓存：SSRF 闸每条记录查一次 DNS 会把整轮回采拖成 DNS 瓶颈。
# 值只有几个 IP 字符串，8000 条约 1~2MB，按条数封顶就够，不值得再套一层预算。
_DNS_CACHE = {}


def _entry_bytes(pages, dead_why):
    """一条缓存项占多少内存（估）。用 repr 长度量太慢，这里只加字符串/字节自身长度。"""
    n = len(dead_why or "")
    for p in (pages or []):
        # 页面条目形状见 enrich_site：(整页 HTML, 落地 URL, 页面标签)。
        # 估算只数 str/bytes 自身长度——真正的对象开销（str 表头、元组、字典）比这还大，
        # 所以这是一个**偏小**的账：预算封的是数量级，不是精确 RSS。
        for part in (p if isinstance(p, (tuple, list)) else (p,)):
            if isinstance(part, (str, bytes)):
                n += len(part)
    return n


def _cache_get(url):
    """读缓存并把它挪到队尾（LRU：刚用过的最不该被淘汰）。"""
    with _LOCK:
        hit = _HOST_CACHE.get(url)
        if hit is not None:
            _HOST_CACHE.move_to_end(url)
        return hit


def reset_caches():
    """离线用例/新任务开始前清进程内缓存（缓存跨任务留着，会让改过的官网字段
    在下一轮看起来「没生效」——那种「明明改了却看不出变化」最难查）。

    DNS 那份**内存镜像**一起清，但磁盘文件不动：清它的代价是下一轮预检重新解析
    上千个域，而这一句注释里说的「看不出变化」只发生在同一轮任务内（留痕列才是
    跨任务的排队依据，不是 DNS 缓存）。离线用例因此始终从「没查过」的起点开始，
    不会因为在开发机上跑过一次真网络就莫名其妙少补几家。"""
    global _DNS_DISK_LOADED
    with _LOCK:
        _HOST_CACHE.clear()
        _HOST_CACHE_BYTES[0] = 0        # 字节账必须跟着清：留着下次一进来就以为满了
        _DNS_CACHE.clear()
        _DNS_DISK.clear()
        _DNS_DISK_LOADED = False


# ── DNS 可达性：磁盘缓存 + 后台并发预检 ─────────────────────────
# 这一节解决的是一件事：**「这个域名到底存不存在」必须在发 HTTP 之前知道，但不许
# 在界面线程上按行串行问**（2026-11-18 外部机器实测点「补采官网联系方式」整窗转圈，
# 以及另一头「不可达(ConnectionError) 327 家」每家白付 10 秒连接超时——同一个事实
# 的两个方向的浪费）。
# 口径全部集中在这里，判据一次讲清：
#   · 结论只有三种：解析得到（live）/ 查无此名（dead）/ 没查过（unknown）。
#     「没查过」**永远不拦、不跳**——把不知道读成危险，离线机器上一次都不给补；
#   · 负结论（dead）只允许由 :func:`preflight_hosts` 写，而且要先过两道本机探针闸
#     （见 _dns_local_resolver_ok 与死名占比闸）：一个坏掉的递归 DNS 能把全网判成
#     不存在，那种负结论一写就是两天，比不缓存糟糕得多；
#   · 缓存跨任务持久，所以落点只许问 tool_config.state_file（口径见其文档），
#     绝不允许「和程序同目录」那种相对路径。
DNS_TTL_LIVE = 7 * 86400           # 解析得到的域：一周后重新问一次（站点会搬家）
DNS_TTL_DEAD = 2 * 86400           # 查无此名：两天后给一次复活机会（新注册域名）
DNS_CACHE_FILE_NAME = "site_dns_cache.json"
DNS_DISK_MAX_ENTRIES = 60000       # 990 行的表约 1~2 千个域，这里封的是几十个任务的量
DNS_PREFLIGHT_WORKERS = 10         # 并发解析路数（DNS 是纯等待，10 路在弱机上也压不出负载）
DNS_PREFLIGHT_HOST_S = 2.5         # 单域硬上限：getaddrinfo 不接受 timeout 参数，只能包线程
DNS_PREFLIGHT_BUDGET_S = 60.0      # 整轮预检封顶：到点就把没查的当「没查过」放行
# 换算依据：一轮 990 行里那 327 家「不可达」每家要付 10 秒连接超时（≈ 3270 秒），
# 而 1000 个域并发 10 路、单域 300 毫秒量级，十秒就能探完——花一分钟换五十分钟划算。
# 到了封顶还没轮到的域一律当「没查过」放行（fail-open），不拦。
# 对照域：判「本机解析器此刻到底通不通」。用多个是因为单站可能被本机 hosts/防火墙
# 特殊照顾，那一次误判会把整轮预检废掉（废掉的代价是白烧几千次连接超时）。
DNS_CONTROL_HOSTS = ("www.baidu.com", "www.qq.com", "www.taobao.com")
# 死名占比闸：一批里 9 成以上都「不存在」，几乎不可能是数据本身的质量问题，
# 而是这台机器的解析器在撒谎（企业内网 split-horizon、认证门户、被污染的 hosts）。
# 这时候一条负结论都不记——宁可慢，也不把好域名判死。
DNS_DEAD_RATIO_MAX = 0.9
DNS_DEAD_MIN_PROBED = 20           # 样本太小不下这个结论（10 家全是死域是可能的）
DNS_DEAD_WHY = "域名不存在（DNS 预检判死）"
# EAI_NONAME=8（名字没有解析出来）；EAI_NODATA 在 glibc 是 5、macOS/BSD 是 71，
# 而且新版 glibc 已经不再导出这个常量——所以按值集合判而不按 socket 上的名字判。
# 其它 gaierror（EAI_AGAIN=临时失败、EAI_FAIL…）一律算「不知道」，不记负结论。
_EAI_DEAD_ERRNO = frozenset({8, 5, 71})

_DNS_DISK = {}                     # host -> [live(bool), ts(float)]；内存镜像，也是持久化的内容
_DNS_DISK_LOADED = False
_DNS_DISK_DIRTY = [0]
_DNS_DISK_ERROR = [""]             # 第一次写失败的原因，只播一次（每轮都刷一行没意义）


def dns_cache_path() -> str:
    """DNS 缓存落点：只问 tool_config.state_file（与审计日志、列映射历史同一口径）。

    三条退路没有一条指向当前工作目录——理由与 audit_logger 那段完全一样：打包态的
    CWD 由「谁把进程拉起来」决定，写 ``./xxx.json`` 会在 Mac 只读副本下静默失败，
    而这一路的失败表现是「预检像没起作用，每次都重新解析」，谁也不会去查。
    """
    try:
        import tool_config as _tc
        try:
            return str(_tc.state_file(DNS_CACHE_FILE_NAME))
        except Exception:
            return str(_tc.user_state_dir() / DNS_CACHE_FILE_NAME)
    except Exception:
        return os.path.join(os.path.expanduser("~"), ".b2bcollect",
                            "b2bcollect_state", DNS_CACHE_FILE_NAME)


def _dns_disk_load():
    """读盘（只在预检开头调一次；文件缺失/损坏都按「没有缓存」继续，不报错）。

    刻意**不**在 :func:`is_public_host` 里惰性加载：那会让离线用例的结果取决于
    开发机上曾经跑过的那一次真网络。加载点唯一 = 预检入口，测试与生产同一条路。
    """
    global _DNS_DISK_LOADED
    if _DNS_DISK_LOADED:
        return
    _DNS_DISK_LOADED = True          # 先置位：读失败也不在每家身上重试一次磁盘 IO
    path = dns_cache_path()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except FileNotFoundError:
        return
    except Exception:
        return
    if not isinstance(raw, dict):
        return
    now = time.time()
    with _LOCK:
        for host, val in raw.items():
            if not isinstance(val, (list, tuple)) or len(val) < 2:
                continue
            try:
                live, ts = bool(val[0]), float(val[1])
            except (TypeError, ValueError):
                continue
            if now - ts > (DNS_TTL_LIVE if live else DNS_TTL_DEAD):
                continue             # 过期的条目不搬进内存，顺手在下次落盘时清掉
            h = str(host).strip().lower()
            if h and h not in _DNS_DISK:     # 内存里的结论优先（本轮刚查出来的更新）
                _DNS_DISK[h] = [live, ts]


def _dns_disk_note(host, live, when=None):
    """记一条可达性结论到内存镜像（落盘由 :func:`dns_disk_flush` 收口，别每家写一次盘）。"""
    h = str(host or "").strip().lower()
    if not h:
        return
    with _LOCK:
        old = _DNS_DISK.get(h)
        if old is not None and old[0] == bool(live) and (when or time.time()) - float(old[1]) < 3600:
            return                   # 同一个结论一小时内反复记：只会把脏标记和落盘次数堆起来
        _DNS_DISK[h] = [bool(live), float(when or time.time())]
        if len(_DNS_DISK) > DNS_DISK_MAX_ENTRIES:
            for k in sorted(_DNS_DISK, key=lambda x: _DNS_DISK[x][1])[: len(_DNS_DISK) // 4]:
                _DNS_DISK.pop(k, None)
        _DNS_DISK_DIRTY[0] += 1


def dns_disk_flush(force=False):
    """把内存镜像原子写盘（临时文件 + replace：写到一半掉电不会留下半个 JSON）。

    「没加载过就不写」是必需的一道：内存镜像此时只有本轮的几十条，直接覆盖会把
    历史攒下来的几万条一次性抹掉——那表现为「缓存越用越少」，最难归因。
    返回 True=已落盘/无需落盘，False=写失败（原因记在 :func:`dns_disk_error`，由预检播报）。
    """
    if not _DNS_DISK_LOADED:
        return False
    with _LOCK:
        if not _DNS_DISK_DIRTY[0] and not force:
            return True
        _DNS_DISK_DIRTY[0] = 0
        payload = {k: [v[0], v[1]] for k, v in _DNS_DISK.items()}
    path = dns_cache_path()
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)
        os.replace(tmp, path)
        return True
    except Exception as e:
        with _LOCK:
            if not _DNS_DISK_ERROR[0]:
                _DNS_DISK_ERROR[0] = f"{type(e).__name__}"
        try:
            os.remove(path + ".tmp")
        except OSError:
            pass
        return False


def dns_disk_error():
    with _LOCK:
        return _DNS_DISK_ERROR[0]


def host_reachable(host):
    """True=解析得到 / False=预检判它查无此名 / None=没查过。**只读，一次都不出网**。

    与 :func:`is_public_host` 是两件事，必须分开：那一个管**安全**（是不是内网地址，
    判不出时不拦），这一个管**值不值得花一次连接超时**。合成一个函数就会出现
    「今天没网 → 几千个域全被判不安全 → 整轮回采空转还报「已拦截」」那种最坏的形状。
    """
    h = str(host or "").strip().lower().rstrip(".")
    if not h:
        return None
    try:
        ipaddress.ip_address(h)
    except ValueError:
        pass
    else:
        return True                      # 直接写 IP 的行：没有「查无此名」这回事
    with _LOCK:
        got = _DNS_DISK.get(h)
    if not got:
        return None
    ttl = DNS_TTL_LIVE if got[0] else DNS_TTL_DEAD
    if time.time() - float(got[1]) > ttl:
        return None                      # 过期当「没查过」：由下一轮预检重新问
    return bool(got[0])


def _dns_lookup(host, limit):
    """单域解析（带硬上限线程包一层）。返回 (结论, 是否公网)；结论 None=不知道。

    为什么要自己开线程包：`socket.getaddrinfo` 没有 timeout 参数，超时由系统 resolver
    的配置决定（默认 2 次尝试 × 5 秒，碰上被防火墙 DROP 的 53 端口就是 10 秒以上）。
    预检是按上千个域跑的，不包这一层，整轮封顶就成了摆设。包起来的线程是 daemon：
    它只是停在一次系统调用里，不会在进程退出时拖住任何人（这也是这里不用
    ThreadPoolExecutor 的原因——它的 atexit 会 join 未完任务，等于把卡死搬到退出路上）。
    """
    box = {}

    def _run():
        try:
            box["infos"] = socket.getaddrinfo(host, None, socket.AF_INET)
        except socket.gaierror as e:
            box["errno"] = (e.args[0] if e.args else None)
        except Exception as e:            # UnicodeError（中文域）/ 内存类异常：都当不知道
            box["other"] = type(e).__name__

    t = threading.Thread(target=_run, name="dns-lookup", daemon=True)
    t.start()
    t.join(timeout=limit)
    if t.is_alive():
        return None, True
    if "infos" in box:
        infos = box.get("infos") or []
        if not infos:
            return None, True
        public = True
        try:
            public = not any(
                ipaddress.ip_address(x[4][0]).is_private
                or ipaddress.ip_address(x[4][0]).is_loopback
                or ipaddress.ip_address(x[4][0]).is_link_local
                for x in infos)
        except ValueError:
            public = True
        return True, public
    if box.get("errno") in _EAI_DEAD_ERRNO:
        return False, True                # 查无此名：可以记负结论
    return None, True                     # 临时失败/其它：不知道


def _resolver_alive():
    """本机解析器现在能不能用（对照域探针）。全都失败 → 这一轮一条负结论都不记。"""
    for host in DNS_CONTROL_HOSTS:
        live, _p = _dns_lookup(host, DNS_PREFLIGHT_HOST_S)
        if live:
            return True
    return False


def preflight_hosts(hosts, workers=DNS_PREFLIGHT_WORKERS, budget=DNS_PREFLIGHT_BUDGET_S,
                    stop=None, log=None, respect_persist=True):
    """并发把一批域名解析一遍，把结论写进同一份缓存（磁盘 + 内存），供后面所有环节复用。

    这是「谁判断可达性」的唯一出口：界面预检查**绝不**调它（那正是上次转圈的根因），
    它只在已经在后台线程里跑的回采开始时调一次。返回统计字典，不抛异常。

    三条闸（每一条都对应一种「缓存被写坏」的真实场景，缺一条就宁可不做这个缓存）：
      1) **对照域探针**：要写负结论、而这批里一个都没解析成功时，先问一次对照域；
         解不出来就判定本机解析器不可信，这一轮的负结论一条都不写（见下面的时序说明）；
      2) **死名占比闸**：一批里九成以上判死 → 判定为解析器不可信，负结论全部丢弃；
      3) **未知不拦**：超时/临时失败/没轮到的域一律算「没查过」，照旧去抓。

    探针的**时序**（2026-11-18 改的，原来在这里发过一轮真外网 DNS）：旧写法是开跑前先打
    三个对照域，不通就整轮跳过。听起来稳妥，实际有两处不对：① 这批域名自己就带着答案——
    只要有一个解析成功，就已经当场证明本机解析器此刻是通的，再问对照域纯属白问（现网每轮
    多付三次查询，离线用例还多查三个夹具外的真域名，把「本机 DNS 不可用」这句**假结论**
    印进日志，后面所有关于预检的行为都不是生产那条路了）；② 真正需要区分「这批数据里域名
    真的死得多」和「这台机器根本解不出来」的时刻，只有**准备写负结论**那一步。所以探针挪到
    了写盘前，条件是「有负结论要写 且 批内零成功」。代价说清楚：解析器完全卡死的机器上，
    这一轮会先把批内查询跑到封顶（整轮预算管着，默认 60 秒）才反应过来——但那种机器上后面
    每一家的抓取本来也每家都要付满超时，整轮已经是废的了，不值得为它保留一次开局白问。
    """
    log = log or (lambda m: None)
    want = []
    seen = set()
    for h in (hosts or []):
        k = str(h or "").strip().lower().rstrip(".")
        if k and k not in seen:
            seen.add(k)
            want.append(k)
    stats = {"asked": len(want), "cached": 0, "live": 0, "dead": 0,
             "unknown": 0, "skipped": "", "seconds": 0, "unprobed": 0}
    if not want:
        return stats
    if respect_persist:
        _dns_disk_load()
    # 筛待查项：host_reachable 自己拿 _LOCK（非可重入），这里**绝不能**先套一层锁。
    todo = [h for h in want if host_reachable(h) is None]
    stats["cached"] = len(want) - len(todo)
    if not todo:
        return stats
    t0 = time.time()
    lock = threading.Lock()
    idx = {"n": 0}
    verdicts = []                      # [(host, live|None, public)]
    n_workers = max(1, min(int(workers), 32))

    def work():
        while True:
            if stop and stop():
                return
            with lock:
                i = idx["n"]
                if i >= len(todo):
                    return
                idx["n"] += 1
                host = todo[i]
            live, public = _dns_lookup(host, DNS_PREFLIGHT_HOST_S)
            with lock:
                verdicts.append((host, live, public))

    ths = [threading.Thread(target=work, name=f"dns-preflight-{i}", daemon=True)
           for i in range(min(n_workers, len(todo)))]
    for t in ths:
        t.start()
    while any(t.is_alive() for t in ths):
        for t in ths:
            t.join(timeout=0.4)
        if time.time() - t0 > budget:
            break
        if stop and stop():
            break
    stats["seconds"] = int(time.time() - t0)
    stats["unprobed"] = max(0, len(todo) - len(verdicts))
    dead = [h for h, live, _p in verdicts if live is False]
    live_n = sum(1 for _h, live, _p in verdicts if live is True)
    unknown = sum(1 for _h, live, _p in verdicts if live is None)
    probed = len(dead) + live_n
    veto = ""
    # 闸 1（对照域）只在「批内一个都没解出来、又确实有负结论要写」时开火：批内有一个
    # 成功就已经证明本机解析器是通的，不必再问夹具外/外网的域名。
    if dead and not live_n and not _resolver_alive():
        stats["skipped"] = "本机 DNS 不可用"
        veto = (f"对照域（{'、'.join(DNS_CONTROL_HOSTS)}）一个都解不出来"
                f"，判定为本机此刻解析不了外网域名")
    # 闸 2（死名占比）：能走到这里说明对照域是通的（或批内本来就有成功），
    # 那 9 成以上判死就只能归因给解析器在乱答 NXDOMAIN。
    elif probed >= DNS_DEAD_MIN_PROBED and len(dead) / float(probed) > DNS_DEAD_RATIO_MAX:
        veto = (f"{len(dead)}/{probed} 个域都判成不存在，比例高得不像数据问题"
                f"（阈值 {int(DNS_DEAD_RATIO_MAX * 100)}%）")
    # 命中的那道闸只改变「负结论写不写」，不改变抓取行为：被丢弃的那些域在这里退回
    # 「没查过」，后面 enrich_site 照旧一家家去试，失败原因仍按实测写。丢弃的**家数**必须
    # 单独留住：下面两句话术都要报这个数，而 dead 在这里清零后报出来就是恒为 0 的假账。
    dropped = len(dead) if veto else 0
    if veto:
        dead = []                      # 全部丢弃：这一轮的负结论一条都不入盘
    # 结论入盘必须在锁外：_dns_disk_note 自己拿同一把 _LOCK（非可重入），
    # 在锁里调它是当场死锁，不是「稍慢」。占比闸命中时（veto 非空）负结论全部丢弃。
    for host, live, public in verdicts:
        if live is False:
            if not veto:
                _dns_disk_note(host, False)
        elif live is True:
            _dns_disk_note(host, True)
            if public is False:
                with _LOCK:
                    _DNS_CACHE[host] = False      # SSRF 闸的公网结论跟着更新
    stats.update({"live": live_n, "dead": len(dead), "unknown": unknown + dropped})
    if veto:
        log(f"🔌 域名预检：{veto} → 本轮不记任何「不存在」结论"
            f"（{dropped} 个域照旧去抓）")
    if live_n or len(dead) or unknown or dropped:
        # 「没答案」这一格报的是 unknown + dropped：被两道闸退回的那些域本来就是「本轮不主张
        # 结论」，不算进去这一行就凑不齐（在网 0、查无 0、没答案 0 却解了 3 个域＝对不上账）。
        log(f"🔌 域名预检：本轮并发解析 {len(verdicts)}/{len(todo)} 个域"
            f"（{n_workers} 路、{stats['seconds']} 秒）→ 在网 {live_n}、查无此名 {len(dead)}"
            f"、没答案 {unknown + dropped}" + (f"，还有 {stats['unprobed']} 个没轮到" if stats["unprobed"] else "")
            + (f"；缓存命中 {stats['cached']} 个（跨任务复用，一次都不问）" if stats["cached"] else ""))
    if respect_persist:
        dns_disk_flush()
        if dns_disk_error():
            log(f"⚠ 域名缓存没能写到本地（{dns_disk_error()}）：不影响本轮补采，"
                f"只是下次还得重新解析一遍")
    return stats


# ── URL 规整与安全闸 ───────────────────────────────────────────
def norm_site(raw):
    """把记录里的官网值规整成可请求的 http(s) 地址；不是官网形态就返回空。"""
    s = str(raw or "").strip().strip('"\'“”’')
    if not s or s in ("-", "无", "暂无"):
        return ""
    if "@" in s.split("/")[0]:          # user:pass@host 这种带凭据的绝不发请求
        return ""
    if not re.match(r'^https?://', s, re.I):
        if not re.match(r'^[\w.-]+\.[a-z]{2,}(/|$)', s, re.I):
            return ""                   # 不是「域名/地址」形态（中文备注、公司名等）
        s = "https://" + s
    p = urlparse(s)
    if not (p.hostname or ""):
        return ""
    try:
        port = p.port
    except ValueError:
        return ""
    if port not in (None, 80, 443):
        return ""                       # 非 web 端口不是官网，是扫描目标
    return s if p.scheme.lower() in ("http", "https") else ""


def host_of(url):
    try:
        return (urlparse(url).hostname or "").lower().rstrip(".")
    except ValueError:
        return ""


def _reg_domain(host):
    """粗粒度注册域：取最后两段（三段结尾是 com.cn/gov.cn 等的取三段）。"""
    parts = host.split(".")
    if len(parts) >= 3 and parts[-2] in (
            "com", "net", "org", "gov", "edu", "ac", "co"):
        return ".".join(parts[-3:])
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def is_platform_host(host):
    """官网字段其实指向 B2B 平台/企查站自己的页面 → 那不是企业官网，不回采。"""
    h = (host or "").lower()
    if not h:
        return True
    return any(h == d or h.endswith("." + d) for d in PLATFORM_HOSTS)


def is_public_host(host, allow_lookup=True):
    """SSRF 边界：外部可控的 URL 不能把请求带进内网/元数据地址。

    只在**能肯定**是私网/回环/链路本地/保留段时才拒；解析失败（NXDOMAIN、无网络）
    不等于危险——那样判会把「今天没网」说成「地址不安全」，也会让整轮回采空转。
    真正的不可达由 fetch 自己报。解析结果按域缓存，避免每条记录都发一次 DNS 查询。

    ``allow_lookup=False`` 是给**一次都不许出网的调用点**用的（界面点按钮前的预检查、
    候选域的本地筛选）。这里必须留一个口子：2026-11-18 外部机器实测「补采官网联系方
    式」点下去整窗转圈几十秒到几分钟，根因就是预检查在 GUI 线程里按行串行发 DNS
    （本机量到 12 行 30.5 秒、平均一次 301 毫秒，990 行就是几十分钟）。缓存里没有就
    返回 True（不拦）——「不知道」不是「危险」，把不知道读成危险，离线机器上一次都
    不给补；真值由后台的 :func:`preflight_hosts` 并发查一次后写进同一份缓存。
    """
    h = (host or "").lower().rstrip(".")
    if not h or h in ("localhost",) or h.endswith((".local", ".internal", ".lan", ".localdomain")):
        return False
    try:
        ip = ipaddress.ip_address(h)
    except ValueError:
        pass
    else:
        return not (ip.is_private or ip.is_loopback or ip.is_link_local
                    or ip.is_reserved or ip.is_multicast)
    with _LOCK:
        cached = _DNS_CACHE.get(h)
    if cached is not None:
        return cached
    if host_reachable(h) is False:
        # 预检已经知道「这个域名解析不到」：那它必然不可能是内网地址，不必再问一次 DNS。
        # 返回值与「判不出不拦」同为 True——可达性该拦的那一刀在 enrich_site 的
        # host_reachable 上（那里拦的是「别花这 10 秒」），不在这个安全闸里。
        return True
    if not allow_lookup:
        # 零网络口径：没查过就当「不拦」。返回值与「查过、判不出」同为 True，
        # 但**不许把它写进缓存**——写进去就把「没查过」固化成「查过且安全」，
        # 下一轮预检会跳过这个域，真拦不住的时候就是拿内网地址去补数据。
        return True
    verdict = True
    live = False
    try:
        infos = socket.getaddrinfo(h, None, socket.AF_INET)
        live = bool(infos)
        verdict = not any(
            ipaddress.ip_address(x[4][0]).is_private
            or ipaddress.ip_address(x[4][0]).is_loopback
            or ipaddress.ip_address(x[4][0]).is_link_local
            for x in infos)
    except (OSError, UnicodeError, ValueError):
        verdict = True                 # 判不出：不拦，让请求自己去失败
    with _LOCK:
        if len(_DNS_CACHE) > 8000:
            _DNS_CACHE.clear()
        _DNS_CACHE[h] = verdict
    # 只顺手记「这个域确实解析得到」这一类肯定结论。「查不到」**不从这里入盘**：
    # 这一路分不清「这个域名不存在」与「本机此刻没网/递归服务器抖了」，把后者
    # 写成负结论会毒化缓存两天，下次联网跑整轮预检会把好域名一家家当不存在拦掉。
    # 负结论只能由 preflight_hosts 出（它先打一个对照域，本机 DNS 不通就一条不记）。
    if live:
        _dns_disk_note(h, True)
    return verdict


# ── 形态校验 ───────────────────────────────────────────────────
def plausible_landline(p):
    """固话形态校验：区号族 + 位数双重规则，剔掉页面日期/编号被误抽的假号。"""
    d = re.sub(r'\D', '', str(p or ""))
    if d.startswith('0086'):
        d = '0' + d[4:]
    if not d.startswith('0') or len(d) not in (10, 11, 12):
        return False
    if d[1] == '0' or '0000' in d:
        return False                      # 00xx 不是区号
    if d[1] == '9' and d[2] not in '134579':
        return False                      # 09xx 只放行西北五省在用的段
    if d[1:3] == '10' or d[1] == '2':
        ac = 3
    elif d[1] == '3' and d[2] not in '13579':
        return False                      # 03XX 次位只存 1/3/5/7/9
    elif d[1] in '3456789':
        if d[2] == '0':
            return False
        ac = 4
    else:
        return False
    local = d[ac:]
    if len(local) == 8 and local[:4] == local[4:]:
        return False                      # 32563256 类页面数字叠词
    if not (7 <= len(d) - ac <= 8):
        return False
    if len(d) == 10 and not re.search(r'[-(（]', p):
        return False                      # 区号+7 位已基本退役，无分隔一律拒
    return True


def plausible_phone(p):
    digits = re.sub(r'\D', '', str(p or ""))
    if len(set(digits)) < 4:
        return False                      # 111.../0000... 演示号
    if digits in ('12345678901', '13800138000', '10086', '10010', '4000000000'):
        return False
    if re.match(r'^(1[3-9])\1{3}', digits):
        return False                      # 13813813813 类连排
    if digits.startswith(('0123', '0202', '075500')):
        return False
    if digits in (str(time.localtime().tm_year), str(time.localtime().tm_year - 1)):
        return False                      # 版权年
    if digits[0] == '1' and len(digits) == 11:
        return bool(re.match(r'1[3-9]\d{9}$', digits))
    if digits[0] == '4' or (digits[0] == '8' and len(digits) == 10):
        return True                       # 400/800
    if digits[0] == '0':
        return plausible_landline(p)
    return len(digits) >= 7               # 境外号保留（本工具不做国内形态强校验）


def plausible_email(e):
    e = str(e or "")
    if BAD_EMAIL_RE.search(e):
        return False
    local, _, dom = e.partition('@')
    if len(dom) < 4 or '.' not in dom:
        return False
    if re.match(r'^(image|img|icon|logo|static|noreply|no-?reply)', local, re.I):
        return False
    return True


def strip_tags(html_text):
    """「人眼可见文本」的唯一实现：先解实体再剥标签与脚本样式。"""
    t = re.sub(r'(?is)<(script|style|noscript)\b.*?</\1>', ' ',
               html_mod.unescape(html_text or ''))
    return re.sub(r'\s+', ' ', re.sub(r'(?s)<[^>]+>', ' ', t))


# ── 抽取 ──────────────────────────────────────────────────────
def _extract_one(text):
    phones, emails = [], []
    for m in HOTLINE_RE.finditer(text):
        p = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
        if plausible_phone(p) and re.sub(r'\D', '', p) not in BLACKLIST_PHONES:
            phones.append(p)
    for m in MOBILE_RE.finditer(text):
        p = m.group(1) + m.group(2) + m.group(3)
        if plausible_phone(p) and p not in BLACKLIST_PHONES:
            phones.append(p)
    for m in LANDLINE_RE.finditer(text):
        p = m.group(0)
        tail = re.search(r'(?:[-转])\d{1,5}$', p)
        base = p[:tail.start()] if tail else p
        cand = base if tail and plausible_phone(base) else p
        if plausible_phone(cand) and re.sub(r'\D', '', cand) not in BLACKLIST_PHONES:
            phones.append(cand)
    for m in LANDLINE_SP_RE.finditer(text):
        p = f"0{m.group(1)}-{m.group(2)}{m.group(3)}"
        if plausible_phone(p) and re.sub(r'\D', '', p) not in BLACKLIST_PHONES:
            phones.append(p)
    for m in HOTLINE_ALT_RE.finditer(text):
        p = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
        if plausible_phone(p) and re.sub(r'\D', '', p) not in BLACKLIST_PHONES:
            phones.append(p)
    for m in EMAIL_RE.finditer(text):
        e = m.group(0).lower().rstrip('.')
        if plausible_email(e):
            emails.append(e)
    seen, out = set(), []
    for x in phones:                       # 同一号码的两种排版只留第一次出现
        k = re.sub(r'\D', '', x)
        if k not in seen:
            seen.add(k)
            out.append(x)
    phones = out
    emails = list(dict.fromkeys(emails))
    return phones, emails


def extract(text):
    """同时扫 raw（tel:/mailto: 只在属性里）与剥标签纯文本（被标签切断的号码）。"""
    phones, emails = [], []
    plain = strip_tags(text)
    for t in (text, plain):
        if not t:
            continue
        p, e = _extract_one(t)
        phones += p
        emails += e
    seen, out = set(), []
    for x in phones:
        k = re.sub(r'\D', '', x)
        if k not in seen:
            seen.add(k)
            out.append(x)
    return out, list(dict.fromkeys(emails))


# ── 证据打分 ──────────────────────────────────────────────────
def score_contacts(html, phones, emails):
    """返回 ([(值, 分数)], [(值, 分数)])，0 分（看不见/只在脚本里）不返回。"""
    html = html or ''
    href_raw = " ".join(HREF_RE.findall(html))
    href_digits = re.sub(r'\D', '', href_raw)
    href_mails = {m.lower().strip('/') for m in HREF_RE.findall(html)}
    vis = strip_tags(html)

    def context_ok(pos, width):
        return bool(CONTACT_CTX_RE.search(vis[max(0, pos - 40):pos + width + 40]))

    def pat_of(digits):
        return re.compile(r'(?<!\d)' + r'\D{0,2}'.join(map(re.escape, digits)) + r'(?!\d)')

    def grade(value, pattern, is_phone):
        if is_phone:
            d = re.sub(r'\D', '', value)
            if len(d) >= 7 and d in href_digits:
                return 3
        elif value.lower() in href_mails:
            return 3
        hits = [m.start() for m in pattern.finditer(vis)] if pattern else []
        if not hits:
            return 0
        # 建站商/第三方署名：命中处前 24 字里出现「技术支持/网站建设/designed by」
        # 就不是这家企业自己的联系方式（实测「技术支持：某某网络 400-888-1234」——
        # 有标签语境能给到 2 分，不认署名这一条就会把建站公司的热线当厂家热线交付）
        if any(WEB_AGENCY_RE.search(vis[max(0, h - 24):h + len(value) + 8]) for h in hits):
            return 0
        if any(context_ok(h, len(value)) for h in hits):
            return 2
        if any(NEG_CTX_RE.search(vis[max(0, h - 20):h + len(value) + 12]) for h in hits):
            return 0
        return 1

    out_p = []
    for p in phones:
        d = re.sub(r'\D', '', p)
        sc = grade(p, pat_of(d), True) if d else 0
        if not sc and d.startswith('0'):
            # 抽取器会把「86-21-3612 9977」补成 021-…，页面原文没有那个长途字冠，
            # 按补全后的串找证据必然找不到，去掉前导 0 再找一次（判据没放宽，只是别找错串）
            alt = pat_of(d[1:])
            if d[1:] and alt and any(alt.finditer(vis)):
                sc = 1
        if sc:
            out_p.append((p, sc))
    out_e = [(e, grade(e, re.compile(re.escape(e), re.I), False)) for e in emails]
    out_e = [(e, s) for e, s in out_e if s]
    return out_p, out_e


# ── 归属弱证据（判定「这页还是不是这家公司」）────────────────────
def brand_grams(name):
    """公司名里可当品牌段的二字组：去掉区划与组织形式，剩下的成对取字。"""
    s = re.sub(r'\s+', '', str(name or ''))
    s = re.sub(r'[\(\)（）\[\]【】]', '', s)
    s = re.sub(r'(有限公司|股份有限公司|有限责任公司|集团|厂|中心|公司|企业|'
               r'(北京|上海|天津|重庆|广东|广州|深圳|东莞|佛山|江苏|苏州|浙江|杭州|'
               r'山东|青岛|河北|河南|湖北|武汉|湖南|四川|成都|陕西|福建|厦门|安徽|'
               r'江西|辽宁|大连|吉林|黑龙江|内蒙古|广西|海南|贵州|云南|西藏|甘肃|'
               r'青海|宁夏|新疆|中国|全国)省?市?县?区?)', '', s)
    s = re.sub(r'[^0-9A-Za-z\u4e00-\u9fa5]', '', s)
    return {s[i:i + 2] for i in range(len(s) - 1)} if len(s) >= 2 else set()


def has_attr(name, vis, host):
    """归属证据：正文含公司名前 2 字 / 域名品牌段见于正文 / 两者都无。"""
    grams = brand_grams(name)
    if not grams:
        return True                       # 名字规整后没剩可判的段，不强卡（fail-open）
    if any(g in vis for g in grams):
        return True
    brand = _reg_domain(host).split('.')[0]
    return bool(brand) and brand.lower() in vis.lower()


# ── 抓取 ──────────────────────────────────────────────────────
_THREAD_LOCAL = threading.local()


def _http_session():
    """本线程复用的 requests.Session（连接池按线程各一份，不跨线程共享）。

    为什么必须是 Session 而不是 `requests.get`：`requests.get` 内部是
    `with sessions.Session() as session: return session.request(...)`——每次调用**现建
    一个 Session 再丢掉**，连接池跟着一起没了，于是每一个页面都要重新 TCP + TLS 握手。
    一家官网最多抓 MAX_PAGES_PER_SITE=6 页，还要跟着重定向走，同站握手就要重复六到
    十几次。复用之后同站第 2~6 页直接拿现成连接。

    实测（本机 8 核，`bash debug/run_perf_probe.sh` 的 F 档，本地 HTTPS 夹具、
    在服务器侧数出来的连接数）：同一主机连打 24 发，每次现建 Session 是
    **16.9 毫秒/发、真开 24 条连接**；线程内复用是 **0.96 毫秒/发、25 发只开 1 条连接**，
    快 17.6 倍。换算到业务单位：一家 6 页的官网从约 101 毫秒降到约 6 毫秒。
    这两个数是**下界**——夹具跑在 127.0.0.1 上，握手只剩 TLS 的 CPU 成本，真公网每次
    还要另付 1 个 TCP RTT + 1~2 个 TLS RTT；也就是说真实回采省下的只会更多。

    为什么按线程各一份：`requests.Session` 不是线程安全的（cookie jar / 连接池状态
    会被并发改写）。回采是多线程的，共享一份等于把并发问题请回来；而 urllib3 的
    连接池本来就在线程内复用连接，按线程切一份正好——既保住复用，也不跨线程串状态。
    """
    sess = getattr(_THREAD_LOCAL, "session", None)
    if sess is not None:
        return sess
    import requests
    from requests.adapters import HTTPAdapter
    sess = requests.Session()
    sess.headers.update(HEADERS)
    try:
        # 池子开 8 条：单线程同一时刻只有一张页在手，8 条是给重定向与相邻站留的余量；
        # max_retries=0 是刻意的——重试交给上层「待重试」台账，别在传输层偷偷再打三次，
        # 那会把预算耗尽变成「谁也说不清这一家到底请求了几次」。
        adapter = HTTPAdapter(pool_connections=4, pool_maxsize=8, max_retries=0)
        sess.mount("https://", adapter)
        sess.mount("http://", adapter)
    except Exception:
        pass                                   # 装不上就用默认适配器，不影响正确性
    _THREAD_LOCAL.session = sess
    return sess


def close_thread_session():
    """线程收工时把这一路的连接池关掉（工作线程退出前调一次）。

    不关的后果不是泄漏到进程外，而是 Keep-Alive 连接一直占着对方服务的会话数——
    企业站前面常有「一个 IP 最多 N 条并发连接」的限制，攒着不放会把后面的请求
    顶成 503/超时，那种超时还会被误记成「这家官网不行」。
    """
    sess = getattr(_THREAD_LOCAL, "session", None)
    if sess is not None:
        _THREAD_LOCAL.session = None
        try:
            sess.close()
        except Exception:
            pass


def fetch_html(url, timeout=TIMEOUT):
    """有界抓取：返回 (html, final_url, 失败原因)。

    TLS 校验**一律不关**。企业站证书链不全确实常见，但「关闭主机名/证书校验」等于
    把补回来的号码交给中间人说了算——本工具是把值直接写进交付表格、后面没有人工复核
    环节，所以宁可少几家也要守住这条：证书失败就如实计入「证书校验失败」这个原因，
    让操作者看得见数量，而不是悄悄采信一条可能被篡改的号码。

    两类**各只一次**的自救（都封在「最多两次请求」，不引入重试风暴）：
      ① HTTP401/403/407 → 换一组完整的浏览器头重打同一 URL。这是 2026-11-18
         那轮 204 家 HTTP403 的直接处置：WAF 按请求头判黑与「这家没有联系方式」
         是两件完全不同的事，不分开就会把一整批正常企业永久当成死站；
      ② 「证书校验失败」→ 回落 http 同路径一次。只限这一种失败：它意味着 TCP 与
         TLS 握手都到了对方服务器、只是证书不让人接受（企业站链不全的头号形状），
         同一个内容走 80 端口极可能是好的。
         **ConnectionError/超时不回落**——443 连不上时 80 大概率也连不上，回落只是
         把一次 10 秒的白付变成两次。明文那一跳确实放弃了传输层防篡改，
         所以它仍然要过同一套停放判定 + 归属核验（_grade_pages），并且行内本来
         就允许「数据里写的就是 http 官网」这种形态——口径与今天一致，不是新开的口子。
    """
    html, final, why = _fetch_once(url, timeout=timeout)
    if html is not None:
        return html, final, ""
    code = _code_of_why(why)
    if code in BLOCK_CODES:
        html2, final2, why2 = _fetch_once(url, timeout=timeout, headers=BROWSER_HEADERS)
        if html2 is not None:
            return html2, final2, ""
        return None, final2 or final, why2 or why        # 换头也没开：报**最后**那个原因
    if str(why or "").startswith("证书校验失败") and str(url).lower().startswith("https://"):
        html3, final3, why3 = _fetch_once("http://" + url[8:], timeout=timeout)
        if html3 is not None:
            return html3, final3, ""
        return None, final3 or final, "证书校验失败"      # 回落也没能：仍按 TLS 原因计入（可重试）
    return None, final, why


def _code_of_why(why):
    """从「HTTP403」这类原因串里取状态码；不是 HTTP 类失败返回 0。"""
    m = re.match(r'^HTTP(\d{3})$', str(why or "").strip())
    return int(m.group(1)) if m else 0


def fetch_html_post(url, data, timeout=TIMEOUT):
    """POST 版拓页：与 :func:`fetch_html` 共用全部安全闸与封顶（只是不发浏览器头重试，
    重试一条 POST 的副作用不可控，而搜索接口重试也不换回结果）。

    为什么需要它而不是在发现层自己 `requests.post`：那样就有两份抓取实现，
    SSRF 闸、响应体封顶、超时拆分、TLS 不降级这些判据只会在一份里演进——
    本仓库记过账的那类「同一规则写两份、迟早各判各的」缺陷就是这么来的。
    """
    return _fetch_once(url, timeout=timeout, data=data, headers=BROWSER_HEADERS)


def _fetch_once(url, timeout=TIMEOUT, headers=None, data=None):
    """一次真的请求（不重试、不换协议、不关校验），返回 (html, final_url, 失败原因)。

    拆成「一次」与「fetch_html 的两次策略」两层，是为了让传输层保持可推理：
    上层那个「待重试」台账数的是**轮次**，如果传输层自己偷偷重试三次，
    「这一家到底请求了几次」就再也没人能说清（这一点写在 _http_session 的注释里）。

    ``data`` 非空时走 POST（搜索接口用）。**GET 与 POST 共用本函数而不是各写一份**：
    超时拆分、响应收口、重定向后的 SSRF 复核、字节封顶、编码推定——这五件里
    任何一件只在一条路径上生效，就会成为「同一个站 GET 能拦、POST 拦不住」那种分叉。
    """
    import requests
    hh = dict(HEADERS)
    if headers:
        hh.update(headers)
    try:
        parsed = urlparse(url)
        if (parsed.path or "") not in ("", "/"):
            # 子页带同站首页作 Referer：很多 WAF 看「document 请求没有 Referer/Sec-Fetch」
            # 就拦，猜路径那一族（/contactus.html）正是最容易撞在这上面的一类。
            hh.setdefault("Referer", f"{parsed.scheme}://{parsed.netloc}/")
    except ValueError:
        pass
    try:
        sess = _http_session()
        kw = dict(timeout=(CONNECT_TIMEOUT, timeout), allow_redirects=True, headers=hh)
        if data is not None:
            r = sess.post(url, data=data, **kw)
        else:
            r = sess.get(url, **kw)
        try:
            final = r.url or url
            code = int(r.status_code)
            body = r.content or b""
            enc, guess = r.encoding, r.apparent_encoding
        finally:
            # 响应一定要收口：连接靠它回到池子里复用；不收口就是每页攥死一条连接，
            # 几十家之后先红的是「Too many open files」这种和逻辑无关的东西。
            try:
                r.close()
            except Exception:
                pass
    except requests.exceptions.SSLError:
        return None, "", "证书校验失败"
    except requests.RequestException as e:
        return None, "", f"不可达({type(e).__name__})"
    except Exception as e:                    # 解析响应本身的怪异常也不能冒泡
        return None, "", f"抓取异常({type(e).__name__})"
    if not is_public_host(host_of(final)):        # 重定向也可能把请求带进内网
        return None, final, "重定向到非公网地址"
    if code != 200:
        return None, final, f"HTTP{code}"
    raw = body[:MAX_BYTES]
    if len(raw) < 120:
        return None, final, "页面过小"
    if enc in (None, '', 'ISO-8859-1'):
        enc = guess or 'utf-8'
    try:
        text = raw.decode(enc or 'utf-8', errors='replace')
    except (LookupError, TypeError):
        text = raw.decode('utf-8', errors='replace')
    return text, final, ""


def find_contact_links(html, base_url, limit=2):
    """首页里的联系/关于页（最多 limit 个）：正则取 a 标签，不引第三方解析器。"""
    out = []
    for m in re.finditer(r'(?is)<a\b([^>]*)>(.*?)(?:</a>|$)', html or ''):
        attrs, text = m.group(1), strip_tags(m.group(2))
        hm = re.search(r'href\s*=\s*["\']?([^"\'>\s]+)', attrs, re.I)
        if not hm:
            continue
        href = hm.group(1)
        if href.startswith(('javascript:', '#', 'mailto:', 'tel:')):
            continue
        blob = f"{href} {text}"
        if CONTACT_HREF_RE.search(blob) or CONTACT_TEXT_RE.search(text):
            u = urljoin(base_url, href)
            if u.rstrip('/') == base_url.rstrip('/') or u in [x[0] for x in out]:
                continue
            out.append((u, text[:12] or '联系页'))
            if len(out) >= limit:
                break
    return out


def _grade_pages(pages, name):
    """多页取同一值的最高分；返回按分数降序的 (电话, 邮箱)。"""
    best_p, best_e = {}, {}
    order_p, order_e = [], []
    parked = False
    attr_any = False
    for html, url, label in pages:
        vis = strip_tags(html)
        if PARKED_RE.search(vis[:8000]):
            parked = True
            break                       # 判定已经成立，后面的页不必再看
        if not attr_any and has_attr(name, vis, host_of(url)):
            attr_any = True
        ph, em = extract(html)
        gp, ge = score_contacts(html, ph, em)
        for v, s in gp:
            k = re.sub(r'\D', '', v)
            if k not in best_p or s > best_p[k][1]:
                if k not in best_p:
                    order_p.append(k)
                best_p[k] = (v, s)
        for v, s in ge:
            k = v.lower()
            if k not in best_e or s > best_e[k][1]:
                if k not in best_e:
                    order_e.append(k)
                best_e[k] = (v, s)
    if parked:
        # dead=这个站不用再请求了：停放/被抢注/挂马页照样返回 200、照样写号码，
        # 但它已经不是这家公司的官网，整页内容一律不采信（实测 gzyhship.com 变成
        # 电竞俱乐部后页面上明写 186-7035-1662 与 QQ 邮箱）。
        return [], [], "官网疑似停放/被劫持（整站不采信）", True
    ranked_p = sorted((best_p[k] for k in order_p), key=lambda x: -x[1])
    ranked_e = sorted((best_e[k] for k in order_e), key=lambda x: -x[1])
    # 归属证据一条都没有 → 整站不采信（fail-closed）。这不是保守，是「谁的值写给谁」
    # 这条底线：页面里既找不到公司名的品牌段、也找不到域名品牌段，就说明这页
    # 不是这家公司的官网（填错、易主、同名异业、集团挂子公司都在这类），
    # 此时 tel:/mailto: 再"硬证据"也只是「这个站的联系方式」，不是「这家的」。
    if not attr_any:
        return [], [], "页面认不出主体（官网可能已易主或填错）", False
    return ranked_p, ranked_e, "", False


def enrich_site(site, name="", timeout=TIMEOUT, opener=None, budget=45.0, deep=False):
    """单个官网：首页 + 最多几个联系页（首页没出东西再兜底猜 contact 路径）。

    返回 {"phones": [(值,分)], "emails": [(值,分)], "why": 人话原因, "pages": 页数}

    ``deep=True`` 是「缺值行深挖」：联系页从 2 个放宽到 5 个、猜路径从 4 条放宽到整张
    命名表、单站页数封顶从 6 提到 12。这个开关**只该给本轮还缺联系方式的行**，
    预算是整轮共享的，已经补到值的行再翻六页就是从别人身上抢预算。

    缓存的粒度是**抓到的页面**，不是打分结果：一张表里同一家公司常有几十条产品记录
    （同一官网），网络只花一次；但归属判定跟「这一行的公司名」有关，必须每行重算——
    按域名缓存打分结果会把 A 公司的电话盖到同站的 B 公司头上（同域名挂多主体的站
    真实存在：集团官网、行业频道页），那种错最贵。

    深挖碰上「同一个站已经被浅轮抓过」时**不重新请求**：先把缓存里的页面重新评一次，
    还是没值才去抓那些**没试过的**路径（靠 tried 集合去重）。同一个站浅、深两轮
    共 6+12 页全部重抽一遍，是预算上最贵的重复劳动。
    """
    t0 = time.time()
    url = norm_site(site)
    if not url:
        return {"phones": [], "emails": [], "why": "官网字段不是网址", "pages": 0}
    host = host_of(url)
    if is_platform_host(host):
        return {"phones": [], "emails": [], "why": "字段指向平台页（非企业官网）", "pages": 0}
    if host_reachable(host) is False:
        # 预检已经知道这个域名解不出来：不必再付 CONNECT_TIMEOUT（10 秒）去碰它。
        # 这一句才是那 327 家「不可达(ConnectionError)」的真正处置——先拦在发请求之前，
        # 而不是每家都去实测一次超时。它不是终态结论（缓存 TTL 两天，预检也会重问），
        # 所以走下面的原因判据：它不匹配 TRANSIENT_WHY_RE → 本轮判终态、不再花预算。
        return {"phones": [], "emails": [], "why": DNS_DEAD_WHY, "pages": 0}
    if not is_public_host(host):
        return {"phones": [], "emails": [], "why": "非公网地址（已拦截）", "pages": 0}
    get = opener or (lambda u: fetch_html(u, timeout=timeout))
    cap = MAX_PAGES_PER_SITE_DEEP if deep else MAX_PAGES_PER_SITE
    link_limit = CONTACT_LINKS_DEEP if deep else CONTACT_LINKS_FIRST
    guess_paths = GUESS_PATHS[:GUESS_PATHS_DEEP] if deep else GUESS_PATHS[:GUESS_MAX]

    pages = []
    cached = _cache_get(url)
    if cached is not None:
        cached_pages, dead_why = cached
        if dead_why:
            return {"phones": [], "emails": [], "why": dead_why, "pages": 0, "cached": True}
        if not deep:
            ph, em, note, dead = _grade_pages(cached_pages, name)
            why = note or ("" if (ph or em) else "页面未见可用号码/邮箱")
            return {"phones": ph, "emails": em, "why": why,
                    "pages": len(cached_pages), "cached": True}
        pages = list(cached_pages)
    # 已经付过请求的 URL（含首页本身）：深挖轮靠它跳过老页面，只补新路径
    tried = {u for u, _f, _l in pages} | {url}
    html = pages[0][0] if pages else None
    final = pages[0][1] if pages else url

    if html is None:
        html, final, err = get(url)
        if html is None:
            _cache_pages(url, [], err or "首页不可达")
            return {"phones": [], "emails": [], "why": err or "首页不可达", "pages": 0}
        pages.append((html, final, "首页"))
    ph, em, note, dead = _grade_pages(pages, name)
    base_reg = _reg_domain(host_of(final))     # 落地域（可能被重定向过）

    def same_site(u):
        """后续页面只认**同一个站**：联系页链接完全可能指向第三方（平台、地图、
        社交媒体、甚至内网地址），照着 href 一路抓下去等于把「回采这一家」变成
        「顺着这一家随便爬」，既越界又失控。"""
        h = host_of(u)
        return bool(h) and _reg_domain(h) == base_reg and not is_platform_host(h) \
            and is_public_host(h)

    # 没出值且站点没有被判死，才值得再花请求：先看页面上的联系/关于页，
    # 仍空再按企业站 CMS 的命名习惯猜几个 contact 路径（很多站首页确实不写号码）。
    if not (ph or em) and not dead:
        for u, label in find_contact_links(html, final, limit=link_limit):
            if time.time() - t0 > budget or not same_site(u) or len(pages) >= cap \
                    or u in tried:
                continue
            tried.add(u)
            h2, f2, _e2 = get(u)
            if h2:
                pages.append((h2, f2, label))
            ph, em, note, dead = _grade_pages(pages, name)
            if (ph or em) or dead:
                break
        if not (ph or em) and not dead:
            parsed = urlparse(final)
            root = f"{parsed.scheme}://{parsed.netloc}/"
            for gp in guess_paths:
                if time.time() - t0 > budget or len(pages) >= cap:
                    break
                gurl = root + gp
                if gurl in tried:
                    continue
                if not same_site(gurl):
                    break
                tried.add(gurl)
                h3, f3, _e3 = get(gurl)
                if h3:
                    pages.append((h3, f3, gp))
                ph, em, note, dead = _grade_pages(pages, name)
                if (ph or em) or dead:
                    break
    why = note or ("" if (ph or em) else "页面未见可用号码/邮箱")
    _cache_pages(url, pages, "")
    return {"phones": ph, "emails": em, "why": why, "pages": len(pages)}


def _cache_pages(url, pages, dead_why):
    """按字节预算写缓存：超预算就淘汰最久没用的那些（LRU），不整表清空。

    旧写法是「超过 800 条 clear 一次」——两个毛病：①800 条按条数算，而单条可
    以是 7MB，封顶封不住内存；②clear 会把刚抓完的一整批热点站一起丢掉，接下来的
    同站记录全部重新联网（弱机上表现为「越跑越慢」）。
    """
    size = _entry_bytes(pages, dead_why)
    with _LOCK:
        old = _HOST_CACHE.pop(url, None)
        if old is not None:
            _HOST_CACHE_BYTES[0] -= _entry_bytes(old[0], old[1])
        _HOST_CACHE[url] = (pages, dead_why)
        _HOST_CACHE_BYTES[0] += size
        evicted = 0
        # 预留一条的余量再淘汰：不然「刚清到预算内、下一条又超」会一直贴着上限抖动
        while _HOST_CACHE_BYTES[0] > HOST_CACHE_BUDGET_BYTES and len(_HOST_CACHE) > 1:
            gone = _HOST_CACHE.popitem(last=False)
            _HOST_CACHE_BYTES[0] -= _entry_bytes(gone[1][0], gone[1][1])
            evicted += 1
        return evicted


# ── 合并（用户要的「英文逗号去重」）──────────────────────────────
# 分隔符集合只许这一处定义：合并出口、算「本来就有的值」、算净新增，
# 三处都要按同一套分隔符切分。写三份的结局是迟早各判各的——比如在某一处
# 补了全角「／」而另一处没补，同一行的「净新增」就会跟真正写进表格的值对不上。
_SPLIT_RE = re.compile(r"[,;/／、，；]+")


def _value_key(v):
    """去重键：号码只看数字串（021-3612 9977 与 021-36129977 同号），邮箱看小写全文。"""
    s = str(v or "").strip()
    if "@" in s:
        return s.lower()
    d = re.sub(r'\D', '', s)
    return d if len(d) >= 5 else s.lower()


def normalize_display(v):
    """把页面里的排版写法收成可直接外呼的标准写法：「010- 64229311」→「010-64229311」、
    「+86 21 36129977」→「021-36129977」。只对**能确定是同一种号的不同排版**的形态动手，
    其余保留原样——宁可写得不好看，也不要把一个号改写成另一个号。"""
    s = re.sub(r'[\s\u3000]+', ' ', str(v or "")).strip()
    if not s:
        return ""
    if "@" in s:
        return s.lower()
    s = re.sub(r'\s*-\s*', '-', s)
    # 反向引用一律写 \g<N>：写 \1 时前面若紧贴数字，会被解析成八进制转义；
    # 分组编号也要数对（这里两个捕获组就是 1 与 2）。
    s = re.sub(r'^\+?86[\-\s]?0?(1[3-9]\d{9})$', r'\g<1>', s)
    if not s.startswith("400"):
        s = re.sub(r'^\+?86[\-\s]?(0?\d{2,3})[\-\s]?(\d{7,8})$', r'0\g<1>-\g<2>', s)
    return s.strip()


def merge_values(existing, incoming):
    """按「值本身」去重后用英文逗号拼接：021-36129977 与 021-3612 9977 算同一个号。

    分隔符只认 `，、;；/／` 这几种**值之间**的写法，刻意不含空格：号码自己就常带
    空格（0755-8612 3456），按空格拆会把一个号劈成两半，去重与回填就全错了。
    """
    out, keys = [], set()
    for part in _SPLIT_RE.split(str(existing or "")):
        part = part.strip().strip("()（）")
        if not part:
            continue
        k = _value_key(part)
        if not k or k in keys:
            continue
        keys.add(k)
        out.append(part)
    for v in (incoming or []):
        v = normalize_display(v)
        if not v:
            continue
        k = _value_key(v)
        if not k or k in keys:
            continue
        keys.add(k)
        out.append(v)
    return ",".join(out)


def split_mobile(phone):
    """手机号判定：只有 11 位 1[3-9] 才算手机，其余归「电话」列。"""
    d = re.sub(r'\D', '', str(phone or ""))
    return bool(re.match(r'^1[3-9]\d{9}$', d))


# ── 留痕列的两种结局：终态 vs 待重试 ──────────────────────────
# 「官网回采」列不是给人看的备注，它是**下一轮的排队依据**，所以必须能区分两类失败：
#   终态  （停放站、主体不符、字段不是网址、非公网）——再请求一百次也是那个答案，
#          永久跳过，把预算留给还没试过的站；
#   待重试（不可达、超时、TLS 抽风、5xx、内部异常）——企业站停机/换证书是常态，
#          下次任务该再给一次机会，但**次数要封顶**：一个真死掉的站不该每轮都
#          白占一份预算，试到第 MAX_ATTEMPTS 次就转终态（想重启它，清空这一列即可）。
RETRY_MARK = SITE_MARK + "待重试"
MAX_ATTEMPTS = 3
# HTTP403/401/407 算**暂时失败**，2026-11-18 外部机器实测推翻了这个模块旧口径：
# 那一轮 800 家里 204 家回 HTTP403（占缺口一半），旧正则不认它 → 全被判终态 →
# 那 204 家从此永不再问，而 403 最常见的成因是「前面的 WAF 按请求头不像浏览器判黑」
# ——换一组完整的浏览器头再打一次的成本是**一次请求**，判成终态的成本是这一家永久
# 不再被补。仍封顶 MAX_ATTEMPTS 次：真被拉黑的站试三次就停，不烧预算。
# （配套的抓取侧升级见 fetch_html：403 族会自动换头重试一次 + https→http 回落一次。）
TRANSIENT_WHY_RE = re.compile(
    r'^(?:不可达|抓取异常|证书校验失败|首页不可达|内部异常|HTTP(?:401|403|407|408|429|5\d\d)$|超时)')


def is_transient_why(why):
    """这个失败原因是「环境暂时不给力」还是「这站就这样了」。"""
    return bool(TRANSIENT_WHY_RE.search(str(why or "").strip()))


# 闸门只认「本机/链路撑不住」这一类信号，判据比 TRANSIENT_WHY_RE **窄**，这是刻意的：
# · 不可达(ReadTimeout/ConnectTimeout)：requests 把超时也归进 RequestException，所以
#   超时长这样——这是并发压过头的头号表现，必须算坏；
# · HTTP408/429/5xx：对方在喊「你太快了」或已经站不住；
# · 抓取异常：解析侧异常多半伴随内存/半包，也算压力。
# 而「不可达(ConnectionError/DNSError)」「证书校验失败」「首页不可达」是**对方**的事，
# 把并发砍下来一点用都没有；反过来，缓存命中也不算好样本（它没花网络，喂进健康窗口
# 只会灌出一窗「全好」，让机器明明撑不住还在加路）。
GATE_BAD_WHY_RE = re.compile(
    r'^(?:不可达\((?:Read|Connect)?Timeout\w*\)|抓取异常|HTTP(?:408|429|5\d\d)$)')

# 等许可的等待上限，2 秒。为什么不是「一直等」也不是 30 秒：线程只在回到循环顶部时
# 才会复查「停止」和预算，卡在 acquire 里时谁都叫不动它。参照物是**已经在手的一家**：
# 最坏 6 页 × 12 秒 = 72 秒本来就停不下来，所以这一档只要明显小于它，等许可就不会
# 变成反应迟滞的主项；2 秒正是把「被限速」的反应拉回秒级。副作用只有一个——
# stats["gated"] 会涨，而它是「限速次数」不是「丢了多少家」，日志里必须这么写。
GATE_ACQUIRE_S = 2.0


def attempts_of(mark):
    """从留痕里读出已经试过几次（`官网回采待重试(2次)：…` → 2）；没标次数按 1 次算。"""
    m = re.search(r'\((\d+)\s*次\)', str(mark or ""))
    return int(m.group(1)) if m else (1 if str(mark or "").startswith(RETRY_MARK) else 0)


def needs_retry(mark):
    """留痕为空（没试过）或仍是待重试且未到次数上限 → 这一行还值得花预算。"""
    s = str(mark or "").strip()
    if not s:
        return True
    return s.startswith(RETRY_MARK) and attempts_of(s) < MAX_ATTEMPTS


# ── 批量回采（会话层调用的唯一入口）────────────────────────────
def pick_site(rec):
    """按优先级取第一个「像企业官网」的字段值。"""
    for k in WEBSITE_KEYS:
        v = norm_site(rec.get(k))
        if v and not is_platform_host(host_of(v)):
            return v
    return ""


def _has_any(rec, keys=("电话", "手机", "邮箱")):
    return any(str(rec.get(k) or "").strip() for k in keys)


def enrich_records(records, log=None, workers=6, budget=15 * 60, timeout=TIMEOUT,
                   stop=None, limit=1500, only_missing=False, resolver=None,
                   deep=False, preflight=True, escalate=None):
    """就地补写 records 的电话/手机/邮箱（英文逗号去重合并），并写「官网回采」账。

    排队口径：**缺联系方式的先做**（收益最高），其次才是本来就有号码的做补充——
    预算到点时优先保证「缺的」被照顾到，而不是按表格顺序做到哪儿算哪儿。
    同一域名在进程内只请求一次（一张表里同一公司常有几十条产品记录）。
    预算用完/用户点停止后置 halt，**未落笔的结果一律不写**：绝不能出现
    「导出已经在跑了、还有野线程往记录里塞值」这种查无可查的数据污染。
    返回统计字典；任何单条失败都不影响其它记录，函数本身不抛异常。

    ``resolver`` 是瀑布式补全（功能 A）接进来的**官网发现provider**：
    `fn(record) -> (url, 层级slug) | [(url, 层级slug), ...] | url | 假值`。
    只在「行自带的官网字段不可用」时才问它，也就是说它是 `pick_site` 的下一级而不是
    替代者——这一条顺序很重要：行里本来就有正确官网时，任何推导都比不上直接访问那一家。
    层级由 provider 报下来，是因为「这个网址是怎么来的」只有它自己知道（邮箱域名反查 /
    品牌域名推导），而这件事决定了后面那批值可信分是多少。provider 抛异常等同于没找到，
    绝不能把已经跑了一半的回采轮带崩。

    为什么允许它回**一串**候选而不是一个：一个商号拼 .com 撞到别人站的概率很高
    （hapstar 这种字号，.com 与 .cn 是两个不相干的主），旧口径「只拿第一个候选、
    归属不过就整行作废」等于把后面那几个本来能成的候选白白拒掉。现在同一行按候选
    顺序试到「抽到值」为止，单行有墙钟上限与个数上限（见 MAX_CANDIDATES_PER_ROW），
    不会退化成「一行烧光整轮预算」。

    ``deep=True`` 时，**本轮还缺联系方式的行**按深挖口径跑（联系页 5 个 + 整张猜路径表）；
    已经有值的行不深挖——预算共享，那里是负收益。
    ``preflight=True`` 时在开工作线程前先把本批候选域并发解析一遍（结论进同一份缓存），
    解不出来的域在 enrich_site 里直接按「域名不存在」不付连接超时。界面线程**绝不能**
    调到这里来（那正是 2026-11-18 那次整窗转圈的根因）。

    ``escalate`` 是**惰性**追加候选的 provider：`fn(record) -> [(url, 层级slug), ...]`。
    与 resolver 的区别全在**问的时机**：resolver 在排队阶段一次性算完（那时还不知道谁
    能抽到值），escalate 只在「这一行前面的候选全部试完、一无所获、还没被预算拦下」
    的那一刻才问一次。要花钱的那一级（现网反查、搜索引擎）必须走这条路——预支配额
    会让本来一次都不用花的行也买单，而预算到点时那些钱连一行结果都换不回来。
    本模块不认识任何引擎：候选从哪来、花谁的额度、怎么限流熔断，都是上游的事，
    这里只管候选序与两个封顶（个数上限、单行墙钟）。没传＝老行为，一个额外请求都不发。
    """
    log = log or (lambda m: None)
    recs = [r for r in (records or []) if isinstance(r, dict)]

    def _norm_tier(tier):
        return tier if tier in _fe.TIER_LABEL else "mailsite"

    def _candidates(rec):
        """问 provider 要**一批**官网候选（行自带的那一个永远排第一）。"""
        if resolver is None:
            return []
        try:
            got = resolver(rec)
        except Exception:
            return []
        if not got:
            return []
        if isinstance(got, str):
            return [(got, "mailsite")] if got else []
        if isinstance(got, (tuple, list)) and got and isinstance(got[0], str):
            url = str(got[0] or "").strip()
            tier = _norm_tier(str(got[1] if len(got) > 1 else "").strip())
            return [(url, tier)] if url else []
        out = []
        for item in (got or []):
            if isinstance(item, str):
                url, tier = item, "mailsite"
            elif isinstance(item, (tuple, list)) and item:
                url = str(item[0] or "").strip()
                tier = _norm_tier(str(item[1] if len(item) > 1 else "").strip())
            else:
                continue
            if url and all(url != x[0] for x in out):
                out.append((url, tier))
        return out

    def _ask_escalate(rec, pool):
        """问「要花钱的那一级」要追加候选：只在免费候选全空手的那一刻叫一次。

        三条兜底都是硬的：没传 provider 就是一个都不问（老行为）；provider 抛异常
        等同于没找到（绝不能把已经跑了一半的回采轮带崩，与 resolver 同一条纪律）；
        拿回来的 URL 与已试过的重复、或层级 slug 不在注册表里，当场不要——
        宁可少一个候选，也不要一个「补全层级」列里写出对不上号的档。
        """
        if escalate is None:
            return []
        try:
            got = escalate(rec) or []
        except Exception:                       # noqa: BLE001 - provider 出问题不影响已跑的部分
            with lock:
                stats["escalate_err"] = stats.get("escalate_err", 0) + 1
            return []
        seen = {u for u, _t in pool}
        out = []
        for item in got:
            if isinstance(item, str):
                url, tier = item, "mailsite"
            elif isinstance(item, (tuple, list)) and item:
                url = str(item[0] or "").strip()
                tier = _norm_tier(str(item[1] if len(item) > 1 else "").strip())
            else:
                continue
            if url and url not in seen:
                seen.add(url)
                out.append((url, tier))
        out = out[:ESCALATE_MAX_PER_ROW]
        with lock:
            stats["escalate_ask"] = stats.get("escalate_ask", 0) + 1
            if out:
                stats["escalate_hit"] = stats.get("escalate_hit", 0) + 1
        return out

    cand = []
    for r in recs:
        site = pick_site(r)
        if site:
            cand.append((r, [(site, "site")]))
            continue
        items = _candidates(r)
        if items:
            cand.append((r, items[:MAX_CANDIDATES_PER_ROW]))
    if only_missing:
        cand = [x for x in cand if not _has_any(x[0])]
    # 留痕已判定为终态的记录不再花第二次预算；待重试的（且未到次数上限）重新排队
    todo = [x for x in cand if needs_retry(x[0].get(SITE_MARK))]
    skip_done = len(cand) - len(todo)
    todo.sort(key=lambda x: 0 if not _has_any(x[0]) else 1)
    targets = todo[:max(0, int(limit))]
    stats = {"total": len(recs), "eligible": len(targets), "cached": skip_done,
             "ok": 0, "added": 0, "no_site": len(recs) - len(cand),
             "reasons": {}, "budget_hit": 0, "marked": 0, "retry": 0,
             "verified": 0, "tiers": {}, "discovered": 0, "gated": 0,
             "multi": 0, "dns": {}}
    if not targets:
        return stats

    # ── 开抓前先把域名并发改一遍（本函数已在后台线程里，界面线程绝不走到这里）──
    if preflight:
        hosts = []
        for _rec, items in targets:
            for url, _tier in items:
                h = host_of(norm_site(url) or url)
                if h and h not in hosts:
                    hosts.append(h)
        try:
            stats["dns"] = preflight_hosts(hosts, stop=stop, log=log)
        except Exception as e:
            # 预检自身出问题不影响回采：没结论就是「不知道」，每家照旧去抓，
            # 最多回到今天这个「327 家各付 10 秒超时」的旧行为。
            log(f"⚠ 域名预检没跑成（{type(e).__name__}）：本轮不拦不可达域，照旧逐家实抓")
            stats["dns"] = {"error": type(e).__name__}

    lock = threading.Lock()
    idx = {"n": 0}
    done_sites = set()
    added_keys = set()      # 本轮真正新增的「值」全集（跨行去重后才是可交付的净新增）
    halt = {"on": False}          # 预算/停止：置位后工作线程只退出，不再落笔
    board = _gov.DoneBoard() if _gov else None      # 进展广播（见下面等待循环的注释）
    n_workers = max(1, min(int(workers), io_workers_cap()))   # 线程上限（=闸门上界）

    # ── 自适应并发闸门 ────────────────────────────────────────
    # 线程按**上限**建（界面那一档 workers），同时在网的家数由闸门说了算。这个分工
    # 不是随意切的：线程数在跑起来之后改不了，而本机压力是随时变的；如果反过来
    # 「按当前许可数建线程」，弱机上起步 2 路就永远只有 2 路——机器空下来了闸门想
    # 上探也没人去占那个许可，表现成「越跑越慢且再也回不去」（和上一版清缓存踢热点
    # 站是同一类错误：把动态量当成了静态量）。
    # 闸门建不起来（导入失败/参数意外）就退回固定并发：治理层出问题不该让采集停摆。
    gate = None
    if _gov is not None:
        try:
            gate = _gov.get_gate(GATE_NAME, max_permits=n_workers,
                                 initial=_gov.recommended_io_workers(cap=n_workers))
        except Exception:
            gate = None
    if gate is not None:
        # 治理必须可见：自动收放如果不打出来，用户只会觉得「这工具今天慢」而查不到原因。
        log(f"🌐 官网回采并发档位：{gate.describe()}（上限 {n_workers} 路，实际同时在网"
            f"由本机负载与实测超时率现算）")

    def stopped():
        return halt["on"] or bool(stop and stop())

    def claim_and_run():
        """认领一家并做完。返回 False = 没有待办了，这一路线程该退。"""
        with lock:
            i = idx["n"]
            if i >= len(targets):
                return False
            idx["n"] += 1
            rec, items = targets[i]
        # 整段包起来：工作线程里任何一条异常若冒泡出去，线程池就少一路，剩下
        # 的家数由更少的线程去跑（表现为「越跑越慢」而日志一句不提）——本轮
        # 真实踩过一次 TypeError，正是这样被静默吞掉的。
        try:
            one(rec, items)
        except Exception as e:
            with lock:
                why = f"内部异常({type(e).__name__})"
                stats["reasons"][why] = stats["reasons"].get(why, 0) + 1
        finally:
            # 每做完一家就播一次：等待方靠这个决定「要不要醒来看一眼进度/预算」。
            if board is not None:
                board.ping()
        return True

    def work():
        try:
            while True:
                if stopped():
                    return
                if gate is None:
                    if not claim_and_run():
                        return
                    continue
                # 队列空了就先退出，别为一个**不存在的活**去排队等许可：idx["n"] 只增
                # 不减，判空是一次性的真结论；反过来先 acquire 再认领的话，压力大的时候
                # 每路线程都要空等满 GATE_ACQUIRE_S 才知道没事干——一轮收尾平白多出
                # 「线程数 × 2 秒」的尾巴（表现为"最后卡几秒才结束"）。
                with lock:
                    if idx["n"] >= len(targets):
                        return
                # **先要许可，再认领**：反过来的话一家公司会在「已被队列占住」的状态下
                # 卡在闸门外——什么都不做，却谁也看不见它（进度条停在原地最容易被读成
                # 卡死）。拿不到许可不算失败，也不写任何留痕：这一行的「官网回采」列
                # 仍是空的，needs_retry 下一轮照旧把它排上，一家都不会丢。
                got = gate.acquire(timeout=GATE_ACQUIRE_S)
                if not got:
                    with lock:
                        stats["gated"] += 1
                    if stopped():
                        return
                    continue
                try:
                    if not claim_and_run():
                        return
                finally:
                    # 释放在 finally：漏一次的后果是许可永久流失，闸门会把自己锁死，
                    # 比没有闸门更糟（这是本模块对 slot()/try-finally 反复核对的那条）。
                    gate.release()
        finally:
            close_thread_session()     # 线程退出前收掉 Keep-Alive 会话（见其文档）

    def feed_gate(res, elapsed_ms):
        """把一家的网络结果喂给闸门（自适应全靠这一口喂出来的数据，不喂就永远不动）。

        三条口径，都是刻意的：
        · **缓存命中不喂**——它没花网络，喂进去只会灌出一窗「全好」，机器明明撑不住
          还在加路；一张表里同一公司有几十条产品记录时，命中的全是这种。
        · **只认 GATE_BAD_WHY_RE 那一类失败**为坏：超时/429/5xx 才是「本机或链路撑不住」；
          对方 DNS 死了、证书坏了、整站挂了，砍并发一点用都没有，反而把后面几家本来
          能成功的公司一起拖慢。
        · **异常路径不喂**（在 claim_and_run 里被接走）：代码 bug 不该让闸门以为
          机器不行——那会把一个 TypeError 永久变成「越跑越慢」。
        找到号码与否**不是**判据：页面压根没写电话是一家完全健康的公司，从资源角度
        看这一路成功极了。
        """
        if gate is None or res.get("cached"):
            return
        bad = bool(GATE_BAD_WHY_RE.search(str(res.get("why") or "")))
        try:
            gate.record(ok=not bad, latency_ms=elapsed_ms)
        except Exception:
            pass                       # 治理自身出问题绝不能把回采带崩

    def one(rec, items):
        """按候选顺序问这一家的官网：**抽到值就停**，一个都没抽到就记最后一个原因。

        候选顺序就是可信顺序（provider 排好的：行自带 > 邮箱域 > 主商号 > 拼音），
        所以「先抽到的就收」不会把低可信的推导盖在高可信的直采上。
        一层没中时**继续下一个候选**是本轮质量提升的主要来源：旧口径一个候选
        没过归属核验就整行作废，而同一个商号的 .com/.cn 往往是一个在别人手里。
        """
        name = str(rec.get("公司名称") or rec.get("公司名") or "")
        row_t0 = time.monotonic()
        deep_row = bool(deep) and not _has_any(rec)
        site, tier = "", "site"
        res = {"phones": [], "emails": [], "why": "没有可用官网候选", "pages": 0}
        tried_n = 0
        pool = list(items or [])
        pos = 0
        asked = False        # 单行只许问一次追加那一级：问了没结果还接着问就是拿预算原地打转
        while True:
            if stopped():
                return                   # 预算/停止：本轮不写这一条，留痕仍是空的（下次照排）
            if pos >= len(pool):
                # 手上的候选走空了。**只有在这一刻**才决定要不要去问要花钱的那一级：
                # 前面抽到值的话已经 break 出去，根本走不到这个破口；走到这里的只有两种
                # ——免费候选全试过都没抽到值，或者行自带那个官网本来就抓不通。
                # 这就是「只对缺口花配额」与「对全表花配额」的分别。
                if asked or escalate is None:
                    break
                asked = True
                pool.extend(_ask_escalate(rec, pool))
                continue
            left = ROW_WALL_BUDGET_S - (time.monotonic() - row_t0)
            if left <= 0:
                # 行级封顶到点：剩下的候选一个都不试。留痕按**最后一个实测原因**写，
                # 而不是把没试过的候选也算成失败——那种账会把「预算没了」记成「这家不行」。
                stats["row_budget_hit"] = stats.get("row_budget_hit", 0) + 1
                break
            url, t = pool[pos]
            pos += 1
            tried_n += 1
            site, tier = url, t
            _t = time.monotonic()
            res = enrich_site(url, name, timeout=timeout,
                              budget=min(45.0, max(8.0, left)), deep=deep_row)
            feed_gate(res, (time.monotonic() - _t) * 1000.0)
            if res.get("phones") or res.get("emails"):
                break
        if tried_n > 1:
            # 按**行**计数而不是按候选：这一格的用途是「本轮有多少家是靠第二个候选
            # 才补上的」，按候选累加会把三家候选全试的行算成两次。
            stats["multi"] = stats.get("multi", 0) + 1
        if stopped():
            return                           # 预算已用完：本轮不写这一条
        phones = [v for v, _s in res.get("phones") or []]
        emails = [v for v, _s in res.get("emails") or []]
        mobs = [v for v in phones if split_mobile(v)]
        tels = [v for v in phones if not split_mobile(v)]
        old = {"电话": str(rec.get("电话") or ""), "手机": str(rec.get("手机") or ""),
               "邮箱": str(rec.get("邮箱") or "")}
        had = {_value_key(x) for part in old.values()
               for x in _SPLIT_RE.split(part) if x}
        new = {"电话": merge_values(old["电话"], tels),
               "手机": merge_values(old["手机"], mobs),
               "邮箱": merge_values(old["邮箱"], emails)}
        gained = 0
        fresh = []
        for k in new:
            for x in _SPLIT_RE.split(new[k]):
                if x and _value_key(x) not in had and _value_key(x):
                    gained += 1
                    fresh.append(_value_key(x))
        with lock:
            # 跨行去重的账必须在锁内并进来：多路线程各自算各自的「净新增」，
            # 同一个号出现在两行上会被统计成两个净新增，交付时数字就对不上。
            fresh = [x for x in fresh if x not in added_keys]
            added_keys.update(fresh)
            gained = len(fresh)
            touched = []
            for k in new:
                if new[k] != old[k]:
                    rec[k] = new[k]
                    # 命中层级只在**真的写进了值**时登记：值没变却记一层，
                    # 就成了「邮箱:官网直采」而邮箱列还是空的那种自相矛盾的格子。
                    _fe.set_field_evidence(
                        rec, k, tier,
                        f"{host_of(site)}({res.get('pages', 0)}页)")
                    stats["tiers"][tier] = stats["tiers"].get(tier, 0) + 1
                    touched.append(k)
            # 「复核确认」= 这一行该字段原有的每一个值，这一轮又在页面上原样见到。
            # 它和「补到新值」同等重要：到期重验（功能 I）重问一圈拿回**同一个号**，
            # 若不刷新核验时间，这一行下一轮还会排在待办里——把预算花在原地打转上，
            # 比不重验更糟。反过来，页面没再见到旧号时**不能**刷新时间戳：那等于把
            # 「这家已经不换这个号了」这件事抹掉，而它正是下一轮该重验的唯一依据。
            seen = {"电话": {_value_key(x) for x in tels},
                    "手机": {_value_key(x) for x in mobs},
                    "邮箱": {_value_key(x) for x in emails}}
            confirmed = []
            for k in _fe.CONTACT_FIELDS:
                old_keys = {_value_key(x) for x in _SPLIT_RE.split(old.get(k, ""))
                            if x and _value_key(x)}
                if old_keys and old_keys <= seen.get(k, set()):
                    confirmed.append(k)
            if touched or confirmed:
                _fe.stamp_verified(rec)
                stats["verified"] = stats.get("verified", 0) + 1
            if phones or emails:
                if tier != "site":
                    stats["discovered"] += 1
                stats["ok"] += 1
                stats["added"] += gained
                rec[SITE_MARK] = (f"官网回采({res.get('pages', 0)}页)："
                                  f"电话{len(tels)}/手机{len(mobs)}/邮箱{len(emails)}，"
                                  f"净新增{gained}")
            else:
                why = res.get("why") or "无可用值"
                stats["reasons"][why] = stats["reasons"].get(why, 0) + 1
                if is_transient_why(why):
                    # 暂时失败：留下「待重试(N次)」，下一轮收尾还会排上它。
                    # 写成终态就是把「今天停机」当成「这家永远没官网」，
                    # 那种数据要用户手工清列才能重跑——不可接受的静默劣化。
                    n = attempts_of(rec.get(SITE_MARK)) + 1
                    if n >= MAX_ATTEMPTS:
                        # 到上限就**如实写成终态**：留痕既是下一轮的排队依据，也是
                        # 给用户看的解释。继续写「待重试(3次)」而 needs_retry 已经判否，
                        # 等于在表格里留一句永远兑不到的承诺——用户会一直等一个不会再来的
                        # 重试轮；改成终态并写明怎么重开，这一行才自洽。
                        rec[SITE_MARK] = (f"{SITE_MARK}：{why}（已试 {n} 次仍不通，"
                                          f"本轮起不再花预算；清空这一列可重新排队）")
                    else:
                        stats["retry"] = stats.get("retry", 0) + 1
                        rec[SITE_MARK] = f"{RETRY_MARK}({n}次)：{why}"
                else:
                    rec[SITE_MARK] = f"{SITE_MARK}：{why}"
            done_sites.add(_reg_domain(host_of(site)))
            stats["marked"] += 1

    ths = [threading.Thread(target=work, name=f"site-enrich-{i}", daemon=True)
           for i in range(n_workers)]
    t0 = time.time()
    for t in ths:
        t.start()
    beat = {"n": 0, "t": t0}
    seen = 0
    while any(t.is_alive() for t in ths):
        # 事件等待（P5）：旧写法是「对每条线程 join(timeout=0.5)」，一个周期 0.5×线程数
        # 秒——6 路时点停止最慢 3 秒才有反应，预算到点同样拖 3 秒，而绝大多数那次唤醒
        # 都是白问（并没有线程刚好在那一刻结束）。现在每做完一家就 ping 一次，等待方
        # 立刻醒来看进度；1 秒兜底超时是给心跳和预算留的落脚点（一批线程全卡在网络
        # 超时上时可能整整一分钟没有一次 ping，日志不能因此一行都不出）。
        if board is not None:
            seen = board.wait(1.0, seen)
        else:
            for t in ths:                       # 治理模块不可用时退回老口径，行为一模一样
                t.join(timeout=0.5)
        # 心跳：一轮回采动辄几分钟，界面只有「开始」和「结束」两行的话，用户没法
        # 判断是在跑还是卡死了（采集主链路同一条纪律：长任务必须自证还在推进）。
        if (idx["n"] - beat["n"] >= 40 or
                (time.time() - beat["t"] >= 60 and idx["n"] > beat["n"])):
            beat["n"], beat["t"] = idx["n"], time.time()
            log(f"🌐 官网回采进行中：{idx['n']}/{len(targets)} 家，"
                f"净新增 {len(added_keys)} 个联系方式")
        if time.time() - t0 > budget:
            halt["on"] = True
            stats["budget_hit"] = max(0, len(targets) - idx["n"])
            log(f"⏳ 官网回采预算（{int(budget // 60)} 分钟）用完：已跑 {idx['n']}/{len(targets)} 家，"
                f"剩余 {stats['budget_hit']} 家本轮不写（下次收尾会自动接着做）")
            break
        if stopped():
            halt["on"] = True
            break
    for t in ths:
        t.join(timeout=timeout + 60)     # halt 已置位：线程最慢也只把手上这一家做完就退
    stats["seconds"] = int(time.time() - t0)
    stats["sites"] = len(done_sites)
    stats["new_values"] = len(added_keys)
    # 以「真正落笔的条数」为准：被 halt 拦在半路的（已认领但没写）也算没做完，
    # 用认领数当进度会少报「剩余 N 家下次接着做」，而那正是用户决定要不要再跑一轮的依据。
    stats["skipped"] = max(0, len(targets) - stats["marked"])
    if gate is not None:
        # 档位账目落到 stats 里（界面/日志都能取）：自适应如果不留痕，慢的时候
        # 就只剩一句「今天有点慢」，谁也没法据此判断该不该把并发上限调低。
        gs = gate.stats()
        stats["permits"] = gs["permits"]
        stats["gate"] = gs
        stats["site_avg_ms"] = gs["avg_ms"]
        if stats["gated"]:
            log(f"🌐 本机档位本轮限速 {stats['gated']} 次（不是丢数据：没认领的公司留痕还是"
                f"空的，下次收尾自动接着做）；收工档位 {gs['permits']}/{gs['max']} 路，"
                f"单家平均 {gs['avg_ms']} 毫秒")
    return stats

