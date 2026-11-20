# -*- coding: utf-8 -*-
"""
话术模板引擎
============

用户在 GUI 编辑器里写固定话术模板，其中占位符按每条数据（Excel 行）动态替换。
占位符直接用 Excel 列名，最直观，例如：

    {姓名}先生你好，我是鸿威集团的{昵称}，我了解到您对{兴趣点}感兴趣。

- 使用 string.Formatter 做具名字段替换，未知字段保留原样（不抛异常）。
- 提供变量抽取（供 GUI 展示可插入的列名按钮）与实时预览。
"""

from __future__ import annotations

import re
from string import Formatter
from typing import Dict, List, Optional

_FMT = Formatter()
# 仅识别简单具名字段：{name} / {name:fmt}，不支持点号与下标（列名一般不含）
_FIELD_RE = re.compile(r"\{([^{}:]+)\}")


def extract_fields(template: str) -> List[str]:
    """抽取模板里的占位符字段名（去重、保序）。"""
    seen: List[str] = []
    for _, field, _, _ in _FMT.parse(template or ""):
        if field and field not in seen:
            seen.append(field)
    return seen


class _SafeDict(dict):
    """缺失键不报错，回填成 {key} 原样，避免整条渲染失败。"""
    def __missing__(self, key):  # noqa
        return "{" + key + "}"


def render(template: str, mapping: Dict[str, str],
           overrides: Optional[Dict[str, str]] = None) -> str:
    """用 mapping（列名->值）渲染模板。overrides 优先级更高（如自定义昵称/兴趣点）。

    任何异常都退回安全渲染，保证工具不因单条数据崩掉。
    """
    data = _SafeDict()
    for k, v in (mapping or {}).items():
        data[str(k)] = "" if v is None else str(v)
    for k, v in (overrides or {}).items():
        if v is not None and str(v) != "":
            data[str(k)] = str(v)
    try:
        return (template or "").format_map(data)
    except Exception:
        # 极端情况（含裸 {} 等）：逐个正则替换
        out = template or ""
        for k, v in data.items():
            out = out.replace("{" + k + "}", v)
        return out


def preview(template: str, sample: Dict[str, str]) -> str:
    """给 GUI 实时预览用：用样例行数据渲染一次。"""
    return render(template, sample)
