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
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import formatdate
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

# ── 异常 ──────────────────────────────────────────────────────────────────────

class OSSPoolError(Exception):
    """OSS 操作失败（网络/签名/权限/不存在）。"""


# ── 密钥解析 ──────────────────────────────────────────────────────────────────

_CONFIG_DIR = Path(__file__).parent
_CONFIG_FILE = _CONFIG_DIR / "config.json"

# iOS 工程文件路径（兜底读取 AK/SK）
_M_FILE_CANDIDATES = [
    Path("/Users/jhnoplan/Desktop/AI云展/JuanYunApp/JuanYunApp/ThirdPart/AliYunImageUpload/AliyunOSSUpload.m"),
]


def _load_config() -> dict:
    """读 config.json（不存在返回空 dict）。"""
    try:
        if _CONFIG_FILE.exists():
            return json.loads(_CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _read_ak_from_m_file() -> Tuple[Optional[str], Optional[str]]:
    """从 iOS .m 文件正则提取 AccessKey / SecretKey（兜底，密钥不入记忆）。"""
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

    优先级：环境变量 > config.json > .m 文件兜底。
    全部失败时抛 OSSPoolError。
    """
    # 1. 环境变量
    ak_id = os.environ.get("OSS_ACCESS_KEY_ID", "").strip()
    ak_secret = os.environ.get("OSS_ACCESS_KEY_SECRET", "").strip()
    if ak_id and ak_secret:
        return ak_id, ak_secret

    # 2. config.json
    cfg = _load_config()
    ak_id = (cfg.get("oss_access_key_id") or "").strip()
    ak_secret = (cfg.get("oss_access_key_secret") or "").strip()
    if ak_id and ak_secret:
        return ak_id, ak_secret

    # 3. .m 文件兜底
    ak_id, ak_secret = _read_ak_from_m_file()
    if ak_id and ak_secret:
        return ak_id, ak_secret

    raise OSSPoolError(
        "未找到 OSS 密钥。请在 config.json 中配置 oss_access_key_id / oss_access_key_secret，"
        "或设置环境变量 OSS_ACCESS_KEY_ID / OSS_ACCESS_KEY_SECRET。"
    )


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
    """RFC 1123 格式的 GMT 时间（OSS 要求）。"""
    return formatdate(usegmt=True)


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

        last_error = ""
        for attempt in range(max(1, int(retries))):
            # 每次尝试重新签名：Date 必须贴合本次请求时刻
            headers = self._auth_headers("PUT", full_key, ct)
            try:
                resp = self._session.put(
                    url, data=body, headers=headers,
                    timeout=(_CONNECT_TIMEOUT_S, read_to))
            except requests.RequestException as e:
                last_error = f"{type(e).__name__}: {e}"
                if attempt + 1 < retries:
                    _retry_sleep(attempt)
                    continue
                raise OSSPoolError(f"上传失败（网络）: {last_error}")
            if resp.status_code in (200, 201):
                return resp.headers.get("ETag", "")
            if resp.status_code in _RETRYABLE_STATUS and attempt + 1 < retries:
                last_error = f"HTTP {resp.status_code}"
                _retry_sleep(attempt)
                continue
            raise OSSPoolError(
                f"上传失败 HTTP {resp.status_code}: {resp.text[:200]}"
            )
        raise OSSPoolError(f"上传失败（重试 {retries} 次后）: {last_error}")

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

        last_error = ""
        for attempt in range(max(1, int(retries))):
            headers = self._auth_headers("GET", full_key)
            try:
                resp = self._session.get(
                    url, headers=headers, timeout=(_CONNECT_TIMEOUT_S, read_to))
            except requests.RequestException as e:
                last_error = f"{type(e).__name__}: {e}"
                if attempt + 1 < retries:
                    _retry_sleep(attempt)
                    continue
                raise OSSPoolError(f"下载失败（网络）: {last_error}")
            if resp.status_code == 404:
                raise OSSPoolError(f"对象不存在: {full_key}")
            if resp.status_code == 200:
                try:
                    return resp.json(), resp.headers.get("ETag", "")
                except json.JSONDecodeError as e:
                    raise OSSPoolError(f"JSON 解析失败: {e}")
            if resp.status_code in _RETRYABLE_STATUS and attempt + 1 < retries:
                last_error = f"HTTP {resp.status_code}"
                _retry_sleep(attempt)
                continue
            raise OSSPoolError(
                f"下载失败 HTTP {resp.status_code}: {resp.text[:200]}"
            )
        raise OSSPoolError(f"下载失败（重试 {retries} 次后）: {last_error}")

    # ── 列出 key ──

    def list_keys(self, sub_prefix: str = "") -> List[str]:
        """列出 {prefix}/{sub_prefix}/ 下的所有 key（分页取全量）。

        返回完整 key 路径（含 prefix）。
        """
        full_prefix = f"{self.prefix}/{sub_prefix}".rstrip("/") + "/"
        keys: List[str] = []
        continuation_token = ""

        while True:
            # 构造请求
            resource_key = f"{self.bucket}/"  # ListObjects 签名用 bucket 级资源
            date = _gmt_date()
            sig = _oss_sign("GET", "", date, f"/{self.bucket}/", self._ak_secret)
            headers = {
                "Date": date,
                "Authorization": f"OSS {self._ak_id}:{sig}",
            }

            params: Dict[str, str] = {
                "list-type": "2",
                "prefix": full_prefix,
                "max-keys": "1000",
            }
            if continuation_token:
                params["continuation-token"] = continuation_token

            url = f"{self.base_url}/"
            resp = self._session.get(url, headers=headers, params=params, timeout=30)
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
        headers = self._auth_headers("HEAD", full_key)
        url = f"{self.base_url}/{full_key}"

        resp = self._session.head(url, headers=headers, timeout=15)
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
        headers = self._auth_headers("DELETE", full_key)
        url = f"{self.base_url}/{full_key}"
        resp = self._session.delete(url, headers=headers, timeout=15)
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

        date = _gmt_date()
        copy_source = f"/{self.bucket}/{src_full}"
        resource = f"/{self.bucket}/{tgt_full}"
        # 签名包含 x-oss-copy-source 头
        string_to_sign = f"PUT\n\n\n{date}\nx-oss-copy-source:{copy_source}\n{resource}"
        digest = hmac.new(
            self._ak_secret.encode("utf-8"),
            string_to_sign.encode("utf-8"),
            hashlib.sha1,
        ).digest()
        sig = base64.b64encode(digest).decode("utf-8")

        headers = {
            "Date": date,
            "x-oss-copy-source": copy_source,
            "Authorization": f"OSS {self._ak_id}:{sig}",
        }
        url = f"{self.base_url}/{tgt_full}"
        resp = self._session.put(url, headers=headers,
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
