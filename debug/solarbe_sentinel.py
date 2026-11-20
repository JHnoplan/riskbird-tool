# scrape_solarbe_full.py 的无人值守外层哨兵（2026-11-04）
#
# 为什么需要它：索比光伏网按出口 IP 限流，采集中途会被「15 分钟内 3 次空白页」的
# 看门狗熔断收工（脚本自己会存好断点、导好 Excel 再退出）。剩下的 1073 片不可能
# 一趟跑完，人工「等半小时再敲一次命令」等于这件事永远做不完。本哨兵把重试循环
# 接管下来：每轮跑完看台账增量决定下一轮什么时候开跑，直到队列清零或预算耗尽。
#
# 判据只用台账本身（queue / records / done），不靠解析日志文本——日志文案会改，
# 台账结构才是这份脚本对外的稳定契约。
#
# 用法（后台跑，务必用 -u 让行缓冲立刻落盘）：
#   nohup .venv/bin/python -u debug/solarbe_sentinel.py >> debug/solarbe_sentinel.log 2>&1 &
# 可选环境变量：
#   SOLARBE_MAX_HOURS   总预算小时数（默认 96，到点收工，防止无限占着网络）
#   SOLARBE_FIRST_WAIT  首轮前先冷却的秒数（默认 1800：上一轮刚被熔断就立刻重发，
#                       只会把放行窗口再往后推；实测站方要 30 分钟以上才松）
#   SOLARBE_DELAY/JITTER 透传给采集脚本的请求基线节奏（默认 8.0/3.0）
import os, json, time, subprocess, pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent          # 仓库根当场推导，换机器不用改一行
PY = str(ROOT / ".venv" / "bin" / "python")
COLLECT = str(ROOT / "debug" / "scrape_solarbe_full.py")
STATE = ROOT / "debug" / "solarbe_full_state.json"
LOG_DIR = ROOT / "debug" / "solarbe_sentinel_rounds"
STATUS = ROOT / "debug" / "solarbe_sentinel_status.json"
LOCK = ROOT / "debug" / "solarbe_sentinel.lock"

MAX_HOURS = float(os.environ.get("SOLARBE_MAX_HOURS", "96"))
FIRST_WAIT = int(os.environ.get("SOLARBE_FIRST_WAIT", "1800"))
BASE_SLEEP, MAX_SLEEP = 600, 7200      # 有进展：10 分钟后再跑；连续无进展：最长退到 2 小时
STALL_LIMIT = 14                       # 连续这么多轮零增量就收工（换网络也没用时的止损）
ROUND_TIMEOUT = 6 * 3600               # 单轮上限 6 小时（正常 30~60 分钟就该被熔断叫停）


def now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def read_state():
    """读台账快照；文件还没生成或正被写坏时返回 None（本轮按无增量处理，绝不崩）。"""
    try:
        st = json.loads(STATE.read_text())
        recs = st.get("records", {})
        return {
            "records": len(recs),
            "queue": len(st.get("queue", [])),
            "done_details": sum(1 for v in recs.values() if v.get("done")),
            "list_req": st.get("list_req", 0),
            "detail_req": st.get("detail_req", 0),
        }
    except Exception:
        return None


def write_status(**kw):
    try:
        data = dict(updated_at=now(), **kw)
        tmp = STATUS.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1))
        os.replace(tmp, STATUS)      # 原子替换：外面随时 cat 这个文件都不会读到半截
    except Exception:
        pass


def main():
    # 单实例：两个哨兵同时跑＝同一 IP 双份请求量，只会双双被踢下线，必须挡掉。
    try:
        import fcntl
        fd = os.open(str(LOCK), os.O_CREAT | os.O_RDWR, 0o644)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print(f"[{now()}] 已有哨兵在跑（{LOCK} 被占用），本实例退出。", flush=True)
        return

    LOG_DIR.mkdir(exist_ok=True)
    t0 = time.time()
    sleep_s, stalls, rounds = 0, 0, 0
    print(f"[{now()}] 哨兵启动：预算 {MAX_HOURS:.0f} 小时，"
          f"节奏 {os.environ.get('SOLARBE_DELAY', '8.0')}s/请求；"
          f"台账 {read_state()}", flush=True)
    if FIRST_WAIT > 0:
        print(f"[{now()}] 首轮前先等 {FIRST_WAIT / 60:.0f} 分钟，给上一个熔断留冷却窗口", flush=True)
        deadline = time.time() + FIRST_WAIT
        while time.time() < deadline:
            time.sleep(min(30, deadline - time.time()))

    while True:
        left = MAX_HOURS * 3600 - (time.time() - t0)
        if left <= 0:
            print(f"[{now()}] 预算 {MAX_HOURS:.0f} 小时用尽，哨兵收工（断点已存，可再次启动接着跑）。", flush=True)
            break

        st_before = read_state()
        rounds += 1
        log = LOG_DIR / f"round_{rounds:03d}_{time.strftime('%m%d_%H%M%S')}.log"
        print(f"[{now()}] 第 {rounds} 轮开跑 → {log.name}"
              f"（当前 {st_before}，剩余预算 {left / 3600:.1f} 小时）", flush=True)
        with open(log, "ab", buffering=0) as fh:
            fh.write(f"=== 第 {rounds} 轮 {now()} ===\n".encode())
            try:
                subprocess.run([PY, "-u", COLLECT], stdout=fh, stderr=subprocess.STDOUT,
                               timeout=ROUND_TIMEOUT, cwd=str(ROOT))
            except subprocess.TimeoutExpired:
                fh.write("\n[哨兵] 本轮超 6 小时未收工，已强制结束（断点每片都落盘，不丢）\n".encode())
            except Exception as e:
                fh.write(f"\n[哨兵] 本轮启动异常：{e}\n".encode())

        st = read_state() or {}
        gained = 0
        if st and st_before:
            # 增量不能只算家数：剩下很多片是「已细分到底、标称 0」的死角片，
            # 一轮跑完十几片却零新增也属正常推进。只比家数会把这种健康轮误判成
            # 卡死，连误判 14 轮哨兵就自己收工了——所以队列前移也要计入增量。
            gained = ((st["records"] - st_before["records"])
                      + (st["done_details"] - st_before["done_details"])
                      + (st_before["queue"] - st["queue"]))

        if st and st.get("queue") == 0 and st.get("records") and st["done_details"] >= st["records"]:
            print(f"[{now()}] ✅ 全库采完：{st['records']} 家明细、联系方式全部补齐"
                  f"（列表 {st['list_req']} 次 / 详情 {st['detail_req']} 次）。哨兵收工。", flush=True)
            break

        if gained > 0:
            stalls, sleep_s = 0, BASE_SLEEP
            tail = "有增量"
        else:
            stalls += 1
            sleep_s = min(MAX_SLEEP, max(BASE_SLEEP, sleep_s * 2) if sleep_s else BASE_SLEEP * 2)
            tail = f"零增量（连续 {stalls} 轮）"
        write_status(rounds=rounds, sleep_next=int(sleep_s), stalls=stalls, **st)
        print(f"[{now()}] 第 {rounds} 轮结束：{tail}；台账 {st}；"
              f"下一轮 {'-' if not sleep_s else f'{sleep_s / 60:.0f} 分钟'} 后开跑", flush=True)

        if stalls >= STALL_LIMIT:
            print(f"[{now()}] 连续 {stalls} 轮零增量：这台出口 IP 已被站方持续冷却，"
                  f"再跑只会白等。哨兵收工——换网络/换出口 IP 后重新启动本脚本即可从断点接续。", flush=True)
            break

        # 分段睡：Ctrl-C 或外层叫停都能立刻退出，不必等完一整段冷却
        deadline = time.time() + sleep_s
        while time.time() < deadline:
            time.sleep(min(30, deadline - time.time()))


if __name__ == "__main__":
    main()
