"""keyword_junk_offline_test.py —— 关键词解析剔除「行业报告文档碎片」离线断言（零网络）。

要钉住的两侧（都是 2026-11-04 实机采集进度.json 里现存的 92 个词量出来的）：
  A. 该剔的全剔：报告小节标题（'#### 【TOP3 核心装备'）、括号残片（'平台类】'
     '从重到轻）'）、榜单词、机构名（'中国船舶工业行业协会'）、整句描述
     （'三峡领航号 16MW 漂浮式风电平台'）——15 个碎片一个不留。
     为什么必须剔而不是只提醒：跨国采购网同一个网络地址只看约 50 个内容页就进
     90 分钟冷却（今天实测 14＋27 页＝41 页撞墙），每个碎片都要花真实请求，
     15 个碎片偷走一轮配额三成以上＝「采了一堆词、数据却停在 99」的帮凶。
  B. 不该杀的一个不杀：带配对括注的词（会被 _strip_brackets 剥成主体词）、
     中英混排词、带空格/连字符的正常词必须原样活着——误杀一个好词等于永久丢一类数据。
"""
import json
import os
import shutil
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 只插 b2bcollect_build：riskbird_build 里也有一个同名的 task_registry.py（旧版，没有
# done_keywords），插在前面会让下面导的 GUI 拿到错的那个模块（实跑踩过）。
sys.path.insert(0, os.path.join(BASE, "b2bcollect_build"))

from cdp_base import CdpSiteCrawler as C  # noqa: E402

PASS = FAIL = 0


def check(tag, got, want):
    global PASS, FAIL
    if got == want:
        PASS += 1
        print(f"  ok   {tag}")
    else:
        FAIL += 1
        print(f"  FAIL {tag}\n        实际={got!r}\n        期望={want!r}")


# 实机现存 92 词里的全部碎片（采集进度.json 逐个人工核对过）
_JUNK = [
    "#### 【TOP3 核心装备", "平台类】", "#### 【TOP4 水下无人", "载人潜器】",
    "#### 【TOP5 标志性项目", "装置名称】", "三峡领航号 16MW 漂浮式风电平台",
    "#### 【TOP7 水下配套零部件", "#### 【TOP8 属性", "参数标签词】",
    "#### 【TOP9 来源", "口径辅助词】", "中国船舶工业行业协会",
    "### 精简一维逗号版（直接复制检索", "从重到轻）",
]

print("\n=== A1 实机 15 个碎片逐个判为垃圾（一个不漏）===")
for j in _JUNK:
    check(f"剔 {j!r}", bool(C._kw_junk_reason(j)), True)

print("\n=== A2 端到端：把报告片段整段粘进来，出来的词表必须干净 ===")
_DOC = "\n".join([
    "海洋工程、深海能源、海工装备",
    "#### 【TOP3 核心装备平台类】",
    "钻井平台，自升式平台，半潜式平台",
    "#### 【TOP8 属性参数标签词】",
    "4500 米级、万米级",
    "中国船舶工业行业协会",
])
_out = C.parse_keywords(_DOC)
check("碎片一个都不进词表", all(not C._kw_junk_reason(k) for k in _out), True)
check("正常词全在", _out[:3], ["海洋工程", "深海能源", "海工装备"])
check("词表长度＝8（3＋3＋2，三个报告标题行与机构名行全部被剔）", len(_out), 8)

# 如实记账：标题行括号配对完整，会被 _strip_brackets 剥成 '####' 后落在「退化碎片」
# 分支——这一支也必须记进 junk_out，否则界面报的剔除数比实际少（用户拿到错的数）。
_j2 = []
C.parse_keywords(_DOC, junk_out=_j2)
check("junk_out 里能看到剥成符号的标题行", ("####", "剥掉括注后只剩符号") in _j2, True)
check("junk_out 里能看到机构名", any(w.startswith("机构名") for _, w in _j2), True)
check("junk_out 不重复计（两行标题都剥成 #### 只记一条）", len(_j2), 2)

print("\n=== A3 junk_out 带回剔除理由，界面才能如实说明 ===")
_j = []
C.parse_keywords("#### 【TOP3 核心装备\n制冷压缩机\n从重到轻）", junk_out=_j)
check("剔了两个", len(_j), 2)
check("第一个理由是文档标题符号", _j[0][1].startswith("文档标题符号"), True)
check("第二个理由是括号残片", _j[1][1].startswith("括号残片"), True)
check("被剔的词原文交回", [x[0] for x in _j], ["#### 【TOP3 核心装备", "从重到轻）"])
check("不传 junk_out 时行为不变（老调用方不受影响）",
      C.parse_keywords("#### 【TOP3 核心装备\n制冷压缩机"), ["制冷压缩机"])

print("\n=== B 误杀回归：这些必须是活词 ===")
_GOOD = [
    ("浮式生产储卸油装置（FPSO）", "浮式生产储卸油装置"),   # 配对括注→剥成主体词
    ("LED（线性驱动）", "LED"),
    ("张力腿平台 TLP", "张力腿平台 TLP"),                  # 中英混排带空格
    ("AUV 自主潜水器", "AUV 自主潜水器"),
    ("载人深潜器 HOV", "载人深潜器 HOV"),
    ("光伏组件", "光伏组件"),
    ("可燃冰", "可燃冰"),
    ("4500 米级", "4500 米级"),
    ("海燕 - L", "海燕 - L"),                              # 带空格与连字符的型号
    ("不锈钢方管", "不锈钢方管"),
    ("stainless steel pipe fittings", "stainless steel pipe fittings"),  # 纯英文长短语
]
for src, want in _GOOD:
    got = C.parse_keywords(src)
    check(f"不误杀 {src!r}", got, [want])

print("\n=== B2 括号残片判据必须四类括号全覆盖（上一版漏了全角右括号）===")
for frag in ("从重到轻）", "平台类】", "装置名称)", "[水下无人", "（直接复制检索"):
    check(f"残片判垃圾 {frag!r}", bool(C._kw_junk_reason(frag)), True)

print("\n=== B3 机构名判据只认结尾，不碰产品词 ===")
for org in ("中国船舶工业行业协会", "某某学会", "船舶研究院"):
    check(f"机构名 {org!r}", bool(C._kw_junk_reason(org)), True)
for notorg in ("协会专用阀门", "学会用起重机", "研究院专用泵"):
    check(f"不是机构名 {notorg!r}", C._kw_junk_reason(notorg), "")

print("\n=== C 续采回填路径也必须洗（它不经过 parse_keywords）===")
# 「继续上次任务」直接拿 采集进度.json 里的 keywords 当词表，当年那版解析器放行过的
# 碎片会原样进下一轮。这一段在临时目录里跑，不碰用户真实任务夹。
# 沙箱（2026-11-04 实机事故）：本测试曾把假任务写进用户桌面真实 _采集任务索引.json，
# 用户打开工具续采后 89 条真实数据被写进了 /var/folders。当时把 GUI 的桌面目录改指临时区
# 就收工了——可那次的教训并没有真的走完：任务索引的现行落点**早就不经过桌面目录**
# （task_registry.state_path 问的是 tool_config.state_file，开发态就是本模块目录），
# 所以 `_get_desktop_dir` 这一句管不到它，假任务从 2026-11-04 一直写到今天。批跑入口
# 现在对「代码自己声明的状态文件」做前后快照对拍（见 debug/run_offline_regression.sh
# 与本套件末尾那条自证），这一类污染一改动就当场判红、并点名是哪一份文件。
import tempfile  # noqa: E402

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_STATE_SANDBOX = tempfile.mkdtemp(prefix="kwjunk_state_")
os.environ["B2B_STATE_DIR"] = _STATE_SANDBOX      # 现行落点唯一的开关：钉这一级才真的隔离
from PyQt5.QtWidgets import QApplication  # noqa: E402
import b2bcollect_gui as gui  # noqa: E402

assert str(gui._tasks.index_path()).startswith(_STATE_SANDBOX), (
    f"隔离没生效：任务索引指向 {gui._tasks.index_path()}（不是 {_STATE_SANDBOX}）"
    f"——落点口径又搬了，本套件的假任务会写进开发机的真实索引")

_app = QApplication.instance() or QApplication([])
_sandbox = tempfile.mkdtemp(prefix="kwjunk_home_")
gui.MainWindow._get_desktop_dir = lambda self: _sandbox
_win = gui.MainWindow()
_tmp = tempfile.mkdtemp(prefix="kwjunk_", dir=str(gui._tasks.tool_dir(_sandbox)))
with open(os.path.join(_tmp, "采集进度.json"), "w", encoding="utf-8") as fh:
    json.dump({"keywords": ["海洋工程", "#### 【TOP3 核心装备", "平台类】",
                            "制冷压缩机", "中国船舶工业行业协会"],
               "sites": [], "ledger": {}}, fh, ensure_ascii=False)
_logs = []
_win._on_log = lambda *a, **k: _logs.append(str(a[0]) if a else "")
_win._restore_task({"id": "t", "folder": _tmp,
                        "keywords": ["海洋工程", "平台类】"]}, silent=True)
check("续采后的词表里没有碎片",
      all(not C._kw_junk_reason(k) for k in _win.keywords), True)
check("续采后只留下好词", _win.keywords, ["海洋工程", "制冷压缩机"])
check("剔碎片一事在日志里如实告知", any("🧹" in x and "剔掉 3 个" in x for x in _logs), True)
check("回填到关键词框的内容也是洗过的",
      _win.kw_text.toPlainText().split("\n"), ["海洋工程", "制冷压缩机"])

print("\n=== D 会话目录不变量守卫：临时目录里的假任务进不了真实链路 ===")
# 实机事故（2026-11-04）：测试在 /var/folders 造的假任务混进正式任务索引，
# 工具启动回填选中它后，真实采集数据被写进了系统随时清理的临时目录。
# 守卫在回填/登记/启动筛选三处把关：只认工具目录直接子目录的会话夹。
_stray = tempfile.mkdtemp(prefix="kwjunk_stray_")            # 系统临时目录下的假会话夹
_ok_dir = gui._tasks.tool_dir(_sandbox) / "20261104_000000"  # 合规：工具目录直接子目录
_ok_dir.mkdir(parents=True, exist_ok=True)
with open(os.path.join(_ok_dir, "采集进度.json"), "w", encoding="utf-8") as fh:
    json.dump({"keywords": ["制冷压缩机"], "sites": [], "ledger": {}}, fh, ensure_ascii=False)
check("守卫：系统临时目录下的目录被拒", _win._session_folder_ok(_stray), False)
check("守卫：工具目录下的会话夹被放行", _win._session_folder_ok(str(_ok_dir)), True)
check("守卫：空目录名被拒", _win._session_folder_ok(""), False)

_logs.clear()
_r = _win._restore_task({"id": "stray", "folder": _stray,
                         "keywords": ["海洋工程"]}, silent=True)
check("回填：临时目录里的假任务拒绝恢复", _r, False)
check("回填：拒绝时日志如实说明原因", any("不在工具目录下" in x for x in _logs), True)

_win.session_dir = _stray
_before = len(gui._tasks.load_tasks())
_win._register_task()
check("登记：不合规会话夹不写任务索引", len(gui._tasks.load_tasks()), _before)
check("登记：跳过时日志说明", any("不登记任务索引" in x for x in _logs), True)

# 启动回填筛选：就算索引已被脏条目污染（且脏条目时间更新），也只能选到合规会话夹
gui._tasks.save_tasks([
    {"id": "stray1", "status": gui._tasks.TASK_STATUS_STOPPED,
     "keywords": ["海洋工程"], "folder": _stray, "started_at": "2026-11-04 13:00:00"},
    {"id": "20261104_000000", "status": gui._tasks.TASK_STATUS_STOPPED,
     "keywords": ["制冷压缩机"], "folder": str(_ok_dir), "started_at": "2026-11-04 12:00:00"},
], _sandbox)
_win.session_dir = None
_win._first_start = None
_win._restore_last_task()
check("启动回填：跳过脏条目、选中合规会话夹", str(_win.session_dir), str(_ok_dir))
_win.close()
shutil.rmtree(_stray, ignore_errors=True)

# 收尾自证：上面那句 `save_tasks(..., _sandbox)` 传的是**已被忽略的老参数**，它正是
# 2026-11-04 那次污染的入口。隔离是否真的生效，不看注释、也不看环境变量拨没拨，只看
# 「按现行口径把开关撤掉之后去读真实索引，本套件的假 id 一个都不在里面」。
print("\n=== E 收尾自证：假任务只留在沙箱，开发机的真实索引里没有本套件的 id ===")
_saved_state_env = os.environ.pop("B2B_STATE_DIR", None)
try:
    _real_index_at = str(gui._tasks.index_path())
    _real_ids = {t.get("id") for t in gui._tasks.load_tasks()}
finally:
    if _saved_state_env is not None:
        os.environ["B2B_STATE_DIR"] = _saved_state_env
for _junk_id in ("t", "stray1", "20261104_000000"):
    check(f"真实索引（{_real_index_at}）里没有假任务 {_junk_id!r}", _junk_id in _real_ids, False)

total = PASS + FAIL
print("\n" + "=" * 62)
print(f"关键词碎片剔除离线断言：{PASS} 项通过 / {FAIL} 项失败（共 {total}）")
sys.exit(1 if FAIL else 0)
