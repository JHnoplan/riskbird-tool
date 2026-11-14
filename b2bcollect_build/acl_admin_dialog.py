# -*- coding: utf-8 -*-
"""acl_admin_dialog.py —— 权限管理面板（构建 pro + 账号等级 0 才能打开）。

这个面板是什么
--------------
把 ``python -m access_guard`` 那一套管理员命令搬到界面上：看名单与等级、看每个号
绑在哪台机器、加号、改等级、踢下线、移号。**它只是一层投影**：所有判定与写回都走
``AccessGuard`` 的类方法，面板自己不含任何权限规则——否则「等级 2 能干什么」这句
话就会在这里长成第二份，而两份真源的必然结局是慢慢漂移。

为什么值得单开一个模块而不是塞进 b2bcollect_gui.py
--------------------------------------------------
主窗口已经四千多行；更重要的是这个模块**只在管理员机器上被 import**（主窗口按能力
位 ``acl_admin`` 才 ``from acl_admin_dialog import ...``）。丐版包里连它的加载路径
都没有，功能自然不存在，与「隐藏入口而不删功能」的整体策略一致。

四条设计约束（都是权限后台踩过的坑）
------------------------------------
1. **网络操作一律离开 UI 线程**。名单一次 + 每个号一次设备分片读，账号多了就是几秒；
   期间锁住面板并在状态行写明「在忙什么」，绝不让用户对着冻结的窗口猜。
2. **每个动作都再问一次能力位**。面板开着的那几分钟里，另一个管理员可能已经把你
   降级；只靠「入口创建时判定过」等于把一次性的检查当成长期的授权。
3. **读不到名单就禁止写**。空快照 + 「应用改动」= 把整份等级表覆盖成空。写按钮必须
   由「名单已成功加载」这个状态驱动，而不是假设网络总是可用；同理，本面板的头部
   信息只用**已经读回来的数据**拼，绝不为了显示一行说明再发一次请求。
4. **自我锁死的路径必须显式确认**。踢本机、删本号、降级自己、把管理员删空——这四件
   事的后果操作者当场就要吃，而且没有第二个入口可以补救。判据留在后端
   （``remove_accounts`` 不带 force 直接拒绝），面板把它的原话转成二次确认框，
   不在界面里另写一遍规则，免得命令行与界面两条路的口径分叉。
"""

from __future__ import annotations

import traceback
from typing import Callable, Dict, List, Optional

from PyQt5.QtCore import QThread, pyqtSignal
from PyQt5.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QHBoxLayout, QHeaderView, QInputDialog,
    QLabel, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QTextEdit,
    QVBoxLayout,
)

try:
    import access_guard as _ag
    from access_guard import AccessGuard
    _HAS_ACL = True
except Exception:                                   # 打包漏依赖时降级，不让主窗口起不来
    _ag = None                                      # type: ignore[assignment]
    AccessGuard = None                              # type: ignore[assignment]
    _HAS_ACL = False

try:
    import edition as _ed
except Exception:
    _ed = None

# 表格列（一处编号，取值都按索引走；调列序不会让取值代码错位）
COL_PHONE = 0
COL_LEVEL = 1
COL_DEVICE = 2
COL_GEN = 3
COL_BOUND_AT = 4
COL_NOTE = 5
_HEADERS = ["手机号", "权限等级", "当前绑定设备", "代际", "绑定时间", "备注"]

# 面板能改自己的权限，改完如果不说清楚，管理员就不知道自己下一步会失去什么
_NOTE_BOUNDARY = ("提示：绑定只需要手机号（无密码、无短信验证码），所以顶号能防"
                  "「一个号随手共享给多台机器」，防不住别人刻意拿你的号去绑定；"
                  "换绑轨迹与强制解绑就是留给管理员的处置手段。")


class _AclTask(QThread):
    """把一次管理员操作放到后台跑。

    只跑一个函数、只交一个结果：面板上的动作都是「点开 → 等一下 → 刷新」这类短
    任务，用不上线程池；把 worker 收敛成一个类，「同一时刻至多一个在途操作」这条
    约束就由结构保证，而不是靠每个按钮各自记得 disable。
    """

    done = pyqtSignal(object)
    failed = pyqtSignal(str, str)

    def __init__(self, fn: Callable[[], object], label: str, parent=None):
        super().__init__(parent)
        self._fn = fn
        self.label = label

    def run(self):
        try:
            result = self._fn()
        except Exception as e:
            # 两路信息分开给：``short`` 是管理员看得懂、也据此能行动的那句话
            # （我们的 API 抛的就是中文说明），``full`` 是原始 traceback，只在状态行
            # 里留着供取证。混成一坨的话，二次确认框里会糊出一堆调用栈。
            self.failed.emit(f"{self.label}失败：{type(e).__name__}: {e}",
                             traceback.format_exc())
            return
        self.done.emit(result)


class AclAdminDialog(QDialog):
    """名单、等级、设备绑定三件事一屏管理。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("权限管理 · 账号等级与设备绑定")
        self.setMinimumSize(900, 600)
        self.resize(980, 660)
        # {_rows} 是「最近一次读回来的样子」：改等级时靠它算差异，没改动就不写名单
        # （点一下刷新就把名单 version +1 是很烦人的行为，version 要保持可解读）。
        self._rows: Dict[str, dict] = {}
        self._version = 0
        self._declared = True
        self._loaded = False              # 名单是否成功读回（决定写按钮可用性）
        self._task: Optional[_AclTask] = None
        self._busy_label = ""
        self._build_ui()
        self._run("正在读取名单…", self._task_load, self._on_loaded)

    # ── 界面 ────────────────────────────────────────────────────────────────

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(10)

        self.tip_lbl = QLabel("正在读取名单…")
        self.tip_lbl.setWordWrap(True)
        self.tip_lbl.setStyleSheet(
            "background:#f5f7fa;border:1px solid #dfe6ee;border-radius:4px;"
            "padding:8px 10px;color:#33475b;")
        lay.addWidget(self.tip_lbl)

        self.table = QTableWidget(0, len(_HEADERS))
        self.table.setHorizontalHeaderLabels(_HEADERS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        hh = self.table.horizontalHeader()
        for col, mode in ((COL_PHONE, QHeaderView.ResizeToContents),
                          (COL_LEVEL, QHeaderView.ResizeToContents),
                          (COL_DEVICE, QHeaderView.Stretch),
                          (COL_GEN, QHeaderView.ResizeToContents),
                          (COL_BOUND_AT, QHeaderView.ResizeToContents),
                          (COL_NOTE, QHeaderView.ResizeToContents)):
            hh.setSectionResizeMode(col, mode)
        self.table.itemSelectionChanged.connect(self._show_history)
        lay.addWidget(self.table, 1)

        self.hist_box = QTextEdit()
        self.hist_box.setReadOnly(True)
        self.hist_box.setPlaceholderText("选中一个号码，这里显示它的换绑轨迹"
                                         "——谁在什么时候把这个号从别的机器上顶掉过。")
        self.hist_box.setMaximumHeight(120)
        lay.addWidget(self.hist_box)

        row1 = QHBoxLayout()
        self.btn_reload = QPushButton("刷新")
        self.btn_reload.setToolTip("重新读一次名单与各号绑定的设备。")
        self.btn_add = QPushButton("添加账号")
        self.btn_add.setToolTip("把手机号加入白名单并指定权限等级。")
        self.btn_remove = QPushButton("移除账号")
        self.btn_remove.setToolTip("把选中号码移出白名单：等级记录与设备绑定一并删除，"
                                   "该号从此用不了工具。")
        self.btn_kick = QPushButton("强制解绑")
        self.btn_kick.setToolTip("删掉选中号码的设备记录：该号所有机器下次校验即掉线，"
                                 "必须重新绑定登记才能用。")
        for b in (self.btn_reload, self.btn_add, self.btn_remove, self.btn_kick):
            row1.addWidget(b)
        row1.addStretch(1)
        lay.addLayout(row1)

        row2 = QHBoxLayout()
        self.status_lbl = QLabel("")
        self.status_lbl.setWordWrap(True)
        self.status_lbl.setStyleSheet("color:#5b6b7a;")
        row2.addWidget(self.status_lbl, 1)
        self.btn_apply = QPushButton("应用等级改动")
        self.btn_apply.setToolTip("把表格里改过的等级写回名单（没有改动就不写）。")
        self.btn_close = QPushButton("关闭")
        row2.addWidget(self.btn_apply)
        row2.addWidget(self.btn_close)
        lay.addLayout(row2)

        self.btn_apply.clicked.connect(self._on_apply_levels)
        self.btn_reload.clicked.connect(self._on_reload)
        self.btn_add.clicked.connect(self._on_add)
        self.btn_remove.clicked.connect(self._on_remove)
        self.btn_kick.clicked.connect(self._on_kick)
        self.btn_close.clicked.connect(self.close)

        if not _HAS_ACL:
            self.tip_lbl.setText("权限模块未加载（缺 access_guard），本面板不可用。")
            self._set_busy(False)

    # ── 公共骨架：能力复核 / 单在途任务 / 按钮可用性 ──────────────────────────

    def _admin_gate(self) -> bool:
        """动作层再问一次能力位：面板开着期间，别的管理员可能已经把我降级了。

        返回 False 时已把该说的话说完（弹窗或静默），调用方直接 return。
        """
        if not _HAS_ACL:
            QMessageBox.warning(self, "提示", "权限模块未加载，无法管理白名单")
            return False
        if _ed is None or not _ed.acl_admin_visible():
            QMessageBox.information(self, "权限已变更",
                                    "当前账号已经没有权限管理入口（等级刚被改动），"
                                    "本次操作不再执行。\n请关闭面板并按提示重启工具。")
            self._loaded = False        # 从此只读：按钮可用性由状态驱动，不靠弹窗拦
            self._set_busy(False)
            return False
        if self._task is not None and self._task.isRunning():
            return False                # 上一个操作还在跑：状态行已写明在忙什么
        return True

    def _run(self, label: str, fn: Callable[[], object],
             on_done: Callable[[object], None],
             on_failed: Optional[Callable[[str, str], None]] = None):
        """唯一的后台任务出口：同一时刻至多一个在途操作。

        ``on_failed`` 是可选的：默认走通用的「失败即转只读」，个别操作（移除账号）
        需要把后端那句「会锁死自己，确认请加 force」接成二次确认框，就自己接管。
        """
        self._busy_label = label
        self.status_lbl.setText(f"⏳ {label}")
        self._set_busy(True)
        task = _AclTask(fn, label, parent=self)
        task.done.connect(on_done)
        task.failed.connect(on_failed or self._on_failed)
        task.finished.connect(self._on_task_finished)
        self._task = task
        task.start()

    def _on_task_finished(self):
        self._task = None
        self._set_busy(False)

    def _set_busy(self, busy: bool):
        """在途操作期间整盘锁掉：按钮、表格一起禁用。

        写按钮还额外受 ``_write_enabled()`` 管：名单没读回来时它们是灰的，用户一眼
        能看出「现在不能改」，而不是点了没反应。
        """
        can_write = (not busy) and self._write_enabled()
        for b in (self.btn_add, self.btn_remove, self.btn_kick, self.btn_apply):
            b.setEnabled(can_write)
        self.btn_reload.setEnabled(not busy)
        self.table.setEnabled(not busy)

    def _write_enabled(self) -> bool:
        """只有「名单确实读回来了」才允许写。

        读失败时表格是空的，这时点「应用改动」看着像没生效，底层却是在按一份残缺
        快照提交；权限后台绝不允许在状态未知的时候写。
        """
        return bool(_HAS_ACL and self._loaded)

    def _on_failed(self, short: str, full: str = ""):
        self._loaded = False
        self._set_busy(False)
        self.tip_lbl.setText("操作未成功，名单状态未知：改动按钮已禁用，"
                             "请点「刷新」重新确认后再操作。")
        self.status_lbl.setText(f"❌ {short}")
        # 框里只给能行动的那句话，原始 traceback 交给主界面日志：管理员看弹窗做决定，
        # 排查时再去日志里翻细节，两件事不该挤在同一块文字里互相盖掉。
        log = getattr(self.parent(), "_on_log", None)
        if callable(log) and full:
            try:
                log(f"⚠ 权限面板操作失败明细：{short}\n{full}")
            except Exception:
                pass
        QMessageBox.critical(self, "操作失败", short)

    def _on_reload(self):
        if not _HAS_ACL:
            return
        if self._task is not None and self._task.isRunning():
            return
        self._run("正在读取名单…", self._task_load, self._on_loaded)

    # ── 读取 ────────────────────────────────────────────────────────────────

    def _task_load(self) -> dict:
        """后台：读名单 + 各号设备分片（两份数据一次取齐，界面不再补请求）。"""
        doc, version = AccessGuard.load_document()
        devices = AccessGuard.list_devices(doc["accounts"])
        return {"doc": doc, "version": version, "devices": devices}

    def _on_loaded(self, res: dict):
        doc = res["doc"]
        devices = {d["phone"]: d for d in res["devices"]}
        self._version = int(res["version"])
        # 等级由后端按唯一口径算（legacy 取标准、缺项取最保守那套规则都在 access_guard
        # 里）；面板只拿结果。在这里自己解析名单里的等级字段就是开第二份真源。
        levels, self._declared = AccessGuard.describe_levels(doc=doc)
        self._rows = {}
        self.table.setRowCount(0)
        for phone in doc["accounts"]:
            lv = levels.get(phone, _ed.UNKNOWN_ACCOUNT_LEVEL)
            d = devices.get(phone, {})
            self._rows[phone] = {"level": lv, "device": d}
            r = self.table.rowCount()
            self.table.insertRow(r)
            self.table.setItem(r, COL_PHONE, QTableWidgetItem(phone))
            combo = QComboBox()
            for value, text in _ed.level_choices():
                combo.addItem(text, value)
            idx = combo.findData(lv)
            combo.setCurrentIndex(idx if idx >= 0 else 0)
            combo.setToolTip("改完要点右下角「应用等级改动」才会写回名单。")
            self.table.setCellWidget(r, COL_LEVEL, combo)
            if d.get("bound"):
                device_text = d.get("device_label") or d.get("device_id") or "（无名设备）"
                gen_text, at_text = str(d.get("generation") or ""), self._fmt_time(d.get("bound_at"))
            else:
                device_text = "（读取失败）" if d.get("error") else "（未登记设备）"
                gen_text = at_text = ""
            self.table.setItem(r, COL_DEVICE, QTableWidgetItem(device_text))
            self.table.setItem(r, COL_GEN, QTableWidgetItem(gen_text))
            self.table.setItem(r, COL_BOUND_AT, QTableWidgetItem(at_text))
            note = ""
            if d.get("bound"):
                note = "本机" if d.get("is_self") else "其他设备在用"
            if d.get("error"):
                note = (note + "｜" if note else "") + "设备记录读取失败"
            self.table.setItem(r, COL_NOTE, QTableWidgetItem(note))
        self._loaded = True
        self._set_busy(False)
        self.tip_lbl.setText(self._header_text(devices))
        self.status_lbl.setText(f"名单 version {self._version}，共 {len(self._rows)} 个账号。")
        if self.table.rowCount():
            self.table.selectRow(0)

    def _header_text(self, devices: Dict[str, dict]) -> str:
        """头部一行取证信息：全部取自已加载数据 + 本地状态，不为此再发网络请求。"""
        mine = ""
        try:
            mine = _ag.get_guard().account()
        except Exception:
            mine = ""
        parts = [f"名单 version {self._version}",
                 f"共 {len(self._rows)} 个账号",
                 f"等级字段：{'已声明' if self._declared else '未声明（旧名单，全部按标准级）'}"]
        if mine:
            d = devices.get(mine) or {}
            lv = self._rows.get(mine, {}).get("level")
            who = _ed.level_label(lv)
            if d.get("bound") and not d.get("is_self"):
                parts.append(f"⚠ 本机账号 {mine}（{who}）当前绑在 {d.get('device_label') or '其他设备'} 上，"
                             f"这台机器已不是使用设备")
            else:
                parts.append(f"本机账号 {mine}（{who}）"
                             + ("，正是当前绑定设备" if d.get("is_self") else "，尚未登记设备"))
        try:
            parts.append(f"本机设备：{_ag.device_id()} · {_ag._device_label()}")
        except Exception:
            pass
        return "｜".join(parts) + "\n\n" + _NOTE_BOUNDARY

    # ── 换绑轨迹 ─────────────────────────────────────────────────────────────

    def _show_history(self):
        row = self.table.currentRow()
        if row < 0:
            self.hist_box.clear()
            return
        phone = self._phone_at(row)
        d = self._rows.get(phone, {}).get("device") or {}
        hist = d.get("history") or []
        if not d.get("bound"):
            self.hist_box.setPlainText(
                f"{phone}：还没有设备登记记录（该号首次绑定或认领后才会出现轨迹）。")
            return
        lines = [f"{phone} 当前绑定：{d.get('device_label') or d.get('device_id')}"
                 f"　代际 {d.get('generation')}　绑定于 {self._fmt_time(d.get('bound_at'))}"]
        for h in hist[-10:][::-1]:
            who = h.get("label") or h.get("device_id") or "?"
            act = h.get("action") or ("绑定" if h.get("bound_at") else "解绑")
            when = self._fmt_time(h.get("released_at") or h.get("bound_at"))
            lines.append(f"· {act}　代际 {h.get('generation')}　{who}　{when}")
        if len(hist) > 10:
            lines.append(f"……更早还有 {len(hist) - 10} 条（分片只保留最近 10 条轨迹）")
        self.hist_box.setPlainText("\n".join(lines))

    def _phone_at(self, row: int) -> str:
        item = self.table.item(row, COL_PHONE)
        return (item.text() if item else "") or ""

    @staticmethod
    def _fmt_time(raw) -> str:
        """ISO 时间收成一行短文本；解析不了就原样显示，不假装成功。"""
        text = str(raw or "")
        return text.replace("T", " ")[:19] if text else ""

    # ── 写操作 ───────────────────────────────────────────────────────────────

    def _selected_phone(self) -> str:
        row = self.table.currentRow()
        if row < 0:
            QMessageBox.information(self, "提示", "请先在表格里选中一个号码。")
            return ""
        return self._phone_at(row)

    def _my_phone(self) -> str:
        try:
            return _ag.get_guard().account() or ""
        except Exception:
            return ""

    def _on_add(self):
        if not self._admin_gate() or not self._write_enabled():
            return
        choices = _ed.level_choices()
        texts = [t for _, t in choices]
        phone, ok = QInputDialog.getText(self, "添加账号", "要加入白名单的手机号：")
        if not ok:
            return
        phone = (phone or "").strip()
        # 默认预选的是**后端在管理员没指定等级时会用的那一档**（最保守那档），
        # 不是"顺手给个管理员"：提权永远是显式动作，看错下拉框的代价不该是多人掌权
        default_lv = _ed.UNKNOWN_ACCOUNT_LEVEL
        values = [v for v, _ in choices]
        idx = values.index(default_lv) if default_lv in values else max(0, len(values) - 1)
        lv_text, ok = QInputDialog.getItem(
            self, "选择权限等级", f"{phone} 的权限等级：", texts, idx, False)
        if not ok:
            return
        level = choices[texts.index(lv_text)][0] if lv_text in texts else None
        if level is None:
            QMessageBox.warning(self, "提示", "没能确定权限等级，操作已取消。")
            return
        self._run("正在写入名单…",
                  lambda: AccessGuard.add_accounts([f"{phone}={level}"]),
                  lambda res: self._after_write(f"已加入 {phone}（{_ed.level_label(level)}）"))

    def _on_apply_levels(self):
        if not self._admin_gate() or not self._write_enabled():
            return
        changed: List[str] = []
        for row in range(self.table.rowCount()):
            phone = self._phone_at(row)
            combo = self.table.cellWidget(row, COL_LEVEL)
            if not phone or not isinstance(combo, QComboBox):
                continue
            new_lv = combo.currentData()
            if new_lv is not None and new_lv != self._rows.get(phone, {}).get("level"):
                changed.append(f"{phone}={new_lv}")
        if not changed:
            self.status_lbl.setText("等级没有改动，未写入名单（version 不变）。")
            return
        if not self._confirm_self_downgrade(changed):
            return
        self._run("正在写回等级…",
                  lambda: AccessGuard.set_levels(changed),
                  lambda res: self._after_write("等级已更新：" + "、".join(changed)))

    def _confirm_self_downgrade(self, changed: List[str]) -> bool:
        """把自己从管理员降下来 = 当场关掉这个面板的唯一入口，必须说清楚后果。"""
        mine = self._my_phone()
        for entry in changed:
            phone, _, lv = entry.partition("=")
            # 「会不会失去管理入口」问矩阵，不写 lv == 0：以后加一档副管理员，
            # 界面里那句比较就成了错的规则
            if phone != mine or _ed.level_allows_admin(int(lv)):
                continue
            ret = QMessageBox.question(
                self, "确认降级自己",
                f"{phone} 正是本机当前登录的账号。\n"
                f"改成 {_ed.level_label(int(lv))} 后你将失去「权限管理」入口，"
                "以后要再改名单只能靠命令行或 OSS 控制台。\n\n仍要应用吗？",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            return ret == QMessageBox.Yes
        return True

    def _on_kick(self):
        phone = self._selected_phone()
        if not phone or not self._admin_gate() or not self._write_enabled():
            return
        if phone == self._my_phone():
            ret = QMessageBox.question(
                self, "确认踢掉本机",
                f"{phone} 是本机当前绑定的账号，而你现在就在这台机器上。\n"
                "解绑后本机立刻失去使用权限，且不会自动重新登记——必须再点一次"
                "「绑定云展动力账号」才会恢复。\n\n确定要踢掉自己吗？",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if ret != QMessageBox.Yes:
                return
        self._run("正在解绑…", lambda: AccessGuard.kick(phone),
                  lambda _res: self._after_write(
                      f"{phone} 已强制解绑：该号所有设备下次校验即掉线"))

    def _on_remove(self):
        phone = self._selected_phone()
        if not phone or not self._admin_gate() or not self._write_enabled():
            return
        ret = QMessageBox.question(
            self, "确认移除账号",
            f"把 {phone} 移出白名单：该号将完全失去工具使用权（这不是降级），"
            "等级记录与设备绑定一并删除；以后重新加回来还得再绑定一次设备。\n\n"
            "确定移除吗？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ret != QMessageBox.Yes:
            return
        self._submit_remove([phone], force=False)

    def _submit_remove(self, phones: List[str], force: bool):
        """先按常规提交；后端以「会锁死自己 / 会删空管理员」拒绝时，把原话转成二次确认。

        判据留在 ``AccessGuard.remove_accounts`` 里：这样命令行与界面两条路共用同一条
        规则，不会出现「界面上被拦、命令行却能干」的缝隙。
        """
        def _failed(short: str, full: str = ""):
            if force or "--force" not in short:
                self._on_failed(short, full)
                return
            # 后端拒绝的就是「你会把自己锁死」这句话，照原样转成二次确认，不再复述规则
            question = short.split(": ", 1)[-1].replace("确认无误请加 --force", "").strip()
            ret = QMessageBox.question(
                self, "需要再次确认", question + "\n\n仍要继续吗？",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if ret == QMessageBox.Yes:
                self._submit_remove(phones, force=True)
            else:
                self._loaded = False
                self._set_busy(False)
                self.status_lbl.setText("已取消，名单未改动；建议点「刷新」确认远端现状。")

        label = "正在移除账号…" if force else "正在提交移除…"
        self._run(label,
                  lambda: AccessGuard.remove_accounts(phones, force=force),
                  lambda _res: self._after_write(f"已移除 {'、'.join(phones)}"),
                  on_failed=_failed)

    def _after_write(self, msg: str):
        """写完一律重读一遍：界面显示的必须是远端的真实状态，而不是我以为的状态。"""
        self.status_lbl.setText(f"✅ {msg}")
        self._loaded = False          # 重读成功前不许再写（状态未知就不给提交入口）
        self._run("正在刷新名单…", self._task_load, self._on_loaded)

    # ── 退出 ────────────────────────────────────────────────────────────────

    def closeEvent(self, ev):
        """有在途操作时等它收尾再关：线程对象先于任务销毁会让进程直接崩掉。"""
        task = self._task
        if task is not None and task.isRunning():
            ret = QMessageBox.question(
                self, "操作进行中",
                f"「{self._busy_label}」还在跑，现在关闭可能看不到写入结果"
                "（名单是否改动要以下次刷新为准）。\n\n仍要关闭吗？",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if ret != QMessageBox.Yes:
                ev.ignore()
                return
            task.wait(5000)
        super().closeEvent(ev)
