# -*- coding: utf-8 -*-
"""b2bcollect 打包专用：把 Actions Secrets 落成一份**随包**的 config.json。

为什么必须有这一步（2026-11 外部机器实机缺陷）：
  config.json 被 .gitignore 忽略——密钥绝不进仓库，这是不可让步的边界。代价是
  「仓库里没有配置文件」，而打包机上同样不存在开发机那份 iOS 工程里的硬编码常量。
  于是云端打出来的每个包都读不到权限服务凭证：外部机器一律被判成精简版，界面还
  把这件事说成「请检查网络」，谁都没往打包环节想。开发机恰好两条路都通，所以
  从第一次打包到现在都测不出来。

  大厂口径是把「产物运行需要的非公开配置」当成构建输入（build input），在构建
  机上现生成、随包分发、仓库里永远查不到；本脚本就是这个「现生成」的那一步。

安全约定：
  · 密钥值只从环境变量读，绝不打印、绝不写进日志、绝不进任何制品清单以外的地方；
  · 缺 OSS 凭证时**当场失败**——宁可发不出包，也不发一个「只有开发者能用」的包；
  · 允许 DEEPSEEK_API_KEY 缺省：它只影响扩展词功能（会如实降级为纯离线），
    不构成"这个包不能用"，所以不拿它卡构建，但在报告里如实标注未注入。

两种来源，同一个产物、同一份键名契约（免得 CI 包与本地包长成两个样子）：
  · 默认（CI）：值来自 Actions Secrets；
  · ``--from-local``（本机手跑打包）：值来自**运行时同一条解析链**（环境变量 /
    config.json / 开发机 .m 兜底）。这条是给 `debug/build_b2bcollect_mac_local.sh`
    用的——本地打包如果靠人肉复制密钥，早晚有一天会漏，漏了就是又一次
    「开发机一切正常，客户那边全是精简版」。
"""
import json
import os
import sys

# 第一件事：把 stdout 切到 UTF-8。Windows runner 上 Python 用**系统代码页**编码
# 控制台（英文系统是 cp1252），打印中文或 ✅ 会抛 UnicodeEncodeError —— 实机踩过：
# config.json 已经写好了，进程却死在打印成功话术那一行，非零退出把构建拦在门外，
# 日志里只剩一句 "codec can't encode character '\u2705'"，看起来像注入步骤坏了。
# 这里不 import tool_config：这一步必须在**产物模块全都可能是坏的时候**也能跑，
# 它就是用来诊断那一类故障的第一环，所以自己带这三行。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# 与 b2bcollect_build/oss_pool_client.py 的 AK_ID_KEY / AK_SECRET_KEY 必须同名：
# 配置文件里的键名是运行时的读取口径，两边不一致就等于没注入。
KEY_MAP = (
    ("OSS_AK_ID", "oss_access_key_id", True),
    ("OSS_AK_SECRET", "oss_access_key_secret", True),
    ("DEEPSEEK_API_KEY", "deepseek_api_key", False),
)

OUT_FILE = os.path.join("b2bcollect_build", "config.json")


def _from_local() -> int:
    """--from-local：用运行时同一条解析链取值，写出的文件与 CI 注入的完全同构。"""
    build_dir = os.path.join(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))), "b2bcollect_build")
    if build_dir not in sys.path:
        sys.path.insert(0, build_dir)
    try:
        import oss_pool_client as oss
        import tool_config as tc
    except Exception as e:
        print(f"::error::本机解析链模块加载失败：{type(e).__name__}: {e}")
        print("   · 说明 b2bcollect_build 不在位或依赖不全，这个脚本不该被搬走单跑。")
        return 1
    values = {}
    try:
        ak_id, ak_secret = oss.resolve_credentials()
        values["oss_access_key_id"] = ak_id
        values["oss_access_key_secret"] = ak_secret
    except Exception as e:
        print("::error::本机也拿不到 OSS 密钥，打出来的包外部机器一律精简版，禁止出包")
        print(f"   · {type(e).__name__}: {str(e).splitlines()[0][:200]}")
        print("   · 云端打包不需要这一步（走 Secrets），只有本机手跑打包才需要。")
        return 1
    ds = tc.get("deepseek_api_key")
    if ds:
        values["deepseek_api_key"] = ds
    return _write(values, source="本机解析链",
                  note="" if ds else "DeepSeek 密钥本机也没配，扩展词将按纯离线降级")


def _write(values: dict, source: str, note: str = "") -> int:
    """写随包配置：保留文件里已有的其它键，只更新这三个。

    整体覆盖会顺手把开发者本机 config.json 里的其它配置抹掉——那不是这个脚本的
    职责，它只负责「随包凭证」这一组键。
    """
    merged = {}
    try:
        if os.path.isfile(OUT_FILE):
            with open(OUT_FILE, encoding="utf-8") as f:
                old = json.load(f)
            if isinstance(old, dict):
                merged = old
    except Exception:
        merged = {}          # 坏文件不值得为它停下来，按新写入的为准
    merged.update(values)
    try:
        os.makedirs(os.path.dirname(OUT_FILE), exist_ok=True)
        with open(OUT_FILE, "w", encoding="utf-8") as f:
            json.dump(merged, f, ensure_ascii=False, indent=2)
    except OSError as e:
        print(f"::error::写入 {OUT_FILE} 失败：{type(e).__name__}")
        return 1

    # 只报键名与字节数：日志是会被所有人看到的地方，值一旦进去就再也收不回来
    print(f"✅ 已生成随包配置 {OUT_FILE}（来源：{source}）")
    print("   · 注入项：" + "、".join(sorted(values)))
    if note:
        print("   · " + note)
    print(f"   · 文件大小 {os.path.getsize(OUT_FILE)} 字节（只随包分发，不入库）")
    return 0


def main() -> int:
    if "--from-local" in sys.argv[1:]:
        return _from_local()

    values = {}
    missing_required = []
    for env_name, json_key, required in KEY_MAP:
        raw = (os.environ.get(env_name) or "").strip()
        if raw:
            values[json_key] = raw
        elif required:
            missing_required.append(env_name)

    if missing_required:
        # ::error:: 会被 Actions 标红并写进注解，比裸 echo 更容易被看到
        print("::error::缺少必需的 Actions Secrets：" + "、".join(missing_required))
        print("   这一步缺了就等于打出一个「外部机器一律精简版」的包，构建必须停。")
        print("   配置位置：仓库 Settings → Secrets and variables → Actions → "
              "New repository secret（键名与上面完全一致，值填阿里云 OSS 的 "
              "AccessKey ID / Secret，建议用只授予本工具前缀读写权限的 RAM 子账号）。")
        return 1

    return _write(values, source="Actions Secrets",
                  note="" if "deepseek_api_key" in values
                  else "DEEPSEEK_API_KEY 未配置，扩展词按纯离线降级")


if __name__ == "__main__":
    sys.exit(main())
