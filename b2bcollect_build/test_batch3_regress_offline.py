# 离线自测：2026-11-17 整轮（582 家 / 45 分钟）日志逐条归因后的修复回归。
# 一轮实机暴露的八类问题里，能在离线层钉死的都钉在这里；钉不死的（真站形态）
# 全部有实机取证写进注释，别把"当时能过"当"以后也对"。
#   ① 1688 两条搜索 URL 缺查询串分隔符 `?`（实机落地 `offer_search.htmkeywords=%CB%AE…`，
#      keywords 被粘进路径段）→ 整轮 11 个词只有 1 个词出货，其余被归因成「暂无该词公司」；
#   ② `beginPage` 不生效时旧代码写「公司池已枯竭」（方向相反的谎）→ 现在按同一批卡片
#      的 id 集合差分如实说「翻页参数没生效」；2026-11-18 把这一条从 1688 两条渠道扩到
#      全部按「连续 N 页 0 新增」早停的列表循环（②e/②f 两格机器判定）。
#   ③ 「页面取回来了却零卡片」只有一句「无公司数据」→ 现在分四型（zero/block/shell/layout），
#      世界工厂网「翻 22 页见 0 条」与 1688「第 1 页起无结果」从此各说各的病因；
#   ④ 相关性早过滤成片误杀（「耐压舱」砍「安阳潜航海洋装备制造有限公司」）→ 加查询条件
#      同域词模型（伪相关性反馈），只**否决误砍**、永不新增砍单；
#   ⑤ 边采边补只跑「四项全空」那一档 + 工商队列收尾才生成（1432 家次永远查不完）→
#      每一轮都走完整瀑布，缺联系方式的家名行级入队；
#   ⑥ 爱企查「凭证在、详情整片空壳」时日志里没有任何一句要人动手 → 现在点亮登录引导
#      并给一次限时窗口（只给一次，防止在队首打转烧名单）；
#   ⑦ 没有入库模板文件时兜底表头写成 18 列（同一组 9 列重复两遍）→ 退回接口那 9 列，
#      三份副本（b2bcollect/ali1688/riskbird 三个独立产物）一起验；
#   ⑧ 取页失败 13 页里 12 页只念一句「【未分型】｜请求层未给出原因（见紧邻上一行）」→
#      归因下沉到 _note_page_fail：现场痕迹（导航失败/熔断/状态码/拦截/当前页地址）自己取证。
#   ⑨ 本轮另拿两类「测试自己先失真」的缺陷入帐：隔壁产品目录把本产品的同名模块
#      掉包（⑦a 就是那一格报红的）、以及旧断言钉在被删掉的旧写法上（⑤/㉚）——
#      前者这里扫全部离线套件的 sys.path 口径，后者靠每个套件自己跑绿。
# 全程不联网、不启 Chrome。
import sys
import tempfile
import importlib.util
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
_ROOT = Path(__file__).resolve().parent.parent
# 外部目录一律 **append**：与 site_adapters 里那一条同口径（本目录模块永远优先）。
# 写成 insert(0) 会把 riskbird_build/ali1688_build 的同名副本顶到本目录前面，
# 测试断言的就是老副本——绿灯只会把主包里真正生效那一份的回归掩盖掉。
for _p in (str(_ROOT / "ali1688_build"), str(_ROOT / "riskbird_build")):
    if _p not in sys.path:
        sys.path.append(_p)

import cdp_base                                                   # noqa: E402
from cdp_base import CdpSiteCrawler, empty_page_triage            # noqa: E402
from b2b168_crawler import B2b168Crawler                          # noqa: E402
import ali1688_crawler as _a1688                                  # noqa: E402
import customer_library_cleaner as _cl                            # noqa: E402
import yzd_client as _yzd                                         # noqa: E402
from parallel_session import ParallelSession, SHELL_RECOVERY_WAIT_S   # noqa: E402

assert _cl.__file__.startswith(str(_ROOT / "b2bcollect_build")), \
    f"测试导错了 cleaner（被副本遮蔽）：{_cl.__file__}"


def _load_copy(mod_name, rel_path):
    """按文件路径加载同名副本（三份 customer_library_cleaner 各自打进自己的产物，
    必须逐份验，普通 import 只能拿到其中一份）。"""
    spec = importlib.util.spec_from_file_location(mod_name, str(_ROOT / rel_path))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m

ok = 0


def check(name, cond, extra=""):
    global ok
    assert cond, f"FAIL: {name}｜{extra}" if extra else f"FAIL: {name}"
    ok += 1


# ── ① 1688 查询串分隔符 ──────────────────────────────────────────────
check("1a 两条搜索 URL 常量必须以 `?keywords=` 起查询串（缺 `?` 那一形实机零产出）",
      "?keywords=" in _a1688.MAIN_SEARCH_URL and "?keywords=" in _a1688.COMPANY_SEARCH_URL
      and ".htm&" not in _a1688.MAIN_SEARCH_URL and ".htm&" not in _a1688.COMPANY_SEARCH_URL,
      f"{_a1688.MAIN_SEARCH_URL} ↔ {_a1688.COMPANY_SEARCH_URL}")
check("1b 校验器把 `.htm&` 修成 `.htm?`，参数之间的 `&` 一个都不许动",
      _a1688.checked_search_url("https://s.1688.com/selloffer/offer_search.htm&keywords=A&beginPage=2")
      == "https://s.1688.com/selloffer/offer_search.htm?keywords=A&beginPage=2",
      "修复形状不符")
check("1c 正确的 URL 原样返回；修错时必须在日志里说（静默修复＝把缺陷藏进正常账目）",
      _a1688.checked_search_url("https://s.1688.com/company/company_search.htm?keywords=A&beginPage=1")
      == "https://s.1688.com/company/company_search.htm?keywords=A&beginPage=1"
      and len(_a1688._bad_sep_warned) == 0,
      f"{_a1688._bad_sep_warned}")
_logs1 = []
_a1688.checked_search_url("https://www.1688.com/zw/page.htm&keywords=B", _logs1.append)
check("1d 就地修正那一句要打得出来（本轮实机就是日志一句没提才查了两轮）",
      any("缺查询串分隔符" in m for m in _logs1), str(_logs1))

# ── ② 翻页参数是否真的在干活 ──────────────────────────────────────────
_c2 = _a1688.Ali1688Crawler._page_repeat    # staticmethod：不拉浏览器也能判（1688 两条渠道共用）
check("2a 同一批卡片（集合相等）→ 重合率 1.0",
      _c2({"1", "2", "3"}, {"3", "2", "1"}) == 1.0)
check("2b 两页内容不同 → 不报警（条数相等也不算）",
      _c2({"1", "2"}, {"3", "4"}) == 0.0
      and _c2({"1", "2", "3"}, {"1", "2", "9"}) < 0.9)
check("2c 空集合不许冒充「翻页失效」（任一页没卡片时无从比对）",
      _c2(set(), {"1"}) == 0.0 and _c2({"1"}, set()) == 0.0)
_src2 = (_ROOT / "ali1688_build" / "ali1688_crawler.py").read_text(encoding="utf-8")
check("2d 三条渠道的翻页循环都必须走集合差分，而且「公司池已枯竭」这句不许再从日志里念出来"
      "（注释里引用旧写法不算，它说的是“为什么那是谎”）",
      _src2.count("self._page_repeat(prev_ids, ids)") == 3
      and "翻页参数 beginPage 没生效" in _src2
      and not any("公司池已枯竭" in ln for ln in _src2.splitlines() if "_log(" in ln),
      "1688 翻页归因回退了")

# 2e 只修 1688 一条渠道等于没修：本轮实机「世界工厂网 22 页 0 条」查的就是同一族。
# 机器判定写成一条普适不变量：凡是靠「连续 N 页 0 新增」早停的列表循环，都必须先拿
# 「这一页与上一页是不是同一批条目」自证一次，否则两种相反的病因在日志里同形。
import ast as _ast2
import inspect as _ins2
_iter_fns, _off2e = [], []
for _m2 in ("cdp_base", "gongchang_crawler", "gi_crawler", "maimaib2b_crawler", "makepolo_crawler",
            "b2b168_crawler", "gys_crawler", "qihuo11467_crawler", "aipage_crawler",
            "solarbe_crawler", "ali1688_crawler"):
    _mod2 = importlib.import_module(_m2)
    assert "b2bcollect_build" in _mod2.__file__ or "ali1688_build" in _mod2.__file__, \
        f"②e 取错了模块（被同名副本遮蔽）：{_m2} → {_mod2.__file__}"
    for _cls2 in [v for v in vars(_mod2).values() if isinstance(v, type)]:
        for _nm2, _fn2 in list(vars(_cls2).items()):
            _raw2 = _fn2.__func__ if isinstance(_fn2, (staticmethod, classmethod)) else _fn2
            if not (callable(_raw2) and getattr(_raw2, "__name__", "").startswith("_iter_keyword")):
                continue
            try:
                _txt2 = _ins2.getsource(_raw2)
            except (OSError, TypeError):
                continue
            # 只看语法树里的变量名与调用名：docstring/注释里提一提「stale」不算这一路做了早停
            _tree2 = _ast2.parse(_txt2.strip())
            _names2 = {_n2.id for _n2 in _ast2.walk(_tree2) if isinstance(_n2, _ast2.Name)}
            _calls2 = {getattr(_n2.func, "attr", "") for _n2 in _ast2.walk(_tree2)
                       if isinstance(_n2, _ast2.Call)}
            if not any(_x.startswith("stale") for _x in _names2) and "prev_sig" not in _names2:
                continue          # 这一路不做「0 新增早停」，不归本判据管
            _key2 = f"{_raw2.__module__}.{_raw2.__qualname__}"
            if _key2 in {_k2 for _k2, _ in _iter_fns}:
                continue          # 同一个基类方被 N 个模块 import 进来只算一路（不然在册数虚胖）
            _iter_fns.append((_key2, "_page_repeat" in _calls2 or "prev_sig" in _names2))
            if not ("_page_repeat" in _calls2 or "prev_sig" in _names2):
                _off2e.append(_key2)
check("2e 每个按「连续 N 页 0 新增」早停的列表循环都用集合差分自证过「是不是同一批条目」"
      f"（共 {len(_iter_fns)} 路循环在册）",
      len(_iter_fns) >= 10 and not _off2e, "没自证的：" + "、".join(_off2e))

# 2f 「枯竭」只能出现在否定句里（「不是『公司池枯竭』」）：断言式的枯竭结论一律点名。
# 拿全文匹配会把解释「为什么那是谎」的注释与 docstring 一起算成违约，那是假阳性。
_off2f = []
for _d2 in ("b2bcollect_build", "riskbird_build", "ali1688_build"):
    for _f2 in sorted((_ROOT / _d2).glob("*.py")):
        if _f2.name.startswith("test_"):
            continue
        _srct2 = _f2.read_text(encoding="utf-8")
        if "枯竭" not in _srct2:
            continue
        _t2 = _ast2.parse(_srct2)
        _doc2 = set()
        for _n2 in _ast2.walk(_t2):
            if isinstance(_n2, (_ast2.Module, _ast2.ClassDef, _ast2.FunctionDef,
                                _ast2.AsyncFunctionDef)):
                _b2 = getattr(_n2, "body", None)
                if _b2 and isinstance(_b2[0], _ast2.Expr) and isinstance(_b2[0].value, _ast2.Constant):
                    _doc2.add(id(_b2[0].value))
        for _n2 in _ast2.walk(_t2):
            if (isinstance(_n2, _ast2.Constant) and isinstance(_n2.value, str)
                    and id(_n2) not in _doc2 and "枯竭" in _n2.value and "不是" not in _n2.value):
                _off2f.append(f"{_d2}/{_f2.name}:{_n2.lineno}")
check("2f 任何会渲染的字符串里「枯竭」只许出现在否定句（断言式枯竭结论＝方向相反的谎）",
      not _off2f, "、".join(_off2f))

# ── ③ 空页四型分诊 ───────────────────────────────────────────────────
_big = "<html><head><title>搜索</title></head><body>" + ("<div>正文</div>" * 9000) + "</body></html>"
check("3a 验证页：只看地址/标题里的拦截特征（实机 A/B 探针就栽在这一形上）",
      empty_page_triage(_big, "https://s.1688.com/x/_____tmd_____/punish?x5secdata=abc")[0] == "block")
check("3b 站方自己说没有：那句原文要抄进日志（世界工厂网「抱歉，没有找到」实机取证）",
      empty_page_triage("<html><body><p>抱歉，没有找到相关的公司信息</p></body></html>")[0] == "zero")
check("3c 薄壳页按字节数说话",
      empty_page_triage("<html><body>ok</body></html>")[0] == "shell")
check("3d 大字页没卡片也没那句「没找到」＝先怀疑改版，绝不默认成「本站没数据」",
      empty_page_triage(_big)[0] == "layout")
check("3e 隐藏验证码容器不许把好页说成风控页（图标记只认地址与标题）",
      empty_page_triage(_big.replace("<div>正文</div>", '<div class="J-captcha">x</div>', 1))[0]
      != "block")
_c3 = empty_page_triage(_big)
_c3b = _a1688.empty_page_triage(_big)
check("3f 1688 镜像那一份与 cdp_base 同判（两套尺子就会有两套结论）",
      _c3[0] == _c3b[0], f"{_c3} ↔ {_c3b}")
_src3 = (_ROOT / "b2bcollect_build" / "gongchang_crawler.py").read_text(encoding="utf-8")
check("3g 世界工厂网那一页确实走 `_no_result_line`（分诊要落在真链路上，不是只活在函数里）",
      "_no_result_line" in _src3, "世界工厂网没接分诊")

# ── ④ 相关性：同域词模型只否决误砍 ────────────────────────────────────
KW = "耐压舱"
_HIT_A = "青岛海洋装备制造有限公司 耐压舱,水下连接器"
_HIT_B = "烟台海洋装备科技实业有限公司 耐压舱体,观察窗"
_HIT_C = "无锡海洋装备工程有限公司 钛耐压舱,法兰"
# 全表分母必须**混多个关键词、多类主营**：稀有度那一档的尺子是「这一字组在全表
# 判定过的候选里占多少」，只喂单一关键词就等于拿命中样本自己当分母——分子分母同源，
# 「海洋」永远 100%，同域词永远学不出来（第一版实现就是这个形状，被这一条测试打回来）。
_OTHER = ["深圳传感网科技有限公司 水下传感器,水声通信机",
          "杭州海洋仪器厂 温深仪,采水器"] * 7          # 含「海洋」但主营不相干
_NOISE = ["东莞精密模具配件厂 冲压件,注塑模",
          "苏州传动设备有限公司 变频器,伺服电机",
          "佛山铝业科技有限公司 氧化铝材,门窗料",
          "温州阀门管件厂 球阀,法兰管件",
          "济南试验仪器有限公司 拉力机,疲劳台"] * 4   # 与「海洋/装备」毫无关系的一类厂
_c4 = B2b168Crawler()
_c4.relevance_guard = True
_c4._log = lambda m: None
check("4a 冷启动（模型没学会）时旧口径一条不变：无关公司仍判不相关",
      not _c4.is_relevant("橡胶手套", "阳东县江华实业有限公司 地桩,新能源智能餐车,滚筒刷")
      and not _c4.is_relevant(KW, "义乌市米汇日用品商行 塑料盆,脸盆"),
      "冷启动就放宽＝把闸门拆了")
for _t in ([_HIT_A, _HIT_B, _HIT_C] + _OTHER + _NOISE):
    _c4.is_relevant(KW, _t)
check("4b 学到的同域词能救回零重叠的真同行（实机被砍的安阳潜航就是这一形）",
      _c4.is_relevant(KW, "安阳潜航海洋装备制造有限公司 水下机器人,探测器"),
      f"pos={_c4._prf['pos'].get(KW)} seen={_c4._prf['seen']} "
      f"海洋={_c4._prf['df'].get('海洋')} 装备={_c4._prf['df'].get('装备')}")
check("4c 救回要有账：rescued 计数落进 stats()，否则「早过滤变少了」看不出是口径变了",
      _c4._prf["rescued"] == 1 and _c4.stats()["rel_rescued"] == 1,
      str(_c4._prf["rescued"]))
check("4d 全表通行证词不算同域证据：只共享「科技/有限公司」这类到处都有的字组仍然砍",
      not _c4.is_relevant(KW, "某科技有限公司 日用百货,五金"),
      f"稀有度这一档失效就会反向放进垃圾商家｜公司={_c4._prf['df'].get('公司')} "
      f"seen={_c4._prf['seen']}")
check("4e 其它关键词的模型互不串用（每个词只学自己的命中）",
      not _c4.is_relevant("声学释放器", "安阳市日用品商行 塑料盆,脸盆"),
      "串了就等于用别的词的相关性放行")

# ── ⑤ 边采边补：每一轮都走完整瀑布 ───────────────────────────────────
_s5 = ParallelSession(enabled=[])
_s5.waterfall = True
_s5.site_enrich = True
_s5._log = lambda m: None
_s5._enrich_stream = {"rounds": 0, "sites": 0, "ok": 0, "new_values": 0,
                      "queued": 0, "reasons": {}}
_seen5 = {}
_s5._all_live_records = lambda: [{"公司名称": "甲公司"}]


def _wf5(reason, records, **kw):
    _seen5["reason"], _seen5["kw"] = reason, kw
    return {"network": {"sites": 3, "ok": 2, "new_values": 5},
            "registry": {"queued": 7, "missing": 9}}


_s5._run_waterfall = _wf5
_s5._enrich_round()
check("5a 勾选瀑布时后台轮走完整瀑布（旧写法只调 enrich_records + only_missing=True，"
      "本轮 42 轮只碰 18 个官网、净新增 1 个值）",
      _seen5.get("reason") == "边采边补" and "only_missing" not in _seen5["kw"]
      and _seen5["kw"].get("verbose") is False, str(_seen5))
check("5b 账目读瀑布的嵌套形状（读扁平键就会报「0 个官网 0 个值」这种假话）",
      _s5._enrich_stream["sites"] == 3 and _s5._enrich_stream["new_values"] == 5
      and _s5._enrich_stream["queued"] == 7, str(_s5._enrich_stream))
_seen5b = {}
_s5b = ParallelSession(enabled=[])
_s5b.waterfall = False
_s5b.site_enrich = True
_s5b._log = lambda m: None
_s5b._enrich_stream = {"rounds": 0, "sites": 0, "ok": 0, "new_values": 0,
                       "queued": 0, "reasons": {}}
_s5b._all_live_records = lambda: [{"公司名称": "甲公司"}]
_s5b._enrich_single_hop = lambda reason, records, **kw: (_seen5b.update(reason=reason, kw=kw)
                                                         or {"sites": 1, "ok": 1, "added": 2})
_s5b._enrich_round()
check("5c 没勾瀑布时退回单跳那一档，同样不许带 only_missing=True 的窄口径",
      _seen5b.get("reason") == "边采边补" and "only_missing" not in _seen5b["kw"],
      str(_seen5b))
check("5d 工商队列没绑任务文件夹时必须明说「一家都没投出去」（假装有队列比没队列更坏）",
      _s5._waterfall_queue(["甲公司", "乙公司"]) == 0, "投出去 0 家却说成了已排队")
check("5e 每轮预算 ≤ 收尾预算，且收尾宽限覆盖「预算 + 同主体复用 + 投队列」两步",
      __import__("parallel_session").ENRICH_STREAM_BUDGET <= _s5.site_enrich_budget
      and _s5._enrich_worker_stop is False, "宽限口径回退了")

# ── ⑥ 空壳登录引导 ───────────────────────────────────────────────────
_src6 = (_ROOT / "b2bcollect_build" / "parallel_session.py").read_text(encoding="utf-8")
check("6a 连续空壳且 cookie 探针仍说「已登录」时也要点亮登录引导（爱企查实机只补到 1 家）",
      'login_required_signal' in _src6 and "shell_login_asked" in _src6
      and "详情空壳但登录凭证仍在" in _src6, "登录引导又被收成只有 need_login 才发")
check("6b 窗口只给一次：`login_state()` 在这一形会立刻回 ok，反复给窗口＝在队首打转烧名单",
      _src6.count("shell_login_asked") >= 2 and SHELL_RECOVERY_WAIT_S >= 60,
      str(SHELL_RECOVERY_WAIT_S))

# ── ⑦ 入库表头：没模板时退回接口那 9 列 ───────────────────────────────
check("7a 兜底表头逐字等于入库接口契约（旧那 18 列把同组 9 列填了两遍，用户看到的是「数据乱」）",
      _cl.FALLBACK_HEADER == list(_yzd.IMPORT_HEADERS) and len(_cl.FALLBACK_HEADER) == 9
      and len(set(_cl.FALLBACK_HEADER)) == 9, str(_cl.FALLBACK_HEADER))
_out7, _blk7, _head7 = _cl.read_template_layout("", log=lambda m: None)
check("7b 无模板时只有外呼一块，第二块为空（写表就不会再重复填一遍）",
      _blk7 == {} and len(_out7) == 9 and _head7 == list(_yzd.IMPORT_HEADERS),
      f"{_blk7} {_head7}")
_logs7 = []
_cl.read_template_layout("", log=_logs7.append)
check("7c 走兜底这件事必须在日志里说清（否则「这次 9 列、上次 18 列」没人能解释）",
      any("没找到入库模板文件" in m for m in _logs7), str(_logs7))
_p7 = Path(tempfile.mkdtemp()) / "clean.xlsx"
_cl.write_clean_excel([{"客户名称": "张三", "企业名称": "甲公司", "岗位": "采购",
                       "手机号": "13800000000", "固话": "", "邮箱": "a@b.com",
                       "微信号": "", "数据来源": "中国制造网", "备注": "x"}],
                      str(_p7), "", log=lambda m: None)
from openpyxl import load_workbook                                # noqa: E402
_ws7 = load_workbook(_p7).active
_row7 = [c.value for c in next(_ws7.iter_rows(min_row=1, max_row=1))]
_body7 = [c.value for c in next(_ws7.iter_rows(min_row=2, max_row=2))]
check("7d 真写出来的表就是 9 列一行数据（列名不重复、值不重复填两遍）",
      _row7 == list(_yzd.IMPORT_HEADERS) and _body7[1] == "甲公司"
      and len([v for v in _body7 if v == "甲公司"]) == 1, f"{_row7} {_body7}")
# 三份副本是**三个独立桌面产物**（build-1688 / build-riskbird / build-b2bcollect 都在云
# 打包），各自吃本目录那一份 cleaner：只改主包，用户从 1688 版/风鸟版导出的表仍旧是
# 18 列。这一条是这类“同名副本只改一份”缺陷的守门人（历史同型：SOURCE_FIELDS 漏登记）。
_CLEANERS7 = [("b2bcollect_build", _cl)]      # 逐份验就要逐份**显式绑定**：下面 7g 直接取这里的名字
for _nm, _rel in (("_cl_1688", "ali1688_build/customer_library_cleaner.py"),
                  ("_cl_rb", "riskbird_build/customer_library_cleaner.py")):
    globals()[_nm] = _load_copy(_nm, _rel)
    _CLEANERS7.append((_rel.split("/")[0], globals()[_nm]))
    _cd = globals()[_nm]
    _yz = _load_copy(f"_yz_{_nm}", str(Path(_rel).parent / "yzd_client.py"))
    check(f"7e {_rel.split('/')[0]} 副本同样是 9 列兜底（只改主包就等于只修一个产物）",
          _cd.FALLBACK_HEADER == list(_yz.IMPORT_HEADERS)
          and len(_cd.FALLBACK_HEADER) == 9 and len(set(_cd.FALLBACK_HEADER)) == 9,
          f"{_cd.FALLBACK_HEADER}")
    _lg7 = []
    _o7, _b7, _h7 = _cd.read_template_layout("", log=_lg7.append)
    check(f"7f {_rel.split('/')[0]} 副本无模板时第二块为空、并说出那句兜底说明",
          _b7 == {} and len(_o7) == 9 and _h7 == list(_yz.IMPORT_HEADERS)
          and any("没找到入库模板文件" in m for m in _lg7),
          f"{_b7} {_h7} {_lg7}")
# 7g 双块（真模板在位）那一路：⑦ 把兜底从 18 列改成 9 列之后，`read_template_layout` 的
# 模板分支在整个离线体系里一个用例都没有（7b/7e/7f 验的全是「无模板→第二块为空」），
# 而主包那条双块断言一直钉在兜底表头上，红了才把这个覆盖空洞暴露出来。三份副本是
# 三个独立交付物（客户把模板放进输出目录时谁都可能只填一块），所以逐份验。
from openpyxl import Workbook as _WB7                              # noqa: E402
_TMPL7 = Path(tempfile.mkdtemp()) / "客户库数据导入模板（双块）.xlsx"
_wb7 = _WB7()
_wb7.active.append(list(_yzd.IMPORT_HEADERS[:8]) + ["外呼备注"]
                   + list(_yzd.IMPORT_HEADERS[:8]) + ["备注"])
_wb7.save(_TMPL7)
_ROW7 = {"客户名称": "甲公司", "企业名称": "甲公司", "岗位": "采购", "手机号": "13800000000",
         "固话": "020-80000000", "邮箱": "a@b.com", "微信号": "wx1", "数据来源": "风鸟",
         "备注": "跟进中"}
for _nm7, _cd7 in _CLEANERS7:
    _o7g, _b7g, _h7g = _cd7.read_template_layout(str(_TMPL7), log=lambda m: None)
    _p7g = _TMPL7.parent / f"out_{_nm7}.xlsx"
    _cd7.write_clean_excel([dict(_ROW7)], str(_p7g), template_path=str(_TMPL7), log=lambda m: None)
    _r7g = [c.value for c in next(load_workbook(_p7g).active.iter_rows(min_row=2, max_row=2))]
    check(f"7g {_nm7} 按真模板出 18 列，两块逐格相同且手机号都在（第二块不许留空）",
          len(_h7g) == 18 and _b7g and len(_o7g) == 9
          and [str(v or "") for v in _r7g[:9]] == [str(v or "") for v in _r7g[9:18]]
          and str(_r7g[3] or "") == str(_r7g[12] or "") == "13800000000",
          f"{_h7g} | {_b7g} | {_r7g}")

# ── ⑧ 取页失败归因下沉：调用方不传原因也得有形状 ───────────────────
import time as _time                                              # noqa: E402
from makepolo_crawler import MakepoloCrawler                       # noqa: E402
from maimaib2b_crawler import Maimaib2bCrawler                      # noqa: E402
from cdp_base import request_error_kind as _kind, request_error_shape as _shape


def _shaper(**facts):
    """免构造拿一个带现场痕迹的取页层（构造器会连浏览器，离线用例不能碰）。"""
    o = MakepoloCrawler.__new__(MakepoloCrawler)
    o._last_goto_error = o._cur_fetch_host = o._block_reason = ""
    o._last_status = 0
    o._dead_hosts = {}
    o._dead_strikes = {}
    o._dead_logged = set()
    o._last_page_error = ""
    o._page_fail = 0
    o.driver = None
    for k, v in facts.items():
        setattr(o, k, v)
    return o


_CASES = (("导航当场失败", dict(_last_goto_error="【页面未就绪】等不到 search 的完整正文"), "页面未就绪"),
          ("主机已熔断", dict(_cur_fetch_host="cn.makepolo.com",
                              _dead_hosts={"cn.makepolo.com":
                                           (_time.time(),
                                            "【连不上·无应答】cn.makepolo.com:443｜timed out")}),
           "连不上·无应答"),
          ("4xx 被拒", dict(_last_status=403), "HTTP 403·风控/需验证"),
          ("被拦停", dict(_block_reason="站点防采/限流插页"), "被拦停"))
_shapes = []
for _lab, _facts, _want in _CASES:
    _got = _shape(_shaper(**_facts)._page_fail_shape(), cap=160)
    _shapes.append((_lab, _got, _kind(_got) == _want))
check("8a 每一格现场痕迹都能读出类别（旧写法全部写成「未分型」，本轮 12/13 页就是这一形）",
      all(w for _, _, w in _shapes), str(_shapes))
check("8b 熔断那一格不许被关键词档抢走读成【读响应超时】（熔断是连接账，抢错就不会进网络账）；"
      "句子还必须紧贴「（」，否则超出嵌套类别的 12 字左边界",
      _shapes[1][2] and "主机已熔断（【" in _shaper(_cur_fetch_host="h.com",
                                                    _dead_hosts={"h.com": (_time.time(), "【连不上·无应答】h.com:443｜x")})
      ._page_fail_shape(), str(_shapes[1]))
_s200 = _shape(_shaper(_last_status=200)._page_fail_shape(), cap=160)
check("8c 站方回了 200 但正文为空，绝不能被状态码分型器说成「请求被拒」（方向相反的谎）",
      "拒" not in _s200 and _kind(_s200) == "页面未就绪", _s200)
_s4xx = _shape(_shaper(_last_status=503)._page_fail_shape(), cap=160)
check("8d 渲染不许出现【 ·xxx】这种半截空洞（嵌套二次分型会把括号里那段当证据剥走）",
      "【 ·" not in _s4xx and "｜｜" not in _s4xx and _kind(_s4xx).startswith("HTTP 503"), _s4xx)
check("8e 现场什么都没有时，要把处方指向代码位置，而不是指向上一行日志",
      "_note_request_error" in _shaper()._page_fail_shape()
      and "紧邻上一行" not in _shaper()._page_fail_shape(), _shaper()._page_fail_shape())
_o8 = _shaper(_last_status=429)
check("8f 那七个覆写翻页循环的调用点不带参数也不会退化成推卸句（归因已下沉到被调用方）",
      _o8._note_page_fail() == 1 and _kind(_o8._last_page_error or "") == "HTTP 429·限流"
      and not any("请求层未给出原因" in ln
                  for ln in (_ROOT / "b2bcollect_build" / "cdp_base.py")
                  .read_text(encoding="utf-8").splitlines() if "_note_request_error(" in ln),
      f"记账后那格＝{_o8._last_page_error!r}")
check("8g 纯 HTTP 取页的路与借标签降级的路共用同一个取证把手（麦麦 11 页全在未分型）",
      Maimaib2bCrawler._page_fail_shape is CdpSiteCrawler._page_fail_shape
      and MakepoloCrawler._page_fail_shape is CdpSiteCrawler._page_fail_shape,
      "子类各自覆写就会长出两套尺子")

# ── ⑨ 套件自己的 sys.path 口径：借隔壁目录的模块可以，把本产品的模块换成隔壁副本不行 ──
# 同一个坑本轮踩了两次：⑦a 那格断言的是 riskbird 那份老 cleaner（假绿），
# debug/qzd_channel_offline_test.py 与 debug/gi_pace_offline_test.py 把 riskbird 插在
# b2bcollect 之前——并行层/cdp_base 里的 _cleaner、task_registry 全是隔壁产物的副本，
# 那种组合任何一个交付包里都不存在。「记得把本目录插在最前」本身就是一条会漏的纪律，
# 所以这里扫全部离线套件，而不是只修犯过的那两份。
_DELIVERY = ("b2bcollect_build", "riskbird_build", "ali1688_build")
_suite_files = []
for _d in _DELIVERY:
    _suite_files += sorted((_ROOT / _d).glob("test_*_offline.py"))
for _pat in ("*_offline_test.py", "*_offline_check.py", "test_*.py", "verify_*.py"):
    _suite_files += sorted((_ROOT / "debug").glob(_pat))
_leaks = []
for _f in _suite_files:
    if not _f.is_file():
        continue
    # 注释放一边：隔壁目录名常出现在「为何不能插在前面」那句解释里，
    # 拿它当「真把那个目录插了 0 号」就是误报（第一版就地中过这一枪）
    _lines = [_ln.split("#")[0] for _ln in _f.read_text(encoding="utf-8").splitlines()]
    # 自证也得**同一行里同时出现**目录名与 `__file__`：只扫「全文提过 __file__」太宽，
    # 几乎每份套件的 os.path.abspath(__file__) 都会命中，那等于没有判据
    _proof = [_ln for _ln in _lines if "__file__" in _ln
              and any(_d in _ln for _d in _DELIVERY)]
    for _i, _ln in enumerate(_lines):
        if "sys.path.insert" not in _ln:
            continue
        # 循环头会把目录名放在上几行：`for _p in (A, B): sys.path.insert(0, _p)`
        _ctx = " ".join(_lines[max(0, _i - 3):_i + 1])
        _hit = [_d for _d in _DELIVERY if _d in _ctx]
        if len(_hit) >= 2 and not _proof:
            _leaks.append(f"{_f.relative_to(_ROOT)}：把 {'、'.join(_hit)} 都插到 sys.path[0]"
                          f"，又没有「哪一份产品」的 `__file__` 自证")
            break
check("9a 没有一份离线套件把多个产品目录同时抢到 sys.path[0]（同名副本会掉包被测产品）",
      not _leaks, "；".join(_leaks))

print(f"test_batch3_regress_offline: {ok} 项全部通过")
