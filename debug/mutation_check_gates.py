# -*- coding: utf-8 -*-
"""mutation_check_gates.py —— 本轮几批新判据（DNS 预检闸、编号同源、现网反查、披露主数据、
需求 2 的版本单一真源与 CI 对拍）的「有没有牙」对拍。

判据全绿不是证据：一条无论实现对错都通过的断言是装饰，而装饰比没判据更坏——它让下一个
人以为这段行为被看着。本脚本把实现**退回修复前的写法**（一次只退一个子规则），跑真实离线
套件，按声明逐条核对：目标判据必须变红。多出来的红要人工确认是不是连带。

三种结论必须分开报，不能顶同一个名字：判据被杀红（✅）、判据真的没牙（❌ 装饰）、
变异本身不成立（❌ rc=97，子进程被变异自己打崩）。最后一种要是被当成前一种报出来，
读的人会去删一条本来有效的判据。

但 rc=97 不能一律读成「变异不成立」：有的套件的 check 是**抬 AssertionError** 的
（test_block_triage_offline 那一路 802 条），它红在第一条就终止整个子进程，那句红只存在于
异常文本里、不是一行 ``FAIL ...``。那一次 rc=97 里同时装着两件相反的事：判据真的被杀红了
（该记账）、以及后面几百条判据根本没跑到（该点名）。所以孩子在死前会把那句红标成
``MUTATION-FAIL-LINE``：收得到就走「杀红」那条路并额外提醒「提前终止」，
收不到才是「变异自己不成立」——不能拿退出码当一个结论。

为什么用子进程而不是进程内 runpy：碰过 Qt 的套件必须在判定后 `flush + os._exit` 收尾
（Qt/线程在解释器收尾时随机段错误，会把全绿读成 rc=139 的抖动；这一点本仓库有硬门守着）。
进程内跑套件会连这个脚本一起结束，所以每个模式都派一个孩子：孩子先打桩再跑套件，父进程
只读孩子的屏幕与退出码。顺带对拍两者一致——屏幕上说失败而退出码说通过，同样是假绿。

跑法：
    python3 debug/mutation_check_gates.py                # 全部模式（含基线）
    python3 debug/mutation_check_gates.py upfront_probe   # 只跑一个模式

解释器不是可调项：同一份命令拿缺依赖的解释器跑，出来的是一片 rc=97，拿齐的那个跑
出来是全绿（2026-11-19 就在 SE/BT 上实发过一次：parallel_session → makepolo_crawler →
mk_phone_ocr 需要 PIL）。所以开跑前先用**现场探针那一份实现**（debug/offline_env_probe.py，
范围取自批跑清单、依赖取自 import 闭包）自证。这里**不再「换一份解释器重开」**：挑候选
就是让两份解释器各算一遍闭包，红话最后说不清是哪一层；探针说不合格就当场停下、把缺哪几
只交给人（批跑入口那一步同理，它才是带病跑与不跑之间那道唯一的门）。
"""
import inspect
import os
import runpy
import subprocess
import sys
import tempfile
import textwrap
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.join(os.path.dirname(HERE), "b2bcollect_build")
SE = "test_site_enrich_offline.py"
WL = "test_waterfall_leads_offline.py"
PG = "test_perf_governor_offline.py"
EM = "test_exchange_master_offline.py"
BT = "test_block_triage_offline.py"
# 版本单一真源与 CI 对拍那一套（需求 2 的引擎层）：它不报 ok/FAIL，报 ✅/❌。
TU = "test_tool_update_offline.py"
# 批跑判定层（本目录里的套件，不是 b2bcollect_build 下那一套）：它自己也需要有牙。
BR = os.path.join(HERE, "batch_runner_offline_test.py")
# 孩子的专用退出码：打桩失败 / 套件未捕获异常。它和“判据红”必须是两种不同的结论。
CRASH = 97

# 每路套件的「判据行长什么样」必须**按套件登记**，不能把两种口径合并成一条松规则：
# 累加型套件把红打成 `FAIL ...`，而 ⑱ 那一路（引擎层）打的是 `❌ ...`，且它的**详情
# 文本里也会引用 ❌**（判据话术里在引另一条红）。改成「凡带 ❌ 都算红」就会把引文当成
# 判据行，把别的套件里本来有效的判据误报成装饰——那是变异台最坏的假案（读的人会去删）。
# 没登记者沿用历史口径，现有模式的行为一个字节都不变。
_OUT_KIND = {
    TU: ("❌", ("✅", "❌")),
}
_OUT_KIND_DEFAULT = ("FAIL", ("ok ", "FAIL", "✓"))

# ── 子进程里才打桩：桩必须发生在被测模块已导入、套件尚未开始的时候 ──────────


def _patch_fn(module, name, old, new):
    src = inspect.getsource(getattr(module, name))
    assert src.count(old) == 1, f"{name} 里找不到唯一一处 {old!r}（实现变了，先修变异夹具）"
    # 直接写进模块的**活**全局字典：变异函数的 __globals__ 就是这份字典，套件随后对模块
    # 名字的 monkeypatch 才对变异代码可见。拿 dict(vars(...)) 拷贝会把全局冻结在打桩那一刻。
    ns = vars(module)
    exec(compile(textwrap.dedent(src.replace(old, new)), "<mutation>", "exec"), ns)


def _patch_method(cls, name, old, new):
    src = inspect.getsource(getattr(cls, name))
    assert src.count(old) == 1, f"{cls.__name__}.{name} 里找不到唯一一处 {old!r}"
    # 变异方法必须共享类所属模块的**活**全局字典：既避开空字典的 NameError，又让套件后续
    # monkeypatch 模块名（如把 SiteEnrichWorker 换成 _FakeThread）对变异代码可见。
    # 拷贝快照会把「桩没接上」伪装成连带红，还能把 _threads 抽干后撞 IndexError。
    mod = sys.modules.get(cls.__module__)
    ns = vars(mod) if mod is not None else {}
    exec(compile(textwrap.dedent(src.replace(old, new)), "<mutation>", "exec"), ns)
    # exec 把方法当模块级同名全局落进了 ns；取回来挂到类上并清掉那个临时全局，
    # 函数对象仍持有 __globals__=ns（活的模块字典），后续 monkeypatch 对它的可见性不丢。
    setattr(cls, name, ns.pop(name))


def _pf(old, new):
    """website_contact.preflight_hosts 的一处文本变异。"""
    return lambda: _patch_fn(__import__("website_contact"), "preflight_hosts", old, new)


def _er(old, new):
    """website_contact.enrich_records 的一处文本变异（单行候选循环都在这个函数体里）。"""
    return lambda: _patch_fn(__import__("website_contact"), "enrich_records", old, new)


def _wf_(name, old, new):
    """enrich_waterfall 里某个函数的一处文本变异。"""
    return lambda: _patch_fn(__import__("enrich_waterfall"), name, old, new)


def _site_now(old, new):
    """MainWindow._on_site_enrich_now 的一处文本变异（手动补采那一段话术）。"""
    return lambda: _patch_method(__import__("b2bcollect_gui").MainWindow,
                                 "_on_site_enrich_now", old, new)


def _gui_fn(name, old, new):
    """b2bcollect_gui 模块级函数的一处文本变异（瀑布逐级说明与 tooltip 都在这一层）。"""
    return lambda: _patch_fn(__import__("b2bcollect_gui"), name, old, new)


def _sess(name, old, new):
    """ParallelSession 某个方法的一处文本变异。"""
    return lambda: _patch_method(__import__("parallel_session").ParallelSession, name, old, new)


def _em_(name, old, new):
    """exchange_master 里某个函数的一处文本变异（披露主数据层）。"""
    return lambda: _patch_fn(__import__("exchange_master"), name, old, new)


def _em_const(name, src):
    """exchange_master 里某个**模块常量**的改写（变异对象不是函数时走这条）。

    为什么需要它：尾缀词表是一张编译好的正则，退化方式是换掉那张表而不是改函数体；
    拿 `_patch_fn` 去动它只会在「找不到唯一一处」上报错，那会被读成「变异不成立」。
    """
    def go():
        mod = __import__("exchange_master")
        assert name in vars(mod), f"exchange_master 里没有 {name}（实现变了，先修变异夹具）"
        exec(compile(textwrap.dedent(src), "<mutation>", "exec"), vars(mod))
    return go


def _br(name, old, new):
    """offline_env_probe（现场探针）里某个函数的一处文本变异。

    探针是「这份解释器跑得动本轮套件吗」这一问的唯一出口：它把「不齐」读成「齐」，后面
    几十路套件就红成一片 ModuleNotFoundError 而被当成业务缺陷去查（本轮真经受过）；反过来
    它把永远装不上的名字当成硬要求，批跑就在开跑前被挡死。两种死法都得有证人。
    """
    return lambda: _patch_fn(__import__("offline_env_probe"), name, old, new)


def _probe_src(old, new):
    """探针**源码文本**的一处变异（写一份副本，判据 ⑯/⑯b 从副本取证）。

    为什么需要另一个口子：⑯/⑯b 钉的是「范围只有一条来路」，取证手段是读源码的 AST，
    而内存里改函数对象对这类判据完全无感（它们看的是字、不是行为）。不给副本，这两条就是
    本轮最该有牙、却永远只能对真文件说「绿」的那两条。
    """
    def go():
        src = open(os.path.join(HERE, "offline_env_probe.py"), encoding="utf-8").read()
        assert src.count(old) == 1, f"探针源码里找不到唯一一处 {old!r}（实现变了，先修变异夹具）"
        fd, path = tempfile.mkstemp(prefix="b2b_mut_probe_", suffix=".py")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(src.replace(old, new))
        os.environ["B2B_PROBE_SRC"] = path
    return go


def _sh_(old, new):
    """批跑入口 shell 文本的一处变异（同样写副本，判据 C 段从副本取证）。

    接线在 shell 文件里，不在任何 Python 对象上：不打副本，那九条「接线真的有牙」的判据
    就没有证人（而「门只印字不改退出码」这一族本仓库已经付过两次学费）。
    """
    def go():
        src = open(os.path.join(HERE, "run_offline_regression.sh"), encoding="utf-8").read()
        assert src.count(old) == 1, f"批跑入口里找不到唯一一处 {old!r}（实现变了，先修变异夹具）"
        fd, path = tempfile.mkstemp(prefix="b2b_mut_sh_", suffix=".sh")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(src.replace(old, new))
        os.environ["B2B_PROBE_SH"] = path
    return go


def _tv(name, old, new):
    """tool_version 里某个函数的一处文本变异（版本单一真源与随包自报那一族）。

    为什么这一族需要自己的证人：对拍脚本判的是「产物写出的那份 JSON」，而那份 JSON 的
    字段名与两格版本都由 `tool_version.report()` 给。两边字段名漂移时脚本会拿着一个根本不
    存在的键判成「说不出自己是哪一版」，而屏幕上两个 run 都是绿的——那正是本仓库在
    hidden-import 清单、pip 源、落点口径上各吃过一次的「两份实现」。
    """
    return lambda: _patch_fn(__import__("tool_version"), name, old, new)


def build_patches():
    """每个模式 = 一串「打桩动作」，全部在子进程里执行。"""
    return {
        # 退回「开跑前先打三个对照域、不通整轮跳过」：批内明明有解析成功的域也照问，
        # 于是离线套件当场查了夹具外的真域名。
        "upfront_probe": [
            _pf("    t0 = time.time()\n    lock = threading.Lock()",
                '    t0 = time.time()\n    if not _resolver_alive():\n'
                '        stats["skipped"] = "\xe6\x9c\xac\xe6\x9c\xba DNS \xe4\xb8\x8d\xe5\x8f\xaf\xe7\x94\xa8"\n'
                '        stats["unknown"] = len(todo)\n        return stats\n'
                "    lock = threading.Lock()"),
        ],
        # 整道对照域闸删掉：坏解析器写的负结论直接入盘（缓存被投毒的那条路）。
        "no_probe": [_pf("    if dead and not live_n and not _resolver_alive():", "    if False:")],
        # 话术里的家数取「已清零的 dead」：一批被拦得一家不剩，日志却说「0 个域照旧去抓」。
        "dropped_zero": [_pf('f"（{dropped} 个域照旧去抓）"', 'f"（{len(dead)} 个域照旧去抓）"')],
        # 丢弃的负结论不记进 unknown：分档账凑不齐（解了 3 个域却三格全 0）。
        "unknown_nodrop": [_pf('"unknown": unknown + dropped}', '"unknown": unknown}')],
        # 界面退回手抄的白话名单：阶段表改名/多级时这句话当众报错，而它不在层级名硬门的
        # 扫描范围里（白话句里没出现任何注册表里的层级名）——只能靠「逐级点名」那条杀。
        # 个数这一维故意仍由 _act 算：它归 gui_claims_all 管，一条变异只退一个子规则。
        "gui_five": [lambda: _patch_method(
            __import__("b2bcollect_gui").MainWindow, "_on_site_enrich_now",
            'f"这一轮开了「多源瀑布补全」，{len(_act)} 种途径都试过："\n'
            "                   f\"{_wf.stage_chain('、', stages=_act)}。\\n\"",
            'f"这一轮开了「多源瀑布补全」，{len(_act)} 种途径都试过："\n'
            '                   "行自带官网、行里邮箱的域名、同主体另一行已有的值、"\n'
            '                   "由商号拼出来的域名、转工商核验的名单。\\n"')],
        # 会话退回写死的「第 3 级」：名字同源了但序号还是第二套编号（层级名硬门扫不到数字）。
        "stage_three": [lambda: _patch_method(
            __import__("parallel_session").ParallelSession, "_waterfall_queue",
            'self._log(f"🏛 {_tag}没投出去：', 'self._log("🏛 瀑布第 3 级没投出去：')],
        # 退掉「投递前按任务文件夹补绑」：恢复/手动补采只 set_task_folder 时工商级整批投不出去。
        "no_rebind": [lambda: _patch_method(
            __import__("parallel_session").ParallelSession, "_waterfall_queue",
            'if not self.pending.is_bound and self.task_folder:', 'if False:')],
        # ── f7serp（现网反查）新判据的忠实杀手，每条对应一种已被否决/已修掉的写法 ──
        # 退回「排队阶段就预支」：抽到值的行也在买单，单行只问一次变成了问两次。
        # 这里不能直接叫 `_ask_escalate`：那个闭包要写 lock/stats，而两者都在这个循环
        # **之后**才创建——变异会把子进程打崩（rc=97），看上去像「判据没牙」实则夹具不成立。
        "serp_preempt": [_er(
            '        site = pick_site(r)\n'
            '        if site:\n'
            '            cand.append((r, [(site, "site")]))\n'
            '            continue\n'
            '        items = _candidates(r)',
            '        _x = list(escalate(r)) if escalate is not None else []\n'
            '        site = pick_site(r)\n'
            '        if site:\n'
            '            cand.append((r, [(site, "site")] + _x))\n'
            '            continue\n'
            '        items = _candidates(r)')],
        # 退回「与免费候选共用单行名额」：零网络候选交 4 个、单行只取 3 个，
        # room 恒为负，那一问永远轮不到（层级表多一级而实际一个包都不发）。
        "slot_shared": [_er('        out = out[:ESCALATE_MAX_PER_ROW]',
                            '        out = out[:max(0, MAX_CANDIDATES_PER_ROW - len(pool))]')],
        # 退回去重：反查回来的域名与免费候选重叠，把唯一那个新站挤出追加名额。
        # 「重复访问两遍」这条路是观察不到的（进程内同域只请求一次的缓存会吞掉它），因此
        # 判据钉的是「去重必须在封顶之前」那个可观察代价，不是访问次数。
        "no_dedup": [_er('            if url and url not in seen:', '            if url:')],
        # 把「没开」写成「开了但什么都不返」：账上两种形状同形，下一轮谁也看不出来。
        # 注意 needle 的词序要跟判据文案里的一模一样（“现网反查”在前），写反了就会把
        # “已杀红”误报成“判据是装饰”。
        "escalate_on_close": [_wf_("escalator_for", "    if not expensive:\n        return None",
                                   "    if False:\n        return None")],
        # 界面话术把没开的现网反查也算进「试过的途径」：个数与名单一起多报一级。
        "gui_claims_all": [_site_now(
            '        _act = _wf.active_stages(bool(self.session.expensive_lookup))',
            '        _act = _wf.active_stages(True)')],
        # 本级没开时不自己说一声：「瀑布跑完了」会被读成「所有途径都用过了」。
        "no_off_line": [_wf_("stage_lines",
                             '    elif stats.get("network") and net.get("eligible"):',
                             '    elif False:')],
        # ── f9gui（级数免疫）新判据的忠实杀手：每一条都是「今天对、加一级就错」的写法 ──
        # 界面逐级说明只讲「说明里有的那几级」：新一级接进阶段表而忘了写说明时，
        # tooltip 就静默少一行（今天六级全有说明，所以它今天完全绿）。
        # 不写成「把六个 slug 硬编码进循环」：那种夹具在摘掉 search 的形状上会拿一个
        # 已不在阶段表里的 slug 去算序号，当场 ValueError 让子进程崩掉（真变异不能这样）。
        "guides_frozen": [_gui_fn("waterfall_tooltip_text",
                                  '        for s in _wf.STAGES)',
                                  '        for s in _wf.STAGES if s in guides)')],
        # 序号改成导入期快照（已知的 slug 照旧念旧号）：阶段表多一级时它仍给旧编号。
        # 变异里故意保留 `or STAGES.index(slug) + 1` 这个兜底：纯下标写法会在没写死的那个
        # slug 上 KeyError 把子进程崩掉，那会被读成「变异不成立」而不是「判据杀红」，
        # 两种结论必须分得开（参见文件头那条分类）。
        "stage_no_snapshot": [_wf_("stage_no", "    return STAGES.index(slug) + 1",
                                   '    return {"carry": 1, "mailsite": 2, "site": 3, "brand": 4, '
                                   '"search": 5, "registry": 6}.get(slug) or STAGES.index(slug) + 1')],
        # 新一级忘了写面向用户的说明：应当当场自曝而不是静默少一行。
        "guides_missing_one": [_gui_fn("waterfall_stage_guides",
                                       '            "brand": "由英文商号、店铺子域拼出候选域名 → 抓回来还得过归属核验才认",\n',
                                       '')],
        # 缺说明时静默给个空串：tooltip 照样逐级点名、只是那一级后面什么也没说。
        # 这种形状读起来完全正常（行在、号在、名字在），比少一行日志难发现得多。
        "guides_silent": [_gui_fn("waterfall_tooltip_text",
                                  '        f"{_wf.stage_mark(s)}：" + (guides.get(s) or\n'
                                  '                                    "（这一级还没有面向用户的说明，源码里补在 "\n'
                                  '                                    "waterfall_stage_guides）")',
                                  '        f"{_wf.stage_mark(s)}：" + (guides.get(s) or "")')],
        # 会话层把级数缓存下来：加一级当天日志仍说旧的那几级。
        "session_stage_cache": [_sess("_wf_stages",
                                      "        return _wf.active_stages(bool(self.expensive_lookup))",
                                      '        return ("carry", "mailsite", "site", "brand", "registry")')],
        # ── f4f10（披露主数据层）新判据的忠实杀手：一条只退一个子规则 ──
        # 「名录侧唯一主体」那道闸退回比核心名：核心名正是分组的键，组内个个相等，
        # 这道闸从此恒放行——同核心名的两家「恒通检测」会被当成一家并合并取值。
        "same_subject_core": [_em_("_same_subject",
                                   '    return len({e["norm"] for e in ents}) == 1',
                                   '    return len({e["core"] for e in ents}) == 1')],
        # 主体标识退回 SECUCODE：每条记录一个键，多市场永远合不上，家数报成记录数。
        # 这条第一次跑时把子进程打崩过（rc=97）：不是变异不成立，而是**夹具**拿
        # `[...][0]` 取那一家——键的形状一变，索引里就没有含「夜光」的键了，IndexError
        # 把后面的判据一起带走，在变异台上会被误读成「变异不成立」。夹具取值先判存在再取。
        "secucode_key": [_em_("build_index",
                              '        eid = norm or core or abbr_core',
                              '        eid = str(row.get("SECUCODE") or "") or norm or core')],
        # 尾缀表放宽到「集团」：母公司与子公司算出同一个核心名（真实名录打回过的那一例）。
        # 变异对象是一张编译好的正则，不是函数体，所以走 _em_const 那条口子。
        "tail_wide": [_em_const("_ALIGN_TAIL_RE",
                                '_ALIGN_TAIL_RE = re.compile('
                                'r"(?:股份有限公司|有限责任公司|有限公司|股份公司|公司|集团)$")')],
        # 档三「余段必须整段是尾缀」那道闸关掉：只剩「简称是前缀」这一条，
        # 「中国船舶集团」就会挂到 600150 头上。
        "no_rest_gate": [_em_("match", "            if align(rest)[0]:", "            if False:")],
        # 机构类只认档一那道关掉：研究院那类天然共享集团前缀的名字会走兜底档。
        # 目标里两条各挡一层：旧那条只验账目归类（余段闸本来也会拦），新那条是真错配形状。
        "institution_fallback": [_em_("match", "    if institution:", "    if False:")],
        # 披露值的形态闸关掉：接口里的不成形 400 与缺位写法直接进表。
        "no_shape_gate": [_em_("contacts", "        if not _wc.plausible_phone(v):",
                               "        if False:")],
        # ensure 的重试闸关掉：拉失败/半截时盘上永远算「该重拉」，边采边补每 60 秒重开全量。
        # 连带红「reset_index 把重试账一起清掉」是预期的：那道闸不参与时，这条判据根本
        # 分辨不了「没试过」与「不许再试」，只能靠上面那三条钉着。
        "no_retry_gap": [_em_("ensure",
                              "        waiting = _FETCH_TRY[0] > 0.0 and "
                              "(time.monotonic() - _FETCH_TRY[0]) < RETRY_GAP_S",
                              "        waiting = False")],
        # status 退回完整读盘：为了报一个字把两万多条解析一遍，界面线程上就是新的转圈。
        "status_full_read": [_em_("status", "    head = _header(path)", "    head = None")],
        # disclosed_site 用回「切电话/邮箱那个分隔符」：分隔符里带斜杠，URL 被切成几截后
        # 平台页剩下的路径末段反倒像个域名（本轮真撞到过一次，锁在这里防回归）。
        "site_split_values": [_em_("disclosed_site",
                                   '    for token in re.split(r"[,;，；、]+", raw):',
                                   '    for token in re.split(r"[,;/／、，；]+", raw):')],
        # 档二那道「三字的商号不够定主体」的闸关掉：真实名录里「大自然有限公司」这种
        # 写法会直接拿到别人（杭州大自然科技）的董秘办总机——错一家比漏一家贵得多。
        # 目标只挂那一笔拒：同一段旁边的正向对照（「夜光微」照收）在这条变异下仍绿，
        # 那是它该绿——下限退掉时只有拒的那一半会红。
        "abbr_floor_off": [_em_("match",
                                "        if ents and len(ab) < ABBR_MIN_LEN"
                                " and strip_punct(name).lower() != core:",
                                "        if False:")],
        # ── 需求 2：版本单一真源与 CI 对拍的忠实杀手 ──
        # report() 里那一格改名：产物写的 JSON 与对拍读的键从此不是同一件事。目标判据不是
        # 「certain 那条反例」（它本来就期望那句红），而是「正例必须绿」——字段漂移时
        # 拿着一份完好无损的报告也会被误判成「产物说不出自己是哪一版」，那种误判只能
        # 由正例提供证人。
        "sr_certain_drift": [_tv("report", '        "certain": n != UNKNOWN_BUILD,',
                                 '        "certain_ok": n != UNKNOWN_BUILD,')],
        # ── 会话层接线（f4f10 把 master_fetch 接进 _run_waterfall 时真撞过一次漂移）──
        # 后台那一路不再钉 master_fetch=False：每 60 秒一轮的那条路自己开一轮五十几秒的全量下载。
        # 这一条就是用户报的「鼠标转圈」在本级上的形状，不是「少一个参数」那么轻。
        "stream_master_on": [_sess("_enrich_round",
                                   "verbose=False, master_fetch=False)",
                                   "verbose=False)")],
        # 调用点把参数名写错一个字母（本轮真实经受过：实现加了名、替身没跟上）。
        # 目标挂的是行为那一条（㉚q）：替身接不住这个关键字 → TypeError 被兜底 try 吞掉 →
        # 这一轮根本没跑瀑布。AST 那条门**不挂在期望里**：它读的是文件源码，而这里的桩
        # 只改内存里的函数对象，两者不在一份字上（它的牙由套件里的负夹具 ㉚s- 自证）。
        "stream_master_typo": [_sess("_enrich_round",
                                      "verbose=False, master_fetch=False)",
                                      "verbose=False, master_fetchx=False)")],
        # 收尾那一路也把 master_fetch 钉成 False：两条路都不付，主数据永远拉不下来，
        # 披露级永远空转（而日志上“没值”与“名录里没这家”长得一模一样）。
        "finish_master_off": [_sess("enrich_websites",
                                     "budget=budget, limit=limit,\n"
                                     "                                            only_missing=only_missing)",
                                     "budget=budget, limit=limit,\n"
                                     "                                            only_missing=only_missing,\n"
                                     "                                            master_fetch=False)")],
        # ── 现场探针（offline_env_probe）与批跑入口的接线 ──
        # 探针的两头死法都要有证人：读宽了（把「不齐」说成「齐」）与读窄了（把永远装不上的
        # 名字当硬要求）。每条变异只退一个子规则，目标判据必须当场红。
        # 退到修复前那一版：If / Try 的 body 递进 `_walk` 后里面的 import 全丢（本轮实发）。
        # 能造出这一形的判据就是④⑦那几条；它们当时在空集上「通过」了一整轮。
        "br_branch_imports_lost": [_br("_walk_stmt",
                                       "        return _emit(stmt, late, gone, opt)",
                                       "        return _walk(stmt, late=late, gone=gone, opt=opt)")],
        # 不往随包代码深处走：只扫套件顶层，PIL 那一类藏在链尾的永远看不见。
        "br_closure_shallow": [_br("closure", "                queue.extend(hits)", "                pass")],
        # 函数体不递归：延迟 import 不算要求（它一旦被调到就崩，症状与本轮那次假红一模一样）。
        "br_no_func_body": [_br("_walk",
                                "            out.extend(_walk(child, late=True, gone=gone, opt=opt))",
                                "            out.extend([])")],
        # try 的主支不再问「接不接得住 ImportError」：有退路的那几只全被拦停（功能降级被喊成现场坏了）。
        "br_no_fallback_probe": [_br("_walk",
                                     "            fallback = _catches_import_error(child)",
                                     "            fallback = False")],
        # 只认 `except ImportError`：现场那句包在 `except Exception` 里的裸 `import sip` 就会被
        # 当成硬要求——拿一只永远装不上的名字挡死整个批跑。
        "br_only_import_error": [_br("_catches_import_error",
                                     '    catch_all = {"ImportError", "ModuleNotFoundError", '
                                     '"Exception", "BaseException"}',
                                     '    catch_all = {"ImportError", "ModuleNotFoundError"}')],
        # except 支不再算退路本身（access_guard 那句就是这一形）。
        "br_handler_hard": [_br("_walk",
                                "                out.extend(_walk(handler, late=late, gone=gone, opt=True))",
                                "                out.extend(_walk(handler, late=late, gone=gone, opt=False))")],
        # 合并按「最后一条说话」：先软后硬那一顺序就会把硬要求洗成可选。
        "br_optional_wins": [_br("closure",
                                 "                rec[\"optional\"] = False",
                                 "                pass")],
        # 平台守卫不分极性（两支都算）：mac 的 CI 被一只永远不该装的 Windows 包挡在开跑前。
        "br_guard_both_reachable": [_br("closure",
                                        "            if unreachable:\n                continue",
                                        "            if False:\n                continue")],
        # 极性读反：真要跑的那一支被当成走不到，PIL 那一入口又漏了。
        # 必须**一次换完那两行**：分两条打桩的话，第一条执行完源码里就有两处 `(state is True)`，
        # 第二条在「找不到唯一一处」上报错——那是夹具自己的错，会被（正确地）归成「变异不成立」。
        "br_guard_flip": [_br("_walk",
                              "            for stmt in child.body:\n"
                              "                out.extend(_walk_stmt(stmt, late, gone or (state is False), opt))\n"
                              "            for stmt in child.orelse:\n"
                              "                out.extend(_walk_stmt(stmt, late, gone or (state is True), opt))",
                              "            for stmt in child.body:\n"
                              "                out.extend(_walk_stmt(stmt, late, gone or (state is True), opt))\n"
                              "            for stmt in child.orelse:\n"
                              "                out.extend(_walk_stmt(stmt, late, gone or (state is False), opt))")],
        # 「有个同名目录」就当仓库包：dist/ 这种不该被 import 的目录被当成随包代码读进闭包。
        "br_dir_is_pkg": [_br("resolve_shipped",
                              '    if os.path.isfile(init):',
                              '    if os.path.isdir(os.path.join(ROOT, head)):')],
        # 反过来：仓库目录包不进去（enf_clean 那一形被当成第三方依赖拦停）。
        "br_pkg_ignored": [_br("resolve_shipped",
                               '    if os.path.isfile(init):',
                               '    if False:')],
        # 同名跨目录只取第一份（不是并集）：另一份缺的那只永远进不了现场判定。
        "br_first_hit_only": [_br("resolve_shipped",
                                  "                hits.append(p)",
                                  "                return [p]")],
        # 不看发起 import 那份文件自己的目录（运行时的 sys.path[0]）：debug/ 同级那份
        # scrape_solarbe_full 会被当成一只缺掉的第三方包。
        "br_no_own_dir": [_br("resolve_shipped",
                              "        for d in (own_dir,) + tuple(os.path.join(ROOT, x) "
                              "for x in DELIVERY_DIRS) + (ROOT,):",
                              "        for d in tuple(os.path.join(ROOT, x) "
                              "for x in DELIVERY_DIRS) + (ROOT,):")],
        # 「范围断了」退成「没缺依赖」：本轮那次 --list rc=1/0 行把 frozen 里 `_files[0]` 崩掉
        # 的形状，就是被这一读法洗成一次体检成功。
        # 证人必须打在 main 那一层而不是 scope 里那一句：两处都拦得住（纵深防御），
        # 只打 scope 那一句时 main 仍然退 RC_NO_SCOPE——那是「另一处生效」，不是判据是装饰。
        # （拿存活变异给判据定罪，读的人会去删一条真有牙的判据。）
        "br_scope_empty_ok": [_br("main", "    if not suites:", "    if False:")],
        # 清单里不存在的那一路不点名：悄悄少算一路，症状与「跑得动」逐字节一样。
        "br_ghost_ok": [_br("closure",
                            '            problems.append(f"清单里这一路在磁盘上没有：'
                            '{os.path.relpath(path, ROOT)}")',
                            "            pass")],
        # 语法过不去的那一路被跳过：读不到却被说成没问题（本轮要拦的就是这一形）。
        "br_syntax_ok": [_br("closure",
                             '            problems.append(f"{os.path.relpath(path, ROOT)} 语法过不去，"',
                             '            problems.append(f"{os.path.relpath(path, ROOT)} 读过了，"')],
        # 候选解释器非零退出被读成「齐」：哪怕它 stdout 里吐了一份看着很对的表。
        "br_audit_rc_ok": [_br("audit", "    if r.returncode != 0:", "    if False:")],
        # 输出不是约定形状时退成「齐」：问不出结论与结论是齐必须是两回事。
        "br_audit_shape_ok": [_br("audit",
                                  '        return RC_BROKEN, f"{py} 的体检输出不是约定形状：'
                                  "{(r.stdout or '')[:120]!r}\"",
                                  '        return RC_OK, "约定形状（变异）"')],
        # 名字没传进子进程（拼进源码那一版的同类死法）：子进程报空表，此后每句「现场齐」都是空的。
        "br_argv_dropped": [_br("audit",
                                '[py, "-c", code, *sorted(names)]',
                                '[py, "-c", code]')],
        # 探针自己重实现一遍发现逻辑（第二套编号）：⑯/⑯b 钉的就是这一形，它们只看字，
        # 所以这条走源码副本而不是内存桩。
        "br_probe_own_scope": [_probe_src(
            "    try:\n"
            "        rc, files, err, _out, chan = gate.regression_suites()\n"
            "    except (OSError, subprocess.SubprocessError, ValueError) as exc:\n"
            "        return [], f\"清单通道起不来（{type(exc).__name__}: {exc}）\"\n"
            "    if rc != 0 or not files:\n"
            "        return [], (f\"退出码 {rc}、展开 {len(files)} 行｜stderr: {err or '(空)'}｜{chan}\")\n"
            "    return files, f\"退出码 {rc}、{len(files)} 项\"",
            "    import glob\n"
            "    files = [os.path.relpath(p, ROOT)\n"
            "             for d in DELIVERY_DIRS\n"
            "             for p in sorted(glob.glob(os.path.join(ROOT, d, 'test_*offline*.py')))]\n"
            "    return files, f\"自己扫了 {len(files)} 项\"")],
        # ── 接线（批跑入口 shell 文本）：门只印字不改退出码，本仓库已经付过两次学费 ──
        "br_sh_devnull": [_sh_(
            '"$PY" debug/offline_env_probe.py --check < /dev/null || probe_rc=$?',
            '"$PY" debug/offline_env_probe.py --check || probe_rc=$?')],
        # 「清单取不到」那一格被归并：两种相反的红退成一个词，读的人只会去看套件红不红。
        "br_sh_rc_split": [_sh_("if [ $probe_rc -eq 2 ]; then", "if [ $probe_rc -eq 92 ]; then")],
        # 手抄的现场清单回到批跑入口：它漏的正是藏在 import 链末端那一类名字。
        "br_sh_hardcode": [_sh_("probe_rc=0\n",
                                '"$PY" -c "import PyQt5" >/dev/null 2>&1 '\
                                '|| { echo "❌ 缺 PyQt5"; exit 2; }\nprobe_rc=0\n')],
        # 探针调用排到 --list 之前：探针读清单会把批跑入口再拉起来一次（自己叫自己）。
        "br_sh_before_list": [_sh_(
            'if [ "${1:-}" = "--list" ]; then',
            '"$PY" debug/offline_env_probe.py --check < /dev/null || probe_rc=$?\n'
            'if [ "${1:-}" = "--list" ]; then')],
        # 指路的依赖清单不存在（本轮实发：riskbird_build/requirements.txt 压根没这份文件）。
        "br_sh_guide": [_sh_(".github/requirements/b2bcollect.txt",
                             "riskbird_build/requirements.txt")],
        # 排除项的理由被缩成一个词：没理由的排除等于把判据改成沉默。
        "br_sh_exclusion_reason": [_sh_(
            "debug/offline_env_probe.py|不是断言套件而是现场探针本体（「这份解释器跑得动吗」"
            "那一问的唯一实现）；它的判据在 debug/batch_runner_offline_test.py 里，"
            "而它自己已被开跑前那一步真跑过一次",
            "debug/offline_env_probe.py|探针")],
        # 本轮 frozen 真实红过的那一形：空清单先让探针说话。文案换成「范围断了」后，
        # 屏幕上再到不了那句「没有任何套件被执行」——退出码仍非零，看起来完全合理。
        "br_sh_empty_preempted": [_sh_(
            '  echo "❌ 批跑清单是空的：没有任何套件被执行，这份「全绿」没有任何含义"\n'
            '  echo "   查 FILES 手写表与 DELIVERY_DIRS 自动发现两段"',
            '  echo "❌ 批跑清单是空的：现场探针问不出依赖"\n'
            '  echo "   查 FILES 手写表与 DELIVERY_DIRS 自动发现两段"')],
        # 空清单那一格只印字不收场（拿掉 exit 1）：下一句就是探针，红话依旧被它接走。
        "br_sh_empty_noexit": [_sh_(
            '  fi\n  exit 1\nfi\n\n# 环境检查为什么必须排在 --list 之后',
            '  fi\nfi\n\n# 环境检查为什么必须排在 --list 之后')],
        # 探针文件不在位的守卫拿掉：`python 不存在的文件` 的退出码是 2，而 2 在批跑入口里
        # 表示「清单取不到」——本轮自审实测出的那一格，拿掉它就回到两种相反的红共用一个词。
        "br_sh_probe_guard": [_sh_(
            'if [ ! -f debug/offline_env_probe.py ]; then', 'if false; then')],
        "0": [],
    }


# ── 每个模式必须杀掉哪些判据（片段取自判据标签里只在该子规则上成立的那几个字） ──
EXPECT = {
    "0": [],
    # upfront_probe 只改 preflight_hosts 的开局探针时机，因此只对「跑到预检」的套件有牙。
    # PG 从不走 preflight（全文无 preflight/_resolver_alive 引用），它的 ⑦i「夹具外」总闸
    # 对这条变异天然无感——挂上去只会把「误挂钩」读成「判据是装饰」。忠实杀手是 SE 那条。
    "upfront_probe": [(SE, ["不再问对照域"])],
    "no_probe": [(SE, ["判废时一条负结论都不写", "探针不许因为", "丢弃的家数"])],
    "dropped_zero": [(SE, ["丢弃的家数"])],
    "unknown_nodrop": [(SE, ["记进 unknown"])],
    "gui_five": [(SE, ["逐级点名到本轮真会走的每一级"])],
    "stage_three": [(WL, ["序号与名字"])],
    "no_rebind": [(WL, ["两家都投得出去"])],
    # f7serp：每条变异只退一个子规则，目标判据必须当场红。
    "serp_preempt": [(SE, ["一次都不问追加那一级", "也只问一次"])],
    "slot_shared": [(SE, ["问回来的候选真的去抓了", "记的是搜索反查那一层"])],
    "no_dedup": [(SE, ["去重必须在封顶之前", "后果必须落在值上"])],
    "escalate_on_close": [(WL, ["provider 根本不存在", "现网反查一次都没问"])],
    "gui_claims_all": [(SE, ["由本轮真会走的层级算", "没开的现网反查不许被说成"])],
    "no_off_line": [(WL, ["没开时本级自己说没开"])],
    # f9gui：每一条只退一个子规则，目标判据必须当场红。
    # needle 只取「按当前层级表逐级点名」这段稳定文字，**不带级数**：P 段扰动从 7 级改成
    # 8 级那天，判据文案里的数字会跟着动，带着数字的 needle 就会把「已经杀红」误报成
    # 「判据是装饰」——那是变异台最坏的一种假案（读的人会去删一条真有牙的判据）。
    "guides_frozen": [(WL, ["按**当前**层级表逐级点名"])],
    "stage_no_snapshot": [(WL, ["序号自动变成", "对得上阶段表里的位置"])],
    "guides_missing_one": [(WL, ["每一级都有自己的界面说明"])],
    "guides_silent": [(WL, ["条数与缺的级数一致"])],
    "session_stage_cache": [(WL, ["会话层不缓存级数"])],
    # f4f10：披露主数据层。每条变异只退一个子规则，EM 里盯那个子规则的判据必须当场红。
    "same_subject_core": [(EM, ["同核心名两家主体就拒", "证券简称时档二也拒",
                                "前缀命中两组主体时拒"])],
    # 「多市场合并的取值」这条**刻意不挂在这里**：合并同时存在于两处（build_index 建组时、
    # _merge 查询时），这条变异只退掉前者，查询期照样把两条合成一份，判据必然仍绿。
    # 那是纵深防御不是装饰——退一处只该看见另一处生效；真正盯「建索引时就合并」的那条
    # （「建索引时」）已被这条变异杀红，牙在不在那儿验过一次就够了。
    "secucode_key": [(EM, ["9 条记录 → 8 家主体", "主体标识是", "多个市场代码都留在账上",
                           "建索引时"])],
    "tail_wide": [(EM, ["留在核心名里", "两个法人两个总机"])],
    "no_rest_gate": [(EM, ["两个法人两个总机", "余段「暂定」不是纯尾缀"])],
    "institution_fallback": [(EM, ["机构类主体只认档一", "不得兜底对齐"])],
    # 需求 2：report() 的字段名漂一格，⑱ 的「正例要绿」必须当场红。
    # 连带那九条红是**预期**的，不是九个 bug：certain 一改名，每一份报告都在判定第一句
    # 就被读成「说不出自己是哪一版」，而下游那几条期望的是各自那一句话——它们拿到的
    # 永远是同一句 certain=false。想只留一条红就得把脚本里的判定顺序挨个往后搬，
    # 而那顺序本身就是「三种红不许互冒充」的实现，不该为了屏幕好看去动它。
    "sr_certain_drift": [(TU, ["正例：自报与本次注入逐条对上就放行"])],
    "no_shape_gate": [(EM, ["一个都不进表"])],
    "no_retry_gap": [(EM, ["第二次不再拉全量", "仍然把上一次的真结论念出来",
                           "重试闸拦下第二轮"])],
    "status_full_read": [(EM, ["从文件头那一小段就报得出账"])],
    "site_split_values": [(EM, ["时不算官网", "而不是退回路径末段", "路径末段**当域名"])],
    "abbr_floor_off": [(EM, ["剥完只剩三个字时不兜底"])],
    # 会话层接线：每条只退一个调用点，目标判据当场红。
    "stream_master_on": [(BT, ["后台那一路不承当主数据全量拉取"])],
    "stream_master_typo": [(BT, ["勾瀑布时每一轮都走完整瀑布"])],
    "finish_master_off": [(BT, ["收尾那一路不把 master_fetch 钉成 False"])],
    # 现场探针层（offline_env_probe）：每条只退一个子规则，目标判据当场红。
    # br_closure_shallow 那三条连带不是误挂：「不往随包代码深处走」同时断了链尾的 PIL
    # （真实现场那一条），同一个子规则的另一面。
    "br_branch_imports_lost": [(BR, ["主支包在", "真会走到的守卫分支里那只必须拦得住"])],
    "br_closure_shallow": [(BR, ["传递闭包算到链尾", "每一份随包文件都留了名", "引发误诊的 PIL"])],
    "br_no_func_body": [(BR, ["函数体内的延迟 import 也算硬要求"])],
    "br_no_fallback_probe": [(BR, ["主支包在"])],
    "br_only_import_error": [(BR, ["也算退路"])],
    "br_handler_hard": [(BR, ["except 支里的那只"])],
    "br_optional_wins": [(BR, ["闭包**合并那一步**"])],
    "br_guard_both_reachable": [(BR, ["不许拦停本机批跑"])],
    "br_guard_flip": [(BR, ["真会走到的守卫分支里那只必须拦得住"])],
    "br_dir_is_pkg": [(BR, ["仍按第三方算"])],
    "br_pkg_ignored": [(BR, ["仓库目录包（有 __init__.py）不是第三方依赖"])],
    "br_first_hit_only": [(BR, ["同名跨目录的两份实现都进闭包"])],
    "br_no_own_dir": [(BR, ["自己所在的目录"])],
    "br_scope_empty_ok": [(BR, ["「范围取不到」那一格", "rc=0 但展开 0 行"])],
    "br_ghost_ok": [(BR, ["这一路在磁盘上没有"])],
    "br_syntax_ok": [(BR, ["语法过不去的那一路"])],
    "br_audit_rc_ok": [(BR, ["退出码非零：哪怕它 stdout"])],
    "br_audit_shape_ok": [(BR, ["宁可报「问不出结论」"])],
    "br_argv_dropped": [(BR, ["名字走 argv 传进子进程"])],
    "br_probe_own_scope": [(BR, ["探针里没有第二套发现逻辑", "不是探针自己同名重写一遍再自证"])],
    # 接线层（批跑入口 shell 文本）：红的都是 C 段那几条，它们验的是「真调用 + 分开接」。
    # D 段的真实现场走的是真入口的 --list（不读副本）：范围那一问永远对真文件。
    "br_sh_devnull": [(BR, ["每一处调用都带 < /dev/null"])],
    "br_sh_rc_split": [(BR, ["四种结论分开接"])],
    "br_sh_hardcode": [(BR, ["可执行文本**里拿掉了"])],
    "br_sh_before_list": [(BR, ["探针调用排在"])],
    "br_sh_guide": [(BR, ["依赖清单必须真在位"])],
    "br_sh_exclusion_reason": [(BR, ["每一项都必须带**自洽的**理由"])],
    "br_sh_empty_preempted": [(BR, ["空清单在**开跑前、探针之前**"]),
                              (BR, ["开跑前与收工后两处都要在"])],
    "br_sh_empty_noexit": [(BR, ["空清单在**开跑前、探针之前**"])],
    "br_sh_probe_guard": [(BR, ["探针文件不在位单独一格"])],
}


def _drop_seams():
    """收掉变异台自己写的两份取证副本。

    必须显式收：子进程一律 `os._exit` 收尾（Qt/线程在解释器收尾时随机段错误），atexit 根本
    不会跑。上一轮的临时仓库回收判据（㊴）盯着的是同一格：没人认领的夹具目录不会报错，
    只会越积越多，直到有人拿它们当现场。
    """
    for var in ("B2B_PROBE_SRC", "B2B_PROBE_SH"):
        path = os.environ.pop(var, "")
        if path:
            try:
                os.unlink(path)
            except OSError:
                pass


def _child(mode, suite):
    sys.path.insert(0, PKG)
    sys.path.insert(0, HERE)          # 批跑判定层的套件与被测模块就在本目录
    # 打桩只在子进程里做：桩必须发生在被测模块已导入、套件尚未开始的那一刻。
    try:
        for p in build_patches()[mode]:
            p()
    except BaseException as e:                    # 变异夹具本身写错也要能报出来
        sys.stderr.write(f"变异打桩失败：{type(e).__name__}: {e}\n")
        sys.stderr.flush()
        _drop_seams()
        os._exit(CRASH)
    # 套件直接写真实 stdout：SE/WL 用 os._exit 收尾，任何 redirect 缓冲区都会在退出前蒸发，
    # 于是父进程读不到判据行、只能看到 rc=1——把「判据被杀掉」误读成「套件崩了」。
    try:
        runpy.run_path(suite if os.path.isabs(suite) else os.path.join(PKG, suite),
                       run_name="__main__")
    except SystemExit as e:                        # 没碰 Qt 的套件（PG）走可捕获的 sys.exit
        code = int(e.code or 0)
        _drop_seams()
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(code)
    except BaseException as e:
        sys.stderr.write(f"\n变异台子进程异常：{type(e).__name__}: {e}\n")
        if isinstance(e, AssertionError) and str(e).startswith("FAIL"):
            # 抬异常型套件（check 直接 assert 的那几路）的红只活在这一句里。给它一个自己的
            # 标记行，父进程就不必拿「退出码 97」这一个数字去猜两件相反的事：
            # 「判据被杀红」与「变异自己把子进程打崩」。要猜就会猜错，猜错就要删真判据。
            sys.stderr.write("MUTATION-FAIL-LINE " + " ".join(str(e).split()) + "\n")
        traceback.print_exc()
        _drop_seams()
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(CRASH)
    _drop_seams()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)


def _run(mode, suite):
    """父进程：派孩子，返回 (FAIL 标签列表, 判据总数, 退出码, 孩子输出尾巴)。

    尾巴是必需的：rc=97 只会告诉我们孩子走了，为什么走必须看得见。

    FAIL 从两处收：屏幕上的 ``FAIL ...`` 行（累加型套件），以及孩子自己标的
    ``MUTATION-FAIL-LINE``（抬异常型套件红在第一条就终止，屏幕上根本不会有 FAIL 行）。
    只收前一处会把「判据被杀红」报成「变异不成立」，那是两种相反的结论。
    """
    r = subprocess.run([sys.executable, os.path.abspath(__file__), "--child", mode, suite],
                       capture_output=True, text=True, cwd=HERE)
    out = r.stdout
    fail_p, total_ps = _OUT_KIND.get(os.path.basename(suite), _OUT_KIND_DEFAULT)
    fails = [ln.strip()[len(fail_p):].strip() for ln in out.splitlines()
             if ln.strip().startswith(fail_p)]
    for ln in r.stderr.splitlines():
        if ln.startswith("MUTATION-FAIL-LINE "):
            label = ln[len("MUTATION-FAIL-LINE "):].strip()
            if label not in fails:                 # traceback 里同一句还会再出现一次
                fails.append(label)
    total = sum(1 for ln in out.splitlines() if ln.strip().startswith(total_ps))
    tail = [ln for ln in (out.rstrip() + "\n" + r.stderr.rstrip()).splitlines() if ln.strip()][-6:]
    return fails, total, r.returncode, tail


def _pin_interpreter():
    """现场不合格就当场停下（而不是带病跑完、也不是挑一份别的解释器重开）。

    判定与批跑同源：都走 debug/offline_env_probe.py 那一句 `--check`，这里不另写一套
    依赖清单（写了就会与批跑分叉，而分叉的方向永远是「变异台比批跑宽或窄」）。
    变异台拿缺依赖的解释器跑会出一片 rc=97，那与「判据被杀红」是两种相反的诊断。
    """
    sys.path.insert(0, HERE)
    import offline_env_probe as P
    rc = P.main(["--check"])
    if rc == P.RC_OK:
        return
    raise SystemExit(f"❌ 变异台不在一份跑得动本轮套件的解释器上（探针退出码 {rc}）："
                     "上面的点名就是结论，先 pip install 或换 PY= 重跑，不要带病跑完全批")


def main():
    _pin_interpreter()
    modes = sys.argv[1:] or list(EXPECT)
    bad = 0
    for mode in modes:
        if mode not in EXPECT:
            print(f"未知模式 {mode}，可选：" + " / ".join(EXPECT))
            return 2
        print(f"\n════ 模式 {mode} ════")
        if mode == "0":
            # 基线：那几套离线套件都必须真全绿——0 条 FAIL、rc==0，且确实吐了判据行
            # （total>0 挡住「孩子提前崩、屏幕空白的假绿」）。
            for suite in (SE, WL, PG, EM, BT, BR, TU):
                fails, total, rc, tail = _run(mode, suite)
                ok = (not fails) and rc == 0 and total > 0
                print(f"  {'✅' if ok else '❌'} {suite}：{total - len(fails)}/{total}、rc={rc}")
                for f in fails:
                    print(f"     基线就红：{f[:88]}")
                if not ok and not fails:
                    print("     孩子没判据可报（被带走或提前崩），尾巴：\n"
                          + "\n".join(f"     | {ln[:100]}" for ln in tail))
                bad += 0 if ok else 1
            continue
        # 变异模式：逐个 (suite, needles) 跑对应套件，要求每条期望判据都被这条变异杀红。
        for suite, needles in EXPECT[mode]:
            fails, total, rc, tail = _run(mode, suite)
            if rc == CRASH and not fails:
                # 孩子是被变异自己打崩的（打桩串过期、变异代码不合法、闭包依赖未定义的量），
                # 而且全程没红过一条判据——拿它给判据定罪就是假案。
                # 这时“判据没变红”完全是假象：先证明变异是一次**干净的退回**，才有资格给
                # 判据定罪——把夹具自身的错读成“判据是装饰”，比装饰更坏（会误删真判据）。
                print(f"  ❌ {suite} 子进程异常（rc={CRASH}）：这条变异本身不成立，"
                      f"不能拿来给判据定罪")
                print("     尾巴：\n" + "\n".join(f"     | {ln[:100]}" for ln in tail))
                bad += 1
                continue
            if rc == CRASH:
                # 抬异常型套件：红在第一条就终止。按已红的那条对拍，但「后面没跑到」必须
                # 当众说出来——它是一次真的可见性损失，不是一句提醒。
                print(f"  ⚠ {suite} 红在一条断言上就终止了（这套 check 抬 AssertionError）："
                      f"后面的判据本轮没跑到，只能按已红的那条对拍")
            killed = [n for n in needles if any(n in f for f in fails)]
            missed = [n for n in needles if not any(n in f for f in fails)]
            for n in killed:
                print(f"  ✅ 「{n}」在 {suite} 里被这条变异杀掉了")
            for n in missed:
                print(f"  ❌ 「{n}」在 {suite} 里没变红 → 这条判据是装饰")
                # 判据行根本没跑到的情况下，“没变红”也可能是套件提前收尾：把尾巴一起给出来。
                if total == 0 or rc != 1:
                    print(f"     退出码 rc={rc}、判据行 {total} 条，尾巴：\n"
                          + "\n".join(f"     | {ln[:100]}" for ln in tail))
            bad += len(missed)
            if rc == 0 and not fails:
                print(f"     ⚠ {suite} 变异后仍全绿（rc=0）：这条变异没落到被测路径上")
                bad += 1
            extra = [f for f in fails if not any(n in f for n in needles)]
            for f in extra:
                print(f"     连带红（人工确认盯的是不是同一个子规则）：{f[:88]}")
    print("\n" + ("对拍通过 ✅：每条新判据都被自己的变异杀掉" if bad == 0
                  else f"对拍不通过 ❌：{bad} 处需要处理"))
    return 1 if bad else 0


if __name__ == "__main__":
    if sys.argv[1:2] == ["--child"]:
        _child(sys.argv[2], sys.argv[3])
    else:
        sys.exit(main())
