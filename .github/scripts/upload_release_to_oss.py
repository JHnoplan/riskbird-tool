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
每份制品上传后过两道独立的复核才算发出去（口径见 _verify_published）：
  1) 服务端回执：简单 PUT 的 ETag 就是内容的 MD5，与本地算出的对一次——这一问
     回答的是「OSS 到底收下了什么」，不受任何中间层影响；
  2) 匿名复读：公开读直链真能读到、字节数与本地一致、Cache-Control 带上了。
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

# Windows runner 的控制台代码页是 cp1252：任何一句中文 print 都会在 print 的当下抛
# UnicodeEncodeError。收口放在**模块顶层**而不是 main() 里：这个脚本的中文输出不只在
# main()（upload_one 也要念复读取证），而第 41 条编码门判的是源码行号——它不接受
# 「运行时总会先调 main」这种调用顺序担保，因为它正是为这类担保已经碎过一次而立的。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

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


def _local_md5(path: str) -> str:
    """流式算本地 MD5（98MB 约 0.3s）：与服务端 ETag 对账用的就是它。"""
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _read_back(url: str, size: int, md5: str) -> str:
    """匿名复读公开直链：读不到、字节数不符、Cache-Control 丢失都算不过门。

    返回一句「凭什么算过了」的取证文本（打进日志，发版红时不用猜）。

    为什么每一次复读都带 `Accept-Encoding: identity`（2026-11-19 build 71 的根因，本机已
    可 100% 复现）：requests 默认发 `Accept-Encoding: gzip, deflate`，而 OSS 会对
    `application/json` 真的做 gzip 传输编码——响应里只有 `Content-Encoding: gzip`、
    **没有 Content-Length**；`.exe`/`.zip` 压不动，于是照旧带长度。旧写法拿
    `str(size) != HEAD 的 Content-Length` 硬比，结果就是「安装包全绿、发布清单必红」，
    而且红在外面那层重试里会把已经传成功的 98MB 再重传三遍。
    两个事实必须分开：「问不出长度」是传输层编码问题，「长度不符」才是内容坏了。
    这里用 identity 直接量**存储的真实字节**（与本地文件同一个口径，也是工具下载后要
    校验的那个东西）；真遇上不肯报长度的中间层，再用一次 `Range: bytes=0-0` 的 GET 从
    206 的 Content-Range 里问出总长（一个 1 字节的请求，不下整包）。
    """
    plain = {"Accept-Encoding": "identity"}
    head = requests.head(url, headers=plain, timeout=30)
    if head.status_code != 200:
        raise RuntimeError(f"直链读不到：HEAD {head.status_code}")
    remote = head.headers.get("Content-Length", "").strip()
    encoded = str(head.headers.get("Content-Encoding", "")).strip().lower()
    how = "HEAD 报出 Content-Length"
    if not remote:
        # 还是问不出长度：不等于内容不对，用一次极小的分段 GET 把真实总长问出来
        with requests.get(url, headers={"Range": "bytes=0-0", **plain},
                          stream=True, timeout=30) as part:
            cr = part.headers.get("Content-Range", "")
            if part.status_code == 206 and "/" in cr:
                remote = cr.rsplit("/", 1)[-1].strip()
                how = "分段 GET 的 Content-Range"
            elif part.status_code == 200:
                # 不支持 Range：整份读出来量字节（小文件才走到这里，清单就是这种）
                remote = str(len(part.content))
                how = "整份 GET 的字节数"
            else:
                raise RuntimeError(
                    f"直链问不出字节数：HEAD 无 Content-Length，分段 GET 又回 "
                    f"{part.status_code}（Content-Range {cr!r}）")
    elif encoded not in ("", "identity"):
        # 报了长度又说自己被传输层压过：那个长度是压缩包的大小，与本地文件不是
        # 同一个口径。宁可不采信这一轮数字，也不拿一个对不上的值当「一致」。
        raise RuntimeError(
            f"HEAD 的 Content-Length 带着 Content-Encoding={encoded!r}："
            "它量的是压缩后的字节，与本地文件不可比")
    if str(size) != str(remote):
        raise RuntimeError(f"直链字节不符：远端 {remote} 字节 / 本地 {size} 字节（{how}）")
    cc = head.headers.get("Cache-Control", "")
    if cc != "no-cache":
        raise RuntimeError(
            f"Cache-Control 没带上（实为 {cc!r}）：覆盖更新会被旧缓存顶掉")
    etag = head.headers.get("ETag", "").strip('"').lower()
    if etag and etag != md5:
        raise RuntimeError(f"直链内容与服务端回执不是同一份：ETag {etag} != 本地 md5 {md5}")
    return f"{how} 一致（{remote} 字节，ETag 对上）"


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
    md5 = _local_md5(path)
    written = False          # 这一份是否已确认写进 OSS（ETag 对上）
    for attempt in (1, 2, 3):
        try:
            if not written:
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
                ack = resp.headers.get("ETag", "").strip('"').lower()
                if ack and ack != md5:
                    # 只有「服务端收下的就不是这一份」才值得重传；复读到的边缘答案
                    # 不一致不算——那正是 build 71 重传三遍 98MB 的那条路。
                    raise RuntimeError(
                        f"OSS 回执 ETag {ack} 与本地 md5 {md5} 不是同一份")
                written = True
            # 写进去了 ≠ 发出去了：公开读直链要自己站得住。边缘一致性给几秒退避，
            # 不拿它去重传一份已经写好的大文件。
            for verify_try in (1, 2, 3, 4):
                try:
                    proof = _read_back(url, size, md5)
                    print(f"   └─ 复读取证：{proof}")
                    return url
                except Exception as e:
                    last_err = e
                    if verify_try < 4:
                        time.sleep(3 * verify_try)
            raise RuntimeError(str(last_err))
        except Exception as e:
            last_err = e
            if attempt < 3:
                time.sleep(5 * attempt)
    raise RuntimeError(
        f"{name} {'上传成功但复读不过门' if written else '上传'}失败（3 次）：{last_err}")


def main(argv: list) -> int:
    # 控制台编码已在模块顶层收口（见文件上方那段注释：它必须早于任何一句中文写控制台）
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
