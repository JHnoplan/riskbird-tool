# -*- coding: utf-8 -*-
"""channel_health.py —— 通道健康指标与「连续零产出」红牌（功能 J）。

问题很具体：这个工具同时跑 8 路关键词站 + 3 路工商名单通道，**某一路悄悄不出货**
是过去几轮实机故障里最难被发现的一类（1688 恒 0 家那轮就是——单看日志每一轮都
「正常结束」，把连续几轮摆在一起才看得出那一路已经死了）。原因在结构上：
一轮跑完的日志只报「本轮采到 N 家」，从来没有「这一路连续第几轮是 0」。
顶尖平台（Clay 的 provider health、各大厂数据中台的通道 SLA 看板）在这件事上的
做法是一致的：**给每条通道记滚动健康账，连续零产出到阈值就自动亮红牌**，
而不是等人肉翻日志。本模块就是那张账。

【三条判据，都是为了让这张账不被误读】
  1) 「本轮没跑」不等于「本轮零产出」：只有**这一轮真的启用并跑过**的通道才参与
     连续计数，否则用户勾掉一站只是为了省时间，第二天醒来会看到它挂着红牌——
     那种假警报会让人整张账都不信。
  2) 计数按**家数**（同主体去重后的公司数）而不是行数：同一家在顺企网有 20 行产品
     是常态，按行数会把一条只有 20 行重复的通道算成健康通道。
  3) 台账坏了要出声：读不出来的时候按空账重开，但必须在日志里说明
     「连续轮次从本轮重新数起」——静默清零等于把红牌历史抹掉，那是最阴的失效方式。

运行：纯标准库 + 同级模块；落盘用 tmp + os.replace（和采集进度台账同一手法）。
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import field_evidence as _fe

__all__ = ["LEDGER_FILENAME", "RED_CARD_ROUNDS", "MAX_ROUNDS", "metrics_from",
           "load_ledger", "update_ledger", "health_rows", "alert_lines"]

LEDGER_FILENAME = "通道健康台账.json"
# 连续 N 轮零产出 → 红牌。取 2 是刻意的：1 轮太敏感（站点当天抽风、验证码让人工
# 接管，都会造成单轮 0），3 轮太迟（等三轮才出声，用户已经白等了半天产能）。
RED_CARD_ROUNDS = 2
# 台账只留最近 N 轮明细（连续计数靠最后几轮就够，留全部会把文件越写越大）
MAX_ROUNDS = 30
LEDGER_VERSION = 1


def _company_key(rec):
    """同主体键：优先用瀑布那份实现（一张表里两处两套认亲口径迟早对不上账）。"""
    try:
        import enrich_waterfall as _wf
        key = _wf.company_key(rec)
        if key:
            return key
    except Exception:
        pass
    name = str((rec or {}).get("公司名称") or (rec or {}).get("公司名") or "").strip()
    return name


def metrics_from(records, stats_by_source=None, score_by_source=None):
    """按「数据来源」聚合本轮健康度。返回 {通道名: {...}}。

    records        ：本轮全部原始记录（就地字典即可，只读不改）。
    stats_by_source：{通道名: 该路爬虫 stats()} —— **这一轮跑过的通道**在这里出现。
                     用它而不是用界面看板那份，是因为看板的键是显示名（名单通道带
                     「补采」后缀），和记录里的「数据来源」不同名，硬对就会对不上。
    score_by_source：{通道名: (线索分合计, 立即跟进家数)}，由会话层用 lead_scoring
                     算好传进来。本模块不 import lead_scoring：健康账只管「出没出货」，
                     评分是另一件事，绑在一起会让两个功能互相拖累（少一边就跑不动）。
    """
    stats_by_source = stats_by_source or {}
    score_by_source = score_by_source or {}
    got = {}

    def bucket(name):
        return got.setdefault(name, {"rows": 0, "companies": set(), "with_contact": 0})

    for rec in records or []:
        if not isinstance(rec, dict):
            continue
        name = str(rec.get("数据来源") or "").strip() or "未知来源"
        b = bucket(name)
        b["rows"] += 1
        key = _company_key(rec)
        if key:
            b["companies"].add(key)
        if _fe.has_any_value(rec, _fe.CONTACT_FIELDS):
            b["with_contact"] += 1
    # 跑过但一行没出的通道也必须进账——这正是红牌要抓的那一类，漏掉它就白做了
    for name in stats_by_source:
        bucket(str(name))
    out = {}
    for name, b in got.items():
        companies = len(b["companies"])
        total = int(b["rows"])
        fill = round(b["with_contact"] * 100.0 / total, 1) if total else 0.0
        # 「没算分」和「平均分 0」是两件事：没勾线索分层时 score_by_source 是空的，
        # 这时把两列印成 0 会被读成「这一路的线索全是废数据」——那是凭空造出来的结论。
        scored = name in score_by_source
        avg, work = (score_by_source.get(name) or (None, None))
        out[name] = {
            "rows": total, "companies": companies, "with_contact": b["with_contact"],
            "fill_rate": fill, "avg_score": avg, "work_now": work, "scored": scored,
            "ran": str(name) in stats_by_source,
            "stats": dict(stats_by_source.get(name) or {}),
        }
    return out


def note_from_stats(metric):
    """把该路 stats() 压成一行说明（跑过才有；没跑过直说本轮未启用）。"""
    if not metric.get("ran"):
        return "本轮未启用（不参与连续计数）"
    s = metric.get("stats") or {}
    if not s:
        return f"本轮 {metric.get('companies', 0)} 家"
    bits = []
    if s.get("pages") is not None:
        bits.append(f"翻 {s.get('pages', 0)} 页见 {s.get('listed', 0)} 条")
    for key, label in (("ok", "成功"), ("no_contact", "无联系方式"),
                       ("failed", "失败可重采"), ("early", "早过滤"),
                       ("filtered", "详情不相关")):
        if s.get(key):
            bits.append(f"{label} {s[key]}")
    if s.get("pf_unknown"):
        bits.append(f"属地未标注转名单 {s['pf_unknown']}")
    return "｜".join(bits) if bits else f"本轮 {metric.get('companies', 0)} 家"


# ── 台账读写 ─────────────────────────────────────────────────────
def load_ledger(path):
    """读台账；文件不存在返回空账，**读坏了也返回空账但把原因交给调用方**。

    返回 (data, error)：error 非空时调用方必须把它念出来——「连续第几轮」这种
    计数一旦被静默清零，红牌就永远不会再亮了。
    """
    empty = {"version": LEDGER_VERSION, "rounds": [], "zero_streak": {}}
    p = Path(str(path or ""))
    if not str(path or "") or not p.exists():
        return empty, ""
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        return empty, f"{type(e).__name__}: {str(e).splitlines()[0][:80]}"
    if not isinstance(data, dict) or not isinstance(data.get("zero_streak"), dict):
        return empty, "结构不认识（可能是旧版本或被人手改过）"
    data.setdefault("rounds", [])
    data.setdefault("version", LEDGER_VERSION)
    return data, ""


def update_ledger(path, metrics, log=None, round_label=""):
    """把本轮账并进台账，返回 {"alerts": [...], "rounds": n, "saved": bool, "error": str}。

    alerts 里是本轮**新达到红牌阈值**的通道（连同连续轮次与阈值），由会话层决定
    用什么语气念出来；已达阈值后的每一轮继续累加，但只在**首次跨过阈值**时报警一次
    （否则挂十几轮的通道会把日志刷成噪音，用户反而开始跳过这一段）。
    """
    log = log or (lambda m: None)
    data, err = load_ledger(path)
    if err:
        log(f"⚠ 通道健康台账读不出来（{err}）：连续轮次从本轮重新数起，"
            f"文件本身不会被覆盖坏数据")
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    streak = data.get("zero_streak") or {}
    alerts = []
    for name, m in (metrics or {}).items():
        if not m.get("ran"):
            continue
        before = int(streak.get(name) or 0)
        after = before + 1 if int(m.get("companies") or 0) <= 0 else 0
        streak[name] = after
        m["zero_streak"] = after
        m["red_card"] = after >= RED_CARD_ROUNDS
        if after >= RED_CARD_ROUNDS and before < RED_CARD_ROUNDS:
            alerts.append({"channel": name, "streak": after, "threshold": RED_CARD_ROUNDS})
    for name, m in (metrics or {}).items():
        m.setdefault("zero_streak", int(streak.get(name) or 0))
        m.setdefault("red_card", m["zero_streak"] >= RED_CARD_ROUNDS)
    data["zero_streak"] = streak
    data["rounds"] = (data.get("rounds") or [])[-(MAX_ROUNDS - 1):]
    data["rounds"].append({
        "at": now, "label": str(round_label or ""),
        "channels": {n: {"companies": int(m.get("companies") or 0),
                         "rows": int(m.get("rows") or 0),
                         "ran": bool(m.get("ran"))}
                     for n, m in (metrics or {}).items()},
    })
    saved = False
    if str(path or ""):
        try:
            p = Path(str(path))
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = str(p) + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, str(p))
            saved = True
        except Exception as e:
            log(f"⚠ 通道健康台账落盘失败（本轮账仍在，只是下轮接不上连续计数）："
                f"{type(e).__name__}: {str(e).splitlines()[0][:80]}")
            data["_save_error"] = type(e).__name__
    return {"alerts": alerts, "rounds": len(data.get("rounds") or []),
            "saved": saved, "error": str(data.get("_save_error") or err or "")}


def alert_lines(result, metrics=None):
    """红牌告警文案（界面与会话共用这一份，别各写一句）。"""
    lines = []
    for a in (result or {}).get("alerts") or []:
        lines.append(
            f"🔴 通道红牌：{a['channel']} 已连续 {a['streak']} 轮一家都没出"
            f"（阈值 {a['threshold']} 轮）。这一路多半是页面改版/被限/需要登录，"
            f"建议先单独跑它一次看日志里的失败原因，暂时勾掉它比挂着省时间")
    if not lines and result and not result.get("saved"):
        # 没落成盘也要让人知道连续计数不可信，否则下一次红牌是从零重新数上来的
        lines.append("ℹ 通道健康台账没落盘（没绑定任务文件夹或写入失败），"
                     "连续零产出轮次只在本轮内存里有效")
    return lines


def health_rows(metrics):
    """→ 报表行（通道健康那张 sheet 的内容），按零产出连续轮次降序、再按家数降序。

    红牌读数**只从 metrics 上取**：那是 ``update_ledger`` 刚在上面标好的那一份。
    以前这里还收一个 ``result`` 参数，函数体里一个字都没读——签名摆着一个「看起来能改判
    红牌」的参数，下一个人会以为传了它就算红牌，不传就不算（而事实恰好相反）。
    """
    rows = []
    for name, m in (metrics or {}).items():
        note = note_from_stats(m)
        if m.get("red_card"):
            note = f"红牌：连续 {m.get('zero_streak', 0)} 轮零产出｜" + note
        rows.append({
            "通道": name,
            "本轮行数": int(m.get("rows") or 0),
            "本轮家数": int(m.get("companies") or 0),
            "有联系方式": int(m.get("with_contact") or 0),
            "填充率%": m.get("fill_rate", 0),
            # 没算分就印「—」：印 0 会被读成「这一路的线索都是 0 分」，
            # 而事实只是「这一轮没勾线索分层」
            "平均线索分": int(m["avg_score"]) if m.get("scored") and m.get("avg_score") is not None else "—",
            "立即跟进": int(m["work_now"]) if m.get("scored") and m.get("work_now") is not None else "—",
            "零产出连续轮次": int(m.get("zero_streak") or 0),
            "红牌": "是" if m.get("red_card") else "",
            "最近一轮": time.strftime("%Y-%m-%d %H:%M:%S"),
            "说明": note,
        })
    rows.sort(key=lambda r: (-int(r["零产出连续轮次"]), -int(r["本轮家数"]), r["通道"]))
    return rows
