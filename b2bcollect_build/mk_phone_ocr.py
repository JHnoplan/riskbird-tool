# -*- coding: utf-8 -*-
"""mk_phone_ocr.py —— 马可波罗(cn.makepolo.com)「号码图片码」离线识别引擎。

为什么要它：马可波罗商铺「联系我们」页把手机号渲染成一张 PNG
（/purchase/buildStringToimg.php?t_page=pro&phone=<密文token>），页面源码里没有任何
明文手机，固话/邮箱才是明文。直接放弃就等于一半客户的手机号拿不到，所以在本工具里
内置一个「不依赖任何 OCR 库、不联网」的图片数字识别器。

技术选型（小工具能自带的量级，思路取自通用车牌/票据 OCR 的经典流水线）：
  ① 版面固定：图片高恒为 22px、字符集只有 0-9 和 '-'、号码长度可由 URL 里的
     token 长度推出（实测 token_len − 43 = 字符数：54→11 位、55→12 位、56→13 位）。
     有先验就能把「切几个字」变成约束而非猜测，这是准确率的主要来源。
  ② 二值化兼容两种渲染：黑字(t_page=y_left，单字仅 6×10)与红字(t_page=pro，单字约
     11×17)，红色按「R 高、G/B 低」判定，避免红字在灰度阈值下丢失。
  ③ 竖投影分段 + 粘连拆分：字间多数有空白列，投影直接分段；粘连段用「列墨量谷点」
     做候选切点（没有谷点时退化为段内全位置），再用动态规划在「必须凑够 N 个字」的
     约束下选总匹配代价最小的切法（分割与识别联合优化，避免单点切错导致整串错开）。
  ④ 约束解码：中国号码的结构就是最强的语言模型——11 位且 1 开头是手机号（串中不许
     出现横杠）、0xx-xxxxxxxx / 0xxx-xxxxxxx 是固话（横杠只许出现在区号后一位）。
     把这套规则写成「每个位置允许哪些字符」喂给 DP 与匹配，实测能把粘连碎片
     误判成 '-' 的那类错误整批消掉。多假设（手机/3位区号/4位区号）并行解码取代价最小。
  ⑤ 识别：字形居中归一化到固定画布后打包成位图，与字典做汉明距离最近邻，
     同字保留多模板（不同字号/渲染样式各自成组），取最小距离。
  ⑥ 自审：整串置信度 + 号码形态校验双闸，过不了就返回空——宁可这一条只有固话/邮箱，
     也不入库一个编造出来的假号。

字典 mk_phone_font.json 由 debug/mk_phone_train.py + debug/mk_phone_eval.py 一次性冷启动
生成（人工只读了「聚类代表字形拼图」两张图，共 21 个字形，不按号码逐张转写），
随工具一起打包，运行期零标注成本。
"""
import io
import itertools
import json
import os
import re
import sys

from PIL import Image

# 字符集：马可波罗的号码栏只会出现数字和分隔符
CHARSET = "0123456789-"
# 归一化画布（实测单字宽 4~13、高 1~17，留余量后居中即可跨字号比较）
CANVAS_W = 17
CANVAS_H = 17
# 匹配阈值：最优模板距离上限、与次优字符的最小差距（低于它就是真两可，弃）
MATCH_MAX_DIST = 0.16
MATCH_MIN_MARGIN = 0.005
# 双通道二值化（实测 186 张样本扫过 60/90/110/130/150/170 后的结论）：
#   找字缝用细图（110）：150 会把抗锯齿灰像素也算成墨，相邻字被灰桥连，粘连 23 例→降到 15 例；
#   取字形用粗图（150）：小字号 y_left 单字只 6×10，按 110 二值化会丢笔画，逐字距离反而飙升。
# 两者各取所长，所以分割与识别分别用自己的那张栅格。
SEG_DARK = 110
GLYPH_DARK = 150
# token 长度与号码字符数的经验偏移（实测 1900+ 个 token：54/55/56 ↔ 11/12/13 字符）
TOKEN_LEN_OFFSET = 43

_MOBILE_RE = re.compile(r"^1[3-9]\d{9}$")
_TEL_RE = re.compile(r"^0\d{2,3}-\d{7,8}$")
_DIGITS = "0123456789"


# ── 像素 → 二值栅格 ────────────────────────────────────────────
def ink_grid(data, dark=GLYPH_DARK):
    """PNG 字节 → (w, h, grid)，grid[y][x] = 1 表示字墨（深色或红色）。
    dark 控制黑字阈值（见上面 SEG_DARK / GLYPH_DARK 的双通道说明）；红字（t_page=pro）
    按「R 高、G/B 低」判，不受 dark 影响，否则红字在灰度阈值下会整个丢失。"""
    im = Image.open(io.BytesIO(data)) if isinstance(data, (bytes, bytearray)) else Image.open(data)
    im = im.convert("RGB")
    w, h = im.size
    px = im.load()
    grid = [[0] * w for _ in range(h)]
    for y in range(h):
        row = grid[y]
        for x in range(w):
            r, g, b = px[x, y]
            if min(r, g, b) < dark or (r > 150 and g < 110 and b < 110):
                row[x] = 1
    return w, h, grid


def col_ink(w, h, grid):
    return [sum(grid[y][x] for y in range(h)) for x in range(w)]


def projection_runs(cols):
    """列墨量 → 连续有墨列区间 [(x0, x1)]。"""
    out, x, n = [], 0, len(cols)
    while x < n:
        if cols[x] > 0:
            x2 = x
            while x2 + 1 < n and cols[x2 + 1] > 0:
                x2 += 1
            out.append((x, x2))
            x = x2 + 1
        else:
            x += 1
    return out


def make_glyph(w, h, grid, x0, x1):
    """按列区间裁出一个字形（行方向自动贴墨），返回 {w,h,rows,ink}。"""
    ys = [y for y in range(h) for x in range(x0, x1 + 1) if grid[y][x]]
    if not ys:
        return None
    y0, y1 = min(ys), max(ys)
    rows = []
    ink = 0
    for y in range(y0, y1 + 1):
        v = 0
        for x in range(x0, x1 + 1):
            if grid[y][x]:
                v |= 1 << (x - x0)
        rows.append(v)
        ink += bin(v).count("1")
    return {"w": x1 - x0 + 1, "h": y1 - y0 + 1, "rows": tuple(rows), "ink": ink}


def canon(gp, cw=CANVAS_W, ch=CANVAS_H):
    """字形居中归一化到 cw×ch 画布，返回 ch 个行位掩码（从此与原始尺寸无关，可跨样式比较）。"""
    pad_x = (cw - gp["w"]) // 2
    pad_y = (ch - gp["h"]) // 2
    rows = [0] * ch
    mask = (1 << cw) - 1
    for i, r in enumerate(gp["rows"]):
        y = i + pad_y
        if 0 <= y < ch:
            rows[y] = ((r << pad_x) if pad_x >= 0 else (r >> -pad_x)) & mask
    return tuple(rows)


def hamming(a, b):
    return sum(bin(x ^ y).count("1") for x, y in zip(a, b))


def norm_dist(a, b):
    """归一化汉明距离（差异像素 / 画布像素），与模板大小无关。"""
    return hamming(a, b) / float(CANVAS_W * CANVAS_H)


# ── 号码结构：每个位置允许什么字符（约束解码）──────────────────
# 三种假设：手机号、3 位区号固话、4 位区号固话
def hypotheses(n):
    """按号码长度列出可能的结构假设，返回 [(kind, 横杠位置或 None)]。"""
    out = []
    if n == 11:
        out.append(("mobile", None))       # 1[3-9]xxxxxxxxx
        out.append(("tel3", 3))            # 0xx-xxxxxxx（如 010-1234567）
    elif n == 12:
        out.append(("tel3", 3))            # 0xx-xxxxxxxx
        out.append(("tel4", 4))            # 0xxx-xxxxxxx
    elif n == 13:
        out.append(("tel4", 4))            # 0xxx-xxxxxxxx
    return out


def pos_allow(kind, dash_pos, pos, n):
    """该假设下第 pos 位允许的字符集合。"""
    if kind == "mobile":
        if pos == 0:
            return "1"
        if pos == 1:
            return "3456789"
        return _DIGITS
    # 固话：首位 0，横杠钉死在区号后
    if pos == 0:
        return "0"
    if pos == dash_pos:
        return "-"
    return _DIGITS


# ── 分割：投影分段 + 谷点候选 + DP 凑字数 ──────────────────────
def cut_candidates(w, h, grid, x0, x1, cols):
    """在一个粘连段内部找候选切点：列墨量的局部谷点（且明显低于段内平均）。
    两端各留 2px，避免把笔画边缘当成字缝。
    小字号（y_left 单字仅 6px）两字紧贴时常常一道谷都没有，这种段就把段内每个位置
    都交给字典打分（宁可多试几刀，也别把粘连段整块当成一个字）。"""
    inner = list(range(x0 + 2, x1 - 1))
    if not inner:
        return []
    avg = sum(cols[x] for x in range(x0, x1 + 1)) / float(x1 - x0 + 1)
    cands = [x for x in inner
             if cols[x] <= cols[x - 1] and cols[x] <= cols[x + 1] and cols[x] <= max(1, avg * 0.55)]
    # 相邻候选只留墨量更低的那个（同一道字缝可能被连续几列判成谷）
    cands.sort(key=lambda x: (cols[x], x))
    keep = []
    for x in cands:
        if all(abs(x - k) >= 2 for k in keep):
            keep.append(x)
    if not keep:
        keep = list(range(x0 + 3, x1 - 2))
    if len(keep) > 12:                      # 组合数保险丝：只留墨量最浅的 12 处
        keep = sorted(sorted(keep, key=lambda x: cols[x])[:12])
    return sorted(keep)


def enumerate_cuts(w, h, grid, seg, cols, max_parts):
    """把一个投影段枚举成若干种切法：{片数: [切点组合]}，每片至少 4px（数字没有更窄的）。"""
    x0, x1 = seg
    width = x1 - x0 + 1
    out = {1: [None]}
    if width < 8:
        return out
    cands = cut_candidates(w, h, grid, x0, x1, cols)
    for k in range(2, min(max_parts, width // 4) + 1):
        if len(cands) < k - 1:
            continue
        combos = []
        for cb in itertools.combinations(cands, k - 1):
            edges = [x0 - 1] + list(cb) + [x1]
            if all(edges[i + 1] - edges[i] >= 4 for i in range(len(edges) - 1)):
                combos.append(cb)
        if combos:
            out[k] = combos
    return out


def slice_segments(w, h, grid_seg, grid_glyph, cols, segs, want, scorer=None):
    """把投影段切成正好 want 个字形，返回 (字形列表, 总代价)；切不出来返回 (None, 0)。
    grid_seg 定字缝（细图）、grid_glyph 取字形（粗图），cols 来自 grid_seg。
    段数 == want：直接取；段数 > want：先并掉最窄的碎段（断笔/'-'）；
    段数 < want：DP 在粘连段里选切点，代价由 scorer(glyph, 位置) 给出
    （scorer 为空时只保证数量正确，供建字典阶段用）。"""
    if len(segs) > want:
        segs = _merge_runs(segs, want)
    if len(segs) == want:
        out = [make_glyph(w, h, grid_glyph, a, b) for a, b in segs]
        if not all(out):
            return None, 0.0
        cost = sum(scorer(g, i) for i, g in enumerate(out)) if scorer else 0.0
        return out, cost
    nseg = len(segs)
    if want > nseg * 6:                       # 需要切出太多片，判定为异常图
        return None, 0.0
    glyphs = []
    for sg in segs:
        per = {}
        for k, combos in enumerate_cuts(w, h, grid_seg, sg, cols, want).items():
            lst = []
            for cb in combos:
                cuts = [sg[0] - 1] + (list(cb) if cb else []) + [sg[1]]
                parts = []
                ok = True
                for i in range(len(cuts) - 1):
                    g = make_glyph(w, h, grid_glyph, cuts[i] + 1, cuts[i + 1])
                    if not g:
                        ok = False
                        break
                    parts.append(g)
                if ok and all(p["w"] >= 4 for p in parts):
                    lst.append(parts)
            if lst:
                per[k] = lst
        glyphs.append(per)
    dp = {0: (0.0, [])}
    for si in range(nseg):
        nxt = {}
        tail = nseg - si - 1                  # 后面还有几段（每段至少 1 片）
        for k, lst in glyphs[si].items():
            for parts in lst:
                for used, (base, hist) in dp.items():
                    key = used + k
                    if key > want - tail:
                        continue
                    cost = base + sum(scorer(p, used + i) for i, p in enumerate(parts)) \
                        if scorer else base
                    if key not in nxt or cost < nxt[key][0]:
                        nxt[key] = (cost, hist + [parts])
        if not nxt:
            return None, 0.0
        dp = nxt
    best = dp.get(want)
    if not best:
        return None, 0.0
    out = []
    for parts in best[1]:
        out.extend(parts)
    return (out if len(out) == want else None), best[0]


def _merge_runs(segs, want):
    """段数多于期望：反复把最窄的段并进邻居（窄段多半是同一个字的断笔）。"""
    segs = list(segs)
    while len(segs) > want:
        widths = [(s[1] - s[0] + 1, i) for i, s in enumerate(segs)]
        _, i = min(widths)
        if i == 0:
            j = 0
        elif i == len(segs) - 1:
            j = i - 1
        else:
            j = i - 1 if (segs[i - 1][1] - segs[i - 1][0]) <= (segs[i + 1][1] - segs[i + 1][0]) else i
        a, b = segs[j]
        c, d = segs[j + 1]
        segs[j:j + 2] = [(a, d)]
    return segs


# ── 引擎 ──────────────────────────────────────────────────────
class MkPhoneOcr(object):
    def __init__(self, font=None):
        # style -> {char: [canon_rows, ...]}
        self.font = {}
        if font:
            self.load(font)

    # -- 字典读写 --
    def load(self, font):
        self.font = {}
        for style, items in (font or {}).items():
            self.font[style] = {ch: [_unhex(hx) for hx in hexes] for ch, hexes in items.items()}
        return self

    def dump(self):
        return {st: {ch: [_hexrows(rows) for rows in lst] for ch, lst in d.items()}
                for st, d in self.font.items()}

    @classmethod
    def from_file(cls, path):
        return cls(json.load(open(path)))

    def add(self, style, ch, gp):
        self.font.setdefault(style, {}).setdefault(ch, []).append(canon(gp))

    def cover(self):
        return {st: sorted(d) for st, d in self.font.items()}

    # -- 单字匹配（可按位置允许集过滤）--
    def _rank(self, style, gp, allow=None):
        c = canon(gp)
        d = self.font.get(style) or {}
        scored = {}
        for ch, tpls in d.items():
            if allow is not None and ch not in allow:
                continue
            best = min(norm_dist(c, t) for t in tpls)
            scored[ch] = best
        if not scored:
            return None, 1.0, 1.0
        rank = sorted(scored.items(), key=lambda kv: kv[1])
        return rank[0][0], rank[0][1], (rank[1][1] if len(rank) > 1 else 1.0)

    def match(self, style, gp, allow=None):
        return self._rank(style, gp, allow)

    def _scorer(self, style, allow_at):
        """给 DP 用的代价函数：该位置不允许的字符一律不参与，代价=最小距离。"""
        def scorer(gp, pos):
            c = canon(gp)
            allow = allow_at(pos)
            tpls = []
            for ch, lst in (self.font.get(style) or {}).items():
                if ch in allow:
                    tpls.extend(lst)
            if not tpls:
                return 1.0
            return min(norm_dist(c, t) for t in tpls)
        return scorer

    # -- 整图识别 --
    def recognize(self, data, style="y_left", chars=None, seg_dark=None, dual=True):
        """返回 (号码, 诊断 dict)。号码为空串表示不可信，调用方应当丢弃不用。
        seg_dark=None 走单通道（分割与字形同阈值，字典同源，作为首选）；
        dual=True 时单通道未过闸再用细图定缝兜一次（专治抗锯齿灰桥造成的粘连）。"""
        plans = [(None, GLYPH_DARK)] if not dual else [(None, GLYPH_DARK), (SEG_DARK, GLYPH_DARK)]
        if seg_dark is not None:
            plans = [(seg_dark, GLYPH_DARK if seg_dark != GLYPH_DARK else seg_dark)]
        err = ""
        diag = {}
        for sd, gd in plans:
            number, diag = self._recognize(data, style, chars, sd, gd)
            if number:
                if sd is not None:
                    diag["refine"] = "细图缝兜底"
                return number, diag
            err = diag.get("reason", "")
        return "", dict(diag, **{"reason": err})

    def _recognize(self, data, style="y_left", chars=None, seg_dark=None, glyph_dark=GLYPH_DARK):
        w, h, grid_glyph = ink_grid(data, glyph_dark)
        if seg_dark is None or seg_dark == glyph_dark:
            grid_seg, cols = grid_glyph, col_ink(w, h, grid_glyph)
        else:
            _, _, grid_seg = ink_grid(data, seg_dark)
            cols = col_ink(w, h, grid_seg)
        segs = projection_runs(cols)
        if not segs:
            return "", {"reason": "空白图"}
        diag = {"w": w, "h": h, "runs": len(segs)}
        if chars is None:
            chars = len(segs)
            diag["chars_src"] = "projection"
        hyps = hypotheses(chars)
        if not hyps:
            return "", {"reason": "长度 %d 不是手机/固话形态" % chars, "diag": diag}
        best = None
        for kind, dash in hyps:
            allow_at = (lambda p, k=kind, d=dash: pos_allow(k, d, p, chars))
            glyphs, cost = slice_segments(w, h, grid_seg, grid_glyph, cols, segs, chars,
                                          self._scorer(style, allow_at))
            if not glyphs:
                continue
            out, dists = [], []
            bad = ""
            for pos, gp in enumerate(glyphs):
                ch, d0, d1 = self._rank(style, gp, allow_at(pos))
                if ch is None:
                    bad = "字典缺样式 %s" % style
                    break
                if d0 > MATCH_MAX_DIST:
                    bad = "第%d位无把握(距离%.3f)" % (pos + 1, d0)
                    break
                if d1 - d0 < MATCH_MIN_MARGIN:
                    bad = "第%d位前后候选几乎同分" % (pos + 1)
                    break
                out.append(ch)
                dists.append((d0, d1))
            if bad:
                continue
            text = "".join(out)
            cand = (cost, text, {"kind": kind, "worst_dist": round(max(d for d, _ in dists), 4),
                                 "margin": round(min(d1 - d0 for d0, d1 in dists), 4),
                                 "n": len(out), "diag": diag})
            if is_plausible_number(text):
                if best is None or cand[0] < best[0]:
                    best = cand
        if not best:
            # 所有结构假设都没过形态闸：宁可不出字，也不交一个编造出来的号
            return "", {"reason": "切分或匹配未过闸", "diag": diag}
        return best[1], dict(best[2], **{"cost": round(best[0], 4)})


def expected_chars(token):
    """URL 里 phone= 的密文 token 长度 → 号码字符数（实测偏移 43）。"""
    if not token:
        return None
    n = len(token) - TOKEN_LEN_OFFSET
    return n if 6 <= n <= 24 else None


def is_plausible_number(text):
    """号码形态自检：手机号或带区号固话才认（出现别的东西就说明识别跑偏了）。"""
    t = (text or "").strip()
    return bool(_MOBILE_RE.match(t) or _TEL_RE.match(t))


def _hexrows(rows):
    big = 0
    for i, r in enumerate(rows):
        big |= r << (CANVAS_W * i)
    return "%x" % big


def _unhex(hx):
    big = int(hx, 16)
    mask = (1 << CANVAS_W) - 1
    return tuple((big >> (CANVAS_W * i)) & mask for i in range(CANVAS_H))


def load_default_font():
    """读随包字体字典（没字典就退化成一个空引擎，马可波罗手机号识别不了但不会报错）。

    打包态下三个位置都得试：PyInstaller 单文件跑在临时解包目录 sys._MEIPASS里，
    --add-data 的 `mk_phone_font.json` 就落在那儿；而 frozen 顶层模块的 __file__ 在不同
    PyInstaller 版本下可能是包内绝对路径、也可能只剩裸文件名（dirname 为空）；
    双击 exe 时 cwd 又不一定是 exe 所在目录。少试一个位置就是“开发机跑得好、
    客户机上手机号整列空白”那种只能现场才能发现的坑。"""
    here = os.path.dirname(os.path.abspath(__file__))
    cands = []
    meipass = getattr(sys, "_MEIPASS", "")
    if meipass:
        cands.append(os.path.join(meipass, "mk_phone_font.json"))
        cands.append(os.path.join(meipass, "b2bcollect_build", "mk_phone_font.json"))
    exe_dir = os.path.dirname(os.path.abspath(getattr(sys, "executable", "")))
    if exe_dir:
        cands.append(os.path.join(exe_dir, "mk_phone_font.json"))
    cands += [os.path.join(here, "mk_phone_font.json"),
              os.path.join(os.getcwd(), "mk_phone_font.json")]
    for cand in cands:
        if os.path.exists(cand):
            try:
                return MkPhoneOcr(json.load(open(cand)))
            except Exception:
                pass
    return MkPhoneOcr()
