# -*- coding: utf-8 -*-
"""recommend_engine.py —— 智能推荐相关公司（只对候选打分，不负责召回）。

职责边界：本模块的三个 find_* 接收的都是**调用方已经召回好的候选子集**。
候选从哪来由 GUI 侧的 RecommendWorker 决定（Gold 视图 FTS5 全文/地域下压 →
磁盘倒排 → 全表），因为"找相似"要的候选本来就是 products 命中的那批记录，
对整张总表逐条分词打分从来不是必需，只是以前没接召回而已。百万级下这点尤其
要命：全表 pass 是几十秒，候选 pass 是毫秒。

设计思想（参考 LinkedIn "你可能认识的人" / 淘宝 "看了又看" / Amazon 协同过滤）：
  · 相似公司：基于主营产品 token 的 Jaccard 相似度 + 行业重叠
  · 供应链推荐：基于 AI 分析的上游/下游关键词搜索（复用 CompanyMatcher）
  · 地域推荐：同省份/同城市 + 联系方式完整度

口径纪律：省级属地一律走 ingest_cleaning.derive_province（与入库分区列、地区
下拉、筛选下压同一份判据）。本模块原先自带一份 28 省私有清单，两处实测差异：
  ① 清单缺宁夏/青海/西藏/港澳台，地址里写了这些省名的公司判不出省份；
  ② 只看 address、不看 country，国外公司（地址写成 "Madrid, Spain" 之类）
     永远归不了地区，derive_province 会用 country 兜底成国别分区键。
判据本身仍是「文本里出现省级名称」：只写城市不写省份的地址两边都判不出，
那种情况下靠同城市比较兜住（实测：宁夏/青海/台北三条只写城市的地址，新旧
实现都返回空串，同省推荐要靠同城或地址里补出省名）。

推荐结果附加 _recommend_type 字段标注推荐原因（写在**副本**上，绝不回写
调用方传入的候选记录——那是总表对象本身，污染一次就永久带脏字段）：
  - "similar"       相似公司
  - "supply_chain"  供应链上下游
  - "regional"      同地区公司
"""
import re
from typing import Dict, List, Optional, Tuple

try:
    from pool_matcher import tokenize, _jaccard, CompanyMatcher, detect_industry
    _HAS_MATCHER = True
except ImportError:
    _HAS_MATCHER = False

# 省级属地的唯一判据（与 pool_store 的 province 分区列同源）
try:
    from ingest_cleaning import derive_province
    _HAS_CLEANING = True
except ImportError:
    _HAS_CLEANING = False

# 退回分支也只引用这一份省级清单（34 个省级单位齐全），绝不另抄
try:
    from data_pool import _CN_PROVINCES as CN_PROVINCES
except ImportError:
    CN_PROVINCES = ()


def _is_anchor(rec: dict, anchor: dict) -> bool:
    """候选是否就是锚点公司本身（推荐必须排除它，但不能误排全部候选）。

    判据纪律：标识为空时**不参与相等判定**。历史数据、未清洗数据、Excel 直接
    导入的数据普遍没有 company_norm；旧写法 `rec.get("company_norm","") ==
    target_norm` 在两边都是空串时恒真 → 把每一条候选都判成"自己"，于是
    「找相似/找同地区」永远返回 0 家，界面上只写一句"未找到相关公司"，
    用户完全看不出这是 bug。与 SQL 的 NULL ≠ NULL 同律。
    """
    if rec is anchor:
        return True
    norm = str((anchor or {}).get("company_norm") or "").strip()
    if not norm:
        return False
    return str((rec or {}).get("company_norm") or "").strip() == norm


class RecommendEngine:
    """智能推荐引擎。"""

    def __init__(self):
        self._matcher = CompanyMatcher() if _HAS_MATCHER else None

    # ── 相似公司推荐 ──────────────────────────────────────────────────────

    def find_similar(self, company_rec: dict, master: List[dict],
                     top_k: int = 10) -> List[dict]:
        """基于主营产品相似度找相似公司。

        策略：
        1. 提取目标公司的产品 token
        2. 对 master 中每家公司算 Jaccard 相似度
        3. 同省份/同城市加权 +10%
        4. 排除目标公司自身
        5. 返回 top_k
        """
        if not company_rec or not master:
            return []

        target_products = company_rec.get("products", "")
        target_tokens = set(tokenize(target_products)) if _HAS_MATCHER else set()
        target_industry = company_rec.get("industry", "")
        target_province = self._extract_province(company_rec)

        if not target_tokens:
            return []

        scored = []
        for rec in master:
            # 排除自身
            if _is_anchor(rec, company_rec):
                continue
            # 产品相似度
            rec_products = rec.get("products", "")
            rec_tokens = set(tokenize(rec_products)) if _HAS_MATCHER else set()
            if not rec_tokens:
                continue
            similarity = _jaccard(list(target_tokens), list(rec_tokens))

            # 行业重叠加权
            rec_industry = rec.get("industry", "")
            if target_industry and rec_industry and target_industry == rec_industry:
                similarity *= 1.3

            # 同省份加权
            rec_province = self._extract_province(rec)
            if target_province and rec_province and target_province == rec_province:
                similarity *= 1.1

            if similarity > 0.1:
                result = dict(rec)
                result["_score"] = round(min(similarity, 1.0), 4)
                result["_recommend_type"] = "similar"
                result["_recommend_reason"] = f"产品相似度 {similarity:.0%}"
                scored.append(result)

        scored.sort(key=lambda x: x.get("_score", 0), reverse=True)
        return scored[:top_k]

    # ── 供应链推荐 ────────────────────────────────────────────────────────

    def find_supply_chain(self, company_rec: dict, master: List[dict],
                          top_k: int = 10) -> List[dict]:
        """基于供应链关系推荐上下游公司。

        策略：
        1. 从目标公司的行业/产品推断关键词
        2. 用 CompanyMatcher 搜索相关公司
        3. 排除目标公司自身
        """
        if not company_rec or not master or not self._matcher:
            return []

        company_name = company_rec.get("company", "")
        products = company_rec.get("products", "")
        industry = company_rec.get("industry", "")

        # 构建搜索关键词
        search_terms = []
        if industry:
            search_terms.append(industry)
        if products:
            search_terms.append(products[:20])
        if not search_terms:
            # 从公司名推断行业
            detected = detect_industry(company_name)
            if detected:
                search_terms.append(detected)

        if not search_terms:
            return []

        # 用第一个关键词搜索
        results = self._matcher.search_local(search_terms[0], master, top_k=top_k + 5)

        # 排除自身并标注
        filtered = []
        for rec in results:
            if _is_anchor(rec, company_rec):
                continue
            # 必须写副本：results 里的元素是总表对象本身，就地打标签会把
            # _recommend_type 永久带进 master，之后的导出/上传全都跟着脏字段
            marked = dict(rec)
            marked["_recommend_type"] = "supply_chain"
            marked["_recommend_reason"] = f"供应链相关（关键词：{search_terms[0]}）"
            filtered.append(marked)
            if len(filtered) >= top_k:
                break

        return filtered

    # ── 地域推荐 ──────────────────────────────────────────────────────────

    def find_regional(self, company_rec: dict, master: List[dict],
                      top_k: int = 10) -> List[dict]:
        """推荐同地区公司。

        策略：
        1. 提取目标公司的省份/城市
        2. 找同地区的其他公司
        3. 按联系方式完整度排序
        """
        if not company_rec or not master:
            return []

        target_address = company_rec.get("address", "")
        target_province = self._extract_province(company_rec)
        target_city = self._extract_city(target_address)

        if not target_province and not target_city:
            return []

        scored = []
        for rec in master:
            if _is_anchor(rec, company_rec):
                continue
            addr = rec.get("address", "")
            rec_province = self._extract_province(rec)
            rec_city = self._extract_city(addr)

            # 同城市 > 同省份
            if target_city and rec_city and target_city == rec_city:
                score = 0.8
            elif target_province and rec_province and target_province == rec_province:
                score = 0.5
            else:
                continue

            # 联系方式完整度加权
            contacts = (len(rec.get("mobiles", [])) + len(rec.get("emails", []))
                       + len(rec.get("wechats", [])) + len(rec.get("phones", [])))
            score += min(contacts * 0.05, 0.2)

            result = dict(rec)
            result["_score"] = round(min(score, 1.0), 4)
            result["_recommend_type"] = "regional"
            region_text = rec_city or rec_province or "同地区"
            result["_recommend_reason"] = f"同地区公司（{region_text}）"
            scored.append(result)

        scored.sort(key=lambda x: x.get("_score", 0), reverse=True)
        return scored[:top_k]

    # ── 辅助方法 ──────────────────────────────────────────────────────────

    @staticmethod
    def _extract_province(rec: dict) -> str:
        """省级属地：一律委托 ingest_cleaning.derive_province（判据唯一出处）。

        core 不可用时退回 _CN_PROVINCES 同一份清单做地址/国家匹配，而不是再抄
        一份清单——历史实现就是自己抄了 28 个省，既缺宁夏/青海/西藏/港澳台，
        也完全不看 country（国外公司因此归不了地区）。
        """
        rec = rec or {}
        if _HAS_CLEANING:
            return derive_province(rec)
        text = "%s %s" % (rec.get("address", "") or "", rec.get("country", "") or "")
        for p in CN_PROVINCES:
            if p in text:
                return p
        return ""

    @staticmethod
    def _extract_city(address: str) -> str:
        """从地址中提取城市（简化版）。"""
        # 匹配 "XX市" 或 "XX区" 前面的城市名
        match = re.search(r"([\u4e00-\u9fff]{2,4})(?:市|地区|自治州)", address)
        if match:
            return match.group(1)
        return ""
