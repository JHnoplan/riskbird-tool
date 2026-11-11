# -*- coding: utf-8 -*-
"""feedback_store.py —— 用户反馈持久化 + 搜索加权。

设计思想（参考 Google 搜索反馈 / 淘宝「不感兴趣」机制）：
  · 用户对搜索结果的 👍 / 👎 / 🚫 反馈以 JSONL 追加写本地文件
  · 搜索引擎在评分时读取反馈统计，对高频正反馈公司加权、高频负反馈公司降权
  · 「不感兴趣」的公司直接从结果中移除（类似淘宝/抖音的"不感兴趣"）
  · 反馈数据仅存本地（~/.b2bcollect/feedback.jsonl），不上云

数据结构（每行一条 JSON）：
  {
    "ts": "2026-11-09T10:30:00",
    "query": "光伏",
    "company": "天合光能",
    "type": "up" | "down" | "dislike",
    "reason": "联系方式不对"   // 可选
  }
"""
import json
import os
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple


# ── 反馈类型常量 ─────────────────────────────────────────────────────────────
FEEDBACK_UP = "up"            # 👍 相关 / 有用
FEEDBACK_DOWN = "down"        # 👎 不相关
FEEDBACK_DISLIKE = "dislike"  # 🚫 不感兴趣（从结果中移除）

# ── 加权参数 ─────────────────────────────────────────────────────────────────
_BOOST_THRESHOLD = 3       # 正反馈 >= 3 次才触发加权
_SUPPRESS_THRESHOLD = 2    # 负反馈 >= 2 次才触发降权
_BOOST_FACTOR = 1.2        # 正反馈加权系数（上浮 20%）
_SUPPRESS_FACTOR = 0.7     # 负反馈降权系数（下降 30%）


class FeedbackStore:
    """用户反馈持久化存储与查询。"""

    def __init__(self, data_dir: Optional[str] = None):
        if data_dir:
            self._dir = Path(data_dir)
        else:
            self._dir = Path.home() / ".b2bcollect"
        self._dir.mkdir(parents=True, exist_ok=True)
        self._feedback_file = self._dir / "feedback.jsonl"
        # 内存缓存（首次访问时懒加载）
        self._records: Optional[List[dict]] = None

    # ── 写入 ──────────────────────────────────────────────────────────────

    def record(self, query: str, company: str, feedback_type: str,
               reason: str = ""):
        """追加一条反馈记录。

        Args:
            query: 搜索词
            company: 公司名
            feedback_type: "up" / "down" / "dislike"
            reason: 可选原因说明
        """
        item = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "query": query.strip(),
            "company": company.strip(),
            "type": feedback_type,
        }
        if reason:
            item["reason"] = reason.strip()
        with open(self._feedback_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
        # 失效缓存
        self._records = None

    # ── 读取 ──────────────────────────────────────────────────────────────

    def _load_records(self) -> List[dict]:
        """懒加载全部反馈记录。"""
        if self._records is not None:
            return self._records
        if not self._feedback_file.exists():
            self._records = []
            return self._records
        records = []
        try:
            with open(self._feedback_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            records.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
        except Exception:
            pass
        self._records = records
        return records

    def get_all(self) -> List[dict]:
        """返回全部反馈记录列表。"""
        return list(self._load_records())

    def get_feedback_stats(self) -> Dict[str, int]:
        """返回反馈统计：{total, positive, negative, not_interested}。"""
        records = self._load_records()
        stats = {"total": len(records), "positive": 0, "negative": 0, "not_interested": 0}
        for r in records:
            t = r.get("type", "")
            if t == FEEDBACK_UP:
                stats["positive"] += 1
            elif t == FEEDBACK_DOWN:
                stats["negative"] += 1
            elif t == FEEDBACK_DISLIKE:
                stats["not_interested"] += 1
        return stats

    def get_negative_patterns(self, min_count: int = 2) -> List[Tuple[str, str, int]]:
        """返回高频负反馈模式：[(query, company, count)]。

        用于诊断搜索质量问题（哪些查询总是返回不相关的公司）。
        """
        records = self._load_records()
        counter = Counter()
        for r in records:
            if r.get("type") in (FEEDBACK_DOWN, FEEDBACK_DISLIKE):
                key = (r.get("query", ""), r.get("company", ""))
                counter[key] += 1
        return [(q, c, n) for (q, c), n in counter.most_common()
                if n >= min_count]

    # ── 搜索加权接口 ──────────────────────────────────────────────────────

    def _company_feedback(self, company: str) -> Dict[str, int]:
        """统计某家公司的反馈次数：{up: N, down: N, dislike: N}。"""
        records = self._load_records()
        counts = {"up": 0, "down": 0, "dislike": 0}
        for r in records:
            if r.get("company", "") == company:
                t = r.get("type", "")
                if t in counts:
                    counts[t] += 1
        return counts

    def should_boost(self, company: str) -> bool:
        """该公司是否应获得正反馈加权。"""
        fb = self._company_feedback(company)
        return fb["up"] >= _BOOST_THRESHOLD

    def should_suppress(self, company: str) -> bool:
        """该公司是否应被负反馈降权。"""
        fb = self._company_feedback(company)
        return fb["down"] >= _SUPPRESS_THRESHOLD

    def is_disliked(self, company: str) -> bool:
        """该公司是否被标记为「不感兴趣」（应从结果中移除）。"""
        fb = self._company_feedback(company)
        return fb["dislike"] >= 1

    def apply_feedback_weight(self, company: str, base_score: float) -> float:
        """根据反馈调整评分。

        Returns:
            调整后的分数：
            - 被标记「不感兴趣」→ 返回 0.0（移除）
            - 正反馈多 → 上浮 20%
            - 负反馈多 → 下降 30%
            - 无反馈 → 原分
        """
        if self.is_disliked(company):
            return 0.0
        if self.should_boost(company):
            return base_score * _BOOST_FACTOR
        if self.should_suppress(company):
            return base_score * _SUPPRESS_FACTOR
        return base_score

    # ── 清理 ──────────────────────────────────────────────────────────────

    def clear_all(self):
        """清空全部反馈记录（慎用）。"""
        if self._feedback_file.exists():
            self._feedback_file.unlink()
        self._records = []

    def clear_company(self, company: str):
        """清除某家公司的全部反馈。"""
        records = self._load_records()
        filtered = [r for r in records if r.get("company", "") != company]
        if len(filtered) < len(records):
            self._records = filtered
            # 重写文件
            with open(self._feedback_file, "w", encoding="utf-8") as f:
                for r in filtered:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")


# ── 全局单例（避免每次搜索都重新加载文件）──────────────────────────────────
_store_instance: Optional[FeedbackStore] = None


def get_feedback_store(data_dir: Optional[str] = None) -> FeedbackStore:
    """获取全局 FeedbackStore 单例。"""
    global _store_instance
    if _store_instance is None:
        _store_instance = FeedbackStore(data_dir)
    return _store_instance
