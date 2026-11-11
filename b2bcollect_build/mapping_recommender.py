# -*- coding: utf-8 -*-
"""mapping_recommender.py —— 列映射智能推荐（基于历史映射记录）。

大厂 ETL 工具（Informatica / DataWorks）的 schema mapping 推荐思想：
用户上次把"公司名称"列映射为"公司名"，下次遇到相同表头自动推荐同一字段，
减少重复劳动。多文件批量学习：10 个文件里 8 个把第 3 列映射为"电话" →
剩下 2 个也推荐"电话"。

实现：
- 历史映射持久化到 mapping_history.json（{表头名: [(字段, 次数), ...]}）
- recommend(headers) 返回 {列下标: 字段名}，按历史次数降序推荐
- update(headers, mapping) 累加次数，新字段初始为 1
"""
import json
import os
from typing import Dict, List, Optional, Tuple


class MappingRecommender:
    """列映射智能推荐器（基于历史映射记录）。"""
    
    def __init__(self, cache_file: str = "mapping_history.json"):
        self.cache_file = cache_file
        self.history = self._load_history()
    
    def _load_history(self) -> Dict[str, List[Tuple[str, int]]]:
        """加载历史映射：{表头名: [(字段名, 次数), ...]}"""
        if os.path.exists(self.cache_file):
            try:
                data = json.load(open(self.cache_file, encoding="utf-8"))
                # 兼容旧格式（如果存在）
                return {k: [(f, c) for f, c in v] for k, v in data.items()}
            except (json.JSONDecodeError, ValueError):
                return {}
        return {}
    
    def _save_history(self):
        """保存历史映射到磁盘"""
        try:
            json.dump(self.history, open(self.cache_file, "w", encoding="utf-8"),
                     ensure_ascii=False, indent=2)
        except OSError as e:
            print(f"⚠ 保存映射历史失败: {e}")
    
    def recommend(self, headers: List[str]) -> Dict[int, str]:
        """根据历史推荐映射：{列下标: 字段名}
        
        Args:
            headers: 表头列表，如 ["公司名称", "手机号", "邮箱"]
        
        Returns:
            推荐映射，如 {0: "公司名", 1: "电话", 2: "邮箱"}
            未推荐的列不在返回字典中
        """
        recommendations = {}
        for col_idx, header in enumerate(headers):
            if not header:  # 空表头不推荐
                continue
            if header in self.history:
                # 取历史次数最多的字段
                top_field = max(self.history[header], key=lambda x: x[1])[0]
                recommendations[col_idx] = top_field
        return recommendations
    
    def update(self, headers: List[str], mapping: Dict[int, str]):
        """更新历史映射（累加次数）
        
        Args:
            headers: 表头列表
            mapping: 用户确认的映射，如 {0: "公司名", 1: "电话"}
        """
        for col_idx, field in mapping.items():
            if col_idx >= len(headers):
                continue
            header = headers[col_idx]
            if not header or not field:  # 空表头或空字段不记录
                continue
            
            if header not in self.history:
                self.history[header] = []
            
            # 累加次数
            found = False
            for i, (f, count) in enumerate(self.history[header]):
                if f == field:
                    self.history[header][i] = (f, count + 1)
                    found = True
                    break
            
            if not found:
                self.history[header].append((field, 1))
        
        self._save_history()
    
    def get_stats(self) -> Dict[str, int]:
        """返回统计信息：{表头名: 总映射次数}"""
        return {header: sum(c for _, c in fields)
                for header, fields in self.history.items()}
