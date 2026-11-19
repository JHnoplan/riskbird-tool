#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""upload_release_to_oss.py —— 制品覆盖上传到固定 OSS 直链（永久下载链接）。

固定 key 方案（链接永不变，每次发版只更新对象内容）：
  https://images6396289.oss-cn-shenzhen.aliyuncs.com/0817899624/tool_releases/<文件名>
只允许白名单内的文件名上链——文件名打错就等于私开一条新的永久链接，
永久链的锚点是文件名，必须收紧：
  B2bCollectToolSetup.exe / B2bCollectTool_Win_portable.zip / B2bCollectTool_Mac_arm64.zip
  latest_windows.json / latest_mac_arm64.json

清单（latest_*.json）与制品走同一个前缀、同一套「覆盖 + no-cache」：工具是照着
清单里的直链去下载制品的，清单本身若不是一条永久地址，整个自动更新就只能靠人
每次发版手工改客户端里写死的地址——那不是发版，是给每一台机器埋一个待修的点。
**每个平台一份自己的清单**，不是全仓一份 latest.json：两个 workflow 的 run_number
是两个独立计数器，一份清单装两个计数器必然有一边说错话（见 tool_update.MANIFEST_BY_KEY）。

为什么放 0817899624/tool_releases/：与数据池前缀 0817899624/company_pool
同目录不同前缀——数据池读取端 list_keys 只列自己前缀下的对象，制品物理上
进不了数据池的视野，不会被当成 upload_*.json 合并。

密钥解析链：环境变量 OSS_ACCESS_KEY_ID / OSS_ACCESS_KEY_SECRET（CI Secrets
同名注入）> b2bcollect_build 的 resolve_credentials（config.json / iOS .m
兜底，与数据池客户端同一口径）。

上传必须带 Cache-Control: no-cache——覆盖式更新不带它，中间缓存/浏览器会
拿旧版本顶新链接（图床活码踩过同一坑：链接一样、下载到的还是旧包）。

用法：python upload_release_to_oss.py <制品路径>...
每份制品上传后用匿名 HEAD 校验（200 且大小与本地一致）才算成功；
任何一份不过门则整体退出码 1（CI 里就是这一步红，GitHub 制品已在上一
步上传，不损失回退通道）。
"""
import base64
import hashlib
import hmac
import os
import sys
import time

import requests

BUCKET = "images6396289"
ENDPOINT = "oss-cn-shenzhen.aliyuncs.com"
RELEASE_PREFIX = "0817899624/tool_releases"
# 与 b2bcollect_build/tool_update.py 里的 ALLOWED_ARTIFACTS + MANIFEST_BY_KEY 逐项对齐；
# 这里**故意**留一份字面量而不是 import 那个模块：这一份是「允许往永久桶里写哪些
# key」的安全边界，必须能被 grep 直接看清，也不该因为读取端模块导入失败就连发布都发
# 不出去。两处各写一份的代价由离线判据 ⑯ 组逐字对拍兜住（少一个名字=有新版本却装不上）。
ALLOWED_NAMES = (
    "B2bCollectToolSetup.exe",
    "B2bCollectTool_Win_portable.zip",
    "B2bCollectTool_Mac_arm64.zip",
    "latest_windows.json",
    "latest_mac_arm64.json",
)
CONTENT_TYPES = {
    ".exe": "application/octet-stream",
    ".zip": "application/zip",
    # 清单必须是 application/json：中间层（CDN/浏览器/代理）按 Content-Type 决定要不要
    # 缓存与怎么解析，给成 octet-stream 时它会把它当一份可缓存的普通文件对待。
    ".json": "application/json",
}


def resolve_ak() -> tuple:
    """环境变量优先；不齐则借数据池客户端的解析链（config.json/.m 兜底）。"""
    ak_id = os.environ.get("OSS_ACCESS_KEY_ID", "").strip()
    ak_secret = os.environ.get("OSS_ACCESS_KEY_SECRET", "").strip()
    if ak_id and ak_secret:
        return ak_id, ak_secret
    build_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "b2bcollect_build")
    sys.path.insert(0, build_dir)
    from oss_pool_client import resolve_credentials
    return resolve_credentials()


def _gmt_date() -> str:
    from email.utils import formatdate
    return formatdate(usegmt=True)


def _sign(ak_secret: str, key: str, content_type: str) -> tuple:
    """OSS v1 头签名：PUT 只把 Content-Type/Date 纳入签名串，
    Cache-Control 是标准头不参与——服务端按收到的原文验签。"""
    from email.utils import formatdate
    date = _gmt_date()
    string_to_sign = f"PUT\n\n{content_type}\n{date}\n/{BUCKET}/{key}"
    digest = hmac.new(ak_secret.encode("utf-8"),
                      string_to_sign.encode("utf-8"),
                      hashlib.sha1).digest()
    return date, base64.b64encode(digest).decode("utf-8")


def upload_one(path: str, ak_id: str, ak_secret: str) -> str:
    name = os.path.basename(path)
    if name not in ALLOWED_NAMES:
        raise ValueError(f"文件名不在发布白名单，拒绝上永久链：{name}")
    if not os.path.isfile(path):
        raise ValueError(f"制品文件不在位：{path}")
    size = os.path.getsize(path)
    content_type = CONTENT_TYPES.get(
        os.path.splitext(name)[1].lower(), "application/octet-stream")
    key = f"{RELEASE_PREFIX}/{name}"
    url = f"https://{BUCKET}.{ENDPOINT}/{key}"

    last_err = None
    for attempt in (1, 2, 3):
        try:
            date, sig = _sign(ak_secret, key, content_type)
            with open(path, "rb") as fh:
                resp = requests.put(
                    url, data=fh,
                    headers={
                        "Date": date,
                        "Content-Type": content_type,
                        "Cache-Control": "no-cache",
                        "Content-Length": str(size),
                        "Authorization": f"OSS {ak_id}:{sig}",
                    },
                    timeout=(30, 1800))
            if resp.status_code not in (200, 201):
                raise RuntimeError(
                    f"HTTP {resp.status_code}: {resp.text[:200]}")
            # 匿名 HEAD 复读：公开读直链真实可下载且字节一致才算发出去
            head = requests.head(url, timeout=30)
            remote = head.headers.get("Content-Length", "")
            if head.status_code != 200 or str(size) != remote:
                raise RuntimeError(
                    f"直链校验不过：HEAD {head.status_code}，"
                    f"远端 {remote} 字节 / 本地 {size} 字节")
            cc = head.headers.get("Cache-Control", "")
            if cc != "no-cache":
                raise RuntimeError(
                    f"Cache-Control 没带上（实为 {cc!r}）：覆盖更新会被旧缓存顶掉")
            return url
        except Exception as e:
            last_err = e
            if attempt < 3:
                time.sleep(5 * attempt)
    raise RuntimeError(f"{name} 上传失败（3 次）：{last_err}")


def main(argv: list) -> int:
    # Windows runner 控制台代码页是 cp1252：下面往控制台写中文/符号
    # 会在 print 的当下抛 UnicodeEncodeError，先把 stdout/stderr 死锁成 UTF-8
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    if len(argv) < 2:
        print(__doc__)
        return 2
    ak_id, ak_secret = resolve_ak()
    if not ak_id or not ak_secret:
        print("❌ 拿不到 OSS 密钥（env OSS_ACCESS_KEY_ID/SECRET 与 config.json/.m 兜底都空）")
        return 1
    failed = 0
    for path in argv[1:]:
        try:
            url = upload_one(path, ak_id, ak_secret)
            size_mb = os.path.getsize(path) / 1048576
            print(f"✅ {os.path.basename(path)}（{size_mb:.0f} MB）→ 永久直链：{url}")
        except Exception as e:
            print(f"❌ {path}: {e}")
            failed += 1
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
