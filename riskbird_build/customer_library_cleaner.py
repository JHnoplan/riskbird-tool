"""清洗后客户库入库数据生成模块。

对齐《客户库数据导入模板.xlsx》的列结构，把三个平台采集的原始结果做
schema mapping + 数据质量校验 + 跨平台合并去重，输出可直接入库的 Excel。

字段映射（模板列 → 采集字段，按优先级）：
    客户名称 ← 法定代表人（真实联系人）?? 公司名
    企业名称 ← 公司名
    岗位     ← 客户名称取自法定代表人时填「法定代表人」，否则留空
    手机号   ← 电话（正则校验 ^1[3-9]\\\\d{9}$）
    固话     ← 电话（区号-号码 / 分机号归一化）
    邮箱     ← 邮箱（正则校验 + 去重）
    微信号   ← 源数据无此字段，留空
    数据来源 ← 数据来源（多平台命中时逗号拼接）
    外呼备注/备注 ← 状态异常说明 + 官网 + 地址摘要

清洗规则：
    1. 剔除「未找到」与「失败」记录；
    2. 同一公司跨平台合并为一条（字段取首个非空值，电话/邮箱取并集），
       避免入库侧重复；
    3. 电话、邮箱拆分为手机号 / 固话 / 邮箱三个独立字段并逐个校验格式；
    4. 文本归一化（去零宽字符与首尾空白、全角标点转半角）；
    5. 手机号与固话各保留 3 个，多个用 / 分隔，避免入库字段超长；
    6. 模板表头按列名动态定位，不写死列序；模板缺失时回退内置表头。
"""

import os
import re

DEFAULT_OUTPUT_NAME = "清洗后客户库入库数据.xlsx"

# 模板表头兜底（A-I 外呼块 / J-R 备注块），运行时优先读模板真实表头
FALLBACK_HEADER = [
    "客户名称", "企业名称", "岗位", "手机号", "固话", "邮箱", "微信号", "数据来源", "外呼备注",
    "客户名称", "企业名称", "岗位", "手机号", "固话", "邮箱", "微信号", "数据来源", "备注",
]

# 采集字段 → 模板列的取值优先级
SOURCE_FIELDS = {
    "公司名": ("公司名", "企业名称", "公司名称"),
    "法定代表人": ("法定代表人", "法人代表", "法人", "负责人"),
    "电话": ("电话", "联系电话", "更多电话"),
    "邮箱": ("邮箱", "邮箱地址", "电子邮箱"),
    "官网": ("官网", "网址", "企业网址"),
    "地址": ("地址", "注册地址", "通信地址"),
    "状态": ("状态",),
    "数据来源": ("数据来源",),
}

_TEMPLATE_SEARCH_NAMES = ("客户库数据导入模板", "客户库导入模板", "客户库数据模板")

_MOBILE_RE = re.compile(r"^1[3-9]\d{9}$")
# 固话：区号(3-4位) + 号码(7-8位) + 可选分机；也接受裸 7-8 位号码
_LANDLINE_RE = re.compile(r"^(?:0(\d{2,3}))?[-\s]?(\d{7,8})(?:[-\s转.]*(\d{1,6}))?$")
# 400/800 客服热线：不能按区号拆分，保留原样
_HOTLINE_RE = re.compile(r"^(0?)(400|800)[-\s]?(\d{3})[-\s]?(\d{4})$")
_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")
_EMAIL_EXTRACT_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_SPLIT_RE = re.compile(r"[;；,，、\s/|]+")
_PLACEHOLDER_RE = re.compile(r"^(-{1,}|暂无|未知|无|待补充|NULL|null|N/?A|见详情)$")
_SEP_CHARS = "/／|｜,，、;；-－–—\\s"

# 零宽字符与全角标点
_ZERO_WIDTH_RE = re.compile(r"[\u200b-\u200f\ufeff\u00ad]")
_FULLWIDTH_MAP = {ord("："): ":", ord("；"): ";", ord("，"): ",", ord("、"): "、",
                  ord("（"): "(", ord("）"): ")", ord("　"): " "}


def normalize_text(value) -> str:
    """文本归一化：非字符串转字符串、去零宽字符、压缩连续空白。"""
    if value is None:
        return ""
    text = str(value)
    text = _ZERO_WIDTH_RE.sub("", text)
    text = text.translate(_FULLWIDTH_MAP)
    text = re.sub(r"[ \t\u3000]+", " ", text)
    return text.strip()


def _get_field(record: dict, key: str) -> str:
    """按 SOURCE_FIELDS 优先级从记录里取第一个非空字段值。"""
    for name in SOURCE_FIELDS.get(key, (key,)):
        val = normalize_text(record.get(name, ""))
        if val and not _PLACEHOLDER_RE.match(val):
            return val
    return ""


def _clean_legal_rep(value: str) -> str:
    """法定代表人可能带「该方已注销 / 多自然人分隔」等脏值，取首个有效人名。"""
    if not value:
        return ""
    parts = [p.strip() for p in re.split(r"[/／|｜,，、;；]", value) if p.strip()]
    for p in parts:
        p = re.sub(r"\s+", "", p)
        if 2 <= len(p) <= 20 and not _PLACEHOLDER_RE.match(p):
            return p
    return re.sub(r"\s+", "", parts[0]) if parts else ""


def split_phones(raw: str):
    """把混合电话串拆成 (手机号列表, 固话列表)，逐个正则校验并格式化。"""
    mobiles, landlines = [], []
    if not raw:
        return mobiles, landlines
    # 先把空格/横线分隔的 400/800 热线归一为连字符形式，避免被分隔符拆开
    raw = re.sub(r"\b([48]00)[-\s]?(\d{3})[-\s]?(\d{4})\b", r"\1-\2-\3", normalize_text(raw))
    for token in _SPLIT_RE.split(raw):
        token = re.sub(r"[^\d+\-]", "", normalize_text(token))
        if not token or token in ("-", "--"):
            continue
        digits = token.replace("-", "")
        if _MOBILE_RE.match(digits):
            if digits not in mobiles:
                mobiles.append(digits)
            continue
        hm = _HOTLINE_RE.match(token) or _HOTLINE_RE.match(digits)
        if hm:
            hotline = f"{hm.group(2)}-{hm.group(3)}-{hm.group(4)}"
            if hotline not in landlines:
                landlines.append(hotline)
            continue
        m = _LANDLINE_RE.match(token) or _LANDLINE_RE.match(digits)
        if m:
            area, number, ext = m.group(1), m.group(2), m.group(3)
            if len(number) < 7:
                continue
            formatted = f"0{area}-{number}" if area else number
            if ext:
                formatted += f"转{ext}"
            if formatted not in landlines:
                landlines.append(formatted)
    return mobiles, landlines


def split_emails(raw: str):
    """邮箱拆分去重 + 格式校验，统一小写域名部分。"""
    result = []
    if not raw:
        return result
    for token in _SPLIT_RE.split(raw):
        email = normalize_text(token).strip("<>\"'()").lower()
        if not email:
            continue
        # 兼容 "a@b.com(公司)" / "a@b.com 官网" 之类粘连：提取所有合法邮箱
        for found in _EMAIL_EXTRACT_RE.findall(email):
            if found not in result:
                result.append(found)
    return result


def is_valid_record(record: dict) -> bool:
    """有效性判定：公司名非空，且状态不含 未找到/失败/异常/无结果/空壳。"""
    name = _get_field(record, "公司名")
    if not name:
        return False
    status = _get_field(record, "状态")
    for kw in ("未找到", "失败", "异常", "无结果", "不存在", "未匹配"):
        if kw in status:
            return False
    return True


def _first_non_blank(values):
    for v in values:
        if v:
            return v
    return ""


def merge_records(results: list):
    """跨平台按公司名合并去重：返回 (合并后的记录字典列表, 重复条数)。"""
    merged = {}
    order = []
    dup = 0
    for record in results:
        if not is_valid_record(record):
            continue
        name = _normalize_key(_get_field(record, "公司名"))
        if not name:
            continue
        source = _get_field(record, "数据来源") or "未知平台"
        if name in merged:
            dup += 1
            item = merged[name]
            if source not in item["sources"]:
                item["sources"].append(source)
            for key in list(SOURCE_FIELDS):
                if not item["fields"].get(key):
                    item["fields"][key] = _get_field(record, key)
            # 电话/邮箱取并集而非首个非空
            for key in ("电话", "邮箱"):
                more = _get_field(record, key)
                if more and key in item["fields"]:
                    base = item["fields"][key]
                    item["fields"][key] = (base + ";" + more) if base else more
        else:
            merged[name] = {
                "sources": [source],
                "fields": {key: _get_field(record, key) for key in SOURCE_FIELDS},
            }
            order.append(name)
    return [merged[n] for n in order], dup


def _normalize_key(name: str) -> str:
    """公司名匹配键：去空白与全角括号转半角，避免同名不同格式重复入库。"""
    key = re.sub(r"\s+", "", name)
    return key.replace("（", "(").replace("）", ")").upper()


def _join_limited(values, limit=3):
    return "/".join(list(values)[:limit])


def _remark(fields: dict):
    """备注：状态说明 + 官网 + 地址摘要，供外呼人员参考。"""
    pieces = []
    status = fields.get("状态", "")
    if status and status not in ("存续", "在业", "开业", "正常"):
        pieces.append(f"经营状态:{status}")
    site = fields.get("官网", "")
    if site:
        pieces.append(f"官网:{site}")
    addr = fields.get("地址", "")
    if addr:
        pieces.append(f"地址:{addr[:50]}")
    remark = " | ".join(pieces)
    return remark[:180]


def build_clean_rows(results: list, log=print):
    """把三平台原始结果转换为模板行数据：返回 [{模板列名: 值}] 列表。"""
    merged, dup = merge_records(results)
    rows = []
    no_mobile = 0
    for item in merged:
        fields = item["fields"]
        mobiles, landlines = split_phones(fields.get("电话", ""))
        emails = split_emails(fields.get("邮箱", ""))
        if not mobiles and not landlines:
            no_mobile += 1
        legal_rep = _clean_legal_rep(fields.get("法定代表人", ""))
        company = fields.get("公司名", "")
        customer = legal_rep or company
        remark = _remark(fields)
        row = {
            "客户名称": customer,
            "企业名称": company,
            "岗位": "法定代表人" if legal_rep else "",
            "手机号": _join_limited(mobiles),
            "固话": _join_limited(landlines),
            "邮箱": ";".join(emails[:3]),
            "微信号": "",
            "数据来源": ",".join(item["sources"]),
            "外呼备注": remark,
            "备注": remark,
        }
        rows.append(row)
    log(f"清洗完成：原始 {len(results)} 条 → 有效入库 {len(rows)} 条"
        f"（剔除无效 {len(results) - sum(1 for r in results if is_valid_record(r))} 条、"
        f"跨平台合并 {dup} 条、无联系方式 {no_mobile} 条）")
    return rows


def locate_template(start_dirs=None) -> str:
    """在常用目录里查找客户的入库模板文件，找到返回路径，否则空串。"""
    home = os.path.expanduser("~")
    dirs = list(start_dirs or []) + [
        os.getcwd(),
        os.path.dirname(os.path.abspath(__file__)),
        os.path.join(home, "Downloads"),
        os.path.join(home, "Desktop"),
        os.path.join(home, "Documents"),
    ]
    for d in dirs:
        if not d or not os.path.isdir(d):
            continue
        try:
            names = sorted(os.listdir(d))
        except OSError:
            continue
        for fn in names:
            if not fn.lower().endswith((".xlsx", ".xls")):
                continue
            # 跳过 Excel/WPS 打开时产生的临时锁文件（~$xxx / .~xxx / .xxx）
            if fn.startswith(("~$", ".~", ".")):
                continue
            stem = os.path.splitext(fn)[0]
            if any(k in stem for k in _TEMPLATE_SEARCH_NAMES):
                return os.path.join(d, fn)
    return ""


def read_template_layout(template_path: str, log=print):
    """读取模板表头，返回 (外呼块列索引, 备注块列索引, 完整表头)。

    列名在模板里出现两次（A-I 与 J-R），按出现顺序区分两块。
    找不到模板时使用内置表头兜底。
    """
    header = list(FALLBACK_HEADER)
    if template_path:
        try:
            from openpyxl import load_workbook
            ws = load_workbook(template_path, read_only=False, data_only=True).active
            first = [normalize_text(c.value) for c in next(ws.iter_rows(min_row=1, max_row=1))]
            if any(first):
                header = first
                log(f"已读取入库模板表头：{os.path.basename(template_path)}（{len(header)} 列）")
        except Exception as e:
            log(f"⚠ 读取模板失败，改用内置表头：{e}")

    pos = {}
    for idx, col in enumerate(header):
        pos.setdefault(col, []).append(idx)
    data_cols = ("客户名称", "企业名称", "岗位", "手机号", "固话",
                 "邮箱", "微信号", "数据来源")
    has_second_block = any(len(idxs) > 1 for idxs in pos.values())
    # 外呼块：以「外呼备注」所在列界定，同名列取其之前的第一次出现
    outbound_remark = "外呼备注" if "外呼备注" in pos else ("备注" if "备注" in pos else "")
    outbound_idx = pos[outbound_remark][-1] if outbound_remark else None
    outbound = {}
    for col in data_cols:
        idxs = pos.get(col) or []
        pick = [i for i in idxs if outbound_idx is None or i < outbound_idx]
        outbound[col] = (pick or idxs)[0]
    if outbound_remark:
        outbound[outbound_remark] = outbound_idx
    # 备注块：同名列取最后一组（模板 A-I / J-R 重复列头时落到第二块）
    remark_remark = "备注" if "备注" in pos else "外呼备注"
    remark_block = {}
    if has_second_block:
        for col in data_cols:
            if col in pos:
                remark_block[col] = pos[col][-1]
        if remark_remark in pos:
            remark_block[remark_remark] = pos[remark_remark][-1]
    return outbound, remark_block, header


def write_clean_excel(rows: list, filepath: str, template_path: str = "", log=print):
    """按模板列结构写出清洗后的入库数据 Excel（两块列头均填充，保证入库兼容）。"""
    from openpyxl import Workbook
    from openpyxl.styles import Font, Alignment, PatternFill, Border, Side

    outbound, remark_block, header = read_template_layout(template_path, log)
    wb = Workbook()
    ws = wb.active
    ws.title = "客户库"

    header_font = Font(bold=True, size=11, color="FFFFFF")
    header_fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
    thin = Border(*[Side(style="thin")] * 4)

    for col_idx, title in enumerate(header, 1):
        cell = ws.cell(row=1, column=col_idx, value=title)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = thin

    # 两块列头都填充同样数据：外呼块用「外呼备注」，备注块用「备注」
    def values_for(row, block):
        out = {}
        for col, idx in block.items():
            if col in ("外呼备注", "备注"):
                out[idx] = row.get("外呼备注") or row.get("备注", "")
            else:
                out[idx] = row.get(col, "")
        return out

    for r, row in enumerate(rows, 2):
        cells = {}
        for block in (outbound, remark_block):
            for idx, val in values_for(row, block).items():
                cells[idx] = val
        for idx in range(len(header)):
            cell = ws.cell(row=r, column=idx + 1, value=cells.get(idx, ""))
            cell.alignment = Alignment(vertical="top", wrap_text=True)
            cell.border = thin

    widths = {"客户名称": 16, "企业名称": 34, "岗位": 12, "手机号": 20,
              "固话": 20, "邮箱": 32, "微信号": 14, "数据来源": 12,
              "外呼备注": 40, "备注": 40}
    for idx, title in enumerate(header, 1):
        ws.column_dimensions[ws.cell(row=1, column=idx).column_letter].width = \
            widths.get(title, 16)

    ws.freeze_panes = "A2"
    wb.save(filepath)
    log(f"✅ 已生成清洗后入库数据：{filepath}（{len(rows)} 条）")
    return filepath
