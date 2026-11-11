# -*- coding: utf-8 -*-
"""search_optimizer.py —— 精排阶段的令牌缓存。本模块只做这一件事。

职责边界先说清楚（本轮把重复的第二份倒排删掉了，理由写在下面）：
  · **召回只有一条主力路径**：Gold 层 pool_store（SQLite + FTS5）。全文、分区裁剪、
    联系方式筛选全部下压到库内完成，百万级也是毫秒返回。
  · 视图不可用时的兜底召回是 data_pool.search_by_index() 的磁盘倒排索引：它覆盖
    pool_matcher.search_fields 定义的全部可检索字段（公司名/主营/邮箱/电话/网址…），
    并与总表做规模校验，不匹配就回退全表扫描。
  · 本模块原先还自带一份 ShardedIndex（Python 分片倒排），属于**同一件事的第三份
    实现**，而且是三份里最错的那个：只索引 company/products 两个字段，口径比
    search_fields 窄 → 用它召回必然漏掉靠邮箱/电话命中的记录；更糟的是它建完之后
    没有任何调用方查询它（候选一直来自磁盘倒排），但 initialize() 会在每次搜索的
    首次调用里无条件把六万条总表重建一遍（几秒 + 几百 MB）。
    同职责索引只要存在两份就迟早漂移，按大厂纪律直接删除，只保留令牌缓存。

本轮真正修掉的缺陷：
  1. initialize() 早退自锁：一旦标记 initialized 就永不校验，总表换了对象或条数
     变了，令牌缓存里旧文本的分词结果仍被继续复用，且不报任何错。现在按数据集
     签名（对象 + 条数）自检，签名不符就整块作废重建。
  2. LRU 驱逐用 list.remove()：每次驱逐都是 O(n)，十万条缓存时驱逐一次等于一次
     全表扫。改成 OrderedDict —— 命中 move_to_end、超限 popitem(last=False)，
     两个操作都是 O(1)。
  3. 缓存钉住旧总表：如果 initialize() 强引用 records，换库后旧数据被缓存器扣住
     不释放（内存只增不减）。现在用 weakref 记签名，旧总表能正常回收。
"""
import weakref
from typing import Dict, List, Optional
from collections import OrderedDict


class TokenCache:
    """令牌缓存（LRU 策略，避免精排阶段重复分词）。"""

    def __init__(self, max_size: int = 100000):
        self.max_size = max(1, int(max_size))
        # OrderedDict 同时承担"值存储"和"访问顺序"两件事：
        # 命中时 move_to_end、超限时 popitem(last=False)，都是 O(1)
        self._items: "OrderedDict[str, List[str]]" = OrderedDict()
        self.hits = 0
        self.misses = 0

    @property
    def cache(self) -> Dict[str, List[str]]:
        """兼容旧字段名（外部打印/测试用）。"""
        return self._items

    @property
    def access_order(self) -> List[str]:
        """当前 key 的访问顺序（最久未使用在前），仅用于诊断。"""
        return list(self._items)

    def get(self, text: str, tokenize_func) -> List[str]:
        """获取分词结果：缓存命中直接返回，否则调用 tokenize_func 并回填。"""
        tokens = self._items.get(text)
        if tokens is not None:
            self.hits += 1
            self._items.move_to_end(text)          # 标记为最近使用
            return tokens
        self.misses += 1
        tokens = tokenize_func(text)
        self._items[text] = tokens
        # 超限时驱逐最久未使用的那条；用 while 而非 if，保证 max_size 被调小后也能收敛
        while len(self._items) > self.max_size:
            self._items.popitem(last=False)
        return tokens

    def clear(self):
        """清空缓存（数据换库时由 SearchOptimizer 调用）。"""
        self._items.clear()
        self.hits = 0
        self.misses = 0

    def stats(self) -> Dict[str, int]:
        """可观测性：命中率是判断"缓存到底有没有用"的唯一依据。"""
        total = self.hits + self.misses
        return {
            "size": len(self._items),
            "max_size": self.max_size,
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": round(self.hits / total, 4) if total else 0.0,
        }


class SearchOptimizer:
    """搜索优化器（只保留令牌缓存，不再有第二份倒排）。"""

    def __init__(self, cache_size: int = 100000):
        self.token_cache = TokenCache(max_size=cache_size)
        # 数据集签名：weakref 优先（不钉住旧总表），不可弱引用的对象退回 id()
        self._source: Optional[object] = None
        self._count: int = -1

    def initialize(self, records: List[dict], tokenize_func=None):
        """绑定当前总表：签名一致就复用缓存，换了数据就整块作废。

        注意这里**不再构建任何倒排**——精排阶段只需要 token 缓存；召回是
        pool_store / search_by_index 的职责，不在本模块重复实现。
        """
        if self._is_current(records):
            return
        self.token_cache.clear()
        self._count = len(records)
        try:
            self._source = weakref.ref(records)
        except TypeError:                      # list/tuple/dict 都支持 weakref，兜底保底
            self._source = id(records)

    def _is_current(self, records: List[dict]) -> bool:
        """判断缓存是否仍属于这批数据（对象同一 + 条数一致）。"""
        if self._source is None or self._count < 0:
            return False
        source = self._source
        if isinstance(source, weakref.ReferenceType):
            if source() is not records:
                return False                   # 换对象了，或原总表已被回收
        elif source != id(records):
            return False
        return len(records) == self._count     # 同一对象被就地增删也要察觉

    def get_tokens(self, text: str, tokenize_func) -> List[str]:
        """获取分词结果（带缓存）。"""
        return self.token_cache.get(text, tokenize_func)

    def reset(self):
        """重置优化器（清空缓存与数据签名）。"""
        self.token_cache.clear()
        self._source = None
        self._count = -1


# ── 全局优化器实例（单例模式）──
_optimizer: Optional[SearchOptimizer] = None


def get_search_optimizer(cache_size: int = 100000) -> SearchOptimizer:
    """获取全局搜索优化器单例（cache_size 只在首次创建时生效）。"""
    global _optimizer
    if _optimizer is None:
        _optimizer = SearchOptimizer(cache_size=cache_size)
    return _optimizer


def reset_search_optimizer():
    """重置全局优化器（数据换库时调用）。"""
    global _optimizer
    if _optimizer is not None:
        _optimizer.reset()
    _optimizer = None
