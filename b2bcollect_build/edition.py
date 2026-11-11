# -*- coding: utf-8 -*-
"""交付档位（edition）：同一份代码库对内外两种交付形态的**唯一真源**。

为什么要有这个模块
------------------
对外发版时，主界面上有若干入口暴露了内部能力：团队数据池（同步 / 搜索 / 批量
导入 / 全盘扫描）、跨运行去重记录清理、历史任务续采、内置行业知识库扩词、
风鸟与爱企查两路名单补采通道。这些功能**代码一行都不删**——它们是内部日常
在用的能力，删掉等于自断后路；需要消失的只有「入口」和「让人看出有入口的文案」。

对标大厂做法
------------
VS Code 用 product.json 的 capability 位决定功能是否出现在界面；Chrome 用
build flag 在**渲染层**决定是否创建控件；Android 的 user build 与 eng build 由
构建档位一次性决定，而不是每个 Activity 各自判断一遍；Google 的 per-user feature
flag、JetBrains 的订阅分级、AWS 的 IAM policy ∩ SCP 则解决另一半问题：**同一个
binary 按账号下发不同开关**。共同点被本模块逐条继承：

1. **档位只在一处判定**（``resolve()``），界面只是它的投影。几十个按钮各写一遍
   ``if lite`` 的必然结局是漏掉一两个入口——入口漏一个，隐藏就等于没做。
2. **隐藏 = 不创建控件、不加入布局**，不是 ``setVisible(False)``。后者仍占 Tab
   焦点顺序、仍能被 ``findChildren`` 遍历到、仍会因间距留下「这里本来有个按钮」
   的空洞；而且「先建再藏」会让任何一次重构里忘记藏起来的引用变成活入口。
3. **能力名而不是控件名**（``pool_ui`` 而不是 ``btn_pool_sync``）：按钮会改名会
   挪位置，能力名稳定；回归测试只需盯住能力矩阵，不必跟着界面文案一起改。
4. **配置读不懂时按最保守档跑**（fail-closed）：打包脚本把档位值写错，可接受的
   代价是「外部用户少看到几个功能」，不可接受的代价是「内部能力全暴露出去」。
   注意区分「没配」（默认 pro，日常开发机就该这样）与「配错」（一律降级）。
5. **档位不许散落在文案里**：对外文案由 ``title_suffix()`` 等同一份投影给出，
   避免标题写着「精简版」而按钮还是全的。
6. **磁盘路径只按构建档位投影**（``pool_cache_dir()``）：一个功能可以从界面上藏掉，
   但它在用户桌面上留下的那个叫得出名字的文件夹藏不掉——路径判定同样只放在本模块，
   不让调用点各写一次 ``if lite``。路径还必须**不依赖账号等级**：等级是从缓存文件里
   读出来的，而缓存文件放在哪儿又由等级决定，就成了自我依赖的死循环。
7. **能力 = 构建档位 ∩ 账号等级**（``_effective_caps()``）：构建档位决定「这个包里
   有没有这段代码的入口」，账号等级决定「这个账号能不能用」，两者取交集，谁低听谁。
   这样内部全功能包给外部低权限账号用、外部包给管理员用，都不必重新打包：
   发版只发一份产物，权限差异全部在运行时收敛。

能力矩阵
--------
``pool_ui``             顶部四个数据池按钮（导入历史 / 扫描本地 / 同步 / 搜索）
``pool_prompt``         采集完成后弹窗询问「是否同步到数据池」
``pool_silent``         采集完成后后台静默同步数据池（界面无感、磁盘留痕）
``clear_seen``          「清空历史种子库」按钮 + 复位对话框里的同名勾选项
``history_tasks``       「历史任务」按钮与续采选择弹窗
``auto_expand_ui``      「自动扩词」勾选框是否显示（**与扩词是否生效是两件事**，
                        生效值见 ``auto_expand_default()``）
``list_all_channels``   名单补采是否显示企知道以外的平台（风鸟 / 爱企查）
``acl_admin``           「权限管理」入口（看名单/等级/绑定设备、改等级、踢下线）

档位与等级的判定优先级
----------------------
1. 环境变量 ``B2B_EDITION``：开发机切换与 CI 打包都用它，不留残余文件。
   空值等同于未设置（``B2B_EDITION=`` 是常见写法，不该被当成「配错」）。
2. 随包标记文件 ``edition.json``（内容 ``{"edition": "lite"}``）：打包时用
   ``--add-data`` 放进包内，与模块同目录；PyInstaller 冻结态再兜底查一次
   ``sys._MEIPASS``。
3. 默认 ``pro``：内部日常版本，行为与改造前完全一致。

账号等级（``set_account_level()`` 由 access_guard 判定后回填）优先级：
1. 环境变量 ``B2B_ACL_LEVEL``：**仅源码运行有效**，冻结态（打包好的 exe/app）直接
   忽略。留它是为了开发机与离线测试能一行切等级；把它带进包里就等于发一个提权开关。
2. ``set_account_level()``：access_guard 从 OSS 名单解析出来的真实等级。
3. 未回填（``None``）：按**构建档位**跑。这不等于提权——等级只有在名单拉取成功后
   才知道，而拉取成功之前 ``check()`` 一律拒绝（fail-closed），窗口期内没有任何功能
   可用；界面先按构建档位摆出来，等级取到后若与已建界面不符，工具会提示重启生效。

本模块只读环境、不碰 Qt、不碰网络，因此可以被离线测试直接驱动。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

# ── 档位名（对外只有这两种；新增档位必须同时补 _CAPABILITIES，否则 has() 为假）──
PRO = "pro"
LITE = "lite"

# 已知档位按「权限从低到高」排列：解析失败时取第一个，即最保守的那一档。
_RESTRICTIVE_ORDER = (LITE, PRO)
KNOWN_EDITIONS = tuple(sorted(_RESTRICTIVE_ORDER))
MOST_RESTRICTIVE = _RESTRICTIVE_ORDER[0]

# ── 能力名 ──────────────────────────────────────────────────────────────────
CAP_POOL_UI = "pool_ui"
CAP_POOL_PROMPT = "pool_prompt"
CAP_POOL_SILENT = "pool_silent"
CAP_CLEAR_SEEN = "clear_seen"
CAP_HISTORY_TASKS = "history_tasks"
CAP_AUTO_EXPAND_UI = "auto_expand_ui"
CAP_LIST_ALL_CHANNELS = "list_all_channels"
CAP_ACL_ADMIN = "acl_admin"

ALL_CAPS = (
    CAP_POOL_UI, CAP_POOL_PROMPT, CAP_POOL_SILENT,
    CAP_CLEAR_SEEN, CAP_HISTORY_TASKS, CAP_AUTO_EXPAND_UI,
    CAP_LIST_ALL_CHANNELS, CAP_ACL_ADMIN,
)

# 能力矩阵：一张表看完两档差别。**互斥关系必须写死在这里**——pool_prompt 与
# pool_silent 同时为真会出现「弹完窗又静默传一遍」，同时为假则采集完成什么都不发生
# （数据静默丢失）。界面只问「这个能力有没有」，永远不自己推断档位。
#
# acl_admin 只挂在 pro：它是「管理别人权限」的入口，属于内部能力，丐版包里连代码
# 入口都不该存在（外部用户拿到包也翻不出这个按钮）。
_CAPABILITIES: Dict[str, Tuple[str, ...]] = {
    PRO: (
        CAP_POOL_UI, CAP_POOL_PROMPT, CAP_CLEAR_SEEN,
        CAP_HISTORY_TASKS, CAP_AUTO_EXPAND_UI, CAP_LIST_ALL_CHANNELS,
        CAP_ACL_ADMIN,
    ),
    LITE: (
        CAP_POOL_SILENT,
    ),
}

# ── 账号权限等级（0 最高）────────────────────────────────────────────────────
# 等级不是一套新能力体系，只是「这个账号允许用到哪一档能力」的上限：
#   0 管理员  全部功能 + 权限管理入口（改名单/改等级/看绑定设备/踢下线）
#   1 标准    全部功能，没有管理入口
#   2 受限    只有丐版那一组功能
# 后续要细分（比如「能用池子搜索但不能清空种子库」）只需往这张表里加一档，
# 界面与门禁都不用改——这正是把等级收敛进能力矩阵而不是散进 if 里的意义。
LEVEL_ADMIN = 0
LEVEL_FULL = 1
LEVEL_LITE = 2
LEVELS = (LEVEL_ADMIN, LEVEL_FULL, LEVEL_LITE)
LEVEL_NAMES = {LEVEL_ADMIN: "管理员", LEVEL_FULL: "标准", LEVEL_LITE: "受限"}

# 能力位与等级的人话名称：**只给界面文案用**，判据一律走 ``has()`` 与能力矩阵。
# 放在这里而不是写死在权限面板里，是为了矩阵与文案改一处就一起改（面板上那句
# 「等级 2 能干什么」如果和矩阵不一致，管理员就是照着假话在授权）。
CAP_LABELS = {
    CAP_POOL_UI: "数据池四个入口",
    CAP_POOL_PROMPT: "采集完成弹窗询问同步",
    CAP_POOL_SILENT: "采集完成后台静默同步",
    CAP_CLEAR_SEEN: "清空历史种子库",
    CAP_HISTORY_TASKS: "历史任务续采",
    CAP_AUTO_EXPAND_UI: "自动扩词勾选框",
    CAP_LIST_ALL_CHANNELS: "风鸟/爱企查名单补采",
    CAP_ACL_ADMIN: "权限管理入口",
}
LEVEL_DESCS = {
    LEVEL_ADMIN: "全部功能 + 权限管理入口",
    LEVEL_FULL: "全部功能（不能管理权限）",
    LEVEL_LITE: "只有精简版那一组功能",
}
for _c in ALL_CAPS:                   # 新增能力忘了配文案时也不至于 KeyError
    CAP_LABELS.setdefault(_c, _c)

_ALL_CAP_SET = frozenset(ALL_CAPS)
_LEVEL_CAPS: Dict[int, frozenset] = {
    LEVEL_ADMIN: _ALL_CAP_SET,
    LEVEL_FULL: _ALL_CAP_SET - {CAP_ACL_ADMIN},
    LEVEL_LITE: frozenset(_CAPABILITIES[LITE]),
}
# 等级 → 生效档位名（标题后缀等「档位级」文案跟着它走）
_LEVEL_EDITION = {LEVEL_ADMIN: PRO, LEVEL_FULL: PRO, LEVEL_LITE: LITE}

# 名单里**完全没有等级字段**（线上旧版 whitelist.json）时沿用的等级：旧行为本来就是
# 「在名单里就能用全部功能」，把 legacy 解释成 1 而不是 0，是为了不把管理权顺手发给
# 一个从没声明过管理权的账号。
LEGACY_ACCOUNT_LEVEL = LEVEL_FULL
# 等级字段存在、但查不到这个账号 → 最保守那一档（宁严勿宽，同 resolve() 的配错口径）
UNKNOWN_ACCOUNT_LEVEL = LEVEL_LITE

LEVEL_ENV_VAR = "B2B_ACL_LEVEL"

# 「自动扩词」是否**生效**。lite 隐藏了勾选框，但采集链路仍需要一个确定值，
# 所以生效值不能挂在控件上。
#
# 两档都取 True 是刻意的，不是没改：本工具有一条硬规矩——提示必须与实际行为一致。
# 若 lite 关掉扩词，外部用户用类别名只能采到十几家，产出与内部版差一大截（而你正是
# 要靠这些产出做后台同步）；若 lite「悄悄展开却不写回关键词框」，就变成「框里 1 个词、
# 实际跑 30 个词」，用户对着对不上的现象无从判断，比看见功能更糟。
# 因此 lite 保持展开 + 写回 + 明说，只隐藏「不想用就别用」的那个勾选框。
# 这里保留成表格形式，是为了以后要改口径时只有这一行需要动。
_AUTO_EXPAND_DEFAULT = {PRO: True, LITE: True}

ENV_VAR = "B2B_EDITION"
FLAG_FILENAME = "edition.json"

_RESOLVED: Dict[str, object] = {}     # {"edition": str, "reason": str}
_WARNINGS: Tuple[str, ...] = ()
# 当前账号的权限等级（由 access_guard 判定后回填）：{} 表示尚未判定
_ACCOUNT: Dict[str, object] = {}


# ── 档位解析 ────────────────────────────────────────────────────────────────

def _flag_files() -> Sequence[Path]:
    """随包标记文件的候选位置（模块同目录优先，冻结态兜底查 _MEIPASS）。"""
    cands = [Path(__file__).with_name(FLAG_FILENAME)]
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        p = Path(str(meipass)) / FLAG_FILENAME
        if p not in cands:
            cands.append(p)
    return cands


def _read_flag_file() -> Tuple[str, str]:
    """返回 (原始档位文本, 来源描述)；文件不存在返回 ("", "")。

    读不懂**不等于**没配：文件在、内容坏（空文件 / 写错字段 / 非 UTF-8）时返回
    一个必然非法的文本，让上层按最保守档跑。沉默地退回 pro 才是真事故——
    那意味着一次打包笔误就能把内部全功能发出去。
    """
    for p in _flag_files():
        if not p.is_file():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8-sig"))
        except Exception as e:
            return f"<坏文件:{type(e).__name__}>", str(p)
        if not isinstance(data, dict):
            return "<坏结构>", str(p)
        return str(data.get("edition") or "<缺 edition 字段>"), str(p)
    return "", ""


def resolve(force: bool = False) -> Dict[str, str]:
    """定档并缓存。返回 ``{"edition": ..., "reason": ...}``，reason 说清是谁决定的。

    reason 不是给用户看的，是给**打包我和回归测试**看的：一次发版到底是环境变量、
    包内文件还是默认值在起作用，出问题时必须能一句话回答。
    """
    global _RESOLVED, _WARNINGS
    if _RESOLVED and not force:
        return dict(_RESOLVED)

    warnings = []
    raw_env = (os.environ.get(ENV_VAR) or "").strip()
    if raw_env:
        raw, source = raw_env, f"环境变量 {ENV_VAR}"
    else:
        raw, source = _read_flag_file()
        if raw:
            source = f"标记文件 {source}"
        else:
            raw, source = "", ""

    if not raw:
        edition, reason = PRO, "默认档位（未设置环境变量与标记文件）"
    else:
        norm = raw.strip().lower()
        if norm in KNOWN_EDITIONS:
            edition, reason = norm, f"{source}={raw}"
        else:
            edition = MOST_RESTRICTIVE
            reason = f"{source}={raw} 不是已知档位，按最保守档 {MOST_RESTRICTIVE} 跑"
            warnings.append(f"[edition] {reason}")

    _RESOLVED = {"edition": edition, "reason": reason}
    _WARNINGS = tuple(warnings)
    for w in warnings:
        print(w, file=sys.stderr)      # 打包态 stderr 会进日志，不会无声吞掉
    return dict(_RESOLVED)


def build_edition() -> str:
    """**构建档位**：只由环境变量 / 随包标记决定，与登录了哪个账号无关。

    磁盘路径必须用它（``pool_cache_dir``），否则会出现「缓存放哪儿取决于缓存里
    写了什么」的自我依赖。
    """
    return str(resolve()["edition"])


def account_level() -> Optional[int]:
    """当前账号权限等级；``None`` = access_guard 还没判定出来（未绑定/未拉到名单）。"""
    lv = _ACCOUNT.get("level")
    return None if lv is None else int(lv)


def account_level_reason() -> str:
    """等级是谁定的（诊断与回归用，不进界面）。未判定时说清是按构建档位隐含取值。"""
    lv, reason = _level_raw()
    if lv is None:
        return f"等级未判定，按构建档位 {build_edition()} 隐含取 {effective_level()}"
    return reason


def _level_raw() -> Tuple[Optional[int], str]:
    """等级解析：环境变量（仅源码态）> 回填值 > 未判定。"""
    raw = (os.environ.get(LEVEL_ENV_VAR) or "").strip()
    if raw and not getattr(sys, "frozen", False):
        try:
            lv = int(raw)
        except ValueError:
            return None, f"环境变量 {LEVEL_ENV_VAR}={raw} 不是合法等级，按未判定处理"
        if lv in _LEVEL_CAPS:
            return lv, f"环境变量 {LEVEL_ENV_VAR}={lv}（仅源码运行生效）"
        return None, f"环境变量 {LEVEL_ENV_VAR}={raw} 不是已知等级，按未判定处理"
    lv = _ACCOUNT.get("level")
    if lv is None:
        return None, "等级未判定（未绑定或名单未取到）"
    return int(lv), str(_ACCOUNT.get("reason") or "")


def effective_level() -> int:
    """生效等级：未判定时取构建档位的隐含等级。

    未判定 ≠ 最保守，因为「取到等级之前什么都不能点」这件事由 check() 的
    fail-closed 保证（名单都没拉到就一律拒绝），界面用构建档位摆出来既不会
    让功能被点到，也不会给开发机与离线测试凭空换一套界面。
    """
    lv, _ = _level_raw()
    if lv is not None:
        return lv
    return LEVEL_ADMIN if build_edition() == PRO else LEVEL_LITE


def edition() -> str:
    """**生效档位名**：构建档位与账号等级取交集后的那一档。

    标题后缀、能力判定都用它；磁盘路径用 ``build_edition()``，两者故意不同名。
    """
    build = build_edition()
    if build == LITE:
        return LITE
    return _LEVEL_EDITION.get(effective_level(), LITE)


def is_lite() -> bool:
    return edition() == LITE


def is_pro() -> bool:
    return edition() == PRO


def is_lite_build() -> bool:
    return build_edition() == LITE


def _effective_caps() -> frozenset:
    """构建能力 ∩ 等级能力（再对互斥组归一次）。任何一层说不，就是没有。"""
    build_caps = frozenset(_CAPABILITIES.get(build_edition(), ()))
    level_caps = _LEVEL_CAPS.get(effective_level(), _LEVEL_CAPS[LEVEL_LITE])
    return _resolve_exclusive(build_caps & level_caps)


# 互斥组：同一件事的「打扰版」与「安静版」两种实现。构建档位和账号等级是从**两个
# 方向**切这一组的（pro 只给弹窗询问、等级 2 只允许静默同步），纯交集会把两边都切掉，
# 结局是「采集完成什么都不发生」——数据不回流，比少看见一个入口严重得多。矩阵里写死
# 的「不许同时为真」防的是重复同步，从来不是要两边都不做。
# 归一规则只有一条：这一组一个都不剩时，退回组里最后一个（安静那一版），条件是等级
# 允许它。等级不允许（例如以后新增「完全不许同步」那一档）就真的不给，规则不外扩。
_EXCLUSIVE_GROUPS: Tuple[Tuple[str, ...], ...] = (
    (CAP_POOL_PROMPT, CAP_POOL_SILENT),
)


def _resolve_exclusive(caps: frozenset) -> frozenset:
    out = set(caps)
    level_caps = _LEVEL_CAPS.get(effective_level(), _LEVEL_CAPS[LEVEL_LITE])
    for group in _EXCLUSIVE_GROUPS:
        if any(c in out for c in group):
            continue
        if group[-1] in level_caps:
            out.add(group[-1])
    return frozenset(out)


def has(cap: str) -> bool:
    """能力是否可用（构建档位与账号等级都要点头）。

    未知能力名一律返回 False（宁可少给，不可多给）：新增能力忘了登记矩阵时，
    结局是「看不见这个入口」而不是「所有人都能用」。
    """
    return cap in _effective_caps()


def matrix() -> Dict[str, Dict[str, bool]]:
    """完整能力矩阵（回归断言用它核对「两档互斥项没有同时为真」）。"""
    return {e: {c: c in caps for c in ALL_CAPS}
            for e, caps in _CAPABILITIES.items()}


def level_matrix() -> Dict[str, Dict[str, bool]]:
    """等级能力矩阵（回归用它断言「等级 2 拿不到 pool_ui」「等级 1 拿不到 acl_admin」）。"""
    return {str(lv): {c: c in _LEVEL_CAPS[lv] for c in ALL_CAPS} for lv in LEVELS}


# ── 账号等级回填（只有 access_guard 该调这里）───────────────────────────────

def set_account_level(level: Optional[int], account: str = "",
                      reason: str = "") -> bool:
    """access_guard 判定出等级后回填；返回**生效档位是否发生变化**（GUI 据此提示重启）。

    未知等级值（既不在矩阵里也不是 None）按最保守那一档处理，与 ``resolve()``
    对「档位配错」的口径完全一致：名单被人改坏时，代价是「少看到功能」。
    """
    global _ACCOUNT
    before = edition()
    if level is None:
        _ACCOUNT = {}
        return edition() != before
    lv = int(level)
    if lv not in _LEVEL_CAPS:
        lv = UNKNOWN_ACCOUNT_LEVEL
        reason = f"{reason}（等级 {level} 不是已知等级，按最保守档 {lv} 跑）"
    _ACCOUNT = {"level": lv, "account": str(account or ""),
                "reason": reason or f"账号 {account} 等级 {lv}"}
    return edition() != before


def clear_account_level() -> None:
    """解绑/换号时清掉旧账号的等级，避免上一个账号的上限继续生效。"""
    set_account_level(None)


# ── 界面投影（GUI 只调这一层，不自己写档位判断）──────────────────────────────

def title_suffix() -> str:
    """标题栏档位后缀。lite 必须让人一眼认出跑的是哪版——包括**给我自己**：
    没有可辨识的标记，就无法证明看到的界面确实是丐版。"""
    return "  ·  精简版" if is_lite() else ""


def pool_ui_visible() -> bool:
    """顶部四个数据池按钮是否创建。"""
    return has(CAP_POOL_UI)


def silent_sync_enabled() -> bool:
    """采集完成后是否走后台静默同步（与弹窗询问互斥，由能力矩阵保证）。"""
    return has(CAP_POOL_SILENT)


def prompt_sync_enabled() -> bool:
    """采集完成后是否弹窗询问同步。"""
    return has(CAP_POOL_PROMPT)


def visible_list_channels(slugs: Iterable[str], default_slug: str) -> Tuple[str, ...]:
    """名单补采通道在界面上的可见集合。

    不显示的通道**不会被勾选**，而「未勾选＝本轮不跑」是编排层的既有语义，所以这里
    只管界面，不去碰通道注册表：功能仍在代码里，档位放开即恢复显示。
    默认通道（企知道）永远保留——它是用户点名要留的那一路，缺了整个名单区就空了。
    """
    all_slugs = tuple(slugs)
    if has(CAP_LIST_ALL_CHANNELS):
        return all_slugs
    return tuple(s for s in all_slugs if s == default_slug) or all_slugs[:1]


def auto_expand_visible() -> bool:
    """「自动扩词」勾选框是否创建。"""
    return has(CAP_AUTO_EXPAND_UI)


def auto_expand_default() -> bool:
    """扩词是否**生效**。控件存在时由界面勾选决定；控件不存在时用这里给的确定值。

    这是「隐藏入口」和「关闭功能」的分界：档位只决定看不看得见，
    生效值必须有个不依赖控件的落点，否则 lite 下 ``isChecked()`` 无处可读。
    """
    return _AUTO_EXPAND_DEFAULT.get(edition(), True)


def clear_seen_visible() -> bool:
    return has(CAP_CLEAR_SEEN)


def history_visible() -> bool:
    return has(CAP_HISTORY_TASKS)


def acl_admin_visible() -> bool:
    """「权限管理」入口是否创建（只有构建 pro 且账号等级 0 才有）。"""
    return has(CAP_ACL_ADMIN)


def level_allows_admin(level: Optional[int]) -> bool:
    """**指定等级**是否有权限管理入口。

    权限面板改等级时要提示「把自己降下来就等于关掉这个面板」——这个判据必须问矩阵，
    不能在界面写 ``lv == 0``：以后加一档「副管理员」，界面那句比较就成了错的规则。
    """
    if level is None:
        return False
    return CAP_ACL_ADMIN in _LEVEL_CAPS.get(int(level), frozenset())


def caps_snapshot() -> Dict[str, bool]:
    """当前能力位快照。GUI 在**建界面那一刻**存一份，之后拿它和实时值对比。

    为什么需要它：等级是拉到名单才知道的，而界面在那之前就得摆出来。快照与实时值
    不一致，说明「按旧上限建好的控件里有多余入口」——收口与重启提示都以此为准。
    这里返回的是能力位而不是等级数字：界面拿到等级数字就会自己去比大小，判据又写了
    第二份（同 ``title_suffix()`` 只给文案不给档位名的理由）。
    """
    return {c: has(c) for c in ALL_CAPS}


def level_label(level: Optional[int] = None) -> str:
    """等级的中文说法（只给管理员的权限面板看，普通账号界面上不出现等级字样）。"""
    lv = account_level() if level is None else int(level)
    if lv is None:
        return "未判定"
    return LEVEL_NAMES.get(lv, f"等级 {lv}")


def level_summary(level: int) -> str:
    """等级的人话说明。权限面板与日志共用这一份文案：面板里那句「等级 2 能用什么」
    如果由界面自己拼，能力矩阵一改它就会开始撒谎。"""
    lv = int(level)
    if lv in LEVEL_DESCS:
        return LEVEL_DESCS[lv]
    caps = _LEVEL_CAPS.get(lv)
    if caps is None:
        return f"等级 {lv} 未登记在矩阵里，实际按最保守档 {UNKNOWN_ACCOUNT_LEVEL} 执行"
    named = [CAP_LABELS[c] for c in ALL_CAPS if c in caps]
    return "包含：" + ("、".join(named) if named else "（没有任何功能）")


def level_choices() -> List[Tuple[int, str]]:
    """管理员面板上的等级选项 ``[(等级值, 文案)]``，0 在最前。

    列出来的只有 ``LEVELS``——面板不该提供一个能填任意数字的输入框，写出矩阵外
    的等级只会得到「按最保守档执行」，那对管理员是意外而不是灵活。
    """
    return [(lv, f"{lv} · {LEVEL_NAMES.get(lv, '自定义')}：{level_summary(lv)}")
            for lv in LEVELS]


# ── 路径投影（数据往磁盘哪儿放，也由档位决定）────────────────────────────────

APP_DATA_NAME = "B2BCollect"


def _app_data_dir() -> Path:
    """各操作系统规范的应用数据目录（不是用户日常会翻的角落）。"""
    home = Path.home()
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / APP_DATA_NAME
    if sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA") or str(home / "AppData" / "Local")
        return Path(base) / APP_DATA_NAME
    # Linux/BSD 按 XDG 规范；没配就用 ~/.local/share
    return Path(os.environ.get("XDG_DATA_HOME") or str(home / ".local" / "share")) / APP_DATA_NAME


def app_data_dir() -> Path:
    """对外只读入口（权限模块也要往同一个角落里放它自己的文件）。"""
    return _app_data_dir()


def pool_cache_dir(default: str) -> str:
    """数据池本地缓存目录：**构建档位**决定「放桌面」还是「放应用数据目录」。

    这条管的不只是界面文案：lite 采集完会在缓存目录里落下 ``silent_sync.log`` 与
    指纹台账（还有池子的本地缓存/加速库），沿用桌面路径就等于在外部用户桌面上凭空
    长出一个名为「数据池缓存」的文件夹——文件夹不会撒谎，它比按钮诚实得多。

    pro 原样返回调用方给的默认路径：内部同事的排查脚本与既往缓存都在那儿，改路径
    等于让他们把整个池子重新下载一遍。

    这里刻意用 ``build_edition()`` 而不是 ``edition()``：目录位置若在登录前后漂移，
    就会出现「登录后找不到自己刚写的缓存」；而等级本身又是从这份缓存里读出来的，
    用生效档位定路径会构成自我依赖。
    """
    if not is_lite_build():
        return default
    return str(_app_data_dir() / "pool")


# ── 诊断（不进界面，只给打包脚本与回归测试）──────────────────────────────────

def describe() -> Dict[str, object]:
    """一次看全：构建档位、账号等级、生效档位、能力位、以及各自是谁定的。"""
    info = dict(resolve())
    info["build_edition"] = build_edition()
    info["edition"] = edition()
    info["level"] = effective_level()
    info["level_declared"] = account_level()
    info["level_reason"] = account_level_reason()
    info["caps"] = {c: has(c) for c in ALL_CAPS}
    return info


def resolve_warnings() -> Tuple[str, ...]:
    resolve()
    return _WARNINGS


def reset_for_tests() -> None:
    """清缓存。档位一旦被模块导入定死就无法在测试里切换，必须留这个口子；
    账号等级同理——它挂在模块全局上，不清会把上一个用例的等级带到下一个用例。"""
    global _RESOLVED, _WARNINGS, _ACCOUNT
    _RESOLVED = {}
    _WARNINGS = ()
    _ACCOUNT = {}


if __name__ == "__main__":      # `python -m edition`：打包前自检用
    d = describe()
    print(f"build edition = {d['build_edition']}   （{d['reason']}）")
    print(f"account level = {d['level']}  {LEVEL_NAMES.get(d['level'], '?')}"
          f"   （{d['level_reason']}）")
    print(f"effective     = {d['edition']}")
    for c in ALL_CAPS:
        print(f"  {'✔' if d['caps'][c] else '✘'} {c}")
    for w in d.get("warnings", ()) or _WARNINGS:
        print(w)
