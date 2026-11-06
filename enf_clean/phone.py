# -*- coding: utf-8 -*-
"""电话号码智能识别：分段 -> 取号 -> 校验 -> 手机号/固话分类 -> 标准化输出。

以 Google libphonenumber（phonenumbers）为判定内核，叠加国内运营商号段规则：
- 手机号列：只放确认的手机号码（可短信、可外呼到个人）；
- 固话列：座机、公司总机、400/800 等特服号，以及无法确认为手机的境外号码；
- 准入门槛：号码必须通过 is_valid_number（号段/区号在元数据里真实存在）才入库，
  仅满足 is_possible_number（长度对但号段不存在）的伪号属于废弃数据，一律不写入结果；
- 一切判定不了的号码进入审计，不会污染结果。
"""
import re
from dataclasses import dataclass

import phonenumbers as pn
from phonenumbers import carrier

from enf_clean.normalize import normalize_text

# ----------------------------------------------------------------------------------
# 正则配置
# ----------------------------------------------------------------------------------

# 强分隔符：同一单元格内的多个号码（换行当空格处理，避免把一个号码拆成两段）
_STRONG_SPLIT_RE = re.compile(r"[;,，、|｜]|\s*(?:或者|或|和|及)\s*")

# 号码标签（按 fax -> mobile -> tel 顺序匹配，互斥命名分组）
_FAX_ALT = r"fax(?:\s*[/\-]\s*(?:tel|phone|telephone))?s?|faks|telecopie|传真机|传真"
_MOBILE_ALT = (
    r"mobile\s*phone|mobile|cellphone|cell\s*phone|cell|handphone|gsm|whatsapp"
    r"|(?<!\w)hp(?!\w)|(?<!\w)mp(?!\w)|移动电话|流动电话|手机号码|手机"
)
_TEL_ALT = (
    r"telephone|tel\s*(?:no\.?|number)?|telefonnummer|telefon|telf(?:ox)?|phone\s*(?:no\.?|number)?"
    r"|hotline|service\s*(?:hot)?line|office\s*(?:tel|phone)?|direct\s*(?:line)?"
    r"|全国统一热线|售后服务电话|服务热线|联系电话|联系方式|座机|固话|总机|直线|热线|号码|电话"
)
_LABEL_RE = re.compile(
    rf"(?ix) (?<!\w) (?:(?P<fax>{_FAX_ALT})|(?P<mobile>{_MOBILE_ALT})|(?P<tel>{_TEL_ALT})) (?![$A-Z_a-z\u00c0-\u02af]) \s*[:：]?\s*"
)

# 分机号：外呼场景无用，剔除
_EXT_RE = re.compile(
    r"(?i)(?:\bext(?:ension)?\b|(?<!\w)分机号?(?!\w)|\bes\b|\bnr\.?)\s*[:：\-]?\s*\d{1,6}"
)

# 号码 token：可选国际前缀 + 数字与号码内合法分隔符
_TOKEN_RE = re.compile(
    r"(?P<intl>(?:\+|00)\s*\d{1,3}[\s\-().]*)?(?P<body>\d[\d\s\-()./]*\d|\d)"
)
_NON_DIGIT_RE = re.compile(r"\D+")

# 中国大陆手机号：1[3-9] 开头 11 位
_CN_MOBILE_RE = re.compile(r"^1[3-9]\d{9}$")
_CN_AREA2 = {"10"} | {f"2{i}" for i in range(0, 10)}  # 010 / 020-029 为两位区号
_CN_HOTLINE_PREFIX = ("400", "800", "1010")

MIN_DIGITS, MAX_DIGITS = 6, 15

# libphonenumber 号码类型常量 -> 可读名（审计用）
_TYPE_NAME = {
    0: "FIXED_LINE", 1: "MOBILE", 2: "FIXED_LINE_OR_MOBILE", 3: "TOLL_FREE",
    4: "PREMIUM_RATE", 5: "SHARED_COST", 6: "VOIP", 7: "PERSONAL_NUMBER",
    8: "PAGER", 9: "UAN", 10: "VOICEMAIL", -1: "UNKNOWN",
}


def meta_of(number):
    return pn.number_type(number)


@dataclass
class PhoneHit:
    """一条通过 libphonenumber 严格校验的号码。"""
    key: str          # E.164，去重键
    display: str      # 入库展示格式
    bucket: str       # mobile / landline
    region: str       # ISO 区域码
    reason: str       # 判定依据
    hint: str = ""    # 解析时实际生效的区域线索


class Audit:
    """清洗审计计数器 + 抽样，用于复盘和规则调优。"""

    def __init__(self, sample_limit=60):
        self.counts = {}
        self.samples = {}
        self._limit = sample_limit

    def add(self, reason, detail=""):
        self.counts[reason] = self.counts.get(reason, 0) + 1
        bucket = self.samples.setdefault(reason, [])
        if len(bucket) < self._limit and detail:
            bucket.append(detail)

    def __str__(self):
        lines = []
        for k, v in sorted(self.counts.items(), key=lambda kv: -kv[1]):
            lines.append(f"  [{v:>6}] {k}")
            for s in self.samples.get(k, [])[:6]:
                lines.append(f"          e.g. {s!r}")
        return "\n".join(lines) if lines else "  (无异常)"


# ----------------------------------------------------------------------------------
# 分段与取号
# ----------------------------------------------------------------------------------

def _label_kind(match):
    for kind in ("fax", "mobile", "tel"):
        if match.group(kind):
            text = match.group(0)
            # "Fax/Tel" 共写：号码同时承接传真与总机，按固话标签处理
            if kind == "fax" and re.search(r"(?i)tel|phone|电话", text):
                return "tel", text
            return kind, text
    return "", match.group(0)


def _segments(raw):
    """单元格 -> [(label_kind, content)]；标签前的裸号码单独成片。"""
    text = normalize_text(raw)
    if not text:
        return []
    # 号码常跨行书写（如“+55 11\n99884-2601”），换行当空白分隔符处理
    text = re.sub(r"[\r\n]+", " ", text)
    # 数字之间的多余 +（如“+30+26213+06747”），当分隔符处理
    text = re.sub(r"(?<=\d)\s*\+(?=\d)", " ", text)
    text = _EXT_RE.sub(" ", text)
    parts = []
    for chunk in _STRONG_SPLIT_RE.split(text):
        chunk = chunk.strip()
        if not chunk:
            continue
        # 去掉不含数字的括号说明，如 "（售后电话）"
        chunk = re.sub(r"\((?![^()]*\d)[^()]*\)", " ", chunk).strip()
        if not chunk:
            continue
        matches = list(_LABEL_RE.finditer(chunk))
        if not matches:
            parts.append(("", chunk))
            continue
        head = chunk[: matches[0].start()].strip()
        if len(_NON_DIGIT_RE.sub("", head)) >= MIN_DIGITS:
            parts.append(("", head))
        for i, m in enumerate(matches):
            end = matches[i + 1].start() if i + 1 < len(matches) else len(chunk)
            content = chunk[m.end():end].strip()
            if content:
                kind, _ = _label_kind(m)
                parts.append((kind, content))
    return parts


def _tokens(content):
    """片段 -> 候选号码 token 列表（粘连超长子串会二次拆分）。"""
    out = []
    for m in _TOKEN_RE.finditer(content):
        token = (m.group("intl") or "") + m.group("body")
        digits = _NON_DIGIT_RE.sub("", token)
        if len(digits) < MIN_DIGITS:
            continue
        if len(digits) > MAX_DIGITS:
            # 可能是号码+分机号/残缺粘连：按空白拆分，只保留长度合理的子串
            subs = [s for s in re.split(r"\s+", token.strip()) if _NON_DIGIT_RE.sub("", s)]
            keep = [s for s in subs if MIN_DIGITS <= len(_NON_DIGIT_RE.sub("", s)) <= MAX_DIGITS]
            if keep:
                out.extend(keep)
                continue
        out.append(token.strip())
    return out


def _parse_variants(token, region):
    """构造多种解析入参，兼容 86-010-xxxx / 0086 / 不带 + / 误写 +0 等脏格式。"""
    digits = _NON_DIGIT_RE.sub("", token)
    # “+001 ...” 这种同时写了 + 和 00 的，按国际前缀去重
    token = re.sub(r"^\s*\+\s*00", "+", token)
    variants = []
    if token.lstrip().startswith("+"):
        variants.append((token, region))
        # “+0731-89911964”、“+400-8110278”：+ 后跟国内长途/特服号，去掉前缀按本地号重试
        stripped = re.sub(r"^\+\s*0*", "", token).strip()
        if stripped and _NON_DIGIT_RE.sub("", stripped):
            variants.append((stripped, region))
    elif digits.startswith("00"):
        variants.append(("+" + digits[2:], region))
    else:
        variants.append((token, region))
    # 不带 + 的中国号码：86 国家码 / 0+区号
    if region in (None, "CN") and digits.startswith("86") and len(digits) >= 12:
        variants.append(("+" + digits, "CN"))
    if region in (None, "CN") and digits.startswith("0") and 10 <= len(digits) <= 12:
        variants.append((digits, "CN"))
    return variants


# 号码形状 -> 附加区域候选（仅用于没写国际前缀、且行内没强线索的号码）
_SHAPE_REGIONS = [
    (re.compile(r"^8800\d{7}$"), ("RU", "KZ")),                        # 俄语区免费号码 8-800
    (re.compile(r"^1?[2-9]\d{2}[2-9]\d{6}$"), ("US", "CA")),          # 北美号码计划
    (re.compile(r"^[6-9]\d{9}$"), ("IN",)),                            # 印度 10 位手机（6-9 开头）
    (re.compile(r"^0[127]\d{9}$"), ("GB",)),                           # 英国 01/02/07 号（11 位）
]


def shape_regions(digits):
    for pattern, regions in _SHAPE_REGIONS:
        if pattern.match(digits):
            return list(regions)
    return []


def _has_intl_prefix(token):
    """号码是否自带国际前缀（有则区域线索无关紧要）。"""
    return bool(re.match(r"^\s*\+", token) or re.match(r"^\s*00\d", token))


def _classify(number, label):
    """返回 (bucket, reason)。"""
    meta_type = pn.number_type(number)
    carrier_type = carrier.number_type(number)
    nat = pn.national_significant_number(number)

    if number.country_code == 86 and _CN_MOBILE_RE.match(nat):
        return "mobile", "cn_mobile_segment"
    if meta_type == pn.PhoneNumberType.MOBILE or carrier_type == pn.PhoneNumberType.MOBILE:
        return "mobile", "libphonenumber_mobile"
    if meta_type == pn.PhoneNumberType.FIXED_LINE_OR_MOBILE:
        # 号码可携地区（美国/加拿大等）：手机与座机同号段，无法区分
        if label == "mobile":
            return "mobile", "ambiguous_mobile_label"
        return "landline", "ambiguous_default_landline"
    for name in ("FIXED_LINE", "TOLL_FREE", "SHARED_COST", "PREMIUM_RATE", "VOIP",
                 "PERSONAL_NUMBER", "PAGER", "UAN", "VOICEMAIL"):
        if meta_type == getattr(pn.PhoneNumberType, name):
            return "landline", "libphonenumber_" + name.lower()
    return None, "unknown_type"


def _format(number):
    """入库展示格式：国内手机 11 位纯号；国内固话 0区号-号码；特服号分组；境外 E.164。"""
    nat = pn.national_significant_number(number)
    if number.country_code == 86:
        if _CN_MOBILE_RE.match(nat):
            return nat
        if nat.startswith(_CN_HOTLINE_PREFIX):
            return f"{nat[:3]}-{nat[3:6]}-{nat[6:]}" if len(nat) == 10 else nat
        if nat[:2] in _CN_AREA2:
            return f"0{nat[:2]}-{nat[2:]}"
        if 9 <= len(nat) <= 11:
            return f"0{nat[:3]}-{nat[3:]}"
        return "0" + nat
    return pn.format_number(number, pn.PhoneNumberFormat.E164)


# ----------------------------------------------------------------------------------
# 对外接口
# ----------------------------------------------------------------------------------

def _repair_candidates(token, regions):
    """最后兜底：修复两种高频写法错误（仅在常规解析拿不到有效号码时启用）。

    1) 国家码后多写了国内冠字 0：+66 02 2055 9101、+234 0803 123 4567；
    2) 国家码重复书写：+86-8620-89269660、+91 91821905707、+45 45 55751919。
    两者都是“拨不通但人能看懂”的号码，直接丢掉会损失有效数据。
    """
    digits = re.sub(r"^(?:\+|00)", "", _NON_DIGIT_RE.sub("", token))
    out, seen = [], set()
    for region in regions:
        cc = str(pn.country_code_for_region(region) or "") if region else ""
        if not cc or not digits.startswith(cc):
            continue
        rest = digits[len(cc):]
        variants = {
            rest.lstrip("0"),                                              # 去国内冠字
            rest[len(cc):].lstrip("0") if rest.startswith(cc) else "",  # 去重复国家码
        }
        for national in variants:
            e164 = "+" + cc + national
            if len(national) >= MIN_DIGITS and e164 not in seen:
                seen.add(e164)
                out.append((e164, region))
    return out


def _resolve_number(token, regions, audit=None):
    """在一个 token 上逐个区域候选尝试，返回 (number, strict, hint_used)。"""
    number, strict, used = None, False, None
    for region in regions:
        for cand_tok, cand_region in _parse_variants(token, region):
            try:
                n = pn.parse(cand_tok, cand_region)
            except Exception:
                continue
            if not pn.is_possible_number(n):
                continue
            if pn.is_valid_number(n):
                return n, True, cand_region
            if number is None:
                number, strict, used = n, False, cand_region
        if number is not None and strict:
            break
    if not strict:
        for cand_tok, cand_region in _repair_candidates(token, regions):
            try:
                n = pn.parse(cand_tok, cand_region)
            except Exception:
                continue
            if pn.is_valid_number(n):
                if audit is not None:
                    audit.add("repaired_country_code_format", f"{token} -> {cand_tok}")
                return n, True, cand_region
    return number, strict, used


def parse_phone_cell(raw, hints, audit):
    """解析「电话」单元格，返回 (mobile_hits, landline_hits)。

    hints: 区域候选列表（国家 > 域名/邮箱域名 > 地址 > 公司名），仅对不带国际前缀的号码生效。
    """
    if isinstance(hints, str) or hints is None:
        hints = [hints] if hints else []
    hints = [h for h in hints if h] or [None]
    hint_tag = "/".join(h or "-" for h in hints)
    mobiles, landlines = {}, {}

    def emit(token, label, allow_split=True):
        digits = _NON_DIGIT_RE.sub("", token)
        regions = list(hints)
        if not _has_intl_prefix(token):
            for extra in shape_regions(digits):  # 无国际前缀时补号码形状线索
                if extra not in regions:
                    regions.append(extra)
        number, strict, used = _resolve_number(token, regions, audit)
        if number is None:
            # 粘连兜底：“+86-13968299505 24”这类多段文本，拆开后逐个解
            subs = [s for s in re.split(r"\s+", token.strip())
                    if MIN_DIGITS <= len(_NON_DIGIT_RE.sub("", s)) <= MAX_DIGITS]
            if allow_split and subs and (len(subs) > 1 or subs[0] != token.strip()):
                audit.add("glued_token_split", token)
                for sub in subs:
                    emit(sub, label, allow_split=False)
                return
            audit.add("unparsable", f"[{hint_tag}] {token}")
            return
        if not strict:
            # 只过了 possible 校验（长度对但号段/区号不存在，如 +016307702906）：属于废号，不入库
            audit.add("invalid_number_dropped", f"[{used or '-'}] {token}")
            return
        if len(set(digits)) <= 2:
            audit.add("abnormal_shape", token)
            return
        bucket, reason = _classify(number, label)
        if bucket is None:
            audit.add("unclassified", f"{token} type={_TYPE_NAME.get(int(meta_of(number)), 'UNKNOWN')}")
            return
        key = pn.format_number(number, pn.PhoneNumberFormat.E164)
        target = mobiles if bucket == "mobile" else landlines
        if key in target:
            return
        target[key] = PhoneHit(
            key=key, display=_format(number), bucket=bucket,
            region=pn.region_code_for_number(number) or "", reason=reason,
            # 只在国际前缀缺失时记录“实际生效的区域线索”
            hint="" if _has_intl_prefix(token) else (used or ""),
        )

    for label, content in _segments(raw):
        tokens = _tokens(content)
        digits_in_content = _NON_DIGIT_RE.sub("", content)
        if not tokens:
            if len(digits_in_content) >= MIN_DIGITS:
                audit.add("shape_rejected", f"[{hint_tag}] {content}")
            continue
        if label == "fax":
            # 纯传真号不可外呼，按废弃数据丢弃（并留审计）
            for t in tokens:
                audit.add("fax_number_dropped", t)
            continue
        for token in tokens:
            emit(token, label)
    return list(mobiles.values()), list(landlines.values())
