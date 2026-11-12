# -*- coding: utf-8 -*-
"""oss_pool_client.py —— 轻量阿里云 OSS 读写客户端（数据池专用）。

设计取舍：
  · 纯 HTTP + v1 头签名，不装 aliyun-oss-sdk（PyInstaller 包体积友好）
  · 签名算法：stringToSign = {METHOD}

{Content-Type}
{Date}
/{bucket}/{key}
    HMAC-SHA1 + Base64，与 qcollect/zz_ossupload.py 同一口径
  · 密钥解析链：环境变量 > config.json > 从 iOS .m 文件正则兜底读取
  · 只暴露数据池需要的四个操作：upload_json / download_json / list_keys / head_etag
"""
import base64
import hashlib
import hmac
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import formatdate, parsedate_to_datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

# ── 异常 ──────────────────────────────────────────────────────────────────────

class OSSPoolError(Exception):
    """OSS 操作失败（网络/签名/权限/不存在）。"""


class OSSCredentialError(OSSPoolError):
    """本机拿不到 OSS 凭证——这是**配置问题**，不是网络问题。

    必须与网络故障分开：两者在用户侧的表现一模一样（名单拉不回来 → 一律丐版），
    但下一步完全不同。混为一谈时，打包漏注入密钥这种「发包前就能查出来」的构建
    缺陷，会被文案推给终端用户去检查自家网络，并让管理员去查白名单，而真正该做的
    是重打一个带凭证的包（2026-11 外部机器全员丐版就是这个形态）。
    """


class OSSTransportError(OSSPoolError):
    """一次**没能拿到 HTTP 响应**的失败（连接 / 超时 / DNS / TLS 握手）。

    带 ``permanent`` 标记是为了让上层分清两种完全不同的重试：瞬时抖动（换个时刻
    就可能成）值得退避重试；确定性失败（证书链被安全软件/代理拦下）重试只是把第一
    次结论推迟几秒，而冷启动只有 3 秒有界等待——等不到结论，界面只能说「权限还在
    确认中」，把一个确定的答案拖成一次猜测。

    为什么转换必须发生在 :meth:`OSSPoolClient._send` 这一处：六个方法各写一份
    try/except 迟早漏一个，漏掉的那个会把裸 ``requests`` 异常抛给调用方，而调用方
    的分类口径认的是 OSSPoolError——结果又是「一句人话都没有」。
    """

    def __init__(self, message: str, permanent: bool = False):
        super().__init__(message)
        self.permanent = bool(permanent)


# ── 密钥解析 ──────────────────────────────────────────────────────────────────

# iOS 工程文件路径：只作为**开发态**兜底，让开发者不必手工往 config.json 里粘密钥。
# 打包态绝不读它——那是只对一台机器成立的绝对路径，正是「开发机永远正常、外部机器
# 永远连不上」这个缺陷的源头。
_M_FILE_CANDIDATES = [
    Path("/Users/jhnoplan/Desktop/AI云展/JuanYunApp/JuanYunApp/ThirdPart/AliYunImageUpload/AliyunOSSUpload.m"),
]

AK_ID_KEY = "oss_access_key_id"
AK_SECRET_KEY = "oss_access_key_secret"

# 模块目录：只作为 tool_config 不可用时的最后退路（开发态），不再是主解析口
_CONFIG_DIR = Path(__file__).resolve().parent

# 环境变量这一级的键名表：唯一口径是 tool_config.ENV_KEY_MAP，这里只是**它加载不了
# 时**的退路（打包清单漏收模块就会发生）。退路允许存在，但不许和主口径各说各话——
# 离线测试逐项比对两份表，改一边忘改另一边会当场红。
try:
    from tool_config import ENV_KEY_MAP as ENV_VAR_OF_KEY
except Exception:                                          # pragma: no cover
    ENV_VAR_OF_KEY = {
        AK_ID_KEY: "OSS_ACCESS_KEY_ID",
        AK_SECRET_KEY: "OSS_ACCESS_KEY_SECRET",
        "deepseek_api_key": "DEEPSEEK_API_KEY",
    }


def _env_get(key: str) -> str:
    """环境变量这一级（键名表与 tool_config 同源，见 :data:`ENV_VAR_OF_KEY`）。"""
    var = ENV_VAR_OF_KEY.get(key)
    return str(os.environ.get(var, "") or "").strip() if var else ""


def _cfg_get(key: str) -> str:
    """经统一解析链取一个配置项（环境变量 > 指定文件 > exe 同级 > 随包 > 开发目录）。"""
    try:
        import tool_config
        return tool_config.get(key)
    except Exception:
        # 解析链模块本身出问题也不能把错误吞成「没配」：先按同一张表看环境变量，
        # 再退化成直接读模块目录
        v = _env_get(key)
        if v:
            return v
        try:
            cfg = json.loads((_CONFIG_DIR / "config.json").read_text(encoding="utf-8"))
            return str(cfg.get(key) or "").strip()
        except Exception:
            return ""


def _cfg_source(key: str) -> str:
    try:
        import tool_config
        return tool_config.source_for(key)
    except Exception:
        # 退路也要能如实报出环境变量这一级，否则来源报告会漏掉真正生效的那一级
        return "环境变量" if _env_get(key) else "未知"


def _read_ak_from_m_file() -> Tuple[Optional[str], Optional[str]]:
    """从 iOS .m 文件正则提取 AccessKey / SecretKey（仅开发态兜底，密钥不入记忆）。"""
    ak_re = re.compile(r'AccessKey\s*=\s*@"([^"]+)"')
    sk_re = re.compile(r'SecretKey\s*=\s*@"([^"]+)"')
    for fp in _M_FILE_CANDIDATES:
        try:
            text = fp.read_text(encoding="utf-8")
            ak_m = ak_re.search(text)
            sk_m = sk_re.search(text)
            if ak_m and sk_m:
                return ak_m.group(1), sk_m.group(1)
        except Exception:
            continue
    return None, None


def resolve_credentials() -> Tuple[str, str]:
    """解析 OSS AK/SK，返回 (access_key_id, access_key_secret)。

    优先级全部由 tool_config 一条链决定：环境变量 > 统一配置链（exe 同级覆盖随包）
    > .m 文件（**仅开发态**）。这里不再自己判一遍环境变量：同一条规则写两处，
    改动时迟早只改一处，而两条规则同时成立时的「胜者」取决于调用顺序。
    成对规则也在 tool_config 里：只给半个密钥的那一级整级跳过，绝不跨级拼合。
    全部失败时抛 :class:`OSSCredentialError`，让调用方能把它和网络故障区分开。
    """
    ak_id = _cfg_get(AK_ID_KEY)
    ak_secret = _cfg_get(AK_SECRET_KEY)
    if ak_id and ak_secret:
        return ak_id, ak_secret

    # 开发态兜底：读本机 iOS 工程里的常量。打包态跳过——产物会被分发到没有
    # 这个文件的机器上，走到这一级就等于「只在开发者机器上可用的密钥」。
    try:
        import tool_config
        frozen = tool_config.is_frozen()
    except Exception:
        frozen = bool(getattr(sys, "frozen", False))
    if not frozen:
        ak_id, ak_secret = _read_ak_from_m_file()
        if ak_id and ak_secret:
            return ak_id, ak_secret

    where = _cfg_source(AK_ID_KEY)
    raise OSSCredentialError(
        f"未找到 OSS 密钥（配置来源：{where}）。"
        + ("这个打包版没有随包注入凭证，属发包问题：请在打包工作流里配置 "
           "OSS_ACCESS_KEY_ID / OSS_ACCESS_KEY_SECRET 两个 Secret 后重新出包；"
           "临时救急可把含 oss_access_key_id / oss_access_key_secret 的 "
           "config.json 放到程序同级目录。"
           if frozen else
           "请在 config.json 中同时配置 oss_access_key_id / oss_access_key_secret，"
           "或设置环境变量 OSS_ACCESS_KEY_ID / OSS_ACCESS_KEY_SECRET。")
    )


def credential_origin() -> str:
    """凭证来自哪一级（只有来源标签，绝不含密钥值），供自检与远程排障使用。

    直接问解析链命中了哪一级：来源与值必须出自同一条链，不然报告说的是文件、
    用的是环境变量，远程排障时两头对不上。
    """
    src = _cfg_source(AK_ID_KEY)
    if src not in ("未配置", "未知"):
        return src
    if src == "未知":
        return "环境变量" if (_env_get(AK_ID_KEY) and _env_get(AK_SECRET_KEY)) else "未配置"
    try:
        import tool_config
        if not tool_config.is_frozen() and _read_ak_from_m_file()[0]:
            return "开发机 iOS 工程文件"
    except Exception:
        pass
    return "未配置"


# ── 签名 ──────────────────────────────────────────────────────────────────────

def _oss_sign(method: str, content_type: str, date: str,
              resource: str, ak_secret: str) -> str:
    """OSS v1 头签名：HMAC-SHA1(stringToSign, secret) → Base64。

    stringToSign = {METHOD}

{Content-Type}
{Date}
{Resource}
    注意：canonicalizedOSSHeaders 只放 x-oss-* 头，普通头（如 Cache-Control）不参与签名。
    """
    string_to_sign = f"{method}\n\n{content_type}\n{date}\n{resource}"
    digest = hmac.new(
        ak_secret.encode("utf-8"),
        string_to_sign.encode("utf-8"),
        hashlib.sha1,
    ).digest()
    return base64.b64encode(digest).decode("utf-8")


def _gmt_date() -> str:
    """RFC 1123 格式的 GMT 时间（OSS 要求），带上已校正的时钟偏差。"""
    return formatdate(time.time() + _CLOCK_OFFSET_S, usegmt=True)


# ── 时钟偏差自愈 ─────────────────────────────────────────────────────────────
# OSS v1 头签名把请求时刻（Date）算进签名里，服务端只接受与标准时间相差 15 分钟
# 以内的请求。外部机器上这不算罕见情况：主板电池没电、系统「自动设置时间」被关掉、
# 虚拟机长时间挂起后恢复……任何一个都会让**签名本身完全正确**的请求被 403 拒掉，
# 症状与「密钥失效」一模一样（都是 403），修法却完全相反。
# 口径对齐主流云 SDK（botocore / ossutil 同一做法）：读服务端回的时间算出本地偏差，
# 用校正后的时刻重新签名再试一次。只在服务端明说「时刻偏得太远」时才校正，其它 403
# 一次都不重试——那是密钥/权限问题，重试只会把启动拖慢。
_CLOCK_OFFSET_S = 0.0
_MIN_CORRECT_S = 30.0        # 比这更小的偏差不算故障，也不值得为它改状态
_SKEW_MARKERS = ("requesttimetooskewed", "difference between the request time")


def clock_offset() -> float:
    """当前生效的时钟校正量（秒，正数 = 本机慢于标准时间）。给自检与测试读。"""
    return _CLOCK_OFFSET_S


def clock_offset_text() -> str:
    """人话版偏差说明：没校正过就别写出一句看起来像故障的话。"""
    if abs(_CLOCK_OFFSET_S) < 1.0:
        return "未校正（本机时钟与标准时间一致）"
    mins = abs(_CLOCK_OFFSET_S) / 60.0
    return f"本机比标准时间{'慢' if _CLOCK_OFFSET_S > 0 else '快'} {mins:.0f} 分钟，已按服务端时间重新签名"


def _is_clock_skew(status: int, body: str) -> bool:
    """403 里唯一值得自己修的那一类：服务端明说请求时刻偏得太远。"""
    if status != 403:
        return False
    low = (body or "").lower()
    return any(m in low for m in _SKEW_MARKERS)


def _apply_server_date(date_header: str) -> Optional[float]:
    """按服务端 Date 校正本地偏差；认不出来或偏差太小就返回 None（绝不瞎校正）。"""
    try:
        parsed = parsedate_to_datetime(str(date_header or ""))
    except Exception:
        return None
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    offset = (parsed - datetime.now(timezone.utc)).total_seconds()
    if abs(offset) < _MIN_CORRECT_S:
        return None
    global _CLOCK_OFFSET_S
    _CLOCK_OFFSET_S = offset
    return offset


# ── 网络韧性：超时自适应 + 瞬时故障重试 ──────────────────────────────────────
# 固定 30s 超时对「一次 PUT 几十 MB 的十万级上传」是必失败配置：
# 超时按对象体积线性放大，并对连接类异常 / 5xx / 429 做指数退避重试。
# PUT 到同一个 key 是幂等覆盖写，重试不会产生重复对象——这是敢重试的前提。
_CONNECT_TIMEOUT_S = 10.0
_IO_BASE_S = 20.0            # 小对象基础读写时间
_IO_PER_MB_S = 12.0          # 每 MB 追加时间（按 85KB/s 保守上行估算再留余量）
_BULK_IO_S = 120.0           # 体积未知的大对象读写（下载/服务端复制）基线
_TIMEOUT_CAP_S = 300.0
_RETRYABLE_STATUS = (408, 429, 500, 502, 503, 504)
_MAX_RETRIES = 3


def _adaptive_timeout(nbytes: int, base: float = _IO_BASE_S) -> float:
    """按负载体积给出读超时（秒），上限 _TIMEOUT_CAP_S。"""
    mb = max(int(nbytes or 0), 0) / (1024.0 * 1024.0)
    return round(min(_TIMEOUT_CAP_S, base + mb * _IO_PER_MB_S), 1)


def _backoff(attempt: int) -> float:
    """第 attempt 次失败后的等待秒数（指数退避，封顶 4s）。"""
    return min(4.0, 0.6 * (2 ** attempt))


def _retry_sleep(attempt: int) -> None:
    time.sleep(_backoff(attempt))


def _is_permanent_failure(exc: Exception) -> bool:
    """确定性失败：重试不会改变结果，只会把第一次结论推迟好几秒。

    证书/握手类失败属于这一类——同一条 TLS 链路每次都会被同一个中间人拦下（安全
    软件网页过滤、公司代理、不受信任的根证书）。冷启动只有 3 秒有界等待，多跑两轮
    指数退避意味着界面在等待结束时还拿不到任何结论，只能说「权限还在确认中」：把一
    个确定的答案拖成一次猜测，比慢一点更糟。
    """
    return isinstance(exc, requests.exceptions.SSLError)


def _tmp_key_for(key: str) -> str:
    """原子上传的临时 key：把 .tmp_ 前缀加在**文件名**上而不是整个 key 上。

    list_keys 是按 basename 判断 .tmp_ 的，历史实现把前缀拼在 key 最前面
    （.tmp_1699_users/xxx/upload_a.json → basename 仍是 upload_a.json），
    过滤形同虚设：清理失败的残留会被搜索端当成正式上传文件读进合并。
    """
    head, sep, tail = key.rpartition("/")
    name = f".tmp_{int(time.time() * 1000)}_{tail}"
    return f"{head}{sep}{name}"


# ── 客户端 ────────────────────────────────────────────────────────────────────

class OSSPoolClient:
    """轻量 OSS 读写客户端，只封装数据池需要的四个操作。"""

    def __init__(self, bucket: str = "images6396289",
                 endpoint: str = "oss-cn-shenzhen.aliyuncs.com",
                 prefix: str = "0817899624/company_pool",
                 ak_id: str = "", ak_secret: str = ""):
        self.bucket = bucket
        self.endpoint = endpoint
        self.prefix = prefix.rstrip("/")
        self.base_url = f"https://{bucket}.{endpoint}"

        # 密钥：传参 > 自动解析
        if ak_id and ak_secret:
            self._ak_id, self._ak_secret = ak_id, ak_secret
        else:
            self._ak_id, self._ak_secret = resolve_credentials()

        self._session = requests.Session()
        self._session.headers["User-Agent"] = "B2BCollect-Pool/1.0"

    # ── 内部：构造带签名的请求头 ──

    def _auth_headers(self, method: str, key: str,
                      content_type: str = "") -> dict:
        date = _gmt_date()
        resource = f"/{self.bucket}/{key}"
        sig = _oss_sign(method, content_type, date, resource, self._ak_secret)
        return {
            "Date": date,
            "Authorization": f"OSS {self._ak_id}:{sig}",
            **({"Content-Type": content_type} if content_type else {}),
        }

    # ── 内部：出网唯一通道（时钟校正只写这一处）────────────────────────────

    def _send(self, method: str, url: str, sign, what: str = "请求",
              retries: int = _MAX_RETRIES, **kw):
        """出网唯一通道：签名 → 发送 → 该重来就重来（时钟校正 / 瞬时故障退避）。

        ``sign`` 是**零参回调**而不是算好的头：每次尝试都必须用**当下**的时刻重新
        签名，拿旧头重发只是把同一个 403 再收一遍。所有方法都走这里，因为
        「校正 + 重试」写在六个地方迟早漏一个，漏掉的那个会在一台时钟不准的机器上
        稳定失败，而症状看起来又像密钥问题。

        重试策略同样集中在这里。以前只有上传/下载各写一份退避重试，HEAD / DELETE /
        列举 / 服务端复制一次抖动就直接报错——同一个 bucket、同一条链路，凭什么这
        四种操作就该更脆弱。集中之后新增方法也自动带上韧性，不会再出现「漏写一份」。

        这里也是**唯一的异常转换点**：出网失败一律变成 :class:`OSSTransportError`
        （见其文档字符串），调用方不需要再认识 ``requests`` 的异常家族。
        """
        attempts = max(1, int(retries))
        skew_fixed = False
        resp = None
        for attempt in range(attempts):
            try:
                resp = self._session.request(method, url, headers=sign(), **kw)
            except requests.RequestException as e:
                permanent = _is_permanent_failure(e)
                # 确定性失败（证书/握手）一次都不重试：同一条 TLS 链路每次都会被
                # 同一个中间人拦下，退避只会把第一次结论推迟几秒，而冷启动只有 3 秒
                # 有界等待——等不到结论，界面只能说「权限还在确认中」。
                if not permanent and attempt + 1 < attempts:
                    _retry_sleep(attempt)
                    continue
                raise OSSTransportError(
                    f"{what}失败（{'加密连接' if permanent else '网络'}）: "
                    f"{type(e).__name__}: {e}", permanent=permanent) from e
            # 时刻偏出容忍带：按服务端时间校正后重签再试（一次进程只校一轮，不来回摆）
            if not skew_fixed and _is_clock_skew(resp.status_code, resp.text) and (
                    _apply_server_date(resp.headers.get("Date")) is not None):
                skew_fixed = True
                continue
            if resp.status_code in _RETRYABLE_STATUS and attempt + 1 < attempts:
                _retry_sleep(attempt)
                continue
            return resp
        return resp

    # ── 上传 JSON ──

    def upload_json(self, key: str, data: Any,
                    timeout: Optional[float] = None,
                    retries: int = _MAX_RETRIES) -> str:
        """PUT JSON 到 {prefix}/{key}，返回 ETag。

        key 会自动加上 prefix 前缀；data 必须是 JSON 可序列化的。
        超时按 body 体积自适应，连接类异常与 5xx/429 指数退避重试
        （同 key 覆盖写幂等，重试安全）。
        """
        full_key = f"{self.prefix}/{key}" if not key.startswith(self.prefix) else key
        body = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        ct = "application/json; charset=utf-8"
        url = f"{self.base_url}/{full_key}"
        read_to = timeout if timeout is not None else _adaptive_timeout(len(body))

        # 重试、退避、时钟校正、异常转换全在 _send 里（那里是出网唯一通道）；
        # 这里只负责把「拿到的响应」翻译成本方法的返回或错误。
        resp = self._send("PUT", url,
                          lambda: self._auth_headers("PUT", full_key, ct),
                          what="上传", retries=retries, data=body,
                          timeout=(_CONNECT_TIMEOUT_S, read_to))
        if resp.status_code in (200, 201):
            return resp.headers.get("ETag", "")
        raise OSSPoolError(f"上传失败 HTTP {resp.status_code}: {resp.text[:200]}")

    # ── 下载 JSON ──

    def download_json(self, key: str, timeout: Optional[float] = None,
                      retries: int = _MAX_RETRIES) -> Tuple[Any, str]:
        """GET {prefix}/{key} 的 JSON，返回 (data, etag)。

        对象不存在时抛 OSSPoolError（HTTP 404）。
        大对象（合并总表可达几十 MB）读超时自适应 + 瞬时故障重试。
        """
        full_key = f"{self.prefix}/{key}" if not key.startswith(self.prefix) else key
        url = f"{self.base_url}/{full_key}"
        read_to = timeout if timeout is not None else _BULK_IO_S

        resp = self._send("GET", url,
                          lambda: self._auth_headers("GET", full_key),
                          what="下载", retries=retries,
                          timeout=(_CONNECT_TIMEOUT_S, read_to))
        if resp.status_code == 404:
            raise OSSPoolError(f"对象不存在: {full_key}")
        if resp.status_code == 200:
            try:
                return resp.json(), resp.headers.get("ETag", "")
            except json.JSONDecodeError as e:
                raise OSSPoolError(f"JSON 解析失败: {e}")
        raise OSSPoolError(f"下载失败 HTTP {resp.status_code}: {resp.text[:200]}")

    # ── 列出 key ──

    def list_keys(self, sub_prefix: str = "") -> List[str]:
        """列出 {prefix}/{sub_prefix}/ 下的所有 key（分页取全量）。

        返回完整 key 路径（含 prefix）。
        """
        full_prefix = f"{self.prefix}/{sub_prefix}".rstrip("/") + "/"
        keys: List[str] = []
        continuation_token = ""

        while True:
            # 构造请求：签名走 bucket 级资源，时刻由 _gmt_date() 统一给（含时钟校正）
            def _list_headers() -> Dict[str, str]:
                date = _gmt_date()
                sig = _oss_sign("GET", "", date, f"/{self.bucket}/", self._ak_secret)
                return {"Date": date, "Authorization": f"OSS {self._ak_id}:{sig}"}

            params: Dict[str, str] = {
                "list-type": "2",
                "prefix": full_prefix,
                "max-keys": "1000",
            }
            if continuation_token:
                params["continuation-token"] = continuation_token

            url = f"{self.base_url}/"
            resp = self._send("GET", url, _list_headers, what="列举对象",
                              params=params, timeout=30)
            if resp.status_code != 200:
                raise OSSPoolError(
                    f"列出 key 失败 HTTP {resp.status_code}: {resp.text[:200]}"
                )

            # 解析 XML
            root = ET.fromstring(resp.text)
            ns = root.tag.split("}")[0] + "}" if "}" in root.tag else ""
            for elem in root.iter(f"{ns}Key"):
                if elem.text and not elem.text.split("/")[-1].startswith(".tmp_"):
                    keys.append(elem.text)

            # 分页
            is_truncated = root.find(f"{ns}IsTruncated")
            if is_truncated is not None and is_truncated.text == "true":
                next_token = root.find(f"{ns}NextContinuationToken")
                if next_token is not None and next_token.text:
                    continuation_token = next_token.text
                    continue
            break

        return keys

    # ── HEAD 获取 ETag ──

    def head_etag(self, key: str) -> str:
        """HEAD 请求获取对象的 ETag（用于检测是否有新上传）。

        不存在返回空字符串。
        """
        full_key = f"{self.prefix}/{key}" if not key.startswith(self.prefix) else key
        url = f"{self.base_url}/{full_key}"

        resp = self._send("HEAD", url,
                          lambda: self._auth_headers("HEAD", full_key),
                          what="查询对象", timeout=15)
        if resp.status_code == 404:
            return ""
        if resp.status_code != 200:
            raise OSSPoolError(
                f"HEAD 失败 HTTP {resp.status_code}: {resp.text[:200]}"
            )
        return resp.headers.get("ETag", "")

    # ── 便捷方法 ──

    def key_exists(self, key: str) -> bool:
        """对象是否存在。"""
        return bool(self.head_etag(key))

    def delete_key(self, key: str) -> None:
        """删除对象（数据池一般不用，但保留接口以备清理）。"""
        full_key = f"{self.prefix}/{key}" if not key.startswith(self.prefix) else key
        url = f"{self.base_url}/{full_key}"
        resp = self._send("DELETE", url,
                          lambda: self._auth_headers("DELETE", full_key),
                          what="删除对象", timeout=15)
        if resp.status_code not in (200, 204):
            raise OSSPoolError(
                f"删除失败 HTTP {resp.status_code}: {resp.text[:200]}"
            )

    def copy_key(self, source_key: str, target_key: str,
                 timeout: float = _BULK_IO_S) -> str:
        """服务端复制对象（用于原子上传：先写临时 key，再复制到正式 key）。

        返回新对象的 ETag。大对象的服务端复制同样不能用固定 30s 超时。
        """
        src_full = f"{self.prefix}/{source_key}" if not source_key.startswith(self.prefix) else source_key
        tgt_full = f"{self.prefix}/{target_key}" if not target_key.startswith(self.prefix) else target_key

        copy_source = f"/{self.bucket}/{src_full}"
        resource = f"/{self.bucket}/{tgt_full}"

        def _copy_headers() -> Dict[str, str]:
            # 每次现取时刻现签名：签名要包含 x-oss-copy-source，时钟校正之后
            # 也只有重算才能拿到贴着新时刻的 Date
            date = _gmt_date()
            string_to_sign = f"PUT\n\n\n{date}\nx-oss-copy-source:{copy_source}\n{resource}"
            digest = hmac.new(
                self._ak_secret.encode("utf-8"),
                string_to_sign.encode("utf-8"),
                hashlib.sha1,
            ).digest()
            return {
                "Date": date,
                "x-oss-copy-source": copy_source,
                "Authorization": f"OSS {self._ak_id}:{base64.b64encode(digest).decode('utf-8')}",
            }

        url = f"{self.base_url}/{tgt_full}"
        resp = self._send("PUT", url, _copy_headers, what="复制对象",
                          timeout=(_CONNECT_TIMEOUT_S, timeout))
        if resp.status_code not in (200, 201):
            raise OSSPoolError(
                f"复制失败 HTTP {resp.status_code}: {resp.text[:200]}"
            )
        return resp.headers.get("ETag", "")

    def upload_json_atomic(self, key: str, data: Any,
                           progress_callback=None) -> str:
        """原子上传：先写到临时 key，成功后复制到正式 key，最后删除临时文件。

        搜索端通过 list_keys 自动过滤文件名以 .tmp_ 开头的文件，因此中途断网
        不会导致搜索端读到半写文件（临时 key 与正式 key 同目录、仅文件名加
        .tmp_ 前缀，见 _tmp_key_for）。

        progress_callback: 可选，每步完成后调用 callback(step_text)。
        """
        tmp_key = _tmp_key_for(key)
        # 1. 上传到临时 key
        if progress_callback:
            progress_callback("正在上传数据文件…")
        self.upload_json(tmp_key, data)
        try:
            # 2. 服务端复制到正式 key
            if progress_callback:
                progress_callback("正在确认文件完整性…")
            etag = self.copy_key(tmp_key, key)
            # 3. 删除临时文件（非阻塞：清理操作不阻塞主流程）
            try:
                self.delete_key(tmp_key)
            except OSSPoolError:
                pass  # 清理失败不影响上传成功；list_keys 自动过滤 .tmp_
            return etag
        except OSSPoolError:
            # 复制失败时清理临时文件
            try:
                self.delete_key(tmp_key)
            except OSSPoolError:
                pass
            raise
