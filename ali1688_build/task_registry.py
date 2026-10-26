"""采集任务索引与云展绑定状态的本地持久化。

为什么要有这个模块：界面上「导入其他」要列出历史采集任务（按采集时间倒序），
而 exe 是 PyInstaller onefile，`Path(__file__).parent` 指向每次运行都会变的 _MEI
临时目录，放程序目录等于不持久。所以任务索引与绑定账号都落在用户一定看得见、
也一定跨重启存在的「桌面/企业数据自动采集工具/」里（与自动保存同一目录）。

任务口径（与用户确认过）：一次「开始采集 → 采集完成」= 一个任务，任务名取本次
导入的采集文档名称；暂停、续采、切换数据源都不拆新任务；采集途中新增文件并入
当前任务（任务名会跟着更新成「xxx 等 N 个文件」）。
"""

import json
import os
from datetime import datetime
from pathlib import Path

TOOL_DIR_NAME = "1688企业数据自动采集工具"
TASK_INDEX_NAME = "_采集任务索引.json"
ACCOUNT_FILE_NAME = "_云展账号.json"
RAW_FILE_NAME = "1688企业数据_原始.xlsx"      # 会话目录里的原数据文件（自动保存固定名）

TASK_STATUS_RUNNING = "采集中"
TASK_STATUS_DONE = "已完成"
TASK_STATUS_STOPPED = "已停止"


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


def index_path(desktop_dir=None) -> Path:
    return tool_dir(desktop_dir) / TASK_INDEX_NAME


def account_path(desktop_dir=None) -> Path:
    return tool_dir(desktop_dir) / ACCOUNT_FILE_NAME


# ── 通用读写（写盘一律临时文件 + 原子替换，避免半个坏文件） ──
def _read_json(path: Path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


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


def new_task(task_id: str, file_paths, folder: str, count: int = 0) -> dict:
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
    }


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
def load_account(desktop_dir=None) -> str:
    data = _read_json(account_path(desktop_dir), {})
    return str((data or {}).get("phone") or "") if isinstance(data, dict) else ""


def save_account(phone: str, desktop_dir=None) -> bool:
    return _write_json(account_path(desktop_dir),
                       {"phone": phone, "bound_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})


def clear_account(desktop_dir=None) -> bool:
    try:
        p = account_path(desktop_dir)
        if p.exists():
            p.unlink()
        return True
    except Exception:
        return False
