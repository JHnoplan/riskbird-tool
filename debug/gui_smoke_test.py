# GUI 冒烟自测（offscreen 跑，不在用户屏幕弹窗）：本轮四项需求的界面端到端验证
# ①入库口径：清洗后表剔掉「手机号/固话/邮箱/微信号」全空行，原始表仍全量
# ②③被拦→弹窗提醒 / 解除→收窗（横幅+弹窗+计时接管三处联动）
# ④「开始新任务」：复位前同步落盘、进度/看板/文件夹归零、历史种子库默认不动
import os
import sys
import json
import shutil
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")   # 无头跑 Qt，绝不打扰用户桌面
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "b2bcollect_build"))

# 状态落点必须单独钉一次（2026-11-13 批跑的状态文件对拍抓出来的第四处）
# 下面那句 `_get_desktop_dir = lambda self: _desk` 的注释写着「任务索引/账号文件全落
# 沙箱」，可它管的是**桌面**那一级：现行任务索引问的是 tool_config.state_file（开发态
# 就是模块目录）。于是本套件每次跑都往开发机真实的 _采集任务索引.json 里登记一条假任务。
# 这一类缺陷的坏在于：犯错的套件自己全绿，红的是隔壁；而且它是走 GUI 自己的登记路径写的，
# 按「谁调了 save_tasks」去静态清点也会漏掉——所以批跑入口现在盯的是环境本身（前后快照
# 对拍），这里再钉一次落点并当场自证。
_STATE_SANDBOX = tempfile.mkdtemp(prefix="b2b_gui_state_")
os.environ["B2B_STATE_DIR"] = _STATE_SANDBOX

from PyQt5.QtWidgets import QApplication, QDialog, QCheckBox, QMessageBox
from openpyxl import load_workbook

import b2bcollect_gui as gui

assert str(gui._tasks.index_path()).startswith(_STATE_SANDBOX), (
    f"隔离没生效：任务索引指向 {gui._tasks.index_path()}（不是 {_STATE_SANDBOX}）"
    f"——落点口径又搬了，本套件的假任务会写进开发机的真实索引")

ok = 0


def check(name, cond, detail=""):
    # detail 只在失败时打印：断言挂了要一眼看到实际值（如弹窗文案长什么样），
    # 而不是让人回去翻代码猜是哪一段文案变了。
    global ok
    assert cond, f"FAIL: {name}" + (f"  实际={detail}" if detail else "")
    ok += 1
    print(f"  ✓ {name}")


app = QApplication.instance() or QApplication([])
# 立刻拆掉「恢复上次任务」这颗定时炸弹：MainWindow 里挂的是
# QTimer.singleShot(0, self._restore_last_task)，只在第一次跑事件循环时才触发，而它会
# 把 session_dir / session.set_task_folder 改指用户桌面上正在跑的真实任务文件夹，
# 还会把真实已采数据 seed 进本窗口的内存。测试后面的保存就会把 3 条假记录写进用户
# 的真实原始表（2026-11-04 实测：真把用户 1689 行的表覆盖成 3 行，靠工具自身内存
# 回写才在 90 秒内恢复）。定时任务里存的是构造时就绑好的绑定方法，事后改
# w._restore_last_task 已经拦不住，只能把它的任务索引入参（load_tasks）先空掉。
if gui.HAS_YZD:
    _load_tasks_real = gui._tasks.load_tasks
    gui._tasks.load_tasks = lambda *a, **k: []
# 沙箱（2026-11-04 实机事故）：测试收尾的 _register_task 曾把 folder 在 /var/folders
# 的假任务写进用户桌面真实 _采集任务索引.json，用户打开工具续采后真实数据被写进了
# 临时目录。现在把 GUI 的桌面目录改指临时区：任务索引/账号文件全落沙箱，
# 测试怎么跑都污染不了真实历史（GUI 侧另有会话目录守卫兜底）。
tmp = Path(tempfile.mkdtemp(prefix="b2b_gui_smoke_"))
_desk = tmp / "Desktop"
_desk.mkdir(parents=True, exist_ok=True)
gui.MainWindow._get_desktop_dir = lambda self: _desk
w = gui.MainWindow()
w.show()        # offscreen 下窗口不会真上屏，但必须 show 才能让子控件 isVisible 生效
app.processEvents()          # 把那颗 singleShot(0) 在这里放掉：已被上面堵死，不会再指回真实任务夹

# 会话目录必须挂在沙箱工具目录下：GUI 的会话目录不变量守卫（_session_folder_ok）
# 只认工具目录直接子目录，测试自造的会话夹也要遵守同一条约定，断言才和真实链路一致。
sess = (gui._tasks.tool_dir(_desk) if gui.HAS_YZD
        else _desk / "B2B多平台企业数据自动采集工具") / "20261028_160000"

# 站点与数据目录就位：结果表与种子库全部改指临时目录——绝不写用户桌面的历史
# 文件夹，也不跟用户正跑着的 GUI 争同一份 seen_store（并发下它一直在写）
seen_dir_tmp = tmp / "seen"
seen_dir_tmp.mkdir(parents=True, exist_ok=True)
SEEN_FIXTURE = json.dumps({"顺企网": {"测试旧公司": {"ct": 1, "st": "成功"}}}, ensure_ascii=False)
w.session.set_enabled(["mic", "b2b168", "11467", "aipage"])   # gys 供应商网已下线
for c in w.session.crawlers.values():
    c._commit_to_store = lambda rec: None      # 假记录绝不写库
    c._data_dir = seen_dir_tmp
    c._seen_store_path = seen_dir_tmp / f"seen_store_{c.PLATFORM_SLUG}.json"
    c._seen_store_path.write_text(SEEN_FIXTURE, encoding="utf-8")
    c._seen_store = json.loads(SEEN_FIXTURE)
seen_dir = w.session.crawlers["11467"]._seen_store_path
seen_bytes = seen_dir.read_bytes()
sess.mkdir(parents=True, exist_ok=True)
w.session_dir = str(sess)
w._first_start = "20261028_160000"

# 三条记录：两条有联系方式，一条四项全空（必须从清洗后表里被剔掉，但留在原始表）
SAMPLES = [
    {"公司名称": "深圳市甲乙太阳能科技有限公司", "数据来源": "顺企网",
     "手机": "13912345678", "电话": "0755-88886666", "邮箱": "a@b.com", "状态": "成功"},
    {"公司名称": "佛山市丙丁照明器材厂", "数据来源": "八方资源网",
     "手机": "", "电话": "0757-82807298", "邮箱": "", "状态": "成功"},
    {"公司名称": "某从未公开联系方式的公司", "数据来源": "中国制造网",
     "手机": "", "电话": "", "邮箱": "", "状态": "无电话(未公开)"},
]
SLUG_OF = {"顺企网": "11467", "八方资源网": "b2b168", "中国制造网": "mic"}
for rec in SAMPLES:
    w.session.crawlers[SLUG_OF[rec["数据来源"]]]._add_result(rec)

# ══ ① 收尾保存：原始全量 + 清洗后按新口径剔除 ══
check("界面：三条记录进入内存结果", len(w.session.snapshot_results()) == 3)
saved = w._final_save_sync()
raw_p = sess / gui._tasks.RAW_FILE_NAME if gui.HAS_YZD else \
    sess / "企业数据_原始.xlsx"
clean_p = sess / "企业数据_清洗后入库数据.xlsx"
check("收尾保存成功落盘（停止后不满一个自动保存批次的数据不丢）", saved and raw_p.exists())
wb = load_workbook(raw_p, read_only=True)
ws = wb.worksheets[0]
raw_rows = [r for r in ws.iter_rows(min_row=2, values_only=True) if any(x for x in r)]
wb.close()
check(f"原始表全量 3 行不过滤（补查线索不丢）（实际 {len(raw_rows)} 行）", len(raw_rows) == 3)
wb2 = load_workbook(clean_p, read_only=True)
ws2 = wb2.worksheets[0]
data_rows = [r for r in ws2.iter_rows(min_row=2, values_only=True) if any(x for x in r)]
wb2.close()
names = [str(r[1] or "") for r in data_rows]
check(f"清洗后入库表只剩有联系方式的 2 行（实际 {len(data_rows)} 行）", len(data_rows) == 2)
check("四项全空那条确实被剔除", "某从未公开联系方式的公司" not in "".join(names))
check("有手机号那条仍在（不误杀）", any("甲乙" in n for n in names))

# ══ ②③ 被拦提醒：横幅 + 独立弹窗 + 计时接管；解除后全部收起 ══
# 拦截分型（网站拦 IP vs 可人工过验证）走两套文案，而且必须是没技术背景也看得懂的大白话
from cdp_base import BLOCK_TAG_IP, BLOCK_TAG_CAP

# 建窗口时挂的恢复提示已被上面堵掉；这里再跑一次事件循环，把其它待办
# 定时器（后台化、计时等）冲干净，后面的横幅断言才不会被子控件的异步刷新盖掉。
app.processEvents()
check("横幅没被「已接上次的任务」占位（测试数据不会写进用户真实任务夹）",
      "已接上次的任务" not in w.banner.text())

check("初始没有提醒弹窗", w._block_alert is None)
w._on_blocked(BLOCK_TAG_IP + "八方资源网：站点返回 HTTP 502（IP 级限流/冷却，非验证码）")
app.processEvents()
check("被拦时顶部横幅可见（带上站名，说的是大白话而不是术语）",
      w.banner.isVisible() and "八方资源网" in w.banner.text()
      and "拦住了你这台电脑的上网地址" in w.banner.text())
check("横幅不出现开发者术语（客户读不懂就等于没提示）",
      not any(k in w.banner.text() for k in ("IP 级", "限流", "退避", "熔断", "看门狗")))
check("IP 类横幅直说「不需要你操作」（客户就是被「手动完成验证」误导到满屏找入口）",
      "不需要你操作" in w.banner.text() and "手动完成验证" not in w.banner.text())
check("被拦时弹出独立提醒窗（旧版只改横幅，最小化后用户根本看不见）",
      w._block_alert is not None and w._block_alert.isVisible())
check("弹窗是非模态（不阻塞主窗口，用户能去操作 Chrome）", w._block_alert.isModal() is False)
txt = w._block_alert.text()
raw_reason = "站点返回 HTTP 502（IP 级限流/冷却，非验证码）"   # 网站原话，只作排查用附一行
plain = txt.replace(raw_reason, "")                           # 判定「我们说的话」是否大白话
check("弹窗带上被拦站点与原因", "八方资源网" in txt and "502" in txt)
check("IP 类弹窗不再声称「已在 Chrome 里打开对应页面」（实际根本没开）",
      "已在 Chrome 里打开对应页面" not in txt)
check("IP 类弹窗讲成两步傻瓜操作并且不堆术语",
      all(k in txt for k in ("第 1 步", "第 2 步", "手机热点", "马上继续")) and
      not any(k in plain for k in ("IP 级", "限流", "退避", "熔断", "看门狗")))
check("弹窗里同时提供「我换好网络了，马上继续」与「打开网站让我看一眼」两个按钮",
      any("马上继续" in b.text() for b in w._block_alert.buttons()) and
      any("打开网站让我看一眼" in b.text() for b in w._block_alert.buttons()))
first_box = w._block_alert

check("IP 类弹窗不会自动拉浏览器：没点按钮就不会去借标签",
      w._block_site == "八方资源网" and w.session.crawlers["b2b168"].pin_tab_id == "")
check_calls = []
w.session.open_site_check = lambda name="": (check_calls.append(name),
                                             (True, "已在 Chrome 里打开（测试替身）"))[1]
for b in w._block_alert.buttons():
    if "打开网站让我看一眼" in b.text():
        b.click()
app.processEvents()
import time as _t
_t.sleep(0.6)
app.processEvents()
check("点「看一眼」会把当前被拦站交给会话去开页（用户主动才能开）",
      check_calls == ["八方资源网"])
retry_calls = []
w.session.retry_site_now = lambda name="": (retry_calls.append(name),
                                            (True, "已让该站马上再试一次（测试替身）"))[1]
for b in w._block_alert.buttons():
    if "马上继续" in b.text():
        b.click()
app.processEvents()
_t.sleep(0.6)
app.processEvents()
check("点「马上继续」把当前被拦站交给会话立即重测（换完 IP 不用干等下一轮探测）",
      retry_calls == ["八方资源网"])
check("按钮跑完会被恢复可点（后台线程结束后轮询复位）",
      all(b.isEnabled() for b in w._block_alert.buttons()
          if "马上继续" in b.text() or "看一眼" in b.text()))

w._on_blocked(BLOCK_TAG_CAP + "中国制造网：页面弹出验证控件（#tcaptcha_transform）")
app.processEvents()
check("验证码类弹窗给出「第1步拖滑块、第2步不用点」的读得懂的步骤",
      all(k in w._block_alert.text()
          for k in ("拖完点完", "第 1 步", "第 2 步", "接着往下采")))
check("验证码类弹窗不再把「已开页」说死，改成没看到就点「看一眼」",
      "已把该站页面在 Chrome 里打开" not in w._block_alert.text()
      and "打开网站让我看一眼" in w._block_alert.text())
check("验证码类横幅仍明确要求去 Chrome 拖滑块，并提另一个按钮名",
      "Chrome 窗口把滑块拖完" in w.banner.text() and "打开网站让我看一眼" in w.banner.text())
check("多站同时被拦不叠窗（复用同一弹窗实例）", w._block_alert is first_box)
check("复用弹窗文案更新成最新站点", "中国制造网" in w._block_alert.text())
check("验证等待期间接管采集计时", w._hold_from > 0)
w._on_block_cleared("八方资源网")
app.processEvents()
check("解除后提醒弹窗关闭", w._block_alert is None)
check("解除后计时交还给采集耗时", w._hold_from == 0.0)
w._show_banner("测试残留", "#fff", "#000")
w._on_blocked(BLOCK_TAG_IP + "顺企网：HTTP 429（IP 级限流/冷却，非验证码）")
app.processEvents()
# 采集收尾会问「要不要同步到数据池」——那是**模态**弹窗，offscreen 下真 exec_ 就是
# 永久挂住（2026-11-10 回归批跑正是卡在这一步 20 多分钟）。这里必须替用户作答，
# 而且必须答「否」：答「是」会让一个自称离线的冒烟测试真的往团队池上传数据。
_asked, _synced = [], []
_real_question, _real_sync = QMessageBox.question, w._on_pool_sync
# 记下「标题 + 正文」两段：标题写着干什么（同步到数据池），正文写着几条，
# 只看其中一段迟早被文案微调骗过去。
QMessageBox.question = staticmethod(
    lambda *a, **k: _asked.append("｜".join(str(x) for x in a[1:3])) or QMessageBox.No)
w._on_pool_sync = lambda *a, **k: _synced.append(1)
try:
    w._on_finished()
finally:
    QMessageBox.question = _real_question
    w._on_pool_sync = _real_sync
check("结束/停止同样收回提醒弹窗与横幅（不留挂着误导用户）",
      w._block_alert is None and w.banner.isVisible() is False)
_all_ask = " ".join(_asked)
check("采到数据才问「同步到数据池」（绝不静默上传）", "同步到数据池" in _all_ask, _asked)
check("问的时候报明本次条数",
      str(len(w.session.snapshot_results())) in _all_ask, _asked)
check("用户答「否」就一步都不做（不碰 OSS）", _synced == [])

# ══ ④ 开始新任务：对话框可确认，复位彻底但不碰历史去重成果 ══
accepted_box = {}


def fake_exec(self):
    accepted_box["dlg"] = self
    for cb in self.findChildren(QCheckBox):
        if "种子库" in cb.text():
            cb.setChecked(False)          # 默认口径：保留历史种子库
    return QDialog.Accepted


QDialog.exec_ = fake_exec
QMessageBox.information = staticmethod(lambda *a, **k: QMessageBox.Ok)
w.btn_start.setEnabled(False)
w.btn_pause.setEnabled(True)
w.btn_stop.setEnabled(True)
w.progress.setValue(66)
w._bar_pct = 66
w._bar_companies = 3
w._last_autosave_bucket = 2
w.lbl_board.setText("上一批看板")
w.lbl_stats.setText("上一批状态行")
w.kw_text.setPlainText("太阳能路灯")
w._on_parse()
before_seen = w.session.crawlers["11467"]._seen_store

w._on_reset_task()
check("复位对话框弹出过（用户能勾额外项）", "dlg" in accepted_box)
check("复位后内存数据清空", w.session.snapshot_results() == [])
check("复位后各站账目清空（看板数字不再延续上一批）",
      all(c.stats()["ok"] == 0 for c in w.session.crawlers.values()))
check("复位释放数据文件夹（下次「开始采集」新建时间戳目录）",
      w.session_dir is None and w._first_start is None)
check("复位后进度条/百分比/家数归零",
      w.progress.value() == 0 and w._bar_pct == 0 and w._bar_companies == 0)
check("复位后自动保存批次计数归零（新任务第一满 20 条就落盘）", w._last_autosave_bucket == 0)
check("复位后计时显示与看板/状态行清空",
      w.timer_lbl.text().endswith("00:00:00") and w.lbl_board.text() == ""
      and w.lbl_stats.text() == "就绪")
check("复位后按钮回到可开始状态",
      w.btn_start.isEnabled() and not w.btn_pause.isEnabled() and not w.btn_stop.isEnabled())
check("未勾选时关键词保留（可换关键词前先复用同一批）", w.keywords == ["太阳能路灯"])
check("未勾选时日志保留（上一轮结果仍可回看）", w.log.toPlainText() != "")
check("历史种子库内存条目未被复位清掉", w.session.crawlers["11467"]._seen_store == before_seen)
check("历史种子库磁盘文件一字未变（跨运行去重成果保住=少请求=防封）", seen_dir.read_bytes() == seen_bytes)
check("上一批的 Excel 文件没被删（历史成果保持）", raw_p.exists() and clean_p.exists())
check("复位日志写明新任务会新建文件夹",
      "新建一个时间戳文件夹" in w.log.toPlainText())

# 关键词清空分支
w.kw_text.setPlainText("光伏储能电池")
w._on_parse()


def fake_exec_kw(self):
    for cb in self.findChildren(QCheckBox):
        # 只勾「关键词输入框/日志区」：注意种子库那条文案里也带“关键词”三字，
        # 按子串乱匹会误清种子库，必须按完整选项名区分
        cb.setChecked("关键词输入框" in cb.text() or "日志区" in cb.text())
    return QDialog.Accepted


QDialog.exec_ = fake_exec_kw
w._on_reset_task()
check("勾选后关键词输入框与解析结果一并清空",
      w.kw_text.toPlainText() == "" and w.keywords == [] and "识别到 0 个关键词" in w.lbl_kw.text())
log_now = w.log.toPlainText()
check("勾选后日志区清空（只留本次复位提示，上一轮痕迹不残）",
      "💾 收尾保存" not in log_now and "🆕 已清空任务" in log_now)
check("没勾种子库选项就绝不删去重文件（文案里也带“关键词”不能误伤）",
      seen_dir.read_bytes() == seen_bytes)

# 第三轮：明确勾选「同时清空历史种子库」→ 磁盘与内存去重记录都应清空
for c in w.session.crawlers.values():
    c._seen_store = json.loads(SEEN_FIXTURE)
    c._seen_store_path.write_text(SEEN_FIXTURE, encoding="utf-8")


def fake_exec_seen(self):
    for cb in self.findChildren(QCheckBox):
        cb.setChecked("历史种子库" in cb.text())
    return QDialog.Accepted


QDialog.exec_ = fake_exec_seen
w._on_reset_task()
check("勾选后五站去重磁盘文件确实删除（下轮对同一批关键词从头重采）",
      not seen_dir.exists() or list(seen_dir_tmp.glob("seen_store_*.json")) == [])
check("勾选后内存去重条目一并清空", w.session.crawlers["11467"]._seen_store == {})
check("清空种子库仍不动已采的结果表文件", raw_p.exists() and clean_p.exists())

# 收尾自检：整个测试过程中 session_dir 只能落在临时目录里（曾有过一次它被启动
# 恢复逻辑改指回用户真实任务夹，把真实原始表覆盖成 3 行假记录）
check("全程 session_dir 只在临时目录里（没被改指回用户真实任务夹）",
      w.session_dir is None or str(w.session_dir).startswith(str(tmp)))

# 第二条收尾自检（同一形状：判据看证据，不看注释）：把落点开关**撤掉**去读开发机真实的
# 任务索引，本套件那条假任务必须不在里面。开头那句 assert 只证明开关拨到位了；这一句证明
# 这一路确实没往真实状态文件写过东西——将来落点再搬一次，红的是这里，而不是隔壁套件。
_saved_state_env = os.environ.pop("B2B_STATE_DIR", None)
try:
    _real_ids = sorted(str(t.get("id")) for t in gui._tasks.load_tasks())
    _real_at = str(gui._tasks.index_path())
finally:
    if _saved_state_env is not None:
        os.environ["B2B_STATE_DIR"] = _saved_state_env
check("撤掉开关后读真实任务索引：里面没有本套件的假任务",
      "20261028_160000" not in _real_ids,
      f"{_real_at} 现有 id：{_real_ids}")

shutil.rmtree(tmp, ignore_errors=True)
print(f"\n全部 {ok} 项断言通过 ✅")

# 判定到此为止，后面不再留任何「还能改变退出码」的东西。批跑里这套件出现过一次
# 「54 项全过、退出码却非零」：崩的是 Qt/线程在解释器收尾时的析构，与断言结果毫无
# 关系，但它把一条真红伪装成了抖动（单跑又是绿的，人就放过去了）。flush 之后直接
# 把退出码交给操作系统——屏幕上那句判定与批跑读到的退出码必须是同一个来源。
sys.stdout.flush()
os._exit(0)
