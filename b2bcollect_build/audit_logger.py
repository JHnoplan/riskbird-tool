# -*- coding: utf-8 -*-
"""audit_logger.py —— 操作审计日志（合规追溯）。

大厂数据治理思想：所有关键操作（导入/搜索/导出/上传）写入审计日志，
满足 GDPR/等保合规要求，可追溯数据泄露风险。

实现：
- JSON Lines 格式（每行一个 JSON 对象，便于追加和分析）
- 日志文件 audit.log 与程序同目录
- 记录：时间戳、用户名、操作类型、详情
"""
import json
import os
from datetime import datetime
from typing import Any, Dict, Optional


class AuditLogger:
    """操作审计日志器。"""
    
    def __init__(self, log_file: str = "audit.log"):
        self.log_file = log_file
    
    def log(self, action: str, user: str, details: Dict[str, Any]):
        """记录操作日志。
        
        Args:
            action: 操作类型，如 "import" / "search" / "export" / "upload"
            user: 用户名
            details: 操作详情，如 {"file_count": 3, "record_count": 100}
        """
        entry = {
            "timestamp": datetime.now().isoformat(),
            "user": user,
            "action": action,
            "details": details,
        }
        try:
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError as e:
            print(f"⚠ 写入审计日志失败: {e}")
    
    def get_recent(self, n: int = 10) -> list:
        """读取最近 N 条日志。"""
        if not os.path.exists(self.log_file):
            return []
        try:
            with open(self.log_file, encoding="utf-8") as f:
                lines = f.readlines()
            return [json.loads(line) for line in lines[-n:] if line.strip()]
        except (OSError, json.JSONDecodeError) as e:
            print(f"⚠ 读取审计日志失败: {e}")
            return []


# 全局单例（方便各模块调用）
_audit_logger: Optional[AuditLogger] = None


def get_audit_logger() -> AuditLogger:
    """获取全局审计日志器单例。"""
    global _audit_logger
    if _audit_logger is None:
        _audit_logger = AuditLogger()
    return _audit_logger
