# -*- coding: utf-8 -*-
"""search_suggest.py —— 搜索建议与自动补全。

设计思想（参考 Google Search Suggest / 百度搜索框 / 淘宝搜索下拉）：
  · 搜索历史：本地 JSON 持久化，记录最近 50 条搜索词（带时间戳和次数）
  · 热门搜索：从团队搜索历史中统计高频词（本地模拟，不上云）
  · 前缀匹配：用户输入 ≥ 2 字符时，从公司名/产品名中匹配前缀
  · 三路合并：历史匹配 + 数据池前缀匹配 + 行业同义词推荐

数据结构（search_history.json）：
  [
    {"query": "光伏", "ts": "2026-11-09T10:30:00", "count": 3},
    {"query": "卫浴", "ts": "2026-11-09T09:15:00", "count": 1},
    ...
  ]
"""
import json
import os
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple


# ── 常量 ─────────────────────────────────────────────────────────────────────
_MAX_HISTORY = 50          # 最多保留 50 条搜索历史
_MIN_PREFIX_LEN = 2        # 最少输入 2 个字符才触发建议
_MAX_SUGGESTIONS = 8       # 下拉列表最多展示 8 条


def _is_unmaterialized_proxy(master) -> bool:
    """master 是不是「还没读盘的总表代理」（data_pool.LazyMaster）。

    为什么判据住在这里而不是调用方：调用方（SuggestLineEdit）在懒态下递的是
    空表，但那只是当前调用方的自觉。取代理的任何一个元素都会把整张总表
    （73.8 万条）读回来，而搜索建议只是锦上添花——这种不对等的代价必须由
    真正会付钱的那一行拦住（与 get_record_by_norm / find_for_tombstone 同一个
    口径）。普通 list 拿不到 materialized → 返回假，既有行为一行不改。
    """
    return getattr(master, "materialized", True) is False


class SearchSuggester:
    """搜索建议引擎：历史 + 热门 + 前缀匹配。"""

    def __init__(self, data_dir: Optional[str] = None):
        if data_dir:
            self._dir = Path(data_dir)
        else:
            self._dir = Path.home() / ".b2bcollect"
        self._dir.mkdir(parents=True, exist_ok=True)
        self._history_file = self._dir / "search_history.json"
        self._history: Optional[List[dict]] = None  # 懒加载

    # ── 历史记录 ──────────────────────────────────────────────────────────

    def _load_history(self) -> List[dict]:
        """懒加载搜索历史。"""
        if self._history is not None:
            return self._history
        if not self._history_file.exists():
            self._history = []
            return self._history
        try:
            with open(self._history_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._history = data if isinstance(data, list) else []
        except Exception:
            self._history = []
        return self._history

    def _save_history(self):
        """持久化搜索历史。"""
        try:
            with open(self._history_file, "w", encoding="utf-8") as f:
                json.dump(self._history, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def add_history(self, query: str):
        """记录一次搜索（去重 + 更新次数 + 更新时间）。"""
        query = query.strip()
        if not query:
            return
        history = self._load_history()
        now_iso = datetime.now().isoformat(timespec="seconds")
        # 查找是否已存在
        for item in history:
            if item.get("query") == query:
                item["ts"] = now_iso
                item["count"] = item.get("count", 1) + 1
                # 移到最前面
                history.remove(item)
                history.insert(0, item)
                self._save_history()
                return
        # 新增
        history.insert(0, {"query": query, "ts": now_iso, "count": 1})
        # 截断到 _MAX_HISTORY
        if len(history) > _MAX_HISTORY:
            history = history[:_MAX_HISTORY]
        self._history = history
        self._save_history()

    def get_history(self, limit: int = 10) -> List[Tuple[str, str, int]]:
        """返回最近 N 条搜索历史：[(query, timestamp, count)]。"""
        history = self._load_history()
        result = []
        for item in history[:limit]:
            result.append((
                item.get("query", ""),
                item.get("ts", ""),
                item.get("count", 1),
            ))
        return result

    def get_popular(self, top_n: int = 5) -> List[Tuple[str, int]]:
        """返回热门搜索词（按搜索次数排序）：[(query, count)]。"""
        history = self._load_history()
        counter = Counter()
        for item in history:
            counter[item.get("query", "")] += item.get("count", 1)
        return counter.most_common(top_n)

    def clear_history(self):
        """清空搜索历史。"""
        self._history = []
        self._save_history()

    # ── 搜索建议 ──────────────────────────────────────────────────────────

    def get_suggestions(self, prefix: str, master: List[dict],
                        view_suggest=None) -> List[dict]:
        """根据输入前缀返回搜索建议列表。

        Args:
            prefix: 用户已经输入的内容
            master: 内存总表（只在视图召回不可用时才用，见下）
            view_suggest: ``callable(prefix, limit) -> {"company": [...], "product": [...]}``，
                由数据池的 Gold 物化视图提供。传了它就不碰 master 一个元素。

        为什么召回必须交给视图（2026-11-18 重写）：旧实现是 `for rec in master[:5000]`，
        两个问题都硬：
          · 只对落盘顺序前 5000 条提建议——实测 73.8 万条的池子里搜「华为」，
            旧口径一条建议都提不出来（华为在池子后段），新口径 8 条；
          · 为了补几个字要把总表整体读进内存（736,263 条实测 6.2GB 常驻），
            这就是「打开搜索框加载很久很久」的真正源头。
        包含性判定（`prefix in text`）两边共用同一条，索引只负责把候选集从
        百万级缩到几十条，绝不自己换一个口径挑数据。

        Returns:
            [{"text": "光伏组件", "type": "history|company|product|synonym"}, ...]
            最多返回 _MAX_SUGGESTIONS 条。
        """
        prefix = prefix.strip()
        if len(prefix) < _MIN_PREFIX_LEN:
            # 前缀太短：只返回搜索历史
            return [{"text": q, "type": "history"}
                    for q, _, _ in self.get_history(limit=_MAX_SUGGESTIONS)]

        suggestions = []
        seen = set()
        prefix_lower = prefix.lower()

        # 1. 历史匹配（优先级最高）
        for item in self._load_history():
            q = item.get("query", "")
            if prefix_lower in q.lower() and q not in seen:
                suggestions.append({"text": q, "type": "history"})
                seen.add(q)
                if len(suggestions) >= _MAX_SUGGESTIONS:
                    return suggestions

        # 2. 公司名/产品名前缀匹配：视图召回优先，视图不可用才退回旧的全表前 5000 条
        company_matches = []
        product_matches = []
        recall = None
        if view_suggest is not None:
            try:
                recall = view_suggest(prefix, _MAX_SUGGESTIONS * 2)
            except Exception:
                recall = None      # 召回接口坏了才允许退回总表路径
        if recall is not None:
            for name in recall.get("company") or []:
                name = str(name or "").strip()
                if name and prefix_lower in name.lower() and name not in seen:
                    company_matches.append(name)
                    seen.add(name)
            for prod in recall.get("product") or []:
                prod = str(prod or "").strip()[:30]
                if prod and prefix_lower in prod.lower() and prod not in seen:
                    product_matches.append(prod)
                    seen.add(prod)
        elif master and not _is_unmaterialized_proxy(master):
            # 视图召回不可用才退回总表前 5000 条；懒代理在这里被直接排除（见上）
            for rec in master[:5000]:  # 限制扫描范围
                company = rec.get("company", "")
                products = rec.get("products", "")
                if prefix_lower in company.lower() and company not in seen:
                    company_matches.append(company)
                    seen.add(company)
                if prefix_lower in products.lower() and products[:30] not in seen:
                    product_matches.append(products[:30])
                    seen.add(products[:30])
                if len(suggestions) + len(company_matches) + len(product_matches) >= _MAX_SUGGESTIONS * 2:
                    break
        # 公司名优先于产品名
        for c in company_matches[:4]:
            suggestions.append({"text": c, "type": "company"})
            if len(suggestions) >= _MAX_SUGGESTIONS:
                return suggestions
        for p in product_matches[:3]:
            suggestions.append({"text": p, "type": "product"})
            if len(suggestions) >= _MAX_SUGGESTIONS:
                return suggestions

        # 3. 行业同义词推荐（从 pool_matcher 导入）
        try:
            from pool_matcher import INDUSTRY_SYNONYMS, GEOGRAPHY_SYNONYMS
            for industry, synonyms in INDUSTRY_SYNONYMS.items():
                if prefix_lower in industry.lower():
                    for s in synonyms[:2]:
                        if s not in seen:
                            suggestions.append({"text": s, "type": "synonym"})
                            seen.add(s)
                            if len(suggestions) >= _MAX_SUGGESTIONS:
                                return suggestions
                for s in synonyms:
                    if prefix_lower in s.lower() and s not in seen:
                        suggestions.append({"text": industry, "type": "synonym"})
                        seen.add(industry)
                        if len(suggestions) >= _MAX_SUGGESTIONS:
                            return suggestions
        except ImportError:
            pass

        return suggestions[:_MAX_SUGGESTIONS]


# ── 全局单例 ─────────────────────────────────────────────────────────────────
_suggester_instance: Optional[SearchSuggester] = None


def get_search_suggester(data_dir: Optional[str] = None) -> SearchSuggester:
    """获取全局 SearchSuggester 单例。"""
    global _suggester_instance
    if _suggester_instance is None:
        _suggester_instance = SearchSuggester(data_dir)
    return _suggester_instance
