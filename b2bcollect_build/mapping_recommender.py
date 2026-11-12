# -*- coding: utf-8 -*-
"""mapping_recommender.py —— 列映射智能推荐（基于历史映射记录）。

大厂 ETL 工具（Informatica / DataWorks）的 schema mapping 推荐思想：
用户上次把"公司名称"列映射为"公司名"，下次遇到相同表头自动推荐同一字段，
减少重复劳动。多文件批量学习：10 个文件里 8 个把第 3 列映射为"电话" →
剩下 2 个也推荐"电话"。

实现：
- 历史映射持久化到 mapping_history.json（{表头名: [(字段, 次数), ...]}），落在
  **持久状态目录**里（口径见 tool_config.state_file，与审计日志同一件事同一份实现）
- recommend(headers) 返回 {列下标: 字段名}，按历史次数降序推荐
- update(headers, mapping) 累加次数，新字段初始为 1
"""
import json
import os
from typing import Dict, List, Optional, Tuple

HISTORY_FILE_NAME = "mapping_history.json"


def _default_cache_file() -> str:
    """历史映射的落点：只问 tool_config.state_file（运行期数据的唯一口径）。

    旧默认值是相对当前工作目录的裸文件名，而打包态的 CWD 由「谁把进程拉起来」决定：
    访达双击 .app 时是 ``/``（Mac 只读转置副本下写不进去），重启链路里是 exe 同级
    （Program Files 同样写不进去），历史上 Windows onefile 下还会落进退出即删的
    _MEI 目录。三种结果都不是「这个用户的历史映射」，而是**永远学不到东西却一句错
    也不报**——列映射推荐一直停在首次使用的空状态，用户只会觉得这功能没用。
    """
    try:
        import tool_config as _tc
        try:
            return str(_tc.state_file(HISTORY_FILE_NAME))
        except Exception:
            return str(_tc.user_state_dir() / HISTORY_FILE_NAME)
    except Exception:
        return os.path.join(os.path.expanduser("~"), ".b2bcollect",
                            "b2bcollect_state", HISTORY_FILE_NAME)


class MappingRecommender:
    """列映射智能推荐器（基于历史映射记录）。"""
    
    def __init__(self, cache_file: Optional[str] = None):
        # 显式传参仍然优先（离线测试与特殊落点要用），默认值改为「问口径」
        self.cache_file = str(cache_file) if cache_file else _default_cache_file()
        self.history = self._load_history()
    
    def _load_history(self) -> Dict[str, List[Tuple[str, int]]]:
        """加载历史映射：{表头名: [(字段名, 次数), ...]}

        读不出来一律退化成「没有历史」，绝不往外抛：这个对象是在「导入数据池」对话框
        构造时创建的（``MappingRecommender()``），让它把界面带崩就是本轮 Mac 崩溃的
        同一个形状——一份半截的、被别的版本写歪的缓存文件，代价不该是整个工具打不开。
        """
        if not os.path.exists(self.cache_file):
            return {}
        try:
            with open(self.cache_file, encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return {}
            out: Dict[str, List[Tuple[str, int]]] = {}
            for header, items in data.items():
                pairs = []
                for item in (items or []):
                    if not isinstance(item, (list, tuple)) or len(item) < 2:
                        continue
                    try:
                        field, count = str(item[0]), int(item[1])
                    except (TypeError, ValueError):
                        continue
                    if field:
                        pairs.append((field, max(0, count)))
                if pairs:
                    out[str(header)] = pairs
            return out
        except (OSError, ValueError, TypeError) as e:
            print(f"⚠ 读取映射历史失败，本次按没有历史处理: {e}")
            return {}
    
    def _save_history(self):
        """保存历史映射到磁盘：写不进去只报一句，绝不带着导入动作一起崩。"""
        try:
            parent = os.path.dirname(self.cache_file)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(self.cache_file, "w", encoding="utf-8") as f:
                json.dump(self.history, f, ensure_ascii=False, indent=2)
        except (OSError, TypeError, ValueError) as e:
            print(f"⚠ 保存映射历史失败: {e}（落点 {self.cache_file}）")
    
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
