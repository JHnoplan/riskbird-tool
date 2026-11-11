# -*- coding: utf-8 -*-
"""test_pool_offline.py —— 数据池模块离线测试（不需要网络/OSS）。

覆盖：
  1. 公司名规范化（中英文后缀去除、循环去后缀）
  2. 分词器（中文 bigram、英文词、数字段、停用词过滤）
  3. 本地模糊搜索评分（公司名匹配 > 主营匹配 > 联系方式完整度 > 新鲜度）
  4. 合并去重（同 company_norm 合并联系方式取并集）
  5. normalize_row（18 列行格式 → 标准记录格式）

运行：cd b2bcollect_build && python test_pool_offline.py
"""
import sys
import os

# 确保能 import 同目录模块
sys.path.insert(0, os.path.dirname(__file__))

from data_pool import (
    normalize_company_name, DataPoolManager,
    _is_valid_record, _filter_invalid_records,
)
from pool_matcher import tokenize, CompanyMatcher

# ── 测试辅助 ──────────────────────────────────────────────────────────────────

_passed = 0
_failed = 0


def _assert(condition: bool, label: str):
    global _passed, _failed
    if condition:
        _passed += 1
        print(f"  ✅ {label}")
    else:
        _failed += 1
        print(f"  ❌ {label}")


def _assert_eq(actual, expected, label: str):
    _assert(actual == expected, f"{label}  (期望={expected!r}, 实际={actual!r})")


# ── 1. 公司名规范化 ──────────────────────────────────────────────────────────

def test_normalize_company_name():
    print("\n═══ 1. 公司名规范化 ═══")

    # 中文后缀
    _assert_eq(normalize_company_name("常州天合光能有限公司"), "常州天合光能", "去「有限公司」")
    _assert_eq(normalize_company_name("华为技术有限公司"), "华为技术", "去「技术有限公司」")
    _assert_eq(normalize_company_name("正泰集团股份有限公司"), "正泰", "循环去「集团股份有限公司」→「集团」")
    _assert_eq(normalize_company_name("中建投有限责任公司"), "中建投", "去「有限责任公司」")

    # 英文后缀
    _assert_eq(normalize_company_name("Trina Solar Co., Ltd."), "trina solar", "去「Co., Ltd.」")
    _assert_eq(normalize_company_name("Apple Inc."), "apple", "去「Inc.」")
    _assert_eq(normalize_company_name("Siemens AG"), "siemens ag", "AG 不在列表中（保留）")
    _assert_eq(normalize_company_name("Samsung Electronics LLC"), "samsung electronics", "去「LLC」")
    _assert_eq(normalize_company_name("BASF SE"), "basf se", "SE 不在列表中（保留）")
    _assert_eq(normalize_company_name("Total Energies SA"), "total energies sa", "SA 不带点不匹配（保留）")
    _assert_eq(normalize_company_name("Total Energies S.A."), "total energies", "去「S.A.」（带点匹配）")

    # 混合
    _assert_eq(normalize_company_name("  常州天合光能有限公司  "), "常州天合光能", "去空白+后缀")
    _assert_eq(normalize_company_name("ABC Trading Corp"), "abc trading", "去「Corp」")

    # 空值
    _assert_eq(normalize_company_name(""), "", "空字符串")
    _assert_eq(normalize_company_name("   "), "", "纯空白")


# ── 2. 分词器 ────────────────────────────────────────────────────────────────

def test_tokenize():
    print("\n═══ 2. 分词器 ═══")

    # 中文 bigram
    tokens = tokenize("光伏组件")
    _assert("光伏" in tokens, f"中文 bigram: '光伏' in {tokens}")
    _assert("组件" in tokens, f"中文 bigram: '组件' in {tokens}")

    # 英文单词
    tokens = tokenize("Solar Panel")
    _assert("solar" in tokens, f"英文词: 'solar' in {tokens}")
    _assert("panel" in tokens, f"英文词: 'panel' in {tokens}")

    # 数字段
    tokens = tokenize("13800138000")
    _assert("13800138000" in tokens, f"数字段: '13800138000' in {tokens}")

    # 停用词过滤
    tokens = tokenize("的天合光能公司")
    _assert("的" not in tokens, f"停用词 '的' 被过滤: {tokens}")
    _assert("公司" not in tokens, f"停用词 '公司' 被过滤: {tokens}")
    _assert("天合" in tokens or "天合光能" in tokens, f"有效词保留: {tokens}")

    # 空值
    _assert_eq(tokenize(""), [], "空字符串返回空列表")
    _assert_eq(tokenize(None), [], "None 返回空列表")


# ── 3. 本地模糊搜索 ──────────────────────────────────────────────────────────

def test_search_local():
    print("\n═══ 3. 本地模糊搜索 ═══")

    master = [
        {
            "company": "常州天合光能有限公司",
            "company_norm": "常州天合光能",
            "products": "光伏组件/逆变器",
            "phones": ["0519-85118888"],
            "mobiles": ["13800138000"],
            "emails": ["sales@trinasolar.com"],
            "wechats": ["zhangsan123"],
            "source_time": "2026-11-06T14:30:00",
        },
        {
            "company": "阳光电源股份有限公司",
            "company_norm": "阳光电源",
            "products": "储能逆变器/光伏系统",
            "phones": [],
            "mobiles": ["13900139000"],
            "emails": ["info@sungrow.com"],
            "wechats": [],
            "source_time": "2026-10-01T10:00:00",
        },
        {
            "company": "宁波锦浪新能源技术有限公司",
            "company_norm": "宁波锦浪新能源",
            "products": "组串式逆变器",
            "phones": ["0574-88888888"],
            "mobiles": [],
            "emails": [],
            "wechats": [],
            "source_time": "2025-06-01T08:00:00",
        },
        {
            "company": "ABC Trading Co., Ltd.",
            "company_norm": "abc trading",
            "products": "electrical equipment",
            "phones": [],
            "mobiles": [],
            "emails": ["abc@trading.com"],
            "wechats": [],
            "source_time": "2026-09-15T12:00:00",
        },
    ]

    matcher = CompanyMatcher(deepseek_api_key="")  # 不用 DeepSeek

    # 搜索「光伏」→ 天合光能应排第一（公司名+产品都含光伏）
    results = matcher.search_local("光伏", master, top_k=5)
    _assert(len(results) > 0, f"搜索'光伏'有结果: {len(results)} 条")
    if results:
        _assert("天合" in results[0]["company"] or "光伏" in results[0].get("products", ""),
                f"搜索'光伏'首条={results[0]['company']}")

    # 搜索「逆变器」→ 阳光电源/锦浪都应出现
    results = matcher.search_local("逆变器", master, top_k=10)
    companies = [r["company"] for r in results]
    _assert(any("阳光" in c for c in companies), f"搜索'逆变器'包含阳光电源: {companies}")
    _assert(any("锦浪" in c for c in companies), f"搜索'逆变器'包含锦浪: {companies}")

    # 搜索「天合」→ 天合光能应排第一
    results = matcher.search_local("天合", master, top_k=5)
    _assert(len(results) > 0 and "天合" in results[0]["company"],
            f"搜索'天合'首条={results[0]['company'] if results else '无'}")

    # 空搜索
    _assert_eq(matcher.search_local("", master), [], "空查询返回空列表")
    _assert_eq(matcher.search_local("光伏", []), [], "空 master 返回空列表")

    # 评分字段
    results = matcher.search_local("光伏", master, top_k=5)
    if results:
        _assert("_score" in results[0], f"结果含 _score 字段: {results[0].get('_score')}")
        _assert("_score_detail" in results[0], f"结果含 _score_detail 字段")


# ── 4. 合并去重 ──────────────────────────────────────────────────────────────

def test_merge_records():
    print("\n═══ 4. 合并去重 ═══")

    records = [
        {
            "company": "常州天合光能有限公司",
            "company_norm": "常州天合光能",
            "contacts": [{"name": "张经理", "title": "销售部"}],
            "phones": ["0519-85118888"],
            "mobiles": ["13800138000"],
            "emails": ["sales@trinasolar.com"],
            "wechats": ["zhangsan123"],
            "address": "常州市新北区",
            "products": "光伏组件",
            "source": "中国制造网",
            "source_user": "userA",
            "source_time": "2026-11-06T14:30:00",
            "country": "CN",
            "is_domestic": True,
        },
        {
            "company": "天合光能有限公司",
            "company_norm": "常州天合光能",  # 同一 company_norm
            "contacts": [{"name": "李总", "title": "采购部"}],
            "phones": ["0519-85119999"],  # 不同电话
            "mobiles": ["13800138000"],   # 相同手机
            "emails": ["info@trinasolar.com"],  # 不同邮箱
            "wechats": ["zhangsan123"],   # 相同微信
            "address": "常州市",
            "products": "光伏组件/逆变器",
            "source": "八方资源网",
            "source_user": "userB",
            "source_time": "2026-11-07T09:30:00",
            "country": "CN",
            "is_domestic": True,
        },
    ]

    merged = DataPoolManager._merge_records(records)
    _assert_eq(len(merged), 1, "同一 company_norm 合并为 1 条")

    if len(merged) == 1:
        m = merged[0]
        # 电话取并集
        _assert(len(m["phones"]) >= 2, f"电话取并集: {m['phones']}")
        # 手机去重
        _assert_eq(len(m["mobiles"]), 1, f"手机去重: {m['mobiles']}")
        # 邮箱取并集
        _assert(len(m["emails"]) >= 2, f"邮箱取并集: {m['emails']}")
        # 微信去重
        _assert_eq(len(m["wechats"]), 1, f"微信去重: {m['wechats']}")
        # 联系人取并集
        _assert_eq(len(m["contacts"]), 2, f"联系人合并: {[c['name'] for c in m['contacts']]}")
        # source 拼接
        _assert("中国制造网" in m["source"] and "八方资源网" in m["source"],
                f"source 拼接: {m['source']}")
        # source_time 取最新
        _assert_eq(m["source_time"], "2026-11-07T09:30:00", "source_time 取最新")

    # 不同 company_norm 不合并
    records2 = records + [{
        "company": "华为技术",
        "company_norm": "华为技术",
        "contacts": [], "phones": ["0755-28780808"], "mobiles": [], "landlines": ["0755-28780808"],
        "emails": [], "wechats": [],
        "address": "", "products": "", "source": "", "source_user": "",
        "source_time": "", "country": "", "is_domestic": True,
        "website": "", "industry": "",
    }]
    merged2 = DataPoolManager._merge_records(records2)
    _assert_eq(len(merged2), 2, "不同 company_norm 不合并")


# ── 5. normalize_row ─────────────────────────────────────────────────────────

def test_normalize_row():
    print("\n═══ 5. normalize_row ═══")

    # 国内 9 列
    row_domestic = {
        "客户名称": "常州天合光能有限公司",
        "手机号": "13800138000",
        "固话": "0519-85118888",
        "邮箱": "sales@trinasolar.com",
        "微信号": "zhangsan123",
        "岗位": "张经理",
        "数据来源": "中国制造网",
        "备注": "常州市新北区",
    }
    rec = DataPoolManager.normalize_row(row_domestic)
    _assert(rec is not None, "国内行转换成功")
    if rec:
        _assert_eq(rec["company"], "常州天合光能有限公司", "公司名")
        _assert_eq(rec["company_norm"], "常州天合光能", "公司名规范化")
        _assert("13800138000" in rec["mobiles"], f"手机号: {rec['mobiles']}")
        _assert("sales@trinasolar.com" in rec["emails"], f"邮箱: {rec['emails']}")
        _assert(rec["is_domestic"], "国内标记")

    # 国外 9 列（带 .1 后缀）
    row_foreign = {
        "客户名称.1": "Trina Solar Co., Ltd.",
        "手机号.1": "+1-555-0123",
        "固话.1": "",
        "邮箱.1": "info@trinasolar.com",
        "微信号.1": "",
        "岗位.1": "John",
        "数据来源.1": "Made-in-China",
    }
    rec2 = DataPoolManager.normalize_row(row_foreign)
    _assert(rec2 is not None, "国外行转换成功")
    if rec2:
        _assert_eq(rec2["company"], "Trina Solar Co., Ltd.", "国外公司名")
        _assert(not rec2["is_domestic"], "国外标记")

    # 空行
    rec3 = DataPoolManager.normalize_row({})
    _assert(rec3 is None, "空行返回 None")


# ── 6. 数据来源清洗（Data Lineage 清洁度） ─────────────────────────────────────

def test_clean_source_name():
    """测试数据来源清洗函数（大厂数据溯源清洁度标准）。"""
    print("\n═══ 6. 数据来源清洗 ═══")

    clean = DataPoolManager._clean_source_name

    # 内部编号前缀 + 域名 + 人名
    _assert_eq(clean("ZHY20260721CJb2b.bjx.com.cn黄芷茵.xlsx"), "b2b.bjx.com.cn", "去 ZHY 前缀+人名")
    _assert_eq(clean("ZHY20260729CJca800.com蔡超.xlsx"), "ca800.com", "去 ZHY 前缀+人名（ca800）")
    _assert_eq(clean("ZHY20260729CJinfo.gongkong.com蔡超.xlsx"), "info.gongkong.com", "去 ZHY 前缀（子域名）")

    # 日期下划线前缀
    _assert_eq(clean("20261109_1688.com_张三.xlsx"), "1688.com", "去日期前缀")

    # 已经是清洁的来源（不应改动）
    _assert_eq(clean("1688.com"), "1688.com", "清洁域名保持不变")
    _assert_eq(clean("b2b.bjx.com.cn"), "b2b.bjx.com.cn", "清洁域名保持不变（.com.cn）")
    _assert_eq(clean("Made-in-China"), "Made-in-China", "清洁平台名保持不变")

    # 空输入
    _assert_eq(clean(""), "", "空字符串返回空")


# ── 7. URL 域名提取（智能数据溯源） ──────────────────────────────────────────

def test_extract_domain_from_url():
    """测试从 URL 提取主域名函数（大厂 Data Provenance 标准）。"""
    print("\n═══ 7. URL 域名提取 ═══")

    extract = DataPoolManager._extract_domain_from_url

    # B2B 平台模式：{任意子域名}.cn.{平台}.{tld}
    _assert_eq(
        extract("https://hjgcadmin.cn.b2b168.com/"),
        "b2b168.com",
        "B2B 平台子域名（八方资源网）"
    )
    _assert_eq(
        extract("https://wangminhong.cn.made-in-china.com/"),
        "made-in-china.com",
        "B2B 平台子域名（中国制造网）"
    )
    _assert_eq(
        extract("http://testuser.cn.b2b168.com"),
        "b2b168.com",
        "B2B 平台子域名（无路径）"
    )

    # 中国 ccTLD 模式：xxx.com.cn
    _assert_eq(
        extract("https://b2b.bjx.com.cn"),
        "b2b.bjx.com.cn",
        "中国 ccTLD（b2b.bjx.com.cn 保持不变）"
    )
    _assert_eq(
        extract("https://example.com.cn/path/to/page"),
        "example.com.cn",
        "中国 ccTLD（去路径）"
    )
    _assert_eq(
        extract("https://www.example.com.cn"),
        "example.com.cn",
        "中国 ccTLD（去 www 前缀）"
    )

    # 常见子域名前缀
    _assert_eq(
        extract("https://www.made-in-china.com/product/xxx"),
        "made-in-china.com",
        "去 www 前缀"
    )
    _assert_eq(
        extract("cn.made-in-china.com"),
        "made-in-china.com",
        "去 cn 前缀"
    )

    # 普通域名
    _assert_eq(extract("http://1688.com"), "1688.com", "裸域名（无子域名）")
    _assert_eq(extract("https://www.google.com"), "google.com", "去 www 前缀（google）")

    # Case 1 bug fix: 中间 .cn. 标记优先于结尾 .com.cn
    _assert_eq(
        extract("https://test.cn.b2b.bjx.com.cn"),
        "b2b.bjx.com.cn",
        "中间 .cn. 标记优先于结尾 .com.cn"
    )

    # 边界情况
    _assert_eq(extract(""), "", "空字符串")
    _assert_eq(extract("  https://b2b.bjx.com.cn  "), "b2b.bjx.com.cn", "带空格的 URL")


# ── 8. 统一来源清洗（_resolve_source 三条路径） ─────────────────────────────

def test_resolve_source():
    """测试统一数据来源清洗函数（大厂 Data Provenance 标准）。"""
    print("\n═══ 8. 统一来源清洗 ═══")

    resolve = DataPoolManager._resolve_source

    # Priority 1: 数据自带的「数据来源」字段
    _assert_eq(
        resolve("八方资源网", "https://example.com", "fallback.xlsx"),
        "八方资源网",
        "Priority 1: 清洁来源保持不变"
    )
    _assert_eq(
        resolve("ZHY20260721CJb2b.bjx.com.cn黄芷茵.xlsx", "", ""),
        "b2b.bjx.com.cn",
        "Priority 1: 脏来源文件名清洗为域名"
    )
    _assert_eq(
        resolve("A.xlsx / B.xlsx", "", ""),
        "A / B",
        "Priority 1: 多来源拼接清洗"
    )

    # Priority 2: 从「官网」列提取域名
    _assert_eq(
        resolve("", "https://hjgcadmin.cn.b2b168.com/", "fallback.xlsx"),
        "b2b168.com",
        "Priority 2: 从官网 URL 提取 B2B 平台域名"
    )
    _assert_eq(
        resolve("", "https://www.made-in-china.com/product/xxx", "fallback.xlsx"),
        "made-in-china.com",
        "Priority 2: 从官网 URL 提取普通平台域名"
    )
    _assert_eq(
        resolve("", "https://b2b.bjx.com.cn/company/123", "fallback.xlsx"),
        "b2b.bjx.com.cn",
        "Priority 2: 从官网 URL 提取中国 ccTLD"
    )

    # Priority 3: 兜底用文件名
    _assert_eq(
        resolve("", "", "ZHY20260729CJca800.com蔡超.xlsx"),
        "ca800.com",
        "Priority 3: 文件名清洗兜底"
    )
    _assert_eq(
        resolve("", "", "1688.com"),
        "1688.com",
        "Priority 3: 清洁文件名保持不变"
    )

    # 全空兜底
    _assert_eq(resolve("", "", ""), "", "全空返回空字符串")


# ── 9. 数据质量门禁（垃圾数据过滤） ─────────────────────────────────────

def test_data_quality_gate():
    """测试数据质量门禁（大厂 Data Quality Gate 标准）。"""
    print("\n═══ 9. 数据质量门禁 ═══")

    # _is_valid_record: 有效记录
    _assert(_is_valid_record({"phones": ["13800138000"]}), "有手机号 → 有效")
    _assert(_is_valid_record({"mobiles": ["13800138000"]}), "有手机号(mobiles) → 有效")
    _assert(_is_valid_record({"landlines": ["0519-85118888"]}), "有固话 → 有效")
    _assert(_is_valid_record({"emails": ["test@example.com"]}), "有邮箱 → 有效")
    _assert(_is_valid_record({"website": "https://example.com"}), "有官网 → 有效")
    _assert(_is_valid_record({"phone": "13800138000"}), "有 phone 平铺字段 → 有效")
    _assert(_is_valid_record({"email": "test@example.com"}), "有 email 平铺字段 → 有效")

    # _is_valid_record: 无效记录
    _assert(not _is_valid_record({}), "空记录 → 无效")
    _assert(not _is_valid_record({"company": "测试公司"}), "仅有公司名 → 无效")
    _assert(not _is_valid_record({"address": "上海市浦东新区"}), "仅有地址 → 无效")
    _assert(not _is_valid_record({"phones": [], "emails": []}), "空列表 → 无效")
    _assert(not _is_valid_record({"website": "  "}), "空白官网 → 无效")

    # _filter_invalid_records: 批量过滤
    records = [
        {"company": "A公司", "phones": ["13800000001"]},
        {"company": "B公司", "address": "北京市"},  # 无效
        {"company": "C公司", "emails": ["c@test.com"]},
        {"company": "D公司"},  # 无效
        {"company": "E公司", "website": "https://e.com"},
    ]
    valid = _filter_invalid_records(records)
    _assert_eq(len(valid), 3, "5 条过滤后剩 3 条")
    _assert_eq(valid[0]["company"], "A公司", "保留第 1 条")
    _assert_eq(valid[1]["company"], "C公司", "保留第 3 条")
    _assert_eq(valid[2]["company"], "E公司", "保留第 5 条")

    # 空输入
    _assert_eq(len(_filter_invalid_records([])), 0, "空输入返回空")


# ── 10. 并发上传合并场景（用户 A + 用户 B） ───────────────────────────────

def test_concurrent_merge_scenario():
    """测试用户场景：A 上传 10 条，B 上传 10 条，5 条重复，字段互补。"""
    print("\n═══ 10. 并发上传合并场景 ═══")

    # 模拟用户 A 的 10 条数据
    user_a_records = [
        {
            "company": "常州天合光能有限公司",
            "company_norm": normalize_company_name("常州天合光能有限公司"),
            "phones": ["13800138000"],
            "mobiles": ["13800138000"],
            "landlines": [],
            "emails": ["sales@trinasolar.com"],
            "wechats": [],
            "contacts": [{"name": "张经理", "title": "销售经理"}],
            "website": "https://www.trinasolar.com",
            "products": "光伏组件、逆变器",
            "industry": "新能源",
            "address": "江苏省常州市",
            "country": "中国",
            "source": "八方资源网",
            "source_time": "2026-11-09T10:00:00",
            "source_user": "userA",
            "is_domestic": True,
        },
        {
            "company": "阳光电源股份有限公司",
            "company_norm": normalize_company_name("阳光电源股份有限公司"),
            "phones": ["0551-65327867"],
            "mobiles": [],
            "landlines": ["0551-65327867"],
            "emails": [],
            "wechats": [],
            "contacts": [{"name": "李总", "title": "总经理"}],
            "website": "",
            "products": "光伏逆变器、储能系统",
            "industry": "新能源",
            "address": "安徽省合肥市",
            "country": "中国",
            "source": "中国制造网",
            "source_time": "2026-11-09T10:30:00",
            "source_user": "userA",
            "is_domestic": True,
        },
        # A 的垃圾数据（无联系方式）
        {
            "company": "垃圾公司A",
            "company_norm": "垃圾公司A",
            "phones": [], "mobiles": [], "landlines": [], "emails": [], "wechats": [],
            "contacts": [], "website": "", "products": "", "industry": "",
            "address": "某地址", "country": "中国", "source": "测试",
            "source_time": "2026-11-09T11:00:00", "source_user": "userA",
            "is_domestic": True,
        },
    ]

    # 模拟用户 B 的 3 条数据（2 条与 A 重复，但字段互补；1 条新数据）
    user_b_records = [
        {
            "company": "常州天合光能有限公司",  # 与 A 重复
            "company_norm": normalize_company_name("常州天合光能有限公司"),
            "phones": ["0519-85118888"],  # 新固话
            "mobiles": [],
            "landlines": ["0519-85118888"],
            "emails": ["info@trinasolar.com"],  # 新邮箱
            "wechats": ["trina_wx"],  # 新微信
            "contacts": [{"name": "王主管", "title": "采购主管"}],  # 新联系人
            "website": "https://www.trinasolar.com",
            "products": "",  # A 有主营，B 无
            "industry": "光伏",  # A 有行业，B 也有
            "address": "江苏省常州市新北区",
            "country": "中国",
            "source": "爱企查",
            "source_time": "2026-11-09T12:00:00",  # 更新的时间
            "source_user": "userB",
            "is_domestic": True,
        },
        {
            "company": "阳光电源股份有限公司",  # 与 A 重复
            "company_norm": normalize_company_name("阳光电源股份有限公司"),
            "phones": [], "mobiles": [], "landlines": [],
            "emails": ["contact@sungrow.com"],  # 新邮箱
            "wechats": [],
            "contacts": [],
            "website": "https://www.sungrowpower.com",  # 新官网
            "products": "", "industry": "", "address": "",
            "country": "中国", "source": "风鸟",
            "source_time": "2026-11-09T13:00:00",
            "source_user": "userB",
            "is_domestic": True,
        },
        {
            "company": "宁波锦浪新能源技术有限公司",  # B 的新数据
            "company_norm": normalize_company_name("宁波锦浪新能源技术有限公司"),
            "phones": ["0574-87778888"],
            "mobiles": [],
            "landlines": ["0574-87778888"],
            "emails": ["sales@ginlong.com"],
            "wechats": [],
            "contacts": [{"name": "赵工", "title": "技术总监"}],
            "website": "https://www.ginlong.com",
            "products": "组串式逆变器",
            "industry": "新能源",
            "address": "浙江省宁波市",
            "country": "中国",
            "source": "八方资源网",
            "source_time": "2026-11-09T14:00:00",
            "source_user": "userB",
            "is_domestic": True,
        },
    ]

    # 合并 A + B 的所有记录（模拟读取端合并）
    all_records = user_a_records + user_b_records
    merged = DataPoolManager._merge_records(all_records)

    # 验证：垃圾数据被过滤
    companies = [r["company"] for r in merged]
    _assert("垃圾公司A" not in companies, "垃圾数据（无联系方式）被过滤")

    # 验证：合并后应有 3 家公司（天合、阳光、锦浪）
    _assert_eq(len(merged), 3, "3 家不重复公司（垃圾数据已过滤）")

    # 验证：天合光能的字段合并
    trina = next(r for r in merged if "天合" in r["company"])
    # 电话取并集
    _assert("13800138000" in trina["phones"], "天合：保留 A 的手机号")
    _assert("0519-85118888" in trina["phones"], "天合：合并 B 的固话")
    # 邮箱取并集
    _assert("sales@trinasolar.com" in trina["emails"], "天合：保留 A 的邮箱")
    _assert("info@trinasolar.com" in trina["emails"], "天合：合并 B 的邮箱")
    # 微信合并
    _assert("trina_wx" in trina["wechats"], "天合：合并 B 的微信")
    # 联系人合并
    contact_names = [c["name"] for c in trina["contacts"]]
    _assert("张经理" in contact_names, "天合：保留 A 的联系人")
    _assert("王主管" in contact_names, "天合：合并 B 的联系人")
    # 文本字段取首个非空
    _assert(trina["products"], "天合：主营不为空")
    _assert("光伏" in trina["products"] or "组件" in trina["products"],
            "天合：主营来自 A（B 为空）")
    # 来源拼接
    _assert("八方资源网" in trina["source"], "天合：来源包含 A 的平台")
    _assert("爱企查" in trina["source"], "天合：来源包含 B 的平台")
    # 时间取最新
    _assert_eq(trina["source_time"], "2026-11-09T12:00:00", "天合：时间取最新")

    # 验证：阳光电源的字段合并
    sungrow = next(r for r in merged if "阳光" in r["company"])
    # 电话保留
    _assert("0551-65327867" in sungrow["phones"], "阳光：保留 A 的电话")
    # 邮箱合并
    _assert("contact@sungrow.com" in sungrow["emails"], "阳光：合并 B 的邮箱")
    # 官网合并
    _assert(sungrow["website"], "阳光：官网不为空（来自 B）")
    # 来源拼接
    _assert("中国制造网" in sungrow["source"], "阳光：来源包含 A 的平台")
    _assert("风鸟" in sungrow["source"], "阳光：来源包含 B 的平台")

    # 验证：锦浪（B 的新数据）
    ginlong = next(r for r in merged if "锦浪" in r["company"])
    _assert_eq(ginlong["company"], "宁波锦浪新能源技术有限公司", "锦浪：公司名正确")
    _assert(ginlong["phones"], "锦浪：有电话")
    _assert(ginlong["emails"], "锦浪：有邮箱")


# ── 主入口 ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("数据池模块离线测试")
    print("=" * 60)

    test_normalize_company_name()
    test_tokenize()
    test_search_local()
    test_merge_records()
    test_normalize_row()
    test_clean_source_name()
    test_extract_domain_from_url()
    test_resolve_source()
    test_data_quality_gate()
    test_concurrent_merge_scenario()

    print("\n" + "=" * 60)
    total = _passed + _failed
    if _failed == 0:
        print(f"全部通过 ✅ ({_passed}/{total})")
    else:
        print(f"通过 {_passed}/{total}，失败 {_failed} ❌")
    print("=" * 60)

    sys.exit(1 if _failed else 0)
