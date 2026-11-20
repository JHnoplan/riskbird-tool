# -*- coding: utf-8 -*-
"""机型适配硬证据回归（真实 tesseract，无真机依赖）：
- 分辨率归一化：720p/1080p/1440p 合成截图，OCR 命中坐标必须映射回原分辨率；
- 暗色模式自愈：浅字深底自动反相后定位精度与明亮模式一致；
- CJK 存在性：暗色模式下分带 OCR 仍能读到小字（对应「该用户不存在」场景）。
y 容差按行高口径（OCR 行框含行距，点击对象是整行/按钮）。
"""
import io
import os

import locator as L
from PIL import Image, ImageDraw, ImageFont


def _resolve_font():
    """取一份能画中文的字体：先按**脚本自身位置**推仓库根目录那份，再退系统字体。

    旧写法把某一台开发机的绝对路径抄死在源码里（开头就是那个用户的 home 目录），
    只对那一台机器成立：换台机器或换个用户，它指向的就是不存在的文件，而症状长得
    像「代码坏了」（第 25 条硬门点名的就是这一形——连解释旧写法的新注释也不能再抄一遍）。
    这一份要有中文笔形（末了那一格要 OCR 读出「添加到通讯录」），所以默认字体不算候选。
    """
    here = os.path.dirname(os.path.abspath(__file__))
    for cand in (os.path.join(here, os.pardir, "NotoSerifCJKkr-Regular.otf"),
                 os.path.join(here, "NotoSerifCJKkr-Regular.otf"),
                 "/System/Library/Fonts/PingFang.ttc",
                 "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
                 os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", "msyh.ttc"),
                 "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc"):
        if os.path.exists(cand):
            return cand
    raise SystemExit("没找到能画中文的字体（仓库根目录的 NotoSerifCJKkr-Regular.otf 或系统 CJK 字体），"
                     "这份硬证据回归需要它才能造截图，不做静默降级")


FONT = _resolve_font()
NUM = "1380013800"


def make(w, h, dark, pos, fsize, txt=NUM):
    bg, fg = ((10, 10, 10), (240, 240, 240)) if dark else ((255, 255, 255), (10, 10, 10))
    im = Image.new("RGB", (w, h), bg)
    d = ImageDraw.Draw(im)
    f = ImageFont.truetype(FONT, fsize)
    d.text(pos, txt, font=f, fill=fg)
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue(), pos


def center_of(pos, fsize, txt=NUM):
    f = ImageFont.truetype(FONT, fsize)
    l, t, r, b = f.getbbox(txt)
    return (pos[0] + (r - l) / 2, pos[1] + (b - t) / 2)


def check(tag, w, h, dark, pos, fsize, tol_x, txt=NUM):
    png, pos = make(w, h, dark, pos, fsize, txt)
    p = L.ocr_find(png, [txt])
    assert p, f"{tag}: 未命中"
    exp = center_of(pos, fsize, txt)
    dx, dy = p.x - exp[0], p.y - exp[1]
    ok = abs(dx) <= tol_x and abs(dy) <= fsize * 0.6
    print(f"{tag}: 命中({p.x},{p.y}) 期望({exp[0]:.0f},{exp[1]:.0f}) "
          f"偏差({dx:.0f},{dy:.0f}) {'PASS' if ok else 'FAIL'}")
    assert ok, tag


def main():
    check("720p  明亮", 720, 1612, False, (100, 200), 44, 15)
    check("1080p 明亮", 1080, 2400, False, (150, 300), 60, 20)
    check("1080p 暗色", 1080, 2400, True, (150, 300), 60, 20)
    check("1440p 明亮", 1440, 3200, False, (200, 400), 80, 25)
    png, _ = make(1080, 2400, True, (200, 1500), 44, "添加到通讯录")
    txt = L.ocr_text(png)
    print("1080p 暗色 ocr_text 含『添加』:", "添加" in txt)
    assert "添加" in txt
    print("ADAPTATION EVIDENCE: ALL PASS")


if __name__ == "__main__":
    main()
