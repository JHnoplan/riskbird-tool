# -*- coding: utf-8 -*-
"""马可波罗号码图片 OCR 冷启动训练：
① 只用「投影段数 == token 推定的字符数」的干净图切片（无粘连，切分零风险）；
② 按渲染样式(y_left/pro)分别聚类成簇；
③ 自动锚点验证：手机号(N=11)首字必为 '1'、固话(N=12/13)区号后第 4/5 字必为 '-'；
④ 打印簇统计 + 出一张簇代表拼图（人工只需读这一张图来标注 0-9）。
产出：/tmp/mk_clusters.json、/tmp/mk_cluster_sheet.png
"""
import glob, json, os, sys, collections
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "b2bcollect_build"))   # 仓库根当场推导，不写死某台机器的路径
from PIL import Image, ImageDraw
from mk_phone_ocr import (ink_grid, col_ink, projection_runs, make_glyph, canon,
                          norm_dist, expected_chars, CANVAS_W, CANVAS_H)

OUT = "/tmp/mkimgs"
LBL = json.load(open("/tmp/mkimgs_labels.json"))

clean, odd, notoken = [], [], 0
for fn, v in sorted(LBL.items()):
    path = os.path.join(OUT, fn)
    if not os.path.exists(path):
        continue
    n = expected_chars(v.get("token", ""))
    if not n:
        notoken += 1
        continue
    w, h, g = ink_grid(path)
    cols = col_ink(w, h, g)
    runs = projection_runs(cols)
    if len(runs) == n:
        gl = [make_glyph(w, h, g, a, b) for a, b in runs]
        if all(gl):
            clean.append((fn, v["style"], n, gl, v.get("shop", "")))
    else:
        odd.append((fn, v["style"], n, len(runs)))

print("总标签:", len(LBL), " 本地有图:", len(clean) + len(odd),
      " 干净(段数=字数):", len(clean), " 粘连/断裂:", len(odd), " token异常:", notoken)
print("干净样本按样式:", collections.Counter(c[1] for c in clean))
print("干净样本按字数:", collections.Counter(c[2] for c in clean))

# ---------- 聚类 ----------
THRESH = float(sys.argv[1]) if len(sys.argv) > 1 else 0.035
clusters = []           # {style, rows, n, w, h, first_fn}
by_glyph = {}           # (style, idx) -> cluster id
for fn, style, n, gl, shop in clean:
    for pos, gp in enumerate(gl):
        c = canon(gp)
        hit = None
        for ci, cl in enumerate(clusters):
            if cl["style"] != style:
                continue
            if norm_dist(c, cl["rows"]) <= THRESH:
                hit = ci
                break
        if hit is None:
            clusters.append({"style": style, "rows": c, "n": 1,
                             "w": gp["w"], "h": gp["h"], "seed": fn})
            by_glyph.setdefault((style, fn), []).append(len(clusters) - 1)
        else:
            clusters[hit]["n"] += 1
            by_glyph.setdefault((style, fn), []).append(hit)

print("\n阈值", THRESH, " 簇数:", len(clusters),
      " 按样式:", collections.Counter(c["style"] for c in clusters))
for i, c in enumerate(clusters):
    print(f"  #{i:<3} {c['style']:<7} 字宽{c['w']:<3} 字高{c['h']:<3} 样本{c['n']:<5} seed={c['seed']}")

# ---------- 自动锚点 ----------
first_of_11 = collections.Counter()
dash_pos = collections.Counter()
for fn, style, n, gl, shop in clean:
    ids = by_glyph[(style, fn)]
    if n == 11:
        first_of_11[(style, ids[0])] += 1
    if n in (12, 13):
        for pos, cid in enumerate(ids):
            c = clusters[cid]
            if c["w"] <= 5:                    # 横杠字形很扁窄
                dash_pos[(style, cid, pos)] += 1
print("\nN=11 首字簇分布(应为单一簇 → '1'):", dict(first_of_11))
print("N=12/13 窄字簇位置分布(应为 '-'，位置=区号长):",
      dict(list(dash_pos.most_common(8))))

# ---------- 簇代表拼图（人工标注用）----------
CELL_W, CELL_H = CANVAS_W * 4 + 8, CANVAS_H * 4 + 26
per_style = {}
for i, c in enumerate(clusters):
    per_style.setdefault(c["style"], []).append(i)
sheets = []
for style, ids in per_style.items():
    cols_n = 10
    rows_n = (len(ids) + cols_n - 1) // cols_n
    im = Image.new("RGB", (cols_n * CELL_W, rows_n * CELL_H), "white")
    dr = ImageDraw.Draw(im)
    for k, i in enumerate(ids):
        c = clusters[i]
        x0 = (k % cols_n) * CELL_W + 4
        y0 = (k // cols_n) * CELL_H + 20
        for y in range(CANVAS_H):
            row = c["rows"][y]
            for x in range(CANVAS_W):
                if (row >> x) & 1:
                    dr.rectangle([x0 + x * 4, y0 + y * 4, x0 + x * 4 + 3, y0 + y * 4 + 3], fill="black")
        dr.text((x0, y0 - 16), f"#{i} n={c['n']} {c['w']}x{c['h']}", fill="red")
    p = f"/tmp/mk_cluster_sheet_{style}.png"
    im.save(p)
    sheets.append((style, p, len(ids)))
    print("存", p, "簇数", len(ids))

json.dump([{"style": c["style"], "w": c["w"], "h": c["h"], "n": c["n"],
            "seed": c["seed"], "rows": list(c["rows"])} for c in clusters],
          open("/tmp/mk_clusters.json", "w"))
print("簇数据存 /tmp/mk_clusters.json")
print("粘连样本(前10):", odd[:10])
