"""云展动力开放接口客户端（只用到接口 3/4/5/6）。

接口清单（详见《云展动力API.md》，均为服务端对接接口、不需要登录态）：
  3 GET  /api/out/member/exist/{phone}                 校验手机号是否为云展动力账号
  4 GET  /api/out/member/sms/group/page                查询该账号的客户库分组（分页）
  5 POST /api/out/member/sms/group/save?phone=         新建客户库分组
  6 POST /api/out/member/sms/client/info/import        Excel 导入客户到客户库/指定分组

设计要点：
1) 统一返回体 code/msg/data，code=0 才算成功；业务失败（code=1）时 HTTP 仍是 200，
   所以必须自己判 code，并把后端 msg 原样透给用户（"请输入正确的手机号"/"云展账号不存在"
   这些提示文案是后端定的，前端不要自造一套，否则和云展站内提示对不上）。
2) 手机号格式先本地校验再发请求：格式不对没必要占一次网络往返，且提示能即时出现。
3) 分组查询自动翻页取全量：界面要一次展示完整分组树，按 size=100 循环到 pages。
4) 上传文件单独构造：云展接口6 要求表头与它的模板完全一致（9 列），而工具里
   清洗后的入库文件是按客户《客户库数据导入模板》的 18 列双块结构写的，
   直接把那份文件传上去会因为列名重复/多余而读不到数据，必须另写一份 9 列表。
5) 字段超长后端不入库只计 failTotal，这里主动按上限截断并记日志，避免静默丢数据。
"""

import os
import re

import requests

API_BASE = "https://api.yzdli.com"

# 手机号：11 位、1 开头、第二位 3-9（与后端提示口径一致）
PHONE_RE = re.compile(r"^1[3-9]\d{9}$")

# 接口6 模板表头（顺序即列顺序，名称必须完全一致，否则该列不会被读取）
IMPORT_HEADERS = ("客户名称", "企业名称", "岗位", "手机号", "固话",
                  "邮箱", "微信号", "数据来源", "备注")

# 接口6 字段长度上限：超长记录后端不入库只计入 failTotal
FIELD_LIMITS = {"客户名称": 255, "手机号": 100, "邮箱": 255, "固话": 500,
                "岗位": 255, "企业名称": 255, "微信号": 255, "数据来源": 255,
                "备注": 2000}

GROUP_NAME_MAX = 10        # 接口5：群组名称最多 10 个字符
GROUP_DESC_MAX = 120       # 接口5：群组描述最多 120 个字符

# 联系方式单元格的分隔符（后端也是按这几个分隔符拆多号码的）
_CONTACT_SPLIT_RE = re.compile(r"[,，、;；/\|\n\r\t]+")


class YzdError(Exception):
    """面向用户的一句话错误（后端 msg 或网络异常的可读描述）"""


class YzdClient:
    """云展动力接口客户端。phone 为已绑定的云展手机号，所有调用都靠它定位账号。"""

    def __init__(self, phone: str = "", timeout: int = 20, log=None):
        self.phone = (phone or "").strip()
        self.timeout = timeout
        self._log = log or (lambda _m: None)

    # ── 底层请求 ──
    def _url(self, path: str) -> str:
        return API_BASE + path

    def _unwrap(self, resp, what: str):
        """校验 HTTP 与业务 code，返回 data；失败统一抛 YzdError"""
        if resp.status_code != 200:
            raise YzdError(f"{what}失败：接口返回 HTTP {resp.status_code}")
        try:
            body = resp.json()
        except ValueError:
            raise YzdError(f"{what}失败：接口返回的不是 JSON（可能被网关拦截）")
        if not isinstance(body, dict):
            raise YzdError(f"{what}失败：接口返回格式异常")
        if body.get("code") != 0:
            # 业务失败：后端 msg 就是给用户看的文案，原样透出
            raise YzdError(str(body.get("msg") or f"{what}失败"))
        return body.get("data")

    def _request(self, method: str, path: str, what: str, **kwargs):
        url = self._url(path)
        kwargs.setdefault("timeout", self.timeout)
        try:
            resp = requests.request(method, url, **kwargs)
        except requests.exceptions.Timeout:
            raise YzdError(f"{what}超时（{kwargs['timeout']}秒），请检查网络后重试")
        except requests.exceptions.ConnectionError:
            raise YzdError(f"无法连接云展服务器（{API_BASE}），请检查网络")
        except Exception as e:
            raise YzdError(f"{what}异常：{str(e)[:120]}")
        return self._unwrap(resp, what)

    # ── 接口3：校验手机号是否为云展动力账号 ──
    def check_phone(self, phone: str) -> bool:
        """格式正确且账号存在返回 True；否则抛 YzdError 带上后端提示文案"""
        phone = (phone or "").strip()
        if not PHONE_RE.match(phone):
            raise YzdError("请输入正确的手机号")
        data = self._request("GET", f"/api/out/member/exist/{phone}", "校验手机号")
        if data is True:
            self.phone = phone
            return True
        raise YzdError("云展账号不存在")

    # ── 接口4：分组列表（自动翻页取全量） ──
    def list_groups(self, foreign_flag: int = 0, group_name: str = "",
                    client_type: str = "1", page_size: int = 100,
                    max_pages: int = 20) -> list:
        params = {"phone": self.phone, "current": 1, "size": page_size,
                  "foreignFlag": int(foreign_flag), "clientType": client_type}
        if group_name:
            params["groupName"] = group_name
        records, page, pages = [], 1, 1
        while page <= min(pages, max_pages):
            params["current"] = page
            data = self._request("GET", "/api/out/member/sms/group/page",
                                 "查询客户库分组", params=params) or {}
            records.extend(data.get("records") or [])
            pages = int(data.get("pages") or 1)
            if not (data.get("records") or []):
                break
            page += 1
        return records

    # ── 接口5：新建分组 ──
    def create_group(self, group_name: str, foreign_flag: int = 0,
                     description: str = "") -> dict:
        name = (group_name or "").strip()
        if not name:
            raise YzdError("群组名称不能为空！")
        if len(name) > GROUP_NAME_MAX:
            raise YzdError(f"群组名称最多输入{GROUP_NAME_MAX}个字符！")
        body = {"groupName": name,
                "foreignFlag": int(foreign_flag)}
        if description:
            body["groupDescription"] = str(description)[:GROUP_DESC_MAX]
        data = self._request("POST", "/api/out/member/sms/group/save",
                             "新建客户库分组", params={"phone": self.phone},
                             json=body,
                             headers={"Content-Type": "application/json"})
        if not isinstance(data, dict) or not data.get("id"):
            raise YzdError("新建分组失败：接口未返回分组 id")
        return data

    # ── 接口6：Excel 导入客户库 ──
    def import_excel(self, file_path: str, group_id=None, foreign_flag: int = 0,
                     client_type: str = "1", timeout: int = 180) -> dict:
        if not os.path.exists(file_path):
            raise YzdError(f"待上传的 Excel 不存在：{file_path}")
        form = {"phone": self.phone, "foreignFlag": str(int(foreign_flag)),
                "clientType": str(client_type)}
        if group_id:
            form["groupId"] = str(group_id)
        with open(file_path, "rb") as fh:
            files = {"file": (os.path.basename(file_path), fh,
                              "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
            data = self._request("POST", "/api/out/member/sms/client/info/import",
                                 "导入客户库", data=form, files=files, timeout=timeout)
        result = data or {}
        return {"importTotal": int(result.get("importTotal") or 0),
                "saveTotal": int(result.get("saveTotal") or 0),
                "failTotal": int(result.get("failTotal") or 0)}


# ── 上传文件构造 ─────────────────────────────────
def _contact_key(value) -> str:
    """联系方式归一化：拆分/去重/排序，让 "A,B" 和 "B,A" 算同一个"""
    tokens = [t.strip() for t in _CONTACT_SPLIT_RE.split(str(value or "")) if t.strip()]
    return ",".join(sorted(set(tokens)))


def row_identity(row: dict) -> tuple:
    """同一条客户的判定：客户名称 + 手机号/固话/邮箱（不比企业名称，同名的不同
    公司归同一条也是云展自己的建模，我们只保证不把完全一样的行传两遍）
    """
    row = row or {}
    return (str(row.get("客户名称") or "").strip(),
            _contact_key(row.get("手机号")),
            _contact_key(row.get("固话")),
            _contact_key(row.get("邮箱")))


def dedupe_rows(rows: list, log=None) -> list:
    """批内去重（接口6 文档明写“接口不做重复校验”，传两遍客户库就多两条）

    重复行不再重复上传，但把两边的「数据来源」合并，不丢平台信息；
    注：只保证“这一次上传里”不重复，同一个任务导两次仍然会出双份。
    """
    log = log or (lambda _m: None)
    out, seen = [], {}
    dropped = 0
    for row in rows or []:
        key = row_identity(row)
        if key in seen:
            target = out[seen[key]]
            src_a = str(target.get("数据来源") or "")
            src_b = str(row.get("数据来源") or "")
            if src_b and src_b not in src_a:
                target["数据来源"] = ",".join(t for t in (src_a, src_b) if t)
            dropped += 1
            continue
        seen[key] = len(out)
        out.append(dict(row))
    if dropped:
        log(f"🧹 批内去重：{len(rows)} 条 → {len(out)} 条（剔除 {dropped} 条名称+联系方式完全重复的行）")
    return out


def clamp_fields(row: dict, log=None) -> dict:
    """按接口6 的字段长度上限截断，超长部分丢掉（后端是整条不入库，截断比丢数据好）"""
    out = {}
    for k, v in (row or {}).items():
        text = "" if v is None else str(v)
        limit = FIELD_LIMITS.get(k)
        if limit and len(text) > limit:
            if log:
                log(f"  ⚠ 字段「{k}」超出 {limit} 字符，已截断后上传")
            text = text[:limit]
        out[k] = text
    return out


def write_import_workbook(rows: list, file_path: str, log=None) -> int:
    """把清洗后的行写成云展接口6 专用的 9 列 Excel，返回写入行数

    只认 IMPORT_HEADERS 这 9 个列名：清洗结果里还带着「外呼备注」等模板列，
    传上去会因列名不在模板里被忽略，干脆不写，保持文件与接口文档一一对应。
    """
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "客户导入"
    ws.append(list(IMPORT_HEADERS))
    written = 0
    for row in rows or []:
        data = clamp_fields(row, log=log)
        values = [data.get(h, "") for h in IMPORT_HEADERS]
        if not any(str(v).strip() for v in values):
            continue          # 整行空白的后端也不会导入，这里直接不写
        ws.append(values)
        written += 1
    widths = {"客户名称": 16, "企业名称": 34, "岗位": 12, "手机号": 22,
              "固话": 20, "邮箱": 28, "微信号": 16, "数据来源": 12, "备注": 40}
    for idx, head in enumerate(IMPORT_HEADERS, start=1):
        ws.column_dimensions[ws.cell(row=1, column=idx).column_letter].width = widths.get(head, 14)
    wb.save(file_path)
    return written
