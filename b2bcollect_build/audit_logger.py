# -*- coding: utf-8 -*-
"""audit_logger.py —— 操作审计日志（合规追溯）。

大厂数据治理思想：所有关键操作（导入/搜索/导出/上传）写入审计日志，
满足 GDPR/等保合规要求，可追溯数据泄露风险。

实现：
- JSON Lines 格式（每行一个 JSON 对象，便于追加和分析）
- 日志落在**持久状态目录**里（落点口径唯一，见 tool_config.state_file；旧注释
  「与程序同目录」写的其实是相对当前工作目录，那是缺陷不是设计，见下）
- 记录：时间戳、用户名、操作类型、详情
"""
import json
import os
from datetime import datetime
from typing import Any, Dict, Optional

LOG_FILE_NAME = "audit.log"


def _default_log_file() -> str:
    """审计日志的落点：只问 tool_config.state_file（运行期数据的唯一口径）。

    为什么不能再留 ``"audit.log"`` 这种相对默认值：打包态的当前工作目录由「谁把进程
    拉起来」决定——访达双击 .app 时是 ``/``，重启链路里是 exe 同级，用户手敲时是用户
    站的目录。同一个审计日志会散落到四处，而在 Mac 只读转置副本下写 ``/`` 直接
    PermissionError，被下面的 except 吞掉之后**审计日志就静默没有了**：合规追溯这件事
    没人会去查，缺陷表现为「什么都没发生」。三条退路（首选 / state_dir 出问题 /
    tool_config 整块没加载）没有一条指向当前工作目录。
    """
    try:
        import tool_config as _tc
        try:
            return str(_tc.state_file(LOG_FILE_NAME))
        except Exception:
            return str(_tc.user_state_dir() / LOG_FILE_NAME)
    except Exception:
        return os.path.join(os.path.expanduser("~"), ".b2bcollect",
                            "b2bcollect_state", LOG_FILE_NAME)


class AuditLogger:
    """操作审计日志器。"""
    
    def __init__(self, log_file: Optional[str] = None):
        # 传参仍然优先（离线测试与手工指定要用），默认值改成「问口径」而不是相对路径
        self.log_file = str(log_file) if log_file else _default_log_file()
    
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
            parent = os.path.dirname(self.log_file)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except (OSError, TypeError, ValueError) as e:
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
