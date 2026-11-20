# -*- coding: utf-8 -*-
"""
控件定位层（uiautomator XML 主 + 图色兜底）
=============================================

适配任意机型/屏幕的关键：坐标一律来自控件 bounds 或颜色块质心，绝不写死像素。

主通道：解析 `uiautomator dump` 的 XML，按 text / content-desc / resource-id
        匹配节点，返回其 bounds 中心点。微信的「添加到通讯录」「发送」「搜索」
        等均为真实文本节点，命中稳定。
兜底通道：图色（HSV 阈值）定位绿色「搜索:xxx」结果卡片与绿色「发送」按钮，
        用于 uiautomator 偶发拿不到文本的场景。
"""

from __future__ import annotations

import io
import os
import re
import shutil
from dataclasses import dataclass
from typing import List, Optional, Tuple
from xml.etree import ElementTree as ET

# ---------------------------------------------------------------------------
# OCR（tesseract）配置：部分机型（如华为/荣耀）微信页面对 uiautomator 完全隐身，
# 只返回空控件树，此时必须靠截图 OCR 定位。这里自动探测 tesseract 可执行路径。
_TESS_CANDIDATES = (
    "/usr/local/bin/tesseract",      # macOS Intel brew
    "/opt/homebrew/bin/tesseract",   # macOS Apple Silicon brew
    "tesseract",                      # PATH 中
)


def set_tesseract(cmd: str):
    """显式指定 tesseract 可执行文件路径（GUI 可在启动时注入）。"""
    global _TESS_OVERRIDE
    _TESS_OVERRIDE = cmd


_TESS_OVERRIDE: Optional[str] = None


def _resolve_tesseract() -> Optional[str]:
    if _TESS_OVERRIDE:
        return _TESS_OVERRIDE
    found = shutil.which("tesseract")
    if found:
        return found
    for c in _TESS_CANDIDATES:
        if os.path.sep in c and os.path.exists(c):
            return c
    return shutil.which("tesseract")


def ocr_available() -> bool:
    """OCR 是否可用（装了 pytesseract 且能找到 tesseract 二进制）。"""
    try:
        import pytesseract  # noqa
    except Exception:  # noqa
        return False
    return _resolve_tesseract() is not None


# ---------------------------------------------------------------------------
@dataclass
class Point:
    """与 Node 接口兼容的轻量命中结果（OCR 用）：只需 .center。"""
    x: int
    y: int
    text: str = ""
    bounds: Tuple[int, int, int, int] = (0, 0, 0, 0)
    clickable: bool = True

    @property
    def center(self) -> Tuple[int, int]:
        return (self.x, self.y)


@dataclass
class Node:
    """uiautomator 控件节点。"""
    text: str
    content_desc: str
    resource_id: str
    cls: str
    bounds: Tuple[int, int, int, int]  # left, top, right, bottom
    clickable: bool

    @property
    def center(self) -> Tuple[int, int]:
        l, t, r, b = self.bounds
        return ((l + r) // 2, (t + b) // 2)


_BOUNDS_RE = re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")


def parse_hierarchy(xml: str) -> List[Node]:
    """把 uiautomator dump 的 XML 解析成扁平 Node 列表。"""
    nodes: List[Node] = []
    if not xml or "<hierarchy" not in xml:
        return nodes
    # 截取合法 XML 段（dump 输出可能带尾部噪声）
    start = xml.find("<?xml")
    if start < 0:
        start = xml.find("<hierarchy")
    xml = xml[start:]
    # 安全加固：uiautomator dump 不应含 DTD/外部实体，出现即拒解（防实体展开/XXE）
    if "<!DOCTYPE" in xml or "<!ENTITY" in xml:
        return nodes
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        # 退一步：用正则抓 node 属性
        return _parse_by_regex(xml)
    for el in root.iter("node"):
        b = el.get("bounds", "")
        m = _BOUNDS_RE.search(b)
        if not m:
            continue
        l, t, r, bt = map(int, m.groups())
        nodes.append(Node(
            text=el.get("text", "") or "",
            content_desc=el.get("content-desc", "") or "",
            resource_id=el.get("resource-id", "") or "",
            cls=el.get("class", "") or "",
            bounds=(l, t, r, bt),
            clickable=(el.get("clickable", "false") == "true"),
        ))
    return nodes


_NODE_RE = re.compile(r"<node\b([^>]*)>")
_ATTR_RE = re.compile(r'(\w[\w-]*)="([^"]*)"')


def _parse_by_regex(xml: str) -> List[Node]:
    nodes: List[Node] = []
    for seg in _NODE_RE.finditer(xml):
        attrs = dict(_ATTR_RE.findall(seg.group(1)))
        m = _BOUNDS_RE.search(attrs.get("bounds", ""))
        if not m:
            continue
        l, t, r, bt = map(int, m.groups())
        nodes.append(Node(
            text=attrs.get("text", ""),
            content_desc=attrs.get("content-desc", ""),
            resource_id=attrs.get("resource-id", ""),
            cls=attrs.get("class", ""),
            bounds=(l, t, r, bt),
            clickable=(attrs.get("clickable", "false") == "true"),
        ))
    return nodes


# ---------------------------------------------------------------------------
# 匹配辅助
# ---------------------------------------------------------------------------
def find_by_text(nodes: List[Node], keyword: str, contains: bool = True) -> Optional[Node]:
    """按 text 或 content-desc 命中关键字，返回第一个可点击优先的节点。"""
    kws = [keyword] if isinstance(keyword, str) else list(keyword)

    def hit(val: str) -> bool:
        v = (val or "").strip()
        return any((k in v) if contains else (v == k) for k in kws)

    matches = [n for n in nodes if hit(n.text) or hit(n.content_desc)]
    if not matches:
        return None
    # 优先可点击、面积适中的
    matches.sort(key=lambda n: (not n.clickable, -(n.bounds[2] - n.bounds[0]) * (n.bounds[3] - n.bounds[1])))
    return matches[0]


def has_any_text(nodes: List[Node], keywords) -> bool:
    return any(find_by_text(nodes, k) is not None for k in keywords)


# ---------------------------------------------------------------------------
# 图色兜底（绿色卡片 / 绿色按钮）
# ---------------------------------------------------------------------------
def _png_to_array(png_bytes: bytes):
    import numpy as np
    from PIL import Image
    img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    return np.asarray(img)


def find_green_button(png_bytes: bytes,
                      hue_lo=70, hue_hi=160,
                      sat_min=90, val_min=90,
                      min_area=800) -> Optional[Tuple[int, int]]:
    """在截图中定位绿色色块（微信绿 #07C160 一类），返回质心 (x, y)。

    健壮性：screencap 偶发返回空/坏字节时，解码失败一律安全返回 None，
    绝不抛出异常弄挂整条采集流程。
    """
    if not png_bytes:
        return None
    try:
        import numpy as np
        arr = _png_to_array(png_bytes)
    except Exception:
        return None
    if arr is None or arr.size == 0:
        return None
    r = arr[..., 0].astype("uint16")
    g = arr[..., 1].astype("uint16")
    b = arr[..., 2].astype("uint16")
    mx = np.maximum(np.maximum(r, g), b)
    mn = np.minimum(np.minimum(r, g), b)
    # 绿色主导：g 明显大于 r 和 b，且亮度足够、饱和度足够
    green_mask = (g > r + 20) & (g > b + 10) & (g > 120) & ((mx - mn) > 60)
    ys, xs = np.nonzero(green_mask)
    if len(xs) < min_area:
        return None
    # 取最大连通块近似：按 y 分带找最密集的一簇（避免把状态栏绿图标算进来）
    # 简化：过滤掉顶部 12% 区域（状态栏）
    h = arr.shape[0]
    keep = ys > int(h * 0.12)
    if keep.sum() < min_area:
        return None
    xs, ys = xs[keep], ys[keep]
    return (int(np.median(xs)), int(np.median(ys)))


def find_blue_button(png_bytes: bytes, min_area: int = 800) -> Optional[Tuple[int, int]]:
    """在截图中定位蓝色色块（钉钉品牌蓝 #0089FF 一类），返回质心 (x, y)。

    与绿色版同构；钉钉结果卡/默认头像/按钮多为蓝，图色探针可直接命中卡片行。
    空字节/坏图一律安全返回 None，不抛异常。
    """
    if not png_bytes:
        return None
    try:
        import numpy as np
        arr = _png_to_array(png_bytes)
    except Exception:
        return None
    if arr is None or arr.size == 0:
        return None
    r = arr[..., 0].astype("uint16")
    g = arr[..., 1].astype("uint16")
    b = arr[..., 2].astype("uint16")
    mx = np.maximum(np.maximum(r, g), b)
    mn = np.minimum(np.minimum(r, g), b)
    # 蓝色主导：b 明显大于 r 和 g，且亮度、饱和度足够
    blue_mask = (b > r + 20) & (b > g + 10) & (b > 120) & ((mx - mn) > 60)
    ys, xs = np.nonzero(blue_mask)
    if len(xs) < min_area:
        return None
    h = arr.shape[0]
    keep = ys > int(h * 0.12)
    if keep.sum() < min_area:
        return None
    xs, ys = xs[keep], ys[keep]
    return (int(np.median(xs)), int(np.median(ys)))


# ---------------------------------------------------------------------------
# OCR 文字定位（主用于 uiautomator 拿不到控件树的机型）
# ---------------------------------------------------------------------------
def image_size(png_bytes: bytes) -> Optional[Tuple[int, int]]:
    if not png_bytes:
        return None
    try:
        from PIL import Image
        return Image.open(io.BytesIO(png_bytes)).size
    except Exception:  # noqa
        return None


def _load_rgb(png_bytes: bytes):
    from PIL import Image
    return Image.open(io.BytesIO(png_bytes)).convert("RGB")


# OCR 归一化宽度：任意分辨率截图先缩放到该宽度再识别，跨机型速度/精度一致；
# 坐标按 ratio 映射回原分辨率，不引入偏差。
CANON_W = 720


def _normalize(im):
    """分辨率归一化 + 暗色模式自愈，返回 (处理后的图, ratio=原宽/归一宽)。"""
    W, H = im.size
    ratio = W / float(CANON_W)
    if abs(ratio - 1.0) > 0.01:
        im = im.resize((CANON_W, max(1, int(H / ratio))))
    # 暗色模式：平均亮度低则反相（tesseract 假定深底浅字时识别率骤降）
    try:
        from PIL import ImageOps
        gray = im.convert("L")
        hist = gray.histogram()
        total = sum(hist) or 1
        mean = sum(i * c for i, c in enumerate(hist)) / total
        if mean < 110:
            im = ImageOps.invert(im)
    except Exception:  # noqa
        pass
    return im, ratio


def _tess():
    import pytesseract
    cmd = _resolve_tesseract()
    if cmd:
        pytesseract.pytesseract.tesseract_cmd = cmd
    return pytesseract


def _bands(im, bands: int = 5, skip_top: float = 0.06):
    """把整屏切成若干横向带（跳过状态栏），返回 (crop, oy) 列表。

    分带 + 放大可专治小灰字（如「该用户不存在」）在全屏版面分析中被丢弃的问题。
    """
    W, H = im.size
    y0 = int(H * skip_top)
    step = max(1, (H - y0) // bands)
    out = []
    for i in range(bands):
        t = y0 + i * step
        b = y0 + (i + 1) * step if i < bands - 1 else H
        if t >= H:
            break
        out.append((im.crop((0, t, W, b)), t))
    return out


def ocr_text(png_bytes: bytes, region: Optional[Tuple[int, int, int, int]] = None,
             lang: str = "chi_sim", scale: float = 1.5, bands: int = 5) -> str:
    """鲁棒整屏/区域 OCR：分横带 + 放大，用于关键词存在性判断（不取坐标）。"""
    if not png_bytes:
        return ""
    try:
        pytesseract = _tess()
        im = _load_rgb(png_bytes)
    except Exception:  # noqa
        return ""
    texts = []
    try:
        if region:
            crops = [(im.crop(region), 0)]
        else:
            crops = _bands(im, bands=bands)
        for crop, _oy in crops:
            crop, _ratio = _normalize(crop)
            W, H = crop.size
            crop = crop.resize((int(W * scale), int(H * scale)))
            texts.append(pytesseract.image_to_string(crop, lang=lang, config="--psm 6"))
    except Exception:  # noqa
        return ""
    return "\n".join(t for t in texts if t)


def ocr_find(png_bytes: bytes, keywords, region: Optional[Tuple[int, int, int, int]] = None,
             lang: str = "chi_sim", min_conf: int = 40,
             scale: float = 1.5) -> Optional[Point]:
    """OCR 坐标定位：全图（或指定区域）适度放大后逐词命中关键字，返回中心 Point。

    与 ocr_text 的分工：本函数只用于需要点击坐标的高对比锚点（按钮/黑绿字），
    因此不切顶、不分带，避免把顶部搜索行裁掉；稀疏小灰字的存在判断交给 ocr_text。
    keywords 可为字符串或列表；为列表时多个候选按传入顺序优先。
    找不到、依赖缺失、坏图均返回 None，绝不抛异常弄挂流程。
    """
    kws = [keywords] if isinstance(keywords, str) else list(keywords)
    if not png_bytes or not kws:
        return None
    try:
        pytesseract = _tess()
        im = _load_rgb(png_bytes)
    except Exception:  # noqa
        return None
    ox = oy = 0
    ratio = 1.0
    try:
        if region:
            ox, oy = region[0], region[1]
            im = im.crop(region)
        im, ratio = _normalize(im)
        W, H = im.size
        big = im.resize((int(W * scale), int(H * scale)))
        d = pytesseract.image_to_data(big, lang=lang, config="--psm 6",
                                      output_type=pytesseract.Output.DICT)
    except Exception:  # noqa
        return None
    words = []
    for i in range(len(d.get("text", []))):
        tx = (d["text"][i] or "").strip()
        if not tx:
            continue
        try:
            conf = float(d["conf"][i])
        except (TypeError, ValueError):
            conf = -1.0
        # 归一化+放大后坐标按 ratio/scale 映射回原分辨率
        x = ox + d["left"][i] / scale * ratio
        y = oy + d["top"][i] / scale * ratio
        w = d["width"][i] / scale * ratio
        h = d["height"][i] / scale * ratio
        key = (d["block_num"][i], d["par_num"][i], d["line_num"][i])
        words.append({"t": tx, "x": x, "y": y, "w": w, "h": h,
                      "conf": conf, "line": key})
    # 按行聚合：中文常被切成子词（如“手机联系人”→“手机/联系/人”），
    # 把同一行的词按 x 排序拼成整行文本再匹配，命中时返回整行中心（按钮/行可点区）。
    grouped = {}
    for wd in words:
        grouped.setdefault(wd["line"], []).append(wd)
    line_objs = []
    for ws in grouped.values():
        ws.sort(key=lambda a: a["x"])
        joined = "".join(a["t"] for a in ws)
        l = min(a["x"] for a in ws)
        t = min(a["y"] for a in ws)
        r = max(a["x"] + a["w"] for a in ws)
        b = max(a["y"] + a["h"] for a in ws)
        cmax = max(a["conf"] for a in ws)
        line_objs.append((joined, l, t, r, b, cmax))

    def _mk(text, l, t, r, b):
        return Point(int((l + r) / 2), int((t + b) / 2), text=text,
                     bounds=(int(l), int(t), int(r), int(b)))

    # 按关键字传入顺序优先；行级命中为主，词级命中作兜底
    for k in kws:
        best, best_score = None, -1.0
        for (joined, l, t, r, b, cmax) in line_objs:
            if cmax < min_conf or k not in joined:
                continue
            score = cmax + (r - l) * (b - t) / 1e6
            if score > best_score:
                best_score = score
                best = _mk(joined, l, t, r, b)
        for wd in words:
            if wd["conf"] < min_conf or k not in wd["t"]:
                continue
            score = wd["conf"] + wd["w"] * wd["h"] / 1e6
            if score > best_score:
                best_score = score
                best = _mk(wd["t"], wd["x"], wd["y"], wd["x"] + wd["w"], wd["y"] + wd["h"])
        if best is not None:
            return best
    return None
