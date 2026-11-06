"""cdp_base.py —— 三站并行的公共采集引擎（裸 CDP，每站固定独占一个标签）。

为什么用裸 CDP 而不是 Selenium：三个站点会话要在**同一个 Chrome** 里真并行，
Selenium 多驱动切窗会互抢活动标签、命令通道串行，不安全（见项目既有并行结论）。
裸 CDP 每条命令都显式带上「本站那个标签」的 webSocketDebuggerUrl，三条命令通道
天然独立，可各跑一个线程并行推进。

本模块把与站点无关的通用能力都收敛到基类 CdpSiteCrawler：
  · 标签固定（pin）与导航（Page.navigate + 回读 location 断言 + 就绪判据）
  · 反采/验证码识别（以「页面异常小 + URL/标题特征」为判据，绝不整页正文关键词匹配，
    否则会误杀正常页里内嵌的验证类 JS 文案）
  · 被拦后原地等人工过验证 + 熔断式递增冷却（软限流不熬成硬封号）
  · 裸 HTTP 链路的防采/限流看门狗（顺企网 429 JS 挑战、供应商网 520 插页）：插页绝不
    当「本页无数据」写库，而是弹提醒 + 借标签把验证页递到眼前，探到人工过验后自动续采
  · 会话失效自愈（标签/浏览器被关→重取标签→续采，绝不崩线程）
  · 增量种子库（跨运行去重，命中即跳过，省请求=防封）
  · crawl(keywords) 模板方法：列表翻页 → 相关性过滤 → 详情取联系方式 → 落结果
  · 结果快照、清洗（复用 customer_library_cleaner）、导出原始/清洗两份 Excel

站点差异部分（搜索 URL 构造、列表解析、详情解析、相关性判据）由子类实现钩子方法。
"""
import os
import re
import sys
import json
import time
import random
import threading
from pathlib import Path

import requests

import province_filter as _pf

try:
    import websocket  # websocket-client
    HAS_WS = True
except ImportError:
    HAS_WS = False

try:
    import customer_library_cleaner as _cleaner
except ImportError:
    sys.path.insert(0, str(Path(__file__).parent))
    import customer_library_cleaner as _cleaner

try:
    from PyQt5.QtCore import QObject, pyqtSignal
    HAS_PYQT = True
except ImportError:
    HAS_PYQT = False

if HAS_PYQT:
    class CrawlerSignals(QObject):
        log_signal = pyqtSignal(str)
        progress_signal = pyqtSignal(int, int)       # 当前, 总数
        kw_progress_signal = pyqtSignal(str, int, int)  # slug, 已完成关键词数, 关键词总数（定量进度条用）
        crawl_finished_signal = pyqtSignal()
        blocked_signal = pyqtSignal(str)             # 需人工过验证
        block_cleared_signal = pyqtSignal(str)       # 已过验证继续
        login_required_signal = pyqtSignal(str)      # 需登录（这三站原则上不需要）
        stats_signal = pyqtSignal(str)               # 阶段/统计信息
else:
    class CrawlerSignals:
        pass


# 通用号码/邮箱正则（站点子类可覆盖）
PHONE_MOBILE_RE = re.compile(r'(?<!\d)1[3-9]\d{9}(?!\d)')
PHONE_TEL_RE = re.compile(r'(?<!\d)(?:\+?86[\s-]?)?(0\d{2,4}[-\s]?\d{7,8})(?!\d)')
EMAIL_RE = re.compile(r'[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}')

# 拦截分型标记：爬虫把它打在 blocked_signal 文本开头，界面据此定文案/要不要拉浏览器。
# 以前界面靠「reason 里有没有 HTTP 字样」自己猜，结果八方这种 IP 限流被当成验证码弹
# 「已在 Chrome 里打开对应页面」，用户满屏找入口找不到（客户实际反馈）。
BLOCK_TAG_IP = "【IP限流·无需操作】"
BLOCK_TAG_CAP = "【需人工验证】"
# 探针返回这个前缀＝本机网络不通（断网/DNS/连接被重置），不是站点在拦。
# 以前两类混成一句「仍在等站点放行（IP 冷却，无需操作）」，用户就会对着
# 一台断网的电脑等站点放行（2026-11-04 09:36 实机：HTTPSConnectionPool
# 那条连接失败被写成 IP 冷却，白等 300 秒）。分开的成本只有这一个前缀。
NET_FAIL_TAG = "本机网络不通："

# 会话/标签已死的特征（CDP 层）：命中即重取标签续采，绝不崩线程。
SESSION_DEAD_MARKS = (
    "connection refused", "connection aborted", "connection reset",
    "broken pipe", "no connection", "target closed", "unable to discover",
    "websocketexception", "invalidstatuscodeexception",
)

_ILLEGAL_RE = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f\ud800-\udfff\ufffe\uffff]')

# 地址尾部常粘连下一行的表单标签（实机原始数据取证：b2b168/gys 地址里吃进
# 「姓名/技术支持/联系人/注册资金」等词），统一从这里切开只留真实地址段。
# MIC 额外一类：contact 页地址后面紧跟着页面按钮，实测 265/286 行被写成
# 「…文官街50-1号 公司主页」（个别还带「查看更多 相关产品 …产品索引 添加到公司收藏
# 夹 举报」），外呼读起来完全不知道地址到哪里为止。只剥页面上确实存在的按钮词，
# 「（注册地址）」这类工商登记后缀不含在内、不会被误剔。
_ADDR_NOISE_RE = re.compile(
    r"(姓名|技\s*术\s*支\s*持|联\s*系\s*人|手\s*机|电\s*话|邮\s*箱|注\s*册\s*资\s*金"
    r"|公司网址|访问商铺|更多.{0,4}首页|扫码|扫一扫|查看地图|查看大图|在线留言|发邮件咨询"
    r"|公司主页|查看更多|相关产品|产品索引|添加到公司收藏夹|收藏夹|举报|在线询价)")


def clean_address(val):
    """截掉地址尾部粘连的表单标签噪声（三站详情解析共用）。"""
    if not val:
        return ""
    return _ADDR_NOISE_RE.split(str(val))[0].strip(" ：:，,、")[:120]


def clean_cell(val):
    """清掉 Excel 不接受的控制字符。"""
    if not isinstance(val, str):
        return val if val is not None else ""
    return _ILLEGAL_RE.sub('', val)


# 长途区号只存在这三种形状：010 / 02X / 03xx~09xx（0193、04450 之类都不在网内）。
# PHONE_TEL_RE 只看「0 开头 + 7~8 位本体」，凑得出这种假区号，外呼一定打不通。
_AREA_CODE_RE = re.compile(r"^0(?:10|2\d|[3-9]\d{2})$")


def is_valid_landline(tel):
    """固话能不能打：区号必须在真实存在的号码树里。
    实测马可波罗把手机 19352178592 写成「0193-52178592」（商家把手机前缀当区号填），
    形状合法但 0193 不是任何地区的长途前缀——这种号入库就是白送一列打不通的电话。"""
    digits = re.sub(r"\D", "", str(tel or ""))
    for L in (3, 4):                     # 区号含前导 0 共 3 位（010/020）或 4 位（0755）
        area, tail = digits[:L], digits[L:]
        if _AREA_CODE_RE.match(area) and re.fullmatch(r"\d{7,8}", tail):
            return True
    return False


def strip_country_code(val):
    """国际区号归一：商家把号码写成「86-0755-89810939」「+86 13912345678」（麦麦B2B/索比
    光伏网实测两种形态混着写）。直接正则会把「86-0」改成「00」造出打不通的号；
    这里按「固话补 0、手机去 0」的电信规则统一，同时去掉空格/横线分隔。"""
    s = re.sub(r"[\s\u3000]+", "", str(val or ""))
    m = re.match(r"^(?:\+?86)[-_]?(.+)$", s)
    if not m:
        return s.strip("()-（）")
    rest = m.group(1)
    if re.fullmatch(r"1[3-9]\d{9}", rest):
        return rest                       # 手机号：国家码去掉即可
    if re.fullmatch(r"0\d{2,4}[-]?\d{7,8}", rest):
        return rest                       # 「86-0755-89810939」本体已带长途 0
    if re.fullmatch(r"[1-9]\d{2,4}[-]?\d{7,8}", rest):
        return "0" + rest                 # 「86-755-89810939」：漏写 0 的补回来
    return rest


# ── 联系人 / 岗位（五站共用）───────────────────────────────────
# 实机取证各站把「谁负责接电话」写成完全不同的形态：
#   gys     商铺页 <dt>联系人：</dt><dd>朱先生</dd> + <dt>职位：</dt><dd>市场部经理</dd>
#   b2b168  contact.aspx「姓　　名：王国民<span>先 生</span><span>（经理）</span>」
#   11467   <li>主管：陈生</li>（职务标签本身就是人名行）
#   aipage  cardInfo「联系人：18962536677」「王经理推销勿扰」（号码/营销尾巴脏值）
# 统一在这里判定，避免各站自己正则、口径互不一致（曾出现有的剥成「周」有的留「杨经理」）。
_CONTACT_JUNK_RE = re.compile(
    r"(推销|勿扰|不接|拒接|加[Vv]?微信|添加微信|微信同号|微信同|手机同|邮箱|地址|网址|链接"
    r"|QQ|电话|手机|热线|先生\s*/|女士\s*/)")
_CONTACT_PAREN_POS_RE = re.compile(r"[（(]\s*([\u4e00-\u9fa5A-Za-z]{2,12})\s*[)）]\s*$")
_HONORIFIC_RE = re.compile(r"(先生|女士|小姐)")
# 站点自己把称呼写两遍（实测八方资源网 fnmeinfkcnhx5 的 contact.aspx 明文
# 「姓 名：徐先生 先生 （经理）」、移动页「联系人：徐先生先生 （经理）」——
# 商家把「徐先生」当名字填进姓名框，平台又在后面补一个「先生」）：
# 不合并就会入库「徐先生先生」这种外呼时叫不出口的名字。
# 只缩同词重复（反引用），否则「徐总经理」会被拆成「徐总」而弄丢岗位信息。
# 量词用 + 而非一次：商家连写三遍（「张先生先生先生」）也一并收拢。
# 多字称谓排在单字之前，保证「总监」先于「总」被捕获。
_DUP_HONORIFIC_RE = re.compile(
    r"(先生|女士|小姐|总监|经理|厂长|老板|主任|工程师|总)(?:\s*\1)+")
# 后缀称谓→岗位。分支必须按「长词在前」排：正则 alternation 是从左到右优先，
# 「经理助理」写在「经理」后面就永远只能匹到「经理」（实测「吴经理助理」被洗成「经理」）。
_TITLE_SUFFIX_RE = re.compile(
    r"(CEO|总经理助理|副总经理|总裁助理|首席执行官|项目经理|生产经理|技术经理|采购经理|财务经理|外贸经理"
    r"|电商经理|客服经理|运营经理|销售经理|业务经理|区域经理|市场经理|客户经理|经理助理|项目总监"
    r"|技术总监|销售总监|总经理|副总|总裁|董事长|总监|经理|厂长|主管|主任|科长|处长|工程师|技术员"
    r"|技术总工|总工|专员|业务员|业务代表|销售代表|跟单员|跟单|客户服务|客服|内勤|助理|顾问|代表"
    r"|老板|师傅|采购员|采购|质检员|质检|仓管|会计|出纳|人事)")
_CONTACT_PLACEHOLDERS = {"", "-", "--", "无", "暂无", "未知", "待补充", "未提供", "不详", "待定", "其他",
                         "未开", "未开通", "未公开", "保密", "联系人", "姓名", "公司", "客户",
                         "负责人", "法人代表", "法定代表人", "NULL", "null", "N/A"}
# 公司名/行业词误入联系人列（标签错位时会把公司名当人名），一律作废。
# 注意不能加「厂」：「张厂长」是真实称呼，误杀代价比漏拦更大。
_NOT_A_NAME_RE = re.compile(r"(公司|企业|集团|有限|商行|商贸|工作室|营业部|分部|店铺)")


def is_placeholder_number(val):
    """占位假号判定：b2b168 contact.aspx 明文写着「电　话：000-000000」「传　真：暂未填写」、
    QQ「000000」——这类全 0/全相同/无信息的假值绝不能当联系方式入库（实测会把「无电话」
    伪装成「有电话」，外呼打过去是空号）。"""
    if not val:
        return True
    s = str(val).strip()
    if re.search(r"暂无|未填写|未公开|待补充|无|N/?A", s) and not re.search(r"\d", s):
        return True
    digits = re.sub(r"\D", "", s)
    if not digits:
        return True
    if set(digits) <= {"0"}:                      # 000-000000 / 000000
        return True
    if len(set(digits)) <= 2 and len(digits) >= 7:   # 1111111 / 00000000 之类灌填
        return True
    # 主体号码是一串 0（实测顺企网「0515 80000000」）：区号是真、商家把号码随手
    # 灌了 8 个 0。整串判不出上面两条（非零位有 5/1），但连七位以上为 0 就是占位。
    # 阈值取 7：真实号段里偶见 4～5 个连 0（18710000805、15800005185），不能误杀。
    if re.search(r"0{7,}", digits):
        return True
    return False


def parse_contact_field(raw):
    """联系人原始串 → (联系人, 岗位)。纯函数、无副作用，便于离线断言。
    规则：剥营销尾巴 → 摘括号内岗位 → 合并重复称呼（「徐先生先生」）→
    无其他岗位信息时按后缀称谓识别 →
    仅在姓名本身够长时才剥「先生/女士」（宁可留「朱先生」也不写成「朱」）；
    纯号码、占位词、邮箱一律作废（返回空串，让清洗层退回公司名而不是写脏值）。"""
    text = normalize_spaces(raw)
    if not text:
        return "", ""
    cut = _CONTACT_JUNK_RE.search(text)
    if cut and cut.start() >= 2:
        text = text[:cut.start()].strip()
    position = ""
    mp = _CONTACT_PAREN_POS_RE.search(text)
    if mp:
        position = mp.group(1)
        text = text[:mp.start()].strip()
    text = _DUP_HONORIFIC_RE.sub(r"\1", text).strip()
    digits = re.sub(r"\D", "", text)
    if digits and (digits == text.replace(" ", "").replace("-", "")
                   or (len(digits) >= 7 and not re.sub(r"[\d\s\-()]", "", text))):
        return "", ""              # 「18962536677」这类把号码填进联系人列的脏值
    if text in _CONTACT_PLACEHOLDERS or "@" in text:
        return "", ""
    # 纯部门词（实测 mic meta「联系人：客服部」、b2b168/爱采购同形）：商家在联系人框
    # 里填的是部门，当名字入库会让外呼张口叫「客服部」。名字作废返回空串，
    # 由调用方的 dept_only 分支把部门归到岗位列（部门≠人名，但确实是岗位线索）。
    if dept_only(text):
        return "", ""
    # 粘在人名后面的号码尾巴（实测「李先生13800001111」）：不截会把号码当名字的一部分
    mnum = re.search(r"\d{7,}", text)
    if mnum and mnum.start() >= 2:
        text = text[:mnum.start()].strip()
    if not position:
        mt = _TITLE_SUFFIX_RE.search(text)
        if mt:
            position = mt.group(1)
    # 句子标点尾巴（实测 b2b168 商铺页「联系人是孟凤环。」——「。」不是名字一部分）。
    text = re.sub(r"[。，、；;，,．\s]+$", "", text).strip()
    name = text
    stripped = _HONORIFIC_RE.sub("", text).strip()
    if len(stripped) >= 2:
        name = stripped
    # 只剩个称谓/光缀词（「先生」「经理」）或实为公司名 → 不能当联系人入库；
    # 名字都不成立时岗位也一并作废（只剩「经理」二字的岗位列对外呼没有意义）。
    if _HONORIFIC_RE.fullmatch(name) or _TITLE_SUFFIX_RE.fullmatch(name) or _NOT_A_NAME_RE.search(name):
        return "", ""
    if not (1 <= len(name) <= 14):
        return "", ""
    return name, position


# ── 岗位智能匹配（五站共用）───────────────────────────────────
# 20261028 本批 940 行实测基线：岗位非空 38.8%，而有岗位的里面 79% 只是被剥成裸
# 「经理」（页面原文就只写了「王经理」）；573 行无岗位中 458 行联系人就是「张三/李四」
# 纯姓名。回访这些记录的详情页证实：八方/爱采购样本页 0 处职务字样，顺企网样本页只有
# 工商段「法人代表：陈杰」和商家自发招聘信息——**页面根本没写岗位，不是字段名没认出来**
# （实测把称谓词典从 21 个扩到 47 个，也只多回收 2 行）。据此定两条规矩：
#   ① 岗位列只写页面能证实的内容（明写职位/职务/岗位 > 职务标签 > 人名后缀称谓），绝不臆造；
#   ② 把旁证（部门字段、邮箱前缀 sales@/export@、公司名尾巴「销售部」）归集成「岗位族」
#      挂进外呼备注——这才是覆盖率之外真正能突破的部分。
# 只由称谓组成、不带部门信息的裸岗位（页面就写了个「经理」）。
_BARE_TITLE_RE = re.compile(
    r"^(?:先生|女士|小姐|经理|主管|主任|科长|处长|总监|厂长|老板|师傅|专员|业务员|代表|顾问"
    r"|工程师|技术员|总工|客服|助理|采购|质检|跟单|内勤|会计|出纳|人事)+$")
# 纯性别/礼貌称谓（不含任何职务信息）：实测索比光伏网联系页写「联 系 人：赵福兴 （先生）」、
# 麦麦B2B 同款，括号里只有性别称谓时不该单独立一个「岗位：先生」的废列（外呼毫无用处）。
HONORIFIC_ONLY_RE = re.compile(r"^(?:先\s*生|女\s*士|小\s*姐|男|女|同\s*志)$")
# 括号前的多余空格（实测索比光伏网岗位字段写「销售部 （销售经理）」，那个空格是页面排版带出来的）
_PAREN_GAP_RE = re.compile(r"\s*([（(])\s*")
# 页面部门写法 → 归一部门名（实测供应商网 <div class="position"> 只写「销售」「市场」二字）
_DEPT_CANON = {
    "销售": "销售部", "销售部": "销售部", "营销": "销售部", "网络销售": "电商部",
    "市场": "市场部", "市场部": "市场部", "业务": "业务部", "业务部": "业务部",
    "商务": "商务部", "外贸": "外贸部", "国际贸易": "外贸部", "进出口": "外贸部",
    "跨境": "外贸部", "电商": "电商部", "客服": "客服部", "售后": "客服部",
    "技术": "技术部", "技术部": "技术部", "研发": "研发部", "工程": "工程部",
    "生产": "生产部", "车间": "生产部", "采购": "采购部", "品质": "品质部",
    "质检": "品质部", "行政": "行政部", "办公室": "行政部", "人事": "行政部",
    "财务": "财务部",
}
# 公司名/商铺名以部门收尾（顺企网大量商铺是「XX有限公司销售部」自己建的号）
_DEPT_TAIL_RE = re.compile(
    r"(销售部|市场部|业务部|商务部|外贸部|国际贸易部|电商部|客服部|技术部|研发部|工程部"
    r"|生产部|采购部|品质部|行政部|财务部)\s*$")
# 邮箱前缀/域名首段所暗示的部门（实测爱采购 contactName=「赵月梅」无岗位，但邮箱是 sales@zcgm.cn）
_EMAIL_DEPT_MAP = {
    "sales": "销售部", "sale": "销售部", "market": "市场部", "marketing": "市场部",
    "export": "外贸部", "trade": "外贸部", "trading": "外贸部", "business": "业务部",
    "service": "客服部", "support": "客服部", "tech": "技术部", "engineer": "技术部",
    "purchase": "采购部", "buy": "采购部", "admin": "行政部", "hr": "行政部",
}
# 岗位族：外呼按角色分支话术用的粗分类（长词、特殊词在前，避开「外贸经理」被判成销售）
_FAMILY_RULES = (
    ("外贸", r"外贸|进出口|跨境|海外|国际业务|export|overseas|foreign"),
    ("管理层", r"董事长|总裁|首席执行官|CEO|总经理|副总经理|副总|老板|厂长|法定代表人"
              r"|法人|合伙人|股东|创始人"),
    ("技术", r"技术|工程师|研发|设计|总工|开发|调试"),
    ("生产", r"生产|车间|制造|工艺|质检|品质|设备|仓管"),
    ("采购", r"采购|供应链"),
    ("客服", r"客服|售后|服务"),
    ("行政财务", r"行政|人事|财务|会计|出纳|内勤|助理|办公"),
    ("销售", r"销售|业务|市场|商务|营销|代表|跟单"),
)


def _family_of(text):
    t = (text or "").lower()
    for family, rx in _FAMILY_RULES:
        if re.search(rx, t, re.I):
            return family
    return ""


def norm_dept(text):
    """部门字段归一：只写「销售」的补成「销售部」，认不出的部门词只取前 12 字。"""
    s = normalize_spaces(text)
    if not s:
        return ""
    s = s.strip("：:、，,")
    for word in sorted(_DEPT_CANON, key=len, reverse=True):
        if word in s:
            return _DEPT_CANON[word]
    return s[:12] if re.fullmatch(r"[\u4e00-\u9fa5]{2,12}(?:部|办|科|组|中心)?", s) else ""


def dept_from_company(company):
    """从公司/商铺名尾巴取部门（顺企网「…有限公司销售部」）；普通公司名返回空串。"""
    m = _DEPT_TAIL_RE.search(normalize_spaces(company))
    return m.group(1) if m else ""


def dept_only(text):
    """值是**纯部门**而非人名时返回归一部门名（实测 mic meta「联系人：客服部」——
    商家在联系人框里填的是部门）。不识别就会把「客服部」当客户名称入库，外呼
    张口叫「客服部」；识别后改道写岗位/岗位族依据，名字宁缺毋滥。"""
    s = normalize_spaces(text)
    if not s:
        return ""
    if s in _DEPT_CANON or _DEPT_TAIL_RE.fullmatch(s):
        return norm_dept(s)
    # 「客服部」「网络销售部」：部门词收尾才认，且必须短（长串是公司名+部门杂揉）
    if 2 <= len(s) <= 8 and re.search(r"(销售|市场|业务|商务|外贸|国际贸易|电商|客服|售后|技术|研发|工程|生产|采购|品质|行政|财务|办公室|内勤)部$", s):
        return norm_dept(s)
    return ""


def _is_pure_dept(text):
    """判定页面岗位字段的值是「裸部门」还是「完整岗位」：实测供应商网 <div class="position">
    既写「销售」也写「市场部经理」，把后者也送进 norm_dept 会被降级成「销售部」（丢掉
    经理信息），故先分辨：只有本身就是部门词的才走部门归一。"""
    s = normalize_spaces(text)
    if not s:
        return False
    return s in _DEPT_CANON or bool(_DEPT_TAIL_RE.fullmatch(s))


def compose_position(field="", title=""):
    """页面岗位/职位/部门字段原值 + 从联系人名后缀拆出的称谓 → 完整岗位。
    实测供应商网联系区块把信息拆成两半写：<div class=position>销售</div> + 联系人「王经理」，
    单独取哪一半都不完整，合起来才是「销售经理」；而字段本身已写完整岗位（「市场部经理」
    「销售总监」）时直接沿用，绝不先降级成部门再拼回去。人名侧写了更具体的岗位时以它为准。
    统一收尾：括号前不留空格（否则入库就是一串怪样，外呼念不出来）。"""
    raw = normalize_spaces(field)
    dept = norm_dept(raw) if _is_pure_dept(raw) else ""
    t = normalize_spaces(title)
    if not dept:                        # 没有部门线索：用完整岗位，其次用称谓
        pos = raw or t
    elif not t:
        pos = dept
    elif _BARE_TITLE_RE.fullmatch(t):
        pos = (dept[:-1] if dept.endswith("部") else dept) + t
    else:
        pos = t if len(t) >= len(dept) else dept
    return _PAREN_GAP_RE.sub(r"\1", pos) if pos else ""


def dept_from_email(email):
    """从邮箱前缀/域名首段取部门线索（sales@ / export@ / info@ 不产线索）。"""
    m = EMAIL_RE.search(normalize_spaces(email))
    if not m:
        return ""
    local, domain = m.group().split("@", 1)
    parts = [local.lower()] + domain.split(".")
    for seg in parts:
        for token in re.split(r"[._\-]|(?<=[a-z])(?=[A-Z])", seg):
            hit = _EMAIL_DEPT_MAP.get((token or "").strip().lower())
            if hit:
                return hit
    return ""


def position_family(position="", dept="", email="", company=""):
    """旁证归集→ (岗位族, 依据标签)。优先级：明写岗位 > 部门字段 > 邮箱 > 公司名尾巴。
    拿不到旁证时只给「管理/销售（未细分）」，不假装有结论。"""
    pos = normalize_spaces(position)
    if pos and not _BARE_TITLE_RE.fullmatch(pos):
        fam = _family_of(pos) or _family_of(norm_dept(pos))
        if fam:
            return fam, "岗位"
    d = norm_dept(dept)
    if d:
        fam = _family_of(d)
        if fam:
            return fam, "部门"
    d = dept_from_email(email)
    if d:
        fam = _family_of(d)
        if fam:
            return fam, "邮箱"
    d = dept_from_company(company)
    if d:
        fam = _family_of(d)
        if fam:
            return fam, "公司名"
    if pos or dept or normalize_spaces(email):
        return "管理/销售（未细分）", "称谓"
    return "", ""


_LEGAL_REP_RE = re.compile(
    r"(?:法定代表人|法人代表|法人|公司负责人)[\s:：是为名姓]{0,4}([\u4e00-\u9fa5]{2,4})"
    r"(?=[\s，,、。；;：:<>&）)】]|$)")
# 抽到的是下一句开头（工商段里「法人代表」后面紧跟「代表人」「经营」等词）时一律作废。
_LEGAL_STOP_RE = re.compile(r"(公司|有限|集团|经营|注册|代表|负责|位于|执照|名称|身份|姓名|注销|存续|序号)")


def extract_legal_rep(text):
    """从页面正文/简介里抽法定代表人姓名（实机取证：顺企网首页工商段「法人代表：陈杰」4/4 页
    命中、供应商网公司简介「法定代表人为杨兵」、爱采购 entInfo.legalPerson）。只负责抽取与
    洗虚词，入库口径由清洗层定（有显式联系人时客户名称仍用联系人）。
    注意这里**不能用 normalize_spaces**：它会把中文之间的空格合拢（「法人代表： 陈杰 成立时间」
    →「法人代表：陈杰成立时间」），姓名右边界就此消失，整段抽不到人。"""
    s = re.sub(r"<[^>]+>", " ", text or "")
    s = s.replace("\u3000", " ").replace("&nbsp;", " ").replace("\xa0", " ")
    s = re.sub(r"\s+", " ", s).strip()
    for m in _LEGAL_REP_RE.finditer(s):
        name = m.group(1)
        if _LEGAL_STOP_RE.search(name) or _NOT_A_NAME_RE.search(name):
            continue
        return name
    return ""


# 平台在 SEO 标题/列表锚文本里给公司名加「（XX厂家）」式营销尾巴（实测供应商网标题
# 「东莞市新金阳光电科技有限公司（光伏连接器接头厂家）」，53 行中招）。不剥的话：
# ① 外呼/外显名字难看；② 同一家公司在不同关键词下尾巴不同，按公司名的跳站/跳词
# 去重全部失效，客户库会出现同一家的多条重复行。只剥**位于结尾**且含厂家/制造商
# 等词的括号段：「东方鑫鸿（北京）科技有限公司」这类括号在名字中间的正规名不受影响。
_NAME_MARKETING_TAIL_RE = re.compile(
    r"[（(][^（）()]{2,24}(?:厂家|制造商|生产厂家|供应商|批发商|价格|报价)[）)]\s*$")


def strip_name_marketing_tail(name):
    return _NAME_MARKETING_TAIL_RE.sub("", (name or "").strip())


def normalize_spaces(val):
    """联系人类字段的空白归一：全角空格 \u3000、&nbsp;、连续空白统一压成一个空格再裁边。"""
    if val is None:
        return ""
    s = str(val).replace("&nbsp;", " ").replace("\u3000", " ").replace("\xa0", " ")
    s = re.sub(r"\s+", " ", s)
    # 中文之间的空格直接合拢（「王 国民」→「王国民」），英文/数字名内部空格保留
    s = re.sub(r"(?<=[\u4e00-\u9fa5])\s+(?=[\u4e00-\u9fa5])", "", s)
    return s.strip()


class CdpSiteCrawler:
    """站点采集器基类。子类须设置类属性并实现钩子方法。"""

    PLATFORM_NAME = "站点"          # 中文站点名（日志/结果「数据来源」用）
    PLATFORM_SLUG = "site"          # 英文短名（种子库文件名用）
    BASE_URL = ""                   # 站点首页
    HOME_DOMAIN = ""                # 用于 acquire_tab 复用判定与导航回读断言
    # 反采/风控特征（子类按站点覆盖）
    BLOCK_URL_MARKERS = ()
    BLOCK_TITLE_MARKERS = ("验证码", "安全验证", "滑块验证", "身份核实", "环境异常",
                           "访问验证", "请验证", "滑动验证", "人机验证", "访问受限",
                           "访问过于频繁", "拒绝访问", "forbidden", "403")
    SAFE_TITLE_HINTS = ()           # 正常页标题里可能出现的词，命中则不判拦截
    BLOCK_BODY_MARKERS = ("滑动验证", "拖动滑块", "验证码", "访问过于频繁", "安全验证",
                          "人机验证", "访问受限", "请完成验证")
    MIN_PAGE_CHARS = 1200           # 正常页正文规模下限（远小于此且命中拦截词才判拦截）
    SMALL_PAGE_CHARS = 1500         # 页面「异常小」阈值：小于此才可能是拦截插页
    NEEDS_BROWSER = True            # 是否需要共享 Chrome 标签（纯 HTTP 站设 False）
    # ── 裸 HTTP 链路的防采/限流判据（实机取证 2026-10-28）──────────────
    #   顺企网：搜索页与首页统一返回 HTTP 429 + 70 字节 JS 挑战体
    #           `<script src="https://do.11467.com/v.js?v=rollback-...">`，requests
    #           不执行 JS 就永远拿不到数据——旧链路把它当「请求失败」重试几轮后返回空串，
    #           列表解析成 0 家、日志写「无公司结果」，整轮就在这种静默失败里空跑。
    #   供应商网：子域商铺页 HTTP 520「访问异常 - 供应商网」（IP 级限流）。
    # 状态码命中即视为被拦，绝不允许当「这家没数据」写进结果与种子库。
    HTTP_BLOCK_STATUS = (403, 405, 412, 429, 502, 503, 520, 521, 522, 523)
    HTTP_BLOCK_MARKS = ("访问异常", "访问过于频繁", "安全验证", "人机验证", "滑动验证",
                        "请完成验证", "拒绝访问", "访问受限", "系统繁忙", "Server Error")
    HTTP_BLOCK_MAX_BYTES = 3000     # 插页体积上限：正常商铺/列表页远大于此
    # 「人可以在浏览器里过掉」的特征（区别于纯拒 IP 的 429/502）：分诊靠它。
    # 只收足够具体的词——被误判成 captcha 会白叫人工（客户这次的投诉），
    # 所以像 security 这种在报错页里满天飞的泛词一律不收。
    HTTP_CHALLENGE_MARKS = ("人机验证", "滑动验证", "安全验证", "请完成验证", "验证码",
                            "captcha", "recaptcha", "verify", "verification",
                            "challenge", "turnstile", "v.js", "cloudflare", "sufei")
    BLOCK_POLL = 15                 # 看门狗恢复探测间隔（秒）——仅针对可人工解除的验证页
    # IP 级限流的探测间隔：必须远大于验证页那种。拿 15s 去探一个正在冷却的 IP，
    # 等于自己把冷却窗口打断（实测八方就是这样从软限流熬成硬封）。
    BLOCK_POLL_IP = 45              # IP 冷却首次探测间隔（秒），之后按此指数退避
    BLOCK_POLL_IP_MAX = 600         # IP 冷却探测间隔上限
    BLOCK_MAX_WAIT = 1800           # 单次等人工过验证的总时长上限（秒）
    BLOCK_MAX_WAIT_IP = 2700        # 单次等 IP 冷却的总时长上限（冷却本来就慢，别提前放弃）
    BLOCK_COOKIE_DOMAIN = ""        # 纯 HTTP 站过验证后需要抄回的 cookie 域（子类设）
    # 「自动挑战页」卡住不动时主动推进（Cloudflare 那种只有转圈、没有任何可点控件的插页）。
    # 默认 False：只有明确实测过「刷新确实能让它继续走」的站才打开（2026-11-02 麦麦B2B 实测）。
    BLOCK_AUTO_REFRESH = False
    BLOCK_REFRESH_EVERY = 10        # 连续被拦多久算「卡住」，到点推一把（实测约 10 秒一次有放行的）
    BLOCK_REFRESH_MAX = 200         # 单轮等待最多推几次（点「刷新此页面」不重开挑战，可以给足次数；
                                    # 按 10 秒一次算要能撑满整个等待窗口 BLOCK_MAX_WAIT）
    BLOCK_RELOAD_LAST_RESORT = 90   # 插页上没任何可点入口、也没真人勾选框时，卡满这么久才「清掉本站通行证再重开这一页」
    # 真人勾选框（Cloudflare Turnstile「请验证您是真人」那种 300×65 嵌入式控件）在跨域 iframe 里：
    # JS 碰不到它，element.click() 也不算真人（CF 只认可信事件）——但鼠标只看屏幕坐标，
    # 所以按控件的几何位置发一次真鼠标点击就能点到勾选框（2026-11-03 麦麦B2B 实测）。
    TURNSTILE_CLICK_X = 30          # 勾选框画在控件左端约 30 像素处，竖向居中
    TURNSTILE_CLICK_COOLDOWN = 30   # 点一下之后给它这么久的时间自己跑完（连着点会把这一轮进度顶回去）
    TURNSTILE_WIDGET_JS = """(function(){
function box(e){if(!e)return null;var r=e.getBoundingClientRect();
return (r.width>=150&&r.height>=40&&r.bottom>0&&r.top<(window.innerHeight||900)*0.92)?
JSON.stringify({x:r.left,y:r.top,w:r.width,h:r.height}):null;}
var s=['.cf-turnstile iframe','.cf-turnstile','iframe[src*="challenges.cloudflare.com"]',
'#challenge-stage','iframe[src*="captcha"]','iframe[src*="verify"]'];
for(var i=0;i<s.length;i++){try{var e=document.querySelector(s[i]);
var r=e&&box(e);if(r)return r;}catch(x){}}
return '';})()"""
    # 插页上那个「刷新此页面」链接（Cloudflare 自己写的 id=challenge-refresh）。优先点它：
    # 它是“拿着现有令牌再试一次”；整页重载则是“重新开始一场挑战”，反而更慢。
    CHALLENGE_REFRESH_JS = """(function(){
var els=[].slice.call(document.querySelectorAll('a,button'));
for(var i=0;i<els.length;i++){var e=els[i];
var t=(e.innerText||e.textContent||'').trim();
if(e.id==='challenge-refresh'||/刷新此页面|刷新页面|refresh this page/i.test(t)){
e.click();return t.slice(0,20)||e.id;}}
return '';})()"""
    # 是否把「采集间隔」下沉到每个请求前对齐（只给一条记录要打多个页的纯 HTTP 站开）。
    # 默认 False：浏览器站一条记录 = 一次导航，记录级节流已经等价，
    # 无差别打开会让正常运行的站莫名慢 2~3 倍。
    PER_REQUEST_PACE = False
    # 请求级节流相对于「采集间隔」的比例：一家公司要发 5 个页的站若每个请求都付
    # 整段间隔，吞吐会掉到 1/5。取 0.5 意味着八方的请求间隔是界面间隔的一半，
    # 既把突发流量摊平（防封的关键），又不至于慢到不能用。
    PACE_FACTOR = 1.0
    # 本站可接受的「采集间隔」下限（界面下发的值低于它时会被钳到这个值）。
    # 对开了请求级节流的纯 HTTP 站，界面间隔×PACE_FACTOR 才是真正的请求间隔，
    # 所以它们必须自己声明下限，否则用户把间隔调到最小就会重现“200 多家后被限 IP”。
    MIN_DELAY = 0.0
    # 被拦时是否借浏览器把验证页递到眼前：限流类站（等 IP 冷却就行）设 False，
    # 免得为了等冷却无端拉起一个 Chrome 窗口。
    BLOCK_OPEN_BROWSER = True

    def __init__(self, port=9255):
        self.port = port
        self.pin_tab_id = ""
        self._ws_cache = ""
        self._lock = threading.RLock()

        self.signals = CrawlerSignals() if HAS_PYQT else None
        self.results = []
        self.results_lock = threading.RLock()

        self._crawling = False
        self._stop_requested = False
        self._pause_requested = False

        # 采集速度：低频是防封的第一原则（多子域共享后端的站点尤其要慢）
        self.delay_between = 4.0
        self.jitter = 2.0
        self._current_delay = self.delay_between
        self.max_pages = 0           # 每个关键词最多翻几页；0=不限，一直翻到没数据为止
        self.max_records = 0         # 采到 N 条自动收尾（0=不限）
        self.relevance_guard = True  # 相关性（关键词↔行业）过滤
        self.incremental = True      # 跨运行增量去重
        self.province = ""           # 省份精准采集：""=全国不限（GUI 省份选择下发）
        self._pf_dropped = 0         # 属地明确指向外省而丢弃计数（完成时如实汇报）
        self._pf_unknown = 0         # 属地无法确认而丢弃计数（同上，并回流待核实名单）
        # 属地无法确认的公司名回注入口（ParallelSession 接 企知道待采名单）：
        # 页面没写属地不等于这家不是目标省，交给企知道按名反查一次属地再定入库，
        # 比拍脑袋丢弃/拍脑袋入库都便宜。没有接线时为 None（单站自测/独立工具）。None-safe。
        self.province_unknown_sink = None

        # 熔断：短窗口内反复被拦＝已进限流区，强制冷却、必要时优雅收本轮
        self._block_times = []
        self._block_window = 900
        self._max_blocks = 5
        # IP 级限流的熔断比验证码更敏感：验证码是“过一道门”，IP 限流是“己方已越限”，
        # 后者再硬撞只会把封禁时长往上叠（实测 3 次后基本就进硬封了）。
        self._max_blocks_ip = 3
        # 看门狗状态：当前是否处于「等人工过验证/等 IP 冷却」，以及本轮拦下的原因
        self._blocked = False
        self._block_reason = ""
        self._block_kind = ""       # "ip"=IP 级限流（无页可点）/ "captcha"=可人工解除
        self._retry_after = 0       # 服务端 Retry-After 提示的秒数（有就听它的）
        self._block_probe_url = ""   # 探测用的代表页（就是刚被拦的那一页）
        self._borrowed_tab = False   # 纯 HTTP 站为过验证临时借的标签（恢复后归还）
        self._reloaded_once = False   # 本轮被拦是否已做过「保底整页重载」（重载会重开挑战，只做一次）
        self._turnstile_last_click = 0.0   # 上次替用户点验证勾选框的时刻（见 TURNSTILE_CLICK_COOLDOWN）
        # 用户点了「我换好网络了，马上继续」时置位：把正在退避的看门狗当场叫醒，
        # 不再让他干等下一个 45s~600s 的探测周期（换出口 IP 后马上就能接上）。
        self._block_wake = threading.Event()

        self._seen_store = {}
        self._seen_members = set()
        self._filtered_count = 0
        self._pf_dropped = 0
        self._pf_unknown = 0
        self._early_skipped = 0

        # 请求间隔统一记账（见 _sleep）：锚定「上一次真实请求完成」的时刻，
        # 同一条记录处理链路里连续多次 _sleep 不会重复支付整段等待。
        self._last_activity = 0.0
        self._last_pace = 0.0

        # 量化统计（回答「实际采了多少 / 过滤了多少」）：
        # 列表条目 / 早过滤 / 详情不相关 / 成功 / 无联系方式 / 失败(导航/解析/拦截)
        self.stat_listed = 0
        self.stat_pages = 0
        self.stat_ok = 0
        self.stat_no_contact = 0
        self.stat_failed = 0

        # 关键词级台账（续采与扩词反馈的地基）：
        #   kw_yield —— 该词本轮净新增几条记录（判断词好不好的唯一硬指标）
        #   _kw_done —— 该词已完整跑完一轮（续采时跳过；中途被停的不算）
        # 旧版只有 start_index（按词表下标续采），多站并行下各站进度不同根本对不齐，
        # 所以下标续采一直没真能用；改成「按词记账」才能做到任意时刻停、下次接着采。
        self.kw_yield = {}
        self._kw_done = set()
        # 续采开关：由 restore_progress 置 True，全新任务保持 False（进 crawl 会清空台账）
        self.resume = False

        self._data_dir = self._stable_data_dir()
        self._seen_store_path = self._data_dir / f"seen_store_{self.PLATFORM_SLUG}.json"

    # ── 日志 / 结果 ────────────────────────────────────────────
    def _log(self, msg):
        line = f"[{time.strftime('%H:%M:%S')}][{self.PLATFORM_NAME}] {msg}"
        print(line, flush=True)
        if self.signals:
            try:
                self.signals.log_signal.emit(line)
            except Exception:
                pass

    def _emit(self, name, *args):
        if self.signals:
            try:
                getattr(self.signals, name).emit(*args)
            except Exception:
                pass

    def snapshot_results(self):
        with self.results_lock:
            return [dict(r) for r in self.results]

    def _apply_province_filter(self, rec):
        """省份精准采集的入库关卡（所有站都经 _add_result 这一条漏斗）。

        统一口径：拿记录自己带回来的「所在区域/地址/公司名」做一次属地判定，
        参数直筛站也不例外——trust but verify：站方参数只在它真的生效时才是优选项，
        而“真的生效”是个会随改版静默失效的前提（1688 实锤：标着广东的 91 条里
        只有 12 条真属广东）。分档由 province_filter.param_verify_policy 定：
          · 数据层站与 strict 参数站：只收 match（fail-closed，判不出也不入库）；
          · lenient 参数站（mic/makepolo）：只砍 miss（站方已筛过，无标注不重复杀）。
        match 且原「所在区域」为空时，回填一个**有页面证据支撑**的属地标签（广东·深圳），
        绝不拿已选省份臆测填。判不出的公司名回 Sink 到企知道待采队列二次核实。
        返回 None = 丢弃；丢弃只记日志计数，不写种子库（没采过≠采完）。"""
        pv = str(self.province or "").strip()
        if not pv or not isinstance(rec, dict):
            return rec
        slug = str(getattr(self, "PLATFORM_SLUG", "") or "").lower()
        text = _pf.record_region_text(rec)
        verdict, keep = _pf.judge_record(slug, pv, rec)
        if keep:
            if not str(rec.get("所在区域") or "").strip():
                label = _pf.region_label(pv, text)
                if label:
                    rec["所在区域"] = label
            return rec
        name = str(rec.get("公司名称") or rec.get("公司名") or "")[:24]
        pv_display = pv if "," not in pv else "目标省份"  # 多省时显示「目标省份」而非列丼
        if verdict == "miss":
            self._pf_dropped += 1
            tag = "站方筛漏了、本地按地址拦下" if _pf.uses_param_filter(slug) else "跳过非目标省份商家"
            self._log(f"🗺 [{pv_display}] {tag}：{name}（{text.strip()[:36]}）")
            return None
        self._pf_unknown += 1
        self._log(f"🗺 [{pv_display}] 页面与公司名都没写省份、无法确认，不入库：{name}")
        self._sink_unverified(rec)
        return None

    def _sink_unverified(self, rec):
        """属地判不出的公司交给外部钩子（企知道反查一次属地比直接丢便宜）；没接线就无操作。"""
        sink = getattr(self, "province_unknown_sink", None)
        if not callable(sink):
            return
        name = str(rec.get("公司名称") or rec.get("公司名") or "").strip()
        if not name:
            return
        try:
            sink([name])
        except Exception:
            pass     # 辅助通道不得影响采集主流程

    def _add_result(self, rec):
        rec = self._apply_province_filter(rec)
        if rec is None:
            return
        with self.results_lock:
            self.results.append(rec)
        kw = str(rec.get("关键词") or "").strip()
        if kw:
            self.kw_yield[kw] = self.kw_yield.get(kw, 0) + 1
        self._commit_to_store(rec)
        if self.max_records and len(self.results) >= self.max_records:
            self._log(f"🛡 已达限量 {self.max_records} 条，自动停止")
            self._stop_requested = True

    # ── 增量种子库（跨运行去重）───────────────────────────────
    # 瞬时/非终态标记：状态命中任一者即视为「本次没采成」，不写入种子库去重，
    # 换关键词或下次重跑仍会重新采集（含「详情解析异常」这类可恢复的解析失败）。
    TRANSIENT_MARKS = ("拦截", "未解除", "导航失败", "待重试", "失败", "异常")
    # 空联系人自愈：历史上「成功但联系人取空」的公司一旦锁进种子库，以后重跑都是命中
    # 即跳过，那批记录就此永久停在缺联系人状态（供应商网 52 家、八方资源网 12 家正是
    # 这么锁死的）。所以条目里记 ct（采到时联系人是否非空）：空联系人的记录允许再采
    # CONTACT_RETRY_MAX 次——补到联系人自然锁全，站点确未公开也在两次后放弃。
    CONTACT_RETRY_MAX = 2

    def _is_terminal(self, rec):
        st = rec.get("状态", "") or ""
        return st != "" and not any(m in st for m in self.TRANSIENT_MARKS)

    def _rec_seen_keys(self, rec):
        keys = []
        mid = str(rec.get("memberId") or rec.get("公司ID") or "")
        if mid:
            keys.append(f"{self.PLATFORM_SLUG}:{mid}")
        name = self._norm_name(rec.get("公司名称") or "")
        if name:
            keys.append(f"{self.PLATFORM_SLUG}:name:{name}")
        url = str(rec.get("详情URL") or rec.get("公司网址") or "")
        if url:
            keys.append(f"{self.PLATFORM_SLUG}:url:{url}")
        return keys

    def _load_seen_store(self):
        try:
            with open(self._seen_store_path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                self._seen_store = {str(k): v for k, v in data.items()}
        except Exception:
            self._seen_store = {}
        return len(self._seen_store)

    def _save_seen_store(self):
        try:
            tmp = self._seen_store_path.with_suffix(".json.tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._seen_store, f, ensure_ascii=False)
            tmp.replace(self._seen_store_path)
            return True
        except Exception as e:
            self._log(f"⚠ 种子库保存失败：{str(e).splitlines()[0]}")
            return False

    @classmethod
    def _entry_locked(cls, entry):
        """种子库条目是否仍应「命中即跳过」。空联系人且重试未用尽者不锁定，
        让下一轮重跑自动补采。旧版本写入了无 ct 字段的条目一律按已完整处理：
        否则一次普通重跑会把历史的上千家公司的详情页重新访一遍，反而加重风控。"""
        if not isinstance(entry, dict) or "ct" not in entry:
            return True
        if entry.get("ct"):
            return True
        return int(entry.get("rt") or 0) >= cls.CONTACT_RETRY_MAX

    def _locked_keys(self):
        return {k for k, v in self._seen_store.items() if self._entry_locked(v)}

    def _commit_to_store(self, rec):
        if not self.incremental or not self._is_terminal(rec):
            return
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        st = rec.get("状态", "")
        if st == "类目不符(已过滤)":
            # 相关性判定是「关键词相关」的：同一家对 A 词不符、对 B 词可能正是目标，
            # 故只按「关键词#主体」作用域记录，换关键词重跑仍会重新评估。
            mid = str(rec.get("memberId") or rec.get("公司ID") or rec.get("公司名称") or "")
            if mid:
                self._seen_store[f"{self.PLATFORM_SLUG}:{rec.get('关键词','')}#{mid}"] = {"at": ts, "st": st}
            return
        for k in self._rec_seen_keys(rec):
            old = self._seen_store.get(k) or {}
            has_contact = bool(normalize_spaces(rec.get("联系人") or ""))
            entry = {"at": ts, "st": st, "ct": 1 if has_contact else 0}
            if not has_contact:
                entry["rt"] = int(old.get("rt") or 0) + 1
            self._seen_store[k] = entry

    def clear_seen_store(self):
        self._seen_store = {}
        try:
            if self._seen_store_path.exists():
                self._seen_store_path.unlink()
        except Exception:
            pass
        return True

    @staticmethod
    def _stable_data_dir():
        """数据目录必须跨运行持久且可写。PyInstaller onefile 下 __file__ 指向每次变、
        退出即删的 _MEIxxxx，故打包运行用 exe 同级目录，开发用模块目录，再回退用户主目录。
        Mac .app 双击启动时 exe 在 Contents/MacOS 里，数据改放 .app 旁边的可见目录：
        既不塞胖应用包，Gatekeeper 只读转置（App Translocation）下也会落到回退位置。"""
        if getattr(sys, "frozen", False):
            exe_dir = Path(sys.executable).resolve().parent
            if (sys.platform == "darwin"
                    and exe_dir.parent.parent.name.endswith(".app")):
                primary = exe_dir.parent.parent.parent / "b2bcollect_data"
            else:
                primary = exe_dir / "b2bcollect_data"
        else:
            primary = Path(__file__).resolve().parent / "b2bcollect_data"
        for d in (primary, Path.home() / ".b2bcollect" / "b2bcollect_data"):
            try:
                d.mkdir(parents=True, exist_ok=True)
                probe = d / ".write_test"
                probe.write_text("1", encoding="utf-8")
                probe.unlink()
                return d
            except Exception:
                continue
        return primary

    @staticmethod
    def _norm_name(name):
        return re.sub(r"[\s（）()・.、，,]+", "", name or "")

    # ── CDP 通信（每条命令显式带本站标签 ws 地址）──────────────
    def _get_tabs(self):
        try:
            data = requests.get(f"http://127.0.0.1:{self.port}/json/list", timeout=4).json()
            return data if isinstance(data, list) else []
        except Exception:
            return []

    def _ws_url(self):
        """返回本站固定标签的 webSocketDebuggerUrl；标签没了返回空串。"""
        tabs = self._get_tabs()
        if self.pin_tab_id:
            for t in tabs:
                if t.get("id") == self.pin_tab_id:
                    self._ws_cache = t.get("webSocketDebuggerUrl") or ""
                    return self._ws_cache
        # pin 丢失：尝试按域名找回，找不到用缓存/第一个
        for t in tabs:
            if self.HOME_DOMAIN and self.HOME_DOMAIN in (t.get("url") or ""):
                self.pin_tab_id = t.get("id")
                self._ws_cache = t.get("webSocketDebuggerUrl") or ""
                return self._ws_cache
        return self._ws_cache

    def _send(self, method, params=None, timeout=20):
        ws_url = self._ws_url()
        if not ws_url:
            raise RuntimeError("no target tab websocket")
        ws = websocket.create_connection(ws_url, timeout=timeout)
        try:
            ws.send(json.dumps({"id": 1, "method": method, "params": params or {}}))
            # 只取与本命令 id 匹配的响应，忽略事件帧
            deadline = time.time() + timeout
            while time.time() < deadline:
                raw = ws.recv()
                if not raw:
                    continue
                msg = json.loads(raw)
                if msg.get("id") == 1:
                    return msg
            return {}
        finally:
            try:
                ws.close()
            except Exception:
                pass

    def _eval(self, script, timeout=20):
        try:
            r = self._send("Runtime.evaluate", {
                "expression": script, "returnByValue": True, "awaitPromise": True}, timeout)
        except Exception as e:
            if self._session_dead(str(e)):
                raise
            return None
        if not r or "error" in r:
            return None
        return r.get("result", {}).get("result", {}).get("value")

    def _html(self, tries=3):
        """取渲染后整页 outerHTML。CDP 瞬时超时/报错时 _eval 返回 None，
        不能直接当空页（会被误读为「无结果/小页」而静默丢数据），故有界重试。
        会话真死时 _eval 会抛异常，由上层自愈逻辑接管，不在此重试。"""
        for i in range(max(1, tries)):
            val = self._eval("document.documentElement.outerHTML")
            if isinstance(val, str) and val:
                return val
            if i < tries - 1:
                time.sleep(0.8 * (i + 1))
        return val if isinstance(val, str) else ""

    def _current_url(self):
        val = self._eval("window.location.href")
        return val if isinstance(val, str) else ""

    def _title(self):
        val = self._eval("document.title")
        return val if isinstance(val, str) else ""

    @classmethod
    def _session_dead(cls, msg):
        m = (msg or "").lower()
        return any(k in m for k in SESSION_DEAD_MARKS)

    # ── 标签固定（每站只用一个标签，绝不新建）─────────────────
    def ensure_tab(self, log=None):
        """取得/复用本站独占标签（import 延迟以避免循环依赖）。返回是否成功。"""
        import shared_chrome as sc
        try:
            tab, pin = sc.acquire_tab(self.port, self.BASE_URL or "about:blank",
                                      self.HOME_DOMAIN, self.pin_tab_id, log or self._log)
            if tab:
                self.pin_tab_id = pin or tab.get("id")
                self._ws_cache = tab.get("webSocketDebuggerUrl") or ""
                return True
        except Exception as e:
            self._log(f"⚠ 取得标签失败：{str(e).splitlines()[0][:120]}")
        return False

    def start_browser(self, headed=True):
        """保证共享 Chrome 在跑并取得本站标签，然后导航到首页建立会话。幂等。"""
        import shared_chrome as sc
        if not sc.is_alive(self.port):
            if not sc.ensure_shared_chrome(self._log, self.port, headed):
                return False
        if not self.ensure_tab():
            return False
        if self.BASE_URL:
            self._goto(self.BASE_URL, expect_domain=self.HOME_DOMAIN or None, tries=2)
        return True

    def close_browser(self):
        """收工：只关自己那个标签，共享 Chrome 保留（下次直接复用）。"""
        import shared_chrome as sc
        try:
            if self.pin_tab_id:
                sc.close_tab(self.port, self.pin_tab_id)
        except Exception:
            pass
        self.pin_tab_id = ""
        self._ws_cache = ""

    def _is_session_alive(self):
        try:
            return bool(self._ws_url()) and self._eval("1") is not None
        except Exception:
            return False

    def try_restore_session(self):
        """标签/浏览器被关后自愈：确保 Chrome 在跑、重取本站标签。"""
        import shared_chrome as sc
        self.pin_tab_id = ""
        self._ws_cache = ""
        if not sc.is_alive(self.port):
            if not sc.ensure_shared_chrome(self._log, self.port, True):
                return False
        return self.ensure_tab()

    # ── 导航（Page.navigate + 回读 location 断言 + 就绪判据）───
    def _cdp_navigate(self, url):
        try:
            self._send("Page.enable", {}, timeout=8)
        except Exception:
            pass
        try:
            self._send("Page.navigate", {"url": url}, timeout=20)
            return True
        except Exception as e:
            if self._session_dead(str(e)):
                raise
            # Page.navigate 偶尔失败，退回 JS 赋值导航
            try:
                self._eval(f"window.location.href={json.dumps(url)}")
                return True
            except Exception:
                return False

    def _page_state(self):
        val = self._eval(
            "JSON.stringify({rs:document.readyState,url:location.href,"
            "len:(document.body?document.body.innerText.length:0)})")
        if not isinstance(val, str):
            return None
        try:
            return json.loads(val)
        except Exception:
            return None

    def _wait_ready(self, expect_domain=None, timeout=22):
        """就绪判据用可观测的真实指标：readyState + 目标域名 + 正文规模，
        绝不用某个框架特有容器节点是否存在（不同站点/版本可能压根没有）。"""
        deadline = time.time() + timeout
        last = None
        while time.time() < deadline:
            if self._stop_requested:
                return False
            st = self._page_state()
            if st:
                last = st
                rs_ok = st.get("rs") in ("complete", "interactive")
                dom_ok = (not expect_domain) or (expect_domain in (st.get("url") or ""))
                len_ok = int(st.get("len") or 0) >= self.MIN_PAGE_CHARS
                if rs_ok and dom_ok and len_ok:
                    return True
                # 域名已到、正文虽短但已 complete：可能是空结果页，也放行交给上层判定
                if rs_ok and dom_ok and st.get("rs") == "complete" and int(st.get("len") or 0) > 0:
                    time.sleep(0.4)
                    st2 = self._page_state()
                    if st2 and int(st2.get("len") or 0) == int(st.get("len") or 0):
                        return True
            time.sleep(0.5)
        return bool(last and last.get("rs") == "complete")

    def _goto(self, url, expect_domain=None, tries=3):
        """导航到 url，回读 location 断言域名命中才算成功；命中拦截则等人工过验证。
        标签/浏览器死掉则自愈重取标签续采，绝不让异常冒泡崩掉采集线程。"""
        backoff = (1.5, 3, 6)
        for i in range(tries):
            if self._stop_requested:
                return False
            try:
                if not self._ws_url():
                    if not self.ensure_tab():
                        raise RuntimeError("no tab")
                self._cdp_navigate(url)
            except Exception as e:
                msg = str(e).splitlines()[0][:160] if str(e) else e.__class__.__name__
                if self._session_dead(msg):
                    self._log(f"⚠ 标签/浏览器会话断开（{msg[:50]}），正在重取标签续采…（{i+1}/{tries}）")
                    if not self.try_restore_session():
                        self._log("❌ 无法恢复浏览器会话，本轮结束（已采数据已保存）")
                        self._stop_requested = True
                        return False
                    continue
                self._log(f"导航异常（{i+1}/{tries}）：{msg} → 重试")
                time.sleep(backoff[min(i, len(backoff) - 1)])
                continue
            dom = expect_domain or self._url_domain(url)
            if not self._wait_ready(dom):
                # 未就绪：可能被拦、可能网络慢，先看拦截再决定重试
                self._mark_activity()
                block = self._detect_block()
                if block:
                    if not self._wait_unblock(block):
                        return False
                    continue
                if i < tries - 1:
                    time.sleep(backoff[min(i, len(backoff) - 1)])
                    continue
                # 最后一次仍未就绪但页面有内容：放行让上层解析（宁可得部分数据）
                if self._html():
                    return True
                return False
            block = self._detect_block()
            if block:
                if not self._wait_unblock(block):
                    return False
                continue
            self._mark_activity()
            return True
        return False

    @staticmethod
    def _url_domain(url):
        m = re.match(r"https?://([^/]+)", url or "")
        return m.group(1) if m else None

    # ── 拦截 / 验证码判定 + 防采看门狗 ────────────────────────
    # 全屏/弹层式验证码控件（滑块、人机验证）。只认「尺寸足够大且落在视口内」的已知控件：
    # 阈值 150×40 是为了容纳 Cloudflare Turnstile 嵌入式勾选框（实测 300×65）等小尺寸控件。
    # 旧阈值 220×90 会把 Turnstile 漏检，导致页面被误判成「纯自动挑战页」而反复刷新，
    # 把用户刚点的验证进度清零（2026-11-05 麦麦B2B/1688 复现）。
    # 商铺页自带的「采购限价」表单里也有验证码输入图（实测 gys 商铺页有 id=codeCaptcha 的
    # img），但那些 img 不会命中的 iframe/class 选择器，所以不会误判。
    CAPTCHA_WIDGET_JS = """(function(){
function ok(e){if(!e)return false;var r=e.getBoundingClientRect();
return r.width>=150&&r.height>=40&&r.bottom>0&&r.top<(window.innerHeight||900)*0.9;}
var s=['#tencent-captcha','.tcaptcha-container','.tcaptcha-body','.tcaptcha-transform',
'iframe[src*="captcha"]','iframe[src*="verify"]','iframe[src*="challenge"]',
'iframe[src*="turnstile"]','.cf-turnstile','#challenge-stage',
'.geetest_panel','.geetest_holder','#geetest_wrap','.slider-verify','.verify-wrap',
'.captcha-container','.captcha-box','#captcha-div','.nc-container'];
for(var i=0;i<s.length;i++){try{var e=document.querySelector(s[i]);
if(e&&ok(e))return s[i];}catch(x){}}
return '';})()"""

    def _detect_block(self):
        """以「URL 特征 + 标题特征 + 正文异常小」为判据，绝不对整页正文做关键词匹配
        （正常详情页里常内嵌『请输入验证码/访问过于频繁』等前端校验 JS 文案，全文匹配会误杀）。"""
        try:
            url = (self._current_url() or "").lower()
            title = self._title() or ""
        except Exception:
            return ""
        if any(m in url for m in self.BLOCK_URL_MARKERS):
            return f"页面被跳转到安全验证/风控页（{url[:40]}）"
        st = self._page_state() or {}
        body_len = int(st.get("len") or 0)
        html_len = 0
        if body_len < self.SMALL_PAGE_CHARS:
            html = self._html()
            html_len = len(html)
            # HTTP 级拦截页/极小插页：正文与 HTML 都很小，且命中标题或正文拦截词
            if html_len < self.SMALL_PAGE_CHARS * 3:
                if any(m in title for m in self.BLOCK_TITLE_MARKERS) and \
                        not any(h in title for h in self.SAFE_TITLE_HINTS):
                    return f"出现验证/拦截页：{title[:24]}"
                low = (title + " " + (self._eval("document.body?document.body.innerText:''") or ""))
                if any(m in low for m in self.BLOCK_BODY_MARKERS):
                    return "页面被风控拦截（验证/访问频繁）"
        # 正文规模正常但页面上盖着滑块/人机验证（正常页正文很大、旧判据看不见）：
        # 实测供应商网搜索结果页就是这样——不认这一道就永远不弹提醒，整轮一直空转。
        try:
            hit = self._eval(self.CAPTCHA_WIDGET_JS)
        except Exception:
            hit = ""
        if isinstance(hit, str) and hit:
            return f"页面弹出验证控件（{hit[:24]}）"
        return ""

    def http_block_reason(self, status, html="", url="", headers=None):
        """裸 HTTP 响应是否防采/限流插页：状态码命中，或体积极小且带插页特征词。
        404 不在此列（商铺真失效是终态数据，不是风控）。命中时记下探测页，
        让看门狗之后就是探这一页能不能真拿到数据。

        同时做**拦截分诊**（决定后面是人过还是干等），这两类处置完全相反：
          · "ip"      429/502/503/520… 且插页里没有验证特征 = 服务端直接拒这个出口 IP。
            页面里根本没滑块，叫人工去 Chrome 里找入口只会白等（实测八方 502、
            供应商网 520 都是这样）→ 不拉浏览器，拉长间隔指数退避等冷却；
          · "captcha" 插页里带人机验证特征（验证词/captcha/challenge/v.js 这类 JS 挑战）
            = 人在浏览器里过一下确实能清 → 借标签把页面递到眼前，过完抄 clearance Cookie 续采。
            实测：顺企网 429 返的是 70 字节 v.js 挑战（浏览器执行 JS 就能过），归 captcha 才对；
            上一版按状态码一律归 ip，会把这种「其实能过」的拦截误杀成只能干等。
        服务端给了 Retry-After 就记下来，冷却时长听它的而不是我们猜。"""
        try:
            code = int(status or 0)
        except (TypeError, ValueError):
            code = 0
        body = html or ""
        if code in self.HTTP_BLOCK_STATUS:
            if url:
                self._block_probe_url = url
            self._block_kind = "captcha" if self._looks_like_challenge(body) else "ip"
            self._retry_after = self._parse_retry_after(headers)
            if self._block_kind == "captcha":
                # 文案刻意不出现“HTTP”字样：上层在没拿到分诊结果时会拿它兜底判
                # 「IP 限流」，带上这个词就会把「其实能人工过」的插页误杀成干等
                return f"站点返 {code} 且是人机验证插页（可在浏览器里手动过）"
            return f"站点返回 HTTP {code}（IP 级限流/冷却，非验证码）"
        if body and len(body) < self.HTTP_BLOCK_MAX_BYTES:
            hit = next((m for m in self.HTTP_BLOCK_MARKS if m in body[:4000]), "")
            if hit:
                if url:
                    self._block_probe_url = url
                self._block_kind = "captcha" if self._looks_like_challenge(body) else "ip"
                return f"防采插页（{hit}）"
        return ""

    def _looks_like_challenge(self, body):
        """插页里是不是真的摆着可人工过的验证（而不是纯粹拒 IP）。
        词表同时收中文文案与英文/脚本特征（v.js、cloudflare、sufei 这类 JS 挑战）：
        请求体不执行 JS 的链路拿到挑战页也只是“200/429 + 一小段 script”，
        不认这一道就会把「人过一下就能清」误判成「只能干等」。"""
        low = (body or "")[:4000].lower()
        return any(m in low for m in self.HTTP_CHALLENGE_MARKS)

    @staticmethod
    def _parse_retry_after(headers):
        """读 HTTP 标准的 Retry-After（秒数形式），拿不到就回 0。"""
        try:
            return max(0, int(float((headers or {}).get("Retry-After") or 0)))
        except (TypeError, ValueError):
            return 0

    def probe_block(self):
        """看门狗恢复探测：返回「仍被拦的原因」，空串＝已解除。
        · 需要浏览器的站看标签当前页；
        · 纯 HTTP 站拿被拦那一页（没有则首页）做**能力探测**：必须真拿得到正常页
          才算解除——只看状态码会把挑战页当已通，探一次就谎报恢复。"""
        if getattr(self, "NEEDS_BROWSER", True):
            return self._detect_block()
        url = self._block_probe_url or self.BASE_URL
        if not url:
            return self._block_reason or "站点仍在防采/限流状态"
        try:
            jar = getattr(self, "_session", None)
            headers = getattr(self, "PROBE_HEADERS", None) or None
            r = (jar or requests).get(url, timeout=20, headers=headers)
            if r.status_code == 200 and len(r.text or "") >= self.HTTP_BLOCK_MAX_BYTES \
                    and not self.http_block_reason(r.status_code, r.text):
                self._block_reason = ""
                return ""
        except Exception:
            pass
        return self._block_reason or "站点仍在防采/限流状态"

    def open_verify_page(self, url=""):
        """把需要人工过的验证页递到共享 Chrome 眼前：
        需要浏览器的站用自己已有的标签；纯 HTTP 站平时不占标签，这里临时借一个，
        过完验证由 _release_verify_tab 归还（不影响它平时「不开标签」的采集方式）。"""
        target = url or self.BASE_URL
        if not target:
            return False
        import shared_chrome as sc
        try:
            if not sc.is_alive(self.port) and \
                    not sc.ensure_shared_chrome(self._log, self.port, True):
                self._log("⚠ 无法启动共享浏览器，只能干等站点限流自行冷却")
                return False
            # 纯 HTTP 站（如八方）把 ensure_tab 覆盖成了空壳（采集不需要标签），
            # 但“借页给人看”必须真有一个标签，所以这里明确走基类实现。
            if not CdpSiteCrawler.ensure_tab(self):
                return False
            if not getattr(self, "NEEDS_BROWSER", True):
                self._borrowed_tab = True
            # 直接导航，不走 _goto：_goto 命中拦截又会叫看门狗，会递归
            self._cdp_navigate(target)
            self._wait_ready(None, timeout=15)
            self._show_tab_window()
            return True
        except Exception as e:
            self._log(f"⚠ 打开验证页失败：{str(e).splitlines()[0][:100]}")
            return False

    def _show_tab_window(self):
        """还原标签所在窗口，别让用户对着最小化的窗口过验证。"""
        try:
            import shared_chrome as sc
            if self.pin_tab_id:
                sc.set_tabs_window_state([self.pin_tab_id], "normal", self.port)
        except Exception:
            pass

    # 自动挑战页特征：Cloudflare 质询页的标题/URL 写法。这类页面人点不了任何东西。
    AUTO_CHALLENGE_MARKS = ("just a moment", "请稍候", "attention required",
                            "checking your browser", "__cf_chl", "cf-chl", "challenge-solving")

    def _auto_challenge_page(self):
        """标签当前是否停在「纯自动挑战页」：只有转圈动画、没有任何可点控件的插页。
        这种页面人做不了任何动作，刷新是唯一能推进的操作；而带滑块/验证码控件的页面
        一律不算——那种页面一刷新就把用户正在拖的滑块重置掉了（绝对不行）。"""
        try:
            if self._eval(self.CAPTCHA_WIDGET_JS):
                return False
            text = ((self._current_url() or "") + " " + (self._title() or "")).lower()
            if any(m in text for m in self.AUTO_CHALLENGE_MARKS):
                return True
            st = self._page_state() or {}
            dom = (getattr(self, "HOME_DOMAIN", "") or "").lower()
            # 地址已经落在本站、正文却几乎是空的：插页把真页面挡住了
            return bool(dom) and dom in (st.get("url") or "").lower() \
                and int(st.get("len") or 0) < 200
        except Exception:
            return False

    def _cdp_open(self, timeout=15):
        """为「一组必须连着做完的 CDP 命令」开一条专用连接（见 _frames_eval）。"""
        ws_url = self._ws_url()
        if not ws_url:
            raise RuntimeError("no target tab websocket")
        return websocket.create_connection(ws_url, timeout=timeout)

    def _frames_eval(self, js, timeout=15):
        """把同一段 JS 依次送进主文档和每一个 iframe（含 challenges.cloudflare.com
        那种跨域帧）执行，返回 [(帧URL, 结果), …]。
        必须一条连接里做完：Page/Runtime 域是按连接启用的，isolated world 的上下文
        也只属于创建它的那条连接——先取帧树、再另开连接执行会「找不到上下文」。
        之前用 _send 分三条命令查帧树，实测直接被拒（Page domain is not enabled），
        所以点不到插页里的「刷新此页面」。"""
        out = []
        try:
            ws = self._cdp_open(timeout)
        except Exception:
            return out
        seq = [0]

        def call(method, params=None, wait=6):
            seq[0] += 1
            mid = seq[0]
            try:
                ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
            except Exception:
                return {}
            deadline = time.time() + wait
            while time.time() < deadline:
                try:
                    raw = ws.recv()
                except Exception:
                    break
                if not raw:
                    continue
                try:
                    msg = json.loads(raw)
                except Exception:
                    continue
                if msg.get("id") == mid:
                    return msg.get("result") or {}
            return {}

        try:
            call("Page.enable")
            call("Runtime.enable")
            tree = call("Page.getFrameTree").get("frameTree") or {}
            frames = []

            def walk(node, depth=0):
                f = node.get("frame") or {}
                if f.get("id"):
                    frames.append((f["id"], f.get("url") or "", depth))
                for ch in node.get("childFrames") or []:
                    walk(ch, depth + 1)

            walk(tree)
            for fid, furl, depth in frames:
                params = {"expression": js, "returnByValue": True}
                if depth:                     # 跨域帧：给它单独建一个可执行的上下文
                    ctx = call("Page.createIsolatedWorld",
                               {"frameId": fid, "worldName": "b2bassist"}
                               ).get("executionContextId")
                    if not ctx:
                        continue
                    params["contextId"] = ctx
                v = call("Runtime.evaluate", params).get("result", {}).get("value")
                if v not in (None, "", False, 0):
                    out.append((furl, v))
        except Exception:
            pass
        finally:
            try:
                ws.close()
            except Exception:
                pass
        return out

    def _click_in_frames(self, js):
        """先主文档找、找不到再逐帧（iframe）找并执行。
        Cloudflare 的插页常把内容放在 challenges.cloudflare.com 的跨域 iframe 里，
        主文档 JS 碰不到它（跨域），但 CDP 可以：Page.createIsolatedWorld 拿到那个帧的
        执行上下文，再在那个上下文里跑同一段 JS。点到就回元素文本，没点到回空串。"""
        try:
            hit = self._eval(js)
            if hit:
                return str(hit)
        except Exception:
            pass
        for _furl, v in self._frames_eval(js):
            if v:
                return str(v)
        return ""

    def _cdp_cmds(self, cmds, timeout=10):
        """在同一条 CDP 连接上按顺序发完一串命令。
        鼠标按下/抬起、以及「先看 Cookie 再删 Cookie」都得走同一条连接：
        分开连接在 CDP 里是两个独立会话，不算一次完整动作。"""
        ws = self._cdp_open(timeout=timeout)
        try:
            out = []
            for mid, (method, params) in enumerate(cmds, 1):
                ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
                deadline = time.time() + timeout
                got = {}
                while time.time() < deadline:
                    raw = ws.recv()
                    if not raw:
                        continue
                    msg = json.loads(raw)
                    if msg.get("id") == mid:
                        got = msg.get("result") or {}
                        break
                out.append(got)
            return out
        finally:
            try:
                ws.close()
            except Exception:
                pass

    def _turnstile_widget(self):
        """页面上有没有摆着要人点的验证控件，有就回它的屏幕位置（跨域不影响坐标）。"""
        try:
            hit = self._eval(self.TURNSTILE_WIDGET_JS)
            return json.loads(hit) if hit else None
        except Exception:
            return None

    def _click_turnstile_at(self, g):
        """按控件几何位置发一次「真鼠标」点击点到勾选框上。
        为什么不能用 JS 点：Cloudflare 只认可信事件（isTrusted），element.click() 发出去的是
        非可信事件，它直接忽略（这就是程序对着勾选框什么也推不动的原因）；而鼠标事件按
        屏幕坐标发，跟控件在跨域 iframe 里无关。实测 2026-11-03 麦麦B2B：
        人工点了也回不来的那种循环，坐标点击＋清掉本站通行证才能把循环打断。"""
        try:
            x = float(g.get("x") or 0) + getattr(self, "TURNSTILE_CLICK_X", 30)
            y = float(g.get("y") or 0) + float(g.get("h") or 60) / 2.0
            self._cdp_cmds([("Input.dispatchMouseEvent",
                             {"type": t, "x": x, "y": y})
                            if t == "mouseMoved" else
                            ("Input.dispatchMouseEvent",
                             {"type": t, "x": x, "y": y, "button": "left", "clickCount": 1})
                            for t in ("mouseMoved", "mousePressed", "mouseReleased")])
        except Exception:
            return False
        self._log(f"   🖱 页面上摆着要人点的验证勾选框，已按坐标替用户点一下"
                  f"（控件 {int(g.get('w') or 0)}×{int(g.get('h') or 0)}）")
        return True

    def _purge_block_cookies(self, url=""):
        """删掉本站的 Cloudflare 通行证与挑战计数（cf_clearance / cf_chl_*）。
        实测依据（2026-11-03）：陷在验证循环里的那个共享配置里 cf_chl_rc_ni=11
        （＝这份会话已经重复挑战 11 次），而同一时间、同一网络、同一台机器上另开一份
        干净配置打开同一个页面是秒过、连验证页都不出——循环是跟着这份会话 Cookie 走的。
        所以卡到保底时间要做的不是「拿同一张废通行证再刷一次」，而是先把本站 cf_* 清掉
        再重开这一页。"""
        probe = url or self._block_probe_url or self.BASE_URL
        if not probe:
            return False
        try:
            got = self._cdp_cmds([("Network.getCookies", {"urls": [probe]})])[0]
            cookies = got.get("cookies") or []
        except Exception:
            return False
        cmds = [("Network.deleteCookies", {"name": c.get("name"), "domain": c.get("domain"),
                                          "path": c.get("path") or "/"})
                for c in cookies if str(c.get("name") or "").startswith("cf_")]
        if not cmds:
            return False
        try:
            self._cdp_cmds(cmds)
        except Exception:
            return False
        self._log(f"   🧹 已清掉本站 {len(cmds)} 张 Cloudflare 通行证与挑战计数"
                  f"（会话已反复验证 {len(cmds)} 次以上，拿旧通行证再刷只会一直循环）")
        return True

    def _refresh_stalled_challenge(self, waited=0, target=""):
        """到点了就推进验证页，别让整轮采集干等在一个不推进的页面上。
        三种页分开处置（2026-11-02~03 麦麦B2B 实机各踩过一次）：
          · 插页上有「刷新此页面」→ 点它（拿着现有令牌再试一次，不重开挑战）；
          · 页面上摆着要人点的验证勾选框（Turnstile）→ 绝不刷新、绝不整页重载：
            那会把用户（或上一次点击）刚积累的进度清零，实测就是「人工点了还是回到这一页」；
            改成按坐标发一次可信鼠标点击替用户点它；
          · 只有转圈、什么都没得点 → 放着让它自己跑（实测不动它 10~50 秒会放行，
            而 8 秒重载一次连着几次、248 秒还停在「Just a moment…」）；
            卡到保底时间才「清掉本站通行证 + 重开这一页」。
        target：调用方明确要拿哪一页时传进来。重开必须回那一页，不能拿 _block_probe_url
        或站点首页顶替——借标签取页的调用方会把重开后的页面当成「我要的那一页」收走，
        那样就采到别的页的数据了（自审发现，2026-11-03）。"""
        if not getattr(self, "BLOCK_AUTO_REFRESH", False) or not self.pin_tab_id:
            return False
        # 先认定「这一页是插页」再动手：真页面上也可能摆着验证码控件（商铺表单里那种），
        # 不能因为看得到控件就去点它。
        if not self._auto_challenge_page():
            return False
        widget = self._turnstile_widget()
        self._show_tab_window()
        if widget:
            # 有真人勾选框：只点，绝不刷新、绝不整页重载（那会把刚积累的进度清零）。
            # 也不连着点：点一下之后 CF 自己要跑 5~20 秒，这期间再点等于重开一轮。
            now = time.time()
            if now - getattr(self, "_turnstile_last_click", 0.0) < \
                    getattr(self, "TURNSTILE_CLICK_COOLDOWN", 30):
                return False
            self._turnstile_last_click = now
            return self._click_turnstile_at(widget)
        clicked = self._click_in_frames(self.CHALLENGE_REFRESH_JS)
        if clicked:
            self._log(f"   🔄 验证页卡住不动，已点页面上的「{clicked[:12]}」推进（并把浏览器窗口带到前台）")
            return True
        if waited < getattr(self, "BLOCK_RELOAD_LAST_RESORT", 90) or self._reloaded_once:
            # 自动挑战页自己会跑完，动它反而更慢：这一拍只把窗口带回前台，继续等
            return False
        target = target or self._block_probe_url or self.BASE_URL
        if not target:
            return False
        try:
            self._reloaded_once = True
            self._purge_block_cookies(target)
            self._cdp_navigate(target)
            self._wait_ready(None, timeout=15)
            self._log("   🔄 验证页长时间毫无进展、页面上也没可点入口：清掉本站通行证后重开这一页")
            return True
        except Exception:
            return False

    def _release_verify_tab(self):
        """归还为过验证临时借的标签（本站自己的标签不动）。"""
        if not self._borrowed_tab:
            return
        self._borrowed_tab = False
        import shared_chrome as sc
        try:
            if self.pin_tab_id:
                sc.close_tab(self.port, self.pin_tab_id)
        except Exception:
            pass
        self.pin_tab_id = ""
        self._ws_cache = ""

    def _harvest_browser_cookies(self):
        """把标签里过验证拿到的 clearance cookie 抄给裸 HTTP 会话（CDP Storage.getCookies，
        含 httpOnly）：纯 HTTP 站靠它才能回到不占标签的快路上（见 mic_crawler 同源做法）。
        返回抄回的条数。"""
        dom = getattr(self, "BLOCK_COOKIE_DOMAIN", "")
        jar = getattr(self, "_session", None)
        if not dom or jar is None or not self.pin_tab_id:
            return 0
        try:
            resp = self._send("Storage.getCookies", timeout=10)
            cks = [c for c in (resp or {}).get("result", {}).get("cookies", [])
                   if dom in (c.get("domain") or "")]
            n = 0
            for c in cks:
                try:
                    jar.cookies.set(c["name"], c.get("value", ""),
                                    domain=c.get("domain"), path=c.get("path") or "/")
                    n += 1
                except Exception:
                    pass
            if n:
                self._log(f"   🍪 已把浏览器过验证后的 {n} 条 {dom} cookie 抄给 HTTP 会话")
            return n
        except Exception:
            return 0

    def handle_http_block(self, url="", reason=""):
        """裸 HTTP 链路命中防采/限流的统一处置：弹提醒 → （确实可人工过的站）
        借浏览器把验证页送到用户眼前 → 看门狗轮询到恢复才返回 True，调用方据此重试同一个请求。
        IP 级限流不拉浏览器（那一页本身就没滑块，开一个也只会多一个被打的出口）。
        返回 False 表示没恢复（超时/熔断/用户停止），上层按非终态「待重试」记账。"""
        if url:
            self._block_probe_url = url
        self._block_reason = reason or self._block_reason or "站点防采/限流插页"
        if getattr(self, "BLOCK_OPEN_BROWSER", True) and \
                getattr(self, "_block_kind", "") != "ip":
            self.open_verify_page(url)
        return self._wait_manual_unblock(self._block_reason, self.probe_block,
                                         on_cleared=self._after_block_cleared)

    def _after_block_cleared(self):
        """过完验证的收尾：抄 cookie 给 HTTP 会话，归还临时借的标签。"""
        self._harvest_browser_cookies()
        self._block_reason = ""
        self._release_verify_tab()

    def _register_block(self):
        now = time.time()
        self._block_times = [t for t in self._block_times if now - t < self._block_window]
        self._block_times.append(now)
        return len(self._block_times)

    def _interruptible_sleep(self, seconds):
        end = time.time() + seconds
        while time.time() < end:
            if self._stop_requested:
                return
            if self._block_wake.is_set():      # 被拦冷却中用户换了网络：不等满冷却也继续
                self._block_wake.clear()
                self._log("   🔁 你点了「马上继续」，跳过剩余冷却直接接着采")
                return
            time.sleep(0.5)

    def retry_now(self):
        """用户点了「我换好网络了，马上继续」：立刻重新探一次，不等本轮退避到点。

        为什么要把这一步交给人：IP 级限流唯一能立见成效的手段就是换一个出口 IP，
        而工具自己没法重拨宽带/连热点，所以只能由人做那一下，剩下的全部自动。
        同时把被拦计数与自动降速清零：他已经换了新出口 IP，不该再按旧 IP 的黑历史
        熔断（不然第 3 次一到就把本站收掉了）。返回是否正处在等待中。"""
        waiting = bool(self._blocked)
        self._block_times = []
        self._current_delay = self.delay_between
        self._retry_after = 0
        self._block_wake.set()
        self._log("   🔁 收到「马上继续」：已重置本站被拦计数与采集间隔，马上再试一次"
                  + ("" if waiting else "（本站当前没在等放行，正常采集不受影响）"))
        return waiting

    def _wait_manual_unblock(self, reason, probe, max_wait=None, on_cleared=None):
        """看门狗主循环。两类拦截走两套节奏，不再一律“叫人工”：

        · captcha（可人工解除的验证页）：弹提醒 + （能开页的站）把页面递到眼前，
          每 BLOCK_POLL 探一次，用户秒过时不用白等；
        · ip（IP 级限流）：**绝不叫人工**——页面里根本没有滑块，人对着 Chrome 找入口
          只会白等（实测供应商网 520 等了 8 分钟）；探测间隔从 BLOCK_POLL_IP 起指数退避，
          让 IP 真拿到冷却窗口（旧实现固定 15s 探一次，等于自己把冷却打断）；
          服务端给了 Retry-After 就先睡够它再探。
        反复被拦（窗口内达阈值）直接优雅结束本站本轮，不再对着插页浪费机会。"""
        kind = getattr(self, "_block_kind", "")
        # 分诊结果优先：http_block_reason 真看过响应体，比猜文案可靠得多。
        # 只有没经过分诊的拦截（浏览器链路自己发现的验证控件）才退回旧启发式：
        # reason 里带 HTTP 字样，或本站压根借不到标签给人过验证。
        if kind:
            ip_mode = kind == "ip"
        else:
            ip_mode = ("HTTP" in (reason or "")) or not getattr(self, "BLOCK_OPEN_BROWSER", True)
        self._ip_cooldown = ip_mode
        self._block_kind = "ip" if ip_mode else "captcha"
        strikes = self._register_block()
        self._blocked = True
        # 前缀是给界面分型用的（界面不能再靠猜“reason 里有没有 HTTP”来定文案）
        tag = BLOCK_TAG_IP if ip_mode else BLOCK_TAG_CAP
        cap = self._max_blocks_ip if ip_mode else self._max_blocks
        if strikes >= cap:
            self._emit("blocked_signal", f"{tag}{self.PLATFORM_NAME}：{reason}")
            self._log(f"🛑 {reason}")
            self._log(f"   ⛔ {self._block_window // 60} 分钟内第 {strikes} 次被拦，判定持续限流。"
                      f"为保护 IP，{self.PLATFORM_NAME} 本轮到此为止（已采数据已保存）。")
            if ip_mode:
                self._log(f"   💡 这不是验证码，人工点不掉。要恢复只能：①重拨宽带/换网络换个出口 IP（最快）；"
                          f"②等 ≥30 分钟再跑；③把「采集间隔」调大（建议 8~15 秒）。已采数据不丢，下次从断点续采。")
            self._stop_requested = True
            self._blocked = False
            self._after_block_cleared()
            return False
        self._emit("blocked_signal", f"{tag}{self.PLATFORM_NAME}：{reason}")
        self._log(f"🛑 {reason}")
        if ip_mode:
            self._log("   已暂停推进（不写入空记录）。这是 IP 级限流/冷却，没有验证码可点也不需要你操作，"
                      "工具会拉长间隔实测，站点放行后自动续采。")
        else:
            self._log("   已暂停推进（不写入空记录）。请在 Chrome 窗口里手动过验证，"
                      "看门狗探测到验证已解除后会自动续采。")
        self._current_delay = min(30.0, max(6.0, self._current_delay * 1.6))
        self._log(f"   自动降速：采集间隔上调为约 {self._current_delay:.1f}s")
        # max_wait 要显式判 None：写成 `max_wait or 默认` 时，调用方传 0
        # （＝「不要等，按当前判定立刻收」）会被 or 吞成默认上限，意图整个失效。
        # 实机证据：debug/gi_mp_offline_check.py 第 399 行传 0 只验文案，却按新的
        # 静默节奏真等了一轮探测间隔，把整条离线回归卡在那里十几分钟。
        limit = (self.BLOCK_MAX_WAIT_IP if ip_mode else self.BLOCK_MAX_WAIT)
        if max_wait is not None:
            limit = max_wait
        waited = 0
        if ip_mode:
            # 指数退避：同一轮里被拦次数越多，下一轮探得越久，给 IP 真正的冷却窗口
            gap = max(10, int(min(self.BLOCK_POLL_IP_MAX,
                                  self.BLOCK_POLL_IP * (2 ** max(0, strikes - 1)))))
            first = max(gap, int(self._retry_after or 0))
            if first > gap:
                self._log(f"   ⏳ 服务端提示 Retry-After {first}s，先等足这么久再探")
        else:
            gap = max(3, int(self.BLOCK_POLL))
            first = 5                 # 首轮探短一点：用户秒过验证时不用白等一个轮询周期
        next_refresh = self.BLOCK_REFRESH_EVERY
        self._reloaded_once = False          # 本轮被拦只允许一次保底重载（见上面实测）
        refreshes = 0
        net_hits = 0              # 连续「本机网络不通」探测次数（归因要与站点拦截分开）
        while waited < limit:
            nap = first if waited == 0 else gap
            # 分段短睡：停止/暂停指令能及时响应；用户点了「马上继续」也在这里醒
            woke = False
            for _ in range(int(nap * 2)):
                if self._stop_requested:
                    self._blocked = False
                    self._after_block_cleared()
                    return False
                if self._block_wake.is_set():
                    self._block_wake.clear()
                    woke = True
                    break
                time.sleep(0.5)
            if woke:
                # 他刚换了出口 IP，不等本轮退避到点，立刻真探一次（探不通也没关系：
                # retry_now 已清过被拦计数，不会被误算成第 3 次而熔断）
                self._log("   🔁 收到「马上继续」，不等本轮退避，立刻重测")
            else:
                waited += nap
            first = gap
            if self._pause_requested:
                continue
            try:
                still = probe()
            except Exception as e:
                still = f"探测异常：{str(e).splitlines()[0][:60]}"
            # 探针带 NET_FAIL_TAG 前缀＝本机网络层就没通（断网/DNS/连接被重置），
            # 与「站点在拦」是两本账：文案、超时归因都按这个分叉走。
            if str(still).startswith(NET_FAIL_TAG):
                net_hits += 1
            else:
                net_hits = 0
            if not still:
                return self._mark_block_cleared(waited, on_cleared)
            # 可人工过的验证页：等了一会儿仍是插页，就主动刷新推一把（见 BLOCK_AUTO_REFRESH）
            if not ip_mode and waited >= next_refresh and refreshes < self.BLOCK_REFRESH_MAX:
                refreshes += 1
                next_refresh += self.BLOCK_REFRESH_EVERY
                self._refresh_stalled_challenge(waited)
            if waited % 60 < gap or ip_mode:
                # 断网不是站点在拦：探针带 NET_FAIL_TAG 前缀就换一句归因，
                # 并且不能继续按「被站点拦」的账算（上面 strikes 已经记过一次，
                # 长静默下不会连着触发，这里先把话说对）。
                if str(still).startswith(NET_FAIL_TAG):
                    word = "本机网络没通（不是站点在拦），等网络恢复后自动续采"
                else:
                    word = "仍在等站点放行（IP 冷却，无需操作）" if ip_mode else "仍在等待验证"
                self._log(f"   {word}…已等 {waited} 秒（{str(still)[:60]}）")
        if ip_mode and net_hits:
            # 断网不能记到站点头上（2026-11-04 09:36 实机：断网期间一路写
            # 「IP 冷却，无需操作」，收工又写「等 IP 冷却超时」，用户以为站点封了他
            # 这台电脑，其实是公司网络断了——两条都得说成网络账才知道该做什么）。
            self._log(f"⚠ 本机网络仍未恢复（{limit} 秒内每次探测都是连接失败，"
                      f"不是站点在拦），本站本轮结束（已采数据不受影响）。"
                      f"网络恢复后重新开始任务会自动续采。")
        elif ip_mode:
            self._log(f"⚠ 等 IP 冷却超时（{limit} 秒），本站本轮结束（已采数据不受影响）。"
                      f"想继续可重拨宽带换个出口 IP，或调大采集间隔后重跑。")
        else:
            self._log(f"⚠ 等待验证超时（{limit} 秒），本站本轮结束（已采数据不受影响）")
        self._blocked = False
        self._after_block_cleared()
        return False

    def _mark_block_cleared(self, waited, on_cleared=None):
        """探测到已解除：发信号让界面收起提醒，必要时先给 IP 冷却窗口再继续。"""
        self._blocked = False
        word = "站点放行" if getattr(self, "_ip_cooldown", False) else "验证已解除"
        self._log(f"✅ {word}（等待约 {waited} 秒），自动续采")
        self._retry_after = 0
        try:
            if on_cleared:
                on_cleared()
        except Exception:
            pass
        self._emit("block_cleared_signal", self.PLATFORM_NAME)
        recent = len([t for t in self._block_times if time.time() - t < self._block_window])
        if recent >= 2:
            cool = min(1800, 120 * (2 ** (recent - 1)))
            self._log(f"   ❄ 已第 {recent} 次被拦，强制冷却 {cool} 秒再继续（给 IP 冷却窗口，"
                      f"且本轮保持降速不回落）")
            self._interruptible_sleep(cool)
        return True

    def _wait_unblock(self, reason, max_wait=1800):
        """浏览器链路被拦：原地等人工过验证，过验后自动继续（看门狗见 _wait_manual_unblock）。"""
        return self._wait_manual_unblock(reason, self._detect_block, max_wait=max_wait)

    def _mark_activity(self):
        """记账「一次真实网络请求/导航刚完成」：裸 HTTP 子类取页后也要调。"""
        self._last_activity = time.time()

    def _sleep(self, mult=1.0):
        """请求间隔统一记账：只补齐「距上一次请求完成时刻」到间隔目标的差额。
        导航/取页本身已花掉的时间计入间隔，同一条记录链路里连续多次 _sleep
        （主页面→兜底子页→收尾）不会重复支付整段等待——这是三站里
        gys/MIC 明显慢于纯 HTTP 站的主要放大器（旧实现每家最多睡 2~3 整轮）。
        节奏下限仍为随机化间隔，不会因为记账而退化成零间隔。"""
        gap = (self._current_delay + random.uniform(0, self.jitter)) * mult
        now = time.time()
        anchor = self._last_activity if self._last_activity else now
        due = max(now, anchor + gap)
        self._last_pace = due
        while time.time() < due:
            if self._stop_requested:
                return
            time.sleep(0.3)

    def _pace_before_request(self):
        """**每次真实请求前**对齐节奏（纯 HTTP 站专用）。

        为什么需要：浏览器站一条记录 = 一次导航，「每条记录结束睡一次」等价于
        「每请求睡一次」；但纯 HTTP 站（八方/顺企网/供应商网）一家公司要发
        商铺首页+备用域+contact+移动版+关于我们 最多 5 个请求，旧链路只在记录
        结束时睡，记录内部 5 个请求背靠背发出——实际速率是设定间隔的 3~5 倍。
        八方正是以这样约 0.5~1 请求/秒的速度在 200 多家后把出口 IP 打进 502 限流。
        把节流下沉到请求级后，界面上的「采集间隔」才是它字面意义上的东西。

        只对显式开了 PER_REQUEST_PACE 的站生效：浏览器站一条记录就是一轮导航，
        无差别开启只会让本来正常的站莫名慢 2~3 倍。"""
        if getattr(self, "PER_REQUEST_PACE", False) and self._last_activity:
            self._sleep(getattr(self, "PACE_FACTOR", 1.0))

    # ── 关键词解析 ─────────────────────────────────────────────
    @staticmethod
    def _split_outside_brackets(seg):
        """按「、」和「/」切分，但**不切括号内**的内容：
        「彩钢夹芯板（岩棉/聚氨酯/蜂窝铝）」旧逻辑会被切成「彩钢夹芯板（岩棉」
        「聚氨酯」「蜂窝铝）」这类括号不配对、无法检索的碎片（数据量骤降的元凶之一）。"""
        openers = {"（": "）", "(": ")", "【": "】", "[": "]"}
        closers = set(openers.values())
        out, buf, depth = [], [], 0
        for ch in seg:
            if ch in openers:
                depth += 1
                buf.append(ch)
            elif ch in closers:
                depth = max(0, depth - 1)
                buf.append(ch)
            elif depth == 0 and ch in "、/":
                out.append("".join(buf))
                buf = []
            else:
                buf.append(ch)
        out.append("".join(buf))
        return out

    @staticmethod
    def _strip_brackets(part):
        """去掉括注、只留可检索的主体词：「彩钢夹芯板（岩棉/聚氨酯）」→「彩钢夹芯板」。
        B2B 站内搜索对短主体词命中率远高于带括注的长短语；括注被剥空时退回内部词。"""
        core = re.sub(r"[（(【\[][^）)】\]]*[）)】\]]", "", part).strip(" 　・-")
        if core:
            return core
        m = re.search(r"[（(【\[]([^）)】\]]+)[）)】\]]", part)
        return (m.group(1).strip() if m else part.strip(" 　"))

    # 「绝不可能是关键词」的文档碎片：从行业报告（markdown/Word）整段粘贴时混进来的
    # 小节标题、括号残片、机构名。判据都刻意取得很窄，宁放勿杀——真正可疑但可能是
    # 产品词的（环节名、类别名）交给界面提示 _warn_abstract_keywords，不在这里删。
    _KW_JUNK_PATTERNS = (
        (r"#", "文档标题符号"),
        # 四类括号全覆盖：漏一种就会放走一种切坏的半句（实机 '从重到轻）' 正是全角右括号漏网）
        (r"【|】|\[|\]|（|）|\(|\)", "括号残片（配对括注早被 _strip_brackets 剥掉了）"),
        (r"TOP\s*\d", "榜单小节标题"),
        (r"(协会|学会|研究院|研究所|委员会|联合会|商会)$", "机构名而非产品词"),
    )
    _KW_JUNK_MAX_LEN = 16       # 中文关键词的长度感：超过这么多还带空格＝整句描述

    @staticmethod
    def _kw_junk_reason(k):
        """这个「关键词」其实是文档碎片的理由；正常词回空串。

        括号那条只会在**不配对**的残片上命中：_strip_brackets 已经把配对括注
        （「浮式生产储卸油装置（FPSO）」）剥成主体词，剩下降号的必然是切坏的半句。"""
        if not k:
            return "空"
        for pat, why in CdpSiteCrawler._KW_JUNK_PATTERNS:
            if re.search(pat, k):
                return why
        if (len(k) > CdpSiteCrawler._KW_JUNK_MAX_LEN and " " in k
                and re.search(r"[\u4e00-\u9fa5]", k)):
            return "带空格的整句描述"
        return ""

    @staticmethod
    def parse_keywords(text, junk_out=None):
        """junk_out 传一个列表就回收被剔的碎片（(词, 理由)），供界面如实说明。"""
        if not text:
            return []
        # 一级分隔：换行/逗号/分号/竖线/制表符（「、」和「/」交给括号感知切分）
        segments = re.split(r"[\r\n,;，；|\t]+", text)
        parts = []
        for seg in segments:
            parts.extend(CdpSiteCrawler._split_outside_brackets(seg))
        seen, out = set(), []
        for p in parts:
            k = re.sub(r"\s+", " ", p).strip()
            k = re.sub(r"^\d+[.、]\s*", "", k)          # 去掉「1. 」「2、」序号前缀
            k = CdpSiteCrawler._strip_brackets(k)
            k = re.sub(r"\s+", " ", k).strip(" 　・-")
            # 丢弃退化碎片：空、无中英文数字、单个汉字（如「初」）——它们检索必然零结果
            if len(k) < 2 or not re.search(r"[\u4e00-\u9fa5A-Za-z0-9]", k):
                # 也要如实记账：整段粘贴的报告标题行（'#### 【TOP3 核心装备平台类】'）
                # 会被 _strip_brackets 剥成 '####' 落到这里，不记的话界面报的剔除数就比
                # 实际少，用户对自己的关键词表拿到的是错的数。
                if k and junk_out is not None and (k, "剥掉括注后只剩符号") not in junk_out:
                    junk_out.append((k, "剥掉括注后只剩符号"))
                continue
            # 丢弃文档碎片（报告小节标题/括号残片/机构名）：见 _kw_junk_reason 的实机账
            why = CdpSiteCrawler._kw_junk_reason(k)
            if why:
                if junk_out is not None and (k, why) not in junk_out:
                    junk_out.append((k, why))
                continue
            if k not in seen and len(k) <= 40:
                seen.add(k)
                out.append(k)
        return out

    # ── 相关性（关键词↔行业）判定：默认按中文二字组，子类可覆盖 ──
    _PEER_FN = None      # keyword_expander.peer_words 的惰性缓存（None=未取过，False=没有）

    def _kw_grams(self, kw):
        base = re.sub(r"(批发|供应|厂家|定制|加工|工厂|价格|报价|大全|网)$", "", kw or "")
        return base, {base[i:i + 2] for i in range(len(base) - 1)}

    def is_relevant(self, kw, text):
        """关键词与「公司名+主营+标题」的相关性：中文二字组重叠或完整词组命中即相关。
        二字组仍不命中时再过一道**同族类目桥**（见 _peer_words）：同行在站上常把同一类
        设备写成兄弟类目（搜「数控机床」回「车床,加工中心」的厂），这类词与关键词零二字组
        重叠，不补这一刀就是成片误杀（实机「数控机床」两页砍掉 36 家里有 5~7 家这样的真同行）。
        桥表是窄的人工表，只收实机证明过误杀的行业，不做泛用词库。
        取不到可靠中文信号时 fail-open（宁多采一家不误杀）。"""
        if not self.relevance_guard:
            return True
        text = (text or "").strip()
        if not text or not re.search(r"[\u4e00-\u9fa5]", text):
            return True
        base, kg = self._kw_grams(kw)
        if not kg:
            return True
        compact = re.sub(r"\s+", "", text)
        if kg & {compact[i:i + 2] for i in range(len(compact) - 1)}:
            return True
        if base and base in compact:
            return True
        for peer in self._peer_words(kw):
            if peer in compact:
                return True
        return False

    @classmethod
    def _peer_words(cls, kw):
        """同族类目词（keyword_expander.PEER_CATEGORY）。取不到那个模块、或这个词不在
        桥表里，就返回空元组——判定退回「纯二字组」的老行为，绝不因为桥而放宽别的词。
        函数引用挂在类属性上只做一次导入（每张列表卡都要过判定，不能反复 import）。"""
        if cls._PEER_FN is None:
            try:
                import keyword_expander as _ke
                cls._PEER_FN = getattr(_ke, "peer_words", None) or False
            except Exception:
                cls._PEER_FN = False
        return cls._PEER_FN(kw) if cls._PEER_FN else ()

    # ── 站点钩子（子类实现）───────────────────────────────────
    def build_search_url(self, kw, page):
        raise NotImplementedError

    def parse_list(self, html, kw):
        """解析列表页 → 公司条目 stub 列表；每个 stub 至少含 公司名称/memberId/详情URL。"""
        raise NotImplementedError

    def list_has_more(self, html, page):
        """是否还有下一页（默认按解析到的条目数判断由 crawl 控制）。"""
        return True

    def crawl_detail(self, stub):
        """进详情页取联系方式，返回完整记录 dict（含「状态」）。"""
        raise NotImplementedError

    def stub_relevance_text(self, stub):
        """列表页早过滤只用**可靠的产品信号**（主营产品/主营商品）。
        三站列表 stub 的公司名/标题都是法人名（常不含产品词，如关键词「太阳能」
        vs「XX新能源科技有限公司」），拿它早过滤会误杀真正相关的公司；产品字段
        列表阶段为空时 is_relevant fail-open，把判定推迟到详情页（detail_relevance_text）。"""
        return " ".join(str(stub.get(k) or "") for k in ("主营产品", "主营商品"))

    def detail_relevance_text(self, rec):
        """详情页复核相关性用的文本。"""
        return " ".join(str(rec.get(k) or "") for k in ("公司名称", "主营产品", "主营商品", "标题"))

    def detail_relevant(self, rec):
        """详情页相关性复核（弱信号 fail-open，实机种子库取证：三站各有
        200~380 家被「类目不符」丢掉，主因是列表 stub 无主营产品、早过滤
        必然放行，而详情页又没解析到主营产品时，旧逻辑只剩法人名可比
        （如关键词「光伏」vs「XX新能源有限公司」）→ 大面积误杀）：
        · 任一信号命中关键词 → 相关；
        · 拿到**可靠中文产品信号**（主营产品/主营商品）仍不命中 → 真不相关，过滤；
        · 产品信号缺失/无中文（弱信号）→ 保留，宁可多采一家不误杀。
        与列表早过滤的 tooltip 口径（信号取不到一律放行）保持一致。"""
        if not self.relevance_guard:
            return True
        if self.is_relevant(rec.get("关键词", ""), self.detail_relevance_text(rec)):
            return True
        strong = str(rec.get("主营产品") or rec.get("主营商品") or "").strip()
        return not re.search(r"[\u4e00-\u9fa5]", strong)

    def stats(self):
        """本轮量化统计（GUI 看板/结束日志用）。"""
        return {
            "pages": self.stat_pages,
            "listed": self.stat_listed,
            "ok": self.stat_ok,
            "early": self._early_skipped,
            "filtered": self._filtered_count,
            "no_contact": self.stat_no_contact,
            "failed": self.stat_failed,
            "got": len(self.results),
            "pf_miss": self._pf_dropped,        # 省份把关：确认外省已排除
            "pf_unknown": self._pf_unknown,     # 省份把关：属地未标注已排除（已转企知道反查）
            "kw_yield": dict(self.kw_yield),
        }

    # ── 列表翻页迭代 ──────────────────────────────────────────
    def _iter_keyword(self, kw):
        empty_streak = stale_streak = 0
        page = 0
        while not self._stop_requested:
            page += 1
            # max_pages 仅作安全上限（>0 时生效）；=0 表示不限页，一直翻到没数据。
            if self.max_pages and page > self.max_pages:
                return
            while self._pause_requested and not self._stop_requested:
                time.sleep(0.5)
            url = self.build_search_url(kw, page)
            if not url:
                return
            if not self._goto(url):
                return
            html = self._html()
            entries = self.parse_list(html, kw)
            self.stat_pages += 1
            self.stat_listed += len(entries)
            if not entries:
                empty_streak += 1
                if empty_streak >= 2:
                    self._log(f"  「{kw}」第 {page-1} 页起无结果，停止翻页")
                    return
                continue
            empty_streak = 0
            new = 0
            skipped = 0
            for e in entries:
                mid = str(e.get("memberId") or e.get("公司ID") or e.get("详情URL") or e.get("公司名称") or "")
                key = f"{self.PLATFORM_SLUG}:{mid}"
                name = self._norm_name(e.get("公司名称") or "")
                nkey = f"{self.PLATFORM_SLUG}:name:{name}" if name else ""
                if key in self._seen_members or (nkey and nkey in self._seen_members) \
                        or f"{self.PLATFORM_SLUG}:{kw}#{mid}" in self._seen_members:
                    continue
                # 列表页早过滤：明显与关键词无关的连详情页都不进（省请求=提速+防封）
                # 砍掉的**只按「关键词#主体」标记**，不占全局已见：相关性是「对这个关键词」的判定，同一家对 A 词不符、对 B 词可能正是目标（实机砍掉的名单里就有真同行）。
                if not self.is_relevant(kw, self.stub_relevance_text(e)):
                    skipped += 1
                    self._early_skipped += 1
                    self._seen_members.add(f"{self.PLATFORM_SLUG}:{kw}#{mid}")
                    self._commit_to_store({"状态": "类目不符(已过滤)", "关键词": kw,
                                           "公司名称": e.get("公司名称", ""), "memberId": mid})
                    continue
                self._seen_members.add(key)
                if nkey:
                    self._seen_members.add(nkey)
                e["关键词"] = kw
                new += 1
                yield e
            tail = f"，早过滤 {skipped} 家(未进详情)" if skipped else ""
            self._log(f"  「{kw}」第 {page} 页：{len(entries)} 条，新增 {new} 家{tail}")
            if new == 0:
                stale_streak += 1
                if stale_streak >= 2:
                    self._log(f"  「{kw}」连续 {stale_streak} 页无新增，公司池已枯竭")
                    return
            else:
                stale_streak = 0
            if not self.list_has_more(html, page):
                return
            self._sleep()

    # ── 关键词台账：快照 / 回填（续采入口）───────────────────────
    def progress_snapshot(self):
        """本站当前的关键词进度（供任务落盘）。"""
        return {"yield": dict(self.kw_yield), "done": sorted(self._kw_done)}

    def restore_progress(self, snap):
        """把上次任务的关键词进度装回来：已跑完的词直接跳过，净新增接着累加。
        同时打开 resume 标记——crawl() 不再清空台账，否则一进循环就把刚恢复的进度抹了。"""
        snap = snap or {}
        self.kw_yield = {str(k): int(v or 0) for k, v in (snap.get("yield") or {}).items()}
        self._kw_done = {str(k) for k in (snap.get("done") or [])}
        self.resume = bool(self._kw_done or self.kw_yield)
        return self.resume

    def seed_results(self, records):
        """续采前把本任务已采到的记录装回内存：自动保存是按 results 全量覆写
        原数据 Excel 的，不装回来就会用「本轮新采的」盖掉上次已采的那几百上千条。"""
        rows = [r for r in (records or []) if isinstance(r, dict) and r.get("公司名称")]
        if not rows:
            return 0
        with self.results_lock:
            self.results.extend(rows)
        return len(rows)

    # ── 主采集循环 ─────────────────────────────────────────────
    def crawl(self, keywords, start_index=0):
        self._crawling = True
        self._stop_requested = False
        self._pause_requested = False
        self._seen_members = set()
        self._filtered_count = 0
        self._pf_dropped = 0
        self._pf_unknown = 0
        self._early_skipped = 0
        self.stat_pages = self.stat_listed = 0
        self.stat_ok = self.stat_no_contact = self.stat_failed = 0
        if not self.resume:
            # 全新任务才清台账；续采（restore_progress 已置 resume）要留着上次的进度接着跑
            self.kw_yield = {}
            self._kw_done = set()
        self._last_activity = 0.0
        self._last_pace = 0.0
        self._block_times = []
        self._current_delay = self.delay_between
        seeded = 0
        if self.incremental:
            seeded = self._load_seen_store()
            # 只把「已完整 / 重试已用尽」的条目当去重依据，缺联系人的历史条目自动解锁
            locked = self._locked_keys()
            self._seen_members |= locked
            refill = seeded - len(locked)
            if refill:
                self._log(f"🔓 增量库内有 {refill} 家条目联系人曾取空，本轮重采补联系人")

        if not self._is_session_alive():
            if not self.start_browser():
                self._crawling = False
                self._emit("crawl_finished_signal")
                return

        kws = [str(x).strip() for x in (keywords or []) if str(x).strip()]
        total = len(kws)
        note = f"，增量已带 {seeded} 家历史公司（命中即跳过）" if seeded else ""
        self._log(f"🚀 开始采集 {total} 个关键词，"
                  + (f"每词最多 {self.max_pages} 页" if self.max_pages else "每词不限页数(翻到没数据为止)")
                  + f"{note}")
        done = 0
        ki = start_index
        self._emit("kw_progress_signal", self.PLATFORM_SLUG, 0, len(kws))
        while ki < len(kws):
            if self._stop_requested:
                break
            while self._pause_requested and not self._stop_requested:
                time.sleep(0.5)
            kw = kws[ki]
            ki += 1
            if kw in self._kw_done:
                self._log(f"↻ 续采：「{kw}」上次已跑完（净新增 {self.kw_yield.get(kw, 0)} 家），本轮跳过")
                self._emit("kw_progress_signal", self.PLATFORM_SLUG, ki, len(kws))
                continue
            pages_before = self.stat_pages
            yield_before = self.kw_yield.get(kw, 0)
            self._log(f"▶ [{ki}/{len(kws)}] 关键词：{kw}")
            try:
                for stub in self._iter_keyword(kw):
                    if self._stop_requested:
                        break
                    while self._pause_requested and not self._stop_requested:
                        time.sleep(0.5)
                    try:
                        rec = self.crawl_detail(stub)
                    except Exception as e:
                        msg = str(e).splitlines()[0][:160] if str(e) else e.__class__.__name__
                        if self._session_dead(msg):
                            self._log(f"⚠ 详情处理中会话断开（{msg[:50]}），重启后续采…")
                            if not self.try_restore_session():
                                self._stop_requested = True
                            continue
                        rec = self._base_rec(stub)
                        rec["状态"] = "详情解析异常"
                    if rec.get("状态") == "类目不符(已过滤)":
                        self._filtered_count += 1
                        self._commit_to_store(rec)
                        if self._filtered_count % 10 == 1:
                            self._log(f"  ⛔ 相关性不符已过滤 {self._filtered_count} 家"
                                      f"（最近：{(rec.get('公司名称') or '')[:20]}）")
                        continue
                    st = rec.get("状态") or ""
                    if st == "成功":
                        self.stat_ok += 1
                    elif st in ("无电话(未公开)", "无联系方式"):
                        self.stat_no_contact += 1
                    else:
                        self.stat_failed += 1
                    self._add_result(rec)
                    done += 1
                    self._emit("progress_signal", done, max(done, 1))
                    self._emit("stats_signal",
                               f"{self.PLATFORM_NAME}｜关键词 {ki}/{len(kws)}｜已采 {done}"
                               f"｜间隔 {self._current_delay:.1f}s")
                    self._sleep()
            except Exception as e:
                msg = str(e).splitlines()[0][:160] if str(e) else e.__class__.__name__
                if self._session_dead(msg):
                    self._log(f"⚠ 「{kw}」处理中会话断开（{msg[:50]}），重启后续采…")
                    if not self.try_restore_session():
                        self._stop_requested = True
                else:
                    self._log(f"⚠ 「{kw}」处理异常：{msg} → 跳过该关键词继续")
            if not self._stop_requested:
                # 跑完一轮才记「已跑过」；中途被停止/暂停掉开的词下次重来，
                # 重复条目由种子库兜住（种子库会一直留着），不会多烧请求。
                # 但“一页都没取到”不能算跑完：那不是「这个词没公司」，而是根本没和站上
                # 说上话（被防采拦、会话断、或列表取页链路没接上）。旧实现连这种也记
                # 「已跑完」，2026-11-02 麦麦B2B 就是十个关键词全被标成跑完、净产出 0，
                # 用户再点续采全部被跳过，看到的就永远是「只有这一家 0 条成功」。
                if self.stat_pages > pages_before or self.kw_yield.get(kw, 0) > yield_before:
                    self._kw_done.add(kw)
                else:
                    self._log(f"⚠ 「{kw}」一页都没取到（被拦或链路没通），本轮不记「已跑完」，下次自动重来")
            if self.incremental:
                self._save_seen_store()
            self._emit("kw_progress_signal", self.PLATFORM_SLUG, ki, len(kws))
        self._crawling = False
        if self.incremental:
            self._save_seen_store()
        tail = f"，另有 {self._filtered_count} 家不相关已过滤" if self._filtered_count else ""
        if self.province and (self._pf_dropped or self._pf_unknown):
            pv_display = self.province if "," not in self.province else "目标省份"
            tail += (f"、{self._pf_dropped} 家非{pv_display}已排除"
                     f"（另有 {self._pf_unknown} 家页面没写属地：已转企知道反查）"
                     if self._pf_unknown else f"、{self._pf_dropped} 家非{pv_display}已排除")
        early = f"，列表页早过滤 {self._early_skipped} 家" if self._early_skipped else ""
        saved = f"，种子库累计 {len(self._seen_store)} 家" if self.incremental else ""
        self._log(f"✅ {self.PLATFORM_NAME}采集结束，共 {len(self.results)} 家{early}{tail}{saved}")
        s = self.stats()
        self._log(f"📊 {self.PLATFORM_NAME}本轮账目：翻 {s['pages']} 页共见 {s['listed']} 条列表条目｜"
                  f"成功 {s['ok']}｜无联系方式 {s['no_contact']}｜失败/可重采 {s['failed']}｜"
                  f"早过滤 {s['early']}｜详情复核不相关 {s['filtered']}"
                  + (f"｜省份把关：外省 {s['pf_miss']}、属地未标注 {s['pf_unknown']}"
                     if self.province else ""))
        self._emit("crawl_finished_signal")

    def _base_rec(self, stub):
        """统一记录骨架（子类 crawl_detail 应基于它扩展）。"""
        return {
            "关键词": stub.get("关键词", ""),
            "公司名称": strip_name_marketing_tail(stub.get("公司名称", "")),
            "联系人": "", "岗位": "", "电话": "", "手机": "", "邮箱": "", "地址": "",
            "主营产品": stub.get("主营产品", ""),
            "经营模式": stub.get("经营模式", ""),
            "所在区域": stub.get("所在区域", ""),
            "公司网址": stub.get("公司网址", ""),
            "详情URL": stub.get("详情URL", ""),
            "memberId": stub.get("memberId", "") or stub.get("公司ID", ""),
            "数据来源": self.PLATFORM_NAME,
            "采集时间": time.strftime("%Y-%m-%d %H:%M:%S"),
            "状态": "",
        }

    # ── 控制 ───────────────────────────────────────────────────
    def reset_run_state(self):
        """「开始新任务」用：清掉本次已采数据与本轮账目/熔断计数，
        不动跨运行历史种子库（要不要清由界面里的选项单独决定）。"""
        with self.results_lock:
            self.results = []
        self._crawling = False
        self._stop_requested = False
        self._pause_requested = False
        self._blocked = False
        self._block_reason = ""
        self._block_times = []
        self._seen_members = set()
        self._filtered_count = 0
        self._pf_dropped = 0
        self._pf_unknown = 0
        self._early_skipped = 0
        self.stat_listed = self.stat_pages = 0
        self.stat_ok = self.stat_no_contact = self.stat_failed = 0
        self.kw_yield = {}
        self._kw_done = set()
        self.resume = False
        self._last_activity = 0.0
        self._last_pace = 0.0
        self._current_delay = self.delay_between
        return True

    def stop_crawl(self):
        self._stop_requested = True
        self._log("正在停止…")

    def pause_crawl(self):
        self._pause_requested = True
        self._log("已暂停")

    def resume_crawl(self):
        self._pause_requested = False
        self._log("已恢复")

    # ── 导出 / 清洗（复用成熟模块）────────────────────────────
    EXPORT_HEADERS = ["序号", "关键词", "公司名称", "联系人", "岗位", "电话", "手机", "邮箱",
                      "地址", "主营产品", "经营模式", "所在区域", "公司网址", "详情URL", "memberId",
                      "数据来源", "采集时间", "状态"]

    def export_excel(self, output_path, rows=None):
        rows = self.snapshot_results() if rows is None else rows
        if not rows:
            self._log("没有数据可导出")
            return False
        try:
            from openpyxl import Workbook
            wb = Workbook()
            ws = wb.active
            ws.title = f"{self.PLATFORM_NAME}企业数据"[:28]
            ws.append(self.EXPORT_HEADERS)
            for i, r in enumerate(rows, 1):
                ws.append([i] + [clean_cell(r.get(h, "")) for h in self.EXPORT_HEADERS[1:]])
            wb.save(output_path)
            return True
        except Exception as e:
            self._log(f"❌ 导出失败：{e}")
            return False

    def _to_cleaner_record(self, r):
        """把原始记录映射为清洗模块认识的字段（电话/手机合并交给 cleaner 再拆分）。"""
        mobiles = [x for x in re.split(r"[/、,;]", r.get("手机") or "") if x.strip()]
        tels = [x for x in re.split(r"[/、,;]", r.get("电话") or "") if x.strip()]
        phones = " ; ".join([m.strip() for m in mobiles] + [t.strip() for t in tels if t.strip()])
        status = r.get("状态") or ""
        remark = ("主营:" + r["主营产品"] if r.get("主营产品") else "")
        if r.get("关键词"):
            remark += ("｜" if remark else "") + "关键词:" + r["关键词"]
        return {
            "公司名": r.get("公司名称") or "",
            "联系人": r.get("联系人") or "",
            "岗位": r.get("岗位") or "",
            "电话": phones,
            "邮箱": r.get("邮箱") or "",
            "地址": r.get("地址") or "",
            "官网": r.get("公司网址") or r.get("详情URL") or "",
            "数据来源": r.get("数据来源") or self.PLATFORM_NAME,
            "状态": "" if status == "成功" else status,
            "备注": remark,
        }

    @staticmethod
    def filter_import_rows(rows, log=None):
        """入库口径：手机号/固话/邮箱/微信号**四项全空**的行剔掉（2026-10-28 定口径：
        这类行入库后外呼/开发一个都碰不到，只会污染后台客户库）。
        只影响「清洗后入库数据」与云展导入，原始表仍然全量保留（补查线索不丢）。"""
        rows = rows or []
        kept = [r for r in rows
                if any(str(r.get(k) or "").strip()
                       for k in ("手机号", "固话", "邮箱", "微信号"))]
        dropped = len(rows) - len(kept)
        if dropped and log:
            log(f"🧹 清洗口径：剔除 {dropped} 条「手机号/固话/邮箱/微信号全空」记录"
                f"（不入库，避免污染后台客户库；原始表仍全量保留）")
        return kept

    def clean_records(self, records, log=None):
        log = log or self._log
        recs = [self._to_cleaner_record(r) for r in records]
        rows = _cleaner.build_clean_rows(recs, log=log)
        return self.filter_import_rows(rows, log=log)

    def build_clean_rows(self, log=None):
        return self.clean_records(self.snapshot_results(), log=log or self._log)

    def export_cleaned_excel(self, output_path, template_path="", log=None):
        log = log or self._log
        rows = self.build_clean_rows(log=log)
        if not rows:
            log("没有可清洗入库的数据")
            return False
        if not template_path:
            template_path = _cleaner.locate_template([str(Path(output_path).parent), str(self._data_dir)])
        return _cleaner.write_clean_excel(rows, output_path, template_path=template_path, log=log)
