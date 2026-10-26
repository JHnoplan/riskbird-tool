"""渲染真实 GUI 界面并生成带红框标注的分步截图，供新手指南图文使用。"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "cocoa")
from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import Qt, QPoint, QRectF
from PyQt5.QtGui import QColor, QPen, QPainter, QFont

from riskbird_gui import MainWindow, CompanyColumnDialog

OUT = "guide_images"
os.makedirs(OUT, exist_ok=True)
app = QApplication.instance() or QApplication([])

RED = QColor(230, 30, 30)


def render(win):
    """固定尺寸渲染窗口为 pixmap（device 像素）。"""
    win.show()
    app.processEvents()
    pm = win.grab()
    return pm


def rect_of(win, w):
    # QPainter 在带 DPR 的 pixmap 上使用逻辑坐标，故不乘 dpr
    p = w.mapTo(win, QPoint(0, 0))
    return QRectF(p.x() - 4, p.y() - 5, w.width() + 8, w.height() + 9)


def annotate(pm, rects):
    """rects: (QRectF, tag)。画红框 + 左上角红底白字圆标。"""
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.Antialiasing)
    font = QFont("PingFang SC", 15, QFont.Bold)
    painter.setFont(font)
    for rect, tag in rects:
        painter.setPen(QPen(RED, 5))
        painter.drawRect(rect)
        d = 40
        by = rect.top() - d / 2 if rect.top() > 40 else rect.bottom() - d - 4
        badge = QRectF(rect.left() - d / 2, by, d, d)
        painter.setPen(Qt.NoPen)
        painter.setBrush(RED)
        painter.drawEllipse(badge)
        painter.setPen(QPen(QColor(255, 255, 255)))
        painter.drawText(badge, Qt.AlignCenter, tag)
        painter.setPen(QPen(RED, 5))
    painter.end()
    return pm


# ── 主窗口 ──
win = MainWindow()
win.resize(1000, 760)
base = render(win)
base.save(f"{OUT}/overview.png")

steps = {
    "step1_source": [(win.source_combo, "1")],
    "step2_login": [(win.open_web_btn, "2")],
    "step3_file": [(win.select_file_btn, "3")],
    "step4_start": [(win.start_btn, "4"), (win.pause_btn, "a"),
                    (win.resume_btn, "b"), (win.stop_btn, "c"), (win.delay_spin, "d")],
    "step5_export": [(win.export_btn, "5")],
}
for name, targets in steps.items():
    pm = base.copy()
    rects = [(rect_of(win, w), lbl) for w, lbl in targets]
    annotate(pm, rects)
    pm.save(f"{OUT}/{name}.png")
    print(name, "->", [(int(r.x()), int(r.y()), int(r.width()), int(r.height())) for r, _ in rects])

# ── 公司名列确认对话框 ──
cands = [
    {"col": 0, "header": "序号", "sample": "1、2、3", "header_score": 0, "content_score": 0, "total": 0},
    {"col": 1, "header": "公司名称", "sample": "深圳市腾讯计算机系统有限公司", "header_score": 100, "content_score": 100, "total": 100},
    {"col": 2, "header": "联系电话", "sample": "13800000000", "header_score": 0, "content_score": 0, "total": 0},
]
dlg = CompanyColumnDialog(cands, 1, "示例公司表.xlsx")
dlg.resize(600, 320)
dpm = render(dlg)
# 高亮正确的那一行（公司名称）
painter = QPainter(dpm)
painter.setPen(QPen(RED, 5))
boxes = dlg._boxes
if 1 in boxes:
    b = boxes[1].mapTo(dlg, QPoint(0, 0))
    painter.drawRect(QRectF(b.x() - 4, b.y() - 4, boxes[1].width() + 8, boxes[1].height() + 8))
painter.end()
dpm.save(f"{OUT}/dialog_column.png")
print("done")
