#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_optimizations.py —— 9 项企业级优化全面自测。

测试覆盖：
  1. mapping_recommender 智能推荐
  2. audit_logger 审计日志
  3. dedup_engine 智能去重
  4. search_optimizer 搜索优化
  5. 传输层韧性（oss_pool_client 超时自适应 + 指数退避）
  6. pool_dialogs 集成（模板/脱敏/审计）
  7. pool_matcher 搜索意图扩展
  8. data_pool 质量评分 + 去重集成
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

# 添加模块路径
sys.path.insert(0, str(Path(__file__).parent))

print("=" * 70)
print("9 项企业级优化全面自测")
print("=" * 70)


# ── 测试 1: mapping_recommender 智能推荐 ──
def test_mapping_recommender():
    print("\n═══ 1. mapping_recommender 智能推荐 ═══")
    try:
        from mapping_recommender import MappingRecommender
        
        # 用临时文件测试
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            temp_file = f.name
        
        try:
            rec = MappingRecommender(cache_file=temp_file)
            
            # 测试 1.1: 首次推荐应为空
            headers1 = ["公司名", "联系人", "电话", "邮箱"]
            rec1 = rec.recommend(headers1)
            assert rec1 == {}, f"首次推荐应为空，实际={rec1}"
            print("  ✅ 1.1 首次推荐为空")
            
            # 测试 1.2: 更新历史后推荐应生效
            mapping1 = {0: "company", 1: "contacts", 2: "phones", 3: "emails"}
            rec.update(headers1, mapping1)
            
            rec2 = MappingRecommender(cache_file=temp_file)
            rec2_result = rec2.recommend(headers1)
            assert rec2_result == mapping1, f"推荐结果不匹配，期望={mapping1}, 实际={rec2_result}"
            print("  ✅ 1.2 更新后推荐正确")
            
            # 测试 1.3: 多次更新后权重累加
            mapping2 = {0: "company", 1: "contacts", 2: "phones", 3: "website"}
            rec.update(headers1, mapping2)
            
            rec3 = MappingRecommender(cache_file=temp_file)
            rec3_result = rec3.recommend(headers1)
            # 第 3 列应该推荐 emails（出现 1 次）vs website（出现 1 次），取首个
            assert 0 in rec3_result and rec3_result[0] == "company"
            print("  ✅ 1.3 多次更新后推荐正确")
            
        finally:
            if os.path.exists(temp_file):
                os.unlink(temp_file)
        
        print("  ✅ mapping_recommender 全部通过")
        return True
    except Exception as e:
        print(f"  ❌ mapping_recommender 失败: {e}")
        import traceback
        traceback.print_exc()
        return False


# ── 测试 2: audit_logger 审计日志 ──
def test_audit_logger():
    print("\n═══ 2. audit_logger 审计日志 ═══")
    try:
        from audit_logger import AuditLogger, get_audit_logger
        
        # 测试 2.1: 单例模式
        logger1 = get_audit_logger()
        logger2 = get_audit_logger()
        assert logger1 is logger2, "单例模式失效"
        print("  ✅ 2.1 单例模式正确")
        
        # 测试 2.2: 写入日志
        with tempfile.NamedTemporaryFile(mode='w', suffix='.log', delete=False) as f:
            temp_log = f.name
        
        try:
            test_logger = AuditLogger(log_file=temp_log)
            test_logger.log("search", "test_user", {"query": "光伏", "count": 10})
            test_logger.log("export", "test_user", {"count": 5, "filepath": "test.xlsx"})
            
            # 读取验证
            logs = test_logger.get_recent(10)
            assert len(logs) == 2, f"日志条数不对，期望=2, 实际={len(logs)}"
            assert logs[0]["action"] == "search"
            assert logs[1]["action"] == "export"
            print("  ✅ 2.2 写入读取日志正确")
            
        finally:
            if os.path.exists(temp_log):
                os.unlink(temp_log)
        
        print("  ✅ audit_logger 全部通过")
        return True
    except Exception as e:
        print(f"  ❌ audit_logger 失败: {e}")
        import traceback
        traceback.print_exc()
        return False


# ── 测试 3: dedup_engine 智能去重 ──
def test_dedup_engine():
    print("\n═══ 3. dedup_engine 智能去重 ═══")
    try:
        from dedup_engine import fuzzy_match_company, compute_duplicate_score
        import dedup_engine as dedup_module
        from pool_merge import merge_duplicates_fast
        
        # 测试 3.1: 公司名模糊匹配
        score1 = fuzzy_match_company("华为技术有限公司", "华为技术公司")
        assert score1 > 0.8, f"相似公司名分数过低: {score1}"
        print(f"  ✅ 3.1 模糊匹配: '华为技术有限公司' vs '华为技术公司' = {score1:.2f}")
        
        score2 = fuzzy_match_company("常州天合光能", "天合光能有限公司")
        assert score2 > 0.7, f"包含关系分数过低: {score2}"
        print(f"  ✅ 3.2 包含匹配: '常州天合光能' vs '天合光能有限公司' = {score2:.2f}")
        
        score3 = fuzzy_match_company("苹果公司", "苹果电脑")
        assert score3 >= 0.5, f"部分匹配分数过低: {score3}"
        print(f"  ✅ 3.3 部分匹配: '苹果公司' vs '苹果电脑' = {score3:.2f}")
        
        # 测试 3.4: 多信号融合评分
        rec1 = {
            "company": "华为技术有限公司",
            "phones": ["0755-28780808"],
            "emails": ["info@huawei.com"],
            "address": "深圳市龙岗区"
        }
        rec2 = {
            "company": "华为技术公司",
            "phones": ["0755-28780808"],
            "emails": ["info@huawei.com"],
            "address": "深圳龙岗"
        }
        total, signals = compute_duplicate_score(rec1, rec2)
        assert total > 0.80, f"重复记录总分过低: {total}"
        print(f"  ✅ 3.4 多信号融合: total={total:.2f}, signals={signals}")
        
        # 测试 3.5: 批量去重走 pool_merge（阻塞键分桶，块内才两两比较）
        records = [
            {"company": "华为技术有限公司", "phones": ["0755-28780808"], "emails": ["info@huawei.com"], "address": "深圳", "source": "官网A"},
            {"company": "华为技术公司", "phones": ["0755-28780808"], "emails": ["info@huawei.com"], "address": "深圳龙岗", "source": "展会B"},
            {"company": "常州天合光能", "phones": ["0519-85118888"], "emails": ["info@trina.com"], "address": "常州", "source": "B2B C"},
        ]
        merged = merge_duplicates_fast(records, threshold=0.80)
        assert len(merged) == 2, f"去重后条数不对，期望=2, 实际={len(merged)}"
        huawei = next(r for r in merged if "华为" in r.get("company", ""))
        assert " / " in huawei.get("source", ""), f"合并后来源未拼接: {huawei.get('source')}"
        assert "sources" not in huawei, f"旧实现的 phantom 键复现: {huawei.get('sources')}"
        assert "官网A" in huawei["source"] and "展会B" in huawei["source"], \
            f"两个来源未全部保留: {huawei['source']}"
        print(f"  ✅ 3.5 批量去重: {len(records)} → {len(merged)} 条，来源={huawei['source']}")

        # 测试 3.6: 合并调度只允许一份实现（O(n²) 旧入口必须已删除）
        assert not hasattr(dedup_module, "find_duplicates"), "dedup_engine 仍留有全量两两比较入口"
        assert not hasattr(dedup_module, "merge_duplicates"), "dedup_engine 仍留有第二份合并实现"
        assert not hasattr(dedup_module, "_merge_record_group"), "dedup_engine 仍留有旧合并辅助函数"
        print("  ✅ 3.6 单一合并实现: 调度只在 pool_merge，打分只在 dedup_engine")
        
        print("  ✅ dedup_engine 全部通过")
        return True
    except Exception as e:
        print(f"  ❌ dedup_engine 失败: {e}")
        import traceback
        traceback.print_exc()
        return False


# ── 测试 4: search_optimizer 搜索优化 ──
def test_search_optimizer():
    print("\n═══ 4. search_optimizer 搜索优化 ═══")
    try:
        from search_optimizer import TokenCache, SearchOptimizer, get_search_optimizer
        from pool_matcher import tokenize
        
        # 测试 4.1: 令牌缓存
        cache = TokenCache(max_size=100)
        text = "光伏组件制造商"
        tokens1 = cache.get(text, tokenize)
        tokens2 = cache.get(text, tokenize)
        assert tokens1 == tokens2, "缓存前后分词不一致"
        print(f"  ✅ 4.1 令牌缓存: '{text}' → {tokens1[:3]}...")
        
        # 测试 4.2: 单一召回实现守卫（Python 侧不得再留第二份倒排）
        import search_optimizer as so_module
        assert not hasattr(so_module, "ShardedIndex"), "第二份倒排实现未清除"
        assert not hasattr(SearchOptimizer, "get_candidates"), "优化器不应再对外提供召回入口"
        assert hasattr(so_module, "reset_search_optimizer"), "重置入口丢失"
        print("  ✅ 4.2 单一召回实现: 倒排只保留 pool_store + search_by_index 两级")

        # 测试 4.3: LRU 驱逐的是最久未使用的那条（旧实现 list.remove 是 O(n)）
        lru = TokenCache(max_size=2)
        lru.get("A公司", tokenize)
        lru.get("B公司", tokenize)
        lru.get("A公司", tokenize)      # A 重新变为最近使用
        lru.get("C公司", tokenize)      # 超限，应驱逐 B 而不是 A
        keys = list(lru.cache)
        assert keys == ["A公司", "C公司"], f"LRU 驱逐顺序错误: {keys}"
        assert len(lru.cache) == 2, f"缓存未收敛到上限: {len(lru.cache)}"
        assert lru.stats()["size"] == 2, "stats 与缓存实际规模不一致"
        print(f"  ✅ 4.3 LRU 驱逐: 保留 {keys}")

        # 测试 4.4: 换库后令牌缓存必须作废（旧实现 initialize 早退自锁，永不校验）
        opt = SearchOptimizer(cache_size=100)
        master_v1 = [{"company": "常州天合光能有限公司"}]
        opt.initialize(master_v1, tokenize)
        opt.get_tokens(" sunlight ", tokenize)
        assert len(opt.token_cache.cache) == 1, "首次分词结果未进缓存"
        master_v2 = [{"company": "华为技术有限公司"}]
        opt.initialize(master_v2, tokenize)
        assert len(opt.token_cache.cache) == 0, "换库后旧分词缓存未作废"
        # 同一批数据再次 initialize 不应把缓存清掉（否则缓存白建）
        opt.initialize(master_v2, tokenize)
        opt.get_tokens("逆变器", tokenize)
        opt.initialize(master_v2, tokenize)
        assert len(opt.token_cache.cache) == 1, "同库重复 initialize 误清缓存"
        # 同一对象就地增删也要被条数校验察觉
        master_v2.append({"company": "阳光电源股份有限公司"})
        opt.initialize(master_v2, tokenize)
        assert len(opt.token_cache.cache) == 0, "总表条数变化未作废缓存"
        print("  ✅ 4.4 换库失效: 数据签名变化即作废、未变化则保留")

        # 测试 4.5: 全局优化器单例
        opt1 = get_search_optimizer()
        opt2 = get_search_optimizer()
        assert opt1 is opt2, "单例模式失效"
        print("  ✅ 4.5 全局优化器单例正确")
        
        print("  ✅ search_optimizer 全部通过")
        return True
    except Exception as e:
        print(f"  ❌ search_optimizer 失败: {e}")
        import traceback
        traceback.print_exc()
        return False


# ── 测试 5: 传输层韧性（oss_pool_client）──
def test_transport_resilience():
    print("\n═══ 5. 传输层韧性（超时自适应 / 指数退避）═══")
    try:
        import importlib
        import sys
        from oss_pool_client import (_adaptive_timeout, _backoff,
                                     _MAX_RETRIES, _TIMEOUT_CAP_S)

        # 测试 5.1: 超时按对象体积线性放大（固定 30s 对几十 MB 上传是必失败配置）
        t_small = _adaptive_timeout(200 * 1024)
        t_mid = _adaptive_timeout(10 * 1024 * 1024)
        t_huge = _adaptive_timeout(1024 * 1024 * 1024)
        assert t_small < t_mid < t_huge, "超时未随体积单调放大"
        assert t_huge == _TIMEOUT_CAP_S, f"超时未封顶: {t_huge}"
        assert t_mid >= 20 + 10 * 12, f"10MB 只给 {t_mid}s，按保守上行速率必失败"
        assert _adaptive_timeout(0) > 0, "空对象给出 0 超时"
        assert _adaptive_timeout(-5) > 0, "异常体积给出 0 超时"
        print(f"  ✅ 5.1 超时自适应: 200KB={t_small}s 10MB={t_mid}s 1GB={t_huge}s")

        # 测试 5.2: 指数退避单调且封顶（不封顶会把一次网络抖动拖成用户干等）
        waits = [_backoff(i) for i in range(6)]
        assert waits == sorted(waits), f"退避不单调: {waits}"
        assert max(waits) <= 4.0, f"退避未封顶: {max(waits)}"
        assert _MAX_RETRIES == 3, f"重试次数不合理: {_MAX_RETRIES}"
        print(f"  ✅ 5.2 指数退避: {waits}（封顶 4s，最多 {_MAX_RETRIES} 次）")

        # 测试 5.3: 传输重试只允许一份实现——被取代的死模块必须真的不在
        try:
            importlib.import_module("sync_optimizer")
            still_there = True
        except ImportError:
            still_there = False
        assert not still_there, "sync_optimizer 仍可导入（会与 oss_pool_client 形成第二份重试）"
        assert "sync_optimizer" not in sys.modules, "sync_optimizer 被某处导入进来了"
        print("  ✅ 5.3 单一传输实现: 重试/超时只在 oss_pool_client 一份")

        print("  ✅ 传输层韧性全部通过")
        return True
    except Exception as e:
        print(f"  ❌ 传输层韧性失败: {e}")
        import traceback
        traceback.print_exc()
        return False


# ── 测试 6: pool_dialogs 集成（脱敏函数）──
def test_pool_dialogs_masking():
    print("\n═══ 6. pool_dialogs 脱敏函数 ═══")
    try:
        # 必须测产品里那一份：旧写法把 _mask_phone/_mask_email/_mask_wechat
        # **抄进测试字符串里 exec 一遍再断言抄出来的副本**——界面上真用的函数
        # 无论怎么改，这个用例都永远绿，等于完全没测（2026-11-10 自查抓到）。
        from pool_dialogs import _mask_phone, _mask_email, _mask_wechat

        # 6.1 手机号：国内号留前3后4，固话留前3后2（固话位数不一，砍中间最稳）
        assert _mask_phone("13812345678") == "138****5678"
        assert _mask_phone("0519-85118888") == "051***88"
        assert _mask_phone("") == ""
        # 6.2 邮箱：只留本地部分首字符，域名完整保留（对方是谁一眼可辨才敢联系）
        assert _mask_email("john@example.com") == "j***@example.com"
        assert _mask_email("a@test.com") == "a***@test.com"
        assert _mask_email("") == ""
        # 6.3 微信：两位及以下只留首字符，长号留首尾
        assert _mask_wechat("abcdef") == "a***f"
        assert _mask_wechat("ab") == "a*"
        assert _mask_wechat("") == ""

        print("  ✅ 6.1-6.3 手机号/邮箱/微信脱敏（直接测 pool_dialogs 本体）全部正确")
        return True
    except Exception as e:
        print(f"  ❌ pool_dialogs 脱敏函数失败: {e}")
        import traceback
        traceback.print_exc()
        return False


# ── 测试 7: pool_matcher 搜索意图扩展 ──
def test_pool_matcher_expansion():
    print("\n═══ 7. pool_matcher 搜索意图扩展 ═══")
    try:
        from pool_matcher import expand_query, INDUSTRY_SYNONYMS, GEOGRAPHY_SYNONYMS
        
        # 测试 7.1: 行业同义词扩展
        expanded1 = expand_query("光伏")
        assert "光伏" in expanded1
        assert any("太阳能" in t or "solar" in t.lower() for t in expanded1)
        print(f"  ✅ 7.1 行业扩展: '光伏' → {expanded1[:3]}...")
        
        # 测试 7.2: 地理同义词扩展
        expanded2 = expand_query("江苏")
        assert "江苏" in expanded2
        assert any("南京" in t or "苏州" in t for t in expanded2)
        print(f"  ✅ 7.2 地理扩展: '江苏' → {expanded2[:3]}...")
        
        # 测试 7.3: 无扩展
        expanded3 = expand_query("未知行业")
        assert expanded3 == ["未知行业"], f"无扩展时应返回原词，实际={expanded3}"
        print("  ✅ 7.3 无扩展时返回原词")
        
        print("  ✅ pool_matcher 搜索意图扩展全部通过")
        return True
    except Exception as e:
        print(f"  ❌ pool_matcher 搜索意图扩展失败: {e}")
        import traceback
        traceback.print_exc()
        return False


# ── 测试 8: data_pool 质量评分 + 去重集成 ──
def test_data_pool_quality():
    print("\n═══ 8. data_pool 质量评分 ═══")
    try:
        from data_pool import compute_quality_score
        
        # 测试 8.1: 完整记录
        rec1 = {
            "company": "华为技术有限公司",
            "contacts": [{"name": "张三", "title": "经理"}],
            "phones": ["0755-28780808"],
            "mobiles": ["13800138000"],
            "emails": ["info@huawei.com"],
            "website": "https://www.huawei.com",
            "address": "深圳市龙岗区",
            "industry": "通信",
            "products": "通信设备",
            "country": "中国",
            "source": "官网",
            "source_time": "2026-11-06"
        }
        score1 = compute_quality_score(rec1)
        assert score1 == 100, f"完整记录评分应为 100，实际={score1}"
        print(f"  ✅ 8.1 完整记录评分: {score1}")
        
        # 测试 8.2: 部分记录
        rec2 = {
            "company": "常州天合光能",
            "phones": ["0519-85118888"],
        }
        score2 = compute_quality_score(rec2)
        assert 20 < score2 < 50, f"部分记录评分异常: {score2}"
        print(f"  ✅ 8.2 部分记录评分: {score2}")
        
        # 测试 8.3: 空记录
        rec3 = {}
        score3 = compute_quality_score(rec3)
        assert score3 == 0, f"空记录评分应为 0，实际={score3}"
        print(f"  ✅ 8.3 空记录评分: {score3}")
        
        print("  ✅ data_pool 质量评分全部通过")
        return True
    except Exception as e:
        print(f"  ❌ data_pool 质量评分失败: {e}")
        import traceback
        traceback.print_exc()
        return False


# ── 主测试流程 ──
if __name__ == "__main__":
    results = []
    
    results.append(("mapping_recommender", test_mapping_recommender()))
    results.append(("audit_logger", test_audit_logger()))
    results.append(("dedup_engine", test_dedup_engine()))
    results.append(("search_optimizer", test_search_optimizer()))
    results.append(("transport_resilience", test_transport_resilience()))
    results.append(("pool_dialogs_masking", test_pool_dialogs_masking()))
    results.append(("pool_matcher_expansion", test_pool_matcher_expansion()))
    results.append(("data_pool_quality", test_data_pool_quality()))
    
    print("\n" + "=" * 70)
    print("测试汇总")
    print("=" * 70)
    
    passed = sum(1 for _, r in results if r)
    total = len(results)
    
    for name, result in results:
        status = "✅ 通过" if result else "❌ 失败"
        print(f"  {status}  {name}")
    
    print(f"\n总计: {passed}/{total} 通过")
    
    if passed == total:
        print("\n🎉 全部测试通过！可以交付用户测试")
        sys.exit(0)
    else:
        print("\n⚠️  存在失败项，需修复后再交付")
        sys.exit(1)
