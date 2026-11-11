# -*- coding: utf-8 -*-
"""dedup_engine.py —— 重复判定打分（Entity Resolution 的信号层）。

职责边界，一句话：**本模块只回答"这两条像不像同一家"，不回答"这批里谁和谁该比"**。
  · 判定调度（阻塞键分桶、候选对生成、并查集归组、字段取并集）只在
    pool_merge 一份实现：dedup_exact → merge_duplicates_fast（块内才调本模块打分）。
  · 本模块原先还自带 find_duplicates()（全量两两比较，O(n²)）和 merge_duplicates()
    （第二份合并实现）。两者都已被 pool_merge 取代且生产零调用，但留着就是地雷：
    1)  O(n²) 入口一旦被复用，10 万条就是 50 亿次打分，百万级根本跑不完；
    2)  两份合并实现的字段口径必然漂移——旧那份把来源写成 merged["sources"]，
        与规范字段 source_platforms 分叉，合并后来源信息直接丢失。
    同职责实现只留一份，因此这两个入口连同其私有辅助函数一并删除。

保留的信号（pool_merge 打分依赖，也是阈值语义的唯一出处）：
  1. 公司名模糊匹配（后缀归一 + 包含关系 + SequenceMatcher）
  2. 电话重叠度（号码只留数字后做 Jaccard）
  3. 邮箱重叠度、地址关键词重叠、联系人姓名重叠

设计原则：
  - 高召回：宁可多合并，不能漏掉真正的重复
  - 高精度：合并置信度 > 0.85 才触发
  - 可解释：每次判定都输出信号分数明细
"""
import re
from difflib import SequenceMatcher
from typing import Dict, Set, Tuple


def _tokenize(text: str) -> Set[str]:
    """简单分词：按空格/标点拆分并转小写。"""
    if not text:
        return set()
    text = re.sub(r"[,;，；。、\s]+", " ", text).lower()
    return {t.strip() for t in text.split() if len(t.strip()) >= 2}


def fuzzy_match_company(name1: str, name2: str) -> float:
    """公司名模糊匹配（0-1 分）。

    算法：
      - 先做归一化（去空格/大小写/常见后缀）
      - 完全相同 → 1.0
      - 包含关系 → 0.9
      - 否则用 SequenceMatcher 取相似度
    """
    if not name1 or not name2:
        return 0.0

    # 归一化
    n1 = name1.strip().lower()
    n2 = name2.strip().lower()

    # 去除常见公司后缀
    suffixes = ["有限公司", "股份有限公司", "集团", "inc", "ltd", "llc", "gmbh", "co.", "corp"]
    for s in suffixes:
        n1 = n1.replace(s, "")
        n2 = n2.replace(s, "")

    n1 = n1.strip()
    n2 = n2.strip()

    if not n1 or not n2:
        return 0.0

    # 完全相同
    if n1 == n2:
        return 1.0

    # 包含关系（短的是长的子串）
    if n1 in n2 or n2 in n1:
        return 0.9

    # 序列匹配
    ratio = SequenceMatcher(None, n1, n2).ratio()
    return ratio


def jaccard_similarity(set1: Set[str], set2: Set[str]) -> float:
    """Jaccard 相似系数（集合交并比）。"""
    if not set1 or not set2:
        return 0.0
    intersection = set1 & set2
    union = set1 | set2
    return len(intersection) / len(union) if union else 0.0


def phone_overlap_score(rec1: dict, rec2: dict) -> float:
    """电话号码重叠度（0-1 分）。"""
    phones1 = set(rec1.get("phones", [])) | set(rec1.get("mobiles", [])) | set(rec1.get("landlines", []))
    phones2 = set(rec2.get("phones", [])) | set(rec2.get("mobiles", [])) | set(rec2.get("landlines", []))

    # 归一化：只保留数字（re.sub 对同一值算两遍是白算，先算一次再判长度）
    norm1 = _digit_only(phones1)
    norm2 = _digit_only(phones2)

    if not norm1 or not norm2:
        return 0.0

    return jaccard_similarity(norm1, norm2)


def _digit_only(values) -> Set[str]:
    """号码归一：只留数字且长度 >= 7（过短的号没有判定价值）。"""
    result: Set[str] = set()
    for value in values:
        digits = re.sub(r"\D", "", str(value or ""))
        if len(digits) >= 7:
            result.add(digits)
    return result


def email_overlap_score(rec1: dict, rec2: dict) -> float:
    """邮箱重叠度（0-1 分）。"""
    emails1 = {e.lower() for e in rec1.get("emails", []) if e}
    emails2 = {e.lower() for e in rec2.get("emails", []) if e}

    if not emails1 or not emails2:
        return 0.0

    return jaccard_similarity(emails1, emails2)


def address_similarity(rec1: dict, rec2: dict) -> float:
    """地址相似度（关键词重叠，0-1 分）。"""
    addr1 = rec1.get("address", "")
    addr2 = rec2.get("address", "")

    if not addr1 or not addr2:
        return 0.0

    tokens1 = _tokenize(addr1)
    tokens2 = _tokenize(addr2)

    if not tokens1 or not tokens2:
        return 0.0

    return jaccard_similarity(tokens1, tokens2)


def contact_name_overlap(rec1: dict, rec2: dict) -> float:
    """联系人姓名重叠度（0-1 分）。"""
    names1 = {c.get("name", "").strip().lower() for c in rec1.get("contacts", []) if c.get("name")}
    names2 = {c.get("name", "").strip().lower() for c in rec2.get("contacts", []) if c.get("name")}

    # 过滤空名
    names1 = {n for n in names1 if len(n) >= 2}
    names2 = {n for n in names2 if len(n) >= 2}

    if not names1 or not names2:
        return 0.0

    return jaccard_similarity(names1, names2)


# 各信号权重：阈值语义的唯一出处，pool_merge 的打分口径以此为准
_WEIGHTS: Dict[str, float] = {
    "company": 0.45,      # 公司名最重要
    "phone": 0.25,        # 电话重叠次重要
    "email": 0.15,        # 邮箱重叠
    "address": 0.10,      # 地址相似
    "contact": 0.05,      # 联系人重叠
}


def compute_duplicate_score(rec1: dict, rec2: dict) -> Tuple[float, Dict[str, float]]:
    """计算两条记录的重复判定分数（0-1 分）。

    返回：(总分, {信号名: 分数})
    """
    scores = {
        "company": fuzzy_match_company(rec1.get("company", ""), rec2.get("company", "")),
        "phone": phone_overlap_score(rec1, rec2),
        "email": email_overlap_score(rec1, rec2),
        "address": address_similarity(rec1, rec2),
        "contact": contact_name_overlap(rec1, rec2),
    }

    total = sum(_WEIGHTS[k] * scores[k] for k in _WEIGHTS)
    return total, scores
