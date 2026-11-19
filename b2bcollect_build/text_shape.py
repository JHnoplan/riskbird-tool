# -*- coding: utf-8 -*-
"""text_shape.py —— 文本收口的**单一把手层**，只依赖标准库。

本层现在管两族（两族都是「同一个缺陷在交付链里有几十处独立写法」这个同一个形状）：
  ① 失败原因收成一行（第 ㉓ 族）：`short_reason` / `first_line`，见下面注释；
  ② 数据值收成可显示文本（第 ㉛ 族，2026-11-19 需求 8）：`cell_text` / `cell_join`，
     挡住「数字型手机号把 QTableWidgetItem 搞崩、把 join 搞崩」这一类类型不保的崩溃。

把手为什么必须住在这样一个零依赖的小模块里（而不是 cdp_base / customer_library_cleaner）：
上面「为什么单独成模块」那一段已经写过一次，两族的选型理由完全同源，不重复印。

【以下为第一族的原有口径，未变】
为什么单独成模块（2026-11-16 第八轮实机报告 + 随后的同类查全）：
  1) 实机体检报告里出现过这种句子——
     `【连不上·无应答】b2b.11467.com:443｜Connection to b2b.11467.com timed out. (con）`
     一个英文词被截掉半边、右括号还是分型器补上的。半截词比不说更误导：读到一半的人
     会照着那半截去查，而「该怎么处置」写在句首，被切掉的恰恰是后半句的证据。
  2) 同一个缺陷在交付链里有 **41 处**独立写法：`str(e).splitlines()[0][:120]`（GUI 告警、
     会话日志、名单通道翻译层、权限自检、健康看板）、`err[:70]`（三平台回执进台账）、
     `resp.text[:200]`（OSS 报错体）。上一轮只把「已经分过型的原因」收进 `short_reason`，
     而当时那条判据 `11p` 扫的是 `*_crawler.py`——于是 cdp_base / gui / access_guard /
     channel_health / site_adapters 里三十多处一刀没动，界面照样念半截词。
     **判据的视野比缺陷小，就等于没修。**
  3) 把手不能住在 cdp_base 里：`access_guard`（权限与凭证自检，最先加载，而且要在采集器
     全坏的时候还能说话）、`channel_health`、`local_scan` 都不该为了「把一句话缩短」去拉
     requests + shared_chrome 这一整套；`customer_library_cleaner` 是三个交付目录里的同名
     拷贝，顶层 import 本目录专属模块会让另外两个目录直接 ImportError。

口径（三选一，别的写法一律不合格）：
  · `short_reason(text, limit)`——一句**已经定过性的原因**收成一行；
  · `first_line(text, limit)`——异常/多行文本收成一行：先按行取首行（自然边界），
    再走同一个标点断句口径。所有 `str(e).splitlines()[0][:N]` 的替代者；
  · 不接把手的地方就**整行带出**（不做任何按字符数的截断）——跨目录同名拷贝
    （customer_library_cleaner）与另两个独立包（riskbird/1688 的采集器共用层）走这一支，
    免得把第二份断句实现带进各自的包里。
  前两支都**只在标点或词边界断开**，`【类别】主机:端口｜` 前缀整段留住，括号不成对就补全。

本模块只依赖标准库 `re`，这样它才能被最早加载的自检链路 import。1688 独立包装不进
本文件，那边在 `ali1688_build/ali1688_crawler.py` 内联一份**逐字节相同**的实现，
由离线夹具 22h 与第 ㉓ 节、以及构建前硬门第 30 条一起拿源码对拍（只比函数返回值比不出
「一边悄悄改了断点表」）。
"""
import re

# 可断的位置：标点、全角分隔符、间隔号、空白（词与词之间）。**不**在任意字符之间断，
# 这正是「一句话的开头」与「半截词」之间的区别。
_REASON_BREAKS = "｜.：，、；)） ·"
# 连接符：左边是词字符、右边也是词字符时，它属于**同一个词**内部，不算边界。
# 由来（第 30 条性质判据在真源上抓到的三条）：`…｜Connection to b2b.`、`…读不了：客户库.`
# 这两条是断在域名/文件名的点号后面（下一字是 `1`、`x`），`…com:` 是断在端号分隔符后面
# （下一字是 `4`）——看起来都「断在标点后」，实际把一个词劈成两半。句子结尾的 `.` 后面
# 跟的是空格，仍然算边界，所以只排除「两边都是词字符」这一种。间隔号同判：
# `连不上·无应答` 是一个词，`冷却）·仅这一条` 才是两件事。
_REASON_JOINERS = ".:：０．·"        # 全角点/冒号/间隔号同判


def _is_break(s, k):
    """下标 k-1 处那个字符，在 k 这个切点上算不算真正的词边界。"""
    ch = s[k - 1]
    if ch not in _REASON_BREAKS:
        return False
    if ch in _REASON_JOINERS:
        left = s[k - 2] if k >= 2 else ""
        right = s[k] if k < len(s) else ""
        if left and right and (left.isalnum() or left in "-_") \
                and (right.isalnum() or right in "-_"):
            return False              # `b2b.11467` / `com:443` / `客户库.xls`：词内部
    return True


def short_reason(text, limit=60):
    """把一句失败原因收成一行读得完的短版：**只在标点或词边界断开，绝不拦腰截字**。

    `【类别】主机:端口｜` 这一截永远整段留住：处方（查出口网络还是改代码）就写在前缀里。
    前缀之后若在 limit 内找不到边界，就**往后**找第一个边界——宁可多带几个字，
    也不留半截词；整句一个边界都没有，就原句带省略号（长词本身就是信息）。
    """
    s = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(s) <= limit:
        return s
    head = 0
    if s.startswith("【"):
        i = s.find("｜")
        if 0 < i:
            head = i + 1              # 类别＋主机整段留住，哪怕它本身已经超过了 limit
    stop = -1
    for k in range(head + 1, min(len(s), max(limit, head)) + 1):
        if _is_break(s, k):
            stop = k
    if stop < 0:
        for i in range(head + 1, len(s)):
            if _is_break(s, i):
                stop = i
                break
    if stop < 0:
        return s + "…"
    out = s[:stop].rstrip("｜：，、； ")
    tail = "…"
    for op, cl in (("（", "）"), ("(", ")")):      # 断点前若留下没合口的括号，补上再收
        gap = out.count(op) - out.count(cl)
        if gap > 0:
            tail += cl * gap
    return out + tail


def first_line(text, limit=120):
    """把异常或多行文本收成一行：**先取首行（自然边界），再只在标点/词边界断**。

    这一支是 `str(e).splitlines()[0][:N]` 的替代品（交付链曾有 30 多处这么写）。取首行
    不算截断——异常消息第一行就是 `XxxError: 消息`；问题出在后面那一刀切在字符上，
    于是 `Message: unknown error: net::ERR_CONNECTION_` 这种半截类名反复出现在日志里。
    空文本回空串：调用方原本就在近旁留着 `or type(e).__name__` 这一档兜底。
    """
    s = str(text or "").strip()
    if not s:
        return ""
    return short_reason(s.splitlines()[0], limit)


# ── 第二族：数据值 → 可显示文本（第 ㉛ 族的单一把手） ─────────────────

def cell_text(value) -> str:  # noqa: C901  类型分派本身就是一串互斥分支，拆成多个函数会丢掉「单一口径」这件事
    r"""把「一个数据值」收成表格/文本能安全显示的字符串。

    为什么必须单独有这一支（2026-11-19 需求 8 实测，四个形状都当场跑过）：
      · ``QTableWidgetItem(13800000000)`` → **OverflowError**：11 位手机号（1.3e10）超过
        int32 上限 2147483647，PyQt 把这个整数当成 ``QTableWidgetItem.Type`` 枚举做了范围校验；
      · ``QTableWidgetItem(123)`` → **不报错但显示空白**（同样是枚举重载，text 为空）——
        小整数不会崩，只会把号码静默吞掉，比崩更难发现；
      · ``QTableWidgetItem(1.5 / ['a'] / {'k':1})`` → TypeError；
      · ``", ".join([13800000000])`` 与 ``re.sub(r"\D", "", 13800000000)`` → TypeError。
    后两条落在由 C++ 调用的 queued 槽里就是进程 ``abort()``：这就是用户报的「搜索完就崩」。
    数据为什么能是数字型：Excel/pandas 读进来不带引号的号码就是数值（本仓
    ``customer_library_cleaner._cell_text`` 的注释已经为这件事写过一次），而
    ``ingest_cleaning`` 旧写法只洗了 ``phones`` 那一支，``mobiles`` 走兜底时原样保留元素类型。

    口径（与仓库内已有的 ``_cell_text`` 保持同一方向，但多挡住三个会崩的形状）：
      None/NaN/inf → 空串；bool → 是/空；int → 完整数字串；float → 展开成不丢位的数字串；
      日期 → 年月日；list/tuple/set → 逐项递归后用「、」接；numpy 标量 → 先 ``.item()``；
      其它 → ``str()``。**本体绝不抛**：显示边界的把手自己招了异常，等于把崩溃换个地方发生。

    为什么住在这里而不是 customer_library_cleaner：那个文件是三个交付目录里的同名拷贝，
    从本工具去 import 它正是需求 5 要避免的跨工具耦合；而本模块只依赖标准库，权限自检那种
    最早加载的链路也接得到（上面第一段已经立过这条选型理由）。
    """
    try:
        if value is None or isinstance(value, str):
            return value if value is not None else ""
        if isinstance(value, bool):          # 必须在 int 之前：Python 里 bool 是 int 的子类
            return "是" if value else ""
        item = getattr(value, "item", None)
        if callable(item) and not isinstance(value, (bytes, bytearray)):
            # numpy/pandas 标量：np.int64 不是 int 的子类，不先转回 Python 原生类型就会
            # 一路落到下面的 str()，得到「13800000000」倒是运气好，得到「<class 'numpy.int64'>」
            # 就是界面上一排乱码。转完递归一次，口径不另外写一份。
            try:
                return cell_text(item())
            except Exception:
                pass                          # .item() 招了就走原生口径，不在这里下结论
        if isinstance(value, int):
            return str(value)
        if isinstance(value, float):
            if value != value or value in (float("inf"), float("-inf")):
                return ""                     # NaN/inf：Excel 空单元格的常见形状，写「nan」进表格就是假数据
            if value.is_integer():
                return str(int(value))
            return f"{value:.10f}".rstrip("0").rstrip(".")
        if isinstance(value, (bytes, bytearray)):
            return value.decode("utf-8", errors="replace")
        if isinstance(value, (list, tuple, set, frozenset)):
            return "、".join(x for x in (cell_text(v) for v in value) if x)
        if hasattr(value, "strftime"):
            try:
                return value.strftime("%Y-%m-%d")
            except Exception:
                pass                          # 自定义日期对象 strftime 招了就走下面的 str，不丢掉整个单元格
        if isinstance(value, dict):
            # 字典直接 str 会给出「{'name': ...}」这种程序员形状；字典出现在业务字段里本身就是
            # 数据异常，如实展成可读的一串，同时在面包屑里能查到这一条。
            return "、".join(f"{cell_text(k)}：{cell_text(v)}" for k, v in value.items())
        return str(value)
    except Exception:
        # 最后的铁底：任何类型的 __str__/__repr__ 都能自己抛（循环引用、破对象）。
        # 这里只报「这个值显示不了」，不报「所以这个工具可以消失」。
        return f"（无法显示：{type(value).__name__}）"


def cell_join(values, sep: str = ", ") -> str:
    """把一批值接成一行：``", ".join(values)`` 的接班人（列表里混一个 int 就抛 TypeError 那一只）。

    为什么不直接 ``cell_text(list)``：展示层的分隔符是有意选定的（手机号用「, 」、
    主营行业用「、」），把手只修类型，不能顺手改掉用户看惯的分隔。

    （2026-11-19 自检当场抓下的真错：本函数初版写成了 `sep(生成器)`——把字符串当函数调用，
    TypeError 被下面那句「绝不抛」直接吞成空串，接口看起来完全正常但永远输出空。
    教训：**防御性 except 会把自己的代码缺陷一起藏掉**，所以这一支必须有一条
    「混进一个 int 也不许抛、且逐字等于手拼结果」的往返判据，不能只靠写的时候看一眼。）
    """
    try:
        return sep.join(x for x in (cell_text(v) for v in (values or [])) if x)
    except Exception:
        return ""
