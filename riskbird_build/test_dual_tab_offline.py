"""离线验证双标签的界面交互与分工逻辑（不启动浏览器，offscreen 跑）。

覆盖：
1. 复选框默认勾选、默认可用；
2. 切到风鸟自动置灰并取消勾选（风鸟有额度限制不参与并行）；
3. 切回企知道/爱企查恢复用户原本的勾选意愿（手动取消过就保持取消）；
4. 单双数分工稳定（续采时同一家公司仍归同一平台）；
5. 双标签运行时"暂停/继续/停止"作用于两个会话，日志前缀函数可复用（disconnect 才对得上）。
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication  # noqa: E402

app = QApplication([])

import dual_session as ds  # noqa: E402
from riskbird_gui import MainWindow, DualCrawlWorker  # noqa: E402

w = MainWindow()

# 1. 默认状态
assert w.dual_tab_check.isChecked(), "双标签应默认勾选"
assert w.dual_tab_check.isEnabled(), "企知道/爱企查下双标签应可用"
assert w._dual_enabled() is True
assert w._dual_pref is True

# 2. 切到风鸟：置灰 + 取消勾选
w.source_combo.setCurrentIndex(0)
app.processEvents()
assert not w.dual_tab_check.isEnabled(), "风鸟下双标签应置灰"
assert not w.dual_tab_check.isChecked(), "风鸟下双标签应自动取消"
assert w._dual_enabled() is False
assert w._dual_pref is True, "置灰不应改变用户意愿"

# 3. 切回企知道：恢复勾选
w.source_combo.setCurrentIndex(1)
app.processEvents()
assert w.dual_tab_check.isEnabled() and w.dual_tab_check.isChecked(), "切回后应恢复默认勾选"
assert w._dual_enabled() is True

# 用户手动取消后，切平台仍尊重其意愿
w.dual_tab_check.setChecked(False)
w.source_combo.setCurrentIndex(2)
app.processEvents()
assert w.dual_tab_check.isEnabled(), "爱企查下应可用"
assert not w.dual_tab_check.isChecked(), "用户手动取消后不应被自动勾回"
assert w._dual_enabled() is False
w.source_combo.setCurrentIndex(1)
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

print("DUAL_GUI_OK")
