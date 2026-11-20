# -*- coding: utf-8 -*-
"""用簇标注建 OCR 字典并做三重验证：
① 人工转写的 20 条真值（含粘连样本）逐条比对；
② 留出实验：一半干净样本建字典、另一半只靠形态合规判分（防止自证）；
③ 全量跑一遍，统计出字率/失败原因分布。
字典最终导出成 b2bcollect_build/mk_phone_font.json 随工具打包。"""
import glob, json, os, sys, collections
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "b2bcollect_build"))   # 仓库根当场推导，不写死某台机器的路径
from PIL import Image
from mk_phone_ocr import (MkPhoneOcr, ink_grid, col_ink, projection_runs, make_glyph,
                          canon, norm_dist, expected_chars, is_plausible_number)

OUT = "/tmp/mkimgs"
LBL = json.load(open("/tmp/mkimgs_labels.json"))
THRESH = 0.035

# 人工读「簇代表拼图」得到的标注（一次读两张图，共 21 个字形）
LABELS = {10: "1", 11: "8", 12: "3", 13: "6", 14: "5", 15: "9", 16: "7", 17: "0",
          18: "2", 19: "4", 20: "-",          # y_left（黑字，小字号）
          0: "1", 1: "3", 2: "7", 3: "0", 4: "6", 5: "8", 6: "4", 7: "5", 8: "2", 9: "9"}

# 人工逐张转写的真值（文件名已按磁盘实际名核对）
TRUTH = {
    "pro_0081slVZ-h.png": "18922800557",
    "pro_1246x8givi.png": "13650342510",
    "pro_172a0yFCgJ.png": "13509839987",
    "pro_1f2ax4nmO-.png": "15020728253",
    "pro_31d97KC1Bo.png": "17791801652",
    "pro_36546pafQJ.png": "15980846919",
    "pro_6441MlwL75.png": "13434060426",
    "pro_7600HYWV99.png": "13636435366",
    "pro_86fbYiMi0c.png": "13755154094",
    "pro_caedju14P1.png": "13923729317",
    "y_left_0656imzYoo.png": "17791801652",
    "y_left_0d9cFC6B8X.png": "19514718865",
    "y_left_1a4ea9SL4y.png": "0757-63301263",
    "y_left_1d731P0AOl.png": "18655118925",
    "y_left_21aesjZC0l.png": "021-54783528",
    "y_left_22aeKkGqMF.png": "13677587189",
    "y_left_236aJPY*m0.png": "0769-86331949",
    "y_left_23fdRvPVoO.png": "13641613875",
    "y_left_269at66WlX.png": "18663973500",
    "y_left_26d5WumYeI.png": "13788947795",
}

# ---------- 重跑聚类（与训练脚本同逻辑同顺序，保证簇号一致） ----------
clean = []
for fn, v in sorted(LBL.items()):
    path = os.path.join(OUT, fn)
    if not os.path.exists(path) or v["token_len"] - 43 < 6:
        continue
    w, h, g = ink_grid(path)
    cols = col_ink(w, h, g)
    runs = projection_runs(cols)
    n = v["token_len"] - 43
    if len(runs) == n:
        gl = [make_glyph(w, h, g, a, b) for a, b in runs]
        if all(gl):
            clean.append((fn, v["style"], n, gl))
clusters, members = [], collections.defaultdict(list)
for fn, style, n, gl in clean:
    for gp in gl:
        c = canon(gp)
        hit = None
        for ci, cl in enumerate(clusters):
            if cl["style"] == style and norm_dist(c, cl["rows"]) <= THRESH:
                hit = ci
                break
        if hit is None:
            clusters.append({"style": style, "rows": c})
            hit = len(clusters) - 1
        members[hit].append((fn, style, c))
assert len(clusters) == 21, f"簇数 {len(clusters)} != 21，聚类结果与标注不对应，需重读簇图"
print("干净样本:", len(clean), " 簇数:", len(clusters))


def build_font(train_fns=None, per_cluster=6):
    ocr = MkPhoneOcr()
    for ci, ms in members.items():
        ch = LABELS.get(ci)
        if not ch:
            continue
        st = clusters[ci]["style"]
        lst = ocr.font.setdefault(st, {}).setdefault(ch, [])
        for fn, _, rows in ms:
            if train_fns is not None and fn not in train_fns:
                continue
            if len(lst) >= per_cluster:
                break
            lst.append(rows)
    return ocr


# ---------- ① 全样本字典 → 真值逐条比对 ----------
ocr = build_font()
print("\n== ① 20 条人工真值回测 ==")
ok = 0
for fn, truth in sorted(TRUTH.items()):
    p = os.path.join(OUT, fn)
    if not os.path.exists(p):
        print("  缺文件", fn); continue
    style = "pro" if fn.startswith("pro") else "y_left"
    pred, diag = ocr.recognize(open(p, "rb").read(), style=style, chars=len(truth))
    good = pred == truth
    ok += good
    print(f"  {'✓' if good else '✗'} {fn[:24]:<25} 真值 {truth:<14} 识别 {pred:<14} {diag.get('reason', '')}")
print(f"  真值准确率: {ok}/{len(TRUTH)}")

# ---------- ② 留出实验 ----------
fns = sorted({fn for fn, _, _, _ in clean})
train = set(fns[::2])
test = [f for f in fns[1::2]]
ocr2 = build_font(train_fns=train, per_cluster=8)
print(f"\n== ② 留出实验：{len(train)} 张建字典，{len(test)} 张只按号码形态判分 ==")
stat = collections.Counter(); bad = []
for fn in test:
    v = LBL[fn]
    n = v["token_len"] - 43
    pred, diag = ocr2.recognize(open(os.path.join(OUT, fn), "rb").read(),
                               style=v["style"], chars=n)
    if not pred:
        stat["没出字"] += 1
        bad.append((fn, "无", diag.get("reason")))
        continue
    if is_plausible_number(pred):
        stat["形态合规"] += 1
    else:
        stat["形态不合规"] += 1
        bad.append((fn, pred, "形态"))
print("  ", dict(stat))
for b in bad[:12]:
    print("   ✗", b)

# ---------- ③ 全量（含粘连）跑一遍 ----------
print("\n== ③ 全量 188 张（含粘连） ==")
res = collections.Counter(); fails = []
for fn, v in sorted(LBL.items()):
    p = os.path.join(OUT, fn)
    if not os.path.exists(p):
        continue
    n = expected_chars(v["token"])
    pred, diag = ocr.recognize(open(p, "rb").read(), style=v["style"], chars=n)
    if pred and is_plausible_number(pred):
        res["出字且合规"] += 1
    elif pred:
        res["出字但不合规"] += 1
        fails.append((fn, pred, "不合规"))
    else:
        res["没出字"] += 1
        fails.append((fn, "", diag.get("reason", "")))
print("  ", dict(res))
for f in fails[:14]:
    print("   ·", f)

# ---------- 导出字典 ----------
os.makedirs("/tmp/mkfont", exist_ok=True)
json.dump(ocr.dump(), open("/tmp/mkfont/mk_phone_font.json", "w"), ensure_ascii=False)
print("\n字典导出 /tmp/mkfont/mk_phone_font.json 体积:",
      os.path.getsize("/tmp/mkfont/mk_phone_font.json"), "字节")
