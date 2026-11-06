# -*- coding: utf-8 -*-
"""deepseek_expander.py —— 用 DeepSeek API 智能展开行业关键词。

背景：离线知识库（keyword_expander.py）只覆盖已收录行业，未收录的抽象类别
（如"智能制造装备""新能源材料"）无法展开。本模块调用 DeepSeek API，让大模型
把抽象行业词展开成具体的、可在 B2B 平台搜索的产品关键词。

设计取舍：
  · 混合策略：离线知识库优先（快、稳、免费），未命中时调用 DeepSeek API 兜底
  · 降级保护：API 失败/超时/无 key 时自动回退到纯离线模式，不阻断采集
  · 缓存机制：相同输入缓存结果（内存 + 磁盘），避免重复调用浪费额度
  · 质量过滤：对 API 输出做去重、过滤纯服务词、长度校验，防止低质量结果
"""
import os
import json
import time
import hashlib
import requests
from pathlib import Path
from typing import List, Dict, Optional, Tuple

# DeepSeek API 配置
DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"
DEEPSEEK_MODEL = "deepseek-chat"
DEEPSEEK_TIMEOUT = 10  # 秒
DEEPSEEK_MAX_RETRIES = 2

# 配置文件路径（存储 API key）
CONFIG_DIR = Path(__file__).parent
CONFIG_FILE = CONFIG_DIR / "config.json"

# 缓存文件路径（相同输入缓存结果，避免重复调用）
CACHE_FILE = CONFIG_DIR / "deepseek_cache.json"
_cache = {}


def load_api_key() -> Optional[str]:
    """从配置文件加载 DeepSeek API key。
    
    优先级：环境变量 DEEPSEEK_API_KEY > config.json
    返回 None 表示未配置，此时降级到纯离线模式。
    """
    # 1. 环境变量优先（开发/测试用）
    key = os.environ.get("DEEPSEEK_API_KEY")
    if key:
        return key.strip()
    
    # 2. 配置文件
    try:
        if CONFIG_FILE.exists():
            cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            key = cfg.get("deepseek_api_key")
            if key:
                return str(key).strip()
    except Exception:
        pass
    
    return None


def save_api_key(key: str) -> bool:
    """保存 DeepSeek API key 到配置文件。
    
    返回 True 表示保存成功，False 表示失败。
    """
    try:
        key = str(key).strip()
        if not key:
            return False
        
        cfg = {}
        if CONFIG_FILE.exists():
            try:
                cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            except Exception:
                cfg = {}
        
        cfg["deepseek_api_key"] = key
        CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except Exception:
        return False


def _load_cache() -> Dict[str, List[str]]:
    """加载磁盘缓存（相同输入 → 相同输出，避免重复调用 API）。"""
    global _cache
    if _cache:
        return _cache
    
    try:
        if CACHE_FILE.exists():
            _cache = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        _cache = {}
    
    return _cache


def _save_cache():
    """保存缓存到磁盘。"""
    try:
        CACHE_FILE.write_text(json.dumps(_cache, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def _cache_key(text: str) -> str:
    """生成缓存 key（输入文本的 MD5）。"""
    return hashlib.md5(text.strip().encode("utf-8")).hexdigest()


def _build_prompt(user_input: str) -> str:
    """构建 DeepSeek API 的 prompt。
    
    大厂水准 prompt 设计：
      · 角色设定：B2B 行业采购专家（让模型理解场景）
      · 任务明确：把抽象行业词展开成具体产品词
      · 约束清晰：输出格式、数量、质量标准
      · 示例引导：告诉模型什么是"具体产品词"
    """
    return f"""你是一个 B2B 行业采购专家，熟悉各类工业品/设备/材料的采购场景。

用户输入了一个抽象的行业类别或产品领域，请展开成具体的、可在 B2B 平台（如 1688、慧聪网、中国制造网）搜索的产品关键词。

要求：
1. 关键词必须是**具体的产品名称或型号**，不是抽象概念（如"设备""材料""系统"）
2. 关键词必须是 B2B 采购中实际会搜索的词（参考 1688 搜索框联想）
3. 每个关键词单独一行，不要序号、不要解释、不要分类
4. 如果输入已经是具体产品词，直接返回原词
5. 输出 5-20 个关键词，覆盖该领域的主要产品
6. 优先输出高产出词（实际采购中搜索量大、供应商多的词）
7. 不要输出纯服务词（如"咨询""安装""维修""培训"）

示例：
输入：制冷与热泵核心部件
输出：
制冷压缩机
冷凝器
蒸发器
膨胀阀
热泵机组
制冷剂
翅片换热器
气液分离器
工业冷水机

输入：{user_input}

请输出具体的产品关键词："""


def _parse_response(response_text: str) -> List[str]:
    """解析 DeepSeek API 返回的文本，提取关键词列表。
    
    容错处理：
      · 去除序号前缀（1. / 2、/ - ）
      · 去除空行和纯符号行
      · 过滤纯服务词（安装/维修/咨询等）
      · 长度校验（2-20 字符）
    """
    # 纯服务词黑名单（与离线知识库的 DROP_MARKS 对齐）
    SERVICE_WORDS = {"安装", "维修", "咨询", "培训", "调试", "施工", "运维", "维保",
                     "保养", "检测", "认证", "方案", "设计", "服务"}
    
    keywords = []
    for line in response_text.strip().split("\n"):
        line = line.strip()
        
        # 去序号前缀
        line = line.lstrip("0123456789.-、)） ")
        
        # 长度校验
        if len(line) < 2 or len(line) > 20:
            continue
        
        # 过滤纯服务词
        if line in SERVICE_WORDS:
            continue
        
        # 必须包含中文或英文字母（过滤纯符号）
        if not any(c.isalnum() for c in line):
            continue
        
        keywords.append(line)
    
    # 去重保序
    seen = set()
    unique = []
    for kw in keywords:
        if kw not in seen:
            seen.add(kw)
            unique.append(kw)
    
    return unique[:20]  # 上限 20 个


def expand_with_deepseek(user_input: str, api_key: Optional[str] = None) -> Tuple[List[str], str]:
    """用 DeepSeek API 展开关键词。
    
    参数：
      user_input —— 用户输入的抽象行业词（如"智能制造装备"）
      api_key    —— DeepSeek API key（None 时自动从配置加载）
    
    返回：
      (keywords, status) —— 展开的关键词列表 + 状态信息
        status: "success" / "no_api_key" / "api_error" / "timeout" / "cached"
    
    降级策略：
      · 无 API key → 返回空列表 + "no_api_key"
      · API 调用失败/超时 → 返回空列表 + 错误信息
      · 缓存命中 → 返回缓存结果 + "cached"
    """
    # 1. 加载 API key
    if api_key is None:
        api_key = load_api_key()
    
    if not api_key:
        return [], "no_api_key"
    
    # 2. 查缓存
    cache = _load_cache()
    ck = _cache_key(user_input)
    if ck in cache:
        return cache[ck], "cached"
    
    # 3. 调用 API（带重试）
    prompt = _build_prompt(user_input)
    
    for attempt in range(DEEPSEEK_MAX_RETRIES + 1):
        try:
            response = requests.post(
                DEEPSEEK_API_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": DEEPSEEK_MODEL,
                    "messages": [
                        {"role": "user", "content": prompt}
                    ],
                    "temperature": 0.7,  # 适度创造性（太低会保守，太高会发散）
                    "max_tokens": 500    # 限制输出长度
                },
                timeout=DEEPSEEK_TIMEOUT
            )
            
            response.raise_for_status()
            data = response.json()
            
            # 提取返回文本
            content = data["choices"][0]["message"]["content"]
            keywords = _parse_response(content)
            
            if not keywords:
                return [], "empty_response"
            
            # 4. 写缓存
            cache[ck] = keywords
            _save_cache()
            
            return keywords, "success"
        
        except requests.exceptions.Timeout:
            if attempt < DEEPSEEK_MAX_RETRIES:
                time.sleep(0.5)  # 短暂等待后重试
                continue
            return [], "timeout"
        
        except requests.exceptions.RequestException as e:
            if attempt < DEEPSEEK_MAX_RETRIES:
                time.sleep(0.5)
                continue
            return [], f"api_error: {str(e)[:50]}"
        
        except (KeyError, IndexError, json.JSONDecodeError) as e:
            return [], f"parse_error: {str(e)[:50]}"
    
    return [], "max_retries_exceeded"


def clear_cache():
    """清空缓存（用于测试或强制刷新）。"""
    global _cache
    _cache = {}
    try:
        if CACHE_FILE.exists():
            CACHE_FILE.unlink()
    except Exception:
        pass
