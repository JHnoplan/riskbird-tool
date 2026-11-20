# -*- coding: utf-8 -*-
"""
号码智能核验内核（免费 / 离线 / 合规层）
================================================

设计铁律（来自本项目历史踩坑，务必遵守）：
1. 绝不自动删除任何一个号码——只做「分级 + 标记 + 给依据」，原值一律保留，
   是否入库交给下游或人工判断。任何"某字段为空/无效即 drop 整行"的写法都被禁止。
2. 单元格数值（int/float）必须先展开成完整数字串，避免 11 位手机号变成 1.38e+10。
3. 运营商 / 归属地为「最佳猜测」（携号转网、新号段会不准），只作参考列，
   绝不作为删除或降级为无效的依据。
4. 号码有效性判断基于 libphonenumber 的号码规划元数据，是"号段是否可能存在"，
   不是"此刻是否有人在用"；后者需运营商 HLR（付费），见文末插件接口。

依赖：phonenumbers（纯本地开源库，无网络、无 QPS 限制、无配额）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional

import phonenumbers
from phonenumbers import carrier as pn_carrier
from phonenumbers import geocoder as pn_geocoder
from phonenumbers import number_type as pn_number_type

# 默认按中国大陆号码解析（号码未带 +86 时生效）
DEFAULT_REGION = "CN"

# ---------------------------------------------------------------------------
# 分级枚举
# ---------------------------------------------------------------------------
TIER_VALID = "有效"        # 绿色：号段在规划内、格式正确
TIER_SUSPECT = "存疑"      # 黄色：能解析但号段校验没过（可能新号段），人工复核
TIER_INVALID = "无效"      # 红色：位数不对 / 非号码 / 明显垃圾，仍保留原值


# number_type 常量 -> 中文
_TYPE_CN = {
    phonenumbers.PhoneNumberType.MOBILE: "手机号",
    phonenumbers.PhoneNumberType.FIXED_LINE: "固话",
    phonenumbers.PhoneNumberType.FIXED_LINE_OR_MOBILE: "固话或手机",
    phonenumbers.PhoneNumberType.TOLL_FREE: "免费电话",
    phonenumbers.PhoneNumberType.PREMIUM_RATE: "增值付费",
    phonenumbers.PhoneNumberType.SHARED_COST: "分摊付费",
    phonenumbers.PhoneNumberType.VOIP: "网络电话",
    phonenumbers.PhoneNumberType.PAGER: "寻呼",
    phonenumbers.PhoneNumberType.UAN: "统一号码",
    phonenumbers.PhoneNumberType.VOICEMAIL: "语音信箱",
    phonenumbers.PhoneNumberType.UNKNOWN: "未知类型",
}

# 中国大陆手机号正则（整串锚定，绝不用 search 找 11 位子串，否则会把固话连写误判）
_CN_MOBILE_RE = re.compile(r"^1[3-9]\d{9}$")
# 显式多号码分隔符（不含空格！空格常出现在 +86 138 0013 8000 这类单号码格式中）
_SPLIT_RE = re.compile(r"[；;，,、/|\n\r]+")
# 分机标记
_EXT_MARK_RE = re.compile(r"(转|分机|ext\.?|extension)", re.IGNORECASE)


@dataclass
class NumberResult:
    """单个号码的核验结果。raw 永远是原始输入，任何情况下不改写、不丢弃。"""
    raw: str                      # 原始片段（去空白后）
    tier: str = TIER_INVALID      # 分级：有效 / 存疑 / 无效
    kind: str = ""                # 类型中文：手机号 / 固话 / ...
    e164: str = ""                # 国际格式 +86...
    national: str = ""            # 国内紧凑格式（手机号 11 位 / 固话 区号-号码）
    region: str = ""              # 归属地（最佳猜测）
    carrier: str = ""             # 运营商（最佳猜测）
    reason: str = ""              # 判定依据（给人看）


@dataclass
class CellResult:
    """一个单元格（可能含多个号码）的核验结果。"""
    raw_cell: str = ""                       # 单元格原始内容
    numbers: List[NumberResult] = field(default_factory=list)
    best_tier: str = TIER_INVALID            # 该单元格最优分级（有一个有效即有效）
    is_mobile: bool = False                  # 是否含有效手机号
    has_fixed: bool = False                  # 是否含固话
    summary: str = ""                        # 汇总说明

    @property
    def normalized_mobile(self) -> str:
        """返回规范化的手机号（若存在有效手机号），多个用逗号连接。"""
        return ",".join(n.national for n in self.numbers
                        if n.tier == TIER_VALID and n.kind == "手机号")

    @property
    def normalized_fixed(self) -> str:
        """返回规范化的固话，多个用逗号连接。"""
        return ",".join(n.national for n in self.numbers
                        if n.tier == TIER_VALID and n.kind in ("固话", "固话或手机"))


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------
def cell_to_text(value) -> str:
    """把 Excel 单元格值安全转成完整数字串文本。

    - int -> 完整数字（不科学计数）
    - float -> 若为整数值则转 int 再转串（防 13800138000.0 / 1.38e10）
    - 其余 -> str，None -> 空串
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        return repr(value)
    return str(value).strip()


def split_numbers(cell_text: str) -> List[str]:
    """把一个单元格拆成多个候选号码片段（合并再拆分，不挑一列/不丢片段）。

    关键：只有出现「显式分隔符」（；，、/| 换行）时才拆分；否则整串当一个号码，
    以免把 '+86 138 0013 8000' 这类含空格的单一号码拆碎造成误杀。
    """
    if not cell_text:
        return []
    text = cell_text.strip()
    if _SPLIT_RE.search(text):
        parts = [p.strip() for p in _SPLIT_RE.split(text) if p and p.strip()]
        if parts:
            return parts
    return [text] if text else []


def _compact_national(num) -> str:
    """国内紧凑格式：手机号去空格成 11 位；固话保留 区号-号码。"""
    nat = phonenumbers.format_number(num, phonenumbers.PhoneNumberFormat.NATIONAL)
    if num.country_code == 86:
        # 手机：NATIONAL 形如 "138 0013 8000" -> 去空格
        digits = re.sub(r"\D", "", nat)
        if _CN_MOBILE_RE.match(digits):
            return digits
        # 固话：形如 "020 8455 4218" -> 020-84554218
        pieces = [p for p in nat.split() if p]
        if len(pieces) >= 2:
            return pieces[0] + "-" + "".join(pieces[1:])
    return nat


# ---------------------------------------------------------------------------
# 单号核验
# ---------------------------------------------------------------------------
def verify_one(raw: str, region: str = DEFAULT_REGION) -> NumberResult:
    """核验单个号码片段。绝不抛异常，任何情况都返回带 tier 的结果并保留 raw。"""
    res = NumberResult(raw=raw.strip() if raw else "")
    token = res.raw
    if not token:
        res.tier = TIER_INVALID
        res.reason = "空值"
        return res

    # 剥离分机（转/分机/ext）：先核验基号，分机原样保留，绝不删除语义字符
    ext = ""
    m = _EXT_MARK_RE.search(token)
    if m:
        base = token[:m.start()].strip()
        tail = token[m.end():]
        ext = re.sub(r"\D", "", tail)
        token = base

    try:
        num = phonenumbers.parse(token, region)
    except phonenumbers.NumberParseException:
        # 解析失败：若像手机号形状，给"存疑"避免误杀；否则无效
        digits = re.sub(r"\D", "", token)
        if _CN_MOBILE_RE.match(digits):
            res.tier = TIER_SUSPECT
            res.kind = "手机号(疑似)"
            res.national = digits
            res.reason = "无法解析但形似手机号，建议人工复核"
        else:
            res.tier = TIER_INVALID
            res.reason = "无法解析为有效号码"
        return res

    ntype = pn_number_type(num)
    res.kind = _TYPE_CN.get(ntype, "未知类型")
    res.e164 = phonenumbers.format_number(num, phonenumbers.PhoneNumberFormat.E164)
    res.national = _compact_national(num)
    res.region = pn_geocoder.description_for_number(num, "zh") or ""
    res.carrier = pn_carrier.name_for_number(num, "zh") or ""
    if ext:
        res.national = f"{res.national}转{ext}"

    is_valid = phonenumbers.is_valid_number(num)
    is_possible = phonenumbers.is_possible_number(num)
    digits = re.sub(r"\D", "", res.e164 or "")

    if is_valid:
        res.tier = TIER_VALID
        res.reason = f"号段校验通过（{res.kind}）"
    else:
        # 未通过号段校验：区分「存疑」与「无效」，核心是防误杀真实号
        if _CN_MOBILE_RE.match(re.sub(r"\D", "", token)) or (
            num.country_code == 86 and len(digits) == 13 and digits.startswith("861")
        ):
            # 11 位手机号形状，但元数据判无效 —— 可能是新号段/携号转网
            res.tier = TIER_SUSPECT
            res.reason = "形似手机号但号段未在规划库中，可能是新号段，建议人工复核"
        elif is_possible:
            res.tier = TIER_SUSPECT
            res.reason = "长度合理但号段校验未通过，建议人工复核"
        else:
            res.tier = TIER_INVALID
            res.reason = "位数或号段不符合号码规划，明显无效"
    return res


def verify_cell(cell_value, region: str = DEFAULT_REGION) -> CellResult:
    """核验一个单元格（可能含多个号码）。合并拆分逐个核验，汇总最优分级。"""
    text = cell_to_text(cell_value)
    cr = CellResult(raw_cell=text)
    if not text:
        cr.best_tier = TIER_INVALID
        cr.summary = "空"
        return cr

    for frag in split_numbers(text):
        cr.numbers.append(verify_one(frag, region))

    tiers = [n.tier for n in cr.numbers]
    if TIER_VALID in tiers:
        cr.best_tier = TIER_VALID
    elif TIER_SUSPECT in tiers:
        cr.best_tier = TIER_SUSPECT
    else:
        cr.best_tier = TIER_INVALID

    cr.is_mobile = any(n.tier == TIER_VALID and n.kind == "手机号" for n in cr.numbers)
    cr.has_fixed = any(n.tier == TIER_VALID and n.kind in ("固话", "固话或手机")
                       for n in cr.numbers)

    valid_n = sum(1 for n in cr.numbers if n.tier == TIER_VALID)
    susp_n = sum(1 for n in cr.numbers if n.tier == TIER_SUSPECT)
    inv_n = sum(1 for n in cr.numbers if n.tier == TIER_INVALID)
    kinds = sorted({n.kind for n in cr.numbers if n.tier == TIER_VALID and n.kind})
    parts = [f"有效{valid_n}"]
    if susp_n:
        parts.append(f"存疑{susp_n}")
    if inv_n:
        parts.append(f"无效{inv_n}")
    if kinds:
        parts.append("/".join(kinds))
    regions = sorted({n.region for n in cr.numbers if n.tier == TIER_VALID and n.region})
    if regions:
        parts.append(regions[0])
    cr.summary = "，".join(parts)
    return cr


# ---------------------------------------------------------------------------
# 批量核验（供 GUI Worker 调用；纯 CPU、无网络，可安全在工作线程执行）
# ---------------------------------------------------------------------------
def verify_batch(values, region: str = DEFAULT_REGION, progress_cb=None):
    """批量核验一列号码。

    values: 可迭代的单元格值序列
    progress_cb(done, total): 每完成若干个回调一次（用于进度条）
    返回 List[CellResult]，与输入顺序一一对应。
    """
    values = list(values)
    total = len(values)
    out: List[CellResult] = []
    for i, v in enumerate(values):
        out.append(verify_cell(v, region))
        if progress_cb and (i % 100 == 0 or i == total - 1):
            progress_cb(i + 1, total)
    return out


# ---------------------------------------------------------------------------
# 插件接口（付费 / 灰区层，本次不实现，仅预留）
# ---------------------------------------------------------------------------
class BaseEnrichProvider:
    """号码增强核验提供者接口。

    免费离线层之外，若将来要接入：
      - 运营商 HLR / 空号检测（付费、需授权）：实现 check_in_network(number)
      - 微信真机 ADB 辅助（人工监督）：实现 check_registered(number)
    统一实现本接口，GUI 侧按 provider 名称追加列，内核分级逻辑不变。
    """
    name = "base"

    def check(self, e164: str) -> dict:  # pragma: no cover - 接口占位
        raise NotImplementedError


_TIER_ORDER = {TIER_VALID: 0, TIER_SUSPECT: 1, TIER_INVALID: 2}


def tier_rank(tier: str) -> int:
    return _TIER_ORDER.get(tier, 3)
