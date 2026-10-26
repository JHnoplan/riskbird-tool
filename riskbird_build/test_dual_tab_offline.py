"""离线验证双标签的界面交互与分工逻辑（不启动浏览器，offscreen 跑）。

覆盖：
1. 复选框默认勾选、默认可用；
2. 切到风鸟自动置灰并取消勾选（风鸟有额度限制不参与并行）；
3. 切回企知道/爱企查恢复用户原本的勾选意愿（手动取消过就保持取消）；
4. 单双数分工稳定（续采时同一家公司仍归同一平台）；
5. 双标签运行时"暂停/继续/停止"作用于两个会话，日志前缀函数可复用（disconnect 才对得上）；
6. 界面文案与下拉顺序（风鸟排最后、标题固定、登录标识已去掉）与合并优先级不跟着展示顺序漂。
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication  # noqa: E402

app = QApplication([])

import dual_session as ds  # noqa: E402
from riskbird_gui import MainWindow, DualCrawlWorker  # noqa: E402

w = MainWindow()
IDX = {k: i for i, k in enumerate(w.SOURCE_KEYS)}     # 下拉顺序改了也不用改下面的用例

# 0. 标题与下拉顺序、登录标识
assert w.windowTitle() == "公司名查企业数据工具", w.windowTitle()
assert w.title_label.text() == "公司名查企业数据工具", w.title_label.text()
assert [w.source_combo.itemText(i) for i in range(3)] == [
    "企知道 (qizhidao.com)", "爱企查 (aiqicha.baidu.com)", "风鸟 (riskbird.com)"]
w._on_source_changed(IDX["aiqicha"])                  # 切平台不能把标题带跑
assert w.windowTitle() == "公司名查企业数据工具" and w.title_label.text() == "公司名查企业数据工具"
assert not hasattr(w, "login_status_lbl"), "登录/未登录标识已按要求删除"
assert "打开网页" in w.hint_lbl.text() and "如未登录" not in w.hint_lbl.text(), w.hint_lbl.text()
w._on_source_changed(IDX["qizhidao"])

# 1. 默认状态
assert w.dual_tab_check.isChecked(), "双标签应默认勾选"
assert w.dual_tab_check.isEnabled(), "企知道/爱企查下双标签应可用"
assert w._dual_enabled() is True
assert w._dual_pref is True

# 2. 切到风鸟：置灰 + 取消勾选
w.source_combo.setCurrentIndex(IDX["riskbird"])
app.processEvents()
assert not w.dual_tab_check.isEnabled(), "风鸟下双标签应置灰"
assert not w.dual_tab_check.isChecked(), "风鸟下双标签应自动取消"
assert w._dual_enabled() is False
assert w._dual_pref is True, "置灰不应改变用户意愿"
assert w.crawler is w.riskbird_crawler and not w.rb_detail_check.isHidden(), "风鸟开关该跟着 key 走"

# 3. 切回企知道：恢复勾选
w.source_combo.setCurrentIndex(IDX["qizhidao"])
app.processEvents()
assert w.dual_tab_check.isEnabled() and w.dual_tab_check.isChecked(), "切回后应恢复默认勾选"
assert w._dual_enabled() is True
assert w.crawler is w.qizhidao_crawler and w.rb_detail_check.isHidden()

# 用户手动取消后，切平台仍尊重其意愿
w.dual_tab_check.setChecked(False)
w.source_combo.setCurrentIndex(IDX["aiqicha"])
app.processEvents()
assert w.dual_tab_check.isEnabled(), "爱企查下应可用"
assert not w.dual_tab_check.isChecked(), "用户手动取消后不应被自动勾回"
assert w._dual_enabled() is False
assert w.crawler is w.aiqicha_crawler
w.source_combo.setCurrentIndex(IDX["qizhidao"])
app.processEvents()
assert not w.dual_tab_check.isChecked()
w.dual_tab_check.setChecked(True)

# 4. 分工
assert ds.split_parity(["A", "B", "C", "D", "E"]) == (["A", "C", "E"], ["B", "D"])
assert ds.split_parity(["A"]) == (["A"], [])
assert ds.split_parity([]) == ([], [])
# 续采时切分结果必须稳定（同一家公司永远归同一平台）
names = [f"公司{i}" for i in range(1, 12)]
first = ds.split_parity(names)
again = ds.split_parity(names)
assert first == again
assert len(first[0]) == 6 and len(first[1]) == 5
assert set(first[0]) | set(first[1]) == set(names)
assert not (set(first[0]) & set(first[1])), "两边不能有重复公司"

# 5. 运行时控制与日志前缀
wk = DualCrawlWorker(w.qizhidao_crawler, w.aiqicha_crawler, names)
assert wk.qzd is w.qizhidao_crawler and wk.aqc is w.aiqicha_crawler
assert wk.companies == names

w._dual_running = True
assert w._active_crawlers() == [w.qizhidao_crawler, w.aiqicha_crawler], "双标签时暂停/停止要管两个会话"
w._dual_running = False
assert w._active_crawlers() == [w.crawler]

assert w._log_dual_platform("企知道") is w._log_dual_platform("企知道"), "日志前缀函数需可复用"
assert w._log_dual_platform("企知道") is not w._log_dual_platform("爱企查")

# 信号改接必须幂等：重复调用不能把日志槽连两遍（否则日志翻倍）
def _log_lines():
    return w.log_text.document().blockCount()


w._attach_dual_logs(True)
w._attach_dual_logs(True)      # 重复开启：应短路
assert w._dual_logs_on is True
before = _log_lines()
w.qizhidao_crawler.signals.log_signal.emit("测试日志")
app.processEvents()
assert _log_lines() - before == 1, f"并行模式下日志应只写一行，实际 {_log_lines() - before} 行"
assert "【企知道】" in w.log_text.toPlainText(), "并行日志应带平台前缀"

w._attach_dual_logs(False)
w._attach_dual_logs(False)     # 重复关闭：应短路
assert w._dual_logs_on is False
before = _log_lines()
w.aiqicha_crawler.signals.log_signal.emit("测试日志2")
app.processEvents()
assert _log_lines() - before == 1, f"还原后日志也应只写一行，实际 {_log_lines() - before} 行"

# 6. 下拉展示顺序改了，但合并取数优先级不能跟着漂（同名公司谁的非空字段胜出）
for c in w._all_crawlers:
    c.results.clear()
w.riskbird_crawler.results.append({"公司名": "甲公司", "数据来源": "风鸟", "电话": "13800000000"})
w.qizhidao_crawler.results.append({"公司名": "甲公司", "数据来源": "企知道", "电话": "13900000000"})
w.aiqicha_crawler.results.append({"公司名": "乙公司", "数据来源": "爱企查", "电话": "13700000000"})
combined = w._all_results()
assert [r["数据来源"] for r in combined] == ["风鸟", "企知道", "爱企查"], \
    f"合并仍按 MERGE_ORDER（风鸟→企知道→爱企查）：{[r['数据来源'] for r in combined]}"
w._update_platform_counts()
text = w.platform_count_lbl.text()
assert text.index("企知道") < text.index("爱企查") < text.index("风鸟"), f"条数展示应按新顺序：{text}"
assert "合计: 3 条" in text, text
for c in w._all_crawlers:
    c.results.clear()

print("DUAL_GUI_OK")
