"""采集任务索引与云展绑定状态的本地持久化。

为什么要有这个模块：界面上「导入其他」要列出历史采集任务（按采集时间倒序），
而界面拿到的是「这台机器上有哪些历史任务、绑的是哪个号」，这些必须在重启后
还在，所以不能放随包目录（Windows onefile 的 _MEI、Mac 只读转置卷都是退出即没）。

落点在 2026-11-13 从桌面搬到了状态目录（``tool_config.state_file``），原因不是
整洁，而是**双击打不开工具**：macOS 的隐私保护（「系统设置 → 隐私与安全性 → 文件
与文件夹」）把 ``~/Desktop`` 里的文件访问扣在内核里等用户点「允许」，而这两份文件
是在**建界面的路上**读的——用户没看见弹窗、或在远程会话里、或点了不允许，界面就
永远出不来，一行报错都没有。同一条命令从终端起却一切正常（那时 TCC 的主体是终端，
早已授权），所以 CI 与本机命令行永远复现不出来。纪律因此是：

  · **启动路径上的机器状态一律放非受保护目录**（``~/.b2bcollect/b2bcollect_state``
    这一族，与审计日志、启动台账同一个家）；
  · 桌面上的老位置继续认，但用**有边界的读**（最多等 1.2 秒），读不到就按没绑过
    处理，绝不让界面陪着系统弹窗等（见 :func:`_read_legacy`）；
  · 桌面仍然留着**会话产出**（``tool_dir`` 那些 xlsx）——那是用户主动采集时才写的、
    看得见摸得着的东西，弹一次授权窗是正常交互，不是启动障碍。

任务口径（与用户确认过）：一次「开始采集 → 采集完成」= 一个任务，任务名取本次
导入的采集文档名称；暂停、续采、切换数据源都不拆新任务；采集途中新增文件并入
当前任务（任务名会跟着更新成「xxx 等 N 个文件」）。
"""

import json
import os
from datetime import datetime
from pathlib import Path

try:                                    # 落点只许问 tool_config 一处（缺了就退到家目录那一级）
    import tool_config as _tc
    HAS_TOOL_CONFIG = True
except Exception:
    _tc = None
    HAS_TOOL_CONFIG = False

TOOL_DIR_NAME = "B2B三站企业数据自动采集工具"
TASK_INDEX_NAME = "_采集任务索引.json"
ACCOUNT_FILE_NAME = "_云展账号.json"
RAW_FILE_NAME = "企业数据_原始.xlsx"      # 会话目录里的原数据文件（自动保存固定名）


TASK_STATUS_RUNNING = "采集中"
TASK_STATUS_DONE = "已完成"
TASK_STATUS_STOPPED = "已停止"
TASK_STATUS_PAUSED = "已暂停"
# 能接着采的状态（界面「历史任务」列表里高亮提示用）：
# 已完成的任务不鼓动用户重跑（要重采请开新任务），但也不禁止。
RESUMABLE_STATUS = (TASK_STATUS_RUNNING, TASK_STATUS_STOPPED, TASK_STATUS_PAUSED)
PROGRESS_FILENAME = "采集进度.json"


# ── 路径 ────────────────────────────────────────────
_desktop_override = ""


def set_desktop_dir(path) -> None:
    """GUI 启动时把它判定的桌面目录交出来

    Windows + OneDrive 重定向下 `Path.home()/Desktop` 经常存在但不是用户真桌面，
    两边各算各的会导致“自动保存在 A、任务索引在 B”。
    """
    global _desktop_override
    _desktop_override = str(path) if path else ""


def tool_dir(desktop_dir=None) -> Path:
    """工具在桌面上的数据根目录（不存在则创建）"""
    chosen = desktop_dir or _desktop_override
    base = Path(chosen) if chosen else Path.home() / "Desktop"
    if not base.exists():
        for env in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
            od = os.environ.get(env)
            if od and (Path(od) / "Desktop").exists():
                base = Path(od) / "Desktop"
                break
    d = base / TOOL_DIR_NAME
    try:
        d.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return d


def state_path(name: str) -> Path:
    """机器状态文件的落点：与审计日志、启动台账、自检报告同一个家。

    只许问 tool_config 一处（口径见 ``tool_config.state_file``：候选链 + 写探针，
    且**永不在应用包内**）。它自己那一级写不进去时退到 ``~/.b2bcollect``——那正是
    本次要的目标：一个不需要用户在系统弹窗上点任何东西的目录。
    """
    if HAS_TOOL_CONFIG:
        try:
            return _tc.state_file(name)
        except Exception:
            pass
    return Path.home() / ".b2bcollect" / "b2bcollect_state" / name


def index_path(desktop_dir=None) -> Path:
    """任务索引的现行落点（状态目录，**不在桌面**，理由见模块头）。

    ``desktop_dir`` 这个参数保留着：老调用方与离线夹具都按位置传它，忽略它比
    让几十处调用点各写一遍 if 要诚实——参数已经不代表落点了。
    """
    return state_path(TASK_INDEX_NAME)


def account_path(desktop_dir=None) -> Path:
    return state_path(ACCOUNT_FILE_NAME)


def legacy_index_path(desktop_dir=None) -> Path:
    """老版本写在桌面上的那份任务索引（只为迁移而存在，不再往里写）。"""
    return tool_dir(desktop_dir) / TASK_INDEX_NAME


def legacy_account_path(desktop_dir=None) -> Path:
    """老版本写在桌面上的那份绑定账号（同上：只读、只搬、不再当第二份真源）。"""
    return tool_dir(desktop_dir) / ACCOUNT_FILE_NAME


# ── 通用读写（写盘一律临时文件 + 原子替换，避免半个坏文件） ──
def _read_json(path: Path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


# 读老位置最多等多久（秒）。这个数不是「性能调优」，是「界面最多为用户等一次多久」：
# macOS 的授权弹窗什么时候出现、有没有人点，都不是这个进程能决定的事。
LEGACY_READ_WAIT_SECONDS = 1.2
# 本进程里已经「等过但没等到」的老位置：记下来就不再重试。第二次 1.2 秒换不来任何
# 新信息（授权什么时候点是系统的事），却会让每次 check() 都拖一下界面。
_legacy_blocked = set()


def legacy_blocked() -> tuple:
    """这次运行里有哪些老位置没读出来（界面据此说明「为什么看不到历史绑定」）。"""
    return tuple(sorted(_legacy_blocked))


def reset_legacy_state_for_tests() -> None:
    """清掉「已卡住」的名单：离线夹具逐例独立，不许带上一次的结论。"""
    _legacy_blocked.clear()


def _legacy_exists_bounded(path: Path) -> bool:
    """老位置上到底有没有这个文件——这一问同样**有界**。

    ``stat`` 不像 ``open`` 那样是本轮实测被扣住的那一次，但判据不能建立在「通常
    不会」上：建立在「通常」上的判据，遇到一台特别的机器就变成无限等待，而那一次
    等待就排在界面出现之前。

    为什么值得单独问一次：没有这一步，每个**从没绑过号**的新用户每次启动都会去
    敲一次桌面（读不到也照样要等满 1.2 秒）。文件不在就直接跳过，一次系统调用都
    不发给那个目录。
    """
    if not HAS_TOOL_CONFIG:
        return path.exists()
    answered, value = _tc.call_bounded(lambda: path.exists(), LEGACY_READ_WAIT_SECONDS)
    return bool(value) if answered else False


def _read_legacy(path: Path, default):
    """**有边界地**读桌面老位置上的一份 JSON：没按时读到就当作没有。

    为什么必须包这一层（2026-11-13 本机实测）：受保护目录里的 ``open()`` 会被内核
    扣住等用户点「允许」，实测扣了 4 分钟以上，界面上一个字都没有。老位置的读因此
    只许发生在「等不到也能继续」的地方，且绝不允许出现在建界面之前的裸调用里。
    探针模块没加载上（那种包本来就是坏的）时退回直读——宁可可疑地慢，不可静默地错。
    """
    key = str(path)
    if key in _legacy_blocked:
        return default
    if not _legacy_exists_bounded(path):
        return default        # 没有这份文件：一次 open 都不必发给那个目录

    def _do():
        return _read_json(path, default)

    if HAS_TOOL_CONFIG:
        answered, data = _tc.call_bounded(_do, LEGACY_READ_WAIT_SECONDS)
        if not answered:
            _legacy_blocked.add(key)
            return default
        return data
    return _do()


def _adopt_legacy(legacy_path: Path, target_path: Path, default):
    """把老位置那份读出来、落到新位置，并删掉老的那一份。

    删老文件不是洁癖：**同时存在两份真源**时，下一次读到哪个由「新位置在不在」决定，
    而用户改绑、清绑、换机都会让新位置先没掉——老那份就把已经作废的绑定又捞回来。
    删不动（没授权、只读、被占用）不算失败：新位置已经落了盘，判据仍然只有一份。

    新位置**已经有**的时候一个字都不覆盖：那才是真源，老副本可能是几周前的等级
    （甚至是一个已经解绑的号）。这一支正常情况下走不到（调用方都先看新位置在不在），
    留着是为了两处调用之间的竞态，以及让这条纪律有一个能被测到的地方。
    """
    if _legacy_exists_bounded(target_path):
        try:
            legacy_path.unlink()
        except Exception:
            pass
        return default
    data = _read_legacy(legacy_path, default)
    if not data or data == default:
        return default
    if _write_json(target_path, data):
        try:
            legacy_path.unlink()
        except Exception:
            pass
    return data


def _write_json(path: Path, data) -> bool:
    tmp = Path(str(path) + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        return True
    except Exception:
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass
        return False


# ── 任务索引 ────────────────────────────────────────
def load_tasks(desktop_dir=None) -> list:
    data = _read_json(index_path(desktop_dir), [])
    if not isinstance(data, list) or not data:
        # 新位置**还没有这个文件**才去捞老位置：判据是「文件在不在」而不是「列表
        # 空不空」——用户把所有历史任务删干净之后，空列表是他自己的决定，不该被
        # 桌面上那份老索引复活。
        if not index_path(desktop_dir).exists():
            data = _adopt_legacy(legacy_index_path(desktop_dir), index_path(desktop_dir), [])
    return [t for t in data if isinstance(t, dict) and t.get("id")] if isinstance(data, list) else []


def save_tasks(tasks: list, desktop_dir=None) -> bool:
    return _write_json(index_path(desktop_dir), tasks or [])


def make_task_name(file_paths) -> str:
    """任务名以采集文档名称为主：单文件用文件名，多文件收成「A 等 N 个文件」"""
    names = [os.path.basename(p) for p in (file_paths or []) if p]
    if not names:
        return "未命名采集任务"
    if len(names) == 1:
        return names[0]
    return f"{names[0]} 等 {len(names)} 个文件"


def upsert_task(task: dict, desktop_dir=None) -> list:
    """按任务 id（=会话文件夹名）插入或覆盖一条任务，返回最新全量列表"""
    tasks = load_tasks(desktop_dir)
    for i, old in enumerate(tasks):
        if old.get("id") == task.get("id"):
            tasks[i] = {**old, **task}
            break
    else:
        tasks.append(task)
    save_tasks(tasks, desktop_dir)
    return tasks


def update_task(task_id: str, desktop_dir=None, **fields) -> bool:
    """只补字段到已存在的任务上（区别于 upsert_task：任务不存在时不会插一条空壳）"""
    if not task_id or not fields:
        return False
    tasks = load_tasks(desktop_dir)
    for t in tasks:
        if t.get("id") == task_id:
            t.update(fields)
            save_tasks(tasks, desktop_dir)
            return True
    return False


def folder_start_time(folder_name: str) -> str:
    """会话目录名 → “年-月-日 时:分:秒”，对不上格式返回空串。
    1688 工具目录名用 %Y%m%d_%H%M%S；同时兼容 riskbird 的 %Y-%m-%d_%H-%M-%S。"""
    name = str(folder_name)
    for fmt, out in (("%Y%m%d_%H%M%S", "%Y-%m-%d %H:%M:%S"),
                     ("%Y-%m-%d_%H-%M-%S", "%Y-%m-%d %H:%M:%S")):
        try:
            return datetime.strptime(name, fmt).strftime(out)
        except ValueError:
            continue
    return ""


def count_records(path: str) -> int:
    """数一下原数据文件里有多少条（不用 read_only，避开行数被静默截断的坑）"""
    from openpyxl import load_workbook

    try:
        wb = load_workbook(path, data_only=True)
    except Exception:
        return 0
    try:
        ws = wb.worksheets[0]
        n = 0
        for row in ws.iter_rows(min_row=2, values_only=True):
            if row and any(v is not None and str(v).strip() for v in row):
                n += 1
        return n
    except Exception:
        return 0
    finally:
        wb.close()


def backfill_tasks(desktop_dir=None) -> int:
    """把索引里没有的历史会话目录补登成任务

    不加这个就只能导入“装上本功能之后”采的数据，用户之前已经采好的那些会话
    会永远消失在「导入其他」列表里。目录名本身就是开始时间，条数从原数据文件数。
    采集文档名无从知道（当时没记），所以任务名用「历史采集 <时间>」。
    """
    base = tool_dir(desktop_dir)
    tasks = load_tasks(desktop_dir)
    known = {t.get("id") for t in tasks}
    added = 0
    try:
        children = sorted(base.iterdir(), reverse=True)
    except Exception:
        return 0
    for child in children:
        try:
            if not child.is_dir() or child.name in known or child.name.startswith((".", "~$")):
                continue
            raw = child / RAW_FILE_NAME
            if not raw.exists():
                continue
        except Exception:
            continue
        stamp = folder_start_time(child.name) or child.name
        count = count_records(str(raw))
        if not count:
            continue          # 空目录/没采到东西的不往列表里塞
        tasks.append({
            "id": child.name,
            "name": f"历史采集 {stamp}",
            "files": [],
            "started_at": stamp,
            "finished_at": stamp,
            "status": TASK_STATUS_DONE,
            "count": count,
            "folder": str(child),
            "raw_file": str(raw),
            "backfilled": True,
        })
        known.add(child.name)
        added += 1
    if added:
        save_tasks(tasks, desktop_dir)
    return added


def latest_task(desktop_dir=None):
    """最新任务 = 开始时间最大的一条（正在采集中的也算，它就是「最新」）"""
    tasks = sorted_for_import(load_tasks(desktop_dir))
    return tasks[0] if tasks else None


def sorted_for_import(tasks: list) -> list:
    """按采集开始时间倒序；时间相同的按 id（同样是时间戳目录名）倒序"""
    return sorted(tasks or [],
                  key=lambda t: (str(t.get("started_at") or ""), str(t.get("id") or "")),
                  reverse=True)


def new_task(task_id: str, file_paths, folder: str, count: int = 0,
             keywords=None, sites=None, params=None, kw_raw: str = "") -> dict:
    """新建任务条目。

    keywords/sites/params/kw_raw 是「续采」要用的配置快照（旧版本只记名字和条数，
    所以关掉工具就什么都不剩了）：
      keywords —— 本次**实际参与采集**的词表（已经智能解析展开后的）
      kw_raw   —— 用户在框里原始输入（回溯展开前的意图）
      sites    —— 勾选的站点 slug
      params   —— 采集间隔/相关性/增量等开关
    """
    return {
        "id": task_id,
        "name": make_task_name(file_paths),
        "files": [os.path.basename(p) for p in (file_paths or [])],
        "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "finished_at": "",
        "status": TASK_STATUS_RUNNING,
        "count": count,
        "folder": str(folder),
        "raw_file": os.path.join(str(folder), RAW_FILE_NAME),
        "keywords": [str(k) for k in (keywords or []) if str(k).strip()],
        "kw_raw": kw_raw or "",
        "sites": list(sites or []),
        "params": dict(params or {}),
    }


def progress_path(task_folder: str) -> str:
    """任务文件夹里的进度台账文件路径（与 parallel_session.PROGRESS_FILENAME 同一名字）。"""
    return os.path.join(str(task_folder or ""), PROGRESS_FILENAME)


def task_progress(task: dict) -> dict:
    """读回任务的实际进度（哪些词跑完了、每词净新增）。
    存在两个源头：索引里冷写的 ledger 字段（旧条目没有）与任务文件夹里的
    采集进度.json（权威、每次自动保存都刷）——以后者为准，前者兜底。"""
    if not isinstance(task, dict):
        return {}
    p = progress_path(task.get("folder") or "")
    if p and os.path.exists(p):
        data = _read_json(Path(p), {})
        if isinstance(data, dict) and isinstance(data.get("ledger"), dict):
            return data
    if isinstance(task.get("ledger"), dict):
        return {"ledger": task["ledger"], "keywords": task.get("keywords") or []}
    return {}


def task_yield_total(task: dict) -> int:
    """任务台账里的净新增总数（界面显示「累计已采」而不是只本轮）。"""
    total = 0
    for v in (task_progress(task).get("ledger") or {}).values():
        if isinstance(v, dict):
            total += sum(int(n or 0) for n in (v.get("yield") or {}).values())
    return total


def done_keywords(task: dict) -> int:
    """已完整跑完的关键词个数（跨站取并集：给用户看「上次跑到哪儿」最直白的一个数）。"""
    out = set()
    for v in (task_progress(task).get("ledger") or {}).values():
        if isinstance(v, dict):
            out |= {str(k) for k in (v.get("done") or [])}
    return len(out)


def finish_task(task_id: str, status: str, count: int = 0, desktop_dir=None) -> bool:
    """采集完成/停止时补上结束时间与条数；任务不存在返回 False"""
    if not task_id:
        return False
    tasks = load_tasks(desktop_dir)
    hit = False
    for t in tasks:
        if t.get("id") == task_id:
            t["status"] = status
            t["finished_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            if count:
                t["count"] = count
            hit = True
            break
    if hit:
        save_tasks(tasks, desktop_dir)
    return hit


# ── 原数据回读（导入时把历史任务的 Excel 读回成记录列表再清洗） ──
def read_records_from_xlsx(path: str) -> list:
    """读回自动保存写出的原数据表 → list[dict]

    注意：这里刻意不用 openpyxl 的 read_only 模式。本项目踩过坑——只读模式下
    某些导出文件维度信息不准，行数会被静默截断，导致导入客户库少数据。
    """
    from openpyxl import load_workbook

    if not path or not os.path.exists(path):
        raise FileNotFoundError(f"任务的数据文件不存在：{path}")
    wb = load_workbook(path, data_only=True)
    try:
        ws = wb.worksheets[0]
        rows = list(ws.iter_rows(values_only=True))
    finally:
        wb.close()
    if not rows:
        return []
    headers = ["" if h is None else str(h).strip() for h in rows[0]]
    records = []
    for row in rows[1:]:
        if row is None or all(v is None or str(v).strip() == "" for v in row):
            continue
        rec = {}
        for idx, head in enumerate(headers):
            if not head:
                continue
            rec[head] = "" if idx >= len(row) or row[idx] is None else str(row[idx])
        records.append(rec)
    return records


# ── 云展绑定账号 ─────────────────────────────────────
def account_data(desktop_dir=None) -> dict:
    """绑定记录的完整内容（现行落点 → 没有这个文件时才看老落点）。

    老落点那一次读是**有边界的**：升级后第一次启动会走到这里，而 macOS 上这可能
    正卡在系统授权弹窗里。等不到就按「这台机器还没绑过」继续开界面——绑定的代价是
    用户重点一次手机号，而挂住的代价是双击打不开工具，两件事不是一个量级。
    """
    p = account_path(desktop_dir)
    data = _read_json(p, {})
    if isinstance(data, dict) and data:
        return data
    if not p.exists():
        data = _adopt_legacy(legacy_account_path(desktop_dir), p, {})
    return data if isinstance(data, dict) else {}


def load_account(desktop_dir=None) -> str:
    return str(account_data(desktop_dir).get("phone") or "")


def save_account(phone: str, desktop_dir=None) -> bool:
    return _write_json(account_path(desktop_dir),
                       {"phone": phone, "bound_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})


def clear_account(desktop_dir=None) -> bool:
    """清绑：写一条**空手机号**的记录，而不是把文件删掉。

    删掉的话，下一次启动就又被判成「这个落点还从来没有过绑定记录」，于是又去捞桌面
    老位置那份——刚被用户解绑的号原地复活。留一个空记录，「解绑」这件事才是持久的。
    """
    return _write_json(account_path(desktop_dir),
                       {"phone": "", "cleared_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
