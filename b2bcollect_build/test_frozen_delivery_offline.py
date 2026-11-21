# -*- coding: utf-8 -*-
"""test_frozen_delivery_offline.py —— 打包态交付链离线测试（凭证链 / 故障分类 / 发包门禁）。

覆盖：
  A. tool_config 解析链：优先级、合并语义、空值不遮蔽、frozen 与源码两种形态
  B. OSS 凭证：环境变量 > 统一链 > 开发机 .m（**打包态必须禁用**），失败必须是
     OSSCredentialError 而不是被压成网络故障
  C. access_guard 故障分类：探测分类、文案分类、classify_reason 反查唯一口径、
     _must_client 管理入口分类
  D. 界面口径：凭证类失败不得说成「还在确认中」，被拒类失败不得指向白名单，
     认领失败不得承诺「网络恢复后自动补齐」
  E. DeepSeek 与缓存：打包态密钥读得到、缓存与配置写包外、pool_matcher 同源
  F. --acl-doctor：退出码分类，且不通的原因说清楚
  G. 交付链：Secret 注入 → 随包 config.json → 产物门禁；三个打包出口（Windows
     workflow / Mac workflow / 本机打包脚本）的 PyInstaller 清单逐项一致，
     .gitignore 里 config.json 仍在（密钥绝不入库）；取制品那一头的解包口径也在
     这里钉：单一口径（ditto 优先）、工具不交互、调用接退出码、下载前问空间、
     临时目录退出自收，口径的三条理由全部由真 unzip/ditto 实测撑住（不是论断）
  H. 本地状态落盘：首选目录写不进去要有备用落点，备用也没有就把后果说清楚；
     写失败不许静默（它是断网时唯一能用的身份凭据），且失败后能自愈回首选
  I. 时钟偏差与出网收口：签名时刻偏出容忍带时按服务端时间自愈重签（只重试一次）；
     加密连接被中途拦下单独一类；出网失败一律在唯一通道里转成可分类的错误
  J. 时间基准：进程内新鲜度与有界等待走单调时钟，落盘时间戳落在将来按不可信处理
     ——改系统时间不许让「被删的账号还能用」，也不许把 3 秒等待变成几小时卡死
  K. 控制台编码：CI 脚本与 CLI/自检入口在**第一次打印之前**就把 stdout 切到
     UTF-8 ——Windows 英文系统用 cp1252 编码控制台，中文和 ✅ 都编不出来，本轮
     CI 就是「config.json 已写好、进程却死在打印成功话术」这种红法
  L. 自检开关口径：CI 的 --acl-doctor、源码 CLI 的 --doctor、产物界面的分派三者
     必须是同一份写法（拼错时不许退化成「安静地去建界面」，那会让 runner 挂到
     job 超时）；退出码全表由故障分类表派生，帮助文本不许再手写一份清单
  M. workflow 步骤的脚本语言与这一步真正用的 shell 必须配套：Windows 上 run 步
     默认是 pwsh，bash 的 `[ -f x ] || { …; exit 1; }` 会被解析成类型表达式，
     ParserError 之后 PyInstaller 根本没启动——本轮 CI 的第二种红法。同组还管四件
     交付链上的「绿得没意义」：NSIS 版本以**真正编译那一次** makensis 自报的横幅为
     权威（拿假编译器真跑四种现实验活）、::error:: 注解必须是本步 shell 打出来的单行
     文本、批跑入口的总判定必须进退出码（它曾红着 6 条却退 0）、批跑入口自己的证据
     不许被嵌套调用删掉（同一落点上的两批互斥、夹具批跑关进私有沙箱，否则外层绿的
     套件会被判成红，而「完整输出」连 cat 都 cat 不出来）
  N. 打包清单硬门与采集通道完整性：site_adapters 声明的跨目录模块必须在三处入口
     逐条对上、且落对目录（缺一条或放错地方都在构建前红）；产物自检在打包态缺
     通道退 3，源码态只警告；界面只点名「该给却没给」的那几路（档位藏起来的
     功能不许被念出来）——本轮第三种红法：CI 全绿、产物却只剩企知道一路
  O. Windows 交付形态：三处入口都不许再出现 --onefile；装配校验器对合成产物树判得
     准（onedir 目录缺失/只有空壳 exe/混进旧单文件 exe/缺 runtime_paths 都要点名），
     校验不过就**不产出 zip**，运行期目录绝不进交付物，zip 顶层必须是一个目录，
     NSIS 脚本与使用说明必须 UTF-8 with BOM，CI 与制品校验器共用同一份判据；
     说明书里的每一条事实都要有代码或脚本撑着（每用户安装、安装目录、三个数据目录、
     崩溃日志名与界面同一个常量）
  P. 运行期文件落点：审计日志与列映射历史的默认值不再相对当前工作目录（切到别处
     也照样落在状态目录、且不落进随包目录），坏缓存/写不进去一律退化而不抛异常
  Q. 重启链路：给新实例的 cwd 绝不是包内目录（Windows 那句 base_library.zip
     FileNotFoundError 就是这么来的），重启参数不被误判成自检参数，不走 shell、
     不猜时长，由新实例观测旧 PID 真退出
  R. 一路通道失败的种类由「文件的主人」回答成因：模块已加载却在构造期抛
     FileNotFoundError 绝不能报成「安装包缺文件」（Mac 只读卷的实况就是这一类）
  S. Mac 只读副本：识别得出转置运行，并且能在界面上一次点完「移进应用程序文件夹」
     ——判定与执行分开，四种不许动手的形态（目标同名/版本不同/多份副本/读不到包
     标识）各有独立判据，执行只做同卷改名，其余一律交回访达
  T. 落点纪律门禁：--acl-doctor 必须在**发包前**认出「数据写进应用包里」——候选链
     和实际落点都查，打包态违规退 9（落点违规），源码态只报现状；这一类故意不进
     服务故障分类表（那三张表说的是服务为什么连不上），但必须进退出码全表和帮助
  U. 窗口冒烟门禁：--ui-smoke 让**产物自己**把界面真建一遍，三处打包入口都跑它，
     Mac 那两处还跑在只读副本上（App Translocation 现场）；正例退 0、反例退非零并
     留下崩溃日志——doctor 不碰 Qt，「CI 全绿、用户双击没界面」这一类只能由这条拦
  V. 运行形态开关的存取纪律：不许用位置元组存/还 sys.frozen 那组开关（还原错一位
     红的是隔了一整节的邻居，报的还是与产品毫不相干的栈）
  W. 启动台账与「真双击」判据：窗口有没有交出去由进程自己记账（时间|pid|阶段|细节），
     落点问产物（--state-dir），门禁按 pid+阶段 取字段；数窗口在锁屏/远程会话下对
     所有进程都报 0，只能当附加信息。「唤旧实例也算通过」那种绿法必须判红——它验的
     是上一份字节
  X. macOS 受系统隐私保护的目录不许把启动扣住：app 放在桌面时，候选链的「包旁」那一级
     就是 ~/Desktop/…，而定落点靠写探针——内核会把这次 open 扣住等用户点「允许」（实测
     四分钟以上），症状是「双击图标，什么也没发生，也没有报错」，而从终端起永远全绿
     （授权主体不同）。判据用假 open 精确扣住那一处：启动路径上任何一次访问都不许替
     用户决定要等多久，等不到也要把界面交出去；落点因此按语义离开受保护目录，老位置的
     数据只做一次性迁移（已有目标绝不覆盖）
  Y. 批跑入口的状态文件对拍：离线用例的隔离钩子挂在旧落点上时，假任务会写进开发机真实
     的 _采集任务索引.json——犯错的那一路一直是绿的，被顶红的是隔壁套件。批跑因此对
     「代码自己声明的状态文件」做前后快照对拍，改动进退出码；本节还抽出脚本里内嵌的那
     两段 Python 在假仓库上真跑一遍，验这道守卫确实点得出被改写/被删除的文件，且在
     什么都没改时报告为空（只查它在不在＝用一条假绿换掉另一条假绿）
  Z. set -e 静默带走交付链：硬门第 15 条拦的是「`VAR=$(pgrep …)` 这种正常情形也返回非零
     的裸赋值」——本机打包脚本第 6 道门（真双击验窗）就是这么安静地没跑，日志停第 5 步 ✅
     之后一行不剩，而它此前一直「绿」只因机器上恰好留着一枚旧实例。本节两头都验：现状干净、
     门抓得住原句（含 local/参数内替换/注释/set +e 四种不许误报的口径），并且**真起 bash**
     证明旧写法确实会吃掉后面的步骤、`|| true` 确实管用（推断也要有证据）。同一节还管它的
     另一半：装了 ERR 陷阱却没有 `set -E`（实测函数里的非零不触发陷阱）与「set 行大小写混排
     时判据整行认不出来」（第一版就在这里对出事那份脚本静默空转）
  AA. 大小写·Unicode 变体不敏感的文件系统：硬门第 16 条要保证同一份仓库在 mac/Windows 上
     检出的还是同一棵树。本节自己就是这条的由来——两个夹具写成 `…_noe.sh` / `…_noE.sh`，
     mac 上其实是同一个文件，判据读出隔壁那份内容，红了一条不相干的话，而 Linux CI 全绿。
     除了拿合成路径判仓库那一半，收尾还硬判本套件自己的夹具名不许撞车
  AB. pip 安装不许挂在一个外部门口：硬门第 18 条。2026-11-13 云端两个 run 同时红在
     `pip install <包名…> -i 清华源` 那一行（5 次 ReadTimeout 后 pip 只留一句
     `No matching distribution found`，一个字都没提「源不通」）。冗余与有界收进
     .github/scripts/pip_install.py，清单收进 .github/requirements/*.txt（Mac 与 Windows
     读同一份，「逐行对齐」的人肉同步就此失去立足点）。本节三层都验：判据接线、七种坏写法
     各红一次、拿假 pip **真起子进程**验回退顺序、成功即早停、每源有界、全挂才非零、命令行
     真的带上 --timeout / --retries / --no-input、子进程的 stdin 拿到的是 EOF，并做一次差分实测
     （同一行读 stdin 的代码，接 DEVNULL 秒回、接一根没人写的管道就挂住）——只看文本的门报绿时
     你分不清是实现对还是判据没看懂实现。18e 是同一族的另一半：清单收成一份文件后「抄漏一个包」
     的位置变了、症状没变，所以进包源码 import 到的每个第三方包都必须能在清单里找到
     （别名 pip 名≠import 名、连带由某发行包提供、仓库内目录包三条放行路线都必须在案，
     并且要自证真被走过）
  AC. 注释里那句「判据见某套件 第 N 节」必须真的走得通：硬门第 19 条。本轮把「择路不许跟着
     本机历史走」写进 tool_config / runtime_paths 时，两边都补了指路注释，而当时那个套件只
     编到第 9 节——一共 13 处悬空指路，其中四个函数（页尾拼法、联系方式判据、补采上限、收尾
     取真身）在全部离线套件里 0 覆盖，正是用户报「以前能采到、现在一直是 0」那条链的守卫
     缺口。「判据在哪一节」这句话本身不是判据，可达性得机械化：本节验接线、验四种写法（单号
     / 并列 / 区间 / 全角数字）与折行都点得出，验三条不许误报的口径（句子被句号断开、间距超
     过 12 字符、套件自己的内部编号），并拿「当时那 13 处」的原句形状做反向对照——一句走不通
     的指路必须红，而且要把缺的那几个节逐个点名
  AD. 交付链上每一次 curl 都必须**自己会停**，写进文件的字节必须**跨次能用**：硬门第 20 条。
     本机取制品连着两趟被外层超时杀掉（rc=124），日志停在「下载中」之后一句原因都没有。实测
     单连接 Range 取 2 MiB 要 182.98 秒（11.5 KB/s）、四并发 166 秒拿到 8 MiB，其中一路还会
     半路 Connection reset 只落 1899440/2097152 字节——所以「把 -m 调大」既救不了慢连接又会弄死
     正常的慢链路，正确的形状是「停顿判定 + 按块续传 + 落点可复用」。旧写法还把 zip 放在 mktemp
     工作目录里，trap EXIT 顺手删掉，于是每次重跑都从第 0 字节开始：慢可以接受，「永远完不成」
     不行。本节按 20a/20b/20c/20d/20e/20f 六格逐个验（check_curl_bounds 管前三格，
     check_no_prompt_hang 管 20d，check_shell_registration 管 20e，check_python_stdin 管 20f，
     四族读同一张 SHELL_SOURCES）：反向对照用的是 HEAD
     那两版的原句，落点认六种写法
     （`-o 值`/`-o值`/`--output=值`/组合开关），NSIS 那一步「有界 + 就近核哈希、无 -C」的真字节
     必须放行（免得为消红补一个用不上的 -C），注释里那句「也核哈希」不算收口，窗口边界第 40 行
     与第 41 行一格之差，而扫到 0 份文本 / 0 条 curl / 登记的脚本不在位 三种情况都必须红。
     20d 是 `git credential fill` 会去开 /dev/tty 问密码（喂 stdin 挡不住），HEAD 那三句原字节
     必须响、注释里那句提及不许假红；20e 是交付链目录里每一份 .sh 都得在案——取证脚本
     debug/fetch_run_log.sh 原本不在表里，四道门在它身上全是空的，20c 一接上就在它身上红了一条
     真缺陷（下坏一半的日志包照样以 PK 开头，错误响应会被当成「日志里没有错误」）；20f 是手写
     脚本里每一次独立起 python 都得自带 fd 0——本机后台取制品时启动它的终端被回收，fd 0 成了
     已关闭的描述符，python 崩在解释器初始化（init_sys_streams / Bad file descriptor），日志里
     只剩上一行的标题，「门不合格」被当成结论而真相是门没跑起来。管道右侧 / heredoc / 已有重定向
     三种放行，变量装的执行（`"$PY"`、`${PY}`）与隔了看门狗的（`timeout … "$PY"`）都要认，而
     引号文案、`command -v`/`which`、注释、赋值右值四种不许假红；workflow 的 bash 步刻意不判
     （Actions 自己关好 stdin），这一格边界用「单独喂就响」自证过滤本身在做事
  AE. 关工具不许弹系统崩溃报告：Qt 退出顺序——硬门第 21 条。本机实测两件事同根：一路离线套件
     打印完「全部通过」仍以 rc=139 收场（崩在汇总之后，批跑只看退出码，一条全绿就这样被读成失败），
     以及四个生产工具入口的 `sys.exit(app.exec_())`——那句后面一个字都放不进去，主窗口只能留给
     解释器拆，而 QApplication 比窗口先定义、按定义顺序清局部变量就是 app 先没。判的是销毁顺序，
     跑起来才知道的事测不到，只能静态判；所以每一格都要证明「改真代码才响」：注释不算、弹窗的
     `dlg.exec_()` 不算入口、`for` 循环收尾与 `w = w2 = None` 连赋不算、收尾写在 `sys.exit` 之后
     不算；读 0 份源码与「一个入口都没判到」两种空转都必须响（反向对照当场抓到过一次 fail-open）。
  AF. 凭证与登录态不许进历史：状态文件防护——硬门第 22 条。本轮提交前一句常规的
     `git add b2bcollect_build riskbird_build`（不带 -A）就把 `_云展账号.json`（手机号）、
     `acl/.acl_claim.json`（三个账号的 32 位 session）、`acl/.acl_device.json`（设备指纹）、
     chromedriver 二进制与一个 10 MB 的 zip 全扫进了索引，靠人读一遍键名才拦下；而它们能被扫进来
     只因为工具跑几天后会在包目录里自己写出这些文件。这类比「手滑提交密钥」更危险：触发条件是
     日常操作，且历史里的凭证删不掉。两条判据各管一头：名单从 `*_NAME = "*.json"` 常量里抽
     （手写清单会在改名那天变成「门还在、名单已过期」；注释里的声明不算，非 .json 的产出名不算，
     免得把用户真正要保存的东西挡在提交外），「挡住没有」问 git 自己的 `check-ignore`（在 Python
     里重抄一遍匹配就会分叉，而分叉的方向永远是「你以为挡住了」）。名单为空 / git 清单读空 /
     不在 work tree / check-ignore 退出码既非 0 也非 1 四种都必须红；豁免要登记；最后一格在临时
     目录里真 `git init` + 真 `git add` 复现当时的现场，没有 .gitignore 时三个名字各响两条

为什么单独成套：本仓库其它离线测试都在**源码态**跑，而这一轮外部机器全员精简版
的根因恰恰是「源码态通、打包态不通」——开发机恰好能从本机 iOS 工程读到密钥，
所以从第一次打包到现在都测不出来。这一套的职责就是**在离线机器上把打包态模拟
出来**：sys.frozen / _MEIPASS / sys.executable 三个开关一拨，凭证链就必须按
产物的规矩说话。

运行：QT_QPA_PLATFORM=offscreen .venv/bin/python b2bcollect_build/test_frozen_delivery_offline.py
"""
import ast
import builtins
import contextlib
import importlib.util
import inspect
import io
import json
import os
import re
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import tokenize
import textwrap
import threading
import time
import zipfile
from datetime import datetime, timezone
from email.utils import formatdate, parsedate_to_datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
_REPO = Path(__file__).resolve().parent.parent

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.pop("B2B_ACL_LEVEL", None)
os.environ.pop("B2B_DEVICE_ID", None)
os.environ["B2B_EDITION"] = "pro"

import edition as _ed                                          # noqa: E402
import access_guard as ag                                      # noqa: E402
import tool_config as tc                                       # noqa: E402
import oss_pool_client as oss                                  # noqa: E402
import text_shape                                              # noqa: E402  第 30 条把手真源
import deepseek_expander as dse                                # noqa: E402
# 版本算法真源。这一段的对拍要拿它自己算出的期望值去比「假编译器实收的那一格」，比的
# 不是算法本身（算法归 test_tool_version 那套判据），比的是**管道**：shell 有没有把模块
# 给的那一格原样递下去，还是半路又自己拼了一份同样的数字。
import tool_version as tv                                      # noqa: E402
from access_guard import AccessGuard                           # noqa: E402
from oss_pool_client import (OSSPoolError, OSSCredentialError,    # noqa: E402
                             OSSTransportError)

# 源码文本：有些约束是「只许有一处」型的（出网只许走 _send、路径策略只许问
# edition），运行时看不出漏了第二处，只有在文本上钉住才不会日后悄悄多出一份。
_OSS_SRC = (_REPO / "b2bcollect_build" / "oss_pool_client.py").read_text(encoding="utf-8")
_AG_SRC = (_REPO / "b2bcollect_build" / "access_guard.py").read_text(encoding="utf-8")
_TC_SRC = (_REPO / "b2bcollect_build" / "tool_config.py").read_text(encoding="utf-8")

_PASS = 0
_FAIL = 0

# 失败详情里不许带出密钥明文。这套用例的主角就是凭证解析链：只要某一级命中了开发机上
# 的真实密钥（本机打过包就一定会有），diff 就会把 AK/SK 原样打进 CI 日志和本地终端——
# 日志会被留存、会被别人看到，而「测试输出里有真密钥」这件事本身就是一个缺陷。
# 判据取「连续 24 位以上的纯字母数字」：AK/SK 这类正好命中；路径、URL、中文里都有
# / . 或非标点字符断串，不会被误伤成脱敏（把有用的定位信息抹掉也是一种坏法）。
_SECRET_RE = re.compile(r"[A-Za-z0-9]{24,}")


def _redact(text) -> str:
    return _SECRET_RE.sub(
        lambda m: m.group(0)[:4] + f"…（已脱敏，{len(m.group(0))} 位）", str(text))


def _one_line(text) -> str:
    """把任何进日志的判定文案压成**一行**（行形状由报告层保证，不靠每一处调用者自觉）。

    为什么要这一层：子进程的 stdout 本身就带 `❌ ` 与 `::error::` 前缀，谁把它原样嵌进
    断言文案，日志里就凭空多出一行以 `❌` 开头的文本——而「grep ❌ 找失败」正是读这份报告的
    人唯一的导航方式。本轮实测两次（DG 节真跑冒烟、上面那个夹具批跑），两次都把人带去查
    一条根本不存在的失败。观测手段污染被观测对象，与判据说谎是同一类代价。
    """
    return " | ".join(str(text).strip().splitlines())


def _assert(cond, msg):
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  ✅ {_one_line(_redact(msg))}")
    else:
        _FAIL += 1
        print(f"  ❌ {_one_line(_redact(msg))}")


def _assert_eq(actual, expected, msg):
    _assert(actual == expected, f"{msg}（期望 {expected!r}，实际 {actual!r}）")


def _assert_raises(exc_type, fn, msg):
    global _PASS, _FAIL
    try:
        fn()
    except exc_type:
        _PASS += 1
        print(f"  ✅ {_one_line(_redact(msg))}")
        return
    except Exception as e:
        _FAIL += 1
        print(f"  ❌ {_one_line(_redact(msg))}（抛的是 {type(e).__name__}: {_one_line(_redact(e))}）")
        return
    _FAIL += 1
    print(f"  ❌ {_one_line(_redact(msg))}（没抛异常）")


def _fmt_idx(i):
    """把「有没有找到某一行」说成人话。

    配合 ``next(gen, None)`` 用：裸 ``next(gen)`` 找不到就抛 StopIteration，那是
    测试框架自己的异常，一整棵用例树会被它带走，屏幕上连一条 ❌ 都看不到——
    本轮 K 组被自己的夹具打死就是这一类，查找/夹具的异常永远不该冒充用例结论。
    """
    return f"第 {i + 1} 行" if i is not None else "没找到"


def _write(path: Path, payload: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


# ── 打包态模拟器 ────────────────────────────────────────────────────────────

@contextlib.contextmanager
def _workspace(with_exe_config=None, with_bundle_config=None, env_config=None):
    """搭一套「 exe 同级 / 随包 / 环境变量指定」三级都可控的临时目录。

    刻意同时支持 frozen 与非 frozen：同三级路径在两种形态下的**语义**不同
    （源码态没有「程序同级」这一级），必须两种都摆出来才测得出优先级差异。

    同时把 access_guard 的本地状态目录（名单副本 / 设备指纹 / 认领记录）整体
    迁到临时目录：那三份文件在开发机上是**真实存在**的，`_load_disk_once()` 会
    从副本里拿到 accounts 从而绕开「包里没带凭证」这一支——不隔离的话，本套最
    关键的几条断言在开发机上会假绿，换一台机器又变成随机红。
    """
    root = Path(tempfile.mkdtemp(prefix="frozen_ws_"))
    exe_dir = root / "ToolDir"
    bundle = root / "MEIPASS"
    local_state = root / "local_state"
    exe_dir.mkdir(parents=True, exist_ok=True)
    bundle.mkdir(parents=True, exist_ok=True)
    local_state.mkdir(parents=True, exist_ok=True)
    if with_exe_config is not None:
        _write(exe_dir / "config.json", with_exe_config)
    if with_bundle_config is not None:
        _write(bundle / "config.json", with_bundle_config)
    env_file = None
    saved = {k: os.environ.get(k) for k in
             ("B2B_CONFIG_FILE", "B2B_STATE_DIR",
              "OSS_ACCESS_KEY_ID", "OSS_ACCESS_KEY_SECRET", "DEEPSEEK_API_KEY")}
    if env_config is not None:
        env_file = root / "given" / "config.json"
        _write(env_file, env_config)
        os.environ["B2B_CONFIG_FILE"] = str(env_file)
    for k in ("OSS_ACCESS_KEY_ID", "OSS_ACCESS_KEY_SECRET", "DEEPSEEK_API_KEY"):
        os.environ.pop(k, None)
    stack = contextlib.ExitStack()
    try:
        stack.enter_context(_patch(ag, "_state_dir", lambda: local_state))
        # ── 源码态的三级文件候选也一并搬进临时目录 ──────────────────────────
        # 不搬就会「跑过一次本地打包，回归就红四条」：打包脚本会现写
        # b2bcollect_build/config.json（真密钥），PyInstaller 还会在源码态把
        # state_dir 指回模块目录。测试读到的来源因此从「什么都没配」变成「随包内置
        # 命中」，.m 兜底那几条断言当场变红——而红法最坏的地方是它**看起来像真缺陷**，
        # 换一台没打过包的机器又全绿。测试的输入必须只由夹具决定（本套的职责就是把
        # 打包态模拟出来，结果被开发机磁盘上的真实产物影响，等于没模拟）。
        _orig_bundle = tc.bundle_dir
        _orig_repo = tc._repo_root

        def _bundle_isolated():
            return (root / "src_module") if not tc.is_frozen() else _orig_bundle()

        def _repo_isolated():
            return (root / "src_none") if not tc.is_frozen() else _orig_repo()

        stack.enter_context(_patch(tc, "bundle_dir", _bundle_isolated))
        stack.enter_context(_patch(tc, "_repo_root", _repo_isolated))
        stack.enter_context(_patch(tc, "user_config_file",
                                  lambda: root / "home_isolated" / "config.json"))
        yield {"root": root, "exe_dir": exe_dir, "bundle": bundle,
               "env_file": env_file, "local_state": local_state}
    finally:
        stack.close()
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


@contextlib.contextmanager
def _frozen(ws, state_dir=None):
    """把当前进程伪装成 PyInstaller 打包态。

    三个开关缺一不可：``sys.frozen`` 是本仓库所有 frozen 判定的唯一依据，
    ``sys._MEIPASS`` 决定「随包」这一级指向哪，``sys.executable`` 决定「程序同级」
    与「包旁」指向哪。少拨一个，测的就不是打包态了；拨了不收回，测的就是邻居的
    环境——所以三项都交给 ``_sys`` 按名字存取，这一层不再自己记 old。
    """
    saved_env = os.environ.get("B2B_STATE_DIR")
    with _sys(frozen=True, _MEIPASS=str(ws["bundle"]),
              executable=str(ws["exe_dir"] / "B2bCollectTool.exe")):
        if state_dir is not None:
            os.environ["B2B_STATE_DIR"] = str(state_dir)
        try:
            yield ws
        finally:
            if saved_env is None:
                os.environ.pop("B2B_STATE_DIR", None)
            else:
                os.environ["B2B_STATE_DIR"] = saved_env


@contextlib.contextmanager
def _patch(obj, name, value):
    """显式补丁 + 保证收回：漏收会污染后面所有用例（本仓库栽过一次）。"""
    old = getattr(obj, name)
    setattr(obj, name, value)
    try:
        yield
    finally:
        setattr(obj, name, old)


_ABSENT = object()


@contextlib.contextmanager
def _sys(**values):
    """按**名字**拨 sys 上的运行形态开关，退出时按名字还原。

    为什么不让各节自己写 ``old = (sys.platform, sys.executable, ...)`` 这种位置元组：
    T 节原来就是这么写的，取值顺序是 (platform, executable, frozen)，还原却按
    (platform, frozen, executable) 赋回去——于是 ``sys.frozen`` 变成一个真值字符串、
    ``sys.executable`` 变成 ``None``。它当场没红，红的是后面一整节（U 节在进程内真建
    窗口，撞上 ``Path(None)``），报的还是「NoneType」这种和产品毫不相干的栈。这类缺陷
    最坏的地方不在红，而在**红的位置离真因隔了一整节**：排查的人会先去查产品代码。
    按名字存取让两边不可能错开；传 ``None`` 表示「这项要恢复成没拨过的样子」，直接
    从 sys.__dict__ 删掉——``tool_config.is_frozen()`` 读的就是「有没有这个属性」。
    """
    old = {k: getattr(sys, k, _ABSENT) for k in values}
    for k, v in values.items():
        if v is None:
            sys.__dict__.pop(k, None)
        else:
            setattr(sys, k, v)
    try:
        yield
    finally:
        for k, v in old.items():
            if v is _ABSENT:
                sys.__dict__.pop(k, None)
            else:
                setattr(sys, k, v)


# 开跑前的运行形态快照：必须在**任何小节拨开关之前**记下来（写在模块顶部就是这个意思），
# 它同时是「源码态」的标准答案和「谁没收拾」的判据来源。
_SYS_SNAPSHOT = {"platform": sys.platform, "executable": sys.executable,
                 "frozen": getattr(sys, "frozen", None),
                 "_MEIPASS": getattr(sys, "_MEIPASS", None)}


@contextlib.contextmanager
def _source_form():
    """把运行形态开关压回「本机源码直跑」这一档，退出时按现场恢复。

    为什么 U 节要自己接管：本套十几节都会拨 frozen/_MEIPASS/executable，而 U 节是在
    **同一个进程内**真建 Qt 窗口。邻居留一个半拨的状态（frozen 成了真值字符串、
    executable 成了 None）时，U 节红的是 ``Path(None)`` 那种 TypeError——看着像新加的
    门禁自己有 bug，其实是上一节没收拾。用例的输入只能由夹具决定，不能由「谁先跑」
    决定，这条与 P/Q 节自己接管 ``Path.home``、cwd 是同一条纪律。
    """
    with _sys(**_SYS_SNAPSHOT):
        yield


def _switches_left() -> list:
    """跑完全部小节后，与开跑前快照不一致的那几项（空 = 每一节都把自己拨走的收回了）。"""
    out = []
    for key, want in _SYS_SNAPSHOT.items():
        now = getattr(sys, key, None)
        if now != want:
            out.append(f"sys.{key}={now!r}（开跑前是 {want!r}）")
    return out


def _code_only(fn) -> str:
    """函数正文，只留**会执行的那几行**（docstring 与注释整体剔掉）。

    「不许再出现某种写法」这类判据必须压在这里：本轮修好的旧写法（``cmd /c start``、
    ``timeout /t 2``、``shell=True``、``Path(__file__).parent``）恰恰都要写进 docstring
    里说明「为什么不能用」——直接从 ``inspect.getsource`` 上判负，会得到两种坏法：判据
    永远红，或者有人为了变绿把解释删掉（那是抹掉下次排查最需要的上下文）。注释行整体
    剔掉同一个道理，与 ``_strip_comments``（shell 文本）配成一对。
    """
    src = textwrap.dedent(inspect.getsource(fn))
    lines = src.splitlines()
    start = 0
    body = ast.parse(src).body[0]
    if (body.body and isinstance(body.body[0], ast.Expr)
            and isinstance(body.body[0].value, ast.Constant)
            and isinstance(body.body[0].value.value, str)):
        start = body.body[0].end_lineno          # docstring 之后才是正文
    return "\n".join(ln for ln in lines[start:]
                     if ln.strip() and not ln.lstrip().startswith("#"))


AKV = {"oss_access_key_id": "AKID_BUNDLE", "oss_access_key_secret": "SK_BUNDLE"}


# ══════════════════════════════════════════════ A. 配置解析链
def test_config_chain():
    print("\n═══ A. tool_config 解析链（打包态必须与开发态同权）═══")

    with _workspace(with_bundle_config=AKV) as ws, _frozen(ws):
        _assert(tc.is_frozen(), "frozen 模拟生效（sys.frozen + _MEIPASS）")
        _assert_eq(tc.bundle_dir(), ws["bundle"],
                   "随包目录取 sys._MEIPASS（不是模块目录）")
        _assert_eq(tc.exe_dir(), ws["exe_dir"].resolve(),
                   "程序同级目录取 sys.executable 的父目录")
        _assert_eq(tc.get("oss_access_key_id"), "AKID_BUNDLE",
                   "只随包带配置时也读得到（这就是 CI 注入后的真实形态）")
        _assert_eq(tc.source_for("oss_access_key_id"), "随包内置",
                   "来源标签如实报「随包内置」，排障时能一眼看出命中的是哪一级")

    # 同级覆盖随包：不发新版也能就地修好一台机器
    with _workspace(with_exe_config={"oss_access_key_id": "AKID_SIDE",
                                     "oss_access_key_secret": "SK_SIDE"},
                    with_bundle_config=AKV) as ws, _frozen(ws):
        _assert_eq(tc.get("oss_access_key_id"), "AKID_SIDE",
                   "exe 同级配置覆盖随包配置（side-by-side 优先）")
        _assert_eq(tc.source_for("oss_access_key_id"), "程序同级",
                   "覆盖命中后来源标签也跟着改")

    # 非成组的键可以跨级补：排障时在 exe 同级只放一个键，其余键仍从随包取到
    with _workspace(with_exe_config={"some_future_toggle": "off"},
                    with_bundle_config=dict(AKV, some_future_toggle="on")) as ws, \
            _frozen(ws):
        merged = tc.load()
        _assert_eq(merged.get("some_future_toggle"), "off",
                   "同级给的那个普通键以同级为准")
        _assert_eq(merged.get("oss_access_key_id"), "AKID_BUNDLE",
                   "同级没给的键继续从随包补齐（合并语义，不是第一个文件说了算）")

    # 成组的密钥**绝不允许**跨级拼合：半份新密钥 + 半份旧密钥 = 必然签名不符，
    # 症状与「没配密钥」不同却更难查（会被归成 denied，两边互相指）
    with _workspace(with_exe_config={"oss_access_key_secret": "SK_SIDE"},
                    with_bundle_config=AKV) as ws, _frozen(ws):
        merged = tc.load()
        _assert_eq((merged.get("oss_access_key_id"), merged.get("oss_access_key_secret")),
                   ("AKID_BUNDLE", "SK_BUNDLE"),
                   "同级只给 secret 时整级跳过：不许把同级的半把密钥与随包的半把拼一起")
        _assert_eq(tc.source_for("oss_access_key_secret"), "随包内置",
                   "来源报告说的是真正生效的那一级")
        warns = tc.warnings()
        _assert(any("OSS 密钥对" in w and "程序同级" in w for w in warns),
                f"配不全这件事必须写进 warnings（否则就是静默失败）：{warns}")

    # 空值不遮蔽
    with _workspace(with_exe_config={"oss_access_key_id": "  "},
                    with_bundle_config=AKV) as ws, _frozen(ws):
        _assert_eq(tc.get("oss_access_key_id"), "AKID_BUNDLE",
                   "同级写了个空串不算「已提供」：随包的值不许被空值遮掉")

    # 环境变量指定的文件优先级最高
    with _workspace(with_exe_config=AKV, with_bundle_config=AKV,
                    env_config={"oss_access_key_id": "AKID_ENVFILE",
                                "oss_access_key_secret": "SK_ENVFILE"}) as ws, _frozen(ws):
        _assert_eq(tc.get("oss_access_key_id"), "AKID_ENVFILE",
                   "B2B_CONFIG_FILE 指定的文件压过一切（临时排障专用口子）")
        _assert_eq(tc.source_for("oss_access_key_id"), "环境变量指定",
                   "来源标签如实报「环境变量指定」")

    # 源码态：没有「程序同级」这一级，且 describe 只报文件名不报内容
    with _workspace(with_bundle_config=AKV) as ws:
        _assert(not tc.is_frozen(), "未模拟时是源码态")
        _assert_eq(tc.exe_dir(), None,
                   "源码态刻意不给「程序同级」，避免与随包候选混成同一目录")
        desc = tc.describe()
        _assert("AKID" not in desc and "SK_" not in desc,
                "describe 绝不带出配置内容（密钥不能进日志）")

    # 坏 JSON 不许把整条链炸掉
    with _workspace(with_bundle_config=AKV) as ws, _frozen(ws):
        (ws["exe_dir"] / "config.json").write_text("{不是合法 JSON", encoding="utf-8")
        _assert_eq(tc.get("oss_access_key_id"), "AKID_BUNDLE",
                   "同级配置是坏 JSON 时退到随包配置（一处脏数据不许废掉整条链）")

    # 环境变量与文件同级参与解析：来源报告必须跟着改成「环境变量」，
    # 否则自检说「随包内置」、实际用的是别人塞进进程的变量，远程排障两头对不上
    with _workspace(with_bundle_config=AKV) as ws, _frozen(ws):
        os.environ["OSS_ACCESS_KEY_ID"] = "AKID_ENV"
        os.environ["OSS_ACCESS_KEY_SECRET"] = "SK_ENV"
        _assert_eq(tc.source_for("oss_access_key_id"), "环境变量",
                   "命中环境变量时来源标签如实说「环境变量」")
        _assert("环境变量=有" in tc.describe(),
                "describe 把环境变量这一级也列出来（优先级最高的一级不能漏报）")
        # 只给半个密钥变量的常见误配：整级跳过，退到随包的完整一对（绝不拼合）
        os.environ.pop("OSS_ACCESS_KEY_SECRET", None)
        _assert_eq((tc.get("oss_access_key_id"), tc.get("oss_access_key_secret")),
                   ("AKID_BUNDLE", "SK_BUNDLE"),
                   "只设了 OSS_ACCESS_KEY_ID 时不许拼出「env 的 id + 文件的 secret」")

    # 来源不许缓存上一次解析：先取值、后出现的环境变量也必须被来源认出来。
    # （CI 与 GUI 的调用顺序本来就不同，缓存过一次就会说出一个没被用上的级）
    with _workspace(with_bundle_config=AKV) as ws, _frozen(ws):
        _assert_eq(tc.get("oss_access_key_id"), "AKID_BUNDLE",
                   "先按文件链取一次值（让「上一次的结论」真实存在）")
        os.environ["OSS_ACCESS_KEY_ID"] = "AKID_ENV"
        os.environ["OSS_ACCESS_KEY_SECRET"] = "SK_ENV"
        _assert_eq(tc.source_for("oss_access_key_id"), "环境变量",
                   "取值之后才出现的环境变量，来源跟着改（不许返回缓存）")
        _assert_eq(tc.get("oss_access_key_id"), "AKID_ENV",
                   "来源与值出自同一次解析：两边说法必须一致")

    # 环境变量键名表只有一份口径：oss_pool_client 的退路必须与主表逐项相等
    _assert_eq(dict(oss.ENV_VAR_OF_KEY), dict(tc.ENV_KEY_MAP),
               "oss_pool_client 的退路表与 tool_config 主表逐项相等（改一边忘另一边就红）")


# ══════════════════════════════════════════════ B. OSS 凭证解析
def test_oss_credentials():
    print("\n═══ B. OSS 凭证：打包态禁用机器专属兜底 ═══")

    _assert(issubclass(OSSCredentialError, OSSPoolError),
            "OSSCredentialError 是 OSSPoolError 的子类（上层现有 except 全部继续成立）")

    # 1) 环境变量最高优先级
    with _workspace(with_bundle_config=AKV) as ws, _frozen(ws):
        os.environ["OSS_ACCESS_KEY_ID"] = "AKID_ENV"
        os.environ["OSS_ACCESS_KEY_SECRET"] = "SK_ENV"
        _assert_eq(oss.resolve_credentials(), ("AKID_ENV", "SK_ENV"),
                   "环境变量压过随包配置")
        _assert_eq(oss.credential_origin(), "环境变量",
                   "credential_origin 报出「环境变量」")

    # 2) 随包配置（CI 注入后的真实形态）
    with _workspace(with_bundle_config=AKV) as ws, _frozen(ws):
        _assert_eq(oss.resolve_credentials(), ("AKID_BUNDLE", "SK_BUNDLE"),
                   "打包态只靠随包 config.json 就能拿到密钥（本轮修复的正身）")
        _assert_eq(oss.credential_origin(), "随包内置",
                   "credential_origin 报出「随包内置」")

    # 3) 打包态**绝不**回落到开发机 .m 文件——哪怕这台机器上真有一份
    with _workspace() as ws, _frozen(ws):
        real_m_exists = any(p.is_file() for p in oss._M_FILE_CANDIDATES)
        with _patch(oss, "_read_ak_from_m_file",
                    lambda: ("AKID_FROM_M", "SK_FROM_M")):
            err = None
            try:
                oss.resolve_credentials()
            except OSSCredentialError as e:
                err = e
            _assert(err is not None,
                    "打包态无任何配置时必须抛 OSSCredentialError"
                    f"（本机 .m {'存在' if real_m_exists else '不存在'}，"
                    "这条断言正是靠它才不是空转）")
            _assert(err is not None and "打包工作流" in str(err),
                    "打包态的失败文案指向打包工作流（处理人是发包方，不是终端用户）")
            _assert(err is not None and "Secret" in str(err),
                    "文案直接给出该配哪两个 Secret，发包方不用再去翻代码")
            _assert(err is not None and "检查网络" not in str(err),
                    "缺密钥的文案绝不混进「请检查网络」")

    # 4) 源码态保留 .m 兜底（开发便利不能被这次修复打掉）
    with _workspace():
        with _patch(oss, "_read_ak_from_m_file",
                    lambda: ("AKID_FROM_M", "SK_FROM_M")):
            _assert_eq(oss.resolve_credentials(), ("AKID_FROM_M", "SK_FROM_M"),
                       "源码态仍然读得到开发机 .m（老开发流程不受影响）")
            _assert_eq(oss.credential_origin(), "开发机 iOS 工程文件",
                       "来源如实标注「开发机 iOS 工程文件」")

    # 5) 源码态彻底没配时的文案要指向本地可操作的动作
    with _workspace() as ws:
        with _patch(oss, "_read_ak_from_m_file", lambda: (None, None)):
            try:
                oss.resolve_credentials()
                _assert(False, "源码态没配密钥时应当抛错")
            except OSSCredentialError as e:
                _assert("config.json" in str(e) and "打包工作流" not in str(e),
                        "源码态文案指向本机 config.json/环境变量，不叫人改 workflow")


# ══════════════════════════════════════════════ C. 故障分类
def test_failure_taxonomy():
    print("\n═══ C. 权限服务故障分类（不再压成「网络问题」）═══")

    # 1) 探测分类：打包态没密钥 = credential，而不是 None
    with _workspace() as ws, _frozen(ws):
        client, kind, detail = AccessGuard._client_probe()
        _assert(client is None and kind == "credential",
                f"没凭证的打包态被分类为 credential（实际 {kind}）")
        _assert("未找到 OSS 密钥" in (detail or ""),
                "detail 带上底层原因，日志里能直接看到证据")

    # 2) check() 的 reason 不能再是「检查网络」
    with _workspace() as ws, _frozen(ws):
        _ed.reset_for_tests()
        g = AccessGuard(ttl=60.0)
        g._account_reader = lambda: "13417047813"
        ok, reason = g.check(wait=False)
        _assert(not ok, "没凭证时不放行（fail-closed 的安全边界必须守住）")
        _assert("不是你的网络问题" in reason,
                f"reason 明说这不是网络问题（实际：{reason[:70]}）")
        _assert("请检查网络后重试" not in reason,
                "reason 不再把发包缺陷伪装成用户侧网络故障")
        _assert_eq(g.service_status()[0], "credential",
                   "service_status 记下类别，界面/自检读同一个口径")

    # 3) 网络类仍然要说成网络（别把修复做成「什么都是发包的错」）
    _assert("检查网络" in AccessGuard._unreachable_text("network"),
            "network 类文案仍然让人查网络")
    _assert_eq(AccessGuard._unreachable_text("whatever"),
               AccessGuard._unreachable_text("network"),
               "认不出的类别退到网络文案兜底，绝不返回空话")

    # 4) OSS 错误归类
    cases = [
        ("OSSPoolError: HTTP 403 Forbidden", "denied"),
        ("SignatureDoesNotMatch 签名不符", "denied"),
        ("InvalidAccessKeyId 密钥不存在", "denied"),
        ("HTTPSConnectionPool: Read timed out", "network"),
        ("Failed to establish a new connection", "network"),
        ("NameResolutionError getaddrinfo failed", "network"),
        # 加密连接被中途拦下（安全软件网页过滤 / 公司代理 / 包里缺根证书）：
        # 报文里同时带着 HTTPSConnectionPool 与证书错误，判据必须**先认证书**，
        # 否则就退化成「请检查网络」——而这类机器网络好得很，用户会一直查下去。
        ("下载失败（加密连接）: SSLError: [SSL: CERTIFICATE_VERIFY_FAILED] "
         "certificate verify failed: unable to get local issuer certificate", "tls"),
        ("请求失败（加密连接）: SSLError: [SSL: WRONG_VERSION_NUMBER] wrong version number",
         "tls"),
        ("某种没见过的服务端错误", "other"),
    ]
    for text, want in cases:
        _assert_eq(AccessGuard._classify_oss_error(OSSPoolError(text))[0], want,
                   f"「{text[:28]}…」归为 {want}")
    # 「确定性失败」的判据只许有一个主人：判据写在两个地方，迟早一处认为该重试、
    # 另一处认为不该重试，而表现出来就是「同一台机器有时秒回、有时卡十几秒」。
    _assert_eq(len(re.findall(r"_is_permanent_failure\(", _OSS_SRC)), 2,
               "永久失败判据只有定义 + _send 一处调用（重试与否只在一个地方决定）")

    # 5) reason ↔ 类别 的反查表与文案表同源（界面靠它决定下一步）
    #    类别清单**从表里取**而不是写死：写死的话，以后新增一类故障（这次的时间
    #    偏差就是新增的）只要忘了在这里加一行，就会带着空文案上线——而这正是本轮
    #    要消灭的「某一类故障没被单独说话」。
    #    四张表的关系也一次说清（差集必须是确定的那一类，不能靠记忆）：
    #      · 文案表 ↔ 退出码表 严格同一批类别：每类故障都既有说法又有专属退出码；
    #      · 反查表 / 下一步表 少一个 network：网络类是**兜底**类，它不靠关键词认出
    #        来（认不出来就是它），文案本身已经写了「请检查网络后重试」。
    text_kinds = set(AccessGuard._UNREACHABLE_TEXT)
    _assert_eq(text_kinds, set(ag._DOCTOR_KIND_CODE),
               "每类故障都有文案，也都有 --acl-doctor 专属退出码（门禁红得能看出修哪层）")
    _assert_eq(set(AccessGuard._REASON_MARKERS), text_kinds - {"network"},
               "除兜底的网络类以外，每类都有可反查的关键词")
    _assert_eq(set(AccessGuard.SERVICE_NEXT_STEP), text_kinds - {"network"},
               "除兜底的网络类以外，每类都有「下一步该谁做什么」")
    for kind in sorted(text_kinds):
        _assert_eq(AccessGuard.classify_reason(AccessGuard._unreachable_text(kind)),
                   kind if kind != "network" else "",
                   f"{kind} 类文案归类唯一（网络类是兜底，不该被关键词反查命中）")
    _assert_eq(AccessGuard._unreachable_text("whatever"),
               AccessGuard._unreachable_text("network"),
               "认不出的类别退到网络文案兜底，绝不返回空话")
    _assert_eq(AccessGuard.classify_reason(
        AccessGuard._admin_client_failure("credential", "")), "credential",
        "管理入口的失败文案也在同一张反查表里")
    _assert_eq(AccessGuard.classify_reason("不在白名单，按丐版收口"), "",
               "非服务类 reason 不误伤成服务类")

    # 6) 管理入口分类：_must_client 抛的必须是分类过的 RuntimeError
    with _workspace() as ws, _frozen(ws):
        _assert_raises(RuntimeError, lambda: AccessGuard._must_client(),
                       "打包态没凭证时管理动作当场抛错（不静默返回 None）")
        try:
            AccessGuard._must_client()
        except RuntimeError as e:
            _assert_eq(AccessGuard.classify_reason(str(e)), "credential",
                       "管理动作的报错也能反查出类别，管理员一眼知道该修哪一层")

    # 7) 顶号/认领路径同样分类（旧写法一律「无法连接权限服务器，设备登记失败」）
    with _workspace() as ws, _frozen(ws):
        g2 = AccessGuard(ttl=60.0)
        ok2, msg2 = g2.claim_device("13417047813")
        _assert(not ok2 and "不是你的网络问题" in msg2,
                 f"设备登记失败也说清类别（实际：{msg2[:70]}）")

    # 8) 客户端能建起来时才谈得上网络问题
    class _FakeClient:
        bucket = "b"
        endpoint = "e"

        def download_json(self, key):
            raise OSSPoolError("HTTP 403 Forbidden")

    with _workspace(), _patch(AccessGuard, "_build_client",
                              staticmethod(lambda: _FakeClient())):
        g3 = AccessGuard(ttl=60.0)
        g3._account_reader = lambda: "13417047813"
        _assert(g3._fetch() is False, "被拒的刷新返回失败")
        _assert_eq(g3.service_status()[0], "denied",
                   "403 被记成 denied 而不是 network（管理员不必再去翻名单）")
        _assert("拒绝了本次访问" in g3._unreachable_reason(),
                "真被拒过之后给的是「密钥/RAM 权限」口径，不是「检查网络」")
        # 没试过的时候不能瞎指：客户端建得起来、又没有任何失败证据，只能诚实说网络
        g4 = AccessGuard(ttl=60.0)
        _assert("检查网络" in g4._unreachable_reason(),
                "客户端可构造但尚未试过 → 退到网络口径，不把猜测写成发包方的错")


# ══════════════════════════════════════════════ D. 界面口径
def _misleads_to_whitelist(text: str) -> bool:
    """这段文案是否在**指示**用户去动白名单（而不是告诉他动了也没用）。

    「加白名单都不起作用」「改白名单没有用」这两句是正确口径里必须出现的否定句，
    所以不能简单用「白名单」子串判对错——那样等于把最有用的那句排除掉，剩下的
    断言只会放过真正危险的写法。判据改成：提到白名单的地方必须紧邻否定词。
    """
    t = text or ""
    i = t.find("白名单")
    while i >= 0:
        seg = t[max(0, i - 16):i + 16]
        if not any(neg in seg for neg in ("不起作用", "没有用", "不用", "无关", "不必")):
            return True
        i = t.find("白名单", i + 1)
    return False


def test_gui_wording():
    print("\n═══ D. 界面下一步口径与故障类别同源 ═══")
    import b2bcollect_gui as gui

    cred = AccessGuard._unreachable_text("credential")
    mod = AccessGuard._unreachable_text("module")
    denied = AccessGuard._unreachable_text("denied")

    # 判据本身先自证：漏判（永远 False）比断言红更危险
    _assert(_misleads_to_whitelist("请联系管理员把你的手机号加入白名单"),
            "判据能认出真正的误导写法")
    _assert(not _misleads_to_whitelist("本地改网络、重新绑定、加白名单都不起作用"),
            "判据不会把否定句误判成误导")

    t, adv = gui.MainWindow._acl_denial(cred)
    _assert("凭证" in t and "自动" not in adv,
            f"缺凭证的拒绝标题点名凭证，且不承诺会自动补齐（{t}）")
    _assert(not _misleads_to_whitelist(adv), "缺凭证不叫人去找管理员加白名单")

    t2, adv2 = gui.MainWindow._acl_denial(mod)
    _assert("模块" in t2, f"缺模块的拒绝标题点名模块（{t2}）")

    # 这条是本轮最容易漏的顺序缺陷：被拒文案里带着「不用改白名单」，
    # 而白名单分支在它前面就会把人引去改白名单
    t3, adv3 = gui.MainWindow._acl_denial(denied)
    _assert("拒绝" in t3 and not _misleads_to_whitelist(adv3),
            f"被拒（403）不再被当成「没进白名单」（{t3}｜{adv3[:24]}）")

    # 网络类维持原口径：仍然承诺下一次刷新自动确认
    t4, adv4 = gui.MainWindow._acl_denial(
        "无法连接权限服务器，请检查网络后重试；仍无法使用请联系管理员")
    _assert("确认中" in t4 and "自动" in adv4,
            "网络类仍然是「权限还在确认中」，这次修复没把它改坏")

    # 每一类**可归类**的故障都要在界面上有自己的一句标题。遍历现表而不是写死清单：
    # 这次新增的时间偏差、加密连接就是这样被漏掉的——分类表加一行，界面那张 map
    # 不会自动跟着长，用户看到的还是那句含糊的「权限服务不可用」。
    for kind in sorted(AccessGuard._REASON_MARKERS):
        tk, ak = gui.MainWindow._acl_denial(AccessGuard._unreachable_text(kind))
        _assert(tk != "账号已绑定，但权限服务不可用",
                f"{kind} 类在界面上有专属标题，不掉回通用兜底话术（{tk}）")
        _assert(not _misleads_to_whitelist(tk) and not _misleads_to_whitelist(ak),
                f"{kind} 类的标题与下一步都不把用户指向白名单")
        _assert_eq(ak, AccessGuard.SERVICE_NEXT_STEP[kind],
                   f"{kind} 类的下一步就是分类表里那一条（界面不另写一份）")

    # 认领失败：凭证类不许落在「网络恢复后自动补齐登记」那一支
    tc_, bc = gui.MainWindow._claim_failure("13417047813", f"设备登记失败：{cred}")
    _assert("凭证" in tc_ and "自动补齐" not in bc,
            f"登记失败也如实说不会自动补齐（{tc_}）")
    _assert("13417047813" in bc, "认领失败的正文仍带上刚绑定的账号")

    # 真正该自动补齐的两类保持原样（防止把修复做成一刀切）
    tc2, bc2 = gui.MainWindow._claim_failure("13417047813", "无法读取该账号的绑定状态，请稍后重试")
    _assert("自动补齐" in bc2, "网络型登记失败仍然指向真实存在的自动补登记路径")


# ══════════════════════════════════════════════ E. DeepSeek 与缓存
def test_deepseek_and_cache():
    print("\n═══ E. DeepSeek 密钥与缓存（打包态读得到、写包外）═══")

    key = {"deepseek_api_key": "DS_FROM_BUNDLE"}
    with _workspace(with_bundle_config=dict(AKV, **key)) as ws, _frozen(ws):
        _assert_eq(dse.load_api_key(), "DS_FROM_BUNDLE",
                   "打包态随包 config.json 里的 DeepSeek 密钥读得到")
        from pool_matcher import CompanyMatcher
        _assert_eq(CompanyMatcher._load_api_key(), "DS_FROM_BUNDLE",
                   "pool_matcher 与 deepseek_expander 同一个口径（不再各读各的）")
        cf = dse.cache_file()
        _assert(str(ws["bundle"]) not in str(cf),
                f"缓存落在包外（实际 {cf}）——onefile 的临时目录每次退出就删")
        _assert(str(ws["exe_dir"]) in str(cf),
                "打包态缓存优先落在程序同级（用户看得见、也拷得走）")

    # 环境变量优先
    with _workspace(with_bundle_config=key) as ws, _frozen(ws):
        os.environ["DEEPSEEK_API_KEY"] = "DS_FROM_ENV"
        _assert_eq(dse.load_api_key(), "DS_FROM_ENV", "环境变量压过随包配置")

    # 缓存读写真的落到包外并且能读回来
    with _workspace(with_bundle_config=key) as ws, _frozen(ws):
        state = ws["root"] / "state"
        with _patch(dse, "cache_file", lambda: state / "deepseek_cache.json"):
            dse._cache.clear()
            dse._cache["k"] = ["a", "b"]
            dse._save_cache()
            _assert((state / "deepseek_cache.json").is_file(),
                    "缓存写到包外状态目录（不是 _MEIPASS 临时目录）")
            dse._cache.clear()
            _assert_eq(dse._load_cache().get("k"), ["a", "b"],
                       "缓存能读回来：否则「避免重复调用 API」这句话是空的")
            dse.clear_cache()
            _assert(not (state / "deepseek_cache.json").exists(),
                    "clear_cache 清的是同一个落点")

    # 坏缓存不许把功能炸掉
    with _workspace(with_bundle_config=key) as ws, _frozen(ws):
        bad = ws["root"] / "bad.json"
        bad.write_text("[\"不是对象\"]", encoding="utf-8")
        with _patch(dse, "cache_file", lambda: bad):
            dse._cache.clear()
            _assert_eq(dse._load_cache(), {},
                       "顶层是列表的脏缓存退回空表（旧写法会留下 AttributeError 隐患）")

    # save_api_key 的落点必须与读取链同源
    with _workspace() as ws:
        target = ws["root"] / "given" / "config.json"
        _write(target, {"placeholder": "1"})
        os.environ["B2B_CONFIG_FILE"] = str(target)
        try:
            _assert(dse.save_api_key("DS_SAVED"), "保存 DeepSeek 密钥返回成功")
            _assert_eq(dse.load_api_key(), "DS_SAVED",
                       "保存完立刻能从同一口径读回（界面提示「已保存」才是真话）")
            _assert("DS_SAVED" in target.read_text(encoding="utf-8"),
                    "写到的是那一份配置文件，没有另起一处影子配置")
        finally:
            os.environ.pop("B2B_CONFIG_FILE", None)

    # 源码态缓存仍在模块目录（历史缓存不搬家）
    _assert(str(Path(dse.cache_file())) .startswith(str(_REPO)),
            "源码态缓存留在仓库内，不因为这次改造把历史缓存搬丢")


# ══════════════════════════════════════════════ F. --acl-doctor
class _DoctorOSS:
    """自检用的假客户端：读可以，**任何写/删/改都必须当场炸**。

    自检会在 CI 与用户机器上反复跑，只要它写过一次线上数据（比如顺手播种
    名单），就等于「跑一次自检改一次生产配置」。这条边界光靠注释不算数，
    必须让方法本身不存在可用实现。
    """

    bucket = "images6396289"
    endpoint = "oss-cn-shenzhen.aliyuncs.com"
    prefix = "0817899624/company_pool"

    def __init__(self, doc=None, err=None):
        self._doc = doc
        self._err = err
        self.keys = []
        self.writes = []

    def download_json(self, key):
        self.keys.append(key)
        if self._err:
            raise self._err
        return self._doc, "etag"

    def _no_write(self, key):
        self.writes.append(key)
        raise AssertionError(f"自检不许写线上数据：{key}")

    def upload_json(self, key, *a, **kw):
        return self._no_write(key)

    def upload_json_atomic(self, key, *a, **kw):
        return self._no_write(key)

    def delete_key(self, key):
        return self._no_write(key)

    def copy_key(self, source_key, target_key, *a, **kw):
        return self._no_write(f"{source_key}->{target_key}")


def test_doctor():
    print("\n═══ F. 产物自检 --acl-doctor（发包门禁）═══")

    # 1) 通：拿到名单 = 退出码 0
    #    套进 _workspace：本机真实绑定号会多读一个设备分片，断言就跟着机器变。
    #    自检必须与「本机绑没绑」无关地在任何机器上给出同一结论。
    doc = {"accounts": ["13417047813"], "grants": {"13417047813": 2},
           "version": 7, "schema": "acl-v3"}
    fake = _DoctorOSS(doc=doc)
    with _workspace():
        # 绑定账号读的是 task_registry（不在 _workspace 的隔离范围内），本机真实
        # 绑定会让「读几个键」这条断言随机器变，所以显式钉成未绑定
        with _patch(AccessGuard, "_load_bound_account", staticmethod(lambda: "")):
            with _patch(AccessGuard, "_client_probe",
                        staticmethod(lambda: (fake, "ok", ""))):
                code, lines = ag.doctor_lines()
                _assert_eq(code, ag.DOCTOR_EXIT_OK, "名单可读 → 自检通过")
                _assert(any("名单读到 1 个账号" in ln for ln in lines), "报告里带名单规模")
                _assert_eq(fake.keys, [ag.ACL_KEY],
                           "未绑定的机器上自检只读名单这一个键")
                _assert_eq(fake.writes, [], "自检一个字节都没往线上写（只读边界）")
                _assert(any("未绑定" in ln for ln in lines),
                        "未绑定也如实报出来（CI runner 上这是正常形态，不能被当成失败）")

        # 1b) 本机已绑定时多读一个自己的分片键，仍然只读
        fake_b = _DoctorOSS(doc=doc)
        with _patch(AccessGuard, "_load_bound_account",
                    staticmethod(lambda: "13417047813")):
            with _patch(AccessGuard, "_client_probe",
                        staticmethod(lambda: (fake_b, "ok", ""))):
                code_b, lines_b = ag.doctor_lines()
                _assert_eq(code_b, ag.DOCTOR_EXIT_OK, "已绑定机器上自检同样通过")
                _assert_eq(fake_b.keys,
                           [ag.ACL_KEY, AccessGuard._bind_key("13417047813")],
                           "已绑定时只多读自己那一个设备分片（键名按账号定位，不扫全量）")
                _assert_eq(fake_b.writes, [], "已绑定路径也不写线上")
                _assert(any("该号等级：2" in ln for ln in lines_b),
                        "报告里能看出这个号的等级（发包方自查一眼对得上）")

    # 2) 不通：没凭证 = 退出码 2，且报告直接给出该改哪里
    with _workspace():
        with _patch(AccessGuard, "_client_probe",
                    staticmethod(lambda: (None, "credential", "未找到 OSS 密钥（配置来源：未配置）"))):
            code2, lines2 = ag.doctor_lines()
            _assert_eq(code2, ag.DOCTOR_EXIT_CREDENTIAL,
                       "缺凭证的自检退 2（CI 据此禁止上传制品）")
            _assert(any("不许发货" in ln for ln in lines2),
                    "报告把「这个包发出去就是精简版」说明白")

        # 3) 被拒 = 4，网络 = 5：两类必须分得开，否则门禁红得没有信息量
        for err, want in ((OSSPoolError("HTTP 403 AccessDenied"), ag.DOCTOR_EXIT_DENIED),
                          (OSSPoolError("connection timeout"), ag.DOCTOR_EXIT_NETWORK)):
            with _patch(AccessGuard, "_client_probe",
                        staticmethod(lambda e=_DoctorOSS(err=err): (e, "ok", ""))):
                code3, lines3 = ag.doctor_lines()
                _assert_eq(code3, want, f"「{str(err)[:24]}」→ 退出码 {want}")
                _assert(any("类别=" in ln for ln in lines3), "失败报告带类别")

        # 3b) 名单还没播种过 ≠ 发包失败：首次使用会自动建，门禁不能长期红着
        missing = _DoctorOSS(err=OSSPoolError("NoSuchKey 对象不存在"))
        with _patch(AccessGuard, "_client_probe",
                    staticmethod(lambda: (missing, "ok", ""))):
            code4, lines4 = ag.doctor_lines()
            _assert_eq(code4, ag.DOCTOR_EXIT_OK, "名单不存在时判通过（别把门禁做成永久红灯）")
            _assert(any("还没播种" in ln for ln in lines4), "报告说明白为什么不拦")

        # 3c) 连 tool_config 都没打进包 = 模块类退出码
        saved_tc = sys.modules.get("tool_config")
        try:
            sys.modules["tool_config"] = None          # import 语句命中 None 会抛 ImportError
            code5, lines5 = ag.doctor_lines()
            _assert_eq(code5, ag.DOCTOR_EXIT_MODULE, "tool_config 缺失 → 模块类退出码")
            _assert(any("打包清单" in ln for ln in lines5),
                    "报告指向 --hidden-import（这就是本轮 CI 新加的那一项）")
        finally:
            sys.modules["tool_config"] = saved_tc

    # 4) GUI 必须在 QApplication 之前分派自检（否则 CI 在无图形会话里跑不动）。
    #    这里只问**先后**，不问写法：写法已经收进 DOCTOR_FLAGS 一处，由 L 组逐字
    #    对拍；在文本里搜 "--acl-doctor" 字面量等于把「写法」和「顺序」两件事焊在
    #    一起，改一处命名就会把这条完全无关的顺序断言炸成整棵树崩溃。
    import b2bcollect_gui as gui
    src = inspect.getsource(gui.main)
    body = [ln for ln in src.splitlines() if not ln.strip().startswith("#")]
    idx = next((i for i, ln in enumerate(body) if "_doctor_request(" in ln), None)
    app_idx = next((i for i, ln in enumerate(body)
                    if "QApplication(sys.argv)" in ln), None)
    _assert(idx is not None and app_idx is not None,
            f"分派与建界面两段代码都在（{_fmt_idx(idx)}, {_fmt_idx(app_idx)}）")
    _assert(idx is not None and app_idx is not None and idx < app_idx,
            "main() 里自检的分派排在 QApplication 之前（纯 python 可跑）")
    _assert("_acl_doctor_entry" in src, "GUI 入口走的是统一收口，不各写一份")


# ══════════════════════════════════════════════ G. 交付链
@contextlib.contextmanager
def _chdir(path: Path):
    old = os.getcwd()
    os.chdir(str(path))
    try:
        yield
    finally:
        os.chdir(old)


# ── PyInstaller 命令行清单提取（yml 里是一整行、脚本里是折行，正则都吃得下）──
_HIDDEN_RE = r'--hidden-import\s+([\w.]+)'
_COLLECT_RE = r'--collect-(?:all|submodules)\s+([\w.]+)'
_ADD_DATA_RE = r'--add-data\s+"([^"]+)"'

_GATE_MOD = None
_LEGACY_TC_MOD = None


def _manifest_gate():
    """把构建前的「打包清单硬门」脚本当模块载进来（它就是判据本体，不另抄一份）。

    这份用例既校验三处入口的清单，也校验那个脚本自己判得对不对：脚本和用例各写一套
    提取逻辑时，同一颗坑要踩两次（注释里的 --hidden-import 被当成参数就是这么回事）。
    """
    global _GATE_MOD
    if _GATE_MOD is None:
        path = _REPO / ".github" / "scripts" / "check_packaging_manifest.py"
        spec = importlib.util.spec_from_file_location("_pkg_manifest_gate", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _GATE_MOD = mod
    return _GATE_MOD


def _bash_argv(*rest):
    """拼一条「交给 bash 跑」的命令：**bash 那一位只从门禁的把手取**。

    由来（2026-11-16 云端 Windows run 43860712318）：这一份用例原本有 5 处各自取 bash——4 处
    把裸名字 `"bash"` 直接交给 `subprocess.run`，1 处 `shutil.which("bash")`。在 Mac 与 Linux 上
    两种取法拿到的是同一个程序，所以这条分叉从来没被看见过；Windows 上裸名字走 `CreateProcess`
    的目录搜索顺序，先命中 `System32\\bash.exe`（WSL 存根：它不跑我们的脚本，只答一句 UTF-16 的话
    并以 rc=1 退掉），而 `shutil.which` 按 PATH 拿到的是 Git 的 cygwin——同一轮日志里两句「现场」
    互相打脸，人照着其中一句去查就查不到真原因。第 35 条就是为这一件事立的。
    取不到时返回 (None, 为什么取不到)，调用方要么把它写成一句响的，要么明说跳过，
    不许拿「没跑」当「跑过了」。
    """
    prog, note = _manifest_gate().bash_executable()
    return ([prog, *rest], note) if prog else (None, note)


def _bash_or_die(*rest):
    """`_bash_argv` 的硬版本：解析不出 bash 就把它报成一次带理由的失败，不静默往下跑。"""
    argv, note = _bash_argv(*rest)
    if argv is None:
        raise RuntimeError(f"第 35 条的 bash 把手在本机解析不出可用 bash（{note}）："
                           "依赖它的夹具一格都没跑，不许按通过算")
    return argv


def _cli_values(text: str, pattern: str) -> set:
    # 注释必须先剔掉再匹配：workflow/脚本的注释里写着「--add-data/--hidden-import 与
    # site_adapters 的清单」这种话，不剔就会凭空多出一个叫「与」的幽灵清单项，
    # 于是「本机脚本与 CI 清单一致」这条永远红——而它红的时候没人怀疑是解析器的问题。
    # 解析器与构建前的硬门共用同一份（见 _manifest_gate），两边不再各写一遍正则。
    return set(re.findall(pattern, _manifest_gate()._strip_comments(text)))


def test_delivery_chain():
    print("\n═══ G. 交付链：Secret 注入 → 随包配置 → 产物门禁 ═══")
    wf = _REPO / ".github" / "workflows"
    win = (wf / "build-b2bcollect.yml").read_text(encoding="utf-8")
    mac = (wf / "build-b2bcollect-mac.yml").read_text(encoding="utf-8")

    # 分隔符本平台化：Windows 用 `;`、macOS/Linux 用 `:`，写反了 PyInstaller
    # 不报错、只是静默不打包（= 又一次「源码态通、打包态不通」）
    for name, text, sep in (("Windows", win, ";"), ("Mac", mac, ":")):
        for needle, why in (
            ("secrets.OSS_ACCESS_KEY_ID", "AK 必须来自 Secrets（不入库）"),
            ("secrets.OSS_ACCESS_KEY_SECRET", "SK 同上"),
            ("inject_build_config.py", "构建前必须现写随包配置"),
            (f'--add-data "config.json{sep}."', "随包清单里有 config.json，且分隔符是本平台写法"),
            ('[ -f config.json ]', "构建前有硬门：注入没成功就不许进 PyInstaller"),
            ("--hidden-import tool_config", "新模块必须显式收录（依赖分析口径会变）"),
            ("--acl-doctor", "产物自检门禁"),
        ):
            _assert(needle in text, f"{name} workflow {why}")
        # 顺序即语义：门禁排在上传之前才有意义
        gate = text.index("--acl-doctor")
        upload = text.index("upload-artifact")
        _assert(gate < upload, f"{name} workflow 自检门禁在上传制品之前（不通就没有包）")
        _assert("paths:" in text and "build-b2bcollect" in text,
                f"{name} workflow 改动 yml 自身也会触发（改完就能验证）")

    # 交付物的第三个入口是「本机手跑打包」。注意 `b2bcollect_build/*.spec` 被
    # .gitignore 忽略（本机可再生产物，不入库），所以**受版本控制**的那个入口是
    # debug/build_b2bcollect_mac_local.sh。断言必须压在这个文件上：压到 spec 的
    # 存在性上，就等于又写了一条「只在作者那台机器上成立的检查」——正是本轮要
    # 根治的形状（只在开发机成立的东西不会失败，只会静默地把坑留给下一台机器）。
    local_sh = _REPO / "debug" / "build_b2bcollect_mac_local.sh"
    _assert(local_sh.is_file(), "本机打包入口受版本控制（换台机器 clone 也打得出同一个包）")
    local = local_sh.read_text(encoding="utf-8")
    for needle, why in (
        ("set -Eeuo pipefail", "任一步失败就整体失败，不会安静地留个半成品；-E 是必需的："
                              "不带它，函数与命令替换里的非零根本不走 ERR 陷阱（Z 节实测）"),
        ("inject_build_config.py --from-local",
         "随包凭证与 CI 同一个脚本、同一份键名契约（不靠人肉复制密钥）"),
        ('--add-data "config.json:."', "本机包也把 config.json 打进包"),
        ('if [ ! -f config.json ]', "构建前硬门：注入没成功就不进 PyInstaller"),
        ("--hidden-import tool_config", "新模块显式收录（与两个 workflow 同口径）"),
        ("--acl-doctor", "产物自检门禁：跑的是产物自己，不是源码"),
        ("RC=$?", "自检退出码被抓出来，而不是只看它跑没跑"),
        ('exit "$RC"', "自检不通就非零退出，末尾那句成功话术骗不了人"),
    ):
        _assert(needle in local, f"本机打包脚本 {why}")

    # 清单一致性：本机脚本与 Mac workflow 的 PyInstaller 收集清单逐项必须相同。
    # 这是三个出口最容易漂移的地方——往 yml 补一个 hidden-import 忘了同步脚本，
    # 本机包就永久少一个模块，而 CI 包一切正常，症状又是「某些机器上不对劲」。
    for label, pattern in (("--hidden-import", _HIDDEN_RE), ("--collect-*", _COLLECT_RE)):
        _assert_eq(_cli_values(local, pattern), _cli_values(mac, pattern),
                   f"本机脚本与 Mac workflow 的 {label} 清单一致")
    # version.json 是这一格里唯一**该不同**的项，理由与驱动那条同样硬：构建号只能来自
    # CI 计数器（github.run_number）。本机构造若要注一份戳就得自己编一个号，而那个号
    # 会与某次真实 CI 构建同号却不是同一份代码——冒名。后果不是难看：更新提示按它比
    # 大小、「这版先不提示」的免打扰按它记账，两处都会串到别人的档上。所以本机产物按
    # 设计自报「版本未能确认」（tool_version.UNKNOWN_BUILD），打包清单硬门里那条
    # CI_ONLY_DATA 记的是同一个判断。两处必须同时成立，所以两边都要反着钉：只把集合
    # 放宽的话，日后真有人在 Mac workflow 里漏注这一格，这条门也跟着一起绿。
    ci_data = {v for v in _cli_values(mac, _ADD_DATA_RE)
               if "chromedriver" not in v and not v.startswith("version.json")}
    local_data = {v for v in _cli_values(local, _ADD_DATA_RE)
                  if "chromedriver" not in v and "$DRIVER" not in v}
    _assert_eq(local_data, ci_data,
               "本机脚本与 Mac workflow 的 --add-data 清单一致（驱动按架构各取所需，"
               "构建戳只由 CI 注入）")
    _assert(not any("version.json" in v for v in _cli_values(local, _ADD_DATA_RE)),
            "本机脚本不许注入 version.json：本地包自报「版本未能确认」是有理由的设计，"
            "不是漏了同步（理由见上一段；与硬门的 CI_ONLY_DATA 同一条判断）")
    _assert(any(v.startswith("version.json") for v in _cli_values(mac, _ADD_DATA_RE)),
            "反过来，Mac workflow 必须注入 version.json：CI 少注这一格就是产物说不出自己"
            "是哪一版，这句不能跟着本机的豁免一起放宽")
    _assert(any("chromedriver" in v for v in _cli_values(mac, _ADD_DATA_RE))
            and "$DRIVER" in local,
            "两边都带内置驱动：CI 写死 arm64 路径，本机按架构探测")

    # 这里**不再**比对 b2bcollect_build/B2bCollectTool.spec：它由 PyInstaller 在
    # CLI 构建时自动生成（.gitignore 里 *.spec 就是为此），内容只是那串命令行参数的
    # 回显，既不是入口也不是人工维护的清单。以前「在位就顺手比对」的写法会让回归结果
    # 取决于「这台机器最近跑没跑过本地打包」——跑过就红三条，而且红的是一条根本不参与
    # 出包的旁路文件。真正要钉的门在下面这句：本机脚本自己必须在 config.json 不在位时中止。
    _assert("if [ ! -f config.json ]" in local and "exit 1" in local,
            "本机脚本在 config.json 不在位时中止（不许安静地出一个没凭证的包）")

    # 校验器自己也得懂产物结构：Actions 制品是「zip 里再套一层 ditto 打的产物 zip」，
    # 只解外层时 .app 永远找不到，脚本会报「没找到 .app，无法校验」并按坏包退出——
    # 症状和产物真坏了完全一样（本轮两个云 run 都 success，却被自己的校验器拦下）。
    # 判据钉在文本上：必须解内层 zip；解不出内层时必须非零退出，绝不许安静跳过校验。
    fetch = (_REPO / "debug" / "fetch_b2bcollect_artifacts.sh").read_text(encoding="utf-8")
    _assert("ditto -x -k" in fetch and "INNER" in fetch,
            "制品校验器解到内层产物 zip 再找 .app（少解一层=把校验器自己的毛病报成产物坏了）")
    _assert("没有内层产物 zip" in fetch and "解不开" in fetch
            and fetch.count("exit 6") >= 2,
            "内层 zip 不在位/解不开两处都非零退出（静默跳过校验等于拆掉随包凭证这道门）")

    # 随包凭证校验读的字节必须就是这一批取回来的字节：旧写法把所有人挤在写死的
    # /tmp/b2b_dl 上，两次取制品并发（或上一次崩了留半个目录）时校验可能读到另一批
    # 解出来的东西——它照样报 ✅，但验的不是要交付的那一份。这一类和「dirty 工作区」
    # 同属最坏的假绿：所有断言都是真的，量的却是别的字节。
    def _workdir_problems(text: str) -> list:
        code = "\n".join(ln for ln in text.splitlines()
                         if not ln.strip().startswith("#"))
        out = []
        if "mktemp -d" not in code:
            out.append("工作目录不是每次现建的（并发取制品会读到另一批解出来的字节）")
        if "/tmp/b2b_dl/" in code:
            out.append("还留着写死的共用落点 /tmp/b2b_dl/（校验的字节与交付的字节可能对不上）")
        # 下面几条都是 2026-11-13 本机那次「盘只剩 2.0Gi + 内层绿色版解不开」的直接教训：
        # 症状不是报错，是**挂住**（unzip 回头问人，问句没人答）+ **越失败越占盘**（半截
        # 工作目录没人收）。那一次它嘴里说的是 `write error (disk full?)`，可把空间腾出来
        # 以后照样红。真因后来按字节定了：中央目录 version made by 的高字节 create_system
        # 为 0（Windows runner 上 CPython 写死）时，Apple unzip 把非 ASCII 条目名再过一遍
        # OEM→Native 转换，得到一个写不下去的名字——修复在生产者那一头（条目名一律 ASCII），
        # 本小节下面的字节手术就是正反两向的证据。我先前在此写过的两句（「unzip 不认 UTF-8
        # 标志位」与「读中文名是对的、真因没有定论」）都已被实测改写，别再抄回去。
        if not re.search(r"\btrap\b[^\n]*\bEXIT\b", code):
            out.append("自动建的临时工作目录没有退出清理（崩一次留一份几百 MB 的半截目录，"
                       "下一次取制品更放不下——失败会攒成下一次的失败）")
        if "df -kP" not in code:
            out.append("开下载前没问目标卷的可用空间（写满时 unzip 不报错退出而是问人，"
                       "后台那条路没人答；`-k` 不是可选的：`-k` 给的是 1K 块，按字节解析会在某些卷上算错）")
        # 只盯**被执行的动作**：引号里的是给人看的话（"unzip 不会报错退出…"），拿它去判
        # 就会把解释当成违规行——下一来的人为了变绿会删注释与话术，那是抹掉下次排查最
        # 需要的上下文（与 _code_only 同一套做法）。
        # 反过来也不许去猜「unzip 前面得是行首/if !/分号」才算调用：`! unzip …`、
        # `then unzip …` 这些一样在执行，猜位置就会把真违规漏成 0 条（本轮反向对照
        # 抓到的正是这个）。剔掉注释与引号之后还活着的 unzip，只能在执行。
        # 续行也要先并成一句：`extract_zip "$INNER" x \` 换行 `|| { …; exit 6; }` 在 shell
        # 里是**同一句**，按行判就会把接住了退出码的调用说成没接住（假阳性→被人关掉）。
        _lines = [ln.strip() for ln in re.sub(r"\\\s*\n", " ",
                                              re.sub(r"'[^']*'|\"[^\"]*\"", " ", code)
                                              ).splitlines()]
        # 解包必须有一个**口径**（优先 ditto、unzip 兜底），而不是每处各写一遍。理由是
        # 实测出来的三条（本小节下面的行为证据就是这三条，不是任何人的复述）：unzip 觉得
        # 需要答复就回头问人，问句拿不到 stdin 时 8 秒还没结束；接了空设备就 0.2 秒按默认
        # 答复走人并留下退出码；ditto 同场景 0.018 秒解完、压根不问。
        # 唯一允许的例外是一个**显式命名的差分探针**（unzip_probe）：同一份字节让另一个
        # 工具再解一次、把事实印进日志。例外必须是例外——它不许决定这一趟成不成。
        def _fn_body(name: str):
            for _i, _ln in enumerate(_lines):
                if _ln.startswith(name + "() {") or _ln.startswith(name + "()  {"):
                    for _j in range(_i + 1, len(_lines)):
                        if _lines[_j] == "}":
                            return range(_i, _j + 1)
                    return range(_i, len(_lines))
            return None
        _cal = _fn_body("extract_zip")
        _probe = _fn_body("unzip_probe")
        _allowed = (set(_cal) if _cal is not None else set()) | \
                   (set(_probe) if _probe is not None else set())
        if _cal is None:
            out.append("解包没有单一口径（ditto 优先、unzip 兜底这一取舍必须只写在一处）："
                       "unzip 觉得需要答复就回头问人，问句拿不到 stdin 会一直挂着——后台那条路没人答")
        else:
            _allowed = set(_cal) | (set(_probe) if _probe is not None else set())
            _elsewhere = [ln for _k, ln in enumerate(_lines)
                          if _k not in _allowed and re.search(r"\b(unzip|ditto)\b", ln)
                          and "command -v" not in ln]
            if _elsewhere:
                out.append(f"解包动作漏在口径外面（{len(_elsewhere)} 处，例如：{_elsewhere[0][:56]}）："
                           f"哪天只改了口径、这些地方照旧用错的那一个")
        if _probe is not None and any(re.search(r"\bexit\b", ln)
                                       for ln in (_lines[k] for k in _probe)):
            out.append("差分探针里出现了 exit：探针只负责把「另一个工具在这一份字节上说了什么」"
                       "记进日志，它一旦能决定成败，交付判据就多了一个没人审的第二真源")
        # 两个方向分开判：接空设备是**工具那一句**的事（口径里面），接退出码是**调用那
        # 一句**的事（口径外面）。合成一条就会互相掩盖：`unzip x < /dev/null` 裸跑、
        # 或 `if ! unzip x`（没空设备）都只缺一半，各报各的才修得准。
        _noeof = [ln for ln in _lines
                  if re.search(r"\b(unzip|ditto)\b", ln) and "command -v" not in ln
                  and "< /dev/null" not in ln]
        if _noeof:
            out.append(f"有 unzip/ditto 没把 stdin 接到空设备（{len(_noeof)} 处，例如：{_noeof[0][:56]}）："
                       f"它回头问「Continue? (y/n)」时没人答就整条挂住（本机实测：拿不到 stdin 8 秒不结束）")
        _unguarded = [ln for _k, ln in enumerate(_lines)
                      if _k not in _allowed and re.search(r"\bextract_zip\b", ln)
                      and "if !" not in ln and "||" not in ln and "&&" not in ln]
        if _unguarded:
            out.append(f"有 extract_zip 调用没接住退出码（{len(_unguarded)} 处，例如：{_unguarded[0][:56]}）："
                       f"解包失败照样往下走，就是拿半份字节做完随包凭证校验还报 ✅")
        return out

    _assert_eq(_workdir_problems(fetch), [], "取制品落在自己现建的工作目录里")
    _assert_eq(_workdir_problems('mkdir -p /tmp/b2b_dl && rm -rf /tmp/b2b_dl/*\n'
                                 'unzip -oq x -d /tmp/b2b_dl/win\n'),
               ["工作目录不是每次现建的（并发取制品会读到另一批解出来的字节）",
                "还留着写死的共用落点 /tmp/b2b_dl/（校验的字节与交付的字节可能对不上）",
                "自动建的临时工作目录没有退出清理（崩一次留一份几百 MB 的半截目录，"
                "下一次取制品更放不下——失败会攒成下一次的失败）",
                "开下载前没问目标卷的可用空间（写满时 unzip 不报错退出而是问人，"
                "后台那条路没人答；`-k` 不是可选的：`-k` 给的是 1K 块，按字节解析会在某些卷上算错）",
                "解包没有单一口径（ditto 优先、unzip 兜底这一取舍必须只写在一处）："
                "unzip 觉得需要答复就回头问人，问句拿不到 stdin 会一直挂着——后台那条路没人答",
                "有 unzip/ditto 没把 stdin 接到空设备（1 处，例如：unzip -oq x -d /tmp/b2b_dl/win）："
                "它回头问「Continue? (y/n)」时没人答就整条挂住（本机实测：拿不到 stdin 8 秒不结束）"],
               "反向对照：本轮那种固定共用落点必须被判红（判据抓不住=这条门是空的）")
    # 每条门各自要能被单独摘掉再抓回来：只对照「全坏的那种」等于任一单独失效
    # 都测不出来（与第 15 条拆三半同一个道理）。
    _ok_work = ('WORK=$(mktemp -d "${TMPDIR:-/tmp}/b2b_dl.XXXXXX")\n'
                'trap \'rm -rf "$WORK"\' EXIT\n'
                'AVAIL_KB=$(df -kP "$WORK" | awk \'NR==2 {print $4}\')\n'
                'extract_zip() {  # $1=zip $2=目标目录\n'
                '  mkdir -p "$2" || return 2\n'
                '  if command -v ditto >/dev/null 2>&1; then\n'
                '    ditto -x -k "$1" "$2" < /dev/null\n'
                '  else\n'
                '    unzip -oq "$1" -d "$2" < /dev/null || return 1\n'
                '  fi\n'
                '}\n'
                'if ! extract_zip a.zip "$WORK/win"; then exit 6; fi\n')
    _assert_eq(_workdir_problems(_ok_work), [],
               "正例：现建目录 + 退出清理 + 空间预检 + 单一口径 + 工具接空设备 + 调用接码")
    for _needle, _why in (
        (" EXIT\n", "退出清理那一句被摘掉要能单独被抓回"),
        ("df -kP", "空间预检被摘掉要能单独被抓回"),
        ('"$2" < /dev/null\n', "口径里 ditto 那一句不接 stdin 也要被抓回"),
        ('"$2" < /dev/null || return 1', "兜底那一句不接空设备就要被抓回（换成 unzip 也一样的规矩）"),
        ("if !", "调用不接退出码就会拿半份字节去做校验"),
    ):
        _mutated = _ok_work.replace(_needle, "", 1)
        _assert(_mutated != _ok_work, f"反向对照夹具真的动到了 {_needle}（没动成功=自己跟自己比）")
        _got = _workdir_problems(_mutated)
        _assert_eq(len(_got), 1,
                   f"反向对照：摘掉「{_needle}」必须恰好报一条——{_why}（实际={_got}）")
    # 整条口径被删掉：只该报「没有单一口径」那一条（工具与调用都随函数一起消失，
    # 再报别的就是把同一次改动数成三处缺陷）。
    _nofn = _ok_work.replace(_ok_work[_ok_work.index("extract_zip() {"):
                                      _ok_work.index("\nif ! extract_zip")], "", 1)
    _assert("extract_zip() {" not in _nofn and "unzip" not in _nofn,
            "反向对照夹具要真的把整条口径删掉（没删成=自己跟自己比）")
    _got = _workdir_problems(_nofn)
    _assert("没有单一口径" in "".join(_got),
            f"删掉口径必须报出这一条（实际={_got}）")
    # 反过来：调用改回「各写一遍」的散装写法。哪怕这一处规矩齐全（接了空设备、接了码），
    # 也照样判红——本轮的真实故障就是「只会 unzip」，哪天改口径时这些地方照旧用错的那一个。
    _stray = _ok_work + 'unzip -oq z.zip -d "$WORK/other" < /dev/null || exit 6\n'
    _got = _workdir_problems(_stray)
    _assert_eq(len(_got), 1,
               f"反向对照：口径外面自己再写一处解包必须恰好报一条（实际={_got}）")
    _assert(_got and _got[0].startswith("解包动作漏在口径外面"),
            f"报的要说是漏在口径外面，不是别的（实际={_got[:1]}）")
    # 例外只留一个，而且是**显式命名**的那个：差分探针（同一份字节让另一个工具再解一次，
    # 把事实印进日志）。它必须既不被算成「漏在口径外面」，又不许决定这一趟成不成——
    # 探针能决定交付成败的那一天，判据就有了第二个没人审过的真源。
    _probe_def = ('unzip_probe() {  # 差分探针：$1=zip\n'
                  '  command -v unzip >/dev/null 2>&1 || return 0\n'
                  '  unzip -q "$1" -d "$WORK/probe" < /dev/null || return 0\n'
                  '}\n'
                  'unzip_probe a.zip\n')
    _assert_eq(_workdir_problems(_ok_work + _probe_def), [],
               "正例：差分探针是允许的第二处解包（工具、空设备、不判成败都齐全）")
    _got = _workdir_problems(_ok_work + _probe_def.replace("|| return 0\n}\n", "|| exit 6\n}\n"))
    _assert_eq(len(_got), 1,
               f"反向对照：探针里写 exit 必须恰好报一条（实际={_got}）")
    _assert("差分探针" in _got[0], f"报的要说是探针越权，不是别的（实际={_got[0][:50]}）")
    # 反向对照的另一半：只在**话说里**提到 unzip（解释「为什么这里要接 /dev/null」的
    # echo）不能被抓成违规行。抓了，下一来的人就会去删那句解释——而删掉的正是下次
    # 排查最需要的一行。
    _assert_eq(_workdir_problems(_ok_work + 'echo "写不下去时 unzip 是回头问人，不是报错"\n'), [],
               "反向对照：注释与话术里提到 unzip 不算一处违规（判的是被执行的动作，不是文中有这个词）")

    # ── 行为侧：口径这句取舍站在实测上；本轮把上一版写的「至今没有定论」也定论了 ──
    # 这一小节先前写过两句半对半错的话，现在都按字节改完：
    #   ✗「Apple unzip 不认中文条目名的 UTF-8 标志位」——错。标志位置着时它读得对。
    #   △「macOS 的 unzip 读中文名是对的」——只在**中央目录声明这包是 Unix 打的**时成立。
    # 决定项是 CD 记录里 version made by 的高字节（create_system）：
    #   3 → 按本地编码直取名字字节；0 → 名字再过一遍 OEM→Native 转换，得到一个写不下去
    #   的名字，工具嘴里说的是 `write error (disk full?)`（那块卷当时还剩 9 GB）。
    # CPython 的 zipfile 在 win32 上把这个字节写死成 0，所以「Windows runner 打的包 +
    # 非 ASCII 条目名」迟早红在用户那边，而构建日志全绿。下面把这个字节来回改两次，
    # 红绿跟着换；最后再证明：条目名全 ASCII 时，这一个字节**影响不到**交付——那才是
    # 修完的样子（判据站着的地方是这段字节，不是任何人的复述）。
    # 另外三条与文件名无关的实测仍然成立，它们是「单一口径 + 接空设备 + 接住退出码」
    # 三道门站着的地方：unzip 觉得需要答复就回头问人（管道 stdin 6 秒不结束）；同一句
    # 接空设备就秒回并留下非零退出码；ditto 同场景不问、不挂、rc=0。
    asm = _win_assembler()
    _gate = asm.check_release_zip            # 判据只认门那一份实现，用例里不抄第二条规矩
    _ztmp = Path(tempfile.mkdtemp(prefix="utf8name_zip_"))

    def _make_portable(entry_name: str, tag: str) -> Path:
        """按**生产者的代码**打一份绿色版，夹具只把 GUIDE_ZIP_ENTRY 这一个常量拨到给定值。

        为什么不自己 zipfile.writestr 造包：本轮我一度用夹具造包，结果 CPython 的
        ``ZipFile.open(mode='w')`` 悄悄把 flag_bits 重置成 0x00，夹具自己把字节改坏了，
        据此得出的结论整轮作废。让真生产者动手，夹具只管一个名字，测到的才是交付字节。
        """
        _app = _ztmp / "dist" / asm.APP_DIR_NAME
        (_app / asm.INTERNAL_DIR).mkdir(parents=True, exist_ok=True)
        (_app / asm.EXE_NAME).write_bytes(b"MZ\x90\x00")
        (_app / asm.INTERNAL_DIR / "base_library.zip").write_bytes(b"PK\x03\x04")
        (_app / asm.INTERNAL_DIR / "config.json").write_text('{"ak": "", "sk": ""}\n',
                                                              encoding="utf-8")
        _gs = _ztmp / asm.GUIDE_SRC
        _gs.parent.mkdir(parents=True, exist_ok=True)
        _gs.write_text("B2B 采集工具使用说明\n", encoding="utf-8")
        _out, _pr = _ztmp / f"portable_{tag}.zip", []
        with _patch(asm, "GUIDE_ZIP_ENTRY", entry_name):
            asm.build_portable_zip(_ztmp, _app, _gs, _out, _pr)
        _assert_eq(_pr, [], f"生产者打 {tag} 这份包时自己报的问题（实际={_pr}）")
        return _out

    _good = _make_portable(asm.GUIDE_ZIP_ENTRY, "ascii")   # 修完的样子
    _bad = _make_portable(asm.GUIDE_DEST, "cjk")           # 本轮之前的那个形状

    # ① 交付形状本身：条目名全 ASCII；中文名只活在安装目录（NTFS 文件名，不走 zip 字节）
    with zipfile.ZipFile(_good) as _zf:
        _names = _zf.namelist()
    _assert(all(n.isascii() for n in _names),
            f"绿色版 zip 的条目名必须全 ASCII（实际非 ASCII="
            f"{[n for n in _names if not n.isascii()]}）")
    _assert(asm.GUIDE_ZIP_ENTRY in _names and asm.GUIDE_DEST not in _names,
            f"根上那份说明的条目名要的是 ASCII 的 {asm.GUIDE_ZIP_ENTRY!r}，不是安装目录里那个 "
            f"{asm.GUIDE_DEST!r}（实际顶层={sorted(n for n in _names if '/' not in n)}）")
    _assert(asm.GUIDE_ZIP_ENTRY == asm.GUIDE_SRC.rsplit("/", 1)[-1],
            "条目名由 GUIDE_SRC 派生：再手抄一份字面量就是第二真源，改一处漏一处")
    _nsi = (_REPO / "b2bcollect_build" / "installer" / "b2bcollect_setup.nsi").read_text(
        encoding="utf-8", errors="replace")
    _assert(f"/oname={asm.GUIDE_DEST}" in _nsi,
            "安装目录里那个中文名仍由 NSIS 的 File /oname 给——ASCII 化的是包内字节，"
            "不是用户看到的那个文件名（把 /oname 一起删了就是拿交付格式限制用户界面）")
    _assert_eq(_gate.check_zip_entries(_good, top=asm.APP_DIR_NAME,
                                       root_files=(asm.GUIDE_ZIP_ENTRY,)), [],
               "门对修复后这份包必须无话可说（它什么时候红，交付就什么时候红）")
    _cp = _gate.check_zip_entries(_bad, top="", root_files=())
    _assert_eq(len(_cp), 1, f"反向对照：条目名换回中文，门要恰好报一条（实际={_cp}）")
    _assert("非 ASCII" in _cp[0], f"报的必须是条目名这件事，不是别的（实际={_cp[0][:60]}）")
    _sp = _gate.check_zip_entries(_good, top="B2bCollectToolX",
                                  root_files=(asm.GUIDE_ZIP_ENTRY,))
    _assert_eq(len(_sp), 1, f"反向对照：唯一顶层目录名对不上时要恰好报散出这一条（实际={_sp}）")
    _assert("顶层" in _sp[0], f"报的必须是顶层形状（实际={_sp[0][:60]}）")
    # 根上那份说明「在不在包里」也要这道门说话，而不是只由测试盯着：装配脚本里写的是
    # ``if guide.is_file(): z.write(...)``——源文件缺席时它静默跳过。CI 那条路上
    # check_bom 已经先拦过缺席，所以这里要防的是**后面那一支**：有人改了写条目的那几行、
    # 或者把名字算错成包里根本没有的形状。一条只会说「解不开」、不会说「少了一份」的门，
    # 交付出去的是「解压后没有使用说明」这种没有报错的坏包。
    _noguide = _ztmp / "portable_noguide.zip"
    _ng = []
    asm.build_portable_zip(_ztmp, _ztmp / "dist" / asm.APP_DIR_NAME,
                           _ztmp / "installer" / "这份源文件不存在.txt", _noguide, _ng)
    _assert_eq(_ng, [], f"生产者这份夹具不该自己报问题（实际={_ng}）")
    _np = _gate.check_zip_entries(_noguide, top=asm.APP_DIR_NAME,
                                  root_files=(asm.GUIDE_ZIP_ENTRY,))
    _assert_eq(len(_np), 1, f"反向对照：包里没有根上那份说明，门必须恰好报一条（实际={_np}）")
    _assert("根上少了" in _np[0], f"报的必须是「少交付」而不是别的（实际={_np[0][:60]}）")
    _assert_eq(_gate.check_zip_entries(_good, top=asm.APP_DIR_NAME,
                                       root_files=(asm.GUIDE_ZIP_ENTRY,)), [],
               "正例：同一条判据在修好的包上无话可说（不然是判据自己在报红）")

    unzip = shutil.which("unzip")
    _cs0_cjk = None
    if not unzip:
        print("   · 这台机器没有 unzip：第 2、3 步跳过（口径不变，交付链上两个工具都必装）")
    else:
        # ② 问句这一半两平台都判：这是 Info-ZIP 自己的行为，与条目名、与哪台机器无关
        _u2 = _ztmp / "u_prompt"
        subprocess.run([unzip, "-oq", str(_good), "-d", str(_u2)],
                       capture_output=True, stdin=subprocess.DEVNULL,
                       timeout=120, check=True)      # 先落地，第二次才会撞上同名文件
        _r, _w = os.pipe()                            # 没人写的管道＝后台那条路的现实
        _hung = False
        try:
            subprocess.run([unzip, "-q", str(_good), "-d", str(_u2)],
                           stdin=_r, capture_output=True, timeout=6)
        except subprocess.TimeoutExpired:
            _hung = True
        finally:
            os.close(_r); os.close(_w)
        _assert(_hung, "反向对照：同名文件在位、stdin 拿不到输入时 unzip 必须挂住"
                       "（没挂住=这条夹具什么都没测，门在赌运气）")
        _t0 = time.time()
        _p2 = subprocess.run([unzip, "-q", str(_good), "-d", str(_u2)],
                             stdin=subprocess.DEVNULL, capture_output=True, timeout=6)
        _assert(_p2.returncode != 0 and time.time() - _t0 < 3,
                f"同一句接了空设备：既不挂（{time.time() - _t0:.1f}s）又要留下非零退出码"
                f"（实际 rc={_p2.returncode}）——「不挂 + 接得住」才是后台能用的形状")
        print(f"   · unzip 的问句实测：管道 stdin 6 秒未结束；空设备 {_p2.returncode} 退出，"
              f"尾句={_p2.stderr.decode('utf-8', 'replace').strip()[-42:]!r}")

        if sys.platform != "darwin":
            print("   · 非 macOS：这一字节怎么被解释是 Apple unzip 的实现，本机只跑门不跑字节手术")
        else:
            # ③ 字节手术（只在 mac 上判）：把中央目录 version made by 的高字节来回改
            def _cd_offsets(data: bytes, want: int) -> list:
                e = data.rfind(b"PK\x05\x06")
                _assert(e >= 0, "找不到 EOCD：字节手术无从做起（夹具坏了，不是交付坏了）")
                p, offs = struct.unpack_from("<I", data, e + 16)[0], []
                while data[p:p + 4] == b"PK\x01\x02":
                    n, ex, cm = struct.unpack_from("<HHH", data, p + 28)
                    offs.append(p)
                    p += 46 + n + ex + cm
                _assert_eq(len(offs), want, "中央目录记录数要与条目数对上（偏移算错时这里先露馅，"
                                           "不该让后面的红绿去猜手术切在哪）")
                return offs

            def _flip_creator(zp: Path, value: int, tag: str) -> Path:
                """只改那一个字节，别的字节一个不动；改完换个读法自证切对了字段。"""
                raw = zp.read_bytes()
                with zipfile.ZipFile(zp) as zf:
                    n_entries = len(zf.infolist())
                offs = _cd_offsets(raw, n_entries)
                data = bytearray(raw)
                for p in offs:
                    data[p + 5] = value            # APPNOTE 4.3.6：低字节版本、高字节 creator
                q = _ztmp / f"{tag}.zip"
                q.write_bytes(bytes(data))
                with zipfile.ZipFile(q) as zf:
                    _assert(all(i.create_system == value for i in zf.infolist()),
                            f"{tag}：改完 create_system 要全等于 {value}"
                            f"（实际={sorted({i.create_system for i in zf.infolist()})}）——"
                            "偏移写错时手术切的是别的字段，下面那条红绿就是自欺")
                    _assert_eq({i.filename for i in zf.infolist()}, set(zipfile.ZipFile(zp).namelist()),
                               f"{tag}：手术不该动条目清单（动了就是在改夹具，不是改那一个字节）")
                _assert_eq(sum(1 for i in range(len(raw)) if raw[i] != data[i]), len(offs),
                           f"{tag}：只许碰 {len(offs)} 个字节（中央目录每条一个），多碰就是夹具越权")
                return q

            def _unzip_to(zp: Path, tag: str):
                dst = _ztmp / f"x_{tag}"
                dst.mkdir(parents=True, exist_ok=True)
                r = subprocess.run([unzip, "-oq", str(zp), "-d", str(dst)],
                                   stdin=subprocess.DEVNULL, capture_output=True, timeout=120)
                got = {q.relative_to(dst).as_posix() for q in dst.rglob("*") if q.is_file()}
                return r, got

            # 本机（Unix creator=3）打的中文名包解得开——这正是上一版那句「半句真话」成立的条件
            _r, _got = _unzip_to(_bad, "cs3")
            _assert(_r.returncode == 0 and asm.GUIDE_DEST in _got,
                    f"creator=3 时 Apple unzip 按本地编码直取名字字节，中文名解得开"
                    f"（rc={_r.returncode} 落地={sorted(_got)}）：这句只在**这一字节是 3** 时成立")

            _cs0_cjk = _flip_creator(_bad, 0, "cjk_cs0")      # Windows runner 就是这一字节
            _r0, _got0 = _unzip_to(_cs0_cjk, "cs0")
            _assert(_r0.returncode != 0 or asm.GUIDE_DEST not in _got0,
                    f"反向对照：只把 create_system 从 3 改成 0，中文名就要出问题"
                    f"（rc={_r0.returncode} 落地={sorted(_got0)}）——决定项不是标志位，是这一字节")

            _back = _flip_creator(_cs0_cjk, 3, "cjk_back3")   # 再改回去
            _r3, _got3 = _unzip_to(_back, "back")
            _assert(_r3.returncode == 0 and asm.GUIDE_DEST in _got3,
                    f"改回 3 就复原（rc={_r3.returncode} 落地={sorted(_got3)}）：上一行的红"
                    "确实是那一个字节造成的，而不是这份包本来另有毛病")

            # 修完的定义：条目名 ASCII 后，那一字节怎么写都影响不到交付
            _a0 = _flip_creator(_good, 0, "ascii_cs0")
            _ra, _gota = _unzip_to(_a0, "ascii0")
            with zipfile.ZipFile(_a0) as zf:
                _want = {i.filename for i in zf.infolist() if not i.filename.endswith("/")}
            _assert(_ra.returncode == 0 and _gota == _want,
                    f"ASCII 条目名 + creator=0（Windows runner 的形状）也要全解得开"
                    f"（rc={_ra.returncode} 落地={sorted(_gota)} 条目={sorted(_want)}）："
                    "交付不再依赖任何解包工具对这一字节的解释——这才是修复完成")
            print(f"   · 字节手术实测：中文名包 3→0 变红、0→3 复原；ASCII 包 creator=0 仍 rc=0/"
                  f"{len(_gota)} 个文件全落地")

    ditto = shutil.which("ditto")
    if not ditto:
        print("   · 这台机器没有 ditto（Linux CI 就是这样）：口径对比这一步跳过")
    else:
        for _tag in ("out", "out2"):                  # 第二次就是「同名文件已在位」那一格
            _t0 = time.time()
            _p = subprocess.run([ditto, "-x", "-k", str(_good), str(_ztmp / _tag)],
                                capture_output=True, stdin=subprocess.DEVNULL, timeout=120)
            _assert(_p.returncode == 0 and time.time() - _t0 < 30,
                    f"ditto 解两遍（第二遍同名文件在位）都不问、不挂、要成功"
                    f"（rc={_p.returncode} 用时 {time.time() - _t0:.1f}s）")
        _assert(asm.GUIDE_ZIP_ENTRY in {q.name for q in (_ztmp / "out").iterdir()},
                "ditto 解出来的名字与生产者写的条目名原样一致")
        if _cs0_cjk is not None:
            # 同一批字节，两个工具给出不同结果——这条分歧才是「别押在别人实现上」的完整证据
            _d = _ztmp / "ditto_cs0"
            _d.mkdir(parents=True, exist_ok=True)
            subprocess.run([ditto, "-x", "-k", str(_cs0_cjk), str(_d)],
                           capture_output=True, stdin=subprocess.DEVNULL,
                           timeout=120, check=True)
            _assert(asm.GUIDE_DEST in {q.relative_to(_d).as_posix() for q in _d.rglob("*") if q.is_file()},
                    "ditto 对 creator=0 的中文名照样解对：与 unzip 的分歧就在这一字节上，"
                    "不在 UTF-8 标志位上（标志位两边都置着）")

    # ④ Windows 那两路的「目录契约」用假工具真跑一次。本机到不了 Windows runner，但契约是
    #    硬的、而且两边相反：bsdtar 的 `-C dir` 要目录**已存在**，.NET 的
    #    ``ExtractToDirectory(src, dst)`` 碰到已存在的 dst 抛 IOException。门初版对每个工具都
    #    先 mkdir，于是 Windows 那一路上「dotnet 解包退出码 1」会是每次必来的假红——红的不是
    #    交付物，是判据自己的写法，而这种红最后的下场是被人在 CI 里整条关掉。
    #    这里让两个假工具按各自的现实当场拒绝，一次跑完覆盖两种形状，不等云端替我们发现。
    #    仿真走的是门自己留的接缝（``platform=`` / ``name=``），**不拨** ``sys.platform`` 也
    #    不拨 ``os.name``：本轮第一版那么做，红的是仿真自己的环境——``os.name='nt'`` 让
    #    pathlib 去实例化 WindowsPath 并抛 UnsupportedOperation，``sys.platform='win32'`` 让
    #    posix 的 shutil.which 去摸 mac 上为 None 的 _winapi。仿的是工具的行为，不是机器名。
    @contextlib.contextmanager
    def _path_first(d: Path):
        old = os.environ.get("PATH", "")
        os.environ["PATH"] = f"{d}{os.pathsep}{old}"
        try:
            yield
        finally:
            os.environ["PATH"] = old                 # 漏收会污染后面所有小节（本仓库栽过一次）

    _fw = _ztmp / "fake_win"
    _fw.mkdir(parents=True, exist_ok=True)
    _sheb = f"#!{sys.executable}\n"
    (_fw / "tar").write_text(_sheb + (
        "import sys, os, zipfile\n"
        "a = sys.argv\n"
        "d = a[a.index('-C') + 1]\n"
        "if not os.path.isdir(d):\n"
        "    sys.stderr.write('tar: ' + d + ': Cannot find directory\\n'); sys.exit(1)\n"
        "zipfile.ZipFile(a[a.index('-xf') + 1]).extractall(d)\n"), encoding="utf-8")
    (_fw / "powershell.exe").write_text(_sheb + (
        "import sys, os, zipfile\n"
        "q = chr(39)\n"
        "c = [x for x in sys.argv if 'ExtractToDirectory' in x]\n"
        "if not c:\n"
        "    sys.stderr.write('夹具：-Command 里没有 ExtractToDirectory\\n'); sys.exit(1)\n"
        "src, dst = c[0].split('ExtractToDirectory(')[1].split(')')[0].split(',')\n"
        "src, dst = src.strip(q), dst.strip(q)\n"
        "if os.path.exists(dst):\n"               # .NET Framework 的真实行为
        "    sys.stderr.write('Exception calling ExtractToDirectory with 2 argument(s): "
        "An item with the same name already exists.\\n'); sys.exit(1)\n"
        "zipfile.ZipFile(src).extractall(dst)\n"), encoding="utf-8")
    for _f in (_fw / "tar", _fw / "powershell.exe"):
        _f.chmod(0o755)
    # 分支本身先对一次拍：Windows 那一头的两条路与各自的目录要求。
    _nt = _gate._tool_specs(_good, platform="win32", name="nt")
    _assert_eq([(l, t, nd) for (l, t, nd, _mk) in _nt],
               [("bsdtar", "tar", True), ("dotnet", "powershell.exe", False)],
               "Windows 这一头跑的就是用户会用的两条路，而且要求相反：bsdtar 要现成目录、"
               ".NET 不许预建（少一条=另一边没人问；标记弄反=每次必来的假红）")
    _specs_real = _gate._tool_specs
    try:
        with _path_first(_fw):
            _wp = _gate.extract_with_os_tools(_good, platform="win32", name="nt")
            _assert_eq(_wp, [],
                       f"正例：Windows 那两路各自按自己的目录契约都要真解一次并对拍上（实际={_wp}）")
            # 反向对照·两个方向：把「预建目录」这件事统一做，必须被假 .NET 当场拒绝；
            # 反过来统一不建，必须被假 bsdtar 当场拒绝。抓出的都是门自己的写法。
            _gate._tool_specs = lambda zp, platform="", name="": [
                (l, t, True, mk) for (l, t, nd, mk) in _nt]
            _wr = _gate.extract_with_os_tools(_good, platform="win32", name="nt")
            _assert(any("dotnet" in x and "退出码" in x for x in _wr),
                    f"反向对照：给 .NET 预建目录必须被拒绝（实际={_wr}）")
            _gate._tool_specs = lambda zp, platform="", name="": [
                (l, t, False, mk) for (l, t, nd, mk) in _nt]
            _wr2 = _gate.extract_with_os_tools(_good, platform="win32", name="nt")
            _assert(any("bsdtar" in x and "退出码" in x for x in _wr2),
                    f"反向对照：不给 bsdtar 建目录必须被拒绝（实际={_wr2}）")
    finally:
        _gate._tool_specs = _specs_real

    # ⑤ 云端那条假红的**真**形状：交付包里有 1078 条符号链接，其中 168 条落地后是目录。
    #    我一开始把它记成「ditto 写目录条目不带尾斜杠、门按尾斜杠分类」——那句是错的，错在
    #    拿推理顶替实测。对着真制品（本机同一份源码打的包，4943 条）数过：目录条目 568 条
    #    **全带**尾斜杠，不带的一条都没有；差的 168 条 external_attr 全是 S_IFLNK，第一条
    #    正是 …/Frameworks/PIL/.dylibs。它是 PyInstaller 的隐藏目录手法：真目录存成
    #    ``__dot__dylibs``，再留一条 ``.dylibs`` → ``__dot__dylibs`` 的链接；门旧版落地侧
    #    ``for r, _, fs in os.walk(dst) for f in fs`` 只收 files，而 os.walk 按「跟随链接后
    #    是不是目录」分类，指向目录的链接被分到 dirs 那一格 —— 于是条目里有、落地清单里
    #    「没有」。红的是判据自己的收法，交付物一个字节都没少。
    #    这一段用**真 ditto** 造这份形状：CPython 的 zipfile 造不出符号链接条目，拿它当夹具
    #    就等于把云端那条假红的原因留在用例之外（本轮第 5 次犯的错：夹具的形状不是生产者的
    #    形状——连「目录条目不带斜杠」这一格都是我凭空造的，实测两个真工具都拒绝那种包）。
    if ditto:
        _dt_app = _ztmp / "ditto_tree" / "B2bCollectTool.app"
        _dl = _dt_app / "Contents" / "Frameworks" / "PIL" / "__dot__dylibs"
        _dl.mkdir(parents=True)
        (_dl / "libPIL.dylib").write_bytes(b"\xce\xcf\xfa\xed")   # 链接目标里得有东西
        (_dt_app / "Contents" / "MacOS").mkdir(parents=True)
        (_dt_app / "Contents" / "MacOS" / "B2bCollectTool").write_bytes(b"\xcf\xfa\xed\xfe")
        (_dt_app / "Contents" / "Resources").mkdir(parents=True)
        (_dt_app / "Contents" / "Resources" / "config.json").write_text('{"ak":""}',
                                                                        encoding="utf-8")
        # 三种链接都铺上，且都用交付里真的出现过的写法：指向同层目录（PyInstaller 的
        # ``.dylibs`` → ``__dot__dylibs``）、跨目录指（``Resources/numpy`` →
        # ``../Frameworks/PIL``，制品里就是这样）、指向文件（unzip 会把它解成一个装着目标
        # 路径的普通文件）。对拍只问「这条路径在不在」，不猜工具把链接解成了什么——猜就又
        # 回到「拿计数差异推断包坏」那个坑。
        os.symlink("__dot__dylibs", _dt_app / "Contents" / "Frameworks" / "PIL" / ".dylibs")
        # 侧车不是「本机碰巧带着元数据」才有的东西：夹具自己把元数据写上去。
        # 不这么写的后果本轮在云端看到了：干净检出里那些文件不带任何扩展属性，那台
        # runner 的 ditto 一个侧车都不产（实测 0 个），于是下面「正例必须走侧车被吸收那一格」
        # 从来就没被执行过——本机绿是因为开过 brew/下载过的文件带 com.apple.provenance。
        _xa = subprocess.run(["xattr", "-w", "com.apple.b2bcollect.sidecar", "1",
                              str(_dl / "libPIL.dylib")],
                             capture_output=True, text=True, timeout=30,
                             stdin=subprocess.DEVNULL)
        for _xf in (_dt_app / "Contents" / "MacOS" / "B2bCollectTool",
                    _dt_app / "Contents" / "Resources" / "config.json"):
            subprocess.run(["xattr", "-w", "com.apple.b2bcollect.sidecar", "1", str(_xf)],
                           check=False, timeout=30, stdin=subprocess.DEVNULL,
                           capture_output=True)
        os.symlink("../Frameworks/PIL", _dt_app / "Contents" / "Resources" / "numpy")
        os.symlink("config.json", _dt_app / "Contents" / "Resources" / "link_to_file")
        os.symlink("PIL", _dt_app / "Contents" / "Frameworks" / "link_to_dir")
        _dz = _ztmp / "ditto_made.zip"
        subprocess.run([ditto, "-c", "-k", "--keepParent",
                        str(_dt_app), str(_dz)], check=True, timeout=300,
                       stdin=subprocess.DEVNULL, capture_output=True)
        with zipfile.ZipFile(_dz) as _zf:
            _entries = [i.filename for i in _zf.infolist()]
            _lnk_dir = [i.filename for i in _zf.infolist() if stat.S_ISLNK(i.external_attr >> 16)]
        _assert_eq(len(_gate._expected_files(_dz)[0]), len(_entries),
                   f"目录条目与符号链接条目都要算一条路径（少算一条就少判一次交付，多算就在"
                   f"每个包上假红）：条目 {len(_entries)} 个")
        _assert(any(n.endswith(".dylibs") for n in _lnk_dir),
                f"夹具自证：包里必须真的带上「指向目录的符号链接」这一格（云端那 168 条就是它，"
                f"链接条目={_lnk_dir}）")

        def _landed(dst: Path) -> set:
            return {Path(r, x).relative_to(dst).as_posix()
                    for r, ds, fs in os.walk(dst) for x in ds + fs}

        _dst_d = _ztmp / "dz_ditto"
        subprocess.run([ditto, "-x", "-k", str(_dz), str(_dst_d)], check=True,
                       timeout=300, stdin=subprocess.DEVNULL, capture_output=True)
        _ld = _landed(_dst_d)
        # 反向对照·旧口径**整条**复现：条目侧只算不带斜杠的、落地侧只收 files（门 332d5c8 之前
        # 两边就是这么写的），报出来的那份清单就是云端日志里的原话「少了 168 个路径，例如
        # …/PIL/.dylibs」。这里必须能复现出同一条差集，否则「修好了」只是我把差集换成了另一组
        # 数字，而不是这一格真的过了。
        with zipfile.ZipFile(_dz) as _zf:
            _want_old = {i.filename for i in _zf.infolist() if not i.filename.endswith("/")}
        _ld_files_only = {Path(r, x).relative_to(_dst_d).as_posix()
                          for r, ds, fs in os.walk(_dst_d) for x in fs}
        _miss_old = sorted(_want_old - _ld_files_only)
        _assert(any(x.endswith(".dylibs") for x in _miss_old)
                and all(not x.endswith("/") for x in _miss_old),
                f"反向对照：旧口径必须在这份包上把「落地后是目录的符号链接」报成少落地"
                f"（实际={_miss_old}）——报不出来就是夹具没覆盖到出问题那一格")
        _assert(not (set(_miss_old) & {n for n in _entries if n.endswith("/")}),
                f"反向对照的另一半：旧口径报的不是目录条目（它们条目侧就不参与比较），所以修的是"
                f"落地清单的收法，不是「ditto 怎么拼目录条目」（实际={_miss_old}）")
        _sp_old = _gate.extract_with_os_tools(_dz, platform="darwin")
        _assert_eq(_sp_old, [],
                   f"正例：同一个口径修好后，ditto 与 unzip 对这份真 ditto 包（空目录、隐藏目录、"
                   f"三种符号链接）都必须对拍得上（实际={_sp_old}）")

        # 夹具自证：这条正例真的经过了「AppleDouble 侧车被吸收」那一格。本地 ditto 打的包里
        # 有 `._X` 侧车，而 ditto 解完后那些名字一个都不落地（它把元数据读回基文件）；unzip
        # 不认识侧车，原样落下来。少了这一格自证，上面的 ✅ 只说明「两边都全量落地」，云端
        # 那种分歧压根没跑到——那正是本轮第 5 次犯的错：正例没覆盖到出问题的那一格。
        _side = [n for n in _entries if n.rsplit("/", 1)[-1].startswith("._")]
        if not _side:
            # 夹具已经显式写了扩展属性，这台机器的 ditto 还是不产侧车：那就是它把元数据存在
            # zip 自己的扩展字段里。这种包在交付链上是另一种真形状，上面的正例照样得对拍得上；
            # 但「侧车被 ditto 吸收」这一格今天确实没验到，必须当场念出来（与上面「这台机器
            # 没有 unzip：跳过那几步」同一口径）——把没验到写成验过，正是这一族判据反复付的学费。
            _assert(_xa.returncode == 0,
                    f"夹具自己写扩展属性这一步就失败了，下面那句「这台机器不产侧车」就没有出处"
                    f"（xattr 退出码 {_xa.returncode}：{_xa.stderr.strip()[:120]}）")
            print("   · 这台机器的 ditto 不产 AppleDouble 侧车（已显式写扩展属性仍为 0）："
                  "侧车吸收那一格今天没验到，上面的正例只覆盖了无侧车那一支")
        else:
            _assert(_side and not (set(_side) & _ld),
                    f"夹具自证：ditto 造的包里有 {len(_side)} 个 AppleDouble 侧车条目（由夹具"
                    f"自己写的扩展属性产生，不依赖本机碰巧有什么），而 ditto 自己解完"
                    f"一个都不留（读回成元数据）——正例走的必须是这一格（实际落地含侧车="
                    f"{sorted(x for x in _ld if x.rsplit('/', 1)[-1].startswith('._'))}）")

        # 顶层那一格的判据按「层级 + 条目身份」，不按尾斜杠的拼写（`_is_under`）。这里造的
        # 是结构、不是交付字节：真 ditto 与真 unzip 都拒绝「目录条目不带斜杠」的包（实测
        # ditto rc=1、unzip rc=2），所以那种拼法不进解包对拍，只进结构判据——它要防的是
        # 门把自己的口径押在拼写上，不是防一个真实存在的包形状。
        _sz = _ztmp / "shape_struct.zip"
        with zipfile.ZipFile(_sz, "w") as _z:
            _z.writestr("app/", "")
            _z.writestr("app/Contents/Frameworks/PIL/.dylibs/", "")
            _z.writestr("app/Contents/MacOS/B2bCollectTool", b"MZ\x90\x00")
            _z.writestr("readme.txt", b"hi")
        _top_ok = _gate.check_zip_entries(_sz, top="app", root_files=("readme.txt",))
        _assert_eq(_top_ok, [],
                   f"正例：目录条目（带斜杠）算落在顶层里，根上那份说明也算合格（实际={_top_ok}）")
        _top_bad = _gate.check_zip_entries(_sz, top="appX", root_files=("readme.txt",))
        _assert(any("顶层" in x for x in _top_bad),
                f"反向对照：顶层目录名真的对不上时必须报散出来（实际={_top_bad}）")
        # 根上躺着一个**与顶层目录同名但身份是普通文件**的条目：按拆层判会把它算进顶层里
        # （假绿），它根本不是那个目录，用户解压后拿到「一个文件 + 一个同名目录」。这一条
        # 是把 `_is_under` 从「只看第一段」收紧到「第一段 + 身份」的依据。
        _fz = _ztmp / "shape_file_named_top.zip"
        with zipfile.ZipFile(_fz, "w") as _z:
            _z.writestr("app", b"not a directory")
            _z.writestr("app/Contents/MacOS/B2bCollectTool", b"MZ\x90\x00")
            _z.writestr("readme.txt", b"hi")
        _fp = _gate.check_zip_entries(_fz, top="app", root_files=("readme.txt",))
        _assert(any("散出来" in x and "app'" in x.replace('"', "'") for x in _fp),
                f"反向对照：根上同名普通文件必须被报成散出来（实际={_fp}）")
        # 必需项也不能由目录条目冒充：同名的目录条目不算那份说明在场。
        _dz2 = _ztmp / "shape_dir_masquerade.zip"
        with zipfile.ZipFile(_dz2, "w") as _z:
            _z.writestr("app/Contents/MacOS/B2bCollectTool", b"MZ\x90\x00")
            _z.writestr("readme.txt/", "")
        _mp = _gate.check_zip_entries(_dz2, top="app", root_files=("readme.txt",))
        _assert(any("根上少了" in x and "readme" in x for x in _mp),
                f"反向对照：必需项被同名目录条目冒充时必须报「根上少了」（实际={_mp}）")
        _absent2 = _gate.check_zip_entries(_sz, top="app", root_files=("readme",))
        _assert(any("根上少了" in x for x in _absent2),
                f"反向对照：根上必需项写错名字（少了 readme 这一条）也要报（实际={_absent2}）")
        _dp = _gate.extract_with_os_tools(_dz, platform="darwin")
        _assert_eq(_dp, [],
                   f"正例：真 ditto 的包（带空目录、隐藏目录、四种符号链接与侧车）在 ditto "
                   f"与 unzip 上都必须对拍得上，实际={_dp}")
        _fake_dz = _ztmp / "fake_drop"
        _fake_dz.mkdir(parents=True)
        (_fake_dz / "unzip").write_text(
            f"#!{sys.executable}\n"
            "import sys, os, zipfile\n"
            "a = sys.argv\n"
            "d = a[a.index('-d') + 1]\n"
            "z = [x for x in a if x.endswith('.zip')][0]\n"
            "zf = zipfile.ZipFile(z)\n"
            "zf.extractall(d)\n"
            # 工具**凭空造**一个名字也要被抓到：把某个文件改名落地（多出来的名字既不是条目、
            # 也不是任何条目的父目录）。这是「extra」那一路唯一的真语义；把「解包工具自己得建
            # 父目录」当成违规，就是云端那一条假红的第二次。
            "os.rename(os.path.join(d, 'B2bCollectTool.app/Contents/MacOS/B2bCollectTool'),\n"
            "           os.path.join(d, 'B2bCollectTool.app/Contents/MacOS/B2bCollectTool_RENAMED'))\n",
            encoding="utf-8")
        (_fake_dz / "unzip").chmod(0o755)
        with _path_first(_fake_dz):
            _dr = _gate.extract_with_os_tools(_dz, platform="darwin")
        _assert(any("unzip" in x and ("少了" in x or "凭空" in x or "没有" in x) for x in _dr),
                f"反向对照：工具把一个名字改写到别的地，必须被对拍抓出来（实际={_dr}）")
    else:
        print("   · 这台机器没有 ditto：第 ⑤ 步跳过（Linux CI 就是这样；交付链上 Mac 那一路"
              "的门跑在 macos runner 上，那边有）")

    shutil.rmtree(_ztmp, ignore_errors=True)

    # 续行写法（`extract_zip x \` 换行 `|| {…}`）是真实脚本里就在用的形状：按行判却不
    # 认续行，就会把接住了退出码的调用报成没接住——假阳性的下场是这条门被人整条关掉。
    _cont = ('extract_zip a.zip "$WORK/win" \\\n'
             '  || { echo bad; exit 6; }\n')
    _body = _ok_work[:_ok_work.index("if ! extract_zip")]
    _assert_eq(_workdir_problems(_body + _cont), [],
               "正例：调用写成跨行 `|| {…}` 同样算接住了退出码（续行必须并成一句再判）")
    _assert_eq(len(_workdir_problems(_body + _cont.replace("|| {", "then {"))), 1,
               "反向对照：跨行调用没接住退出码（换成 `then {`）必须恰好报一条")

    # ── 硬门第 17 条：交付 zip 的门必须真的挂在链路上（结构判据，逐个接线点拔一次）──
    # 上面那一段验的是**字节与真解一次**；这一段验的是**链路**：门有几路、生产者接没接、
    # Mac 侧调没调、退出码接没接住。字节会一直变，接线一旦断了却是「日志照样打 ✅」——
    # 本轮我自己就写漏过一次（真解一次的 ep 没进 return，CI 判的是退出码）。
    _g17 = _manifest_gate()
    _asm17_path = _REPO / ".github" / "scripts" / "package_win_artifacts.py"
    _zip17_path = _REPO / ".github" / "scripts" / "check_release_zip.py"
    _asm17 = _asm17_path.read_text(encoding="utf-8", errors="replace")
    _zip17 = _zip17_path.read_text(encoding="utf-8", errors="replace")
    _assert_eq(_g17.check_release_zip_chain(_asm17, mac, asm), [],
               "第 17 条对当前这条链路必须无话可说（它红的时候就是交付链上少了一环的时候）")

    _t17 = Path(tempfile.mkdtemp(prefix="gate17_"))

    def _mut_gate_script(mutated: str) -> list:
        """把门脚本换成改过的副本再跑第 17 条（判据本体在磁盘上，副本只活在这一步）。"""
        _p17 = _t17 / "check_release_zip.py"
        _p17.write_text(mutated, encoding="utf-8")
        with _patch(_g17, "RELEASE_ZIP_SCRIPT", _p17):
            return _g17.check_release_zip_chain(_asm17, mac, asm)

    # a) 真解一次那一路的结果不进退出码：workflow 判的就是退出码，门只会说话不会拦人
    _got17 = _mut_gate_script(_zip17.replace("problems += ep", "pass"))
    _assert_eq(len(_got17), 1, f"反向对照：删掉 ep 接进退出码那一行要恰好报一条（实际={_got17}）")
    _assert("退出码" in _got17[0], f"报的必须是退出码这一路没接住（实际={_got17[0][:60]}）")
    # b) 「这台机器没有该工具」从 append 退回 print：没验过的一次构建照样报绿
    _got17 = _mut_gate_script(_zip17.replace(
        'problems.append(f"{zp.name}：这台机器没有 {label}', 'print(f"{zp.name}：这台机器没有 {label}'))
    _assert_eq(len(_got17), 1, f"反向对照：把「没验到」只印不报要恰好报一条（实际={_got17}）")
    _assert("这台机器没有" in _got17[0], f"报的必须是「没验」被当成「过了」（实际={_got17[0][:60]}）")
    # c) 装配主流程里不调这道门（同一条缺陷的两面，所以门报两条：没调 + 结果没接住）
    _got17 = _g17.check_release_zip_chain(
        _asm17.replace("        check_portable_zip(portable, problems)\n", ""), mac, asm)
    _assert_eq(len(_got17), 2, f"反向对照：门定义了却不调用要报「没调用」这条（实际={_got17}）")
    _assert(any("没被装配主流程调用" in x for x in _got17),
            f"两条里必须有「没被调用」这一句（实际={_got17}）")
    # d) 调用排在打 zip 之前：验的是上一轮留下的旧 zip
    _got17 = _g17.check_release_zip_chain(
        _asm17.replace("        build_portable_zip(root, app, guide, portable, problems)",
                       "        check_portable_zip(portable, problems)\n"
                       "        build_portable_zip(root, app, guide, portable, problems)"), mac, asm)
    _assert_eq(len(_got17), 1, f"反向对照：门排在打包之前要恰好报一条（实际={_got17}）")
    _assert("之前" in _got17[0], f"报的必须是先后次序（实际={_got17[0][:60]}）")
    # e) 调了，但不合格项没接进 problems：门红了自己往下走，zip 照样上传
    _got17 = _g17.check_release_zip_chain(
        _asm17.replace("check_portable_zip(portable, problems)", "check_portable_zip(portable, [])"),
        mac, asm)
    _assert_eq(len(_got17), 1, f"反向对照：结果不接进 problems 要恰好报一条（实际={_got17}）")
    _assert("接进 problems" in _got17[0], f"报的必须是结果没接住（实际={_got17[0][:60]}）")
    # f) 生产者把中文名写回条目名（GUIDE_DEST 只属于安装目录那份 NTFS 文件名）
    _got17 = _g17.check_release_zip_chain(
        _asm17.replace("z.write(guide, GUIDE_ZIP_ENTRY)", "z.write(guide, GUIDE_DEST)"), mac, asm)
    _assert_eq(len(_got17), 1, f"反向对照：条目名换回中文要恰好报一条（实际={_got17}）")
    _assert("非 ASCII 条目名写进了 zip" in _got17[0], f"报的必须是条目名字节（实际={_got17[0][:60]}）")
    # g) 条目名不再由源文件名派生（手抄第二份字面量：改一处漏一处）
    with _patch(asm, "GUIDE_ZIP_ENTRY", "usage_guide_v2.txt"):
        _got17 = _g17.check_release_zip_chain(_asm17, mac, asm)
    _assert_eq(len(_got17), 1, f"反向对照：条目名与 GUIDE_SRC 脱钩要恰好报一条（实际={_got17}）")
    _assert("GUIDE_SRC" in _got17[0], f"报的必须是派生关系断了（实际={_got17[0][:60]}）")
    # h) Mac 侧整道门缺席 / 只剩半路（不带 --os-extract 就是只判名字、没真解过一次）
    _got17 = _g17.check_release_zip_chain(_asm17, mac.replace("check_release_zip.py", "zip_tool.py"), asm)
    _assert_eq(len(_got17), 1, f"反向对照：Mac workflow 不调这道门要恰好报一条（实际={_got17}）")
    _assert(_g17.MAC_WORKFLOW_LABEL in _got17[0], f"报的必须点名 Mac 这一侧（实际={_got17[0][:60]}）")
    _got17 = _g17.check_release_zip_chain(_asm17, mac.replace("--os-extract", ""), asm)
    _assert_eq(len(_got17), 1, f"反向对照：Mac 调了门却没真解一次要恰好报一条（实际={_got17}）")
    _assert("--os-extract" in _got17[0], f"报的必须是 --os-extract 缺席（实际={_got17[0][:60]}）")

    # 取制品这一头也必须过同一道门：交付边界上的字节才是用户手里的字节。这里的门不是
    # 第二份实现（调的还是 check_release_zip.py），但 **CLI 上那两个字面量是抄的**——
    # shell 里抄一次可以接受，前提是抄错时当场响，而不是等门报出一条「顶层散出」让人去
    # 怀疑生产者。
    def _fetch_gate_problems(text: str) -> list:
        _c = "\n".join(ln for ln in text.splitlines() if not ln.strip().startswith("#"))
        _p = []
        _call = re.search(r"if ! python3 \.github/scripts/check_release_zip\.py", _c)
        if not _call:
            _p.append("取制品没在交付边界上过交付 zip 的门（云端装配验过 ≠ 用户手里这批字节验过）")
            return _p
        if "--os-extract" not in _c:
            _p.append("取制品调了 zip 的门却没带 --os-extract：只判名字，没在这台机器上真解一次")
        # 值写在 `ZGATE_ARGS=(--top X --root-file Y)` 这种数组字面量里，收尾的 `)` 不属于
        # 值。这里按「一个开关一个值」取，并把**取不到值**本身当成不合格：判据正则匹配不到
        # 某一种写法时安静地当没这项，就等于这条门只在能解析的写法上有效——而抄错的那个
        # 形状（本轮反向对照里的中文名）恰好就是解析不出来的形状。
        _tops = [v.rstrip(")") for v in re.findall(r"--top\s+(\S+)", _c)]
        _rf = [v.rstrip(")") for v in re.findall(r"--root-file\s+(\S+)", _c)]
        if len(_tops) != len(re.findall(r"--top\b", _c)) or \
                len(_rf) != len(re.findall(r"--root-file\b", _c)):
            _p.append("取制品的 --top/--root-file 有开关没解析出值（判据在这种写法上空转，"
                      "抄错了也不会红）：--tops=" + str(_tops) + " --root-file=" + str(_rf))
        if not _tops or any(t not in (asm.APP_DIR_NAME, "B2bCollectTool.app") for t in _tops):
            _p.append(f"取制品传的 --top 与装配常量 APP_DIR_NAME 对不上（实际={_tops}）："
                      "目录名漂了，门会报一条让人去找生产者的假红")
        if any(v != asm.GUIDE_ZIP_ENTRY for v in _rf):
            _p.append(f"取制品传的 --root-file 与 GUIDE_ZIP_ENTRY 不再是同一个（实际={_rf}）")
        return _p

    _assert_eq(_fetch_gate_problems(fetch), [],
               "正例：取制品在交付边界上过这道门，两个字面量与装配常量对得上")
    _assert_eq(len(_fetch_gate_problems(fetch.replace("--os-extract", ""))), 1,
               "反向对照：取制品这一路没真解一次必须报一条")
    _assert_eq(len(_fetch_gate_problems(fetch.replace(
        "--root-file usage_guide.txt", "--root-file 使用说明.txt"))), 1,
               "反向对照：--root-file 抄成中文名（正是本轮那个坏形状）必须报一条")
    _assert_eq(len(_fetch_gate_problems(fetch.replace(
        "--top B2bCollectTool ", "--top B2bCollectToolV2 "))), 1,
               "反向对照：--top 漂了必须报一条，而不是让门替它编一条误导的理由")
    shutil.rmtree(_t17, ignore_errors=True)


    # 自检报告的落点由**产物自己回答**（--state-dir），两个 Mac 入口都不许在 shell 里猜目录：
    # 猜出来的那份是第二真源——候选链或布局一变（本轮受保护目录改动后落点就搬去了 ~/.b2bcollect），
    # 门禁就静默 cat 不到本次报告，CI 里看着像「产物是好的，只是这一步没打印原文」，
    # 而这种红法没人会去修（本来就拦不住上传）。查找根必须是变量，不是写死的目录。
    _assert("b2bcollect_state/acl_doctor.txt" not in mac
            and "b2bcollect_state/acl_doctor.txt" not in local,
            "两个 Mac 入口都不写死自检报告路径（Windows 那一步早就是查找，两边同规矩）")
    _REPORT_FIND = re.compile(r'find\s+"\$\{?(\w+)\}?"\s+-name\s+acl_doctor\.txt\s+-mmin\s')
    _g = _manifest_gate()
    for label, text2 in (("Mac workflow", mac), ("本机打包脚本", local)):
        _plain = _g._strip_comments(text2)
        _m = _REPORT_FIND.search(_plain)
        _assert(_m, f"{label} 查自检报告的根必须是产物答出来的那个变量（写死目录=第二份真源，"
                    f"布局一变就静默找不到本次报告）")
        _assert("--state-dir" in _plain,
                f"{label} 落点是问产物要的（--state-dir），不是 shell 自己算的")
        _assert("set +e" not in _plain,
                f"{label} 不许用 set +e 关掉整段保护（第 15 条第三半：接管一条命令的退出码"
                f"用 `cmd || rc=$?` 就够，区间里后来人插的那一句失败不会有人知道）")
    # 反向对照：上面那条正则抓不住旧写法就是空判据（旧写法正是「按 dist 递归找」）
    _assert_eq(_REPORT_FIND.search('log=$(find b2bcollect_build/dist -name acl_doctor.txt '
                                   '-mmin -10 2>/dev/null | head -1 || true)'), None,
               "反向对照：查找根写成 dist 目录必须落到判据外面（这条判据要的就是「不许写死根」）")
    _assert(_REPORT_FIND.search('LOG=$(find "$STATE_DIR" -name acl_doctor.txt -mmin -10)'),
            "反向对照：同一处换成产物答出来的变量就得匹配上（不然上面那条红永远修不掉）")
    # Actions 的 bash 步默认 -e：不收住自检退出码，::error:: 那行永远打不出来，日志里只剩
    # 一句 exit code 1，分不清是产物坏了还是这一步自己坏了。
    _assert(re.search(r'--acl-doctor"?\s+\|\|\s+(rc|RC)=\$', _g._strip_comments(mac)),
            "Mac workflow 用 `|| rc=$?` 收住自检退出码，失败注解才有机会打印")
    _assert(re.search(r'--acl-doctor"?\s+\|\|\s+(rc|RC)=\$', _g._strip_comments(local))
            or re.search(r'--acl-doctor.*\|\|\s*RC=\$', _g._strip_comments(local)),
            "本机脚本同样收住退出码（两个入口的失败话术要一样齐全）")

    # 注入脚本与运行时的键名契约：不一致 = 注入了等于没注入
    import oss_pool_client as _oss
    script = (_REPO / ".github" / "scripts" / "inject_build_config.py").read_text(
        encoding="utf-8")
    _assert(_oss.AK_ID_KEY in script and _oss.AK_SECRET_KEY in script,
            "注入脚本写的键名与 oss_pool_client 的常量完全一致")
    _assert("deepseek_api_key" in script, "DeepSeek 密钥也走同一次注入")

    tmp = Path(tempfile.mkdtemp(prefix="inject_"))
    script_src = _REPO / ".github" / "scripts" / "inject_build_config.py"
    with _chdir(tmp):
        # 缺 Secret：必须红，且不落任何配置文件
        env = dict(os.environ)
        for k in ("OSS_AK_ID", "OSS_AK_SECRET", "DEEPSEEK_API_KEY"):
            env.pop(k, None)
        r = subprocess.run([sys.executable, str(script_src)], env=env,
                           capture_output=True, text=True)
        _assert(r.returncode != 0, "缺 OSS Secret 时注入脚本失败退出（宁可不发包）")
        _assert("::error::" in r.stdout, "失败信息走 Actions 的 ::error:: 注解")
        _assert(not (tmp / "b2bcollect_build" / "config.json").exists(),
                "失败时不写出半份配置")

        # 齐了：写出的文件必须能被 tool_config 读到同一个值
        env2 = dict(env)
        env2.update({"OSS_AK_ID": "AKID_CI", "OSS_AK_SECRET": "SK_CI",
                     "DEEPSEEK_API_KEY": "DS_CI"})
        r2 = subprocess.run([sys.executable, str(script_src)], env=env2,
                            capture_output=True, text=True)
        _assert_eq(r2.returncode, 0, "Secrets 齐全时注入成功")
        _assert("AKID_CI" not in r2.stdout and "SK_CI" not in r2.stdout,
                "注入脚本的日志绝不带出密钥值")
        written = json.loads((tmp / "b2bcollect_build" / "config.json")
                             .read_text(encoding="utf-8"))
        _assert_eq(written.get(_oss.AK_ID_KEY), "AKID_CI",
                   "写出的键名运行时读得到（这份配置就是产物的随包配置）")
        with _workspace() as ws:
            ws["bundle"].mkdir(parents=True, exist_ok=True)
            (ws["bundle"] / "config.json").write_text(
                (tmp / "b2bcollect_build" / "config.json").read_text(encoding="utf-8"),
                encoding="utf-8")
            with _frozen(ws):
                _assert_eq(_oss.resolve_credentials(), ("AKID_CI", "SK_CI"),
                           "端到端：CI 写出的文件放进包里就读得到")

    # 密钥绝不入库这条边界不能被这次改动冲掉。判定要看**真实生效**的那份 ignore
    # 规则（本仓库的 config.json 规则在 b2bcollect_build/.gitignore，不在仓库根），
    # 再加一道 git 层面的硬证据：这个文件从来没被跟踪过。
    ignored = False
    for gitignore in (_REPO / ".gitignore", _REPO / "b2bcollect_build" / ".gitignore"):
        if gitignore.is_file() and "config.json" in gitignore.read_text(encoding="utf-8"):
            ignored = True
    _assert(ignored, "生效的 .gitignore 里仍然忽略 config.json（密钥不入库）")
    tracked = subprocess.run(["git", "-C", str(_REPO), "ls-files",
                              "b2bcollect_build/config.json"],
                             capture_output=True, text=True)
    _assert(tracked.returncode == 0 and not tracked.stdout.strip(),
            "git 确认 b2bcollect_build/config.json 未被跟踪（历史里也没进去）")


# ══════════════════════════════════════════════ H. 本地状态落盘
@contextlib.contextmanager
def _clean_state_dir():
    """清空并还原进程级的「落点缓存 + 问题清单」。

    这两样是全进程共享的，测试之间不隔离就会互相带味道（上一例发现的问题会
    出现在下一例的报告里，红得莫名其妙）。
    """
    saved = dict(ag._STATE_DIR_CACHE), list(ag._STATE_PROBLEMS)
    ag._STATE_DIR_CACHE.clear()
    del ag._STATE_PROBLEMS[:]
    try:
        yield
    finally:
        ag._STATE_DIR_CACHE.clear()
        ag._STATE_DIR_CACHE.update(saved[0])
        ag._STATE_PROBLEMS[:] = saved[1]


def _blocked_dir(root: Path, name: str = "数据池缓存") -> Path:
    """造一个「必定写不进去」的目录路径：父级是普通文件，mkdir 一定失败。

    刻意不用 chmod 做不可写目录：以管理员/root 跑的机器上 chmod 拦不住写入，
    这种测试换台机器就假绿——正是本轮要根治的形状。
    """
    root.mkdir(parents=True, exist_ok=True)
    blocker = root / "blocker"
    blocker.write_text("x", encoding="utf-8")
    return blocker / name


def test_state_dir():
    print("\n═══ H. 本地状态：写不进去要看得见，也要有退路 ═══")
    root = Path(tempfile.mkdtemp(prefix="state_"))
    app = root / "appdata"
    good = root / "good_state"
    bad = _blocked_dir(root)

    # 0) 路径策略仍只在 edition 里判：权限模块只问投影函数，不许自己 if lite
    src = inspect.getsource(ag._preferred_state_dir)
    _assert_eq(re.findall(r"_ed\.(\w+)", src), ["pool_cache_dir"],
               "首选落点只问 edition 的档位投影（路径策略只许有一份）")

    def _fresh() -> None:
        """按「新起一个进程」的姿态重测一次：落点在一次运行内只定一次。

        这是刻意的（每次读写都去探盘会在漫游目录/网络盘上拖慢工具），所以逐例复用
        同一条首选路径时必须显式重来，否则测的是上一例留下的结论。
        """
        ag._forget_state_dir()
        del ag._STATE_PROBLEMS[:]

    with _clean_state_dir(), _patch(ag._ed, "app_data_dir", lambda: app):
        # 1) 首选写得进去：原位不动。内部同事的历史缓存都在桌面目录，搬家 = 重下整池
        with _patch(ag._ed, "pool_cache_dir", lambda default: str(good)):
            _fresh()
            _assert_eq(ag._state_dir(), good, "首选可写时不搬家（老缓存继续可用）")
            _assert_eq(ag.state_problems(), [], "没故障就不制造故障")
            _assert_eq(ag._state_dir(), good, "同一进程内落点稳定（不会一会儿东一会儿西）")
            parents = {ag._cache_file().parent, ag._device_file().parent,
                       ag._claim_file().parent}
            _assert_eq(parents, {good}, "三份状态文件同源一个目录（不许散在两地）")

        # 2) 首选写不进去：退到应用数据目录下的备用落点，并把问题如实记下来
        with _patch(ag._ed, "pool_cache_dir", lambda default: str(bad)):
            _fresh()
            chosen = ag._state_dir()
            _assert_eq(chosen, app / ag.BACKUP_STATE_SUBDIR,
                       "桌面落不下去时用备用目录，而不是静默放弃落盘")
            _assert(tc.probe_writable(chosen), "备用目录真的写得进去")
            problems = ag.state_problems()
            _assert(any("不可写" in p for p in problems),
                    f"首选不可写这件事必须现形（实际：{problems}）")
            _assert(any(str(bad) in p for p in problems),
                    "问题里带上是哪一处，远程排障才不用猜")
            # 磁盘副本真的能落地：断网时沿用上一次身份等级靠它
            _assert(ag._write_json(ag._cache_file(), {"level": 1}),
                    "退到备用目录后名单副本写成功")
            _assert(ag._cache_file().is_file(), "副本确实落在磁盘上（不是只返回成功）")

        # 3) 两处都写不进去：不瞎搬家（保持首选），但一定把后果说清楚
        with _patch(ag._ed, "pool_cache_dir", lambda default: str(bad)), \
             _patch(ag._ed, "app_data_dir", lambda: _blocked_dir(root / "b2", "appdata")):
            _fresh()
            _assert_eq(ag._state_dir(), bad, "无路可退时留在首选目录（不拿猜测定落点）")
            _assert(any("无法沿用上一次的身份等级" in p for p in ag.state_problems()),
                    "最坏情况把代价说明白：断网时会被判成丐版")
            _assert(ag._write_json(ag._cache_file(), {"level": 1}) is False,
                    "写失败返回 False（调用方要能判断）")

        # 4) 自愈：写失败 = 落点判定的凭据失效，之后封锁解除要能自己回到首选目录。
        #    少了这一步，「桌面被安全软件临时拦一下」的机器整个进程都会留在备用目录。
        c4 = _blocked_dir(root / "c4")               # root/c4/blocker/数据池缓存
        with _patch(ag._ed, "pool_cache_dir", lambda default: str(c4)):
            _fresh()
            backup = ag._state_dir()
            _assert_eq(backup, app / ag.BACKUP_STATE_SUBDIR, "先退到备用目录")
            (root / "c4" / "blocker").unlink()       # 封锁解除：同一条首选路径又可写了
            _assert_eq(ag._state_dir(), backup,
                       "没有新的失败证据就不搬家（一次运行内落点保持稳定）")
            for stale in backup.glob("*"):
                stale.unlink()
            backup.rmdir()
            backup.write_text("占位：让备用目录变成写不进去的地方", encoding="utf-8")
            _assert(ag._write_json(ag._cache_file(), {"level": 1}) is False,
                    "备用目录也写不进去时如实返回失败")
            _assert_eq(ag._state_dir(), c4,
                       "写失败之后重新判定：封锁已解除的首选目录回来了")
            _assert(tc.probe_writable(c4), "重新判定选中的目录真的写得进去")
            backup.unlink()                          # 还原备用目录，别把脏状态留给下一例

        # 5) 落盘失败不许静默：写不进去必须进问题清单
        _fresh()
        _assert(ag._write_json(bad / ".acl_cache.json", {"a": 1}) is False,
                "写到不可用路径返回失败")
        _assert(any(".acl_cache.json" in p and "失败" in p for p in ag.state_problems()),
                "具体是哪份文件写失败也要看得见")

    # 6) 探针模块自己没了（打包清单漏收 tool_config）：不拿假结论搬家、也不凭空报故障
    with _clean_state_dir():
        saved_tc = sys.modules.get("tool_config")
        try:
            sys.modules["tool_config"] = None
            for case_dir in (good, bad):
                _fresh()
                with _patch(ag._ed, "pool_cache_dir", lambda default, d=case_dir: str(d)):
                    _assert_eq(ag._state_dir(), case_dir,
                               f"探针不可用时保持首选路径（{case_dir.name}）")
            _assert_eq(ag.state_problems(), [],
                       "探针不可用不是一条故障：产物缺模块由 --acl-doctor 的模块类退出码说")
        finally:
            sys.modules["tool_config"] = saved_tc

    # 7) 测试可替换性不变量：_state_dir 是本地状态**唯一**的替换点
    with _patch(ag, "_state_dir", lambda: good), _clean_state_dir():
        _assert_eq(ag._cache_file().parent, good,
                   "替换 _state_dir 就能整体改道（离线夹具依赖这一点）")
        _assert(ag._device_file().parent == good and ag._claim_file().parent == good,
                "三份文件都经过同一个替换点，漏一份就会有人写到真实桌面")


# ══════════════════════════════════════════════ I. 时钟偏差自愈
class _Resp:
    def __init__(self, status=200, body="", headers=None, payload=None):
        self.status_code = status
        self.text = body
        self.headers = headers or {}
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class _FakeSession:
    """记下每次请求的**头**：时钟校正必须体现在重新签名的 Date 上。

    只重发旧头等于把同一个 403 再收一遍——那是「重试了」而不是「修好了」，
    两者在日志里长得一模一样，只有 Date 能区分。
    """

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.headers = {}

    def request(self, method, url, headers=None, **kw):
        self.calls.append((method, url, dict(headers or {}), kw))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _client_with(session) -> oss.OSSPoolClient:
    c = oss.OSSPoolClient(ak_id="AK_TEST", ak_secret="SK_TEST")
    c._session = session
    return c


@contextlib.contextmanager
def _no_skew():
    """校正量是进程级状态，测试必须自己收尾（否则会污染同进程的其它用例）。"""
    saved = oss._CLOCK_OFFSET_S
    oss._CLOCK_OFFSET_S = 0.0
    try:
        yield
    finally:
        oss._CLOCK_OFFSET_S = saved


def _skew_response(seconds_ahead: float = 7200.0) -> _Resp:
    return _Resp(
        403,
        "<Error><Code>RequestTimeTooSkewed</Code><Message>The difference between "
        "the request time and the current time is too large.</Message></Error>",
        {"Date": formatdate(time.time() + seconds_ahead, usegmt=True)})


def test_clock_skew():
    print("\n═══ I. 时钟偏差：签名时刻被服务端拒掉时自己校正 ═══")
    import b2bcollect_gui as gui
    with _no_skew():
        # 1) 判据本身：只有「服务端明说时刻偏太远」才值得自己修
        _assert(oss._is_clock_skew(403, "<Code>RequestTimeTooSkewed</Code>"),
                "RequestTimeTooSkewed 认得出来")
        _assert(oss._is_clock_skew(403, "The difference between the request time "
                                        "and the current time is too large."),
                "只有英文原文时也认得出来")
        _assert(not oss._is_clock_skew(403, "<Code>AccessDenied</Code>"),
                "普通 403（密钥/权限）不算时间问题")
        _assert(not oss._is_clock_skew(500, "RequestTimeTooSkewed"),
                "非 403 不碰（不是这一类故障的形态）")

        # 2) 校正来源只有服务端时间：解析不了、或偏差不大，就一律不瞎改
        _assert_eq(oss._apply_server_date(""), None, "没有 Date 头就不校正")
        _assert_eq(oss._apply_server_date("昨天天气不错"), None, "读不懂的时间不校正")
        _assert_eq(oss._apply_server_date(formatdate(time.time() + 5, usegmt=True)),
                   None, "五秒钟的抖动不值得为此改状态")
        with _no_skew():
            _assert(abs(oss._apply_server_date(
                formatdate(time.time() + 7200, usegmt=True)) - 7200) < 120,
                "两小时的偏差按服务端时间校正")
            _assert(abs(oss.clock_offset() - 7200) < 120, "校正量可被外部读到（自检报告用）")
            signed = parsedate_to_datetime(oss._gmt_date())
            skew = abs((signed - datetime.now(timezone.utc)).total_seconds())
            _assert(skew > 7080, "签名用的 Date 跟着校正量走（不是只记了个数）")
            _assert("7200" not in oss.clock_offset_text() and "慢" in oss.clock_offset_text(),
                    f"人话版说明不含原始秒数（{oss.clock_offset_text()}）")

        # 3) 端到端：先 403 后 200，重试必须带**新** Date
        sess = _FakeSession([_skew_response(), _Resp(200, payload={"ok": 1},
                                                     headers={"ETag": '"e1"'})])
        data, etag = _client_with(sess).download_json("acl/whitelist.json")
        _assert_eq(data, {"ok": 1}, "校正后这一次读到了名单")
        _assert_eq(etag, '"e1"', "ETag 照常返回")
        _assert_eq(len(sess.calls), 2, "时间偏差只重试一次（不做无限重试）")
        _assert(sess.calls[0][2]["Date"] != sess.calls[1][2]["Date"],
                "重试用的是重新签名的 Date，不是把旧头重发一遍")
        _assert(oss.clock_offset() > 6000, "自愈过程确实改了校正量")

        # 4) 其它 403 一次都不重试：那是密钥/权限问题，重试只会把启动拖慢
        sess2 = _FakeSession([_Resp(403, "<Code>AccessDenied</Code>",
                                    {"Date": formatdate(usegmt=True)})])
        _assert_raises(OSSPoolError,
                       lambda: _client_with(sess2).download_json("acl/whitelist.json"),
                       "被拒的下载照旧抛错")
        _assert_eq(len(sess2.calls), 1, "AccessDenied 不重试（不把 403 当时间问题硬试）")

        # 5) 拿不到服务端时间就不猜：错误原样抛出，交给分类与文案说话
        with _no_skew():
            sess3 = _FakeSession([_Resp(403, "<Code>RequestTimeTooSkewed</Code>")])
            _assert_raises(OSSPoolError,
                           lambda: _client_with(sess3).download_json("acl/x.json"),
                           "没有时间源时如实失败（不拿猜测定时刻）")
            _assert_eq(len(sess3.calls), 1, "没校正成功就不重试")
            _assert_eq(oss.clock_offset(), 0.0, "校正量没被瞎改")

        # 6) 出网只有一处：六个方法全部走 _send，组合方法不自己碰 session。
        #    漏掉哪个方法，哪个方法就在一台时钟不准的机器上稳定失败，而症状看起来
        #    又像密钥问题——所以这条「只许有一处」的约束必须在文本上钉住。
        for name in ("download_json", "upload_json", "head_etag",
                     "delete_key", "list_keys", "copy_key"):
            _assert("_send" in inspect.getsource(getattr(oss.OSSPoolClient, name)),
                    f"{name} 走 _send（时钟校正在这条路径上也生效）")
        for name in ("upload_json_atomic", "key_exists"):
            _assert("self._session" not in inspect.getsource(
                getattr(oss.OSSPoolClient, name)),
                f"{name} 不自己出网（复用上面的方法，自愈自动跟着来）")
        outlets = [ln.strip() for ln in _OSS_SRC.splitlines()
                   if "self._session." in ln and 'headers["User-Agent"]' not in ln]
        _assert_eq(len(outlets), 1, "整个客户端只有一个真正出网的语句")
        _assert_eq([ln for ln in outlets if ".request(" not in ln], [],
                   "唯一出网点是 _send 里的 .request（绕过它 = 那条路径没有自愈）")

        # 7) 分类：自愈没成之后，别再把它说成「密钥失效」
        kind, _detail = AccessGuard._classify_oss_error(
            OSSPoolError("下载失败 HTTP 403: <Code>RequestTimeTooSkewed</Code>"))
        _assert_eq(kind, "clock", "时间偏差单独一类，不混进 denied（否则两边互相指）")
        text = AccessGuard._unreachable_text("clock")
        _assert("时间" in text and "检查网络" not in text,
                f"这类故障既不叫人查网络也不叫人换包（{text[:40]}）")
        _assert(not _misleads_to_whitelist(text), "时间问题不叫人去找白名单")
        _assert("自动设置时间" in AccessGuard.SERVICE_NEXT_STEP["clock"],
                "下一步就给到校准时间这一步操作")
        _assert_eq(ag._DOCTOR_KIND_CODE["clock"], ag.DOCTOR_EXIT_CLOCK,
                   "--acl-doctor 有专属退出码（CI 红在这里时不用猜）")
        t, adv = gui.MainWindow._acl_denial(text)
        _assert("时间" in t and not _misleads_to_whitelist(adv),
                f"界面标题也说是时间问题（{t}）")

        # 8) 自愈成功后用户就不该看到故障：doctor 里这一行只是把做过的事写出来
        with _no_skew():
            _assert("未校正" in oss.clock_offset_text(), "没校过时不写出一句像故障的话")
        _assert("时钟校正" in inspect.getsource(ag.doctor_lines),
                "--acl-doctor 报告里带上时钟校正情况")

        # 9) 唯一出网点还负责把裸 requests 异常转成**可分类**的错误（它只认
        #    OSSPoolError 家族）。六个方法各写一份 try/except 迟早漏一个，漏掉的那
        #    个会把异常原样抛上去，结果又是「一句人话都没有」。
        import requests as _rq
        ssl_err = _rq.exceptions.SSLError(
            "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: "
            "unable to get local issuer certificate")
        conn_err = _rq.exceptions.ConnectionError("Connection to host lost")

        # 退避睡眠在测试里必须换成计数：真睡就是每个方法 1.8 秒，而「睡了几次」恰好
        # 就是这条路径上最该被钉住的东西（重试次数）。
        sleeps = []

        def _raised(call, exc):
            try:
                call(_client_with(_FakeSession([exc] * 4)))
            except BaseException as got:          # noqa: BLE001 正是要抓出来看类型
                return got
            return None

        cases9 = (
            ("head_etag", lambda c: c.head_etag("acl/whitelist.json")),
            ("delete_key", lambda c: c.delete_key("acl/x.json")),
            ("copy_key", lambda c: c.copy_key("a.json", "b.json")),
            ("list_keys", lambda c: c.list_keys("1699_users")),
            ("upload_json", lambda c: c.upload_json("acl/x.json", {"a": 1})),
            ("download_json", lambda c: c.download_json("acl/whitelist.json")),
        )
        with _patch(oss, "_retry_sleep", lambda attempt: sleeps.append(attempt)):
            for name, call in cases9:
                err = _raised(call, ssl_err)
                _assert(isinstance(err, OSSTransportError),
                        f"{name} 的 TLS 失败转成了 OSSTransportError（不是裸 requests 异常）")
                _assert(err is not None and err.permanent and "加密连接" in str(err),
                        f"{name} 的报错说清是加密连接（{str(err)[:52]}）")
                _assert_eq(AccessGuard._classify_oss_error(err)[0] if err else "", "tls",
                           f"{name} 的失败能被权限模块归到加密连接一类")
                del sleeps[:]
                err2 = _raised(call, conn_err)
                _assert(err2 is not None and not err2.permanent and "网络" in str(err2),
                        f"{name} 的瞬时连接失败仍标成网络（该重试的那类没被一并禁掉）")
                _assert_eq(len(sleeps), oss._MAX_RETRIES - 1,
                           f"{name} 的瞬时失败跑满重试并逐次退避（韧性还在）")
            _assert_eq(AccessGuard._classify_oss_error(
                _raised(lambda c: c.head_etag("k"), conn_err))[0], "network",
                "连接类失败还是网络类（这次新增类别没有把旧类别吃掉）")

            # 10) 重试语义的**反面**：确定性失败一次都不重试，也一次都不该睡。
            #     只测「重试还在」会把「顺手永久禁掉重试」也判成通过，两边都要钉。
            del sleeps[:]
            sessP = _FakeSession([ssl_err] * 4)
            _assert_raises(OSSTransportError,
                           lambda: _client_with(sessP).download_json("acl/whitelist.json"),
                           "证书类失败直接上报")
            _assert_eq(len(sessP.calls), 1,
                       "确定性失败不重试（冷启动那 3 秒等不起三轮退避）")
            _assert_eq(sleeps, [], "确定性失败连一次退避都不睡")

            del sleeps[:]
            sessT = _FakeSession([conn_err] * 4)
            _assert_raises(OSSPoolError,
                           lambda: _client_with(sessT).download_json("acl/whitelist.json"),
                           "瞬时失败跑满重试后如实报错")
            _assert_eq(len(sessT.calls), oss._MAX_RETRIES,
                       "瞬时失败仍然重试（这次改动没把网络韧性一起删掉）")
            _assert_eq(len(sleeps), oss._MAX_RETRIES - 1,
                       "最后一次失败之后不再多睡一觉（等待预算要花在刀刃上）")


# ══════════════════════════════════════════════ J. 时间基准
@contextlib.contextmanager
def _shifted_wall_clock(seconds: float):
    """把墙上时钟整体挪一段（NTP 纠正、用户手动改时间、虚拟机恢复后回拨）。

    只挪 ``time.time``：单调时钟本来就不该被这种动作影响，能被影响到的都是真缺陷。
    """
    real = time.time
    with _patch(time, "time", lambda: real() + seconds):
        yield


class _StubClient:
    """桩权限服务：名单与设备分片都读得到（不出网，只喂判定逻辑）。"""

    bucket = "b"
    endpoint = "e"
    phone = "13417047813"

    def download_json(self, key, **_kw):
        if key == ag.ACL_KEY:
            return ({"schema": ag.ACL_SCHEMA, "accounts": [self.phone],
                     "grants": {self.phone: ag.LEVEL_FULL}}, '"etag"')
        return ({"phone": self.phone, "state": "active", "session": "sess-1",
                 "generation": 1, "device_id": ag.device_id(),
                 "device_label": "stub"}, '"etag"')


class _SilentClient:
    """一次都给不出结论的服务（用来走冷启动那段有界等待）。"""

    bucket = "b"
    endpoint = "e"

    def download_json(self, key, **_kw):
        raise OSSPoolError("下载失败（网络）: ConnectTimeout: timed out")


def test_time_basis():
    print("\n═══ J. 时间基准：改系统时间不许让权限判定失忆 ═══")
    root = Path(tempfile.mkdtemp(prefix="clock_"))

    # 1) 判据本身先自证：不可信的时间戳一律按最旧，容差以内按「刚写的」
    now = time.time()
    _assert(abs(ag._stamp_age(now - 10) - 10) < 2, "十秒前的时间戳算得出年龄")
    for junk in (0, None, "", "abc", -1, {}):
        _assert_eq(ag._stamp_age(junk), ag._STALE, f"{junk!r} 这种时间戳按不可信处理")
    _assert_eq(ag._stamp_age(now + 3600), ag._STALE,
               "落在将来一小时 = 不可信（这台机器的系统时间被改过）")
    _assert_eq(ag._stamp_age(now + 30), 0.0,
               "几秒的抖动按刚写的处理（不因为抖动误杀一份好副本）")

    with _workspace():
        # 2) 进程内新鲜度与墙上时钟跳变无关：这条正是「被删的账号还能用」的入口
        with _patch(AccessGuard, "_build_client", staticmethod(lambda: _StubClient())):
            g = AccessGuard(ttl=0.05)
            g._account_reader = lambda: _StubClient.phone
            _assert(g._fetch() is True, "桩服务下这次刷新算成功")
            calls = []
            with _patch(g, "_start_refresh", lambda: calls.append(1)):
                with _shifted_wall_clock(-7200.0):
                    time.sleep(0.08)          # 真实过了 80ms > ttl
                    g.tick()
                    _assert_eq(calls, [1],
                               "时钟被往回调两小时后仍然按 ttl 过期刷新（缓存不会「永远新鲜」）")
                    g._fetched_at = time.monotonic()
                    g._bind_at = time.monotonic()
                    del calls[:]
                    g.tick()
                    _assert_eq(calls, [], "没到 ttl 就不多跑一趟（自愈不等于无脑重试）")

        # 3) 冷启动那段有界等待：等待途中时间被纠正也不许提前结束，更不许变成卡死
        #    必须换一间全新的状态目录：上一例的刷新刚把名单副本写到盘上，紧接着
        #    这一例要模拟的却是「本地什么都没有」的冷启动——复用同一间屋子的话，
        #    _load_disk_once 会拿上一例的副本当自己的，测试测的就不再是冷启动，
        #    而是「有副本但没绑定」那条完全不同的分支（症状：期望网络文案，
        #    实际拿到设备绑定文案，而且只在用例顺序变动时才复现）。
        with _workspace(), \
             _patch(AccessGuard, "_build_client", staticmethod(lambda: _SilentClient())), \
             _patch(ag, "FIRST_FETCH_WAIT_SECONDS", 0.4):
            g2 = AccessGuard(ttl=60.0)
            g2._account_reader = lambda: _StubClient.phone
            real = time.time

            def _jump_midway():
                """0.15 秒之后系统时间被往前纠两小时（等待途中跳变）。"""
                return real() + (7200.0 if real() - t0 > 0.15 else 0.0)

            t0 = real()
            with _patch(time, "time", _jump_midway):
                started = time.monotonic()
                ok3, reason3 = g2.check(wait=True)
                waited = time.monotonic() - started
            _assert(not ok3, "拿不到名单就是拒绝（fail-closed）")
            _assert(0.35 <= waited < 1.2,
                    f"等待按预算走完就收口：实际 {waited:.2f}s（墙上时钟跳变不参与计时）")
            _assert_eq(reason3, AccessGuard._unreachable_text("network"),
                       "确实连不上的那一类仍然给网络文案（新增类别没把旧类别吃掉）")

        # 4) 磁盘副本：不可信的时间戳不再能撑起离线可用，而且这件事要看得见
        del ag._STATE_PROBLEMS[:]
        future = {"schema": ag.ACL_SCHEMA, "accounts": [_StubClient.phone],
                  "grants": {_StubClient.phone: ag.LEVEL_FULL},
                  "grants_declared": True, "fetched_at": time.time() + 7200}
        _write(ag._cache_file(), future)
        g4 = AccessGuard(ttl=60.0)
        g4._load_disk_once()
        _assert_eq(g4._accounts, None, "时间戳落在将来的副本被丢弃（未来时间戳不是通行证）")
        _assert(any("时间戳不可信" in p for p in ag.state_problems()),
                f"这份副本为什么被丢掉要写进自检清单（{ag.state_problems()}）")
        del ag._STATE_PROBLEMS[:]
        _write(ag._cache_file(), dict(future, fetched_at=time.time() - 3600))
        g5 = AccessGuard(ttl=60.0)
        g5._load_disk_once()
        _assert_eq(g5._accounts, [_StubClient.phone], "一小时前的正常副本照旧可用")
        _assert_eq(ag.state_problems(), [], "正常路径不制造故障")

        # 5) 认领记录里的同一套判据（断网时它顶替远端分片）
        _write(ag._claim_file(), {_StubClient.phone: {
            "session": "sess-1", "generation": 1, "device_id": ag.device_id(),
            "claimed_ts": time.time() + 7200}})
        _assert_eq(g4._local_binding_fallback(_StubClient.phone), None,
                   "认领记录时间戳落在将来 → 不用它顶替远端（宁严勿宽）")

        # 6) 被顶号后的冷却：不可信时间戳按「冷却刚开始」算，但边界必须封住
        claim = {_StubClient.phone: {"session": "sess-1", "generation": 1,
                                     "device_id": ag.device_id()}}
        for raw, want in ((time.time() + 7200, float(ag.RECLAIM_COOLDOWN_SECONDS)),
                          (time.time() - 30, ag.RECLAIM_COOLDOWN_SECONDS - 30),
                          (time.time() - 99999, 0.0),
                          (0, 0.0),
                          ("不是数字", float(ag.RECLAIM_COOLDOWN_SECONDS))):
            _write(ag._claim_file(), dict(claim, **{
                _StubClient.phone: dict(claim[_StubClient.phone], kicked_ts=raw)}))
            got = ag._reclaim_blocked(_StubClient.phone)
            _assert(abs(got - want) <= 1.0, f"kicked_ts={raw!r} → 还要等 {got:.0f}s")
            _assert(0.0 <= got <= ag.RECLAIM_COOLDOWN_SECONDS,
                    f"冷却永远在一整段以内（{raw!r} 算出 {got:.0f}s 就是荒唐数字）")

    # 7) 文本级不变量：时间基准只许两套，写墙上时钟的地方只许是要落盘的那几个
    deadlines = re.findall(r"deadline = ([\w.]+\()", _AG_SRC)
    _assert_eq(deadlines, ["time.monotonic(", "time.monotonic("],
               "两处有界等待都用单调时钟（墙上时钟跳变不许把 3 秒变成几小时）")
    _assert_eq(re.findall(r"time\.time\(\) - self\._\w*at", _AG_SRC), [],
               "不许再用墙上时钟算进程内新鲜度")
    wall = [ln.strip() for ln in _AG_SRC.splitlines()
            if "time.time()" in ln and not ln.strip().startswith("#")]
    _assert_eq(len(wall), 4, f"整个模块只剩 4 处墙上时钟（实际 {len(wall)} 处：{wall}）")
    for ln in wall:
        _assert(any(k in ln for k in ("claimed_ts", "kicked_ts", "fetched_at", "delta =")),
                f"这一处墙上时钟必须是**要落盘**的时间戳或其判定：{ln}")


# ══════════════════════════════════════════════ K. 控制台编码
def _cp1252_stdout():
    """造一个「英文 Windows 控制台」：缓冲区是内存，编码是 cp1252。

    为什么要在 macOS 上模拟它：Windows 的 Python 用**系统代码页**给 stdout 编码，
    非中文系统就是 cp1252 —— 中文和 ✅ 都编不出来，直接抛 UnicodeEncodeError。
    这个坑只在 CI 的那台机器上成立，本机跑一百遍也测不出来（本轮 CI 就是这么红的：
    config.json 已经写好了，进程却死在打印成功话术那一行）。所以这里把控制台
    本身做成夹具。
    """
    buf = io.BytesIO()
    return io.TextIOWrapper(buf, encoding="cp1252", errors="strict"), buf


def _print_on_cp1252(text: str, fix=None):
    """在「英文 Windows 控制台」上真打印一次，把异常和落盘字节**带回给调用方**。

    夹具的作用域只许包住被测语句本身：断言函数自己也要 print 结论，一旦它跑在
    cp1252 流上，结论还没打出来就先抛 UnicodeEncodeError——测试框架被自己的夹具
    打死，整棵用例树崩掉，反而看不到到底是哪一条红（本套第一版就是这么炸的）。
    所以这里在交出结论之前先把 sys.stdout 换回真的那个。
    """
    stream, buf = _cp1252_stdout()
    saved = sys.stdout
    err = None
    try:
        sys.stdout = stream
        if fix is not None:
            fix()
        print(text)
    except BaseException as e:
        err = e
    finally:
        try:
            stream.flush()
        except Exception:
            pass
        raw = buf.getvalue()
        sys.stdout = saved          # 先恢复，再谈结论
    return err, raw


def _force_with_stdout(value):
    """把 sys.stdout 换成 value 后调用 force_utf8_stdout，恢复之后才交出结果。"""
    saved = sys.stdout
    try:
        sys.stdout = value
        got = tc.force_utf8_stdout()
    except BaseException as e:
        got = e
    finally:
        sys.stdout = saved
    return got


def _prints_before(src: str, needle: str) -> bool:
    """``needle`` 是否排在这段代码**第一次真正打印**之前。

    顺序就是这里的全部语义：编码修在第一次打印之后，那一行就已经抛完了，后面
    再改也换不回结论。注释行先剔掉——文档字符串里出现「print」不算打印。
    """
    body = "\n".join(ln for ln in src.splitlines() if not ln.strip().startswith("#"))
    i = body.find(needle)
    if i < 0:
        return False
    outlets = [body.find(p) for p in ("print(", "say(") if body.find(p) >= 0]
    return not outlets or i < min(outlets)


def test_console_encoding():
    print("\n═══ K. 控制台编码：中文与 ✅ 在英文 Windows 上必须说得出来 ═══")
    import b2bcollect_gui as gui

    # 1) 先把「环境确实会坏」钉住：否则后面所有断言都是在测一个不会失败的场景
    err, _ = _print_on_cp1252("✅ 已生成随包配置")
    _assert(isinstance(err, UnicodeEncodeError),
            f"cp1252 控制台上打印带 ✅ 的成功话术当场抛错（实际 {type(err).__name__}）")
    err, _ = _print_on_cp1252("写入失败")
    _assert(isinstance(err, UnicodeEncodeError),
            f"纯中文一样抛错（所以只改 emoji 是假修法，下一句中文字又撞墙）："
            f"实际 {type(err).__name__}")

    # 2) 修法本身：换编码之后同一条打印能出来，且落的是 UTF-8 字节
    fixed = []
    err, raw = _print_on_cp1252("✅ 已生成随包配置 b2bcollect_build/config.json",
                               fix=lambda: fixed.append(tc.force_utf8_stdout()))
    _assert(err is None, f"换成 UTF-8 后同一条打印不再抛错（实际 {type(err).__name__}）")
    _assert_eq(fixed, [True], "能改的流改成功了（返回值如实说）")
    _assert("随包配置" in raw.decode("utf-8") and "✅" in raw.decode("utf-8"),
            f"中文与符号都落到了字节上（{raw[:40]!r}）")

    # 3) 改不动的流不许把调用方带崩：--windowed 产物里 stdout 是 None / 不落字的
    #    NullWriter，没有 reconfigure；自检报告的价值远高于「顺便修好编码」
    got = _force_with_stdout(io.StringIO())        # 有 write、没有 reconfigure
    _assert(got is True, f"无 reconfigure 的流安静跳过（不算失败）（实际 {got!r}）")
    got = _force_with_stdout(None)                 # 打包 --windowed 的真实形态
    _assert(got is True, f"stdout 是 None 也不抛（CI 的产物门禁就长这样）（实际 {got!r}）")

    # 4) 四个会打印的入口都必须**在第一次打印之前**修好编码。它们是各自独立的进程
    #    入口（CI 脚本 / 源码 CLI / 产物自检 / 打包后的 --acl-doctor），谁都不能假设
    #    另外几个跑过；判据统一成「排在第一次打印之前」，而不是「代码里提过一句」。
    inject = (_REPO / ".github" / "scripts" / "inject_build_config.py").read_text(encoding="utf-8")
    entries = (
        ("CI 注入脚本", inject, 'reconfigure(encoding="utf-8"'),
        ("源码 CLI 入口", inspect.getsource(ag._cli_guarded), "_force_utf8_stdout()"),
        ("自检主流程", inspect.getsource(ag.run_doctor), "_force_utf8_stdout()"),
        ("产物自检入口", inspect.getsource(gui._acl_doctor_entry),
         "_tool_config.force_utf8_stdout()"),
    )
    for name, src, needle in entries:
        _assert(_prints_before(src, needle), f"{name} 第一次打印之前就修好了编码")
    # 「本地取密钥」那一支确实要 import tool_config（开发机上它就在旁边），所以
    # 判据只许管它之前的顶层部分；找不到这个分界就得直说，不能让下面那条断言
    # 因为 split 退化成整份文件而报一个看不出根因的错。
    _assert("def _from_local" in inject, "CI 脚本仍保留 --from-local 分支（本机打包同一条链）")
    head = inject.split("def _from_local")[0]
    head_code = "\n".join(ln for ln in head.splitlines() if not ln.strip().startswith("#"))
    _assert(not re.search(r"^\s*(import|from)\s+tool_config", head_code, re.M),
            "CI 脚本顶层的编码修法不依赖产物模块（模块全坏时它还得能说话）")
    _assert("HAS_TOOL_CONFIG" in inspect.getsource(gui._acl_doctor_entry),
            "tool_config 自己没加载进来时也不能抛（那正是门禁要报的故障）")

    # 5) 只许有一处实现：编码口径写三份，迟早一份改了另两份没改
    _assert_eq(len(re.findall(r"def force_utf8_stdout", _TC_SRC)), 1,
               "force_utf8_stdout 只有 tool_config 一个定义")
    _assert_eq(len(re.findall(r'reconfigure\(encoding="utf-8"', _TC_SRC)), 1,
               "tool_config 里真正改编码的语句只有一处")


# ══════════════════════════════════════════ L. 自检开关的口径一致
def test_doctor_flag_parity():
    print("\n═══ L. 自检开关：CI 写法、CLI 写法、界面写法必须是同一件事 ═══")
    import b2bcollect_gui as gui

    # 1) 两种写法在三个地方都得认：CI 的 yml 写 --acl-doctor，管理员手敲源码 CLI
    #    看帮助是 --doctor，产物 exe 收哪个由界面分派决定。历史上这三处各写一份，
    #    拼错的一方不会报错，而是**安静地走另一条路**（见下面第 3 条）。
    _assert_eq(set(ag.DOCTOR_FLAGS), {"--doctor", "--acl-doctor"},
               "access_guard 声明的自检开关就这两种写法")
    _assert_eq(set(gui._DOCTOR_FLAGS_FALLBACK), set(ag.DOCTOR_FLAGS),
               "界面那份退路副本与 access_guard 逐字一致（漏收权限模块时仍要认得）")

    # 三个发包出口都要用同一个写法：两个 CI workflow + 本机 Mac 打包脚本。
    # 少查一个出口，就等于允许那个出口用一个产物认不出来的开关。
    wf_dir = _REPO / ".github" / "workflows"
    exits = [(wf_dir / "build-b2bcollect.yml", True),
             (wf_dir / "build-b2bcollect-mac.yml", True),
             (_REPO / "debug" / "build_b2bcollect_mac_local.sh", False)]
    for p, in_ci in exits:
        name = p.relative_to(_REPO).as_posix()
        if not p.exists():
            _assert(False, f"{name} 必须还在（发包门禁的定义处）")
            continue
        text = p.read_text(encoding="utf-8")
        used = set(re.findall(r"(--acl-doctor|--doctor)", text))
        _assert(used and used <= set(ag.DOCTOR_FLAGS),
                f"{name} 里调自检用的写法都在口径内（实际 {sorted(used)}）")
        _assert("acl_doctor.txt" in text or "--acl-doctor" not in text,
                f"{name} 自检失败时会把报告原文打出来（不只剩一个退出码）")
        if in_ci:
            # CI 里必须有超时：这一步最坏的失败不是非零退出，而是一声不响地卡住
            _assert("timeout-minutes" in text,
                    f"{name} 的自检门禁带超时（卡住比非零退出更难查）")

    # 2) 界面分派本身：两种写法都进自检；带 doctor 字样却拼错的**不许**掉去建界面
    for argv, want in ((["--acl-doctor"], (True, [])),
                       (["--doctor"], (True, [])),
                       (["--windowed", "--doctor"], (True, [])),
                       ([], (False, [])),
                       (["--doctors"], (False, ["--doctors"])),
                       (["--acl-doctor-x"], (False, ["--acl-doctor-x"]))):
        _assert_eq(gui._doctor_request(argv), want, f"界面分派 {argv}")

    # 3) 反向确认这条分支真的会「挂住 CI」：拼错时主流程走到的是建界面那一行，
    #    所以这里必须靠兜底把它拦下来——判据直接读源码里的先后顺序。
    src = inspect.getsource(gui.main)
    _assert(src.index("if wrong:") < src.index("QApplication(sys.argv)"),
            "认不出的自检写法在 QApplication 之前就被拦下（否则 runner 挂到超时）")

    # 4) 权限模块没加载时退回本地常量：那正是门禁要报的故障，开关必须先认得出来
    with _patch(gui, "HAS_ACL_GUARD", False):
        _assert_eq(set(gui._doctor_flags()), set(gui._DOCTOR_FLAGS_FALLBACK),
                   "access_guard 缺席时仍认得两种写法（用退路副本）")
    with _patch(ag, "DOCTOR_FLAGS", ("--only-one-way",)):
        _assert_eq(gui._doctor_flags(), ("--only-one-way",),
                   "口径只认 access_guard 那一份，界面不另立一套")

    # 5) 源码 CLI 侧：两种写法都要真的走到自检，且「只读」约束对两种写法同样成立
    for flag in ag.DOCTOR_FLAGS:
        seen = []
        with _patch(ag, "run_doctor", lambda *a, **k: (seen.append(1), 0)[1]):
            ag._cli([flag])
        _assert_eq(seen, [1], f"python -m access_guard {flag} 真的跑了自检")
        _assert_raises(SystemExit,
                       lambda: ag._cli([flag, "--add", "13417047813"]),
                       f"{flag} 与写操作混用时当场拒绝（自检读到的会是刚写下去的东西）")

    # 6) 退出码全表由分类表派生：加了新故障类别却忘了补标签，这里立刻红
    codes = [c for c, _ in ag._DOCTOR_EXIT_TABLE]
    _assert_eq(len(codes), len(set(codes)), "退出码不重复")
    declared = {v for k, v in vars(ag).items() if k.startswith("DOCTOR_EXIT_")
                and isinstance(v, int)}
    _assert_eq(declared, set(codes),
               "每一个 DOCTOR_EXIT_* 常量都进了全表（漏一个=那种故障在帮助里没名字）")
    _assert_eq(set(ag._DOCTOR_KINDS), set(ag._DOCTOR_KIND_CODE),
               "分类表与退出码表同源（派生出来的，不是又抄一份）")
    for kind, (code, label) in ag._DOCTOR_KINDS.items():
        _assert(label and code != ag.DOCTOR_EXIT_OK, f"{kind} 有码也有短标签")
    _assert("8" in ag.DOCTOR_EXIT_HELP and "加密被拦" in ag.DOCTOR_EXIT_HELP,
            f"帮助文本里能数出全部类别（本轮就吃过「帮助只列 6 个码」的亏）："
            f"{ag.DOCTOR_EXIT_HELP}")


# ════════════════════════════════ M. workflow 步骤的脚本与 shell 必须配套
_BASH_ONLY = ("[ -f ", "[ -n ", "] ||", "|| {", "rc=$?", "$((", "&& {")
_PS_ONLY = ("Test-Path", "Start-Process", "Write-Host", "$env:", "$($", "-PassThru")


def _step_region(text: str, step_name: str):
    """取出某个 step 的正文：从它的 ``- name:`` 到下一个 ``- name:`` 之前。

    不用 PyYAML：这套离线测试跑在任何一台开发机上，多一个依赖就多一类「本机绿、
    别人红」。所以只用最笨的定位，并让调用方先确认这一步真的存在。
    """
    i = text.find(f"- name: {step_name}")
    if i < 0:
        return None
    j = text.find("- name: ", i + 1)
    return text[i:(j if j > 0 else len(text))]


def _iter_steps(text: str):
    """按 ``- name:`` 把 workflow 切成一个个 step 正文。

    不用 PyYAML：这套离线测试要跑在任何一台开发机上，多一个依赖就多一类
    「本机绿、别人红」。定位只需最笨的切法，判据是「这一步的正文里出现了什么」。
    注释行整体剔掉：注释里引用另一种 shell 的写法是写给维护者看的，不是要执行的。
    """
    heads = [(m.start(), m.group(1).strip())
             for m in re.finditer(r"^\s*-\s+name:\s*(.+)$", text, re.M)]
    for i, (pos, name) in enumerate(heads):
        end = heads[i + 1][0] if i + 1 < len(heads) else len(text)
        body = "\n".join(ln for ln in text[pos:end].splitlines()
                         if not ln.strip().startswith("#"))
        yield name, body


def _step_env(body: str) -> dict:
    """这一步 `env:` 块里的**字面量**赋值（`"${{ … }}"` 那种要 Actions 现场才解得开的跳过）。

    为什么夹具需要它：本文件的跑法是把 run 体**抠出来直接执行**，而真实 Actions 会先把
    这一步的 env 注入进程环境。需求 10B 把包名/地板从命令体提到了 step env（两条通道共用
    同一段 shell，差异只放这四个值），不注入就会得到一串假红：`ls -l "$OUT_FILE "` 展开成
    空串 → `ls: : No such file or directory` → 八条正例全倒（本轮实测）。
    只注入字面量、不替表达式编值：编一个假值进去等于让夹具决定交付物叫什么，红了也说不清。
    """
    m = re.search(r"^      env:\s*$\n((?:        [A-Za-z_][A-Za-z0-9_]*:.*\n?)+)", body, re.M)
    out = {}
    for ln in (m.group(1).splitlines() if m else []):
        key, _, val = ln.strip().partition(":")
        val = val.strip().strip('"').strip("'")
        if "${{" in val or not val:
            continue
        out[key] = val
    return out


def _step_shell(region: str, default: str) -> str:
    """这一步真正由哪个 shell 执行：显式声明优先，没声明就是 runner 的默认值。"""
    m = re.search(r"^\s*shell:\s*(\w+)", region, re.M)
    return (m.group(1).strip() if m else default)


def test_workflow_shell_syntax():
    print("\n═══ M. workflow：写的脚本必须和这一步真正用的 shell 对上 ═══")
    # 本轮 CI 的第二种红法：Windows 上 run 步**默认**是 pwsh，而构建前那道
    # 「config.json 不在位就不许出包」的硬门是 bash 写法，PowerShell 把 `[ -f ...`
    # 当类型表达式解析，ParserError 之后 PyInstaller 根本没启动。这类错误不在任何
    # 一行的逻辑里，只在「脚本语言」和「解释器」的接缝上，本机永远测不出来。
    wf = _REPO / ".github" / "workflows"
    win = (wf / "build-b2bcollect.yml").read_text(encoding="utf-8")
    mac = (wf / "build-b2bcollect-mac.yml").read_text(encoding="utf-8")
    _assert(len(list(_iter_steps(win))) >= 6 and len(list(_iter_steps(mac))) >= 6,
            "两个 workflow 都切得出至少 6 个 step（切不出来=定位失效）")

    # 1) 通用不变量：每一个 step 的脚本语法都得配上它的 shell。
    #    只在真的扫到某种写法时才下结论——每条都报一句会把 60 行无关的 ✅ 灌进
    #    输出里，而那些 ✅ 一条都没有回答「万一改坏了会怎样」。
    seen_bash = seen_ps = 0
    for text, runner, default in ((win, "Windows", "pwsh"), (mac, "macOS", "bash")):
        for name, region in _iter_steps(text):
            shell = _step_shell(region, default)
            bashy = [b for b in _BASH_ONLY if b in region]
            psky = [p for p in _PS_ONLY if p in region]
            is_bash = shell in ("bash", "sh")
            seen_bash += bool(bashy)
            seen_ps += bool(psky)
            if bashy:
                _assert(is_bash,
                        f"{runner}「{name}」里的 bash 写法 {bashy} 配的是 shell={shell}")
            if psky:
                _assert(not is_bash,
                        f"{runner}「{name}」里的 PowerShell 写法 {psky} 配的是 shell={shell}")
    _assert(seen_bash >= 3 and seen_ps >= 1,
            f"扫描确实找到了待配对的写法（bash 步骤 {seen_bash} 个 / PowerShell 步骤 {seen_ps} 个）")

    # 2) 关键步骤的存在性与各自的手法：这三样一旦被人顺手改回去，包就又变成
    #    「没凭证也照样出」「自检拿不到退出码」
    build = _step_region(win, "Build EXE with PyInstaller")
    _assert(build is not None and _step_shell(build, "pwsh") == "bash",
            "Windows 构建步骤显式 shell: bash（bash 硬门才生效，也不依赖默认值）")
    gate = _step_region(win, "Artifact doctor gate (B2bCollectTool.exe --acl-doctor)")
    _assert(gate is not None, "Windows 产物自检门禁步骤还在（发包前最后一道门）")
    if gate:
        _assert(_step_shell(gate, "bash") == "pwsh", "自检门禁显式声明 pwsh（不靠默认值）")
        for need in ("Start-Process", "-Wait", "-PassThru", "$p.ExitCode"):
            _assert(need in gate, f"自检门禁保留了 {need}（GUI 子系统程序靠它才拿得到退出码）")

    # 3) 两个平台的构建前硬门必须是**同一句**：少一边，那一边就是「没凭证也照出包」
    _assert("[ -f config.json ]" in win and "[ -f config.json ]" in mac,
            "两个 workflow 都在构建前检查随包 config.json")
    for text, path in ((win, "Windows"), (mac, "Mac")):
        _assert("不许出包" in text, f"{path} 的构建前硬门说清了后果（不是只 exit 1）")

    # 4) M 组的第三种接缝错：不是「语法配不上解释器」，而是**报错那一行自己配不上变量名**。
    #    `$MK（…）` 这种写法在 bash 里会把全角括号/全角逗号的字节一起读进变量名：开
    #    `set -u` 的步骤直接 unbound variable 炸掉（炸的是那句解释失败原因的 echo），
    #    没开的更坏——值被静默印成空，「退出码 ）」。本轮是给 NSIS 步骤干跑时当场撞上的，
    #    而同类写法在 Mac 那两个入口里已经躺了两处。判据用构建前硬门那一份，不另抄规则。
    g = _manifest_gate()
    local_sh = (_REPO / "debug" / "build_b2bcollect_mac_local.sh").read_text(encoding="utf-8")
    for label, text in (("本机 Mac 打包脚本", local_sh),
                        ("Windows workflow", win), ("Mac workflow", mac)):
        got = g.check_shell_hygiene(g._strip_comments(text))
        _assert(not got, f"{label} 里没有 $VAR 紧跟非 ASCII 的写法（{got[:1]}）")
    for script in sorted((_REPO / ".github" / "scripts").glob("*.sh")):
        got = g.check_shell_hygiene(script.read_text(encoding="utf-8", errors="replace"))
        _assert(not got, f"{script.name} 的报错文案不会自己把变量名读坏（{got[:1]}）")
    # 反向对照：判据必须真的抓得住，而不是「现在恰好没样本」。
    _assert(len(g.check_shell_hygiene('echo "退出码 $RC，见上"')) == 1,
            "反向对照：$RC 紧跟全角逗号必须判红")
    _assert(g.check_shell_hygiene('echo "退出码 ${RC}，见上"') == [],
            "反向对照：写成 ${RC} 就放行（判据抓的是写法，不是中文）")
    _assert(g.check_shell_hygiene('# 注释里的 $RC，不会被执行') == [],
            "反向对照：注释行不算（否则每条中文注释都要加花括号，门禁立刻沦为噪音）")
    # 本轮那三处实际坏掉的行，钉成「现在这个样子」：顺手改回去时用例不会跟着改。
    _assert("${RC}，报告见上" in local_sh,
            "本机脚本失败分支把退出码印得出来（${RC} 而不是 $RC）")
    _assert("退出码 ${rc}）" in mac, "Mac workflow 自检门禁的失败文案同理")

    # 4-b) 第 11 条：交付链上每一段 shell 都得过 **bash 自己的解析器**。上面 4) 那种正则
    #    只能覆盖「已经想到的写法」，而本轮真坏掉的那句是字节级的意外：
    #    `${APP_VERSION:-（空）」}` ——右花括号前一个全角标点把 bash 3.2 的词法扫描带跑，
    #    整步在 `set -e`、判据、日志统统出场之前就死在语法错误上（云端表现为「这一步只有一
    #    行 bash: -c: line N: unexpected EOF」）。判据交给解析器之后，覆盖面等于解析器的
    #    覆盖面，不再依赖哪个人记得住哪几个组合会踩雷。
    _assert_eq(g.check_shell_syntax(), [],
               "交付链 shell 语法：登记表里每份脚本 + 四个 workflow 的每个 bash 步都过 bash -n")

    # 判据必须**可被反向对照**：塞一段语法坏掉的脚本进假 workflow 要响，合法写法要不响。
    # 夹具用「哪种 bash 都判得出错」的写法（缺 fi 的 if、没闭合的引号）——本轮那句
    # `${APP_VERSION:-（空）」}` 只在 bash 3.2 上炸，拿它当反向对照会得到一条
    # 「本机红、别人绿」的判据，那和没有判据一样。它在这条链上的位置由下面两条产品侧
    # 断言钉住：坏写法不许回来，而它要保护的那段版本号判据不许被整段删掉当修法。
    def _wf(script_body: str, runner: str = "windows-latest", shell: str = "bash",
            indent: int = 8) -> str:
        step_shell = f"\n{' ' * 6}shell: {shell}" if shell else ""
        body = "\n".join(" " * indent + ln if ln.strip() else ""
                         for ln in script_body.rstrip("\n").splitlines())
        return (f"jobs:\n  b:\n    runs-on: {runner}\n    steps:\n"
                f"    - name: 被测的那一步{step_shell}\n      run: |\n{body}\n")

    _NO_FI = 'if [ -x "$MAKENSIS" ]; then\n    echo ok\n'      # 缺 fi：任何 bash 都判语法错
    _DEEP_BODY = 'if [ -x "$MAKENSIS" ]; then\n    echo ok\nfi'
    _FIXTURES = {
        "debug/zz_wf_bad_default.yml": _wf(_NO_FI, runner="macos-latest", shell=""),
        "debug/zz_wf_bad_explicit.yml": _wf(_NO_FI),
        "debug/zz_wf_good.yml": _wf('AV="$APP_VERSION"\n'
                                    '[ -n "$AV" ] || AV="（空）"\n'
                                    'echo "实际拿到「${AV}」"'),
        "debug/zz_wf_pwsh.yml": _wf('if (-not (Test-Path $exe)) { throw "没有产物" }',
                                    shell="pwsh"),
        "debug/zz_wf_inline.yml": ("jobs:\n  b:\n    runs-on: windows-latest\n    steps:\n"
                                   "    - name: 一行写完的 run\n      shell: bash\n"
                                   '      run: echo "没闭合的引号\n'),
        "debug/zz_wf_deep_indent.yml": _wf(_DEEP_BODY, indent=10),
        "debug/zz_wf_expr.yml": ("jobs:\n  b:\n    runs-on: windows-latest\n    steps:\n"
                                 "    - name: Actions 表达式\n      shell: bash\n"
                                 "      run: |\n        echo \"v=${{ github.run_number }}\"\n"),
        "debug/zz_wf_orphan.yml": "run: |\n    echo ok\njobs: {}\n",
    }
    for _rel, _src in _FIXTURES.items():
        (_REPO / _rel).write_text(_src, encoding="utf-8")
    try:
        for _rel in ("debug/zz_wf_bad_default.yml", "debug/zz_wf_bad_explicit.yml",
                     "debug/zz_wf_inline.yml"):
            _got = g.check_shell_syntax(workflows=(_rel,), sources=())
            _assert(len(_got) == 1 and "unexpected" in _got[0],
                    f"反向对照：{_rel} 那种写法必须被判红（判不住=这条门是空的）（实际={_got}）")
        _assert_eq(g.check_shell_syntax(workflows=("debug/zz_wf_good.yml",), sources=()), [],
                   "反向对照：同一位置换成合法写法就放行（否则这条门只会一直响）")
        # 只有真被 bash 解析过才算「判过了」：切错块（把 pwsh 步喂给 bash、或整段漏切）
        # 两种都会让上面两条红/绿互换，所以下面三组钉住切块本身。
        _blocks = g.run_blocks_in_workflow(_FIXTURES["debug/zz_wf_pwsh.yml"])
        _assert_eq([(n, s) for n, _, s, _b in _blocks], [("被测的那一步", "pwsh")],
                   "pwsh 步按 pwsh 登记（拿 bash 判它只会得到一条修不掉的假红）")
        _assert_eq(g.check_shell_syntax(workflows=("debug/zz_wf_pwsh.yml",), sources=()), [],
                   "pwsh 步不交给 bash 判（Windows runner 的 run 步默认就是 pwsh）")
        _blocks = g.run_blocks_in_workflow(_FIXTURES["debug/zz_wf_deep_indent.yml"])
        _assert_eq([b.splitlines() for _, _, _, b in _blocks],
                   [_DEEP_BODY.splitlines()],
                   "块标量的缩进按第一个非空行算（写死 +2 的话，多缩一级的整段会漏判）")
        _blocks = g.run_blocks_in_workflow(_FIXTURES["debug/zz_wf_expr.yml"])
        _assert("${{ " not in _blocks[0][3] and "GH_EXPR" in _blocks[0][3],
                f"Actions 表达式先换成占位符再判（原样喂给 bash 判的就不是 CI 跑的那份字节）")
        _got = g.check_shell_syntax(workflows=("debug/zz_wf_orphan.yml",), sources=())
        _assert(any("只切出" in p for p in _got),
                f"反向对照：切漏 run 步必须当场响（静默漏切=那条门根本没在跑）（实际={_got}）")
        _assert_eq(g.check_shell_syntax(workflows=(), sources=("debug/这个文件不存在.sh",)),
                   ["debug/这个文件不存在.sh 不在位（登记了却读不到，这条判据正在空转）"],
                   "反向对照：登记表里的脚本不在位要响，不许静默跳过")
    finally:
        for _rel in _FIXTURES:
            (_REPO / _rel).unlink(missing_ok=True)
    # 本轮那一处的两头：坏写法不在交付链上，且它保护的那段判据还在（把整段删掉也能
    # 让上面所有条变绿，那种「修法」必须被这一条抓住）。读的是**切出来的要执行的字节**，
    # 不是整个文件：注释里留着「为什么不用这种写法」是有意写下的解释，不该被判成违规。
    _win_exe = g._strip_comments("\n".join(b for _, _, s, b in g.run_blocks_in_workflow(win)
                                            if s == "bash"))
    _assert(len(_win_exe) > 2000 and 'MAKENSIS' in _win_exe,
            f"从真 workflow 切出来的 bash 字节非空（切空了下面两条就是空的；实得 {len(_win_exe)} 字符）")
    _assert("${APP_VERSION:-（空）" not in _win_exe,
            "要执行的脚本里没有 ${VAR:-中文} 这种把非 ASCII 写进参数展开的写法")
    # 版本号那一段的「在案」现在长这样：两格都问 tool_version，而且**不许**再出现 shell
    # 自己拆 16 位的算式。后半句必须显式判「不在」：只判前一句在的话，有人把老算式加回来
    # 当兜底（两份实现同时在场、哪天算出两个数）这条门照样是绿的，而本轮拆掉的就是它。
    _assert("DV_SHOW=" in _win_exe
            and "--print-field display" in _win_exe
            and "--print-field product" in _win_exe
            and "$((APP_VERSION" not in _win_exe,
            "版本号那一段（问 tool_version 拿两格 + 展示用的空值兜底）仍在案，且 shell 不再"
            "自己拆 16 位字段——修语法不是把判据删掉，也不是把老算式留在原地当兜底")
    # 登记表本身也要在案：新增 workflow 忘了登记=那条流水线的 shell 没人判，
    # 而磁盘上明明有文件，看上去像「都验过了」。
    _wf_on_disk = {p.relative_to(_REPO).as_posix()
                   for p in (_REPO / ".github" / "workflows").glob("*.yml")}
    _assert_eq(sorted(_wf_on_disk - set(g.WORKFLOWS)), [],
               f".github/workflows 里每个 workflow 都在第 11 条的登记表里（共 {len(g.WORKFLOWS)} 份）")
    for _rel in g.SHELL_SOURCES:
        _assert((_REPO / _rel).is_file(), f"第 11 条登记的脚本在位：{_rel}")

    # 4-c) 第 12 条：交付链脚本的**编码**不许交给环境决定。上面 4-b) 那条判据上线当天
    #      就在云端炸出一条形态全新的红：Windows runner 上 `input=<str>` 在**写 stdin
    #      那一刻**抛 UnicodeEncodeError（'charmap'＝控制台代码页 cp1252），构建前硬门
    #      自己炸在打包之前，症状与「被判的脚本有问题」一模一样。本机是 UTF-8，这类缺陷
    #      在本机永远复现不出来——所以光有静态判据不够，还要一条**行为仿真**：把解释器
    #      放进一个窄代码页（US-ASCII）的子进程里跑这条判据，要求它既不抛异常，也还能
    #      给出正确的红与绿；同时要求「旧写法」在那个环境里**确实会抛**，否则这个夹具
    #      只是在自我表扬（有人关掉 locale 强制、或换一台 UTF-8 镜像，它就变成空跑）。
    _assert_eq(g.check_text_io_encoding(), [],
               "交付链那份判据现在一条都不报（报了就说明有人在 Windows runner 上会把"
               "门禁炸成一条 UnicodeError，而门禁的结论会被这条异常整个盖掉）")
    _assert(len(g.io_scripts()) >= 5,
            f"扫描是按目录枚举的，真找到 {len(g.io_scripts())} 份脚本（找空了下面所有条都是空的）")

    _IO_FIXTURES = {
        # a) 本轮云端那条红的最小复现：input 给 str + text=True 且没有 encoding
        "debug/zz_io_input_str.py":
            "import subprocess\n"
            "subprocess.run(['bash', '-n'], input='echo 中文\\n', text=True)\n",
        # b) 只缺 encoding 的解码半边：git 关掉 core.quotePath 之后输出中文文件名
        "debug/zz_io_text_noenc.py":
            "import subprocess\n"
            "r = subprocess.run(['git', 'ls-files'], capture_output=True, text=True)\n"
            "print(r.stdout)\n",
        # c) 写对了：显式 encoding + errors
        "debug/zz_io_ok.py":
            "import subprocess\n"
            "r = subprocess.run(['git', 'ls-files'], capture_output=True, text=True,\n"
            "                   encoding='utf-8', errors='replace')\n"
            "print(r.stdout)\n",
        # d) 中文进控制台却没修编码（✅ 那一类）
        "debug/zz_io_print_noop.py":
            "print('✅ 交付链编码合格')\n",
        # e) 修好了，而且修在第一次打印之前
        "debug/zz_io_print_fixed.py":
            "import sys\n"
            "sys.stdout.reconfigure(encoding='utf-8')\n"
            "print('✅ 交付链编码合格')\n",
        # f) 修晚了：第一句中文已经抛过才算
        "debug/zz_io_print_late.py":
            "import sys\n"
            "print('❌ 门禁失败')\n"
            "sys.stdout.reconfigure(encoding='utf-8')\n",
        # g) 只盯 print 会漏的那一半：中文的 SystemExit 消息也是往 stderr 写
        "debug/zz_io_systemexit.py":
            "import sys\n"
            "sys.exit('Stable 通道没有 mac-arm64 chromedriver，检查网络')\n",
        # h) 全 ASCII 的脚本不需要修编码（不跟着报，门才不会沦为噪音）
        "debug/zz_io_ascii_only.py":
            "import subprocess\n"
            "print('saved chromedriver')\n"
            "subprocess.run(['git', 'rev-parse', 'HEAD'], capture_output=True)\n",
        # i) 判据自己也吃语法错误：读不懂必须响，不能当成「没问题」
        "debug/zz_io_broken.py": "def (\n",
    }
    for _rel, _src in _IO_FIXTURES.items():
        (_REPO / _rel).write_text(_src, encoding="utf-8")
    try:
        _got = g.check_text_io_encoding(paths=("debug/zz_io_input_str.py",))
        _assert(len(_got) == 2 and any("input= 给的是 str" in p for p in _got)
                and any("text=True 却没写 encoding" in p for p in _got),
                f"反向对照：本轮云端那种 input=<str> 要同时被抓两次（写法与解码）（实际={_got}）")
        _assert(any("encoding=\"utf-8\", errors=\"replace\"" in p for p in _got),
                "报错文案要直接给出下一步（补哪两个参数），不要让人回去读判据源码")
        _got = g.check_text_io_encoding(paths=("debug/zz_io_text_noenc.py",))
        _assert(len(_got) == 1 and "text=True 却没写 encoding" in _got[0],
                f"反向对照：text=True 不写 encoding 单独也能被抓到（实际={_got}）")
        _assert_eq(g.check_text_io_encoding(paths=("debug/zz_io_ok.py",)), [],
                   "反向对照：显式写了 encoding 就放行（否则这条门只会一直响）")
        _got = g.check_text_io_encoding(paths=("debug/zz_io_print_noop.py",))
        _assert(len(_got) == 1 and "reconfigure" in _got[0],
                f"反向对照：中文进控制台却没修编码要红（实际={_got}）")
        _assert_eq(g.check_text_io_encoding(paths=("debug/zz_io_print_fixed.py",)), [],
                   "反向对照：修在第一次打印之前就放行")
        _got = g.check_text_io_encoding(paths=("debug/zz_io_print_late.py",))
        _assert(len(_got) == 1 and "第一次中文写控制台" in _got[0],
                f"反向对照：修晚了同样红——顺序才是这件事的实质（实际={_got}）")
        _got = g.check_text_io_encoding(paths=("debug/zz_io_systemexit.py",))
        _assert(len(_got) == 1,
                f"反向对照：中文的 sys.exit 消息也算往控制台写（只盯 print 会放过它，"
                f"而那常常正是门禁唯一要说清原因的一句）（实际={_got}）")
        _assert_eq(g.check_text_io_encoding(paths=("debug/zz_io_ascii_only.py",)), [],
                   "反向对照：全 ASCII 的脚本不要求修编码（误报的门会被当噪音关掉）")
        _got = g.check_text_io_encoding(paths=("debug/zz_io_broken.py",))
        _assert(len(_got) == 1 and "语法读不懂" in _got[0],
                f"反向对照：读不懂的脚本要响，不能当成合格（实际={_got}）")
        _assert_eq(g.check_text_io_encoding(paths=("debug/这个文件不存在.py",)),
                   ["debug/这个文件不存在.py 不在位（登记了却读不到，这条判据正在空转）"],
                   "反向对照：登记表里的脚本不在位要响，不许静默跳过")
    finally:
        for _rel in _IO_FIXTURES:
            (_REPO / _rel).unlink(missing_ok=True)

    # 行为仿真：真的开一个窄代码页的解释器，把判据放进去跑。上面那些夹具判的是
    # 「写法」，这一条判的是「换一种环境以后这条门还会不会说话」。
    _narrow_probe = (
        "import locale, sys\n"
        "sys.path.insert(0, '.github/scripts')\n"
        "import check_packaging_manifest as g\n"
        "good = 'echo \"实际拿到「${AV}」\"\\n'\n"
        "bad = 'if [ -x \"$X\" ]; then\\n    echo 中文\\n'\n"
        "try:\n"
        "    a = g.check_shell_parse(good, 'bash')\n"
        "    b = g.check_shell_parse(bad, 'bash')\n"
        "except Exception as e:\n"
        "    print('EXC %s' % type(e).__name__)\n"
        "    raise SystemExit(1)\n"
        "print('ENC %s GOOD %d BAD %d' % (locale.getpreferredencoding(False), len(a), len(b)))\n"
    )
    _old_probe = (
        "import subprocess\n"
        "try:\n"
        "    subprocess.run(['bash', '-n'], input='echo 中文\\n', text=True)\n"
        "    print('OLD no raise')\n"
        "except UnicodeEncodeError:\n"
        "    print('OLD raised UnicodeEncodeError')\n"
    )
    _narrow_env = dict(os.environ)
    for _k in ("PYTHONLOADING", "LC_CTYPE", "LANGUAGE"):
        _narrow_env.pop(_k, None)
    # 两个都要给：只关 PEP 538 的 locale 强制还不够——关掉之后解释器会**自动**进入
    # PEP 540 的 UTF-8 模式，preferredencoding 照样是 UTF-8，这个夹具就白搭了。
    # 上一轮就是这么空的：断言拿到 'utf-8'，窄环境压根没起来。
    _narrow_env.update({"LC_ALL": "C", "LANG": "C",
                        "PYTHONCOERCECLOCALE": "0", "PYTHONUTF8": "0"})
    _nr = subprocess.run([sys.executable, "-c", _narrow_probe], cwd=str(_REPO),
                         capture_output=True, text=True, encoding="utf-8",
                         errors="replace", env=_narrow_env)
    _old = subprocess.run([sys.executable, "-c", _old_probe], cwd=str(_REPO),
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", env=_narrow_env)
    _assert("OLD raised UnicodeEncodeError" in _old.stdout,
            f"夹具本身要先被验住：窄环境下旧写法（input=<str>）确实当场抛 UnicodeEncodeError"
            f"（不抛就说明这台机器没模拟出 Windows 那半个环境，下面两条就是空跑）"
            f"（实际 stdout={_old.stdout.strip()!r} stderr={_old.stderr.strip()[-160:]!r}）")
    _fields = _nr.stdout.split()
    _assert(len(_fields) >= 6 and _fields[0] == "ENC",
            f"窄环境那一跑要有结论可读（没读到就先看 stderr——那正是这一条要抓的故障）"
            f"（实际 stdout={_nr.stdout.strip()!r} stderr={_nr.stderr.strip()[-160:]!r}）")
    _assert("utf" not in _fields[1].lower(),
            f"窄环境确实生效：preferredencoding 不是 UTF-8（实际={_fields[1]!r}）")
    _assert_eq(_fields[2:], ["GOOD", "0", "BAD", "1"],
               "字节进出的判据在窄代码页里既不抛异常、给的红绿也一个不差"
               f"（抛了就复现云端那条：构建前硬门自己炸在打包之前；实际 stdout={_nr.stdout.strip()!r} "
               f"stderr={_nr.stderr.strip()[-160:]!r}）")


    # 4-d) 第 14 条：按外部工具的文本输出取值的那句 grep 必须带出处。这一条防的不是
    # 「判据写错」，是「判据按想象中的格式写」——2026-11-13 云端那次红（makensis 自报
    # MakeNSIS v3.10，判据却去找 Nullsoft Install System）的代价是一整个交付没有安装包，
    # 而本机同一套测试**全绿**：仿真假编译器印的就是判据想要的那句假话。
    # 所以这条门两头都要跑：不带出处必须响、带证据要放行、自家 printf 搬进 grep 的那些
    # **不许**算违规（算进去只会逼人为了消红写一个假出处，那正是它要防的动作）。
    # 判据的地基是「这句 shell 里的 grep 从谁手里拿字节」，也就是 _shell_segments 的切分：
    # 切分错了它会安静地一条不报（全绿但没有门），所以切分本身也要被钉住。
    _P_PROV = {
        "取外部文件不写出处": 'BANNER=$(grep -haoE "(MakeNSIS|Nullsoft Install System) '
                             'v[0-9][0-9.]*" "$LOG" 2>/dev/null | head -1 || true)\n',
        "外部命令的管道不写出处": 'certutil -hashfile "$(cygpath -w "$1")" SHA256 '
                              '| grep -oE "[0-9a-fA-F]{64}" | head -1 || true\n',
        "反斜杠续行把文件参数推到下一行": 'grep -E "FAIL" \\\n    "$LOG"\n',
    }
    for _tag, _src in _P_PROV.items():
        _got = g.check_external_output_provenance(workflows=(), sources=(), texts=(("夹具", _src),))
        _assert_eq(len(_got), 1, f"反向对照·{_tag}：这一句必须在案（判不住=这条门是空的）"
                                 f"（实际={_got}）")
        _assert("查不到这批字节的出处" in _got[0], f"反向对照·{_tag}：报的话要说清缺的是出处")
    _P_OK = {
        "带 run/job 号出处": "# 证据：run 43271171596 / job 131367773604 编译日志第 924 行原字节\n"
                            + _P_PROV["取外部文件不写出处"],
        # 「格式依据」是给那种「取值不依赖工具话术」的地方开的口：SHA-256 的十六进制恒为
        # 64 位，这种取值不需要日志样本，但需要有人把「为什么不用猜」写下来。
        "带格式依据": "# 格式依据：SHA-256 的十六进制表示按定义恒为 64 个 hex 字符\n"
                     + _P_PROV["外部命令的管道不写出处"],
        "自家 printf 搬进 grep": 'last=$(printf \'%s\\n\' "$out" | grep -E "PASS|FAIL|通过|失败|_OK"'
                                ' | tail -1)\n',
        "同句里两个 grep 都读自家文案": 'nfail=$(printf \'%s\\n\' "$out"'
                                        ' | grep -oE "[0-9]+ 失败" | tail -1'
                                        ' | grep -oE "[0-9]+" | tail -1)\n',
        "嵌在引号里的命令替换也算自家文案": 'printf \'SKIP %s\\n\' "$f" "$(printf \'%s\\n\' "$out"'
                                            ' | grep \'SKIP-FIXTURE\' | head -1)"\n',
        "注释里演示坏写法": "# 老写法 grep -haoE \"Nullsoft Install System v\" \"$LOG\" 已被证明是猜的\n"
                            'BANNER=x\n',
    }
    for _tag, _src in _P_OK.items():
        _got = g.check_external_output_provenance(workflows=(), sources=(), texts=(("夹具", _src),))
        _assert_eq(_got, [], f"正例·{_tag}：不该响（响了人们就会去写假出处）")
    # 切分的地基：`grep -E "PASS|FAIL"` 里那个 `|` 是模式的一部分，`$( … | … )` 才是交接点。
    # 少切一刀=「grep 没有上游」，多切一刀=「凭空多出一个上游」，两种都会让上面那两组红绿互换。
    _segs = g._shell_segments('last=$(printf \'%s\\n\' "$out" | grep -E "PASS|FAIL|通过" | tail -1)')
    _assert_eq(_segs, ['last=', 'printf \'%s\\n\' "$out"', 'grep -E "PASS|FAIL|通过"', 'tail -1'],
               "引号内的 | 不当管道、$( 与 ) 当交接点（切错了这条门就只会指错方向）")
    _assert_eq(g._shell_segments('grep -c "a|b" "$F" && echo ok'), ['grep -c "a|b" "$F"', 'echo ok'],
               "`&&` 也切，但模式里的 | 不切")
    # 窗口有上限：出处必须**就近**。文件顶上留一句「实测过」不该给下面 200 行的取值当免检牌。
    _near = "".join(f"step_{i}=1\n" for i in range(10))
    _assert_eq(g.check_external_output_provenance(
        workflows=(), sources=(),
        texts=(("夹具", _near + "# 证据：run 43271171596 原字节\n"
                + _P_PROV["取外部文件不写出处"]),)),
        [], f"出处在窗口内（{g.PROVENANCE_LOOKBACK} 行以内）时放行")
    _far = "".join(f"step_{i}=1\n" for i in range(g.PROVENANCE_LOOKBACK + 2))
    _got = g.check_external_output_provenance(
        workflows=(), sources=(),
        texts=(("夹具", "# 证据：run 43271171596 原字节\n" + _far
                + _P_PROV["取外部文件不写出处"]),))
    _assert_eq(len(_got), 1, f"隔了 {g.PROVENANCE_LOOKBACK + 2} 行以上的出处不算出处（实际={_got}）")
    # 真交付链必须绿：上面那一整组只证明「判据会说话」，这一条才证明「现在没坏」。
    _assert_eq(g.check_external_output_provenance(), [],
               "交付链 shell：按外部工具输出取值的 grep 全部带出处（run/job 号 / 原字节 / 格式依据）")
    # 真 workflow 里那两处哈希取值就是这条门的靶子：把注释整段拿掉必须响。判「注释存在」
    # 没用——那正是第 14 条要防的形状（话写了，取值还是猜的）；响的是**取值那一句**。
    _prov_step = [b for n, _s, sh, b in g.run_blocks_in_workflow(win)
                  if sh == "bash" and "Provision NSIS" in n]
    _assert_eq(len(_prov_step), 1, f"切得到 NSIS 那一步（实得 {len(_prov_step)} 段）")
    _naked = "\n".join(l for l in _prov_step[0].splitlines() if not l.strip().startswith("#"))
    _got = g._provenance_sites(_naked)
    _assert_eq([("sha256sum" if "sha256sum" in s else "certutil" if "certutil" in s else "?")
                for _no, s in _got], ["sha256sum", "certutil"],
               f"删掉解释就当场响：两处哈希取值靠的是那段注释里的依据（实际={_got}）")
    for _rel in g.SHELL_SOURCES:
        _assert((_REPO / _rel).is_file(), f"第 14 条登记的脚本在位：{_rel}")


    # 5b) 同一份判据盖住交付链上另外几份手写 shell 脚本（派发云打包、取制品、轮询 CI、
    # 批跑回归）。它们坏的时候恰好都是「排障现场最该看见那个数字」的时刻。登记表里的
    # 脚本不在位或没提交也要红——一条静默不跑的判据比没有判据更危险（会让人以为验过）。
    _assert_eq(g.check_script_hygiene(), [], "交付链 shell 脚本：文案卫生 + 在位 + 已提交")
    for rel in g.HYGIENE_SCRIPTS:
        _assert((_REPO / rel).is_file(), f"登记的脚本在位：{rel}")
    _assert("debug/run_offline_regression.sh" in g.HYGIENE_SCRIPTS,
            "回归入口自己也在判据范围内（它坏掉的那一行正是批跑要报红的那一行）")
    # 反向对照：登记表读空了这条判据就永远绿，所以先证明它抓得住真问题
    _saved_scripts = g.HYGIENE_SCRIPTS
    try:
        g.HYGIENE_SCRIPTS = ("debug/这个脚本不存在.sh",)
        _gone = g.check_script_hygiene()
        _assert(any("这个脚本不存在.sh" in p and "不在位" in p for p in _gone),
                f"登记了却读不到必须红（判据不许静默空转）：{_gone}")
        g.HYGIENE_SCRIPTS = ("debug/b2bcollect_bad_hygiene_probe.sh",)
        probe = _REPO / "debug" / "b2bcollect_bad_hygiene_probe.sh"
        probe.write_text('echo "清单不完整：$missing（见上）"\n', encoding="utf-8")
        try:
            _bad = g.check_script_hygiene()
            _assert(any("紧跟" in p for p in _bad), "登记过的脚本里出现坏写法同样要红")
            # 每条问题自带类目标签（2026-11-15 本机自审抓到）：这一条门一次判三件事——文案写法、
            # 脚本在位、是否已提交，三件事的处置完全不同。当时满屏都是【未提交】那一类，而标题
            # 写的是「`$VAR` 紧跟中文会把报错那行本身弄坏」，照着标题去找文案 bug 的人会白找一轮；
            # 一条报不准自己在报什么的门，下一次就是被人整条跳过的那一条。
            _assert_eq(set(g.HYGIENE_TAGS), {"文案", "在位", "未提交"},
                       "三类判据各自的类目要在案（新增一类而不登记，下一格就抓不到它）")
            for _ps, _tag in ((_gone, "在位"), (_bad, "文案")):
                _assert(all(p.startswith("【") and p.split("】", 1)[0].lstrip("【") in g.HYGIENE_TAGS
                            for p in _ps),
                        f"这一条门报出的每一行都得有类目，不许出现「不知道该归哪类」那一种：{_ps}")
                # 「至少一条归到本格造的那一类」而不是「全部归那一类」：探针脚本自己没进过提交，
                # 于是同一批结果里真会同时出现【未提交】——那是这条门该报的第三条线，把它当成
                # 夹具的杂质要求消失，等于教下次写判据的人「一批结果里只许有一种问题」。
                _assert(any(p.split("】", 1)[0].lstrip("【") == _tag for p in _ps),
                        f"夹具造出来的那一类必须被归到【{_tag}】：{_ps}")
        finally:
            probe.unlink(missing_ok=True)
    finally:
        g.HYGIENE_SCRIPTS = _saved_scripts
    _gsrc = (_REPO / ".github" / "scripts" / "check_packaging_manifest.py").read_text(encoding="utf-8")
    _assert('::error::shell 文案：{p}' not in _gsrc,
            "注解前缀不许写死成「文案」——那一行正是把【未提交】报成 shell 文案的源头")
    _assert('partition("】")' in _gsrc and "::error::shell {" in _gsrc,
            "main 里注解的类目要从这条问题自带的标签上取，不在那儿再猜一次「这批大概都是文案」")

    # 5b-ii) 第 7 条的最后一头：文件**在提交里，但工作区改过没提交**。老判据看它在 HEAD 里
    # 就放行，于是本机跑绿的是工作区、CI 检出并打包的是提交，两份内容不是同一份——验证结论
    # 对交付没有担保（这条链上最坏的假绿：所有断言都是真的，量的却是不会被交付的那份字节）。
    _scr = _REPO / g.HYGIENE_SCRIPTS[0]
    _orig = _scr.read_bytes()
    # 进这一格之前它是什么状态、这一条门报什么，先记下来当基准。下面承诺的是「自己造的红不许
    # 留给别人」，那基准就只能是自己动手之前那一份，而不是硬写的 "ok"/[]：本机仓库脏着的时候
    # （本轮就是这样，几十份文件改过没提交），写回原字节之后仍然读得到【未提交】——那是别人的红，
    # 硬写基准会把它读成「还原失败」，等于拿环境事实冒充用例缺陷，还逼人先去 commit 才能跑测试。
    g.reset_git_cache()
    _pre_state = g.tracked_state(_scr)
    _pre_problems = g.check_script_hygiene()
    try:
        _scr.write_bytes(_orig + "\n# 探针：改过没提交\n".encode("utf-8"))
        g.reset_git_cache()                 # 三张 git 清单是进程内缓存的，不重置就是空跑
        _assert_eq(g.tracked_state(_scr), "dirty",
                   f"改过没提交要能被认出来（{_scr.name} 此刻就不在提交里）")
        _gotd = g.check_script_hygiene()
        _assert(any(_scr.name in p and "两份内容不是同一份" in p for p in _gotd),
                f"卫生判据也要报这一头（它读的是工作区那份，交付的却是提交那份）：{_gotd}")
        _assert(all(p.startswith("【未提交】") for p in _gotd
                    if _scr.name in p and "两份内容不是同一份" in p),
                f"「改过没提交」这一类必须自带类目（注解要按它说的那件事去标）：{_gotd}")
        _gotd2 = g.check_tracked([(f"../{_scr.parent.name}/{_scr.name}", ".")])
        _assert(any(_scr.name in p and "git commit" in p for p in _gotd2),
                f"进包输入同款判据（第 7 条本身）也要报，措辞给出下一步：{_gotd2}")
    finally:
        _scr.write_bytes(_orig)
        g.reset_git_cache()
    _assert_eq(_scr.read_bytes(), _orig, "写回的必须是进来那一份字节（下面两句都以它为前提）")
    _assert_eq(g.tracked_state(_scr), _pre_state,
               f"按原字节写回后回到**进这一格之前**的状态（判据看内容不看时间戳——touch 造不出"
               f"假红；进格前它是 {_pre_state}）")
    _assert_eq(g.check_script_hygiene(), _pre_problems,
               "自己造的红不许留给别人：还原后与进这一格之前逐字一致（仓库干净时这一句就是 []）")
    _assert(not any("探针" in p for p in g.check_script_hygiene()),
            "探针那一行不许留在结果里——它才是这一格自己造出来的那条")

    # 5c) 批跑清单的完整性：交付目录里靠命名约定**自动发现**，这条判据核对「磁盘上有、
    # 清单里没有」。riskbird_build 那 13 个套件就是这么整目录消失了两年的——写完当天
    # 跑绿，此后随包代码怎么改都不红（本轮手工补跑才发现一条真实上传缺陷）。
    proc = subprocess.run(_bash_or_die(str(_REPO / "debug" / "run_offline_regression.sh"), "--list"),
                          capture_output=True, text=True, cwd=str(_REPO))
    _assert_eq(proc.returncode, 0, f"回归入口能解析清单（stderr={proc.stderr[-200:]}）")
    listed = {ln.strip() for ln in proc.stdout.splitlines() if ln.strip().endswith(".py")}
    on_disk = {p.relative_to(_REPO).as_posix() for d in g.SHARED_SOURCE_DIRS
               for p in (_REPO / d).glob("test_*_offline.py")}
    _assert_eq(sorted(on_disk - listed), [],
               f"三个交付目录里每个 test_*_offline.py 都在批跑清单里（清单共 {len(listed)} 项）")
    _assert(len(on_disk) >= 20, f"自动发现真的跑起来了（发现 {len(on_disk)} 个，少于 20 就是约定没匹配上）")
    for junk in ("append_offline_14.py", "append_offline_15.py", "fix_offline_14_5.py"):
        _assert(not any(junk in x for x in listed),
                f"一次性源码补丁脚本不许进批跑（跑一次就往套件里再插一段）：{junk}")

    # 5c-II) 批跑入口的**退出码**才是「全量跑绿」唯一的机器口径。它此前无论红多少都
    # exit 0——本轮就是这样：屏幕顶上写着「1 失败」，脚本却退 0，被当成全绿提交。
    # 「跑绿」只有人盯屏幕那一次算数，等于没有口径。判据不能只写成「文里有 exit 1」，
    # 注入夹具真跑一遍：会绿的、会红的、自己数出 1 条失败却退 0 的、一句话都不说的、
    # 以及断言全过却带着非零码出局的（gui_smoke_test 本轮的红法——判定与退出码不同源）。
    #
    # 内层这一批必须落在**自己的沙箱**里。本轮它落在默认落点上，而入口一开头就要
    # rm -rf 那个目录：外层已经写好的 31 份日志连同外层自己那份正开着的句柄一起被删，
    # 结果外层两个其实是绿的套件被判成红，报「完整输出」时连 cat 都 cat 不出来。
    # 这是判据链自己造的红——而且造的是**假红**（会让人去改代码，代码却是好的）。
    runner = _REPO / "debug" / "run_offline_regression.sh"
    fixture = _REPO / "debug" / "zz_regression_probe.py"
    sandbox = Path(tempfile.mkdtemp(prefix="b2b_regression_sandbox_"))
    shared = _REPO / "debug" / "regression_logs"

    def _shared_state():
        """默认落点上的**证据文件**快照（只看普通文件，不看目录）。

        `.lock` 不进快照：它是「哪一批正在用这个落点」的互斥量，外层那一批收尾时
        本来就会把它删掉。把它算进「一份都不许少」，就会把隔壁批次的正常收尾判成本轮
        删了证据——判据自己造假红（本轮就是这么撞上的：同时开着两批）。
        """
        return {p.relative_to(shared).as_posix() for p in shared.rglob("*") if p.is_file()} \
            if shared.is_dir() else set()

    # `dict | None` 直接写出来是 PEP 604（3.10 起）：这一份是「兼容通道的 Python 3.8 也要
    # 能整套跑完」那条路的被测对象之一，注解在定义时就会求值，3.8 上直接 TypeError。
    # 加 `from __future__ import annotations` 也能修，但那是整份文件换个求值口径；
    # 引号里的前向引用只有这一格受影响，改动面小、也不改别的注解的行为。
    def _run_probe(src: str, extra_env: "dict | None" = None):
        fixture.write_text(src, encoding="utf-8")
        try:
            env = dict(os.environ)
            env.update(extra_env or {})
            env["B2B_REGRESSION_ONLY"] = "debug/zz_regression_probe.py"
            env["B2B_REGRESSION_SANDBOX"] = str(sandbox)
            return subprocess.run(_bash_or_die(str(runner)), capture_output=True, text=True,
                                  cwd=str(_REPO), env=env)
        finally:
            fixture.unlink(missing_ok=True)

    _before = _shared_state()
    for tag, src, want_rc, want_said in (
            ("绿的套件", 'print("结果：3 通过 / 0 失败")\n', 0, ""),
            ("红的套件", 'print("  ❌ 某条断言")\nprint("结果：2 通过 / 1 失败")\nraise SystemExit(1)\n', 1, ""),
            ("退出码说谎的套件", 'print("  ❌ 某条断言")\nprint("结果：2 通过 / 1 失败")\n', 1,
             "判定与退出码不一致"),
            ("一句话都不说的套件", 'import os\nos._exit(0)\n', 1, "一个字都没写"),
            ("断言全过却崩在收尾的套件",
             'print("全部 1 项断言通过 ✅")\nimport os\nos._exit(139)\n', 1, "崩在收尾阶段")):
        proc = _run_probe(src)
        _assert_eq(proc.returncode, want_rc,
                   f"批跑入口对「{tag}」给的退出码（尾部：{proc.stdout.strip()[-110:]}）")
        if want_said:
            _assert(want_said in proc.stdout,
                    f"「{tag}」要红得说清为什么（期望含「{want_said}」，实际：{proc.stdout.strip()[-160:]}）")
        _assert(not fixture.exists(), f"夹具跑完就删，不许留在磁盘上冒充套件（{tag}）")
    _after = _shared_state()
    # 判的是**性质**而不是「那一刻的目录一字不变」：隔壁正开着的一批往默认落点里新增
    # 自己那几份日志，是正常的；把它算成本轮越界，就成了只有「同一时刻全机器只跑我一批」
    # 才不红的假红（本轮同时开两批就是这样撞红的）。真正要守的两条是：外层已有的证据一份
    # 都不许少（历史事故：入口一开头 rm -rf 掉外层 31 份日志），以及内层自己的证据一份
    # 都不许出现在这里（出现了＝沙箱没在用，上面那条「不许少」随时可能再犯）。
    _missing = sorted(_before - _after)
    _leak = sorted(x for x in _after if "zz_regression_probe" in x)
    _assert_eq(_missing, [],
               "内层夹具批跑不许碰默认落点：外层那一批的证据（每套件一份日志）被删过一次，"
               "删证据的判据本身必须被关在自己的沙箱里")
    _assert_eq(_leak, [],
               f"内层夹具的证据不许落在默认落点上（落在这儿说明沙箱没在用）：{_leak}")
    _assert(_before, "默认落点上真的有一份份证据在被判（快照为空=上面那条「一份不许少」在判空气）")
    _assert((sandbox / "logs").is_dir(),
            "沙箱落点真的在用（没在用=上面两条判据都在判空气，而历史事故就出在这儿）")

    # 5c-IIB) 死法分类按**实测的退出码**配对，不按日志措辞猜（本轮重写的那一支）。
    # 上面那张表里所有非零码都是夹具**自己 os._exit(N)** 造的，而真实挂死与被信号打死分别
    # 来自 timeout 与内核——这两支配对贴反了不会有任何一句说出口。本机实测 coreutils 9.7 的
    # `timeout -s ABRT -k 10`：被 ABRT 打断→124、ABRT 叫不应升级 KILL→137、进程自己
    # abort→134、自己段错误→139。旧写法把挂死配成 134/137，于是真挂死掉进「普通断言红」
    # 那一支（报出来的是最后一行汇总，跟超时毫无关系），而进程自己 abort 被喊成超时——两个
    # 结论都指向错的地方。所以这一格不用夹具造码，直接放**真的**挂死与**真的**信号进去。
    for _tag, _src, _extra, _want, _not in (
            ("挂死（到点被 SIGABRT 打断）",
             'import time\nprint("结果：0 通过 / 0 失败")\ntime.sleep(300)\n',
             {"PER_TEST_TIMEOUT": "3"},
             ["HANG", "看门狗 124 打断"],
             ["CRASH", "FAIL "]),
            ("挂死且 SIGABRT 叫不应（升级 KILL）",
             'import signal, time\nsignal.signal(signal.SIGABRT, signal.SIG_IGN)\n'
             'print("结果：0 通过 / 0 失败")\ntime.sleep(300)\n',
             {"PER_TEST_TIMEOUT": "3"},
             ["HANG", "升级 KILL 才停（137）"],
             ["CRASH", "FAIL "]),
            ("真段错误、日志里一条红都没有",
             'import ctypes\nprint("全部 1 项断言通过 ✅")\nctypes.string_at(0)\n',
             {},
             ["CRASH", "被信号 11 打死", "Fatal Python error: Segmentation fault",
              "崩在收尾阶段", "判定要与退出码同源"],
             ["HANG", "超过"]),
            ("真 abort、日志里带着红断言",
             'import os, signal\nprint("  ❌ 某条断言")\nprint("结果：2 通过 / 1 失败")\n'
             'os.kill(os.getpid(), signal.SIGABRT)\n',
             {},
             ["CRASH", "被信号 6 打死", "处红：先按栈修判定"],
             ["HANG", "超过"]),
    ):
        _proc = _run_probe(_src, _extra)
        _assert_eq(_proc.returncode, 1,
                   f"「{_tag}」必须算失败（尾部：{_proc.stdout.strip()[-120:]}）")
        for _w in _want:
            _assert(_w in _proc.stdout,
                    f"「{_tag}」要报成「{_w}」这一类死法（实际：{_proc.stdout.strip()[-260:]}）")
        for _w in _not:
            _assert(_w not in _proc.stdout,
                    f"「{_tag}」不许被读成「{_w}」——旧写法正是把这两支配对贴反了，"
                    f"挂死报成断言红、自己 abort 报成超时（实际：{_proc.stdout.strip()[-260:]}）")
        # 挂死那两支还要进 hung 汇总：分类报了但没收进「哪几路是挂死的」那张表，收尾看板上
        # 就只剩一条 FAIL，超时与崩在收尾从此分不开。（`hung="$hung $f"` 前面带一个空格，
        # 所以判的是「HANG: 之后的那段里有点名」，不是把整句写死。）
        if "124" in _want[0] or "137" in _want[0] or "HANG" in _want[0]:
            _tail_h = _proc.stdout.split("HANG:")[-1]
            _assert("zz_regression_probe.py" in _tail_h,
                    f"「{_tag}」要出现在收工的 HANG 清单里（实际：{_proc.stdout.strip()[-160:]}）")
        # 「挂死点栈见下」这句承诺本身也得判：本机实测被 SIGABRT 打断时 faulthandler 只有
        # 约七成概率把栈落盘（12 次 9 次），所以入口必须**按当场有没有栈**改口。这一格钉的
        # 是那个同源性，不是那 30% 的运气——写死「一定要有栈」就是一条迟早自己变红的假红。
        if "看门狗 124 打断" in _proc.stdout:
            _promised = "栈见下" in _proc.stdout
            _delivered = "Fatal Python error: " in _proc.stdout
            _assert(_promised == _delivered,
                    f"「{_tag}」承诺与证据必须同源：有栈才说「见下」（{_delivered}），"
                    f"没栈就得当场改口（{_promised}）（实际：{_proc.stdout.strip()[-200:]}）")

    # 5c-IIC) 没有 timeout/gtimeout 的那一支也必须抓得住挂死（2026-11-20 干净检出泳道实测）。
    # 来由：入口旧写法在取不到 timeout 时退成「裸跑」，而裸跑就是把挂死读成通过——本机手工
    # 复现过一份真挂死 20 秒的夹具在那种环境下的输出：`PASS … 套件：1 通过 / 0 失败 …
    # ALL SUITES PASS`。云端那 6 条红只是它的影子：挂死分类那两支在 runner 上压根没执行，
    # 于是「必须算失败 / 要报成 HANG / 要报成 124(137)」三条各红一次。真正贵的不是那 6 条红，
    # 是 2026-11-12 那次「整批被一个模态弹窗拖死 20 多分钟」——保护它的看门狗在一台干净的新
    # Mac 上其实并不存在，而屏幕上照样写着通过。
    # 这一支用 B2B_WATCHDOG=builtin 把「缺席」造出来，所以开发机（装过 brew）与 runner 跑的是
    # 同一条断言；退出码必须仍是 124/137 那一套，否则上面按码分类的两支就分叉了。
    for _tag, _src, _want in (
            ("内置看门狗·到点打断", 'import time\nprint("结果：0 通过 / 0 失败")\ntime.sleep(60)\n',
             ["HANG", "看门狗 124 打断"]),
            ("内置看门狗·叫不应升级 KILL",
             'import signal, time\nsignal.signal(signal.SIGABRT, signal.SIG_IGN)\n'
             'print("结果：0 通过 / 0 失败")\ntime.sleep(120)\n',
             ["HANG", "升级 KILL 才停（137）"]),
    ):
        _p = _run_probe(_src, {"B2B_WATCHDOG": "builtin", "PER_TEST_TIMEOUT": "2"})
        _assert_eq(_p.returncode, 1, f"{_tag}：挂死必须算失败（尾部：{_p.stdout.strip()[-120:]}）")
        for _w in _want:
            _assert(_w in _p.stdout,
                    f"{_tag}：要报成「{_w}」这一类死法（实际：{_p.stdout.strip()[-240:]}）")
        # 降级必须当场说出口：默默换一条路跑，就等于「这台机器上挂死是怎么判的」这句话
        # 没有出处——下一轮再红就只能从头猜。
        _assert("内置看门狗" in _p.stdout,
                f"{_tag}：选了内置看门狗要在屏幕上承认（实际：{_p.stdout.strip()[-240:]}）")
        _assert("zz_regression_probe.py" in _p.stdout.split("HANG:")[-1],
                f"{_tag}：要进收工的 HANG 清单（实际：{_p.stdout.strip()[-160:]}）")
    # 正控·auto 模式不许把外部 timeout 丢掉（丢了就等于全批悄悄换成另一套保护）：
    # 只在装了 coreutils 的机器上判这一格，没装的机器上这句本身就是废话，得当场念出来。
    if shutil.which("timeout") or shutil.which("gtimeout"):
        _p = _run_probe('print("结果：1 通过 / 0 失败")\n')
        _assert("内置看门狗" not in _p.stdout,
                f"本机有 timeout/gtimeout 时默认仍须走外部看门狗（实际：{_p.stdout.strip()[-200:]}）")
    else:
        print("   · 这台机器没有 timeout/gtimeout：上面那条 auto 模式对照跳过（CI 就是这样；"
              "内置那两支已经跑过，不影响结论）")

    # 5c-III) 污染要报在**犯错的套件名下**：只在收工那句「改动了 N 份」里说总数，接下来
    # 就得靠人回想这一批跑了什么——上一轮那两份「爱企查_结果页证据_*.txt」正是这么找的，
    # 而回想出来的结论没有一条能复跑。这里注入一份真的往开发机数据目录写字节的夹具：
    # 入口既要拦住（套件全绿也不放行），又要把这一路套件的名字打出来。
    polluted = _REPO / "b2bcollect_build" / "b2bcollect_data" / "zz_batch_attribution.json"
    polluted.unlink(missing_ok=True)
    try:
        proc = _run_probe('from pathlib import Path\n'
                          f'Path({str(polluted)!r}).write_text("污染归属夹具", encoding="utf-8")\n'
                          'print("结果：1 通过 / 0 失败")\n')
        _assert_eq(proc.returncode, 1,
                   f"往开发机数据目录写字节的套件不许算放行（实际 rc={proc.returncode}）")
        _assert("zz_regression_probe.py" in proc.stdout,
                f"污染必须点名到是哪一路套件写的（实际：{proc.stdout.strip()[-300:]}）")
    finally:
        polluted.unlink(missing_ok=True)
    _assert(not polluted.exists(),
            "污染归属跑完必须自己收尾：留着这份文件，外层这一批就会把它算成开发机被改动")

    # 反向对照：同一落点上的两批必须互斥。锁只在文档里写着、实际不生效，症状与
    # 「从来没并发过」完全一样——所以先把锁真的摆上，再要求入口当场拒绝并给出下一步。
    (sandbox / ".lock").mkdir(parents=True, exist_ok=True)
    try:
        proc = _run_probe('print("结果：1 通过 / 0 失败")\n')
        _assert_eq(proc.returncode, 8, f"落点上已有锁时必须拒绝开跑（实际 rc={proc.returncode}）")
        _assert("已经有一批回归在跑" in proc.stdout and "B2B_REGRESSION_SANDBOX" in proc.stdout,
                f"被挡住时要说清等什么、以及怎么绕开（实际：{proc.stdout.strip()[-200:]}）")
    finally:
        shutil.rmtree(sandbox / ".lock", ignore_errors=True)
    proc = _run_probe('print("结果：1 通过 / 0 失败")\n')
    _assert_eq(proc.returncode, 0, f"锁收掉后同一落点立刻能再跑（rc={proc.returncode}）")
    _assert(not (sandbox / ".lock").exists(),
            "跑完必须自己释放锁：留下一把死锁会把之后每次批跑都挡在门外，"
            "症状和「回归跑不动」一模一样")
    shutil.rmtree(sandbox, ignore_errors=True)

    sandbox2 = Path(tempfile.mkdtemp(prefix="b2b_reg_empty_"))
    proc = subprocess.run(_bash_or_die(str(runner)), capture_output=True, text=True, cwd=str(_REPO),
                          env=dict(os.environ, B2B_REGRESSION_ONLY=" ",
                                   B2B_REGRESSION_SANDBOX=str(sandbox2)))
    shutil.rmtree(sandbox2, ignore_errors=True)
    _assert(proc.returncode != 0 and "没有任何套件被执行" in proc.stdout,
            f"清单展开成空也必须红：一项都没跑的症状和「真的全过」一模一样（rc={proc.returncode}）")

    # 5) 安装包编译器不许「赌这台机器恰好装了什么」：本轮 Windows 就是红在 CI 全绿、
    #    产物与自检都过、最后一步找不到 makensis ——镜像里 NSIS 没了，而本机永远复现不了。
    #    判据只看这一步自己的手法：钉版本、钉 sha256、校验不过就停、解压后还要问一次
    #    版本；并且不许再留「去 Program Files 里找」的旧写法（那条路等于把交付形态
    #    交给别人的镜像决定）。
    prov = _step_region(win, "Provision NSIS")
    _assert(prov is not None, "Windows workflow 里 NSIS 的准备步骤还在（安装包不能靠镜像）")
    if prov:
        _assert(_step_shell(prov, "pwsh") == "bash", "NSIS 准备步骤显式 bash")
        m = re.search(r'NSIS_SHA256:\s*"([0-9a-f]{64})"', prov)
        _assert(m is not None, "NSIS_SHA256 钉的是 64 位十六进制（占位串或空值都算没钉）")
        _assert(re.search(r'NSIS_VERSION:\s*"(\d+\.\d+)"', prov) is not None,
                "NSIS_VERSION 钉了具体版本（不钉就会出现「今天能打包、明天打的不是同一个安装包」）")
        for need in ("sha256", "exit 1", "::error::"):
            _assert(need in prov, f"NSIS 准备步骤保留了 {need}（下载/校验/失败即停任一缺失都是拿坏编译器出包）")
        # 5-d) 版本探测不许赌一种写法：2026-11-12 云端 Windows 就是红在这一步——sha256
        # 早就过了、包也打好了，只有「问一句版本号」拿 `makensis -V` 回了一句 Usage，判据
        # 把它读成「版本不是钉住的那个」。Git Bash 会把以 / 开头的参数当路径改写，而某些
        # 写法又以 - 开头才被认得，一种写法必然在某一侧失灵。判据做成函数，再用旧做法反向对照。
        def _probe_problems(text: str) -> list:
            out = []
            if "VERSION" not in text:
                out.append("压根没问编译器版本（打出来的安装包说不清是谁编的）")
            if text.count("VERSION") < 2:
                out.append("只赌一种写法（/ 与 - 各有一侧失灵：路径改写与选项解析）")
            if "MSYS_NO_PATHCONV" not in text and "//VERSION" not in text:
                out.append("没防 Git Bash 把 / 开头的参数改写成路径")
            if not re.search(r"for\s+\w+\s+in[^\n]*VERSION", text):
                out.append("几种写法没有逐个试一遍、取第一个真报出版本号的结果")
            if "Usage" in text and "试过的写法" not in text:
                out.append("失败时没把试过的写法与实际输出印出来（只说版本不对＝让下个人从头猜）")
            return out
        _pc = g._strip_comments(prov)      # 注释里正写着那种错写法，拿原文判就是自己判自己
        _assert_eq(_probe_problems(_pc), [],
                   "NSIS 版本探测合格：多种写法、防路径改写、逐个试、失败留证")
        _bad_probe = ('VOUT=$("$MK" -V 2>&1 || true)\n'
                      'case "$VOUT" in *v3.10*) : ;; *) echo bad; exit 1 ;; esac\n')
        _assert_eq(_probe_problems(_bad_probe),
                   ["压根没问编译器版本（打出来的安装包说不清是谁编的）",
                    "只赌一种写法（/ 与 - 各有一侧失灵：路径改写与选项解析）",
                    "没防 Git Bash 把 / 开头的参数改写成路径",
                    "几种写法没有逐个试一遍、取第一个真报出版本号的结果"],
                   "反向对照：本轮云端那种单写法必须被判红（判据空转等于没有判据）")
        _assert("试过的写法与返回" in _pc,
                "失败分支会印出试过的每种写法与返回（探测失败与编译器坏了长得不一样，得能分开）")
        # 本轮那次 CI 红的不是 NSIS，是**校验哈希的那一行自己**：Git Bash 里
        # $RUNNER_TEMP 是带反斜杠的 Windows 路径，而 GNU coreutils 遇到「文件名里含反斜杠」
        # 会给整行输出加一个 `\` 前缀，`| awk '{print $1}'` 于是取出 `\fcdce…`——
        # 哈希本身一字不差，比对却永远不过。判据不许依赖字段位置，也不许把文件名喂进输出。
        # 只看**会被执行的行**：注释里正是写着那种错写法（写给维护者看的），拿原文判
        # 就是「解释了 bug 的行自己被当成 bug」。
        prov_code = g._strip_comments(prov)
        _assert("sha256sum < " in prov_code and 'sha256sum "$1"' not in prov_code,
                "哈希只从内容算（文件名参与输出时 GNU 会整行加转义前缀，Windows 路径必踩）")
        _assert('grep -oE "[0-9a-fA-F]{64}"' in prov_code
                and "| awk '{print $1}'" not in prov_code and "sed -n 2p" not in prov_code,
                "哈希值按 64 位十六进制取，不按行号/字段位置切（切歪了就是「值对、比对失败」）")
    inst = _step_region(win, "Build Windows installer (NSIS, per-user)")
    _assert(inst is not None and "Program Files (x86)/NSIS" not in inst
            and "这台 runner 上没有 makensis" not in win,
            "安装包步骤不再在 runner 上找 NSIS（找不找得到是镜像的事，不该决定用户有没有安装包）")
    _assert("MAKENSIS=$MK\" >> \"$GITHUB_ENV" in prov and "${MAKENSIS:-}" in inst,
            "编译器路径由准备步骤写进 GITHUB_ENV、安装步骤只认这一个来源")

    # 5-e) 「编译器自报的版本就是钉住的那个」这句判据必须**真跑一遍**才算数。这一段的成败
    # 全在 shell 自己的语义上：管道里取的是 makensis 的退出码还是 tee 的、`set -e` 会不会
    # 在判据之前就把进程带走、grep 在那条带反斜杠的日志路径上取不取得到横幅、case 的通配
    # 能不能匹配 v3.10——这些都不是「文本里有这句话」能担保的。本轮云端两次红都是「每一行
    # 看着都对」的语义坑，其中一次还是**负责解释失败的那句 echo 自己先炸**（引号写漏）。
    # 所以拿一个假 makensis 把判据整段跑五种现实：版本对、版本不对、不印横幅、编译器非零、
    # 上一步没把版本号传下来。后四种都必须非零退出且不印成功话术——松一头，就是把一份
    # 「说不清是谁编的」安装包交到用户手上。
    INST_STEP = "Build Windows installer (NSIS, per-user)"
    PIN_ANCHOR = '[ -n "${NSIS_PINNED:-}" ]'
    # 5-e-0) 横幅的真字节以**云端跑过的那台编译器**为准，不是任何人脑子里的格式。
    # 2026-11-13 凌晨那次云端红：判据只找 "Nullsoft Install System v…"，而 NSIS 3.10 的
    # makensis 自报的是 "MakeNSIS v3.10 - Copyright 1999-2023 Contributors"——114MB 安装包
    # 已经编出来了，却被「读不到横幅」这句整体拦下。更难看的是本机这一整段**全绿**：
    # 仿真假编译器印的就是判据想要的那句假格式，两边抄的是同一个想象。所以现在把真日志
    # 开头原样钉在这里（来源见下面的注释），仿真用它的字节，判据用正则去够它——两头都
    # 得对上证据，谁再改格式就当场红。
    # 证据：run 43271171596 / job 131367773604 编译日志第 924-926 行（行尾实测 \r\n）。
    NSIS_V4_HEAD = ("MakeNSIS v3.10 - Copyright 1999-2023 Contributors\n"
                    "See the file COPYING for license details.\n"
                    "Credits can be found in the Users Manual.\n")
    NSIS_V4_BANNER = "MakeNSIS v3.10"                 # 判据从上面那行里取出来的片段
    NSIS_LEGACY_BANNER = "Nullsoft Install System v3.10"   # 老 NSIS / -V1 的自报写法
    inst_body = next((b for n, b in _iter_steps(win) if INST_STEP in n), "")
    # 安装步骤自己的 env：夹具接下来要像 Actions 那样把它注进子进程（见 `_step_env`）。
    # 这里先把它「确实读到了两个交付物名」钉住：读不到就不注入，那会退回本轮那串假红——
    # 红了却指向一个不存在的交付缺陷，比不红更贵。
    INST_ENV = _step_env(inst_body)
    _assert("OUT_FILE" in INST_ENV and "PORTABLE_NAME" in INST_ENV,
            f"安装步骤的 env 里读不到交付物名（实得={INST_ENV}）：夹具没法照 Actions 那样注入，"
            "下面那些正例的红会是假红")
    cut = inst_body.find(PIN_ANCHOR)
    _assert(cut >= 0, f"安装步骤里找得到版本判据的锚点 {PIN_ANCHOR}（找不到=整段被删了）")
    gate_script = inst_body[cut:] if cut >= 0 else ""
    for need in ('-V4', "PIPESTATUS[0]", "MSYS_NO_PATHCONV=1", "set -o pipefail",
                 'grep -haoE', "(MakeNSIS|Nullsoft Install System)", "|| true",
                 '[ ! -s "$LOG" ]',
                 'BANNER_VER=${BANNER##* v}', '[ "$BANNER_VER" != "$NSIS_PINNED" ]'):
        _assert(need in gate_script, f"版本判据保留了 {need}（缺一样就是判据少一个方向）")
    # 取首段退出码的那个写法**依赖 pipefail**：没开它时 tee 成功就不进 || 分支，编译器的
    # 非零压根不会被问一次。这一步自己声明，而不是赌 runner 默认（默认值是外部实现细节）。
    _assert(gate_script.index("set -o pipefail") < gate_script.index("NSIS_RC=${PIPESTATUS[0]}"),
            "pipefail 必须声明在取码那一句之前（写在后面等于这一句自己没保护）")
    # 「只认一种想象出来的格式」本身就是这轮的真缺陷，反向钉住：文本里不许再出现那种
    # 只找 Nullsoft 一句、并且不带 MakeNSIS 的写法。
    _assert('grep -oE "Nullsoft Install System' not in gate_script,
            "横幅判据不许退回只猜一种写法（云端那台编译器自报的是 MakeNSIS，猜错=交付没安装包）")
    # 正则必须真的够得到钉在这里的真字节：判据与证据之间的关系由这一条把住，
    # 而不是由「看起来差不多」把住。
    _assert(re.search(r"(MakeNSIS|Nullsoft Install System) v[0-9][0-9.]*", NSIS_V4_HEAD),
            "横幅正则必须命中真 makensis 自报的那一行（命中不了=判据与证据再一次分家）")

    def _run_installer_gate(banner: str, stub_rc: int, pinned: str = "3.10",
                            pipefail: bool = True, crlf: bool = False,
                            stamp_build: str = "999", script: str = "",
                            binary: bool = False, bad_temp: bool = False):
        """在临时目录里真跑一遍安装步骤的版本判据，返回 (退出码, 合并后的输出)。

        假 makensis 干四件事：按真编译器的口径验 `/DVER4=` 是不是 X.X.X.X、印横幅
        （或不印）、把两个交付文件名落出来、按指定码退出。2026-11-12 那次云端红就
        停在第一件事：makensis 报「invalid VIProductVersion format」直接 abort，前面
        所有步骤全绿。判据读的是它自己的那套 shell 语义，与被编的东西无关——这正是
        能本机复现的部分，所以把编译器的这条行为也一起搬进假编译器。
        crlf=True 按 Windows 编译器的实际输出给行尾：判据取到的那行会带一个尾随 \\r，
        「在本机换行下绿、到 Windows 换行下红」正是这一段最可能被漏掉的那半个坑。
        binary=True 在横幅前面塞两个非 UTF-8 字节：真编译日志里就有（安装包里的中文
        菜单名是 ANSI 写的），GNU grep 会把整份文件判成二进制并只回一句
        "Binary file matches"，横幅照样取不到——少了 `-a` 就是这个下场，而它在
        本机那种干净 ASCII 日志上永远试不出来。
        bad_temp=True 把 RUNNER_TEMP 指到本次仿真目录里一个**不存在**的子目录：仿真
        「编译器成功、只有 tee 写不下去」那一路（Windows 上 cygpath/MSYS 把路径转坏就是
        这个形状）。取码只看 $? 时这一路会红成「makensis 编译失败」，而编译器明明没坏。
        路径挂在本次的临时目录下（不是系统 /tmp 里一个固定名）：并发批跑时别人碰不到它，
        「这次到底写没写成」只由本次夹具决定。
        stamp_build 是**随包构建戳里的那个构建号**，不是环境变量：本轮把版本号那一段从
        「shell 自己拆 16 位字段」改成「问 tool_version --print-field」以后，这一步真正
        的输入就只剩 tempdir 里那一份 version.json。所以夹具在这里得自己摆好三样东西——
        模块本体（`stamp_file()` 源码态是按 `__file__` 所在目录找戳的，只 cd 过去不搬模块
        就拿不到戳，那种红会假装成「上一步没写戳」）、戳本身、以及一个把 `python` 递进
        PATH 的转发脚本（CI 那台 runner 上 python 在 PATH 里，判据不许赌镜像上有什么，
        所以这一格由夹具自己给）。stamp_build=None 表示「上一步压根没写出戳」，那是本轮
        新增的一味反例，形状与「戳里是个非数字」不同：前者连文件都不存在。
        APP_VERSION 仍然传，但它现在只剩 legacy 那一句反例在用（本轮那种三段拼法）。正例
        一律不再读它——「正例还吃得下 APP_VERSION」就意味着 shell 还在自己拼版本号，而那
        正是本轮要拆掉的第二份实现（上一条文本判据同时判了它不在场，这里判它在场时只
        服务于反例，两头合起来才封得住）。
        """
        root = Path(tempfile.mkdtemp(prefix="b2b_nsis_gate_"))
        try:
            shutil.copy(_REPO / "b2bcollect_build" / "tool_version.py", root)
            if stamp_build is not None:
                # 原样写进去，不经 json.dumps 之前不洗值：CI 那一步也是这么把 run_number
                # 的字节直接递过去的，像不像数、带不带引号都由 tool_version 自己判。
                (root / "version.json").write_text('{"build": "%s"}' % stamp_build,
                                                   encoding="utf-8")
            py = root / "python"
            py.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n', encoding="utf-8")
            py.chmod(0o755)
            stub = root / "makensis"
            eol = r"\r\n" if crlf else r"\n"
            head = 'printf "\\200\\377\\n"\n' if binary else ""
            head += (f'printf "{banner}{eol}"\n' if banner
                     else "echo 'Processing config: installer/b2bcollect_setup.nsi'\n")
            stub.write_text("#!/bin/sh\n"
                            # 先把它**实际收到**的两格 /D 值原样念回来，再按真编译器口径判
                            # 格式：顺序反过来就只剩「被拒了，但收下的是什么」这种问不出口的
                            # 证据，而下面那组正例要的恰恰是「模块算出的那一格 == 编译器吃下
                            # 的那一格」。
                            'for a in "$@"; do case "$a" in /DVER4=*) '
                            'echo "STUB-VER4=${a#/DVER4=}";; '
                            '/DAPP_VERSION=*) echo "STUB-APPV=${a#/DAPP_VERSION=}";; '
                            'esac; done\n'
                            # 与真实 makensis 同口径：VIProductVersion 必须是四段数字
                            'for a in "$@"; do case "$a" in /DVER4=*) '
                            'v=${a#/DVER4=}; '
                            'echo "$v" | grep -qE "^[0-9]+\\.[0-9]+\\.[0-9]+\\.[0-9]+$" || '
                            '{ echo "Error: invalid VIProductVersion format, should be X.X.X.X"; '
                            'echo "Error - aborting creation process"; exit 1; }; ;; esac; done\n'
                            + head
                            # 两个交付文件名也从**注入的 env** 里取，不在这里抄一遍名字：
                            # 真包名改了（上一轮就是这种漂移），夹具跟着落新名字，而
                            # `ls -l "$OUT_FILE"` 那一问继续是真的在问交付物在不在。
                            + ': > "${OUT_FILE:?夹具没注入 OUT_FILE}"\n'
                              ': > "${PORTABLE_NAME:?夹具没注入 PORTABLE_NAME}"\n'
                              f"exit {stub_rc}\n", encoding="utf-8")
            stub.chmod(0o755)
            env = dict(os.environ, **INST_ENV,
                       MAKENSIS=str(stub), NSIS_PINNED=pinned,
                       RUNNER_TEMP=(str(root / "nope") if bad_temp else str(root)),
                       APP_VERSION=(stamp_build if stamp_build is not None else ""),
                       WIN_ROOT=str(root),
                       # 环境变量这一格必须清空：它是 tool_version 的第二来源，留着宿主上
                       # 可能存在的值就等于让夹具之外的东西决定版本号，红了也说不清是谁。
                       B2B_BUILD_NUMBER="",
                       PATH=str(root) + os.pathsep + os.environ.get("PATH", ""))
            cmd = _bash_or_die("-e") + (["-o", "pipefail"] if pipefail else []) + \
                  ["-c", script or gate_script]
            # errors="replace"：binary=True 时子进程的输出里就是非法 UTF-8 字节，
            # 严格解码会让取证本身炸掉（观测手段不能污染被测对象，这条在这轮已经付过学费）
            proc = subprocess.run(cmd, cwd=str(root), env=env,
                                  capture_output=True, text=True, errors="replace")
            return proc.returncode, _redact(proc.stdout + proc.stderr)
        finally:
            shutil.rmtree(root, ignore_errors=True)

    # 正例一律用**钉在上面的真字节**（NSIS_V4_HEAD），不再用手抄的一句话：这一段的
    # 历史就是一次「假编译器印假格式、判据读假格式、双双绿着把真交付挡在门外」。
    _ok_rc, _ok_out = _run_installer_gate(NSIS_V4_HEAD, 0)
    _assert_eq(_ok_rc, 0, f"正例：真 makensis 的自报横幅与钉住版本一致时放行（输出尾部 {_ok_out[-90:]}）")
    _assert("✅ 安装包出自" in _ok_out,
            "放行时把这个安装包出自哪个编译器念进日志（可追溯性写在交付当场，不靠事后翻 CI）")
    _assert(NSIS_V4_BANNER in _ok_out,
            f"放行话术里念的是判据真取到的那一段（{NSIS_V4_BANNER}），不是重新拼一遍的字符串")
    _crlf_rc, _crlf_out = _run_installer_gate(NSIS_V4_HEAD, 0, crlf=True)
    _assert_eq(_crlf_rc, 0, f"正例·Windows 行尾：横幅带 \\r 也放行（输出尾部 {_crlf_out[-90:]}）")
    _bin_rc, _bin_out = _run_installer_gate(NSIS_V4_HEAD, 0, binary=True)
    _assert_eq(_bin_rc, 0, f"正例·日志含非 UTF-8 字节：横幅照样取得到（尾部 {_bin_out[-90:]}）")
    _old_rc, _old_out = _run_installer_gate(NSIS_LEGACY_BANNER + "\n", 0)
    _assert_eq(_old_rc, 0, f"正例·老 NSIS 那句自报写法也认（尾部 {_old_out[-90:]}）")
    _bad = {
        "版本不是钉住的那个": _run_installer_gate(NSIS_V4_HEAD.replace("v3.10", "v3.08"), 0),
        # 前缀撞车：旧写法是 case 通配 `*v3.10*`，一台自报 v3.100 的编译器会被判成
        # 「就是钉住的 3.10」放行——现在按全等比，必须红。
        "自报 v3.100 冒充 3.10": _run_installer_gate(NSIS_V4_HEAD.replace("v3.10", "v3.100"), 0),
        "连 -V4 都不印横幅": _run_installer_gate("", 0),
        "编译器非零退出": _run_installer_gate(NSIS_V4_HEAD, 1),
        "关掉 pipefail 仍要认得编译器": _run_installer_gate(NSIS_V4_HEAD, 3,
                                                          pipefail=False),
        "上一步没传版本号": _run_installer_gate(NSIS_V4_HEAD, 0, pinned=""),
    }
    _needles = {"版本不是钉住的那个": "不是钉住的 3.10",
                "自报 v3.100 冒充 3.10": "不是钉住的 3.10",
                # 取不到横幅时必须把日志开头原样贴出来：下一回编译器改措辞，靠这几行
                # 就能当场对格式，而不是再猜一次、再红一次。
                "连 -V4 都不印横幅": "读不到 makensis 自报版本",
                "编译器非零退出": "编译安装包失败（退出码 1",
                "关掉 pipefail 仍要认得编译器": "编译安装包失败（退出码 3",
                "上一步没传版本号": "NSIS_PINNED 是空的"}
    for _tag, (_rc, _out) in _bad.items():
        _assert(_rc != 0 and _needles[_tag] in _out and "✅" not in _out,
                f"反例·{_tag}：必须非零退出、说清后果、且不印成功话术（rc={_rc}，尾部 {_out[-120:]}）")
    _assert("Processing config" in _bad["连 -V4 都不印横幅"][1],
            "读不到横幅时，判据把它当场看到的日志开头原样念出来（猜格式的人需要证据，不需要形容词）")

    # 反向对照：救「编译器退 3 + 没 pipefail」那一场的必须是**本步自己那句声明**，不是
    # runner 的默认值。摘掉它、同一份仿真就必须退化成「编译器失败却被当成成功放行」——
    # 只写文本判据（上面那条 need）证明不了这行代码真的在起作用。
    _nopipe = gate_script.replace("set -o pipefail\n", "", 1)
    _assert(_nopipe != gate_script,
            "反向对照用的脚本真摘掉了本步的 pipefail 声明（没摘成功=拿同一段代码自己跟自己比）")
    _rc, _out = _run_installer_gate(NSIS_V4_HEAD, 3, pipefail=False, script=_nopipe)
    _assert(_rc == 0 and "✅" in _out,
            f"反向对照：摘掉声明后编译器退 3 被当成成功放行（rc={_rc}，尾部 {_out[-120:]}）"
            f"——这一路的保护只能由本步自己给")

    # 5-e-i′) 「只有 tee 坏」这一路必须说清坏的是谁。取了首段退出码以后，编译器成功、
    #   日志没写成的那一次就不再被第一道门拦下，而会一路走到读横幅那句「读不到 makensis
    #   自报版本」——把一次落盘故障说成编译器行为异常，正好是本轮反复踩的那种形状：错误
    #   的话听起来像另一个东西坏了。所以这一路要自己的一句话，而且不许提「编译失败」。
    _tee_only = _run_installer_gate(NSIS_V4_HEAD, 0, bad_temp=True)
    _assert(_tee_only[0] != 0 and "坏的是 tee/落盘" in _tee_only[1]
            and "编译安装包失败" not in _tee_only[1] and "✅" not in _tee_only[1],
            f"反例·编译器成功但日志写不成：非零退出、点名落盘这一步、不冤枉编译器"
            f"（rc={_tee_only[0]}，尾部 {_tee_only[1][-160:]}）")
    # 反向对照：同一份仿真脚本只把取码换回 `$?`，这一路就会红成「makensis 编译失败（退出码 1）」
    # ——那个 1 是 tee 的。这条对照钉住的正是「PIPESTATUS[0] 是为了归因，不是为了抓到非零」
    # （pipefail 取最右非零，两种写法都抓得到；只有首段口径才不撒谎，Z 节跑真 bash 作证）。
    _blame = gate_script.replace("NSIS_RC=${PIPESTATUS[0]}", "NSIS_RC=$?")
    _assert(_blame != gate_script,
            "反向对照用的脚本真把取码换回了 $?（没换成功就是拿同一段代码自己跟自己比）")
    _blame_out = _run_installer_gate(NSIS_V4_HEAD, 0, script=_blame, bad_temp=True)
    _assert(_blame_out[0] != 0 and "编译安装包失败（退出码 1" in _blame_out[1],
            f"反向对照：只改取码写法这一路就把 tee 的 1 报成编译器的失败"
            f"（rc={_blame_out[0]}，尾部 {_blame_out[1][-160:]}）——放行 PIPESTATUS[0] 的理由在这")

    # 5-e-ii) 版本号那四个数字。2026-11-12 的 Windows 红法：PyInstaller、产物自检、窗口
    #   冒烟全部通过，最后一步 makensis 回一句「invalid VIProductVersion format,
    #   should be X.X.X.X」就 abort —— 旧拼法 `/DVER4="1.$APP_VERSION.0"` 凑出来的是
    #   三段。这种红只在编译器那一步出现，本机没有 makensis，所以把这条口径搬进假
    #   编译器：拿本轮真实用过的那句拼法喂它，必须被拒；现在这两格都不再由 shell 自己算，
    #   而是问 tool_version（它读的就是 CI 上一步写下去、并且随包进包的那一份戳），所以
    #   这里喂的是**戳里的那个构建号**，非法输入的形状也跟着从「环境变量」换成了「戳」：
    #   没写、空、不像数、超出四段可表达范围——四种都在下面，每种都要停下来说清是谁坏了。
    legacy = gate_script.replace('/DVER4="$VER4"', '/DVER4="1.$APP_VERSION.0"')
    _assert(legacy != gate_script,
            "安装步骤里找得着那句 makensis 调用（找不着=这段判据在跟着代码一起漂）")
    _rc, _out = _run_installer_gate(NSIS_V4_HEAD, 0, script=legacy)
    _assert(_rc != 0 and "invalid VIProductVersion" in _out and "✅" not in _out,
            f"反向对照：本轮那种三段版本号必须被编译器拒掉（rc={_rc}，尾部 {_out[-120:]}）")
    for _tag, _n in (("常规 run_number", "999"), ("跨过 16 位边界", "65536"),
                      ("七位数", "1234567")):
        _rc, _out = _run_installer_gate(NSIS_V4_HEAD, 0, stamp_build=_n)
        _assert_eq(_rc, 0, f"{_tag}（戳里 build={_n}）也要拼得出合法版本号（尾部 {_out[-110:]}）")
        # 只判 rc=0 不够：shell 用另一种写法**重新拼一遍同样的数字**也会绿，而第二份实现
        # 就是这么长回来的。要比的是编译器实收的字节 == 模块导出的字节。
        _assert(f"STUB-VER4={tv.product_version(int(_n))}\n" in _out
                and f"STUB-APPV={tv.version_text(int(_n))}\n" in _out,
                f"{_tag}：编进安装包的版本字节必须逐字等于 tool_version 的导出值，"
                f"不是 shell 再算一遍（实收 {_out[-170:]}）")
    for _tag, _n, _said in (("非数字", "1.2.3-beta", "没能从随包构建戳里读出来"),
                             ("空值", "", "没能从随包构建戳里读出来"),
                             ("戳压根没写", None, "没能从随包构建戳里读出来"),
                             ("大到四段装不下", "5000000000", "没能从随包构建戳里算出来")):
        _rc, _out = _run_installer_gate(NSIS_V4_HEAD, 0, stamp_build=_n)
        # 「停下」还不够，还得**没递出去**：假编译器收不到号才是真的不开建。少了这半句，
        # 「先调用 makensis、再补一句错误」也会绿，而那正是 makensis abort 掉以后的形状。
        _assert(_rc != 0 and _said in _out and "✅" not in _out and "STUB-VER4=" not in _out,
                f"反例·{_tag}的构建戳：不开建就停下来说清是谁坏了，且不把号递到编译器手上"
                f"（rc={_rc}，尾部 {_out[-110:]}）")

    # 5-f) 注解必须是「本步 shell 打出来的单行文本」。跨行或引号没闭合，GitHub 不认这条
    # ::error::，于是失败原因静默消失，屏幕上只剩一个说不清的退出码——本轮写上面那段时就
    # 真漏了一个引号（bash -n 抓到了语法，但语法过了不等于注解还在）。判据对两个 workflow
    # 的每个 step 都跑，不只对本次改的这一段；打印语句按这一步真正用的 shell 认（bash 用
    # echo/printf，pwsh 用 Write-Host/Write-Output），拿一种 shell 的关键字去判另一种，
    # 就会把写得对的那一步判成红的——而消红的人下一步就会去改判据。
    _ANNOT_PRINTERS = {"bash": ('echo "', 'printf "'),
                       "pwsh": ('Write-Host "', 'Write-Output "', 'echo "')}

    def _annotation_problems(script: str, shell: str = "bash") -> list:
        printers = _ANNOT_PRINTERS.get(shell, _ANNOT_PRINTERS["bash"])
        out = []
        for i, ln in enumerate(script.splitlines(), 1):
            k = ln.find("::error::")
            while k >= 0:
                if not any(p in ln[:k] for p in printers):
                    out.append(f"第 {i} 行 ::error:: 前面没有本步 shell 认得的打印语句"
                               f"（不是打出来的注解 Actions 不认，失败原因会静默消失）")
                elif '"' not in ln[k + len("::error::"):]:
                    out.append(f"第 {i} 行 ::error:: 后面没有闭合的引号（跨行的注解会被直接丢掉，报错静默消失）")
                k = ln.find("::error::", k + 1)
        return out

    _assert_eq(_annotation_problems(gate_script), [], f"{INST_STEP} 的失败注解都是单行 echo 出来的")
    for _label, _body in _iter_steps(win):
        _assert_eq(_annotation_problems(_body, _step_shell(_body, "pwsh")),
                   [], f"Windows step「{_label}」注解单行且引号闭合")
    for _label, _body in _iter_steps(mac):
        _assert_eq(_annotation_problems(_body, _step_shell(_body, "bash")),
                   [], f"Mac step「{_label}」注解单行且引号闭合")
    _assert_eq(_annotation_problems('echo "::error::甲' + "\n" + '乙"'),
               ['第 1 行 ::error:: 后面没有闭合的引号（跨行的注解会被直接丢掉，报错静默消失）'],
               "反向对照：本轮真实写漏的那个引号必须被这条抓住（否则判据只是现在恰好没样本）")
    _assert_eq(_annotation_problems('::error::没走 echo'),
               ['第 1 行 ::error:: 前面没有本步 shell 认得的打印语句'
                '（不是打出来的注解 Actions 不认，失败原因会静默消失）'],
               "反向对照：裸写注解不经过打印语句同样要红")
    _assert_eq(_annotation_problems('Write-Host "::error::产物没生成到位"', "pwsh"), [],
               "反向对照：pwsh 那两步用 Write-Host 是合法的，不该被 bash 的关键字误判")
    _assert_eq(len(_annotation_problems('echo "::error::甲"')), 0,
               "反向对照：bash 写法喂给 bash 判据也是空的（判据不是一律判红）")


# ══════════════════════════════════════════════ N. 打包清单硬门 + 采集通道完整性
class _NoSuchKeyClient:
    """凭证通、网络通、但名单还没播种过的 OSS（自检里唯一那条提前返回的分支）。"""

    bucket = "images6396289"
    endpoint = "oss-cn-shenzhen.aliyuncs.com"

    def download_json(self, key, *a, **kw):
        raise OSSPoolError("404 NoSuchKey: 指定的名单文件不存在")


def test_packaging_manifest_gate():
    """2026-11-12 实测：等级 0 的账号在产物里只看到企知道 —— 根因是三处入口的
    --add-data 漏收了 riskbird_crawler_v2.py / aiqicha_crawler_v2.py。

    「少一个文件不会报错、只会少一个功能」是这一类缺陷的共同形状，而且它在界面上和
    「档位没给这个功能」一模一样，第一轮排查必然被带去查权限。所以六个方向都要钉住：
      1) 清单真源（site_adapters.EXTERNAL_MODULES）与三处入口逐条对拍，含目标目录；
      2) 硬门脚本自己判得对不对（反例：删一条、放错目录，都必须红）；
      2b) 第 7 条——要进包的源码必须已在**提交**里（本机绿、CI 检出后没文件就是它）；
      2c) 第 8 条——清单顺着 import 走必须闭合，故意让位给本目录的那几笔要登记理由；
      2d) 第 10 条——三个交付目录里的同名拷贝要么字节一致、要么登记分叉理由（生效的那
          份由 import 顺序决定，「一致但都是旧的」还要按行为钉住）；
      2e) 第 13 条——Windows 装配清单与构建命令的 --add-data **双向**对拍（单向清单等于
          没有清单：构建命令放进包的东西，装配校验可能压根不知道它存在）；
      3) 产物自检的通道完整性（打包态退 3、源码态只警告、那条提前返回不放过）；
      4) 界面说话只点名「该给却没给」的那几路，档位藏起来的路绝不念出来。
    """
    print("\n═══ N. 打包清单硬门 + 采集通道完整性（少一路=静默少功能）═══")
    gate = _manifest_gate()
    externals, channels = gate.module_manifest()
    _assert_eq(set(channels), {"qzd", "riskbird", "aiqicha"},
               "名单通道应有路数 = 企知道 / 风鸟 / 爱企查 三路")
    _assert(any("riskbird_crawler_v2.py" in r for r in externals)
            and any("aiqicha_crawler_v2.py" in r for r in externals),
            "两个 v2 采集器在清单真源里（正是这次漏掉的那两个文件）")

    # 1) 三处入口逐条对拍（正例）
    modules = gate.local_modules()
    _assert(len(modules) >= 40, f"本地模块清单扫出来了（{len(modules)} 个）")
    for label, path in gate.CALLERS.items():
        _assert(path.is_file(), f"{label} 入口在位：{path}")
        problems = gate.check_caller(label, path.read_text(encoding="utf-8", errors="replace"),
                                     externals, modules)
        _assert_eq(problems, [], f"{label} 过打包清单硬门（任何一条不合格都拦在开建之前）")

    # 2) 硬门自己判得对不对：两个反例必须红，否则「门禁全绿、用户少功能」会原样复发
    local = gate.CALLERS["本机 Mac 打包脚本"].read_text(encoding="utf-8")
    cut = "\n".join(ln for ln in local.splitlines() if "riskbird_crawler_v2.py" not in ln)
    _assert(cut != local, "反例①构造成功（删掉那条 --add-data；没删掉就是空跑）")
    got = gate.check_caller("反例①", cut, externals, modules)
    _assert(any("riskbird_crawler_v2.py" in p and "缺跨目录模块" in p for p in got),
            f"漏一条 --add-data 必须被点名（实际={got}）")
    moved = local.replace("riskbird_build/riskbird_crawler_v2.py:riskbird_build",
                          "riskbird_build/riskbird_crawler_v2.py:.")
    _assert(moved != local, "反例②构造成功（目标目录写成 .）")
    got2 = gate.check_caller("反例②", moved, externals, modules)
    _assert(any("riskbird_crawler_v2.py" in p and "目标目录不是 riskbird_build" in p for p in got2),
            f"文件进了包但落错目录同样要红（运行时按 _MEIPASS/riskbird_build/ 找不到）：{got2}")
    # 注释里的参数名不是参数：这颗坑在硬门和本用例里各踩过一次，解析器现在只有一份
    _assert_eq(gate._flag_values(gate._strip_comments(
                   "# --hidden-import 注释里的假项\n--hidden-import real_mod\n"),
                   "--hidden-import"), ["real_mod"],
               "整行注释被剔掉，只有真正会执行的参数进清单")

    # 2b) 第 7 条「进包源码必须已在提交里」。这是 2026-11-12 的第二刀：清单写得对、
    # 磁盘上文件也在、本机打包全绿，唯独没 git add —— CI 检出后文件压根不存在，两个
    # 平台的构建一起红在「--add-data 指向的源码不存在」。判据必须能区分三种状态：
    # 在提交里 / 只 add 没提交 / 压根没跟踪，后两种都要红，而且要点名下一步命令。
    probe = gate.ROOT / "_gate_untracked_probe.py"
    _assert(not probe.exists(), "反例③起点干净（探针文件不存在）")
    probe.write_text("# 硬门自审用探针，跑完即删\n", encoding="utf-8")
    try:
        _assert_eq(gate.tracked_state(probe), "missing", "新文件＝压根没被跟踪")
        _assert_eq(gate.tracked_state(gate.ADAPTERS), "ok", "已提交的源码＝在提交里")
        got3 = gate.check_tracked([("../_gate_untracked_probe.py", ".")])
        _assert(any("_gate_untracked_probe.py" in p and "从未被 git 跟踪" in p
                    for p in got3), f"未跟踪的进包源码必须红并给出 git add（实际={got3}）")
        # 分隔符两种写法都要能换算到仓库内路径：Windows 用 ; 、类 Unix 用 :
        _assert_eq(gate._split_add_data("../x.py;pkg")[1], "pkg", "Windows 分号拆得开")
        _assert_eq(gate._split_add_data("../x.py:pkg")[1], "pkg", "类 Unix 冒号拆得开")
        _assert_eq(gate._package_input("../riskbird_build/x.py").name, "x.py",
                   "跨目录 --add-data 换算到仓库内路径（第 7 条判的就是它）")
        # 有意不提交的那类（Secrets 现写 / CI 现下载）必须在表里写理由，不能靠沉默放行
        got3c = gate.check_tracked([("config.json", ".")])
        _assert_eq(got3c, [], "GENERATED_AT_BUILD 登记过的输入不误伤（否则门禁变噪音）")
    finally:
        probe.unlink(missing_ok=True)
    # 同一形状的另外两个入口：本目录新模块（靠 --hidden-import 进包）与入口命令引用的
    # 构建期脚本。少提交它们的下场分别是一句谁也看不懂的 "Hidden import not found" 和
    # 「门禁脚本自己没检出」，比少一条 --add-data 更难查，所以一并判。
    mod_probe = gate.BUILD_DIR / "zz_gate_probe_module.py"
    _assert(not mod_probe.exists(), "反例④起点干净（本地模块探针不存在）")
    mod_probe.write_text("# 硬门自审探针，跑完即删\n", encoding="utf-8")
    try:
        _assert("zz_gate_probe_module" in gate.local_modules(),
                "新增的本地模块会进 local_modules（说明 --hidden-import 那条判据也看得见它）")
        got7 = gate.check_tracked([], gate.CALLERS["Mac workflow"].read_text(encoding="utf-8"))
        _assert(any("zz_gate_probe_module.py" in p and "--hidden-import" in p for p in got7),
                f"本地模块没提交同样要红（实际={[p for p in got7 if 'zz_gate' in p]}）")
    finally:
        mod_probe.unlink(missing_ok=True)
    got8 = gate.check_tracked([], gate.CALLERS["Mac workflow"].read_text(encoding="utf-8"))
    _assert_eq([p for p in got8 if "zz_gate" in p], [], "探针删掉后立刻不再报（不是残留误报）")
    _assert(any(".github/scripts/" in str(p) for p in
                gate.referenced_inputs(gate.CALLERS["Mac workflow"].read_text(encoding="utf-8"))),
           "入口引用的构建期脚本被扫进第 7 条的范围（缺一个连门禁自己都跑不起来）")
    # 分隔符：git 在任何平台都输出正斜杠，而 Windows 上 str(Path(...)) 给的是反斜杠。
    # 两边直接比对的结果是 2026-11-12 那次 Windows 构建里**每一个**进包文件都被判成
    # 「从未被 git 跟踪」——门禁自己坏了，比没有门禁更糟。Mac 上复现不出来，所以这里
    # 用「源码文本 + 嵌套路径」两条一起钉住：前者防写法改回去，后者防判据只测过顶层。
    gate_src = (gate.ROOT / ".github" / "scripts" /
                "check_packaging_manifest.py").read_text(encoding="utf-8")
    _assert("path.relative_to(ROOT).as_posix()" in gate_src,
            "_rel_posix 用 as_posix 归一分隔符（不写归一，Windows runner 上整条清单全红）")
    _assert("rel = _rel_posix(path)" in gate_src,
            "tracked_state 只比归一后的路径，不拿 str(Path) 去撞 git 的输出")
    _assert_eq(gate._rel_posix(gate.BUILD_DIR / "site_adapters.py"),
               "b2bcollect_build/site_adapters.py",
               "比对用的说法与 git 一致（子目录也要是正斜杠）")
    _index, _head = gate._tracked_sets()
    _assert(all("\\" not in x for x in _head),
            "git 列出来的路径里没有反斜杠（这条就是上面那处归一的现实依据）")
    # 同一次实跑里抓出来的第二个坑：git 默认把非 ASCII 文件名八进制转义再套引号，
    # 那种字符串跟任何真实路径都对不上，于是每个中文名文件都被判成「没提交」。
    # 判据跨平台的另一半含义是**跨文件名**，所以这里直接拿仓库里真有的中文名文件验。
    cjk = next((x for x in sorted(_head) if not x.isascii()), "")
    _assert(cjk, f"仓库里确实有非 ASCII 路径，下面这条不是空跑（HEAD 清单 {len(_head)} 项）")
    _assert_eq(gate.tracked_state(gate.ROOT / cjk), "ok",
               f"中文名文件也认得（不加 -z 拆 NUL 就会被 git 的转义说法骗过去）：{cjk}")
    sub = gate.BUILD_DIR / "zz_gate_probe_pkg"
    sub.mkdir(exist_ok=True)
    nested = sub / "mod.py"
    nested.write_text("# 分隔符探针，跑完即删\n", encoding="utf-8")
    try:
        _assert_eq(gate.tracked_state(nested), "missing",
                   "子目录里未跟踪的文件也认得（分隔符判错的话这条在 Windows 上是唯一漏网）")
    finally:
        nested.unlink(missing_ok=True)
        sub.rmdir()
    # 真清单实跑：三处入口的进包输入此刻都必须已在提交里（这条红＝现在推上去 CI 必红）
    _assert_eq(gate.check_tracked(
        [gate._split_add_data(t) for t in gate._flag_values(
            gate._strip_comments(gate.CALLERS["Mac workflow"].read_text(encoding="utf-8")),
            "--add-data")]), [], "Mac workflow 的进包输入全部已在提交里")

    # 2c) 第 8 条「跨目录依赖链必须闭合」：清单是人手抄的，顺着 import 走一遍才敢收口。
    _assert_eq(gate.check_closure(externals, gate.shadowed_modules()), [],
               "EXTERNAL_MODULES 顺着 import 走是闭合的（含 SHADOWED 显式让位那几笔）")
    _assert("customer_library_cleaner" in gate.shadowed_modules(),
            "1688 清洗模块由本目录供给这件事是登记过的，不是碰巧 import 顺序对上")
    _assert((gate.BUILD_DIR / "customer_library_cleaner.py").exists(),
            "SHADOWED 说由本目录供给，本目录就得真有这个文件")
    no_shadow = {k: v for k, v in gate.shadowed_modules().items()
                 if k != "customer_library_cleaner"}
    got4 = gate.check_closure(externals, no_shadow)
    _assert(any("customer_library_cleaner" in p for p in got4),
            f"撤掉那笔让位登记，链就必须报红（否则第 8 条是空转）：{got4}")
    no_block = tuple(x for x in externals if not x.endswith("block_guard.py"))
    got5 = gate.check_closure(no_block, gate.shadowed_modules())
    _assert(any("block_guard.py" in p and "没进 EXTERNAL_MODULES" in p for p in got5),
            f"漏登记一个传递依赖同样要红（真漏，不是让位）：{got5}")
    got6 = gate.check_closure(externals + ("ali1688_build/customer_library_cleaner.py",),
                              gate.shadowed_modules())
    _assert(any("两份同名实现" in p for p in got6),
            f"让位登记与进包清单同时出现＝两份实现一起进包，必须红：{got6}")

    # 2d) 第 10 条「跨目录同名拷贝必须在案」。这条盯的不是「少文件」，是「多份文件里挑了
    # 一份旧的」：三个交付目录里有同名 .py，产物实际吃到哪一份由 sys.path 顺序决定
    # （riskbird_gui 把 ../b2bcollect_build 插到最前，b2bcollect 那份永远赢）。本轮实测的
    # 代价：两处上传侧修复只落在 riskbird 那份 yzd_client.py 里，进包生效的是 b2bcollect
    # 那份旧的——界面不报错、日志不报错、CI 全绿，客户库里却多了没有联系方式的行，超长
    # 手机号被从中间切成打不通的半截号。症状和「压根没写过这个修复」一模一样。
    _assert_eq(gate.check_shared_source(), [],
               "真实仓库现况：同名源码要么字节一致，要么在 DIVERGENT_VENDORED 登记了理由")
    copies = [gate.ROOT / d / "yzd_client.py" for d in gate.SHARED_SOURCE_DIRS]
    _assert(all(p.is_file() for p in copies), "yzd_client 三份拷贝都在位")
    _assert_eq(len({gate._file_digest(p) for p in copies}), 1,
               "yzd_client 三份字节一致（本轮同步的就是那两处上传侧修复）")
    # 一致 ≠ 对：三份一起停在旧实现，这条判据同样是绿的。所以还要按行为钉住生效的那一份
    import yzd_client as _yzd
    _numbers = ",".join(f"1380000000{i}" for i in range(9))     # 9 条 = 107 字符 > 上限 100
    _clamped = _yzd.clamp_fields({"手机号": _numbers})["手机号"]
    _assert(len(_clamped) <= _yzd.FIELD_LIMITS["手机号"], "超长号码列收进字段上限")
    _assert(all(_yzd.PHONE_RE.search(p) for p in _clamped.split(",")),
            f"收拢后每一条都是完整号码（旧实现 text[:100] 会把最后一条切成半截打不通）"
            f"（实际={_clamped!r}）")
    _assert(not _yzd.has_contact({"企业名称": "只有一家公司名", "手机号": "", "固话": "",
                                 "邮箱": "", "微信号": ""}),
            "三个联系方式全空的行判为「不可入库」（旧实现没有这道过滤）")

    # 反向对照：把判据放回到一个假仓库里跑四种现场，缺一种就说明它有一头是空转
    fake = Path(tempfile.mkdtemp(prefix="shared_gate_"))
    try:
        dirs = {}
        for _i, name in enumerate(gate.SHARED_SOURCE_DIRS):
            dirs[name] = fake / name
            dirs[name].mkdir(parents=True)
            # same.py 三份一致；fork.py 三份不同；test_only.py 只有一份且是测试文件
            # （分叉内容按下标生成，绝不能用 hash()：它是每进程加盐的，两台机器上可能
            #   撞成相同值，那下面几条「必须红」就全成了空跑）
            (dirs[name] / "same.py").write_text("A = 1\n", encoding="utf-8")
            (dirs[name] / "fork.py").write_text(f"B = {_i}\n", encoding="utf-8")
            # 测试文件三份故意不同：前缀判据一旦失效，这条就会把回归套件判成缺陷
            (dirs[name] / "test_only.py").write_text(f"# {_i}\n", encoding="utf-8")
        # 夹具检查：fork.py 必须真的分叉，否则下面几条「必须红」全是空跑
        _assert_eq(len({(d / "fork.py").read_bytes() for d in dirs.values()}),
                   len(gate.SHARED_SOURCE_DIRS), "反例起点：fork.py 三份内容互不相同")
        with _patch(gate, "ROOT", fake), _patch(gate, "DIVERGENT_VENDORED",
                                                {"fork.py": "两份按产品各留一份（假登记）"}):
            _assert_eq(gate.check_shared_source(), [],
                       "一致的不动、分叉的登记过 → 放行（test_ 前缀不参与）")
        with _patch(gate, "ROOT", fake), _patch(gate, "DIVERGENT_VENDORED", {}):
            got9 = gate.check_shared_source()
            _assert(any("fork.py" in p and "sys.path" in p for p in got9),
                    f"分叉又没登记必须红，还要说清「哪份生效由 import 顺序决定」：{got9}")
            _assert_eq([p for p in got9 if "same.py" in p or "test_only.py" in p], [],
                       "一致的文件与测试文件不被牵连（误报会把整条门禁消耗成噪音）")
        with _patch(gate, "ROOT", fake), _patch(gate, "DIVERGENT_VENDORED", {"fork.py": "  "}):
            got10 = gate.check_shared_source()
            _assert(any("fork.py" in p and "没写理由" in p for p in got10),
                    f"登记了却不写理由同样要红：{got10}")
        for d in dirs.values():                     # 后来同步了，登记却留着
            (d / "fork.py").write_text("B = 1\n", encoding="utf-8")
        with _patch(gate, "ROOT", fake), _patch(gate, "DIVERGENT_VENDORED",
                                                {"fork.py": "曾经分叉的理由"}):
            got11 = gate.check_shared_source()
            _assert(any("fork.py" in p and "该删了" in p for p in got11),
                    f"早已一致还挂着「允许不同」的登记要红（否则下次真分叉时这张表不值得信）"
                    f"：{got11}")
        with _patch(gate, "ROOT", fake), _patch(gate, "SHARED_SOURCE_DIRS",
                                                ("b2bcollect_build", "no_such_build")):
            got12 = gate.check_shared_source()
            _assert(any("no_such_build" in p for p in got12),
                    f"交付目录整个不见了也要红，不能当成「没有同名文件」悄悄放过：{got12}")
    finally:
        shutil.rmtree(fake, ignore_errors=True)

    # 2e) 第 13 条「Windows 装配清单 ↔ 构建命令的 --add-data 双向对拍」。第 1 条判的是
    #     「运行时要什么 → 构建命令抄了没有」，方向单向；它管不到另一半：构建命令明明把
    #     文件放进了包，装配校验（package_win_artifacts.REQUIRED_INTERNAL）却不知道它存在。
    #     本轮实测漏登记的是 block_guard.py / chrome_driver.py / drivers：驱动目录哪天
    #     下载失败被剔掉，check_layout 一声不响、安装包照样出，症状与「用户机器没网」一样。
    #     反方向同样致命：清单登记了一项却没有任何 --add-data 产它，装配那一步会永远红在
    #     「_internal 缺 x」，而真原因是构建命令少抄了一行——报错报在校验上会把人带去查
    #     PyInstaller，而不是查 workflow 那一行。
    asm = _win_assembler()
    win_text = gate.CALLERS[gate.WIN_WORKFLOW_LABEL].read_text(encoding="utf-8")
    _assert_eq(gate.check_win_payload_manifest(win_text, asm), [],
               "真仓库现况：装配清单与构建命令互相钉死（每一项都有「缺了会怎样」的理由）")
    for key in ("riskbird_build/block_guard.py", "riskbird_build/chrome_driver.py", "drivers"):
        _assert(key in asm.REQUIRED_INTERNAL,
                f"{key} 必须在装配清单里（本轮实测漏掉的正是这三项）")
    _assert(asm.is_dir_entry("drivers") and asm.is_dir_entry("enf_clean")
            and not asm.is_dir_entry("riskbird_build/block_guard.py"),
            "目录/文件按「最后一段有没有扩展名分隔点」判，夹具与判据共用这一个口径")
    _assert_eq(gate.add_data_internal_key("../ali1688_build/drivers", "drivers"), "drivers",
               "目录型 --add-data 的落点就是目标目录本身（dst 是它在包里的名字）")
    _assert_eq(gate.add_data_internal_key("../riskbird_build/block_guard.py", "riskbird_build"),
               "riskbird_build/block_guard.py",
               "文件型 --add-data 的落点要带上文件名（同一目标目录下有多个文件）")
    _assert_eq(gate.add_data_internal_key("mk_phone_font.json", "."), "mk_phone_font.json",
               'dst="." 就是 _internal 根')

    # 反例：四种漂移都要红，而且要点名是哪个方向（报「清单不合格」却不指方向＝下个人从头比对）
    _k0 = "riskbird_build/block_guard.py"
    with _patch(asm, "REQUIRED_INTERNAL", {k: v for k, v in asm.REQUIRED_INTERNAL.items()
                                           if k != _k0}):
        got13 = gate.check_win_payload_manifest(win_text, asm)
        _assert(any(_k0 in p and "REQUIRED_INTERNAL 却没有它" in p for p in got13),
                f"构建命令放了进包、清单没登记 → 必须点名这一项（这是本轮的真漏）：{got13}")
    with _patch(asm, "REQUIRED_INTERNAL", dict(asm.REQUIRED_INTERNAL,
                                               **{"no_such_dir": "缺它 = 夹具自造项"})):
        got14 = gate.check_win_payload_manifest(win_text, asm)
        _assert(any("no_such_dir" in p and "少抄了一条" in p for p in got14),
                f"清单登记了、构建命令却不产它 → 必须说清真原因在 workflow：{got14}")
    _vague = dict(asm.REQUIRED_INTERNAL)
    _vague["config.json"] = "随包凭证"          # 有理由，但没说缺了会怎样
    with _patch(asm, "REQUIRED_INTERNAL", _vague):
        got15 = gate.check_win_payload_manifest(win_text, asm)
        _assert(any("config.json" in p and "没写清后果" in p for p in got15),
                f"理由不含后果时必须红（凑数的条目会把真漏项淹在里面）：{got15}")
    with _patch(asm, "REQUIRED_INTERNAL", _vague), \
            _patch(asm, "BY_BUILDER", {"base_library.zip": "  "}):
        got16 = gate.check_win_payload_manifest(win_text, asm)
        _assert(any("base_library.zip" in p and "没写它由谁产出" in p for p in got16),
                f"放行名单里空白理由＝放行任何名字，必须红：{got16}")
    with _patch(asm, "BY_BUILDER", dict(asm.BY_BUILDER, **{"phantom.zip": "自产"})):
        got17 = gate.check_win_payload_manifest(win_text, asm)
        _assert(any("phantom.zip" in p and "这条登记在空转" in p for p in got17),
                f"放行名单多出一项、校验清单里却没有 → 登记在空转，必须红：{got17}")
    with _patch(asm, "REQUIRED_INTERNAL", {}), _patch(asm, "BY_BUILDER", {}):
        got18 = gate.check_win_payload_manifest(win_text, asm)
        _assert(all("REQUIRED_INTERNAL 却没有它" in p for p in got18) and len(got18) >= 10,
                f"清单被清空时每一条 --add-data 都要点名（清空≠放行）：{len(got18)} 条")
    for _broken_wf in ("build:\n    echo 没有打包命令\n", "Name X\n", ""):
        _b = gate.check_win_payload_manifest(_broken_wf, asm)
        _assert_eq(_b, [p for p in _b if "切不出任何 --add-data" in p],
                   f"构建命令被改坏到读不出清单时必须红在「无从对拍」上（返回空列表=这条门"
                   f"在这份 workflow 上绿灯）（输入={_broken_wf!r}）")
        _assert(len(_b) == 1, f"这一格必须是**一条**红，而不是零条：输入={_broken_wf!r} "
                              f"实际={_b}")

    # CLI 退出码语义：CI 靠它拦构建，返回 1 就等于门禁形同虚设
    proc = subprocess.run([sys.executable,
                           str(_REPO / ".github" / "scripts" / "check_packaging_manifest.py")],
                          capture_output=True, text=True, cwd=str(_REPO))
    _assert_eq(proc.returncode, 0, f"硬门脚本实跑通过（stderr={proc.stderr[-200:]}）")

    # 3) 产物自检的通道完整性
    import site_adapters as _sa
    import b2bcollect_gui as gui
    two_lost = {"qzd": True, "riskbird": False, "aiqicha": False}
    errs = {"riskbird": "FileNotFoundError: riskbird_crawler_v2.py 不存在",
            "aiqicha": "ImportError: selenium 没装"}
    with _patch(_sa, "LIST_CHANNEL_AVAILABLE", two_lost), _patch(_sa, "ADAPTER_ERRORS", dict(errs)):
        _assert_eq(_sa.missing_list_channels(), ("riskbird", "aiqicha"),
                   "缺路口径只看「加载上了没有」，与档位/等级无关")
        code_f, lines_f = ag._channel_integrity(frozen=True)
        _assert_eq(code_f, ag.DOCTOR_EXIT_MODULE, "打包态缺通道 → 自检退 3（不许发货）")
        code_s, lines_s = ag._channel_integrity(frozen=False)
        _assert_eq(code_s, ag.DOCTOR_EXIT_OK, "源码态只警告不判红（开发机没装 selenium 不该拖红 CI）")
        # 中文名按 slug 取，不是按位置：位置映射在增删通道时全体错位，会把风鸟说成爱企查
        _assert(any("产物里缺采集通道：风鸟 / 爱企查" in ln for ln in lines_f),
                f"报告按 slug 点名缺哪几路（实际={lines_f[:2]}）")
        _assert(any("不是权限问题" in ln for ln in lines_f),
                "报告直接把人从「查权限」这条错路上拦回来")
        _assert(any("check_packaging_manifest" in ln for ln in lines_f),
                "报告给出可执行的修法（发包方照着跑就能定位缺哪条）")

    # 唯一那条提前返回的成功分支（名单还没播种）不许放过通道判红
    with _patch(_sa, "LIST_CHANNEL_AVAILABLE", two_lost), _patch(_sa, "ADAPTER_ERRORS", dict(errs)):
        with _workspace() as _ws, _frozen(_ws):
            with _patch(AccessGuard, "_load_bound_account", staticmethod(lambda: "")), \
                 _patch(AccessGuard, "_client_probe", staticmethod(lambda: (_NoSuchKeyClient(), "ok", ""))):
                code_e, lines_e = ag.doctor_lines()
                _assert_eq(code_e, ag.DOCTOR_EXIT_MODULE,
                           "「名单没播种 + 包少平台」两件事叠在一起时，提前返回也必须带通道退出码")
                _assert(any("采集通道完整性不通" in ln for ln in lines_e),
                        f"报告说清为什么权限项过了还判红（实际={lines_e[-3:]}）")
    with _patch(_sa, "LIST_CHANNEL_AVAILABLE", {"qzd": True, "riskbird": True, "aiqicha": True}):
        with _workspace() as _ws, _frozen(_ws):
            with _patch(AccessGuard, "_load_bound_account", staticmethod(lambda: "")), \
                 _patch(AccessGuard, "_client_probe", staticmethod(lambda: (_NoSuchKeyClient(), "ok", ""))):
                code_ok, _ = ag.doctor_lines()
                _assert_eq(code_ok, ag.DOCTOR_EXIT_OK, "通道齐全时，名单没播种仍然判通过（不误伤首次使用）")

    # 3b) 关键词通道里那个**从外部目录装载**的 1688 也在同一道自检里。它与名单三路
    # 是同一个装载形状（运行时按路径 import 外部 .py），此前却只被名单判据覆盖：
    # 它依赖的 customer_library_cleaner 靠「本目录同名模块先进 sys.modules」这条隐式链，
    # 链一断就是「界面上少一个来源、自检全绿」——正是本轮要根治的形状，所以判据必须
    # 覆盖到它，报告也必须把这一路打印出来。
    _assert_eq(_sa.missing_keyword_channels(), (), "源码实跑：关键词外部通道装载成功")
    with _patch(_sa, "HAS_1688", False), _patch(_sa, "ADAPTER_ERRORS",
                                                {"1688": "ModuleNotFoundError: customer_library_cleaner"}):
        _assert_eq(_sa.missing_keyword_channels(), ("1688",),
                   "1688 没装载起来时判据认得它（读当前值，不是定义时的快照）")
        code_kw, lines_kw = ag._channel_integrity(frozen=True)
        _assert_eq(code_kw, ag.DOCTOR_EXIT_MODULE,
                   "打包态缺关键词外部通道同样退 3（与缺名单路同级，不许发货）")
        _assert(any("1688 阿里巴巴" in ln for ln in lines_kw),
                f"报告按登记名点名缺了哪一路（实际={lines_kw[:3]}）")
        _assert(any("不是权限问题" in ln for ln in lines_kw),
                "缺 1688 同样要把人从「查权限」这条错路上拦回来")
    with _patch(_sa, "HAS_1688", True):
        _c, _l = ag._channel_integrity(frozen=True)
        _assert_eq(_c, ag.DOCTOR_EXIT_OK, "1688 在位时不判红（不误伤正常包）")
        _assert(any("关键词通道（外部目录）：1 路齐全" in ln for ln in _l),
                f"齐全时也要打印出来——不打印就没人知道这一路被验过（实际={_l}）")

    # 4) 界面说话：只念「该给却没给」的那几路
    _old_ed = os.environ.get(_ed.ENV_VAR)
    _old_lv = os.environ.get(_ed.LEVEL_ENV_VAR)
    try:
        with _patch(_sa, "LIST_CHANNEL_AVAILABLE", two_lost), _patch(_sa, "ADAPTER_ERRORS", dict(errs)):
            os.environ[_ed.ENV_VAR] = "pro"
            os.environ[_ed.LEVEL_ENV_VAR] = "0"
            _ed.reset_for_tests()
            _assert_eq(gui._lost_list_channels(), ("riskbird", "aiqicha"),
                       "pro + 等级 0 该有三路：缺的两路必须报出来")
            os.environ[_ed.ENV_VAR] = "lite"
            os.environ[_ed.LEVEL_ENV_VAR] = "2"
            _ed.reset_for_tests()
            _assert_eq(gui._lost_list_channels(), (),
                       "丐版本来只给企知道：缺风鸟/爱企查不能在日志里点名内部平台（等于把藏起来的功能念出来）")
            # 档位藏起来的那几路即使真的缺失也不许出现在给外部用户看的文案里
            _lost_pro = gui.MainWindow._lost_channels_text(("riskbird", "aiqicha"), "风鸟 / 爱企查")
            _assert("不是账号权限" in _lost_pro and "找发包方" in _lost_pro,
                    "缺路文案三件事齐全：是什么、不是什么（权限）、怎么办（要新包）")
            _lines = [x for x in _lost_pro.strip().splitlines() if x]
            _assert(len(_lines) == 3 and _lines[-1].startswith("⚠ 处置"),
                    f"「怎么办」必须排在最后一条：收起态只看得见最后两三行（实际={_lines}）")
            # 「原因」那一行必须由实测到的加载错误决定：漏模块、漏依赖、初始化抛异常
            # 三种情况界面症状完全一样，说死其中一种就等于让发包方照着错的那句去查。
            _reason = gui.MainWindow._lost_channels_reason
            with _patch(_sa, "ADAPTER_ERRORS", {
                    "riskbird": "ModuleNotFoundError: No module named 'riskbird_crawler_v2'",
                    "aiqicha": "ImportError: No module named 'cv2'"}):
                _r = _reason(("riskbird", "aiqicha"))
                _assert("缺了它们运行所需的文件" in _r and "采集器本身或它的依赖" in _r,
                        f"全是「找不到文件」这一类时才说漏文件，且把依赖也算进去"
                        f"（selenium/cv2 不是采集器文件）（实际={_r}）")
            with _patch(_sa, "ADAPTER_ERRORS", {
                    "riskbird": "AttributeError: 'NoneType' object has no attribute 'x'",
                    "aiqicha": "AttributeError: 缺模板图"}):
                _assert("本机加载这些采集器时报错了" in _reason(("riskbird", "aiqicha")),
                        "文件都在、初始化抛异常时说「加载报错」，不说漏文件")
            with _patch(_sa, "ADAPTER_ERRORS", {
                    "riskbird": "ModuleNotFoundError: No module named 'riskbird_crawler_v2'",
                    "aiqicha": "AttributeError: 缺模板图"}):
                _r2 = _reason(("riskbird", "aiqicha"))
                _assert("缺了、或本机加载不出" in _r2,
                        f"证据不止一种时退回覆盖得下的那句，不替某一路下结论（实际={_r2}）")
            with _patch(_sa, "ADAPTER_ERRORS", {}):
                _r = _reason(("riskbird",))
                _assert("缺了、或本机加载不出" in _r and "--doctor" in _r,
                        f"没有任何加载错误记录时不编原因，只把人指向自检报告（实际={_r}）")
            _detail = gui.MainWindow._lost_channels_detail(("riskbird", "aiqicha"))
            _assert("风鸟" in _detail and "爱企查" in _detail and "selenium 没装" in _detail,
                    f"逐路的错误原文进悬停说明（长句不进日志）（实际={_detail[:120]}）")
            _assert("check_packaging_manifest" in gui.MainWindow._lost_channels_detail(("riskbird",)),
                    "发包方自查命令挂在悬停说明里（长句不进日志，否则会把结论顶出可见区）")
            _assert_eq(gui.MainWindow._lost_channels_detail(()), "",
                       "一路不缺时连细节也不产（正常机器上不多一个字）")
            _assert_eq(gui.MainWindow._lost_channels_text((), ""), "",
                       "一路不缺时不产文案（正常机器上日志里一条也不会多）")
    finally:
        # 档位是进程级状态：不收回去，后面每一条用例都在 lite 里跑
        for _k, _v in ((_ed.ENV_VAR, _old_ed), (_ed.LEVEL_ENV_VAR, _old_lv)):
            if _v is None:
                os.environ.pop(_k, None)
            else:
                os.environ[_k] = _v
        _ed.reset_for_tests()


# ══════════════════════════════════════════════ O. 两条实机根因的判据
_WIN_ASM_MOD = None


def _win_assembler():
    """把 Windows 交付物装配脚本当模块载进来：它就是判据本体，这里不抄第二份。

    与 N 组的 `_manifest_gate()` 同一个道理——校验器与用例各写一套提取逻辑时，同一颗
    坑要踩两次；而 CI 与本机的制品校验器**必须**共用同一份装配判据（否则 CI 绿了、
    本机校验器却按另一套规矩说话，用户拿到的包由谁说了算就变成了运气）。
    """
    global _WIN_ASM_MOD
    if _WIN_ASM_MOD is None:
        path = _REPO / ".github" / "scripts" / "package_win_artifacts.py"
        spec = importlib.util.spec_from_file_location("_win_artifact_asm", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _WIN_ASM_MOD = mod
    return _WIN_ASM_MOD


# 合格的那一份卸载段只在下面这几行常量里定义一次
_UN_ASK = ('MessageBox MB_YESNO|MB_ICONQUESTION|MB_DEFBUTTON2 '
            '"是否连登录状态和缓存一起删？选「否」保留数据" IDNO keep_data')
_UN_ASK_NO_DEF = ('MessageBox MB_YESNO|MB_ICONQUESTION '
                  '"是否连登录状态和缓存一起删？选「否」保留数据" IDNO keep_data')
_UN_TAIL = ["uninstall_tail:", 'Delete "$DESKTOP\\x.lnk"', 'DeleteRegKey HKCU "Key"']


def _uninstall_nsi(runtime_dirs, ask=_UN_ASK, dirs=None, inst=True, goto=None,
                   keep=None, tail=_UN_TAIL, header=()):
    """按**分支**拼一份卸载段：默认拼出来的是合格那一份。

    每个参数只覆盖一条分支的一处，反例才能做到「只差一处」。整份 .nsi 抄九遍的话，
    改一处要改九处，迟早有一份反例悄悄变成正例——那比没有反例更坏，因为看上去验过了。
    """
    _dirs = list(runtime_dirs) if dirs is None else list(dirs)
    lines = list(header) + [ask]
    for d in _dirs:
        lines.append(f'RMDir /r "$INSTDIR\\{d}"')
    if inst:
        lines.append('RMDir /r "$INSTDIR"')
    lines += (list(goto) if goto is not None else ["Goto uninstall_tail"])
    lines.append("keep_data:")
    lines += (list(keep) if keep is not None else [
        'DetailPrint "已保留登录状态与采集数据：$INSTDIR"', 'RMDir "$INSTDIR"'])
    lines += list(tail)
    return ('Section "Uninstall"\n'
            + "\n".join("    " + ln for ln in lines) + "\nSectionEnd\n")


def _install_nsi(asm, drop=(), dup=None, extra=None, abort=True, goto=None,
                 target="copy_missing", checks=True, entry_before=False,
                 file_after=False, nameless=False, cont=False, stray=False,
                 nostep=False, sections=1, clean=None, clean_after=False,
                 clean_twice=False):
    """按**清单、次序、分支归属**拼一份安装段：默认那份合格，每个参数只坏一处。

    校验项直接从 `asm.nsi_payload_checks()` 取——它是 REQUIRED_INTERNAL 的投影。夹具
    自己抄一份文件名单的话，清单改了名这里会红成「装配失败」而不是「夹具过期」，
    那是把第 13 条要防的「两处各抄一份」在测试里再演一遍。
    """
    # 运行时目录的名字也从装配脚本取（INTERNAL_DIR）：夹具这里再硬写一遍 `_internal`，
    # 布局改名时会红成「夹具过期」而不是「判据与脚本一起跟着布局走」，同一个道理。
    # clean 传的是**不带引号**的路径（""＝这一句没写），坏在哪一处由参数一眼看出。
    clean = f"$INSTDIR\\{asm.INTERNAL_DIR}" if clean is None else clean
    items = [(p, w) for p, w in asm.nsi_payload_checks() if p not in drop]
    if extra:
        items.append((extra, "夹具自造的一项"))
    if dup is not None:
        items.append((dup, "同一项验第二遍"))
    entries = ['    CreateShortCut "$SMPROGRAMS\\x.lnk" "$INSTDIR\\x.exe"',
               '    WriteUninstaller "$INSTDIR\\uninstall.exe"']
    # 引号必须是 NSIS 的写法：判据认的是 `RMDir /r "路径"`，夹具拼成不带引号的话，
    # 「合格的树」会红成「没清旧运行时」，而那六个反例也会全部红成同一句指错方向的话
    # ——一轮里七条红指向同一个夹具笔误，这种红最费人。
    clean_line = f'    RMDir /r "{clean}"' if clean else None
    copy_in = ['    File /r "dist\\*.*"']
    if clean_line:
        # 默认排在 File **之前**：先清旧运行时，再铺这一版的（排在后面等于把刚拷好的删掉）
        copy_in = ([] if clean_after else [clean_line]) + copy_in \
                  + ([clean_line] if clean_after else [])
        if clean_twice:
            copy_in = copy_in + [clean_line]
    probe = []
    if checks:
        for path, _why in items:
            named = asm._payload_item(path) if not nameless else "装不全"
            probe.append(f'    StrCpy $7 "{named}"')
            probe.append(f'    IfFileExists "$INSTDIR\\{path}" 0 {target}')
    else:
        probe = ['    DetailPrint "拷完就直接登记"']
    if entry_before:                 # 控制面板先登记，校验才有机会补上
        probe, entries = entries + probe, []
    if file_after:                   # 拷完却不验这一批（校验排在 File 之前 = 验的是旧内容）
        probe, copy_in = probe + copy_in, []
    lines = list(copy_in) + probe + list(entries)
    if stray:      # 真脚本里本来就有一段「工具正在运行」的 Abort：失败分支没 Abort 时，
        # 安装段并非「一个 Abort 都没有」，判据必须按**归属到哪个标号**判而不是数 Abort
        lines = ['abort_locked:', '    Abort "工具正在运行，请先退出"', "    Goto skip_it"] \
            + lines + ["skip_it:"]
    if goto is not None:
        lines += ["    " + g for g in goto]
    else:
        lines.append("    Goto copy_ok")
    # 失败分支的标号名**不跟着 target 变**：target="copy_gone" 要的正是「跳进一个不存在的标号」
    lines.append("copy_missing:")
    if cont:
        lines.append('    MessageBox MB_OK|MB_ICONSTOP "程序文件没落地" \\')
        lines.append('        "请重新运行这个安装包"')
    else:
        lines.append('    MessageBox MB_OK|MB_ICONSTOP "程序文件没落地"')
        if not nostep:      # 中止安装的那一刻用户手里只剩这个弹窗，光说失败=让他猜下一步
            lines.insert(-1, '    StrCpy $6 "$6请关掉正在运行的工具后重新运行这个安装包"')
    if abort:
        lines.append('    Abort "安装未完成：关键文件没有落到安装目录"')
    lines.append("copy_ok:")
    lines.append('    DetailPrint "安装完成"')
    out = ('Section "Install"\n' + "\n".join(lines) + "\nSectionEnd\n")
    if sections > 1:      # 「安装段」只能有一个归属：分段之后校验条数就数不出来了
        out += 'Section "Helpers"\n    DetailPrint "又一段"\nSectionEnd\n'
    return out


def _stub_nsi_text() -> str:
    """合成产物树里那份 .nsi：既然叫「合格的树」，卸载分支与安装校验都得跟着合格。

    不在这里复制仓库里的真脚本——真文件合规与否由 O 组单独判；这里的职责只是给装配
    脚本一个「除了被测那一项以外全都合格」的输入，好让失败一定指向被测的那一项。
    """
    asm = _win_assembler()
    return ('Unicode true\nName "B2B"\n' + _install_nsi(asm)
            + _uninstall_nsi(asm.RUNTIME_DIRS))


def _fake_dist(root: Path, drop=(), stale_onefile=False, runtime_dirs=(), nsi=None):
    """搭一棵**合成**的 onedir 产物树：指定项可以缺席，用来验校验器自己判得对不对。"""
    app = root / "dist" / _win_assembler().APP_DIR_NAME
    internal = app / "_internal"
    internal.mkdir(parents=True, exist_ok=True)
    (app / _win_assembler().EXE_NAME).write_text("MZ", encoding="utf-8")
    asm = _win_assembler()
    for rel in asm.REQUIRED_INTERNAL:
        if rel in drop:
            continue
        target = internal / rel
        if asm.is_dir_entry(rel):
            # 目录型条目（drivers / enf_clean）必须**里面有文件**才算落地：装配判据
            # 认「空目录 = 一个驱动都没下载成功」，夹具铺一个空目录就是在伪造正例
            (target / "keep.txt").parent.mkdir(parents=True, exist_ok=True)
            (target / "keep.txt").write_text("x", encoding="utf-8")
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("x", encoding="utf-8")
    # installer/ 那两份也必须铺上：装配脚本在目录布局之外还校验它们的编码，缺席时
    # 校验不过就**不产出 zip**，于是「合成树装配通过」会红成一条看着像产物坏了的结果
    # ——那其实是夹具自己没铺全。这里写带 BOM 的合成桩而不是复制仓库里的真文件：
    # 夹具的职责只是「搭一棵合格的树」，真文件合规与否由下面第 3 段单独判（同一件事
    # 不留两处判据，否则真文件哪天改了名，这里会跟着红成一条莫名其妙的装配失败）。
    for rel in (asm.NSI_PATH, asm.GUIDE_SRC):
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(asm.BOM + (
            "合成桩\n" if rel == asm.GUIDE_SRC
            else (_stub_nsi_text() if nsi is None else nsi)).encode("utf-8"))
    if stale_onefile:
        (root / "dist" / asm.EXE_NAME).write_text("MZ", encoding="utf-8")
    for name in runtime_dirs:
        (app / name / "history.json").parent.mkdir(parents=True, exist_ok=True)
        (app / name / "history.json").write_text("{}", encoding="utf-8")
    return app


@contextlib.contextmanager
def _hush():
    """收下被检对象自己往屏幕上的播报。

    反例段落要验的是**判据**（problems 列表里有没有那一条），而不是把校验器已经说过
    的话再刷一遍：它的 ❌ 混在用例结果里，读日志的人会以为用例失败了，而真正失败的
    那一条反而被淹掉——这类「夹具的动静冒充结论」在 K 组已经红过一次。
    """
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        yield buf


def test_windows_onedir_delivery():
    print("\n═══ O. Windows 交付形态：onefile 绝迹、装配判据共用一份、装进用户机器也逐条验 ═══")
    asm = _win_assembler()
    gate = _manifest_gate()

    # 1) 三个入口都不许再出现 --onefile（注释里提它是写给维护者看的，不算）
    wf = _REPO / ".github" / "workflows"
    win = (wf / "build-b2bcollect.yml").read_text(encoding="utf-8")
    mac = (wf / "build-b2bcollect-mac.yml").read_text(encoding="utf-8")
    local = (_REPO / "debug" / "build_b2bcollect_mac_local.sh").read_text(encoding="utf-8")
    for label, text in (("Windows workflow", win), ("Mac workflow", mac), ("本机脚本", local)):
        _assert("--onefile" not in gate._strip_comments(text),
                f"{label} 的构建命令里没有 --onefile（onefile 会原样复发 "
                "base_library.zip 找不到 + Cookie 每次归零）")
    _assert("--name B2bCollectTool" in gate._strip_comments(win),
            "Windows 仍然产出同一个名字（改的是形态，不是品牌）")

    # 2) 装配判据自己判得对不对：正例全绿 + 四个反例各自点名
    root = Path(tempfile.mkdtemp(prefix="windist_"))
    _fake_dist(root)
    problems = []
    with _hush():
        exe, internal = asm.check_layout(root, problems)
    _assert_eq(problems, [], f"合格的 onedir 布局一条也不报（实际={problems}）")
    _assert(exe is not None and internal is not None, "正例返回 exe 与 _internal 两个路径")

    bad = Path(tempfile.mkdtemp(prefix="windist_bad_"))
    _fake_dist(bad, drop=("riskbird_build/runtime_paths.py",))
    got = []
    with _hush():
        asm.check_layout(bad, got)
    _assert(any("runtime_paths.py" in p for p in got),
            f"_internal 少了 runtime_paths.py 必须点名（缺它开机即崩）：{got}")

    stale = Path(tempfile.mkdtemp(prefix="windist_stale_"))
    _fake_dist(stale, stale_onefile=True)
    got = []
    with _hush():
        asm.check_layout(stale, got)
    _assert(any("单文件" in p and "原样复发" in p for p in got),
            f"制品里混着旧的单文件 exe 必须判红（用户双击它就回到本轮修好的故障）：{got}")

    noapp = Path(tempfile.mkdtemp(prefix="windist_none_"))
    got = []
    with _hush():
        asm.check_layout(noapp, got)
    _assert(any("--onefile 又回来了" in p for p in got),
            f"连 onedir 目录都没有时，直接把最可能的原因说成 onefile 复发：{got}")

    nointernal = Path(tempfile.mkdtemp(prefix="windist_shell_"))
    (nointernal / "dist" / asm.APP_DIR_NAME).mkdir(parents=True)
    (nointernal / "dist" / asm.APP_DIR_NAME / asm.EXE_NAME).write_text("MZ", encoding="utf-8")
    got = []
    with _hush():
        asm.check_layout(nointernal, got)
    _assert(any("空壳" in p for p in got), f"只有 exe、没有 _internal 时判红：{got}")

    # 3) UTF-8 BOM：仓库里那两份真实文件必须已经合规，缺 BOM 的反例必须红
    real = []
    with _hush():
        asm.check_bom(_REPO / "b2bcollect_build" / asm.NSI_PATH, "NSIS", real)
        asm.check_bom(_REPO / "b2bcollect_build" / asm.GUIDE_SRC, "说明", real)
    _assert_eq(real, [], f"仓库里的安装脚本与使用说明都是 UTF-8 with BOM（实际={real}）")
    nobom = Path(tempfile.mkdtemp(prefix="nobom_")) / "x.nsi"
    nobom.write_text("中文", encoding="utf-8")
    got = []
    with _hush():
        asm.check_bom(nobom, "反例", got)
    _assert(any("BOM" in p for p in got),
            "没有 BOM 时必须判红：makensis 会按系统代码页解释，中文全乱码而构建照样成功")

    # 4) 装配次序：校验不过就到此为止；运行期目录绝不进 zip；zip 顶层是一个目录
    dirty = Path(tempfile.mkdtemp(prefix="windist_dirty_"))
    _fake_dist(dirty, runtime_dirs=asm.RUNTIME_DIRS)
    _rc = subprocess.run([sys.executable, str(_REPO / ".github" / "scripts" /
                            "package_win_artifacts.py"), "--root", str(dirty)],
                         capture_output=True, text=True, cwd=str(_REPO))
    # 装配脚本把「为什么红」打到 stdout（❌ 那几行），stderr 反而是空的：只贴 stderr
    # 的话，红一次就等于让人重新查一次——本轮第 7 条判据接上之后第一次红就是这样，
    # 屏幕上只有一句「期望 0，实际 1」，真原因在没打印出来的那几行里。
    _assert_eq(_rc.returncode, 0,
               f"合成产物树装配通过（stderr={_rc.stderr[-200:]} stdout尾部={_rc.stdout[-400:]}）")
    _assert(not any((dirty / "dist" / asm.APP_DIR_NAME / n).exists()
                    for n in asm.RUNTIME_DIRS),
            "runner 上 --acl-doctor 跑出来的状态目录被剔掉了（带着别人机器的登录状态"
            "和任务历史安装包不能发）")
    zpath = dirty / asm.PORTABLE_NAME
    # 这里必须是「一条干净的判据」而不是一个 traceback：上一条判据一红，zip 就不存在，
    # 拿 ZipFile 直接打开会让整节炸在栈里——真原因（装配失败）被埋在最后一行，读到的
    # 人只会去查 zip，而 zip 从来不是问题。取不到 zip 就交一个空清单下去，后面几条各自
    # 报自己的红，栈里那一条不再有「唯一线索却是错线索」的威力。
    _assert(zpath.is_file(), f"校验通过才产出绿色版 zip（{zpath} 不存在）")
    names = zipfile.ZipFile(zpath).namelist() if zpath.is_file() else []
    # 这条判据盯的是 **zip 里的字节**，不是安装目录里的文件名：绿色版根上那份说明
    # 用的是 ASCII 条目名（GUIDE_ZIP_ENTRY，由 GUIDE_SRC 派生），`使用说明.txt` 只活在
    # NSIS 的 /oname 上。以前这里抄的是 GUIDE_DEST，装配脚本改条目名以后它红了——
    # 红的不是产物坏了，是判据跟着旧形状没改；留着一个字面量在测试里，下一次改动
    # 还是要靠人记得同步。名字与「不许再出现非 ASCII 条目」一起钉在这里：那正是
    # 本轮交付 zip 坏掉的实际原因，值得两处各留一条（另一处是 G 节的字节手术）。
    _assert(all(n.isascii() for n in names),
            f"装配产出的 zip 条目名全 ASCII（非 ASCII={[n for n in names if not n.isascii()]}）")
    _assert(all(n.startswith(asm.APP_DIR_NAME + "/") or n == asm.GUIDE_ZIP_ENTRY
                for n in names),
            f"zip 顶层只有 {asm.APP_DIR_NAME}/ 与根上的使用说明（解压出来是一堆散文件的话，"
            f"用户会把它摊到桌面上）（实际={sorted({n.split('/')[0] for n in names})}）")
    _assert(asm.GUIDE_ZIP_ENTRY in names, "绿色版 zip 根上有使用说明（第一眼就看到）")
    _assert(asm.GUIDE_DEST not in names,
            f"根上的条目名不许换回安装目录那个中文名 {asm.GUIDE_DEST!r}（本轮红在用户机器上的"
            f"就是这一形状：解包工具对非 ASCII 名字的处置由中央目录一个字节决定）")
    _assert(not any(f"/{n}/" in f"/{x}" for x in names for n in asm.RUNTIME_DIRS),
            "运行期目录连一个条目都没进 zip")

    broken = Path(tempfile.mkdtemp(prefix="windist_broken_"))
    _fake_dist(broken, drop=("config.json",))
    _rc2 = subprocess.run([sys.executable, str(_REPO / ".github" / "scripts" /
                             "package_win_artifacts.py"), "--root", str(broken)],
                          capture_output=True, text=True, cwd=str(_REPO))
    _assert(_rc2.returncode != 0 and not (broken / asm.PORTABLE_NAME).exists(),
            "随包凭证缺失时非零退出**且不产出 zip**（否则又是一个 CI 绿、用户拿坏包）")

    # 5) CI 与本机的制品校验器共用这份判据，不抄第二份
    fetch = (_REPO / "debug" / "fetch_b2bcollect_artifacts.sh").read_text(encoding="utf-8")
    _assert("package_win_artifacts" in fetch and "check_layout" in fetch,
            "制品校验器 import 装配脚本复用 check_layout（两套规矩迟早对不上）")
    _assert("B2bCollectToolSetup" in fetch,
            "制品校验器要求安装包存在（只有绿色版目录 zip 等于交付形态没落地）")
    _assert("makensis" in win and "Package Windows artifacts" in win,
            "Windows workflow 里既有装配步也有 NSIS 安装包步")
    _assert(asm.SETUP_NAME in win and asm.PORTABLE_NAME in win,
            "安装包与绿色版两份产物都进上传清单")

    # 6) 交付文档与安装脚本、界面话术必须说同一件事：说明书写下的承诺没有代码撑着时，
    #    漂移没有任何运行期症状，只有用户在第一次安装时被问住（本轮说明书改过三处，
    #    每处都对应一个真实存在/不存在的目录，逐条对拍比读一遍可靠）。
    nsi = (_REPO / "b2bcollect_build" / asm.NSI_PATH).read_text(encoding="utf-8-sig")
    guide = (_REPO / "b2bcollect_build" / asm.GUIDE_SRC).read_text(encoding="utf-8-sig")
    _assert("RequestExecutionLevel user" in nsi
            and "RequestExecutionLevel admin" not in nsi,
            "安装脚本是每用户安装：说明书那句「全程不会弹管理员权限窗口」全靠这一行撑着")
    _assert("$LOCALAPPDATA\\Programs" in nsi and "管理员权限" in guide,
            "默认安装目录与说明书第一节写的是同一个位置（装进 Program Files 的话 "
            "Cookie 与缓存写不进去，那节讲的正是这个因果）")
    for d in ("b2bcollect_data", "riskbird_data", "b2bcollect_state"):
        _assert(d in guide, f"说明书第三节逐个写明 {d} 存的是什么（用户找日志不必猜）")
        _assert(d in asm.RUNTIME_DIRS,
                f"{d} 同时在装配剔除名单里（说明书承诺的目录与发包前剔的目录必须是同一批，"
                "否则 CI 上跑出自检报告就会随包发出去）")
        _assert(f'"$INSTDIR\\{d}"' in nsi,
                f"卸载脚本的「连数据一起删」清单覆盖 {d}（用户选了全删却还剩一个空目录，"
                "这种漏法没有症状，只有下次重装时才被发现）")
    _assert("aiqicha_data" not in guide,
            "说明书不列 aiqicha_data：爱企查与风鸟共用 riskbird_data，"
            "文档不许列出程序根本不会创建的目录（RUNTIME_DIRS 里那一项是留着防身的）")
    import b2bcollect_gui as _gui
    _assert(_gui.FATAL_LOG_NAME in guide,
            f"说明书里的崩溃日志名与界面落盘用的是同一个名字（{_gui.FATAL_LOG_NAME}）")
    _assert("App Translocation" in guide and "应用程序" in guide,
            "说明书 Mac 那节仍在讲只读副本这件事（界面横幅的出口要有文字版可对）")

    # 7) 安装脚本里每一个 $变量 都必须是 NSIS 真认得的那个。拼错时 makensis 只给一句
    #    warning 6000「unknown variable …ignoring」，然后把它当空串：安装包照样出、
    #    卸载照样成功，而那句「数据还留在个人目录」永远不会打印——2026-11-12 云端日志
    #    里躺着的正是这样一条静默 warning（写成了 $HOME，NSIS 的个人目录是 $PROFILE）。
    #    这一类和「绿得没意义」同源：编译器不报错的缺陷只能靠判据抓，靠构建结果抓不到。
    #
    #    白名单必须**说清来源**，不然会退化成两种坏结果：内置变量漏登记（$SMPROGRAMS
    #    那种，判据报假红，门会被当噪音关掉），或把「其实在别处定义」的名字也报出来
    #    （${B2B_ROOT} 由命令行 /D 注入、${GetSize} 来自 !include 的库）。所以四路来源
    #    分开登记、分开解释：内置变量表、本文件 !define/Var、命令行注入、已登记的库宏。
    _NSIS_VARS = {
        # 路径与进程类
        "INSTDIR", "EXEDIR", "CMDLINE", "NOW", "OPTIONS", "PLUGINSDIR", "OUTDFILE",
        "WINDIR", "WINDOWS", "SYSDIR", "SYSTEM", "SYSTEM32", "SYSTEMARM64",
        "PROGRAMFILES", "PROGRAMFILES32", "PROGRAMFILES64",
        "COMMONFILES", "COMMONFILES32", "COMMONFILES64", "PROGRAMDATA",
        # 个人与公共目录类（个人目录只有 $PROFILE——NSIS 没有 $HOME）
        "PROFILE", "DESKTOP", "APPDATA", "LOCALAPPDATA", "COMMONAPPDATA",
        "FAVORITES", "COMMONFAVORITES", "HISTORY", "RECENT", "SENDTO", "STARTUP",
        "LOCALSTARTUP", "NETHOOD", "TEMPLATES", "COMMONTEMPLATES",
        "DOCUMENTS", "COMMONDOCUMENTS", "MUSIC", "COMMONMUSIC", "PICTURES",
        "COMMONPICTURES", "VIDEOS", "COMMONVIDEOS", "RESOURCEDATA",
        "PROGRAMS", "SMPROGRAMS", "COMMONPROGRAMS", "STARTMENU", "COMMONSTARTMENU",
        # 界面、字体、注册表句柄与寄存器变量
        "HWND", "HWNDPARENT", "CONTROL", "FONT", "OUTDFONT", "LANGUAGE",
        "ADMIN", "POWERUSER", "GUEST",
    } | {f"R{i}" for i in range(10)}

    # !include 的库里定义的宏：本机没有 NSIS 头文件可读，「哪个库提供哪些宏」就必须在案。
    # 用了没登记的宏会红，措辞直接说下一步（补这张表）——宁可响一声让人补表，也不能
    # 反过来放开：${} 一律放行等于任何拼错的宏名都不判，那这条门就是空的。
    _NSIS_LIB_MACROS = {
        "FileFunc.nsh": {"GetSize", "GetFileTime", "GetDrives", "GetParameters"},
        "LogicLib.nsh": {"If", "ElseIf", "Else", "EndIf", "Unless", "AndIf", "OrIf",
                         "While", "Do", "Loop", "Select", "Case", "Switch"},
        "WinVer.nsh": {"AtLeastWin7", "AtLeastWin10", "IsWin10", "IsWow64"},
        "MUI2.nsh": set(),          # MUI 走 !insertmacro，正文里不用 ${}
    }

    # 只能由命令行注入、且 CI 必须真的注入的名字。漏一个，.nsi 不会报错，只会退到
    # !ifndef 里的兜底值（版本 0.0 / 1.0.0.0、!cd 不生效）——那是另一份「编得出来、
    # 装得上、版本信息与字节对不上」的安装包，和本轮 VIProductVersion 同一族。
    _NSIS_CI_DEFINES = {"B2B_ROOT": "b2bcollect_build 的绝对路径（!cd 的基准）",
                        "APP_VERSION": "显示版本号 1.<build>（Name 里那一段；这一格由随包构建戳"
                                       "经 tool_version 给出，不是 shell 自己拼出来的）",
                        "VER4": "文件属性四段版本号（makensis 只认 X.X.X.X）"}

    def _nsi_var_problems(text: str, cmdline=frozenset()) -> list:
        """$NAME 要在内置变量表里；${NAME} 要来自本文件 !define、命令行 /D 或已登记的库宏。"""
        defined = set(re.findall(r"!define\s+(\w+)", text)) | set(
            re.findall(r"^\s*Var\s+(?:/\w+\s+)?(\w+)", text, re.M))
        lib_macros, unknown_libs = set(), []
        for inc in re.findall(r'!include\s+"([^"]+)"', text):
            key = inc.split("/")[-1]
            if key in _NSIS_LIB_MACROS:
                lib_macros |= _NSIS_LIB_MACROS[key]
            else:
                unknown_libs.append(key)
        known_braces = defined | set(cmdline) | lib_macros
        lib_hint = "、".join(sorted(_NSIS_LIB_MACROS))
        out = []
        for ln, raw in enumerate(text.splitlines(), 1):
            if raw.strip().startswith((";", "#")):
                continue                       # 注释里写的是「曾经错成什么」，不是要执行的
            for m in re.finditer(r"\$([A-Za-z_][A-Za-z0-9_]*)", raw):
                name = m.group(1)
                if name in _NSIS_VARS or name in defined:
                    continue
                out.append(f"第 {ln} 行 ${name}：NSIS 认不出这个变量"
                           f"（编译器只报 warning 6000 并当空串，那句话永远打印不出来）")
            for m in re.finditer(r"\$\{(\w+)\}", raw):
                if m.group(1) in known_braces:
                    continue
                extra = (f"；本文件还 !include 了未登记的库 {'/'.join(unknown_libs)}"
                         f"（它的宏要补进 _NSIS_LIB_MACROS 才认）" if unknown_libs else "")
                out.append(f"第 {ln} 行 ${{{m.group(1)}}}：既不在本文件 !define/Var 里，"
                           f"也不在命令行 /D 注入的名字里，也不在已登记的库宏（{lib_hint}）"
                           f"里{extra}——宏名拼错时 NSIS 不展开它，只给一句 warning")
        return out

    _nsi_cmdline = set(re.findall(r"/D([A-Za-z_]\w*)=", win))
    _assert_eq(_nsi_var_problems(nsi, _nsi_cmdline), [],
               "安装脚本里没有拼错的 NSIS 变量（个人目录是 $PROFILE，没有 $HOME）")
    _assert_eq(_nsi_var_problems('IfFileExists "$HOME\\.b2bcollect\\*.*" 0 done\n'
                                 'DetailPrint "$NOPE_DIR"\nDetailPrint "${NOPE_MACRO}"\n',
                                 {"B2B_ROOT"}),
               ["第 1 行 $HOME：NSIS 认不出这个变量（编译器只报 warning 6000 并当空串，"
                "那句话永远打印不出来）",
                "第 2 行 $NOPE_DIR：NSIS 认不出这个变量（编译器只报 warning 6000 并当空串，"
                "那句话永远打印不出来）",
                "第 3 行 ${NOPE_MACRO}：既不在本文件 !define/Var 里，也不在命令行 /D 注入的"
                "名字里，也不在已登记的库宏（FileFunc.nsh、LogicLib.nsh、MUI2.nsh、"
                "WinVer.nsh）里——宏名拼错时 NSIS 不展开它，只给一句 warning"],
               "反向对照：本轮那种 $HOME 与拼错的宏名都必须被抓出来（抓不住=这条门是空的）")
    _assert_eq(_nsi_var_problems('!include "FileFunc.nsh"\n!define FOO "x"\n'
                                 'Var /GLOBAL Bar\nDetailPrint "${FOO} ${GetSize}" '
                                 '"$INSTDIR $PROFILE $SMPROGRAMS $R3"\n',
                                 {"BAR2"}), [],
               "反向对照：内置变量、本文件定义过的名字、命令行注入、已登记的库宏都不误报"
               "（误报的门会被当噪音关掉）")
    _assert_eq(_nsi_var_problems('; 旧写法是 "$HOME"，NSIS 认不出来\n'), [],
               "反向对照：注释里的错写法不算（否则解释 bug 的那行自己被当成 bug）")
    _odd = _nsi_var_problems('!include "WeirdLib.nsh"\nDetailPrint "${Whatever}"\n')
    _assert(len(_odd) == 1 and "未登记的库 WeirdLib.nsh" in _odd[0],
            "反向对照：没登记的库里的宏必须红，并且要说清去补哪张表"
            f"（放行=任何 ${{}} 都不判）（实际={_odd}）")

    for _dn, _why in sorted(_NSIS_CI_DEFINES.items()):
        _assert(f"/D{_dn}=" in win,
                f"workflow 的 makensis 命令必须注入 {_dn}（{_why}；少注入一个就退到 .nsi 的"
                f" !ifndef 兜底值，那种安装包编得出来、装得上，只有版本信息与字节对不上）")
        _assert(f"${{{_dn}}}" in nsi,
                f".nsi 必须真的用到 {_dn}（不用却还注入=上面那条判据在空转）")

    # 8) 个人目录这件事，脚本与说明书还要说的是同一个地方：运行期退回的是
    #    Path.home()/.b2bcollect（NSIS 侧就是 $PROFILE），说明书指错目录的话，用户
    #    卸载后去找日志会一无所获。
    _assert("$PROFILE" in nsi, "卸载脚本用 $PROFILE 指个人目录（与运行期 Path.home() 同一处）")
    _assert(".b2bcollect" in nsi and ".b2bcollect" in guide,
            "个人目录那一级的名字在安装脚本与说明书里是同一个（两边各写一份迟早对不上）")

    # 9) 卸载段的「保留数据」分支必须**真的保留数据**。这一条不是防想象中的错：本轮读
    #    这份脚本时读到的就是旧写法——两条选项最后都走到同一句 RMDir /r "$INSTDIR"，
    #    于是问句里那句「选『否』（推荐）：保留 b2bcollect_data / b2bcollect_state /
    #    riskbird_data」是空的，选保留也照样被递归删掉。编译器不报（语法完全合法）、
    #    卸载退出码 0、DetailPrint 里那句「已保留」也照样打印，症状要等下次重装、
    #    登录状态全没了才浮现——而那时没人会怀疑到卸载脚本少了一个分支跳转。
    #    另一处同类：MessageBox 不带 MB_DEFBUTTON2 时默认焦点在**第一个按钮**（=「是，
    #    连数据一起删」），一路回车下去就把数据删了，和说明书那句「默认保留」正好相反。
    #    判据实现在 .github/scripts/package_win_artifacts.py（CI 在 makensis 之前跑），
    #    这里钉住它的**分支归属**判定与接线，不留第二份规矩。
    _un = []
    with _hush():
        asm.check_installer_uninstall(nsi, _un)
    _assert_eq(_un, [], "真安装脚本的卸载分支合格：保留分支不递归删、全删分支逐个点名、"
                        "默认焦点在「否」、两条分支各有归宿")
    _assert('Goto uninstall_tail' in nsi and 'RMDir "$INSTDIR"' in nsi,
            "两条分支各走各的：全删分支显式跳转，保留分支只用不带 /r 的 RMDir"
            "（不带 /r 才会在目录还剩用户数据时停下）")
    _assert("MB_DEFBUTTON2" in nsi,
            "问句的默认焦点落在「否」（=保留），说明书那句「默认保留」才有代码撑着")

    # 反例逐条只差一处（见 _uninstall_nsi 的参数分工）：每个参数都判**具体那条消息**，
    # 只要求条数与关键词同时成立——「有条红」不等于「红在对的地方」。
    _RD = asm.RUNTIME_DIRS
    _cases = [
        ("旧写法：两条分支汇到同一句递归删除",
         _uninstall_nsi(_RD, ask=_UN_ASK_NO_DEF, inst=False, goto=[],
                        keep=['RMDir /r "$INSTDIR"']),
         ["递归删除 $INSTDIR", "MB_DEFBUTTON2", "没有 Goto"]),
        ("保留分支里换成 Delete /r 也一样是删数据",
         _uninstall_nsi(_RD, keep=['Delete /r "$INSTDIR\\riskbird_data"']),
         ["递归删除 $INSTDIR\\riskbird_data", "没有 DetailPrint"]),
        ("全删分支少列一个数据目录",
         _uninstall_nsi(_RD, dirs=tuple(_RD[1:])),
         [f'没有 RMDir /r "$INSTDIR\\{_RD[0]}"']),
        ("全删分支末尾不跳转，直接落进保留分支",
         _uninstall_nsi(_RD, goto=[]),
         ["没有 Goto"]),
        ("跳转目标落进保留分支内部（往回跳）",
         _uninstall_nsi(_RD, goto=["Goto keep_data"]),
         ["跳转目标必须落在两条"]),
        ("跳转目标压根没有这个标号",
         _uninstall_nsi(_RD, goto=["Goto uninstall_end"]),
         ["在卸载段里不存在"]),
        ("根本没问用户就删数据",
         _uninstall_nsi(_RD, ask='DetailPrint "直接删"'),
         ["没有任何「要不要连登录状态和缓存一起删」的问句"]),
        ("问了两遍，分支归属不再唯一",
         _uninstall_nsi(_RD, ask=_UN_ASK, header=[_UN_ASK_NO_DEF.replace("IDNO keep_data",
                                                                        "IDNO keep_other")]),
         ["条带 IDNO 跳转的问句"]),
        ("IDNO 跳到不存在的标号",
         _uninstall_nsi(_RD, ask=_UN_ASK.replace("keep_data", "keep_gone")),
         ["没有这个标号 keep_gone"]),
        ("卸载段整块没了",
         'Section "Install"\n    File x\nSectionEnd\n',
         ['没有 Section "Uninstall"']),
    ]
    for _label, _text, _needles in _cases:
        _p = []
        with _hush():
            asm.check_installer_uninstall(_text, _p)
        _assert(_p, f"反向对照：{_label} 必须报红（报不出来=这条门在这一类改动上是空的）")
        for _n in _needles:
            _assert(any(_n in x for x in _p),
                    f"反向对照：{_label} 要红在正确的地方——消息里得出现「{_n}」（实际={_p}）")

    # 注释里写「旧写法长什么样」不该被判成违规：解释这个 bug 的那一行本身就带着坏写法，
    # 判据读的是要执行的字节。这一条不设防的话，第一条诚实的注释会让整道门永远响。
    _p = []
    with _hush():
        asm.check_installer_uninstall(_uninstall_nsi(
            _RD, header=['; 旧写法是 RMDir /r "$INSTDIR"，两个选项都走到同一句递归删除']), _p)
    _assert_eq(_p, [], "反向对照：注释里的旧写法不算违规（真脚本里就留着这样一行解释）")

    # 接线：判据不能只是「一个函数存在」，CI 那一步真会因为它而不发包。行为验证——
    # 把合成产物树里那份 .nsi 换成旧写法，装配脚本必须非零退出**且不产出 zip**。
    _bad_root = Path(tempfile.mkdtemp(prefix="windist_uninstall_"))
    _fake_dist(_bad_root)
    (_bad_root / asm.NSI_PATH).write_bytes(
        asm.BOM + _uninstall_nsi(_RD, ask=_UN_ASK_NO_DEF, inst=False, goto=[],
                                 keep=['RMDir /r "$INSTDIR"']).encode("utf-8"))
    _rc = subprocess.run([sys.executable, str(_REPO / ".github" / "scripts" /
                             "package_win_artifacts.py"), "--root", str(_bad_root)],
                         capture_output=True, text=True, encoding="utf-8",
                         errors="replace", cwd=str(_REPO))
    _assert(_rc.returncode != 0 and "递归删除 $INSTDIR" in (_rc.stdout + _rc.stderr),
            f"反向对照：旧写法在安装包编译之前就被装配那一步拦下，消息说清是哪一句"
            f"（rc={_rc.returncode} out尾部={_rc.stdout[-200:]!r}）")
    _assert(not (_bad_root / asm.PORTABLE_NAME).exists(),
            "拦下来时连绿色版 zip 都不产出：产物有问题还继续装配，正是「CI 绿、用户拿坏包」的形状")
    _good_root = Path(tempfile.mkdtemp(prefix="windist_uninstall_ok_"))
    _fake_dist(_good_root)
    _rc2 = subprocess.run([sys.executable, str(_REPO / ".github" / "scripts" /
                              "package_win_artifacts.py"), "--root", str(_good_root)],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", cwd=str(_REPO))
    _assert_eq(_rc2.returncode, 0,
               f"同一棵树只把 .nsi 换成合格那份就必须通过（不通过=这条门一直响）"
               f"（out尾部={_rc2.stdout[-200:]!r}）")

    # 10) 安装段必须**逐条验关键文件真的落进了安装目录**。CI 上一直只验 dist 树
    #     （check_layout），从来没有一处验「makensis 把这些文件放进了用户机器」；而安装
    #     脚本用的是 `SetOverwrite try`——目标被占用就跳过、**不算失败**，于是新 exe 配
    #     旧运行时这种拼起来的版本照样走到「完成」页。这一类和前九类同源：装了一半不报
    #     错，只有用户点开才发现少一路功能，而那时没人会怀疑安装向导自己报的「成功」。
    #     判据实现在 .github/scripts/package_win_artifacts.py（CI 在 makensis 之前跑），
    #     这里钉住它的清单来源、次序判定与接线，不留第二份规矩。
    _pl = []
    with _hush():
        asm.check_installer_payload(nsi, _pl)
    _assert_eq(_pl, [], "真安装脚本的拷贝后校验合格：装配清单里每一项都逐条验过、"
                        "失败分支落到 Abort、快捷方式与注册表排在校验之后")
    _payload = asm.nsi_payload_checks()
    _assert_eq(len(_payload), len(asm.REQUIRED_INTERNAL) + 2,
               "校验清单 = 装配清单 + 根上的 exe 与使用说明（夹具的条数就是从这份投影来的，"
               "自己抄一份文件名的话清单改名时这里会红成「装配失败」而不是「夹具过期」）")
    _expand = asm._nsi_expander(nsi)
    _expanded = "\n".join(_expand(ln) for ln in nsi.splitlines())
    for _p, _why in _payload:
        # 宏要**展开后**再对：真脚本写的是 "$INSTDIR\${APP_EXE}"，按字面比会报一条指错
        # 方向的假红（清单与脚本其实是对得上的），而判据本身走的就是展开后的字节。
        _assert(f'IfFileExists "$INSTDIR\\{_p}" 0 copy_missing' in _expanded,
                f"清单里的 {_p} 必须逐条出现在安装段的校验里（{_why}）")
    _assert_eq(_expanded.count(" 0 copy_missing"), len(_payload),
               "校验条数与清单条数一条不差（多一条=清单外硬编码，少一条=静默少验一项）")
    _assert(nsi.index("copy_missing:") > nsi.index("0 copy_missing"),
            "失败分支排在校验之后：否则每次安装都会走进 Abort")
    _assert(nsi.index("copy_ok:") < nsi.index("CreateShortCut"),
            "快捷方式排在成功标号之后：装不全的包不许在控制面板里留条目")
    # 升级那一条：先清旧运行时、再铺新的，而且**安装段**里只允许这一处递归删除。
    # 刻意只在安装段里数：卸载段本来就有五处 RMDir /r（那是「连数据一起删」那条分支），
    # 拿全文当安装段数会数出 6 条并红成「有人在动别的东西」——判据读的是执行顺序，
    # 归属必须先切开，否则报的是一条指错方向的话。目录名取自 asm.INTERNAL_DIR 而不是
    # 夹具里再硬写一遍：布局改名时这条断言与判据要一起跟着走，红的才真是布局变了。
    _rt = f'$INSTDIR\\{asm.INTERNAL_DIR}'
    _clean_k = f'RMDir /r "{_rt}"'
    _ins_code = [_expand(nsi.splitlines()[k])
                 for _t, _b, _s in asm._nsis_section_bodies(nsi.splitlines())
                 if _t != "Uninstall" for k in _b
                 if not asm.NSIS_COMMENT.match(nsi.splitlines()[k])]
    _hits = [i for i, ln in enumerate(_ins_code) if _clean_k in ln]
    _assert_eq(len(_hits), 1,
               f"真安装脚本必须恰好一句 {_clean_k}：少一句=升级留下新旧混着的一棵树，"
               f"多一句=同一件事抄了两遍（改动时只会改到一处）")
    _i_clean = _hits[0] if _hits else len(_ins_code)
    _i_file = min((i for i, ln in enumerate(_ins_code) if ln.lstrip().startswith("File /r")),
                  default=len(_ins_code))
    _assert(_i_clean < _i_file,
            "清旧运行时必须排在 File 之前：排在后面就是把这一版刚铺好的运行时删掉，"
            "下面那段逐条校验会红在「文件没落地」上而真原因是这一行的次序")
    _assert_eq(len([ln for ln in _ins_code if "RMDir /r" in ln]), 1,
               "安装段里唯一的递归删除就是运行时目录那一句：多一句就说明有人在动安装目录里的"
               "别的东西（用户的数据也在那里面）")

    # 反例逐条只差一处（见 _install_nsi 的参数分工）：既要求报红，也要求红在**那一条**消息上
    _cases_p = [
        ("少验一项（清单里有、脚本没验）",
         dict(drop=("_internal\\config.json",)), ["没有校验 $INSTDIR\\_internal\\config.json"]),
        ("同一项验两遍（说明有一段是从别处抄来的）",
         dict(dup="_internal\\config.json"), ["被校验了 2 次"]),
        ("验了清单里没有的路径（两处各抄一份迟早对不上）",
         dict(extra="_internal\\somewhere_else.py"), ["校验了装配清单里没有的路径"]),
        ("失败分支只弹窗不 Abort（整个安装段一个 Abort 都没有）",
         dict(abort=False), ["没有任何带 Abort 的失败分支"]),
        # 这一条和上一条只差「段里另有一句 Abort」：真脚本里那句「工具正在运行」就在那儿，
        # 所以数 Abort 的判据会以为「有中止分支就够了」，而校验跳进去的那条路其实一路走进
        # copy_ok。判据必须按**归属**判，报的也必须是「这条路掉进了成功标号」而不是「没有
        # Abort」——后者会把人带去补一句 Abort，补完照样走进成功分支。
        ("失败分支只弹窗不 Abort（同段里另有「工具正在运行」那句 Abort）",
         dict(abort=False, stray=True),
         ["这条路最后掉进了成功标号 copy_ok", "任何一条校验把失败分支接到 Abort"]),
        ("校验全过却忘了 Goto 跳过失败分支（每次安装都中止）",
         dict(goto=[]), ["没有 Goto 跳到成功分支"]),
        ("Goto 跳错标号，直接跳进失败分支（同上，更隐蔽）",
         dict(goto=["Goto copy_missing"]), ["没有 Goto 跳到成功分支"]),
        ("假分支跳进一个不存在的标号（makensis 报 unknown label）",
         dict(target="copy_gone"), ["安装段里没有这个标号"]),
        ("整块拷贝后校验都没写",
         dict(checks=False), ["一条「拷完验一遍」的 IfFileExists 都没有"]),
        ("File 排在校验之后（验的是上一次装的旧内容）",
         dict(file_after=True), ["排在拷贝后校验之后"]),
        ("快捷方式/卸载器排在校验之前",
         dict(entry_before=True), ["排在校验之前"]),
        ("校验前那句 StrCpy 不点文件名（弹窗只会说「装不全」）",
         dict(nameless=True), ["不是一句点名"]),
        ("长文案用反斜杠续行（makensis 当额外参数，只留第一句）",
         dict(cont=True), ["用反斜杠续行"]),
        ("失败分支只说「没落地」，不说下一步该做什么",
         dict(nostep=True), ["没给出下一步动作"]),
        ("安装段被拆成两段（校验条数就不再唯一）",
         dict(sections=2), ["关键文件校验只能有一个归属"]),
        # ── 运行时目录那一处递归删除：它坏的是「升级」这条只有老用户才会走的路径 ──
        ("升级不清旧运行时（新旧混着一棵树，本机全新安装永远试不出来）",
         dict(clean=""), ['安装段没有 RMDir /r "$INSTDIR\\_internal"']),
        ("清的是整个安装目录（把用户这几个月的数据一起带走）",
         dict(clean="$INSTDIR"), ["递归删除的是整个安装目录"]),
        ("清到了用户的数据目录（安装步骤没资格删它）",
         dict(clean="$INSTDIR\\b2bcollect_data"), ["安装步骤没有资格删它"]),
        ("清了一个清单外的目录（行为没人核对过）",
         dict(clean="$INSTDIR\\somewhere_else"), ["唯一的递归删除对象是运行时目录"]),
        ("清排在 File 之后（刚铺好的运行时又被删掉）",
         dict(clean_after=True), ["排在 File 之后"]),
        ("同一棵运行时目录清两遍（有一段是从别处抄来的）",
         dict(clean_twice=True), ["被清了 2 遍"]),
    ]
    for _label, _kw, _needles in _cases_p:
        _p = []
        with _hush():
            asm.check_installer_payload(_install_nsi(asm, **_kw), _p)
        _assert(_p, f"反向对照：{_label} 必须报红（报不出来=这条门在这一类改动上是空的）")
        for _n in _needles:
            _assert(any(_n in x for x in _p),
                    f"反向对照：{_label} 要红在正确的地方——消息里得出现「{_n}」（实际={_p}）")
    _p = []
    with _hush():
        asm.check_installer_payload('Section "Uninstall"\n    RMDir /r "$INSTDIR"\n'
                                    "SectionEnd\n", _p)
    _assert(any("没有安装段" in x for x in _p),
            f"反向对照：整个安装段没了必须红，不能当成「没有校验项」放过：{_p}")
    # 注释里写的旧形状不该被判成违规（和 9) 同一件事：判据读的是要执行的字节）
    _p = []
    with _hush():
        asm.check_installer_payload(_install_nsi(asm).replace(
            "    File /r", ';     旧写法是 ${IfNot} ${FileExists}，本文件没引 LogicLib.nsh\n'
                           "    File /r", 1), _p)
    _assert_eq(_p, [], "反向对照：注释里的旧写法不算违规（真脚本里就留着这样一行解释）")

    # 接线：判据不能只是「一个函数存在」。行为验证——把合成树里那份 .nsi 换成「少验
    # 一项」，装配脚本必须非零退出**且不产出 zip**（正是 CI 全绿、用户拿坏包那一形状）。
    _less = Path(tempfile.mkdtemp(prefix="windist_payload_"))
    _fake_dist(_less, nsi=('Unicode true\nName "B2B"\n'
                           + _install_nsi(asm, drop=("_internal\\config.json",))
                           + _uninstall_nsi(asm.RUNTIME_DIRS)))
    _rc = subprocess.run([sys.executable, str(_REPO / ".github" / "scripts" /
                             "package_win_artifacts.py"), "--root", str(_less)],
                         capture_output=True, text=True, encoding="utf-8",
                         errors="replace", cwd=str(_REPO))
    _assert(_rc.returncode != 0 and "没有校验 $INSTDIR\\_internal\\config.json"
            in (_rc.stdout + _rc.stderr),
            f"反向对照：少验一项在安装包编译之前就被装配那一步拦下，消息点出是哪一项"
            f"（rc={_rc.returncode} out尾部={_rc.stdout[-200:]!r}）")
    _assert(not (_less / asm.PORTABLE_NAME).exists(),
            "拦下来时不产出绿色版 zip：校验没过还继续装配，等于把坏包发出去")
    # dist 树少一个驱动文件（目录在、里面空）也必须判红：空目录与缺目录在用户机器上
    # 是同一个症状（浏览器通道点了没反应），而产物自检照样绿。
    _empty = Path(tempfile.mkdtemp(prefix="windist_drivers_"))
    _fake_dist(_empty)
    shutil.rmtree(_empty / "dist" / asm.APP_DIR_NAME / "_internal" / "drivers")
    (_empty / "dist" / asm.APP_DIR_NAME / "_internal" / "drivers").mkdir()
    got = []
    with _hush():
        asm.check_layout(_empty, got)
    _assert(any("drivers" in p and "目录型条目空着也算缺" in p for p in got),
            f"反向对照：drivers/ 空目录＝一个 chromedriver 都没下载成功，必须点名：{got}")


def test_state_file_never_relative_cwd():
    print("\n═══ P. 运行期文件的落点：绝不相对当前工作目录 ═══")
    import audit_logger as _al
    import mapping_recommender as _mr

    # 1) 源码层面：两个默认值都不许再是「裸文件名」（那等于相对 CWD）
    for label, path, name in (("audit_logger", "audit_logger.py", "log_file"),
                              ("mapping_recommender", "mapping_recommender.py", "cache_file")):
        src = (_REPO / "b2bcollect_build" / path).read_text(encoding="utf-8")
        _assert(not re.search(rf'{name}: str = "[^"/\\\\]+\.(json|log)"', src),
                f"{label} 的 {name} 默认值不再是相对 CWD 的裸文件名")
        _assert("state_file" in src, f"{label} 的默认落点问 tool_config.state_file（唯一口径）")

    # 2) 行为层面：CWD 切到一个根本写不出文件的地方，落点仍在状态目录里
    elsewhere = Path(tempfile.mkdtemp(prefix="cwd_elsewhere_"))
    old_cwd = os.getcwd()
    try:
        os.chdir(str(elsewhere))
        with _workspace() as ws, _frozen(ws):
            for label, got in (("审计日志", _al._default_log_file()),
                               ("映射历史", _mr._default_cache_file())):
                p = Path(got)
                _assert(p.is_absolute(), f"{label} 的落点是绝对路径（实际={got}）")
                _assert(str(ws["bundle"]) not in str(p),
                        f"{label} 不落在随包数据目录里（打包态那可能是退出即删的 _MEI，"
                        f"或 Mac 的只读转置卷）（实际={got}）")
                _assert(p.parent == tc.state_dir(),
                        f"{label} 与状态目录同源（口径只有一份）（实际={p.parent}）")
            # 真的写一次：状态目录可写就落得下，且读得回来
            _mr.MappingRecommender().update(["公司名称"], {0: "公司名"})
            _assert(Path(_mr._default_cache_file()).is_file(), "映射历史真写得进去")
            _al.AuditLogger().log("export", "u", {"n": 1})
            _assert(Path(_al._default_log_file()).is_file(), "审计日志真写得进去")
    finally:
        os.chdir(old_cwd)

    # 3) 坏缓存绝不能把对话框带崩：这一类形状就是本轮 Mac 崩溃的翻版
    bad_root = Path(tempfile.mkdtemp(prefix="badhist_"))
    for name, payload in (("半截", "{不是 JSON"), ("形状不对", "[1, 2, 3]"),
                          ("条目是字符串", json.dumps({"表头": "字段"})),
                          ("计数不是数字", json.dumps({"表头": [["字段", "abc"]]})),
                          ("空值", json.dumps({"表头": [None, [], ["", 1]]}))):
        f = bad_root / f"{name}.json"
        f.write_text(payload, encoding="utf-8")
        rec = _mr.MappingRecommender(cache_file=str(f))
        _assert_eq(rec.recommend(["表头"]), {}, f"坏缓存「{name}」退化成没有历史，不抛异常")
    _assert_eq(_mr.MappingRecommender(cache_file=str(bad_root / "不存在.json")).history, {},
               "文件不存在时按空历史起步")
    unwritable = bad_root / "blocker"
    unwritable.write_text("x", encoding="utf-8")
    raised = None
    try:
        _mr.MappingRecommender(cache_file=str(unwritable / "nope" / "h.json"))._save_history()
        _al.AuditLogger(log_file=str(unwritable / "nope" / "a.log")).log("a", "u", {})
    except Exception as e:
        raised = e
    _assert(raised is None,
            "落点写不进去时只报一句、不把导入动作带崩"
            + (f"（实际抛了 {type(raised).__name__}: {raised}）" if raised else ""))


def test_relaunch_chain_off_the_bundle():
    print("\n═══ Q. 重启链路：不拿包内目录当 cwd、旧进程真退出才起新实例 ═══")
    import b2bcollect_gui as gui

    # 1) 工作目录：打包态取 exe 同级（磁盘上那个真实文件），绝不取随包数据目录
    with _workspace() as ws, _frozen(ws):
        _assert(ws["exe_dir"].exists(),
                "夹具的 exe 同级目录在位（onedir 的意义就在这：可执行文件是真实文件）")
        workdir = Path(gui._relaunch_workdir())
        _assert(str(ws["bundle"]) not in str(workdir.resolve()),
                f"重启给新实例的 cwd 不是随包数据目录（实际={workdir}）——"
                "子进程占着那个目录，旧实例就删不掉自己的临时目录，"
                "base_library.zip 正是这么报 FileNotFound 的")
        _assert(workdir.is_dir(), f"给出去的 cwd 真存在：{workdir}")

    # 2) 参数写法：重启标记不许被当成自检参数（否则 CI/界面两条路互相劫持）
    for argv in ([gui.RELAUNCH_FLAG, "4242"], [gui.RELAUNCH_FLAG, "4242",
                                              gui.RELAUNCH_NOTE_MOVED]):
        want_doctor, wrong = gui._doctor_request(list(argv))
        _assert(not want_doctor and wrong == [],
                f"{argv} 被判成「不是自检、也没有认不出的写法」（必须走建界面这条路）")
    _assert_eq(gui._relaunch_source_pid([gui.RELAUNCH_FLAG, "4242"]), 4242,
               "取到前任 PID")
    _assert_eq(gui._relaunch_source_pid([gui.RELAUNCH_FLAG, "abc"]), 0,
               "PID 写坏时退回 0（当普通启动，绝不 wait 一个不存在的进程）")
    _assert_eq(gui._relaunch_source_pid([]), 0, "没有这个参数就不是重启拉起的")
    _assert_eq(gui._relaunch_note([gui.RELAUNCH_FLAG, "1", gui.RELAUNCH_NOTE_MOVED]),
               gui.RELAUNCH_NOTE_MOVED, "搬家那一次的重启原因读得出来")
    _assert_eq(gui._relaunch_note([gui.RELAUNCH_FLAG, "1"]), "",
               "权限变更那一次没有原因标记（话术各说各的，不互相冒充）")

    # 3) 三条纪律只能在文本上钉住（跑一次就会真的拉起新进程/真的退出）
    body = _code_only(gui.MainWindow._spawn_relaunch)
    _assert("shell=True" not in body,
            "自动重启不走 shell：命令行里有中文路径、空格、8.3 短名"
            "（实机日志里就是 C:\\Users\\ADMINI~1\\...），过一层 shell 就多一层引号歧义")
    _assert("_relaunch_workdir()" in body, "cwd 走唯一那个函数，不在这里各写一份")
    _assert("RELAUNCH_FLAG" in body and "os.getpid()" in body,
            "把「我是谁」交给新实例去观测（旧写法 timeout /t 2 是猜时长）")
    _assert("_relaunch_note" in body or gui.RELAUNCH_NOTE_MOVED in body,
            "重启原因跟着参数走，新实例才知道该说哪句话")
    _assert("timeout" not in body.lower() and "cmd /c" not in body.lower(),
            "不再用 cmd /c start + timeout 猜时长：真实判据是「前任进程没了」，"
            "由新实例去观测（见 :func:`_wait_for_previous_instance`）")
    cmd_src = _code_only(gui.MainWindow._relaunch_command)
    _assert("return [sys.executable]" in cmd_src,
            "打包态起的是自身可执行文件——onedir 下它是磁盘上的真实文件，"
            "不再指向退出即删的临时解包目录")
    _assert(0 <= cmd_src.index("frozen") < cmd_src.index("return [sys.executable]"),
            "frozen 分支先返回，源码分支才用 __file__（两种形态各有各的启动路径）")


def test_channel_failure_kind():
    print("\n═══ R. 一路通道失败的种类：成因由文件的主人回答 ═══")
    import site_adapters as _sa

    with _patch(_sa, "_LIST_CHANNEL_LOADED", {"riskbird": True, "aiqicha": False}), \
         _patch(_sa, "ADAPTER_ERRORS",
                {"riskbird": "FileNotFoundError: [Errno 30] Read-only file system",
                 "aiqicha": "ModuleNotFoundError: No module named 'aiqicha_crawler_v2'"}):
        _assert_eq(_sa.channel_failure_kind("riskbird"), "broken",
                   "模块已经加载成功却在构造期抛 FileNotFoundError：文件明明在包里，"
                   "说成「缺文件」就是把只读卷的环境问题报成发包缺陷")
        _assert_eq(_sa.channel_failure_kind("aiqicha"), "missing",
                   "模块压根没加载上、报的又是找不到模块：这是发包缺文件，两条证据同向")
        _assert_eq(_sa.channel_failure_kind("qzd"), "", "没失败就不下结论")
    with _patch(_sa, "_LIST_CHANNEL_LOADED", {"aiqicha": False}), \
         _patch(_sa, "ADAPTER_ERRORS", {"aiqicha": "FileNotFoundError: [Errno 2] "
                                                   "No such file or directory"}):
        _assert_eq(_sa.channel_failure_kind("aiqicha"), "missing",
                   "同一句 FileNotFoundError，模块没加载上时它就是「包里有依赖但那个文件"
                   "没进包」——判据必须看加载状态，只读异常文本会把两类故障说成同一句话")
    with _patch(_sa, "_LIST_CHANNEL_LOADED", {"aiqicha": False}), \
         _patch(_sa, "ADAPTER_ERRORS", {"aiqicha": "ImportError: No module named 'cv2'"}):
        _assert_eq(_sa.channel_failure_kind("aiqicha"), "missing",
                   "缺依赖也是「东西没进包」这一类（发包方要查的是清单，不是用户权限）")
    with _patch(_sa, "_LIST_CHANNEL_LOADED", {"aiqicha": False}), \
         _patch(_sa, "ADAPTER_ERRORS", {"aiqicha": "OSError: [Errno 13] Permission denied"}):
        _assert_eq(_sa.channel_failure_kind("aiqicha"), "broken",
                   "非 import 期的错误不算缺文件（本机加载不出）")
    import b2bcollect_gui as gui
    with _patch(_sa, "_LIST_CHANNEL_LOADED", {"riskbird": True, "aiqicha": True}), \
         _patch(_sa, "ADAPTER_ERRORS", {"riskbird": "FileNotFoundError: x",
                                        "aiqicha": "FileNotFoundError: y"}):
        _assert_eq(gui.MainWindow._lost_channels_kinds(("riskbird", "aiqicha")), {"broken"},
                   "两路同一成因只得到一个种类（界面据此只说一句结论）")
        _assert("磁盘只读" in gui.MainWindow._lost_channels_reason(("riskbird", "aiqicha")),
                "全是 broken 时把「只读/不可写」这一类摆在话术里，不让人去查网络")
    with _patch(_sa, "_LIST_CHANNEL_LOADED", {"riskbird": False, "aiqicha": True}), \
         _patch(_sa, "ADAPTER_ERRORS", {"riskbird": "ModuleNotFoundError: x",
                                        "aiqicha": "AttributeError: y"}):
        _assert("缺了、或本机加载不出" in gui.MainWindow._lost_channels_reason(
            ("riskbird", "aiqicha")),
            "两种成因并存时退回覆盖得下的那句，不替某一路下结论")
    # 判据本体出问题也不能把界面带崩
    with _patch(_sa, "channel_failure_kind", lambda s: (_ for _ in ()).throw(RuntimeError("x"))):
        _assert_eq(gui.MainWindow._lost_channels_kinds(("riskbird",)), {"unknown"},
                   "判据自身抛异常时退化成 unknown，界面照常起得来")


def test_app_translocation_relocation():
    print("\n═══ S. Mac 只读副本：识别得出，也能一次点完搬家 ═══")
    import plistlib

    root = Path(tempfile.mkdtemp(prefix="relocate_"))
    home = root / "home"
    apps = home / "Applications"
    apps.mkdir(parents=True)

    def mkapp(p, bid="com.b2bcollect.tool", ver="1.0"):
        (p / "Contents" / "MacOS").mkdir(parents=True, exist_ok=True)
        (p / "Contents" / "Info.plist").write_bytes(plistlib.dumps(
            {"CFBundleIdentifier": bid, "CFBundleShortVersionString": ver,
             "CFBundleExecutable": "B2bCollectTool"}))
        (p / "Contents" / "MacOS" / "B2bCollectTool").write_text("MZ", encoding="utf-8")
        return p

    running = mkapp(root / "AppTranslocation" / "UUID-XYZ" / "B2bCollectTool.app")
    source = mkapp(home / "Downloads" / "B2bCollectTool.app")
    K = dict(app=running, home=home, apps_dir=apps, platform="darwin")

    plan = tc.app_migration_plan(**K)
    _assert_eq(plan["status"], "ready", "只读副本 + 下载目录里有同一份 = 可以由工具代做")
    _assert_eq(plan["source"], source, "源就是实测到的那一份，不靠猜文件名")
    _assert_eq(Path(str(plan["dest_exe"])), apps / "B2bCollectTool.app" / "Contents" /
               "MacOS" / "B2bCollectTool", "新位置的可执行路径由 Info.plist 算出")

    # 四种「不许动手」的形态各有一条独立判据（每一种都是能损坏用户数据的方向）
    stash = [0]

    def _clear_dest():
        """把「应用程序」里那份挪走：每颗反例都从目标位置是空的起步。"""
        d = apps / "B2bCollectTool.app"
        if d.exists():
            stash[0] += 1
            d.rename(root / f"stash{stash[0]}.app")

    mkapp(apps / "B2bCollectTool.app", bid="com.someone.else")
    _assert_eq(tc.app_migration_plan(**K)["status"], "name_taken",
               "目标位置有同名但不同产品的包时绝不动手（同名不是证据）")
    _clear_dest()
    mkapp(apps / "B2bCollectTool.app", bid="com.b2bcollect.tool", ver="9.9")
    _assert_eq(tc.app_migration_plan(**K)["status"], "name_taken",
               "目标位置已有同产品但版本不同：不覆盖、不删除，交回给用户决定")
    _clear_dest()
    running_no_id = mkapp(root / "AppTranslocation" / "UUID-2" / "B2bCollectTool.app")
    (running_no_id / "Contents" / "Info.plist").write_bytes(plistlib.dumps(
        {"CFBundleExecutable": "B2bCollectTool"}))
    got = tc.app_migration_plan(app=running_no_id, home=home, apps_dir=apps,
                                platform="darwin")
    _assert_eq(got["status"], "unknown_identity", "读不到包标识就承认无法证明，不猜")

    mkapp(apps / "B2bCollectTool.app", bid="com.b2bcollect.tool")   # 同一份已在应用程序里
    _assert_eq(tc.app_migration_plan(**K)["status"], "already_installed",
               "已经装好了就说「从那里打开」，不再给一个会失败的搬家按钮")
    _clear_dest()
    source.rename(root / "moved-away.app")
    _assert_eq(tc.app_migration_plan(**K)["status"], "no_source",
               "在有限的几个用户目录里没找到同一份时实话实说（绝不做全盘搜索）")
    root_app = mkapp(home / "B2bCollectTool.app", bid="com.b2bcollect.tool")
    _assert_eq(tc.app_migration_plan(**K)["status"], "ready", "家目录根上也算候选（深度一层）")
    mkapp(home / "Desktop" / "B2bCollectTool.app", bid="com.b2bcollect.tool")
    got = tc.app_migration_plan(**K)
    _assert_eq(got["status"], "ambiguous", "多份副本时不替用户猜搬哪一个")
    _assert(got["detail"].count("B2bCollectTool.app") >= 2,
            f"两份都逐字列在细节里，用户才知道要去处理谁（实际={got['detail']}）")
    root_app.rename(home / "Desktop" / "extra.app")      # 只剩桌面那一份
    _assert_eq(tc.app_migration_plan(**K)["status"], "ready", "去掉一份就不再含糊")
    _assert_eq(tc.app_migration_plan(**{**K, "platform": "win32"})["status"], "not_mac",
               "非 macOS 直接判「没这回事」（搬家这件事是 Mac 专属）")
    _assert_eq(tc.app_migration_plan(app=home / "Desktop" / "B2bCollectTool.app", home=home,
                                     apps_dir=apps, platform="darwin")["status"],
               "not_translocated", "不是从只读副本启动的，就不给搬家入口")

    # 执行层：同卷改名真能搬成；源没了、目标已存在都不许判成功
    got = tc.app_migration_plan(app=running, home=home, apps_dir=apps, platform="darwin")
    src_now, dest_now = got["source"], got["dest"]
    ok, why = tc.move_app_bundle(src_now, dest_now)
    _assert(ok and Path(str(got["dest_exe"])).is_file() and not src_now.exists(),
            "同卷改名真搬得动，且源目录不残留半个副本"
            + ("" if ok else f"（失败原因：{why}）"))
    again = tc.move_app_bundle(src_now, dest_now)
    _assert(not again[0], "源已经不在了再搬一次必须判失败，绝不能把「不存在」当成功")
    other = mkapp(home / "Desktop" / "second.app")
    ok2, why2 = tc.move_app_bundle(other, dest_now)
    _assert(not ok2 and "不覆盖" in why2, f"目标已存在时拒绝动手并说清原因：{why2}")
    _assert(other.is_dir(), "拒绝的时候源目录一定还在（不许留下半个搬家现场）")

    # 界面侧：链接永远给，但话术跟着判据变；判据自身出问题也不能带崩界面
    import b2bcollect_gui as gui
    with _patch(tc, "app_migration_plan", lambda: {"status": "ready", "dest": apps}):
        _assert("帮我移进" in gui.MainWindow._app_relocate_link(),
                "能代做时链接就承诺代做，不写「请你自己拖」")
    with _patch(tc, "app_migration_plan", lambda: {"status": "no_source"}):
        _assert("在访达里打开" in gui.MainWindow._app_relocate_link(),
                "做不了时链接只承诺指路，不假装一键搞定")
    with _patch(tc, "app_migration_plan",
                lambda: (_ for _ in ()).throw(RuntimeError("boom"))):
        _assert("在访达里打开" in gui.MainWindow._app_relocate_link(),
                "判据抛异常时退回最保守的那句（一条提醒绝不能把界面带崩）")

    class _Fake:
        def __init__(self):
            self.called = []

        def _acl_prompt_restart(self):
            self.called.append("restart")

        def _offer_app_relocation(self):
            self.called.append("relocate")

    f = _Fake()
    gui.MainWindow._on_banner_link(f, "#app-relocate")
    gui.MainWindow._on_banner_link(f, "#acl-restart")
    gui.MainWindow._on_banner_link(f, "#什么都不知道")
    _assert_eq(f.called, ["relocate", "restart"],
               "两个动作各自分派、未知动作静默忽略（横幅加新链接不该炸界面）")
    # reveal_in_finder 只在 macOS 上真的去开访达：其它平台必须原样返回 False，
    # 离线机器上谁都不该因为一条提醒拉起一个外部进程（判据先于动作，不靠 except 兜）
    old_platform = sys.platform
    try:
        sys.platform = "win32"
        _assert_eq(tc.reveal_in_finder([apps]), False,
                   "非 macOS 不碰 /usr/bin/open")
    finally:
        sys.platform = old_platform
    _assert("shell=True" not in inspect.getsource(tc.reveal_in_finder)
            and "/usr/bin/open" in inspect.getsource(tc.reveal_in_finder),
            "开访达走绝对路径 + 参数列表，不过 shell（中文路径与引号歧义不值得再冒一次险）")
    _assert("app_relocate_link" in inspect.getsource(gui.MainWindow._warn_read_only_runtime),
            "播报只读副本的那一屏同时给出出口（只提示不给办法等于让人烦躁）")


def test_path_discipline_gate():
    print("\n═══ T. 落点纪律门禁：发包前就得认出「数据写进应用包里」 ═══")
    # 为什么这条门禁值得单独一类退出码：本轮 Mac 崩的那句 Errno 30 只是同一条纪律最
    # 响亮的下场，另外三种写法（整包可写时把数据塞进 .app、Windows 写进退出即删的
    # _MEI、界面报「已保存」而文件下次开机不见了）在界面上**一点痕迹都没有**。凭
    # 证链与网络全绿的产物照样能带病发货，所以它必须让 --acl-doctor 的退出码也变红。
    root = Path(tempfile.mkdtemp(prefix="pathgate_"))
    app = root / "B2bCollectTool.app"
    macos = app / "Contents" / "MacOS"
    macos.mkdir(parents=True)
    (macos / "B2bCollectTool").write_text("MZ", encoding="utf-8")
    try:
        with _sys(platform="darwin", frozen=True,
                  executable=str(macos / "B2bCollectTool")):

            code0, lines0 = ag._path_discipline(frozen=True)
            _assert_eq(code0, ag.DOCTOR_EXIT_OK, "候选与实际落点都在包外时判绿（不误伤好包）")
            _assert(any("落点纪律" in l for l in lines0),
                    f"绿也要在报告里留一行，否则下次没人记得这道门在不在（实际={lines0}）")

            # 旧写法「exe 同级 = Contents/MacOS 也进候选链」：判据必须认出来并点名。
            # 这里**只**改候选链、不改 inside_app_bundle：后者既是排除规则又是这道门禁的
            # 判据，整体打桩会把两边一起打掉（自废武功式的假绿），那正是 7a0 立条要避免的。
            with _patch(tc, "state_dir_candidates", lambda subdir="": [macos / tc.STATE_DIR_NAME]):
                code1, lines1 = ag._path_discipline(frozen=True)
            _assert_eq(code1, ag.DOCTOR_EXIT_PATH, "候选链里出现包内那一级时直接判红（不许发货）")
            _assert(any(str(macos) in l or str(macos.resolve()) in l for l in lines1),
                    f"红的那一段必须点名是哪一处（实际={lines1}）")
            _assert(any("test_data_dir_offline" in l for l in lines1),
                    "报告要给下一步该做什么（离线判据在哪、按形态排除是什么意思）"
                    f"（实际={lines1}）")

            # 包内判定必须对同一个目录的两种拼写都成立：macOS 上 /var 是 /private/var 的
            # 软链，而 App Translocation 就发生在 /private/var/folders 这一棵上。只认其中
            # 一种拼写（一边 resolve 一边不 resolve），这条纪律恰好会在最需要它的那台机器
            # 上把包内判成包外——本轮给自检加这道门时就是这样实测出来的。
            _assert(tc.inside_app_bundle(macos / tc.STATE_DIR_NAME)
                    and tc.inside_app_bundle(macos.resolve() / tc.STATE_DIR_NAME),
                    "包内判定对软链两种拼写都成立（比较前统一 resolve）")
            _assert(not tc.inside_app_bundle(app.parent / tc.STATE_DIR_NAME),
                    "包旁那个目录不算包内（否则包旁这一级会被自己排除掉，退路全断）")

            # 只看候选链不够：真正选中的落点在包内同样要红（探针运气挑出包内路径那种形态）
            with _patch(tc, "state_dir", lambda subdir="": macos / tc.STATE_DIR_NAME):
                code2, lines2 = ag._path_discipline(frozen=True)
            _assert_eq(code2, ag.DOCTOR_EXIT_PATH, "实际落点跑进包里同样判红（不靠「候选看着干净」）")

            # 源码态不判红：开发机的数据本来就该待在仓库目录里（与 N 节通道口径同一纪律）
            with _patch(tc, "state_dir_candidates",
                        lambda subdir="": [macos / tc.STATE_DIR_NAME]):
                code3, _ = ag._path_discipline(frozen=False)
            _assert_eq(code3, ag.DOCTOR_EXIT_OK, "源码态只报现状不判红（别把开发机拖成红灯）")

            # 新类别的三件配套事：有码、进了全表、帮助里念得出名字
            _assert(ag.DOCTOR_EXIT_PATH not in {v[0] for v in ag._DOCTOR_KINDS.values()},
                    "落点违规不许混进「服务故障分类表」（那三张表说的是服务为什么连不上）")
            _assert(ag.DOCTOR_EXIT_PATH in {c for c, _ in ag._DOCTOR_EXIT_TABLE},
                    "退出码进了常量却没进全表 = --doctor 帮助里它没名字（上一轮就吃过这亏）")
            _assert("9" in ag.DOCTOR_EXIT_HELP and "落点违规" in ag.DOCTOR_EXIT_HELP,
                    f"帮助文本要数得出这一类（实际={ag.DOCTOR_EXIT_HELP}）")
            src = _code_only(ag.doctor_lines)
            _assert("_path_discipline" in src and "path_code" in src,
                    "自检必须真的调用这道门禁并把它的码并进结论（只写函数不调用＝门开着）")
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ── U. 窗口冒烟门禁 ────────────────────────────────────────────────────────
# 这一条管的是「CI 全绿、用户双击却没界面」那类盲区：--acl-doctor 验凭证、验通道、
# 验落点，唯独不碰 Qt，而本轮两份实机故障恰好都落在 Qt 那一段（Mac 崩在建名单通道，
# Windows 崩在建窗口之前的 import）。所以判据分三层，缺一层就有一个方向被放过：
#   界面侧：开关认得出、分派早于任何副作用、不与自检同族；
#   行为侧：正例真把窗口建出来退 0，反例（只读卷上的 OSError）退非零并留下崩溃日志；
#   入口侧：三处打包入口都跑了它，Mac 那两处还跑在**只读副本**上，且仿真自己也被验活。
SMOKE_STEP_NAME = "Window smoke gate"
SMOKE_MARK = "--ui-smoke"


def _smoke_region(text: str, is_workflow: bool) -> str:
    """三处入口里「窗口冒烟」那一段**会被执行**的行。

    workflow 按 step 切（注释整体剔掉，_iter_steps 已经做了），本机脚本没有 step
    概念，按整篇剔注释。两处都只留可执行行：这些入口的注释里正写着「--acl-doctor
    不建窗口」这种话，拿原文判就是「解释了盲区的行自己被当成盲区」。
    """
    if not is_workflow:
        return "\n".join(ln for ln in text.splitlines()
                         if not ln.strip().startswith("#"))
    for name, body in _iter_steps(text):
        if SMOKE_STEP_NAME in name:
            return body
    return ""


def _smoke_problems(region: str, readonly_expected: bool) -> list:
    """一段冒烟脚本够不够格当发包门禁。返回问题清单，空 = 合格。"""
    out = []
    if SMOKE_MARK not in region:
        out.append(f"没把 {SMOKE_MARK} 传进产物（跑 --acl-doctor 不算：它刻意不建窗口）")
    elif not any(k in region for k in ("ExitCode", "rc=$?", "RC=$?")):
        out.append("冒烟结果没进退出码（只看它跑没跑，红了也不知道为什么红）")
    if "*.log" not in region:
        out.append("没查「临时用户目录里有没有崩溃日志」：异常被吞掉、界面勉强开出来"
                   "那种半坏状态，退出码 0 是绿的")
    if readonly_expected:
        if "chmod -R a-w" not in region:
            out.append("没把副本改成只读：这一步验的就不是 Gatekeeper 转置那个现场")
        # 探针必须是**真写入**。macOS 上文件属主改时间戳不需要写权限，touch 在 r-- 的文件
        # 上照样成功——2026-11-12 本机与云端同时红在「这份副本没被改成只读」，而那份副本
        # 其实早就只读了：一个永远成功的检查拿来当判据，比没有判据更坏（它把真做成的仿真
        # 说成失效，整条门禁就此卡在「验不过自己」上，产品反而是好的）。
        # 判据按**同一行**要求 printf + >> + 探针变量：只要求「文中有 >> 也有 READONLY_PROBE」
        # 的话，把探针语句换成 touch 却留着那行赋值就蒙混过去了（反向对照当场抓到过一次）。
        if not re.search(r"printf[^\n]*>>[^\n]*READONLY_PROBE", region):
            out.append("只读仿真自己没被一次真写入验过（touch 改时间戳对属主永远成功，"
                       "判据必须往探针文件里追加字节才算数）")
        # ACL 与扩展属性会盖在模式位之上（PyInstaller 产物每个文件都带 com.apple.provenance），
        # 只 chmod 就可能「看着只读、实际还能写」——那是第二种假象，方向与上面正好相反。
        if "chmod -R -N" not in region or "xattr" not in region:
            out.append("改只读之前没清掉 ACL 与扩展属性（它们会盖过模式位，仿真可以看着成立而实际可写）")
        if "wc -l" not in region:
            out.append("没比对副本前后的文件数：写进包内在没转置的机器上不报错，只会在"
                       "升级/卸载时把用户数据一起带走")
    return out


# ── V. 运行形态开关的存取纪律 ──────────────────────────────────────────────
# 本轮 3 条红（663 绿里有 3 条是隔了一整节才红的）根因就在这里：T 节把
# ``old = (sys.platform, sys.executable, getattr(sys, "frozen", None))`` 存成位置元组，
# 还原却按 ``sys.executable = old[2]`` / ``sys.frozen = old[1]`` 写——顺序错开一位。
# 当场不红，红的是下一节（U 节在进程内真建窗口，撞上 Path(None)），报的还是
# 「NoneType」这种和产品毫不相干的栈。这类缺陷最坏的地方不是红，是**红的位置离真因
# 隔了一整节**：排查的人会先去查产品代码。所以除了本套自己的 ``_sys``/``_run``，
# 这条写法要在全部离线套件上被机械地拦住。
_SWITCH_INDEX_RESTORE = re.compile(
    r"sys\.(?:frozen|_MEIPASS|executable|platform)\s*=\s*[A-Za-z_]\w*\[\d\]")
_SWITCH_TUPLE_SAVE = re.compile(
    r"(?:^|[\s(])(?:old|saved|before|_old|_saved)\s*=\s*\(\s*sys\.(?:frozen|_MEIPASS|executable|platform)\b")


_STR_TOKENS = {tokenize.COMMENT, tokenize.STRING}
for _name in ("FSTRING_START", "FSTRING_MIDDLE", "FSTRING_END"):   # Python 3.12+ 拆开 f-string
    _tok = getattr(tokenize, _name, None)
    if _tok is not None:
        _STR_TOKENS.add(_tok)


def _switch_code_text(text: str):
    """把注释与字符串**就地**掩成空格，返回只剩代码骨架的文本；行号与列位一字不移。

    为什么是掩字符而不是跳过整行：本节自己的反例样本就写在代码行上
    （``_bad_restore = "…sys.frozen = old[1]…"``），跳过整行会把同一行上真会执行的代码
    一起放过；而 docstring/注释里那两句是**解释**这种写法为什么不行，不是要拦的行为。
    一条误报的代价比漏报还大：下一来的人会为了消红去改判据。
    读不懂（语法错、编码怪异）返回 None，调用方必须判红——「读不懂」不等于「没问题」。
    """
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except Exception:
        return None
    lines = text.splitlines(keepends=True)
    for tok in tokens:
        if tok.type not in _STR_TOKENS:
            continue
        sl, sc, el, ec = tok.start[0], tok.start[1], tok.end[0], tok.end[1]
        for i in range(sl, el + 1):
            ln = lines[i - 1]
            body = ln.rstrip("\r\n")
            a = max(0, min(sc if i == sl else 0, len(body)))
            b = max(a, min(ec if i == el else len(body), len(body)))
            lines[i - 1] = body[:a] + " " * (b - a) + ln[len(body):]
    return "".join(lines)


def _switch_discipline_hits(text: str, where: str = "<文本>"):
    """在这份源码上跑一次纪律，返回「位置:行号: 原文」清单；None 表示这份文本读不懂。"""
    skeleton = _switch_code_text(text)
    if skeleton is None:
        return None
    src = text.splitlines()
    out = []
    for lineno, line in enumerate(skeleton.splitlines(), 1):
        if _SWITCH_INDEX_RESTORE.search(line) or _SWITCH_TUPLE_SAVE.search(line):
            out.append(f"{where}:{lineno}: {src[lineno - 1].strip()[:80]}")
    return out


def _switch_scan_files():
    """纪律扫描范围：三个交付目录的 test_*.py + 交付链上全部 debug/*.py。"""
    g = _manifest_gate()
    dirs = [d for d in g.SHARED_SOURCE_DIRS if (_REPO / d).is_dir()]
    _assert_eq(len(dirs), len(g.SHARED_SOURCE_DIRS),
               f"交付目录都在位，范围没缩水（登记 {list(g.SHARED_SOURCE_DIRS)}，实到 {dirs}）")
    return sorted({p for d in dirs for p in (_REPO / d).glob("test_*.py")} |
                  set((_REPO / "debug").glob("*.py"))), dirs


def _switch_scan_hits(files):
    """对一批文件跑纪律，返回违规清单；读不出来/读不懂的文件也算违规（不是跳过）。"""
    hits = []
    for f in files:
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            hits.append(f"{f.relative_to(_REPO)}: 读不出来（{exc}）")
            continue
        got = _switch_discipline_hits(text, str(f.relative_to(_REPO)))
        if got is None:
            hits.append(f"{f.relative_to(_REPO)}: 语法读不懂，这条判据在它上面没跑")
            continue
        hits.extend(got)
    return hits


def test_switch_discipline_gate():
    print("\n═══ V. 开关存取纪律：不许用位置元组存运行形态开关（还原错一位没人看得出来）═══")
    # 扫描范围按「三个交付目录 + 交付链脚本」来，不写 `*/test_*.py`：仓库里躺着本地备份
    # 目录（b2bcollect_build_backup_20261106 那种），它们既不进包也不参与交付，把里面
    # 特意保留的旧写法算成产品缺陷，这条判据就永久红——而永久红的判据等于没有判据。
    # 目录清单取自构建前硬门，两边共用一张表（各抄一份迟早会漂）。
    files, dirs = _switch_scan_files()
    _assert(len(files) >= 40, f"扫描范围不是空的（实扫 {len(files)} 个文件，少于 40 就是通配写错了）")
    _assert(__file__ in [str(f) for f in files], "本套自己也在扫描范围内（判据不许只量别人）")
    _assert(not any("backup" in p.as_posix() for p in files),
            "本地备份目录不在范围内（不进包的东西不该决定交付判据）")
    _assert_eq(_switch_scan_hits(files), [], "全部离线套件里没有人用位置元组存/还运行形态开关"
                       "（改回按名字存取：with _sys(...) / with _switches(...)）")
    # 反向对照：把本轮真出过事的那两句喂给判据，必须各抓一次（抓不住就是空转）
    _bad_restore = "        sys.frozen = old[1]\n"
    _bad_save = "    old = (sys.platform, sys.executable, getattr(sys, 'frozen', None))\n"
    _assert(_SWITCH_INDEX_RESTORE.search(_bad_restore), "反例①：按位置还原必须被抓住")
    _assert(_SWITCH_TUPLE_SAVE.search(_bad_save), "反例②：位置元组取值必须被抓住")
    _sample = ("# 注释里写 old = (sys.platform, sys.executable)\n"
               "def f():\n"
               '    """文档里写 old = (sys.platform, sys.executable) 这种位置元组\n'
               "    是本轮翻车的那句，这里只是在解释它为什么不行\"\"\"\n"
               "    sys.frozen = old[1]\n")
    # 掩码版判据的三条要害：行号不漂、同一行上的真代码一个字符不少、字符串里的反例不算
    _sample = _sample + '    print("上面那行才是真违规，这一句里的 sys.frozen = old[1] 只是话")'
    _masked = _switch_code_text(_sample)
    _assert(_masked is not None, "反例③起点：样例读得懂（读不懂时后面几条全是空跑）")
    _assert_eq(len(_masked.splitlines()), len(_sample.splitlines()),
               "反例③：掩码不合并行也不拆行（行号一漂，报出来的位置就不是人能跳过去的位置）")
    _assert_eq(_masked.splitlines()[0], " " * len(_sample.splitlines()[0]),
               "注释整行掩成等长空格：判据抓的是代码骨架，报出来的位置却还是原文的位置")
    _assert(not _masked.splitlines()[2].strip() and not _masked.splitlines()[3].strip(),
            "跨两行的 docstring 也掩干净，那两句示例说明都不算违规")
    _assert_eq(_switch_discipline_hits(_sample, "样例"), ["样例:5: sys.frozen = old[1]"],
               f"反例④：真违规那行照抓，同一份文本里注释/docstring/字符串上的同一句不抓"
               f"（实际={_switch_discipline_hits(_sample, '样例')}）")
    _assert("print(" in _masked.splitlines()[5] and "old[1]" not in _masked.splitlines()[5],
            "字符串只掩字面量本身，同一行上的真代码一个字符都没少（跳过整行的老写法做不到）")
    _fs = '    print(f"翻车那句是 sys.frozen = old[1]，值是 {old[1]}")'
    _assert_eq(_switch_discipline_hits(_fs, "f串"), [],
               "反例⑤：f-string 里的反例文字不算代码（3.12+ 把它拆成 FSTRING_* token，一样要掩）")
    _assert_eq(_switch_discipline_hits(_bad_restore, "坏"), ["坏:1: sys.frozen = old[1]"],
               "同样一份判据放在可执行行上照样抓得住（掩码不是把判据调松）")
    _assert_eq(_switch_discipline_hits(Path(__file__).read_text(encoding="utf-8"), "本套"), [],
               "本套不把自己这一节的反例样本判成违规（上一版「跳过整行」就是这么红的）")
    # 反向对照⑥：上面几条测的是「判据认不认得这种写法」，这一条测的是「真放进交付目录的
    # 文件会不会被扫到」。少了它，通配写错、目录名漂掉、helper 换成读常量表这些事故都会
    # 表现成「全绿」——而这条判据当初就是为了拦那个让 U 节隔节翻车的 T 节写法。
    probe = _REPO / "b2bcollect_build" / "zz_discipline_probe.py"
    _assert(not probe.exists(), "反例⑥起点干净（探针文件不在位，上次没留残留）")
    probe.write_text(os.linesep.join(("def g():", "    old = (sys.platform, sys.frozen)",
                                      "    sys.frozen = old[1]")), encoding="utf-8")
    try:
        got6 = _switch_scan_hits([probe])
        _assert_eq([h.split(":")[1] for h in got6], ["2", "3"],
                   f"反例⑥：交付目录里新放一个位置元组的文件，扫描当场报出存与还两行"
                   f"（实际={got6}）")
    finally:
        probe.unlink(missing_ok=True)
    _assert(not any(p.name == probe.name for p in (_REPO / "b2bcollect_build").glob("*.py")),
            "探针删掉后磁盘上立刻没有它（留一个「故意违规」的文件在交付目录里，下一次扫描就永久红）")
    _good = ("    with _sys(platform='win32', frozen=True):\n"
             "        sys.executable = str(exe)\n")
    _assert_eq(_switch_discipline_hits(_good, "正例"), [],
               "正例：就地拨、由 with 还——不误伤（否则这条纪律第一天就把所有套件拖红）")


def test_window_smoke_gate():
    print("\n═══ U. 窗口冒烟门禁：产物自己把界面建出来，Mac 跑在只读副本上 ═══")
    import b2bcollect_gui as _gui

    # 1) 界面侧：写法、分派位置、与自检那条边界
    _assert_eq(_gui.UI_SMOKE_FLAG, SMOKE_MARK, "冒烟开关的写法（CI 抄的是同一个字符串）")
    _assert_eq(_gui._doctor_request([SMOKE_MARK]), (False, []),
               f"{SMOKE_MARK} 既不被当成自检开关，也不进「像自检却认不出」那份清单"
               "（判错的方向很坏：CI 传冒烟参数却退出 3，红的是门禁不是包）")
    msrc = _code_only(_gui.main)
    _assert("UI_SMOKE_FLAG in sys.argv" in msrc and "_ui_smoke_entry()" in msrc,
            "main() 里真的分派到冒烟入口（只有函数没有分派＝门开着没人走）")
    _assert(0 < msrc.index("_acl_doctor_entry()") < msrc.index("_ui_smoke_entry()")
            < msrc.index("previous_pid"),
            "分派排在自检之后、重启交接与建界面之前——早于任何 Qt/Chrome 副作用")
    ssrc = _code_only(_gui._ui_smoke_entry)
    for need in ("MainWindow()", ".show()", "prefetch_authoritative", "install(app)",
                 "_fatal_log(", "QTimer"):
        _assert(need in ssrc, f"冒烟入口保留了 {need}（少一样，验的就不是用户那一次双击）")
    _assert(_gui.UI_SMOKE_EXIT_FAILED != _gui.UI_SMOKE_EXIT_TIMEOUT
            and _gui.UI_SMOKE_DEADLINE_MS > 0,
            "「没建出来」与「建到一半卡住」是两种成因，退出码必须分开；看门狗必须有时长")

    # 2) 行为侧：正例退 0、反例退非零并留下崩溃日志。崩溃日志的落点按 Path.home() 算，
    #    所以整段把家目录指进临时目录——顺手也保证门禁不往真实桌面写东西。运行形态开关
    #    也由这一段自己接管（_source_form）：建窗口这件事在两种形态下走的落点不同，
    #    不压回源码态的话，测出来的可能是上一节留下的那副假打包态。
    root = Path(tempfile.mkdtemp(prefix="uicheck_"))
    old_home, old_prefetch = Path.home, _gui._access_guard.prefetch_authoritative
    import site_adapters as _sa
    import runtime_paths as _rp       # 落点口径本体：它有进程内择路缓存，切环境必须清
    old_build = _sa.build_list_channels
    seen = []
    saved_env = {k: os.environ.get(k) for k in
                 ("B2B_STATE_DIR", _rp.DATA_DIR_ENV_VAR)}
    try:
        Path.home = lambda *a, **k: root
        _gui._access_guard.prefetch_authoritative = lambda *a, **k: seen.append("prefetch")
        # 状态目录与采集数据目录整体指进临时目录：建一次界面会读写 seen_store 那几份
        # 文件，不隔离的话这道门禁每跑一次就动一次开发机的真实数据——而「门禁改了用户的
        # 数据」会让下一次回归的结果不再由夹具决定（与 _workspace 隔离本地状态同理）。
        os.environ["B2B_STATE_DIR"] = str(root / "state")
        os.environ[_rp.DATA_DIR_ENV_VAR] = str(root / "data")
        _rp.reset_cache()
        with _source_form():
            _assert(not tc.is_frozen(), "行为侧跑在源码态（开关由这一段自己拨，不靠邻居收回）")
            rc = _gui._ui_smoke_entry()
            _assert_eq(rc, _gui.UI_SMOKE_EXIT_OK, f"正例：窗口建得出来就退 0（实际={rc}）")
            _assert_eq(seen, ["prefetch"], "冒烟走的是与真实启动同一条路径（含权威身份判定）")
            _assert(not list(root.rglob("*.log")),
                    f"正例不该留下崩溃日志（实际={list(root.rglob('*.log'))}）")

            def _boom(*a, **k):
                raise OSError(30, "Read-only file system")

            _sa.build_list_channels = _boom           # Mac 那份日志的第一个真实形态
            rc2 = _gui._ui_smoke_entry()
            _assert_eq(rc2, _gui.UI_SMOKE_EXIT_FAILED,
                       f"建名单通道撞上只读卷时必须退非零（实际={rc2}）")
            logs = sorted(root.rglob("*.log"))
            _assert(logs, "失败要留下一份崩溃日志：Finder 双击没有终端，日志是唯一取证入口")
            if logs:
                txt = logs[0].read_text(encoding="utf-8")
                _assert("Read-only file system" in txt and "运行形态" in txt,
                        "日志里既要有那句栈，也要有「打包态/转置」这段运行形态抬头")
                _assert(_gui.FATAL_LOG_NAME in str(logs[0]),
                        f"日志名与界面话术、说明书用的是同一个常量（{_gui.FATAL_LOG_NAME}）")
    finally:
        Path.home = old_home
        _gui._access_guard.prefetch_authoritative = old_prefetch
        _sa.build_list_channels = old_build
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        _rp.reset_cache()                             # 缓存里还指着临时目录就得清掉
        shutil.rmtree(root, ignore_errors=True)

    # 3) 入口侧：三处打包入口都把这道门跑起来，Mac 那两处跑在只读副本上
    wf = _REPO / ".github" / "workflows"
    win = (wf / "build-b2bcollect.yml").read_text(encoding="utf-8")
    mac = (wf / "build-b2bcollect-mac.yml").read_text(encoding="utf-8")
    local_sh = (_REPO / "debug" / "build_b2bcollect_mac_local.sh").read_text(encoding="utf-8")
    entries = (("Windows workflow", _smoke_region(win, True), False),
               ("Mac workflow", _smoke_region(mac, True), True),
               ("本机 Mac 打包脚本", _smoke_region(local_sh, False), True))
    for label, region, ro in entries:
        _assert(bool(region), f"{label} 找得出冒烟那一段（找不到=这一步压根不存在）")
        _assert_eq(_smoke_problems(region, ro), [], f"{label} 的冒烟门禁判据齐全")
        _assert("--acl-doctor" in (win if label.startswith("Windows") else
                                   (mac if label.startswith("Mac") else local_sh)),
                f"{label} 里自检门禁仍在（冒烟补的是 Qt 那段盲区，不替凭证与落点让位）")
    # 反向对照：三种「顺手改回去」的形态必须各自点名，否则判据只是摆设
    n = _smoke_region(mac, True).replace(SMOKE_MARK, "--acl-doctor")
    _assert(any("没把 --ui-smoke 传进产物" in p for p in _smoke_problems(n, True)),
            "把冒烟退回成「再跑一遍自检」必须判红——那正是本轮的盲区本身")
    n = _smoke_region(mac, True).replace("chmod -R a-w", "echo ok")
    _assert(any("只读" in p for p in _smoke_problems(n, True)),
            "只读副本被摘掉必须判红（Mac 那两份崩溃日志全靠这个形态才复现得出来）")
    n = _smoke_region(mac, True).replace(' -name "*.log"', '-name "*.txt"')
    _assert(any("崩溃日志" in p for p in _smoke_problems(n, True)),
            "不查崩溃日志必须判红（异常被吞掉、界面勉强开出来那种半坏看不出来）")
    # 「只读仿真成立了吗」这句判据自己也要被验：本轮它就是坏在探针永远成功上（本机与云端
    # 同时红在这里）。拿一份按老写法（touch 当探针、没清 ACL）的段落喂进同一条判据，必须
    # 各判红一次；合格段落喂进去必须是空的——否则上面那几条压根是空转。
    _ro_ok = ('chmod -R -N "$SAPP" 2>/dev/null || true\n'
              'xattr -cr "$SAPP" 2>/dev/null || true\n'
              'chmod -R a-w "$SAPP"\n'
              'READONLY_PROBE="$SAPP/Contents/Info.plist"\n'
              "if (printf 'x' >> \"$READONLY_PROBE\") 2>/dev/null; then exit 1; fi\n"
              'before=$(find "$SAPP" | wc -l)\n'
              'env "$BIN" --ui-smoke\n'
              'rc=$?\n'
              'stray=$(find "$STAGE/home" -name "*.log")\n')
    _assert_eq(_smoke_problems(_ro_ok, True), [],
               "反例起点：合格写法在这条判据上必须是空的（不然下面几条抓不到任何东西）")

    def _ro_drop(text, *needles):
        return "".join(ln + "\n" for ln in text.splitlines()
                       if not any(n in ln for n in needles))

    _touch_probe = _ro_drop(_ro_ok, ">>") + 'touch "$READONLY_PROBE" 2>/dev/null\n'
    _assert(any("真写入" in p for p in _smoke_problems(_touch_probe, True)),
            "拿 touch 当只读探针必须判红（属主改时间戳不需要写权限，那种探针永远成功）")
    _assert(any("ACL" in p for p in _smoke_problems(
        _ro_drop(_ro_ok, "chmod -R -N", "xattr -cr"), True)),
        "少清 ACL 与扩展属性那两句同样要红（模式位之外还有 ACL，仿真可以看着成立而实际可写）")
    _assert(any("没把副本改成只读" in p for p in _smoke_problems(
        _ro_drop(_ro_ok, 'chmod -R a-w'), True)),
        "只读那一句被摘掉要红（同一条判据的第三种断法）")
    gone = "\n".join(ln for ln in mac.splitlines() if SMOKE_STEP_NAME not in ln)
    _assert(not _smoke_region(gone, True),
            "整步删掉时切出来的是空段（判据靠 step 名字定位，改名也要能被看见）")


# ── W. 启动台账与「真双击」判据 ──────────────────────────────────────────────
# 这一条管的是**交付判断本身**可不可信。本机打包脚本最后那一步原来靠 System Events
# 数窗口，而 2026-11-13 本机实测：锁屏与远程会话下它对每一个进程都报 0 窗口（同一时刻
# 连 Qoder、Safari 也数不出）——观测手段在最需要它的场合恰好是瞎的，产物明明活着、窗口
# 明明在，门禁却只能判红。反过来，旧写法为了消这个红又允许「open 唤到前台的旧实例」算
# 通过，那验的是上一份字节，是另一种更坏的红法（它看起来完全成功）。两种毛病同一个根：
# 让别人的状态替产物作证。现在判据由进程自己记账，落点问产物，数窗口只作附加信息。
LEDGER_LOCAL_SH = Path("debug") / "build_b2bcollect_mac_local.sh"
LEDGER_HEAD_MARK = "最后一步：真按用户那一次双击验一遍"


def _ledger_code(text: str) -> str:
    """整份本机脚本去掉整行注释之后的代码。

    落点与有界函数这类是**脚本级**的规矩（一次打包只该问产物一次落点），判据就得跟着
    脚本级走：只盯第 6 步那一段的话，把 `_run_bounded` 或 `--state-dir` 挪到第 4 步复用
    （本轮就是这么改的：报告与台账读同一个落点）会被判成「没有」。
    注释照旧整行剔掉，理由与 _ledger_region 一样。
    """
    return "\n".join(ln for ln in text.splitlines() if not ln.strip().startswith("#"))


def _ledger_region(text: str) -> str:
    """本机脚本里「真双击」那一步**会被执行**的行（注释整行剔掉）。

    判据只看代码骨架：那一段的注释里正写着「数窗口不作判据」这类解释，拿原文去判
    就会把「说明了为什么不能这么写的行」当成违规行——下一来的人为了变绿会删注释，
    那是抹掉下次排查最需要的上下文（与 _code_only 配成一对）。

    但**找那一步的起点必须用原文**：段头「── 6) 最后一步：…」本身就是一行注释，
    先在去掉注释的文本里找起点，等于先把你要找的那一行删掉再按它切块——切出来永远是
    空的，于是下面整串判据全变成「没有这一步」的红（本轮实测就是这么红了一屏）。
    """
    out, started = [], False
    for ln in text.splitlines():
        if not started:
            started = LEDGER_HEAD_MARK in ln
            continue
        if ln.strip().startswith("#"):
            continue
        out.append(ln)
    return "\n".join(out)


def _ledger_problems(region: str, whole: str = None) -> list:
    """「真双击」那一步够不够格当交付判据。返回问题清单，空 = 合格。

    region=那一步的代码，whole=整份脚本的代码（省略时按 region 判，反向对照就地把
    两段一起改即可）。脚本级的三条（问落点、有界函数、只问一次）看 whole，步骤级的
    那几条看 region——分工写明白，否则下次一搬动就得到三条莫名其妙的红。
    """
    out = []
    whole = region if whole is None else whole
    if not region.strip():
        return [f"本机脚本里没有「{LEDGER_HEAD_MARK}」那一步（用户那一次双击压根没验）"]
    if "--state-dir" not in whole:
        out.append("台账落点没问产物（--state-dir）：在 shell 里重算候选链就是第二份真源，"
                   "改了 tool_config 忘了改这里，门禁会去读一个不存在的路径再判一场莫名其妙的红")
    if "state_dir_candidates" in whole or '/.b2bcollect/' in whole:
        out.append("shell 自己算了一遍状态目录（落点必须由产物回答；两处各算迟早漂）")
    # 只问一次：问两次就会有「报告读 A 落点、台账读 B 落点」这种查不出来的分裂
    # （产物两次都答得出来，答的却可能不是同一个目录——受保护目录、环境变量任一变动就是）。
    if len(re.findall(r"_run_bounded\s+\d+\s+\S+\s+--state-dir", whole)) != 1:
        out.append("问产物落点这一步要**全脚本只问一次**（各步各问一次，两次拿到的落点可能"
                   "不是同一个目录，报告与台账就分别读进两个地方）")
    # 台账是产物写的文件，取值必须按字段：按行号或整行匹配的话，改一处话术门禁就安静地
    # 读错字段（第 14 条硬门管的是外部工具输出，这一条管的是自家产物留下的机器可读文件）。
    if not re.search(r"awk -F'\|'.*\$3 == .*print \$2", region):
        out.append("台账不是按 | 分字段取的（时间|pid|阶段|细节 取第 3 列比阶段、第 2 列当 pid）")
    snap, opened = region.find("BEFORE_SHOWN="), region.find("/usr/bin/open")
    if not (0 <= snap < opened):
        out.append("没在 open 之前拍一份 shown 快照：旧实例留下的记录会让这一步绿在上一份字节上")
    if "kill -0" not in region:
        out.append("没验那个 pid 还活着：记过 shown 之后崩掉的进程不能算通过")
    if "-ge 1" in region:
        out.append("数窗口又被拿来当判据（锁屏/远程会话下 System Events 对所有进程都报空，"
                   "那种场合它一个字也证不了；数得出就印出来给人看，数不出不判红）")
    if "_run_bounded() {" not in whole:
        out.append("边界函数没定义（每个调用点自己等一遍，等法还各写各的）")
    # 判的是「这一次调用被包在边界里」，不是「文中有 _run_bounded 这个词」：只看词的话，
    # 把调用改回裸 exec、只留下函数定义，这条门就空转了。
    if not re.search(r"_run_bounded\s+\d+\s+.*--state-dir", whole):
        out.append("问产物落点这一步没有边界（exec 评估队列被堵死时整条交付链跟着挂住，"
                   "日志里一行结论都留不下——与 doctor、ui-smoke 各立看门狗同一条理由）")
    if 'find "$APP_LIVE" -newer' not in region:
        out.append("没查真跑起来那一会儿往不往应用包里写东西（Mac 只读转置卷的 OSError 30 "
                   "在本机双击这条路上就拦不住了）")
    return out


def test_launch_ledger_gate():
    print("\n═══ W. 启动台账：窗口有没有交出去，由进程自己说，不由屏幕状态说 ═══")
    import b2bcollect_gui as _gui

    # 1) 常量侧：shell 里那两处字面量必须与 Python 同一份（漂了就是读不存在的字段）
    local_sh = (_REPO / LEDGER_LOCAL_SH).read_text(encoding="utf-8")
    _assert_eq(_gui.LAUNCH_LOG_NAME, "launch_history.log", "台账文件名只有一份写法")
    _assert_eq(_gui.LAUNCH_STAGE_SHOWN, "shown", "台账阶段名只有一份写法")
    for _lit in (f'LEDGER_NAME="{_gui.LAUNCH_LOG_NAME}"',
                 f'LEDGER_STAGE="{_gui.LAUNCH_STAGE_SHOWN}"'):
        _assert(_lit in local_sh,
                f"本机脚本里的 {_lit} 与 Python 常量逐字一致（各写一份迟早对不上字段）")
    # 卡点文件名也要对拍：脚本第④条成因说的是「去读那份栈」，名字漂了它就去数一个
    # 不存在的路径，而这一步照样绿。本轮那二十分钟的现场取证（``sample`` 数栈 +
    # lldb 读寄存器才问出被扣住的路径）之所以不必在用户机器上重跑，全靠产物自己
    # 把这个名字写进了台账。
    _assert(f'/{_gui.LAUNCH_STALL_FILE_NAME}"' in local_sh,
            f"本机脚本数的卡点文件与产物写的是同一个名字（{_gui.LAUNCH_STALL_FILE_NAME}）")
    _assert(_gui.LAUNCH_STAGE_STALLED in local_sh,
            "本机脚本的成因清单点名了 stalled：产物写了这一行而脚本不读，等于白记")
    _assert(_gui.LAUNCH_STALL_SECONDS >= 30,
            f"取证时限不许缩进正常冷启动的时间里（机械硬盘冷启动 + 系统弹窗等人，"
            f"当前 {_gui.LAUNCH_STALL_SECONDS} 秒）")

    # 2) 开关侧：--state-dir 认得出、不与自检同族、分派最早
    _assert_eq(_gui.STATE_DIR_FLAG, "--state-dir", "取落点的开关写法（门禁抄的是同一个字符串）")
    _assert_eq(_gui._doctor_request([_gui.STATE_DIR_FLAG]), (False, []),
               "--state-dir 既不被当成自检开关，也不进「像自检却认不出」那份清单"
               "（判错的方向很坏：门禁拿不到路径，却看见工具安静地弹出界面）")
    msrc = _code_only(_gui.main)
    _assert("STATE_DIR_FLAG in sys.argv" in msrc and "_state_dir_entry()" in msrc,
            "main() 里真的分派到落点查询（只有函数没有分派＝门开着没人走）")
    _assert(0 <= msrc.index("_state_dir_entry()") < msrc.index("_acl_doctor_entry()"),
            "落点查询排在自检之前：取一个路径不该依赖这台机器连不连得上权限服务")
    for _st in ("LAUNCH_STAGE_BEGIN", "LAUNCH_STAGE_SHOWN", "LAUNCH_STAGE_FAIL"):
        _assert(f"_launch_record({_st}" in msrc,
                f"main() 记了 {_st}：只记 shown 就分不出「压根没起来」和「起来到一半」")
    _assert(0 < msrc.index("_launch_record(LAUNCH_STAGE_BEGIN")
            < msrc.index("QApplication(sys.argv)")
            < msrc.index("_launch_record(LAUNCH_STAGE_SHOWN"),
            "begin 在建 QApplication 之前、shown 在 show() 之后：两条之间夹的正是"
            "「用户看不看得到界面」那一段")
    _assert("_launch_record(" not in _code_only(_gui._ui_smoke_entry),
            "冒烟不写台账：它验的是「窗口建得出」这条代码路径，写进台账会让门禁把 CI "
            "的自检当成用户那一次双击")

    # 卡点取证的接线：这三处顺序错了，都会得到一份「写了也没人能用」的记录
    _assert("_launch_stall_guard_start()" in msrc,
            "main() 真放起了看门狗：只有函数没有调用＝到点没人写证据")
    _assert(msrc.index("_launch_record(LAUNCH_STAGE_BEGIN")
            < msrc.index("_launch_stall_guard_start()")
            < msrc.index("QApplication(sys.argv)"),
            "看门狗在 begin 之后、建界面之前放起：begin 之前那一段是 import（归崩溃日志管），"
            "建界面之后再放就正好等不到最可能卡住的那一段")
    _assert(msrc.index("_launch_shown_or_failed.set()")
            < msrc.index("_launch_record(LAUNCH_STAGE_SHOWN"),
            "记 shown 之前先叫停看门狗：反过来会留下一对自相矛盾的记录"
            "（同一次启动既有 shown 又有 stalled，而两者的修法相反）")
    _assert('name="launch-stall-watchdog"' in _code_only(_gui._launch_stall_guard_start),
            "看门狗必须是守护线程：不是守护的话，它会让进程替自己多活一次「退出」")

    # 3) 行为侧：真写一行、写不进去不抛异常只报信、超长自动截断、落点绝不在包内
    def _trim_ok(path: Path) -> bool:
        """台账的有界不变式：读它的人最多要读多少字节（刚写完一行留一行余量）。

        正向用它、反向对照（把裁剪摘掉）也用它：判的是同一条性质，不是某一句断言。
        """
        return _gui._launch_bytes(
            path.read_text(encoding="utf-8", errors="replace").splitlines()
        ) <= _gui.LAUNCH_LOG_MAX_BYTES + _gui.LAUNCH_LOG_MAX_LINE_BYTES

    root = Path(tempfile.mkdtemp(prefix="ledger_"))
    saved_env = {k: os.environ.get(k) for k in ("B2B_STATE_DIR",)}
    old_home = Path.home
    try:
        Path.home = lambda *a, **k: root
        os.environ["B2B_STATE_DIR"] = str(root / "state")
        with _source_form():
            _gui._launch_record(_gui.LAUNCH_STAGE_BEGIN, "frozen=否 platform=test")
            p = _gui._launch_log_path()
            _assert(p.is_file(), f"记一笔就有一行落在 {_gui.LAUNCH_LOG_NAME}（实际路径={p}）")
            _gui._launch_record(_gui.LAUNCH_STAGE_SHOWN, "frozen=否 platform=test")
            fields = p.read_text(encoding="utf-8").splitlines()[-1].split("|")
            _assert_eq(len(fields), 4, f"行格式固定四段（实际={fields}）")
            _assert_eq(fields[1], str(os.getpid()), "第 2 段是 pid：门禁按 pid 认「本次那一个」")
            _assert_eq(fields[2], _gui.LAUNCH_STAGE_SHOWN, "第 3 段是阶段：门禁按阶段认走到哪")

            # 截断：台账对这台机器承诺的是**有界**，不是「永远只留 400 行」。字节线
            # 管磁盘和「读它的人要等多久」；没到线故意不动老记录——每写一行就重读整份
            # 文件的话，取证功能自己会变成随使用时长变慢的东西（读-改-写的老写法如此，
            # 它还顺带把并发那一次启动的记录冲掉）。
            short_all = _gui.LAUNCH_LOG_MAX_LINES + 60
            p.write_text("\n".join(f"t|{i}|begin|x" for i in range(short_all)) + "\n",
                         encoding="utf-8")
            _assert(_trim_ok(p), "夹具本身没顶到字节线（不然下面那条「不动老记录」测不到东西）")
            _gui._launch_record(_gui.LAUNCH_STAGE_SHOWN)
            kept = p.read_text(encoding="utf-8").splitlines()
            _assert_eq(len(kept), short_all + 1,
                       f"没到字节线就不裁（实际留了 {len(kept)} 行，夹具 {short_all} 行）")

            # 顶过字节线之后，两道收口都必须生效：条数上限 + 字节上限。
            # 只按条数收口是不够的——行一长，400 行照样能超 64KB（下面这个夹具就是）。
            fat = "\n".join(f"t|{i}|begin|" + "x" * 200 for i in range(2000)) + "\n"
            p.write_text(fat, encoding="utf-8")
            _gui._launch_record(_gui.LAUNCH_STAGE_SHOWN)
            kept = p.read_text(encoding="utf-8").splitlines()
            _assert(len(kept) <= _gui.LAUNCH_LOG_MAX_LINES,
                    f"顶过字节线就丢最老的（实际留了 {len(kept)} 行，上限 {_gui.LAUNCH_LOG_MAX_LINES}）")
            _assert(_trim_ok(p), f"裁完字节也必须达标（长行时只按条数收口会漏，实际 {len(kept)} 行）")
            _assert_eq(kept[-1].split("|")[2], _gui.LAUNCH_STAGE_SHOWN, "截断不丢刚记的那一行")

            # 反向对照：把裁剪摘掉，上面两道必须红。留着这条是因为判据会因为写法而
            # 失去牙齿——本轮台账正是从「读整份再重写」换成「追加」，裁剪挪了位置，
            # 如果只留正向断言，谁都能把它改成永不触发的一支而测试照旧全绿。
            old_trim = _gui._launch_trim
            try:
                _gui._launch_trim = lambda path: None
                p.write_text(fat, encoding="utf-8")
                _gui._launch_record(_gui.LAUNCH_STAGE_SHOWN)
                _assert(not _trim_ok(p),
                        "把 _launch_trim 摘掉必须判红（不然上面两道管不住裁剪被删）")
            finally:
                _gui._launch_trim = old_trim

            # 单行上界：按条数收口成立的前提是「一行不会比整份文件还长」。哪天一行人
            # 把栈摘要塞进 extra，400 行的上限就形同虚设，所以超长必须先在写入前截掉。
            _gui._launch_record(_gui.LAUNCH_STAGE_SHOWN, "y" * (_gui.LAUNCH_LOG_MAX_BYTES * 2))
            last = p.read_text(encoding="utf-8").splitlines()[-1]
            _assert(len(last.encode("utf-8")) <= _gui.LAUNCH_LOG_MAX_LINE_BYTES,
                    f"超长的一行写入前就截到单行上限内（实际 {len(last.encode('utf-8'))} 字节）")
            _assert(_trim_ok(p), "一行超长也不能把台账顶过字节线")

            # 卡点取证真跑到点：这是本轮 Mac 那次「台账只有 begin、界面没出来」唯一
            # 能在用户机器上自证的通道。夹具用 0.2 秒代替 45 秒——判的是「到点就写」
            # 这件事本身；时限的量级由上面那条常量判据钉住，不必让测试睡 45 秒。
            saved_stall = _gui.LAUNCH_STALL_SECONDS
            try:
                _gui.LAUNCH_STALL_SECONDS = 0.2
                _gui._launch_shown_or_failed.clear()
                # 另起一根活着、但没在跑代码的线程：只有这一根在夹具里，才能证明
                # dump 出来的**不止**当前线程（CPython 给当前线程的标题是 "Current
                # thread"，其余才是 "Thread 0x…"，所以判据必须按这个差别写，
                # 按 "Thread" 比会被 "Current thread" 蒙过去）。
                holder = threading.Event()
                other = threading.Thread(target=holder.wait, name="stall-probe-thread")
                other.start()
                try:
                    _gui._launch_stall_watchdog()   # 直接调线程体：夹具要的是结论，不是线程
                finally:
                    holder.set()
                    other.join()
                stall = _gui._launch_stall_path()
                tail = p.read_text(encoding="utf-8").splitlines()[-1]
                _assert_eq(tail.split("|")[2], _gui.LAUNCH_STAGE_STALLED,
                           f"到点没动静就在台账里记一笔 stalled（实际最后一行={tail}）")
                _assert(stall.is_file(), f"卡点文件落了盘（{stall}）")
                text = stall.read_text(encoding="utf-8", errors="replace")
                _assert("Current thread" in text,
                        f"卡点文件里有当前线程的栈（实际开头={text.splitlines()[:2]}）")
                _assert(re.search(r"^Thread 0x", text, re.M) is not None,
                        "卡点文件里还有**别的线程**：只 dump 当前线程的话，卡在别的线程"
                        "上那一族就永远没有证据（判据按 CPython 的两种标题写）")
                # 反向对照：已经交出界面（或已经失败）之后到点，一个字都不许再记。
                # 否则台账会出现 shown 与 stalled 并存，而这两种成因的修法完全相反。
                _gui._launch_shown_or_failed.set()
                stall.unlink()
                _gui._launch_stall_watchdog()
                _assert(not stall.exists(),
                        "叫停之后看门狗不再补写卡点文件（矛盾记录比没有记录更难解释）")
                _assert_eq(p.read_text(encoding="utf-8").splitlines()[-1].split("|")[2],
                           _gui.LAUNCH_STAGE_STALLED,
                           "叫停只拦今后，不删已经取到的证据")
            finally:
                _gui.LAUNCH_STALL_SECONDS = saved_stall
                _gui._launch_shown_or_failed.clear()

            # 写不进去：绝不抛异常，但必须把这件事说出来——静默失败会让「没记过这条」和
            # 「这台机器写不进」两件事长成一个样，本轮 Mac 的 OSError 30 撞的正是这一族。
            ro = root / "ro_state"
            ro.mkdir()
            os.environ["B2B_STATE_DIR"] = str(ro)
            os.chmod(ro, 0o500)
            buf = io.StringIO()
            try:
                with contextlib.redirect_stdout(buf):
                    _gui._launch_record(_gui.LAUNCH_STAGE_SHOWN, "只读落点")
                _assert("写不进去" in buf.getvalue(),
                        f"写不进去要当场说清（实际输出={buf.getvalue()!r}）")
            finally:
                os.chmod(ro, 0o700)
            os.environ["B2B_STATE_DIR"] = str(root / "state")

        # 落点纪律：打包态这条记录绝不允许落在应用包里（Mac 转置只读卷会当场 OSError 30，
        # Windows onefile 会落在退出即删的 _MEI 目录，等于每次归零）。
        app = root / "B2bCollectTool.app"
        macos = app / "Contents" / "MacOS"
        macos.mkdir(parents=True)
        (macos / "B2bCollectTool").write_text("MZ", encoding="utf-8")
        with _sys(platform="darwin", frozen=True, executable=str(macos / "B2bCollectTool")):
            lp = _gui._launch_log_path()
            _assert(not tc.inside_app_bundle(lp.parent),
                    f"Mac .app 打包态台账落在包外（实际={lp}）")
            _gui._launch_record(_gui.LAUNCH_STAGE_SHOWN, "打包态仿真")
            _assert(lp.is_file(), f"打包态这一行也真写得出去（实际={lp}）")
        with _sys(platform="win32", frozen=True, _MEIPASS=str(root / "MEI"),
                  executable=str(root / "B2bCollectTool.exe")):
            _assert(str(root / "MEI") not in str(_gui._launch_log_path()),
                    f"Windows 打包态台账不进 _MEIPASS（实际={_gui._launch_log_path()}）")
    finally:
        Path.home = old_home
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(root, ignore_errors=True)

    # 4) 入口侧：本机脚本那一步的判据齐全，且每种「顺手改回去」都各自点名
    whole_sh = _ledger_code(local_sh)
    region = _ledger_region(local_sh)
    _assert(bool(region), "本机脚本里认得出真双击那一段")
    _assert_eq(_ledger_problems(region, whole_sh), [], "本机脚本的真双击判据齐全")
    _assert("count of windows" in region,
            "数窗口仍留在脚本里作附加信息（全删掉就没人告诉运维「这台机器现在能不能看见界面」）")

    def _drop(text, *needles):
        return "\n".join(ln for ln in text.splitlines()
                         if not any(n in ln for n in needles))

    # 老写法整段回灌：按窗口数放行 + 唤旧实例也算通过，必须各判红一次
    _old_way = ('  _w=$(/usr/bin/osascript -e "..." 2>/dev/null || true)\n'
                '  if [ "$_w" -ge 1 ]; then LIVE_PID=$_p; break; fi\n')
    _assert(any("数窗口" in p for p in _ledger_problems(region + "\n" + _old_way, whole_sh)),
            "把窗口数改回判据必须判红——那正是本机实测数不出的一种绿法")
    _assert(any("拍一份 shown 快照" in p for p in
                _ledger_problems(_drop(region, "BEFORE_SHOWN="), whole_sh)),
            "快照那行被摘掉必须判红（旧实例的记录会让这一步绿在上一份字节上）")
    _assert(any("还活着" in p for p in _ledger_problems(_drop(region, "kill -0"), whole_sh)),
            "不验 pid 存活必须判红（记过 shown 之后崩掉的过程不能算通过）")
    _assert(any("按 | 分字段" in p for p in
                _ledger_problems(region.replace("""awk -F'|' -v s="$LEDGER_STAGE" '$3 == s { print $2 }'""",
                                                 """grep shown"""), whole_sh)),
            "取值改回整行 grep 必须判红（改一处话术门禁就安静地读错字段）")
    _assert(any("落点没问产物" in p for p in
                _ledger_problems(_drop(region, "--state-dir"), _drop(whole_sh, "--state-dir"))),
            "落点不问产物必须判红")
    _assert(any("自己算了一遍状态目录" in p for p in
                _ledger_problems(region, whole_sh
                                 + '\nLEDGER="$HOME/.b2bcollect/b2bcollect_state/launch_history.log"\n')),
            "shell 里重抄候选链必须判红（第二份真源）")
    _ASK = '_run_bounded 20 "$APP" --state-dir'
    _assert(any("没有边界" in p for p in
                _ledger_problems(region, _drop(whole_sh, _ASK))),
            "取落点不带边界必须判红")
    _assert(any("只问一次" in p for p in
                _ledger_problems(region, whole_sh + f"\n{_ASK} > /dev/null 2>&1 || _rc=$?\n")),
            "同一份脚本问两次落点必须判红（报告读 A、台账读 B 这种分裂查不出来）")
    _assert(any("边界函数没定义" in p for p in
                _ledger_problems(region, _drop(whole_sh, "_run_bounded() {"))),
            "只留下调用、把边界函数删掉同样要判红（判的是这一次调用真被包住，不是文中有这个词）")
    _assert(any("应用包" in p for p in _ledger_problems(_drop(region, 'find "$APP_LIVE" -newer'),
                                                     whole_sh)),
            "包内写入那项检查被摘掉必须判红（Mac 只读卷的根因就靠它）")
    _assert(_ledger_problems("") and LEDGER_HEAD_MARK in _ledger_problems("")[0],
            "整段被删掉时要点名「这一步压根不存在」（空串不能被当成合格）")


def _hanging_open(gate, wanted, counter=None):
    """把命中 ``wanted`` 的那几条路径上的 ``open()`` 换成「扣住不放」。

    为什么必须这样仿真（2026-11-13 本机实测）：macOS 的隐私保护把受保护目录里的
    ``open()`` 扣在内核里等用户点「允许」，实测扣了四分钟以上。磁盘满、权限拒绝、
    路径不存在都造不出「**等待没有上限**」这一种形状，而这一族的全部要害就在这一句。
    其余路径继续走真实的 open，夹具才能只挡住那一条路。
    """
    real = builtins.open

    def fake(file, *a, **k):
        s = str(file)
        if any(w and w in s for w in wanted):
            if counter is not None:
                counter.append(s)
            gate.wait()
            raise OSError("这次 open 不该返回")
        return real(file, *a, **k)

    return fake


def test_protected_dir_never_blocks_startup():
    print("\n═══ X. 受保护目录不许把启动扣住：等不到也要把界面交出去 ═══")
    import b2bcollect_gui as _gui
    import task_registry as _tr

    root = Path(tempfile.mkdtemp(prefix="protected_"))
    home = root / "home"
    (home / "Desktop").mkdir(parents=True)
    old_home = Path.home
    Path.home = lambda *a, **k: home
    try:
        # 1) 判据本身：哪些目录受保护，两侧都要说清（漏判的代价是「一次没有超时的访问」，
        #    误判的代价是首选落点在好机器上白白降级）
        with _sys(platform="darwin"):
            _assert(tc.is_protected_user_dir(home / "Desktop" / "a.json"), "桌面里的文件受保护")
            _assert(tc.is_protected_user_dir(home / "Desktop"), "桌面目录本身受保护")
            _assert(tc.is_protected_user_dir(home / "desktop" / "x"),
                    "大小写不同也是同一个桌面（macOS 默认文件系统不分大小写）")
            _assert(tc.is_protected_user_dir(home / "Desktop" / "子目录" / "x"),
                    "受保护目录的**子目录**同样受保护：只挡一层等于没挡")
            _assert(tc.is_protected_user_dir(home / "Documents" / "x"), "文稿受保护")
            _assert(tc.is_protected_user_dir(home / "Downloads" / "x"), "下载受保护")
            _assert(not tc.is_protected_user_dir(home / "PythonDemo" / "x"),
                    "家目录下的其他目录不受保护")
            _assert(not tc.is_protected_user_dir(home),
                    "家目录本身不受保护（否则整条候选链会被自己过滤空）")
            _assert(not tc.is_protected_user_dir(root / "state" / "x"), "临时/状态目录不受保护")
        with _sys(platform="win32"):
            _assert(not tc.is_protected_user_dir(home / "Desktop" / "a.json"),
                    "Windows 不套这一层：没有「等用户点允许」这一级，算进去只会白白降级")

        # 2) 候选链：app 被放在桌面上时，「包旁」那一级就是受保护目录，必须整条摘掉
        app = home / "Desktop" / "B2bCollectTool.app"
        macos = app / "Contents" / "MacOS"
        macos.mkdir(parents=True)
        (macos / "B2bCollectTool").write_text("MZ", encoding="utf-8")
        with _sys(platform="darwin", frozen=True, executable=str(macos / "B2bCollectTool")):
            cands = tc.state_dir_candidates()
            _assert_eq([str(c) for c in cands if tc.is_protected_user_dir(c)], [],
                       f"状态目录候选一条都不许在受保护目录（实际={cands}）")
            _assert(not tc.is_protected_user_dir(tc.state_dir()),
                    f"落点自己也不许在受保护目录（实际={tc.state_dir()}）")
            for _n in (_gui.LAUNCH_LOG_NAME, _gui.LAUNCH_STALL_FILE_NAME,
                       _tr.ACCOUNT_FILE_NAME, _tr.TASK_INDEX_NAME):
                _assert(not tc.is_protected_user_dir(tc.state_file(_n)),
                        f"{_n} 的落点不在受保护目录：它是在建界面的路上被读的")
            # 反向对照：把这条过滤摘掉，上面那几道立刻失效——留着它是为了证明
            # 「过滤真的在起作用」，而不是「这台机器恰好把 app 装在别处」
            old_guard = tc.is_protected_user_dir
            tc.is_protected_user_dir = lambda p: False
            try:
                back = [str(c) for c in tc.state_dir_candidates() if "Desktop" in str(c)]
                _assert(back,
                        f"摘掉过滤之后桌面那一级会回到候选链（说明上面那道过滤不是空转，实际={back}）")
            finally:
                tc.is_protected_user_dir = old_guard

            # 3) 权限模块的机器状态也跟着搬家：三份里丢了设备指纹最要命，用户会被
            #    「该账号已在另一台设备使用」拦在门外，而他从来没换过电脑
            with _clean_state_dir():
                want = ag._preferred_state_dir()
                _assert(not tc.is_protected_user_dir(want),
                        f"权限本地状态的首选落点不在受保护目录（实际={want}）")
                _assert(tc.is_protected_user_dir(ag._legacy_state_dir()),
                        "老首选仍然认得桌面那一级：不认得就没法把已经存在的设备指纹搬走")

        # 4) 有界原语：等不到 ≠ 失败，异常 ≠ 超时
        gate = threading.Event()
        try:
            t0 = time.monotonic()
            with _patch(builtins, "open", _hanging_open(gate, [str(home / "Desktop")])):
                answered, value = tc.call_bounded(
                    lambda: open(home / "Desktop" / "x.json"), 0.2)
            took = time.monotonic() - t0
            _assert_eq((answered, value), (False, None),
                       "被扣住的访问如实报成「没答上来」，而不是「失败」")
            _assert(took < 3.0, f"有界等待真的有界（这次用了 {took:.2f} 秒）")
        finally:
            gate.set()
        _assert_eq(tc.call_bounded(lambda: 7, 0.5), (True, 7), "正常路径要给出答案")
        _assert_raises(ValueError, lambda: tc.call_bounded(_raise_value_error, 0.5),
                       "异常也是一种答案，不许被当成超时（那种包本来就是坏的）")

        # 5) 老位置的读：扣住就按没有处理，且本进程只试一次
        #    这一段把状态落点也钉在夹具目录里：开发态的候选链第一级是模块目录，
        #    不钉住的话，下面的 save_account/save_tasks 会写进**仓库里那两份真文件**。
        saved_state_env = os.environ.get("B2B_STATE_DIR")
        os.environ["B2B_STATE_DIR"] = str(root / "state")
        old_wait = _tr.LEGACY_READ_WAIT_SECONDS
        _tr.LEGACY_READ_WAIT_SECONDS = 0.2
        _tr.reset_legacy_state_for_tests()
        try:
            legacy = _tr.legacy_account_path()
            legacy.parent.mkdir(parents=True, exist_ok=True)
            legacy.write_text(json.dumps({"phone": "13800000000"}), encoding="utf-8")
            gate = threading.Event()
            try:
                with _patch(builtins, "open", _hanging_open(gate, [str(legacy)])):
                    t0 = time.monotonic()
                    data = _tr._read_legacy(legacy, {})
                    took = time.monotonic() - t0
                _assert_eq(data, {}, "老位置被扣住时按「没有」继续开界面")
                _assert(took < 3.0, f"老位置的读按时收口（这次用了 {took:.2f} 秒）")
                _assert(str(legacy) in _tr.legacy_blocked(),
                        "记下「这次没读到」：界面据此要说一句，不然像历史绑定凭空没了")
                t0 = time.monotonic()
                with _patch(builtins, "open", _hanging_open(gate, [str(legacy)])):
                    _tr._read_legacy(legacy, {})
                _assert(time.monotonic() - t0 < 0.05,
                        "同一个老位置本进程只试一次：每次 check() 都拖一下界面就是拿功能换取证")
            finally:
                gate.set()

            # 6) 迁移：搬得动就搬走并删老；目标已有则绝不覆盖；两种「已经作废」的状态
            #    都不许被桌面那份捞回来
            _tr.reset_legacy_state_for_tests()
            new_acc = _tr.account_path()
            if new_acc.exists():
                new_acc.unlink()
            data = _tr._adopt_legacy(legacy, new_acc, {})
            _assert_eq(str(data.get("phone") or ""), "13800000000", "把老绑定读出来")
            _assert(new_acc.is_file(), "新落点落了盘")
            _assert(not legacy.exists(), "老的那一份删掉：同时存在两份时，读到哪一份由运气决定")
            legacy.parent.mkdir(parents=True, exist_ok=True)
            legacy.write_text(json.dumps({"phone": "13800000000"}), encoding="utf-8")
            new_acc.write_text(json.dumps({"phone": "13900000000"}), encoding="utf-8")
            _tr._adopt_legacy(legacy, new_acc, {})
            _assert_eq(json.loads(new_acc.read_text(encoding="utf-8"))["phone"], "13900000000",
                       "新落点已有记录时绝不拿老的覆盖（副本可能是几周前的）")
            _tr.reset_legacy_state_for_tests()
            _tr.save_account("13800000000")
            _tr.clear_account()
            legacy.write_text(json.dumps({"phone": "13800000000"}), encoding="utf-8")
            _assert_eq(_tr.load_account(), "",
                       "解绑是持久的：清绑写一条空记录而不是删文件，桌面那份捞不回来")
            legacy_idx = _tr.legacy_index_path()
            legacy_idx.parent.mkdir(parents=True, exist_ok=True)
            legacy_idx.write_text(json.dumps([{"id": "t1", "name": "旧任务"}]), encoding="utf-8")
            _tr.save_tasks([])
            _assert_eq(_tr.load_tasks(), [],
                       "用户删干净历史任务之后，老索引不许复活（空列表是他自己的决定）")
            _idx = _tr.index_path()
            if _idx.exists():
                _idx.unlink()
            _tr.reset_legacy_state_for_tests()
            _assert_eq([t.get("id") for t in _tr.load_tasks()], ["t1"],
                       "升级后第一次启动：桌面上的历史任务搬进状态目录")
            _assert(not legacy_idx.exists(), "历史任务也一并删老，不留第二份真源")

            # 两份新落点都齐了之后，启动路径上一次也不该敲桌面（新用户的常态：
            # 每次都去试一次，等于把已经修好的挂死换成一次固定的等待）
            counter = []
            _tr.reset_legacy_state_for_tests()
            with _patch(builtins, "open", _hanging_open(gate, [str(home / "Desktop")], counter)):
                _tr.account_data()
                _tr.load_tasks()
            _assert_eq(counter, [], "落点搬家之后，启动路径不再访问受保护目录")

            # 7) 权限模块的三份：设备指纹必须跟着搬，且同一个老目录本进程只试一次
            old_dir, new_dir = root / "acl_old", root / "acl_new"
            old_dir.mkdir(parents=True, exist_ok=True)
            (old_dir / ag.DEVICE_FILE_NAME).write_text(json.dumps({"device_id": "D-1"}),
                                                       encoding="utf-8")
            (old_dir / ag.CACHE_FILE_NAME).write_text(json.dumps({"level": 1}), encoding="utf-8")
            saved_tried = set(ag._LEGACY_TRIED)
            ag._LEGACY_TRIED.clear()
            try:
                ag._adopt_from_legacy(old_dir, new_dir)
                _assert((new_dir / ag.DEVICE_FILE_NAME).is_file(),
                        "设备指纹跟着搬：丢了它这台机器自认新设备，用户看到的是「已在另一台设备使用」")
                _assert(not (old_dir / ag.DEVICE_FILE_NAME).exists(), "搬完删老，不留第二份真源")
                (old_dir / ag.CACHE_FILE_NAME).write_text(json.dumps({"level": 2}), encoding="utf-8")
                ag._adopt_from_legacy(old_dir, new_dir)
                _assert_eq(json.loads((new_dir / ag.CACHE_FILE_NAME).read_text(encoding="utf-8"))["level"], 1,
                           "第二次调用不动已有的一份（老副本不许覆盖新落点）")
                (new_dir / ag.CACHE_FILE_NAME).unlink()
                ag._adopt_from_legacy(old_dir, new_dir)
                _assert(not (new_dir / ag.CACHE_FILE_NAME).exists(),
                        "同一个老目录本进程只试一次：不许每次落盘都去敲一次受保护目录")
            finally:
                ag._LEGACY_TRIED.clear()
                ag._LEGACY_TRIED.update(saved_tried)
        finally:
            _tr.LEGACY_READ_WAIT_SECONDS = old_wait
            gate.set()
            if saved_state_env is None:
                os.environ.pop("B2B_STATE_DIR", None)
            else:
                os.environ["B2B_STATE_DIR"] = saved_state_env
    finally:
        Path.home = old_home
        shutil.rmtree(root, ignore_errors=True)


def _raise_value_error():
    raise ValueError("夹具：调用方自己抛的异常")


def _switches_state() -> dict:
    """运行形态开关的当前值（None = 这一项没拨过）。"""
    return {k: getattr(sys, k, None) for k in _SYS_SNAPSHOT}


_SECTION_COST = {}          # 节名 → 本套跑下来的墙钟秒数（只给收尾那一句看）


def _run(section):
    """跑一节，跑完立刻核对运行形态开关：谁拨走没收回就点谁的名。

    为什么不等到最后统一核对：本轮 T 节还原开关时按位置元组赋错了值（executable 变成
    None、frozen 变成一个真值字符串），红的是**隔了一整节**的 U 节，报的还是「NoneType」
    这种和产品毫不相干的栈——排查的人会先去查产品代码。按节点名把这件事的成本从「翻一
    整套用例」压回「看那一个函数」。最后一道总量核对仍然保留在 main() 末尾：它管的是
    「每节都恢复到了上一节留下的错值」这种逐节看不出、但结果与开跑前不一致的形态。

    顺带记下每一节的耗时：批跑看门狗（600s/套件）只能告诉你「这一路没跑完」，说不出
    慢在哪一节、是哪一节把整套件顶过去的（本轮 AS 节刚加进去时就是这样：套件从 5 分钟
    涨到 10 分钟，屏幕上只有一句 HANG）。逐节记账后，最后一道判据能点出名字。

    但只记「跑完的那一节」等于没记：被看门狗掐断的那一轮，收尾那句「最慢小节」根本
    来不及打印（2026-11-16 那一轮就是这样：明知超了 600s，却拿不出是哪一节的半句证据，
    只能靠人手工一节一节量）。所以**开跑前**先把节名打出去：日志的最后一行就是那一个
    正在吃时间的小节——与「FAIL 行必须自带理由」「取不到字节数也要把取不到说出来」同一族。
    """
    before = _switches_state()
    _t = time.perf_counter()
    print(f"▶ {section.__name__}", flush=True)
    section()
    _SECTION_COST[section.__name__] = _SECTION_COST.get(section.__name__, 0.0) + (
        time.perf_counter() - _t)
    after = _switches_state()
    diff = [f"sys.{k}: {before[k]!r} → {after[k]!r}"
            for k in before if before[k] != after[k]]
    _assert_eq(diff, [], f"{section.__name__} 跑完后的运行形态开关要和进来时一致"
                         "（拨走不收回＝下一节测的是邻居的环境）")


def test_batch_state_watch_gate():
    print("\n═══ Y. 批跑不许污染开发机：那道状态文件对拍要真判得出改动 ═══")
    # 由来：一路离线用例的隔离钩子挂在**旧落点**上（tool_dir 早就不决定任务索引在哪），
    # 于是它把假任务 upsert 进开发机真实的 _采集任务索引.json。它自己一直是绿的（按 id
    # 找回自己那条就行），被顶红的是隔壁套件——而屏幕上那两条 ❌ 看起来和产品毫无关系。
    # 「每个套件都记得钉落点」不是防：忘一次就污染几周。批跑入口因此加了一道环境级对拍，
    # 这里验的是**这道守卫本身有没有牙**：只查它在不在，等于用一条假绿换掉另一条假绿。
    runner = (_REPO / "debug" / "run_offline_regression.sh").read_text(encoding="utf-8")

    # 1) 接线侧：取两次、逐字节比、差异进退出码，三处少一处都拿不到「拦得住」这句话
    _assert('_state_snapshot "$STATE_BEFORE"' in runner,
            "开工前真的记了一次快照（只在收工时记＝没有基准，比不出「本轮改的」）")
    _assert('_state_snapshot "$STATE_AFTER"' in runner,
            "收工后真的记了第二次（只记不比对，守卫就只是一句注释）")
    _assert('cmp -s "$STATE_BEFORE" "$STATE_AFTER"' in runner,
            "两份快照逐字节对拍：只看文件在不在，会漏掉内容被改写那一半故障")
    _assert("leak=1" in runner and "[ $leak -ne 0 ]" in runner,
            "对拍不等要置 leak 并且 leak 进最终退出码（只打印不拦路的判据等于没有判据）")

    # 1b) 逐套件归属侧：只在收工对拍的写法答得出「有没有」，答不出「是谁」。上一轮屏幕
    # 上是「改动了 2 份」，接下来靠人回想这一批跑过什么——回想出来的结论一条都不能复跑。
    _assert('_state_snapshot "$CUR" "$STATE_NAMES"' in runner,
            "每跑完一路套件就要取一张新快照（只在收工时取＝归属这一问没接线）")
    _assert('cmp -s "$PREV" "$CUR"' in runner,
            "这一张和上一张要逐字节对拍（同一张跟自己比恒等，报出来的归属永远是空）")
    _assert('cp "$CUR" "$PREV"' in runner,
            "对拍完必须推进「上一张」：不推进则第一处污染之后的每一路都被点名，噪音埋掉真元凶")
    _assert('leak_suites="$leak_suites $f"' in runner and "⚑" in runner,
            "改动要打在犯错的那一路套件名下（只有总数没有名字的报告＝把人引去猜方向）")
    _at = runner.find("⚑")
    _assert("leak=1" in runner[max(0, _at - 400):_at],
            "逐套件那一条也要就地置 leak：只有收工对拍进退出码的话，归属红了仍会「ALL SUITES PASS」")
    _assert("逐套件归属" in runner and "未定位到具体套件" in runner,
            "收工那句结论要把归属带上（名字只出现在中间流水里，人看的是最后一行）")

    # 2) 行为侧：把脚本里内嵌的 Python 段抽出来，在一个假仓库上真跑。
    # 这一段原先按「段数 == 2 + 位置元组解包」写，本轮为了「污染要报在犯错的套件名下」
    # 加了第 3 段（一次性取声明名单），顶出来的是一条 ValueError——和产品毫不相干，还会
    # 把人引去改判据。现在按**所在 shell 函数名**绑定：多一个具名函数就多一段，各自点名；
    # 而任何没挂在具名函数里的裸 heredoc 由下面的 orphan 断言抓住（分段职责不能靠自觉）。
    def _block_of(fn: str) -> str:
        m = re.search(rf"^{re.escape(fn)}\(\)[^\n]*\n(.*?)^\}}\n", runner, re.S | re.M)
        _assert(m, f"批跑脚本里要有具名函数 {fn}（判据散在循环里就没法逐段真跑）")
        found = re.findall(r"<<'PY'\n(.*?)\nPY\n", m.group(1), re.S)
        _assert_eq(len(found), 1, f"{fn} 里应恰好内嵌一段 Python（多段＝职责没分段）")
        return found[0]

    names_code = _block_of("_state_declared_names")
    snap_code = _block_of("_state_snapshot")
    report_code = _block_of("_state_leak_report")
    _assert_eq(len(re.findall(r"<<'PY'\n", runner)), 3,
               "三段内嵌 Python 都要挂在上面那三个具名函数里（数出来的段数对不上＝有人新加了一段"
               "没归属的脚本，逐段真跑覆盖不到它）")

    def _run_block(code: str, argv, capture=False) -> str:
        old_argv = sys.argv
        buf = io.StringIO()
        try:
            sys.argv = ["<批跑内嵌段>"] + [str(a) for a in argv]
            with contextlib.redirect_stdout(buf):
                exec(compile(code, "run_offline_regression.sh:内嵌段", "exec"), {})
        finally:
            sys.argv = old_argv
        return buf.getvalue() if capture else ""

    fake = Path(tempfile.mkdtemp(prefix="statewatch_"))
    pkg = fake / "b2bcollect_build"
    # 数据目录也在假仓库里造一份：本轮真实污染就发生在这里（riskbird_build/riskbird_data
    # 多出两份「爱企查_结果页证据_*.txt」），而那种文件名是运行时长出来的，代码里根本没有
    # 常量可声明——监视范围只靠「读常量」必然漏掉这一类。
    data = pkg / "b2bcollect_data"
    data.mkdir(parents=True)
    (data / "seen_store_1688.json").write_text("{}", encoding="utf-8")
    # 状态文件名从**代码里的常量**来：夹具故意用一个本轮新造的常量名，它要是没被认出来，
    # 就说明「新加一份状态文件自动进监视范围」这句话没有依据（那样守卫会随改名失效）。
    (pkg / "registry_stub.py").write_text(
        'TASK_INDEX_NAME = "_采集任务索引.json"\nNEW_STATE_NAME = "新加的状态.json"\n'
        'RAW_SUFFIX = ".xlsx"\n', encoding="utf-8")
    idx = pkg / "_采集任务索引.json"
    idx.write_text("[]", encoding="utf-8")
    newone = pkg / "新加的状态.json"
    newone.write_text("{}", encoding="utf-8")
    other_py = pkg / "tool_body.py"
    other_py.write_text("print(1)\n", encoding="utf-8")
    names_txt = fake / "names.txt"
    before_tsv = fake / "before.tsv"
    after_tsv = fake / "after.tsv"

    _run_block(names_code, [fake, names_txt])
    declared = names_txt.read_text(encoding="utf-8")
    _assert("_采集任务索引.json" in declared and "新加的状态.json" in declared,
            f"声明名单从代码常量里扫出来（实际名单：{declared.strip() or '空'}）")
    _assert("tool_body.py" not in declared, "扫的是声明出来的状态文件，不是整仓源码")

    _run_block(snap_code, [fake, before_tsv, names_txt])
    listed = before_tsv.read_text(encoding="utf-8")
    _assert("_采集任务索引.json" in listed and "新加的状态.json" in listed,
            "两份既有状态文件都在监视范围内（含一个新造常量名，靠的是读常量而不是抄清单）")
    _assert("b2bcollect_data/seen_store_1688.json" in listed,
            f"数据目录里现有的文件也要进监视范围（这是本轮新加的口径，漏了它下面那条就抓不到）："
            f"{listed.strip() or '空'}")
    _assert("tool_body.py" not in listed, "监视的是状态文件，不是整仓源码（不然一跑就全是噪音）")

    # 改动判得出：这是「有牙」那一半，前面所有接线断言都不能替代它
    idx.write_text('[{"id":"假任务"}]', encoding="utf-8")
    newone.unlink()                                  # 被删也是改动：监视范围里「不见了」同样要说
    other_py.write_text("print(2)\n", encoding="utf-8")   # 非状态文件的改动不该被算进来
    # 现场证据类污染：运行时长出来的文件名，代码里没有任何常量声明它
    (data / "现场证据_183426.txt").write_text("污染", encoding="utf-8")
    _run_block(snap_code, [fake, after_tsv, names_txt])
    report = _run_block(report_code, [before_tsv, after_tsv], capture=True)
    _assert("_采集任务索引.json" in report,
            f"被改写的状态文件必须点名（实际报告：{report.strip() or '空'}）")
    _assert("新加的状态.json" in report and "跑完不见了" in report,
            f"被删掉的也要点名并说清是哪一种改动（实际报告：{report.strip() or '空'}）")
    _assert("现场证据_183426.txt" in report,
            f"数据目录里**新长出**的一份也要点名（本轮真实污染就是这一类，旧口径按常量名单扫，"
            f"它看不见）：{report.strip() or '空'}")
    _assert("tool_body.py" not in report,
            f"非状态文件的改动不许混进报告（那会把人引去查错方向：{report.strip()}）")

    # 反向对照：不动任何状态文件时报告必须为空。少了这一条，上面三条都可能是在报噪音。
    _run_block(snap_code, [fake, before_tsv, names_txt])
    _run_block(snap_code, [fake, after_tsv, names_txt])
    _assert_eq(_run_block(report_code, [before_tsv, after_tsv], capture=True).strip(), "",
               "状态文件一字未动时报告是空的（不空就说明守卫在自说自话）")
    shutil.rmtree(fake, ignore_errors=True)


def test_errexit_silent_death_gate():
    print("\n═══ Z. set -e 不许把最后一道门安静带走：第 15 条硬门要有牙 ═══")
    # 由来（2026-11-13 本机实发）：Mac 打包脚本的日志停在第 5 步的 ✅ 之后，一行报错都没有
    # ——第 6 道门（真双击验窗）压根没执行，而它正是「用户能不能打开」那最后一问。坏掉的
    # 那一行是：
    #     BEFORE_PIDS=$(pgrep -x B2bCollectTool 2>/dev/null | tr '\n' ' ')
    # 「机器上没有旧实例」才是干净机器的正常情形，pgrep 在这种情形返回 1；pipefail 让整条
    # 管道非零，裸赋值又把这个非零原样交给 set -e，脚本当场结束、零解释。它此前一直「绿」，
    # 只因为那台机器上恰好留着一枚我自己起的旧实例——**判据依赖运气**比没有判据更危险：
    # 它连「去查一下」的动机都不给。本节两头都要验：现状干净（正例）、门抓得住原句（反例）。
    gate = _manifest_gate()
    gate_src = (_REPO / ".github" / "scripts" / "check_packaging_manifest.py").read_text(
        encoding="utf-8")
    build_sh = (_REPO / "debug" / "build_b2bcollect_mac_local.sh").read_text(encoding="utf-8")
    mac_wf = (_REPO / ".github" / "workflows" / "build-b2bcollect-mac.yml").read_text(
        encoding="utf-8")

    # 1) 接线：结果必须并进「不合格」计数，只打印不拦路的那条不算门
    _assert(callable(getattr(gate, "check_shell_errexit_hazards", None)),
            "硬门模块里有第 15 条这个函数（能被单独喂夹具，才谈得上反向对照）")
    _at = gate_src.find("hazards = check_shell_errexit_hazards()")
    _assert(_at > 0, "硬门 main() 里真的调了它（函数在而不调＝这条门从没跑过）")
    _assert("bad += 1" in gate_src[_at:_at + 600],
            "报出来的问题要并进不合格计数并影响退出码")
    _assert("debug/build_b2bcollect_mac_local.sh" in gate.SHELL_SOURCES,
            "出事那份脚本在这条门的登记表里（不在表里的判据等于没判）")

    # 2) 现状：整条交付链干净，且本轮改过的三处保持改后的写法
    _assert_eq(gate.check_shell_errexit_hazards(), [],
               "第 15 条：交付链 shell 里没有会被 set -e 静默带走的裸赋值")
    _assert("BEFORE_PIDS=$(pgrep -x B2bCollectTool 2>/dev/null | tr '\\n' ' ' || true)" in build_sh,
            "事故原句保持修好的样子（|| true 被删掉就是让这道门重新裸奔）")
    _assert("set -Eeuo pipefail" in build_sh and "trap 'rc=$?;" in build_sh,
            "本机打包脚本带 ERR 陷阱：以后再有一句非零把脚本带走，至少留下一行人话")
    for _v in ("before=", "after="):
        _line = next((ln for ln in mac_wf.splitlines() if ln.strip().startswith(_v)), "")
        _assert("|| {" in _line,
                f"Mac workflow 只读冒烟里的 {_v.strip()} 取值必须自己接管非零（裸赋值=这一步会静默消失）")

    # 3) 反向对照：门必须抓得住「原句这一类」，而且只抓这一类
    def _haz(script: str) -> list:
        return gate.check_shell_errexit_hazards(sources=(), workflows=(),
                                                texts=(("夹具", script),))

    _RAW = "BEFORE_PIDS=$(pgrep -x Foo 2>/dev/null | tr '\\n' ' ')\necho hi\n"
    _got = _haz(_RAW)
    _assert_eq(len(_got), 1, "反向对照：裸赋值 + pgrep 必须判红（判不住=这条门是空的）")
    _assert("pgrep" in _got[0] and "第 1 行" in _got[0],
            f"报的要能被直接照做：命令名与行号（实际={_got[0][:90]}）")
    _assert_eq(_haz(_RAW.replace("' ')", "' ') || true)")), [],
               "反向对照：同一句加 || true 就放行（修法是明确的，不是让人删判据）")
    _assert_eq(_haz('before=$(find "$APP" | wc -l) || { echo "数不到"; exit 1; }\n'), [],
               "反向对照：自己止损的写法放行（这种写法已经把「为什么停」说出来了）")
    _assert_eq(_haz('local x=$(pgrep -x Foo)\n'), [],
               "反向对照：local/export 会吞掉替换的退出码（实测不触发 set -e），按口径不报")
    _assert_eq(_haz('echo "看到的 pid：$(pgrep -x Foo)"\n'), [],
               "反向对照：替换出现在别的命令参数里时退出码被外层覆盖，不该报（报了就是噪音）")
    _assert_eq(_haz('# BEFORE_PIDS=$(pgrep -x Foo)\n'), [],
               "反向对照：注释行不算（否则每句解释都要改写成转义，门禁立刻沦为噪音）")
    _got = _haz('set +e\nX=$(pgrep -x Foo)\n')
    _assert_eq(_got, [],
               "口径（2026-11-14 改的）：**第一句**就 `set +e` 是第 28 条要求的立场表态，"
               "这段里裸赋值不会静默死，15a 不适用、15c 也不该把它当「关保护」报红——否则这条门"
               "会逼下一个人在批跑入口里补一句 `set -e`，那才是真把一百多条裸赋值交到 errexit 手上")
    _assert_eq(len(_haz('X=$(pgrep -x Foo)\n')), 1,
               "表态的另一半：同样的裸赋值放在**没表态**的脚本里必须红（默认按调用方给了 -e 判）。"
               "上面那条放行是靠这一条兜住的，两条一起才成立")
    _assert("pgrep" in _haz('X=$(pgrep -x Foo)\n')[0],
            "没表态那一红要能照做（点名命令）")
    for _cmd in ("grep -c ok f", "ls /nope", "diff a b", "command -v nope"):
        _assert_eq(len(_haz(f"V=$({_cmd})\n")), 1,
                   f"覆盖面不止 pgrep：`{_cmd}` 正常情形也返回非零，裸赋值同样要报")

    # 3b) set 行的字母大小写必须都认得：第一版判据写的是 `-[a-z]*e[a-z]*`，本轮把脚本改成
    #     `set -Eeuo pipefail` 之后那一行**整行匹配不上**，第 15 条就在出事那份脚本上静默
    #     空转了——正例照旧 []、夹具照旧红，没有任何一条现象会提醒门已经断。这不是假想：
    #     本节加 15b 时真就这么红了一次。所以把「同一句裸赋值 × 三种开启 errexit 的写法」
    #     钉成判据，认不出哪一种就等于那条门在那种写法下没有跑。
    for _s in ("set -euo pipefail", "set -Eeuo pipefail", "set -o errexit -o pipefail",
               "set -e -u -o pipefail"):
        _assert_eq(len(_haz(f"{_s}\n{_RAW}")), 1,
                   f"反向对照：`{_s}` 都算开了 errexit，裸赋值那一句必须报"
                   f"（认不出来=这条门在那种写法下从没跑过）")
    _got = _haz("set +o errexit\n" + _RAW)
    _assert_eq(_got, [],
               "反向对照：长写法的第一句表态和短写法同口径（`set +o errexit` 也算表态），"
               "只认 `set +e` 的字面会逼出一份「为了过门而写的假表态」")
    _got = _haz("set -Eeuo pipefail\nfoo\nset +o errexit\n" + _RAW)
    _assert_eq(len(_got), 1,
               "反向对照：先把保护开起来、中段再用长写法关掉——15c 必须认得 "
               "`set +o errexit` 这种拼法并报红（实际=%s）" % (_got[:1],))
    _assert("整段的 errexit" in _got[0],
            f"报的要说是哪一行关的保护（实际={_got[0][:80]}）")

    # 3e) 15c：`set +e … set -e` 那种整段关保护的区间一律判红，接管单条命令的两种写法
    #     必须放行——不然这条门会在真正需要收退出码的地方变成噪音，而噪音的下场是被关掉。
    for _txt, _want, _why in (
        ("set -Eeuo pipefail\nset +e\nfoo\nset -e\n", 1, "整段关保护要判红"),
        ("set -Eeuo pipefail\nfoo || rc=$?\n", 0, "只接管这一条就放行"),
        ('set -Eeuo pipefail\nmake | tee "$LOG" || rc=${PIPESTATUS[0]}\n', 0,
         "管道里取首段退出码的写法同样放行（Windows 那一步用的就是这个）"),
        ("set -Eeuo pipefail\n# 这里不要用 set +e\n", 0, "注释里提到不算（否则解释都得删掉）"),
    ):
        _got = _haz(_txt)
        _assert_eq(len(_got), _want, f"反向对照：15c 对{_why}（实际={_got[:1]}）")

    # 3c) 15b：装了 ERR 陷阱，脚本却让它没有触发的机会（症状与「日志到此为止」一模一样，
    #     但脚本看上去像处理过了，比什么都没有更危险）。两种坏样子各判一次红，两种好写法
    #     各放行一次；`trap - ERR`（摘掉）、注释里的、非 ERR 信号的都不算装了陷阱。
    for _txt, _want, _why in (
        ("set -euo pipefail\ntrap 'echo boom' ERR\n", 1, "装了陷阱却没有 -E（函数里的非零不响）"),
        ("set -Eeuo pipefail\ntrap 'echo boom' ERR\n", 0, "补了 -E 就放行（修法明确）"),
        ("set -euo pipefail\nset -o errtrace\ntrap 'echo boom' ERR\n", 0, "长写法同样算数"),
        ("set -euo pipefail\ntrap - ERR\n", 0, "trap - ERR 是摘掉陷阱，不是装"),
        ("set -euo pipefail\n# trap 'echo boom' ERR\n", 0, "注释里的 trap 不算"),
        ("set -euo pipefail\ntrap 'echo bye' EXIT\n", 0, "非 ERR 信号不在本条口径内"),
    ):
        _got = _haz(_txt)
        _assert_eq(len(_got), _want, f"反向对照：{_why}（实际={_got[:1]}）")
    _assert(any("-E" in p for p in _haz("set -euo pipefail\ntrap 'echo boom' ERR\n")),
            "报的话要能直接照做：点名补 -E")

    # 3d) 「装了陷阱却永远不会响」与「装了陷阱但只盖住脚本主体那一层」是两种坏样子，
    #     分别由**明确 `set +e`** 和**从头到尾没表态**引出来。后半句是本轮重立默认口径之后
    #     改的：调用方的 `-e` 会被 bash 交给子脚本（第 28 条整条就是这一件事），所以「脚本
    #     自己没写 set」不能再推出「这一段没有 errexit」——没表态的那份按 -e 生效判，报的
    #     是「缺 -E」而不是「没有一段在 -e 下跑」。两条都留在夹具里跑真文件，是为了让
    #     「默认口径变了」这件事必须在判据上留下痕迹，而不是只改一句注释。
    #     这一格必须落到真文件上跑一遍登记表（texts 那侧只喂得出一种默认）。夹具名不许只差
    #     大小写（noe/noE 那种）：macOS 与 Windows 的默认文件系统大小写不敏感，两个名字是
    #     **同一个文件**，后写的把先写的盖掉——本轮实测于是把「没开 -e」那条判成「缺 -E」，
    #     红得莫名其妙，而 Linux CI 上永远不会出现这个症状。
    _shfx = {
        "debug/zz_errtrap_setplus.sh": "set +e\ntrap 'echo boom' ERR\npgrep -x Foo\n",
        "debug/zz_errtrap_noset.sh": "trap 'echo boom' ERR\npgrep -x Foo\n",
        "debug/zz_errtrap_noerrtrace.sh": "set -euo pipefail\ntrap 'echo boom' ERR\n",
        "debug/zz_errtrap_ok.sh": "set -Eeuo pipefail\ntrap 'echo boom' ERR\n",
    }
    for _rel, _src in _shfx.items():
        (_REPO / _rel).write_text(_src, encoding="utf-8")
    try:
        _got = gate.check_shell_errexit_hazards(sources=("debug/zz_errtrap_setplus.sh",),
                                                workflows=())
        _assert(any("没有任何一段在 -e 下跑" in p for p in _got),
                f"反向对照：明确 set +e 的脚本装了 ERR 陷阱要判红（实际={_got}）")
        _got = gate.check_shell_errexit_hazards(sources=("debug/zz_errtrap_noset.sh",),
                                                workflows=())
        _assert(any("-E" in p for p in _got)
                and not any("没有任何一段在 -e 下跑" in p for p in _got),
                f"默认口径：全程没表态的脚本按 -e 生效判，报「缺 -E」而不是「陷阱不会响」"
                f"（实际={_got}）")
        _got = gate.check_shell_errexit_hazards(sources=("debug/zz_errtrap_noerrtrace.sh",),
                                                workflows=())
        _assert(any("-E" in p for p in _got),
                f"反向对照：登记表里读到的脚本，缺 -E 同样判红（实际={_got}）")
        _assert_eq(gate.check_shell_errexit_hazards(sources=("debug/zz_errtrap_ok.sh",),
                                                    workflows=()), [],
                   "反向对照：同一份补上 -E 就干净（否则这条门只会一直响）")
    finally:
        for _rel in _shfx:
            (_REPO / _rel).unlink(missing_ok=True)

    # 4) 行为侧：上面那些都是「我认为 bash 会怎么走」。判据依赖的这一条推断本身也要有证据
    #    ——真起 bash 跑一遍：旧写法必须 rc≠0 且**第二句 echo 不出现**（这才是「门安静消失」
    #    的现实），修好的写法必须 rc=0 且两句都在。
    #    取法走第 35 条那个把手：上一版这里是 `shutil.which("bash")`，而上面四处是裸名字，
    #    同一份用例里两种取法在 Windows 上会拿到两个不同的程序（那正是本轮云端红话自相
    #    矛盾的来源），行为侧测的就不是要被判的那一个 bash 了。
    _argv, _bash_note = _bash_argv()
    bash = _argv[0] if _argv else ""
    if not bash:
        print(f"   ⏭ 这台机器上没有 bash（{_bash_note}），行为侧跳过（静态那几条照跑）；"
              "第 11/35 条会因缺 bash 判红，所以交付链上不会出现「静默跳过的验窗」")
    else:
        _tmp = Path(tempfile.mkdtemp(prefix="errexit_fixture_"))
        try:
            _old = _tmp / "old.sh"
            _old.write_text("set -Eeuo pipefail\n"
                            "echo STEP1\n"
                            "X=$(pgrep -x DefinitelyNoSuchProcZZZ 2>/dev/null | tr '\\n' ' ')\n"
                            "echo STEP2\n", encoding="utf-8")
            _new = _tmp / "new.sh"
            # 整段写死而不是对 old.sh 做一次 str.replace：上一版这里用 replace 补 `|| true`，
            # 补出来的是一份**语法就有错**的脚本（rc=2，STEP2 一样不出现），行为侧照旧「红得
            # 很有道理」。正反两份夹具必须只差那一处写法，差在别处就是在验另一件事。
            _new.write_text("set -Eeuo pipefail\n"
                            "echo STEP1\n"
                            "X=$(pgrep -x DefinitelyNoSuchProcZZZ 2>/dev/null | tr '\\n' ' ' || true)\n"
                            "echo STEP2\n", encoding="utf-8")
            # 夹具自己先过一遍解析器：语法错的「反例」也会让第二句不出现，那种 rc≠0 说明不了
            # set -e 的任何事——本条要证的是「合法脚本会被非零赋值带走」，前提就得是它合法。
            for _p in (_old, _new):
                _chk = subprocess.run([bash, "-n", str(_p)], capture_output=True, timeout=20)
                _assert_eq(_chk.returncode, 0,
                           f"夹具 {_p.name} 自己得是一份能解析的脚本"
                           f"（{(_chk.stderr.decode('utf-8', errors='replace').strip()[:60])}）")
            _res = {}
            for _tag, _p in (("old", _old), ("new", _new)):
                try:
                    _cp = subprocess.run([bash, str(_p)], capture_output=True, timeout=20)
                    _res[_tag] = (_cp.returncode,
                                  _cp.stdout.decode("utf-8", errors="replace"))
                except subprocess.TimeoutExpired:
                    _assert(False, f"夹具 {_tag}.sh 20 秒没返回：这条行为判据拿不到结论")
            _assert(_res["old"][0] != 0 and "STEP2" not in _res["old"][1],
                    f"旧写法真的会把后面的步骤吃掉（rc={_res['old'][0]}，"
                    f"输出={_res['old'][1].strip()!r}）——这条门要拦的就是这个")
            _assert(_res["new"][0] == 0 and "STEP1" in _res["new"][1] and "STEP2" in _res["new"][1],
                    f"修法真的管用：rc={_res['new'][0]}，输出={_res['new'][1].strip()!r}")
            # 15b 的同一套：判据要 -E 不是洁癖，这里拿真 bash 作证——函数里的那一次非零，
            # 只有开了 errtrace 才会走到陷阱。两份夹具只差 set 行的一个字母。
            _trap_body = ("{setline}\n"
                          "trap 'echo TRAP-FIRED' ERR\n"
                          "f() {{ pgrep -x DefinitelyNoSuchProcZZZ > /dev/null; }}\n"
                          "f\n"
                          "echo STEP2\n")
            _tres = {}
            for _tag, _setline in (("noE", "set -euo pipefail"), ("E", "set -Eeuo pipefail")):
                _p = _tmp / f"trap_{_tag}.sh"
                _p.write_text(_trap_body.format(setline=_setline), encoding="utf-8")
                _chk = subprocess.run([bash, "-n", str(_p)], capture_output=True, timeout=20)
                _assert_eq(_chk.returncode, 0, f"夹具 trap_{_tag}.sh 自己得能被解析")
                try:
                    _cp = subprocess.run([bash, str(_p)], capture_output=True, timeout=20)
                    _tres[_tag] = (_cp.returncode,
                                   _cp.stdout.decode("utf-8", errors="replace"))
                except subprocess.TimeoutExpired:
                    _assert(False, f"夹具 trap_{_tag}.sh 20 秒没返回：这条行为判据拿不到结论")
            _assert(_tres["noE"][0] != 0 and "TRAP-FIRED" not in _tres["noE"][1],
                    f"行为证据：没有 -E 时函数里的非零**不会**走到 ERR 陷阱"
                    f"（rc={_tres['noE'][0]}，输出={_tres['noE'][1].strip()!r}）"
                    f"——脚本看上去像处理过了，出事时却一行话都不留")
            _assert(_tres["E"][0] != 0 and "TRAP-FIRED" in _tres["E"][1],
                    f"行为证据：只差一个字母，同一处非零就留下一行人话"
                    f"（rc={_tres['E'][0]}，输出={_tres['E'][1].strip()!r}）")
            # 15c 的放行口径也要有**真 bash 的**证据，不能靠我对 pipefail 的印象。
            # 2026-11-12 本机实测（bash 3.2.57）把两件我原先说反的事钉下来：
            #   · pipefail 取的是**最右非零**，不是「最后一个命令的状态」：编译器退 7、
            #     tee 成功时，`|| rc=$?` 拿到的同样是 7（我先前断言它会「把 7 说成 0」是错的）。
            #   · `${PIPESTATUS[0]}` 真正的价值是**归因**：tee 自己坏（日志路径不合法，
            #     Windows 上 cygpath/MSYS 转换就可能坏）时，`$?` 会把 tee 的 1 说成
            #     「makensis 编译失败（退出码 1）」——编译器明明成功，红了一条假话；
            #     PIPESTATUS[0] 拿到的是首段的 0，不冤枉编译器。
            #   · 反过来 PIPESTATUS 也**代替不了** pipefail：没开 pipefail 时整条管道只看
            #     tee，tee 成功就压根不进 `||` 分支，两种写法都留不下非零。
            # 这四条一起构成本步写法该往哪个方向修的根据，所以逐条跑真 bash。
            _pipes = {
                # 首段坏（编译器退 7）、tee 正常：PIPESTATUS 拿得到 7 且脚本继续跑
                "first": ('set -Eeuo pipefail\nrc=0\n(echo LINE; exit 7) | tee "$OUT" '
                          '|| rc=${PIPESTATUS[0]}\necho "rc=$rc"\necho STEP2\n'),
                # 同一条管道换成 $?：pipefail 取最右非零，同样是 7（不许再按「$? 只看最后一个
                # 命令」的错误口径写判据或写注释）
                "whole": ('set -Eeuo pipefail\nrc=0\n(echo LINE; exit 7) | tee "$OUT" '
                          '|| rc=$?\necho "rc=$rc"\necho STEP2\n'),
                # 只有 tee 坏、编译器成功：$? 会把成功说成失败（假红 + 一句谎话）
                "blame_ok": ('set -Eeuo pipefail\nrc=0\n(echo LINE; exit 0) | tee "$BAD" '
                             '|| rc=$?\necho "rc=$rc"\necho STEP2\n'),
                # 同一处用 PIPESTATUS[0]：首段是 0，不替 tee 背锅，也不拦住后面的检查
                "blame_real": ('set -Eeuo pipefail\nrc=0\n(echo LINE; exit 0) | tee "$BAD" '
                               '|| rc=${PIPESTATUS[0]}\necho "rc=$rc"\necho STEP2\n'),
                # 没开 pipefail 时，PIPESTATUS 也救不回来（tee 成功 → 不进 || 分支）
                "nopipefail": ('set -Eeu\nrc=0\n(echo LINE; exit 7) | tee "$OUT" '
                               '|| rc=${PIPESTATUS[0]}\necho "rc=$rc"\necho STEP2\n'),
            }
            _pres = {}
            for _tag, _body in _pipes.items():
                _p = _tmp / f"pipe_{_tag}.sh"
                _p.write_text(_body, encoding="utf-8")
                _chk = subprocess.run([bash, "-n", str(_p)], capture_output=True, timeout=20)
                _assert_eq(_chk.returncode, 0, f"夹具 pipe_{_tag}.sh 自己得能被解析")
                try:
                    _cp = subprocess.run([bash, str(_p)], capture_output=True, timeout=20,
                                         env={**os.environ,
                                              "OUT": str(_tmp / f"out_{_tag}.txt"),
                                              # 不存在的目录里的文件名：tee 必失败（退出码 1）
                                              "BAD": str(_tmp / "nope" / "x.log")})
                    _pres[_tag] = (_cp.returncode,
                                   _cp.stdout.decode("utf-8", errors="replace"))
                except subprocess.TimeoutExpired:
                    _assert(False, f"夹具 pipe_{_tag}.sh 20 秒没返回：这条行为判据拿不到结论")

            def _rc_of(_tag):
                """夹具自己打印的那句 rc=（脚本本身退出码恒 0，判据只能读这句）"""
                _m = re.search(r"^rc=(\d+)$", _pres[_tag][1], re.M)
                _assert(_m, f"夹具 pipe_{_tag}.sh 没打印 rc=（输出={_pres[_tag][1].strip()!r}）："
                            f"这条行为判据拿不到结论")
                return int(_m.group(1))

            _assert(_pres["first"][0] == 0 and _rc_of("first") == 7
                    and "STEP2" in _pres["first"][1],
                    f"行为证据：`|| rc=${{PIPESTATUS[0]}}` 拿到首段的 7 且脚本继续跑"
                    f"（rc={_pres['first'][0]}，输出={_pres['first'][1].strip()!r}）")
            _assert(_rc_of("whole") == 7,
                    f"行为证据：pipefail 取的是**最右非零**，同一条管道用 `|| rc=$?` 也是 7"
                    f"（实测输出={_pres['whole'][1].strip()!r}）——判据与注释都不许再写成"
                    f"「$? 只看最后一个命令，所以会把编译器的失败说成成功」")
            _assert(_rc_of("blame_ok") != 0,
                    f"行为证据：编译器明明成功（退 0），只有 tee 坏时 `$?` 也把整条管道判成失败"
                    f"（实测输出={_pres['blame_ok'][1].strip()!r}）——那句 ::error:: 会说"
                    f"「makensis 编译失败」，红了一条谎话")
            _assert(_rc_of("blame_real") == 0,
                    f"行为证据：同一处用 PIPESTATUS[0] 拿到的是首段的 0"
                    f"（实测输出={_pres['blame_real'][1].strip()!r}）——这才是放行它而不是"
                    f"放行 $? 的理由：**归因要归到编译器那一段**")
            _assert(_rc_of("nopipefail") == 0 and "STEP2" in _pres["nopipefail"][1],
                    f"行为证据：PIPESTATUS 代替不了 pipefail——没开它时 tee 成功就不进 || 分支，"
                    f"编译器的 7 谁都没看见（实测输出={_pres['nopipefail'][1].strip()!r}）")
        finally:
            shutil.rmtree(_tmp, ignore_errors=True)

    # 5) workflow 的默认值口径：Actions 的 bash 步本身就带 -e，所以同一句裸赋值在 workflow
    #    里也必须报；pwsh 步没有这套语义，拿它去报就是一条永远修不掉的假红（同第 11 条）。
    def _wf(body: str, shell: str = "bash") -> str:
        _s = f"\n{' ' * 6}shell: {shell}" if shell else ""
        _b = "\n".join(" " * 8 + ln for ln in body.rstrip("\n").splitlines())
        return (f"jobs:\n  b:\n    runs-on: macos-latest\n    steps:\n"
                f"    - name: 被测的那一步{_s}\n      run: |\n{_b}\n")

    _fx = {"debug/zz_errexit_bash.yml": _wf("pids=$(pgrep -x Foo | tr '\\n' ' ')\n"),
           "debug/zz_errexit_pwsh.yml": _wf("$p = Get-Process", shell="pwsh")}
    for _rel, _src in _fx.items():
        (_REPO / _rel).write_text(_src, encoding="utf-8")
    try:
        _got = gate.check_shell_errexit_hazards(sources=(),
                                                workflows=("debug/zz_errexit_bash.yml",))
        _assert_eq(len(_got), 1,
                   "反向对照：workflow 的 bash 步默认就是 -e，同一句裸赋值在这里也要报")
        # 行号按**文件里**的那一行算：块内偏移不换算，人照着判据去翻就翻到隔壁行上去
        # （这份夹具里 `run:` 上面还有一行 shell:，写死一个数字第一次就指错）。
        _want_line = _fx["debug/zz_errexit_bash.yml"].splitlines().index(
            "        pids=$(pgrep -x Foo | tr '\\n' ' ')") + 1
        _assert(f"第 {_want_line} 行" in _got[0],
                f"报的要是文件里那一行的行号（实际={_got[0][:100]}，期望含「第 {_want_line} 行」）")
        _assert_eq(gate.check_shell_errexit_hazards(
            sources=(), workflows=("debug/zz_errexit_pwsh.yml",)), [],
            "pwsh 步不套 bash 的语义（假红会让这条门被人整个关掉）")
    finally:
        for _rel in _fx:
            (_REPO / _rel).unlink(missing_ok=True)


_FIXTURE_PATH = re.compile(r"[\"'](debug/zz_[A-Za-z0-9_.-]+\.[A-Za-z]{1,5})[\"']")


def _fixture_name_collisions(names=None) -> list:
    """把一批夹具路径按「大小写不敏感文件系统上的同一个文件」分组，报出撞车的组。

    比较键**直接用硬门第 16 条那一个函数**（不在这儿再抄一份口径）：判据说一套、夹具按
    另一套，下次撞车就又是一条「本机红、云端绿」的谜案——本节的存在理由就是这一类。
    口径边界：只看源码里写死的夹具名；动态拼出来的（f"debug/zz_{tag}.sh"）不在这里，
    那种地方要靠命名本身把用途写全。
    """
    if names is None:
        names = _FIXTURE_PATH.findall(Path(__file__).read_text(encoding="utf-8"))
    key = _manifest_gate().fs_insensitive_key
    groups = {}
    for _n in sorted(set(names)):
        groups.setdefault(key(_n), set()).add(_n)
    return [", ".join(sorted(_v)) for _v in groups.values() if len(_v) > 1]


def test_case_insensitive_filesystem_gate():
    print("\n═══ AA. 同一份仓库在 mac/Windows 上得检出同一棵树：第 16 条硬门 ═══")
    # 由来（2026-11-13 本机实发）：Z 节加反向对照时写了 `zz_errexit_trap_noe.sh` 与
    # `zz_errexit_trap_noE.sh` 两个夹具，本意两份、mac 上是一份（APFS 默认大小写不敏感），
    # 后写的静默盖掉先写的，于是「没开 -e」那条判据读到的是「没开 -E」那份内容，红了一句
    # 与现场无关的话。Linux CI 大小写敏感，同一份用例在云端永远全绿——这类缺陷的形状就是
    # 「只在开发机出现，而且长得像判据自己写错了」。仓库层面的同一形状更贵：`Foo.py` 与
    # `foo.py` 同时入库时，Windows/macOS 检出的那棵树里只有一个文件，装进包的就是最后
    # 落盘的那一份，而 CI 不会为这件事变红一次。
    gate = _manifest_gate()
    gate_src = (_REPO / ".github" / "scripts" / "check_packaging_manifest.py").read_text(
        encoding="utf-8")

    # 1) 接线：函数在、main() 真调、结果进不合格计数
    _assert(callable(getattr(gate, "check_case_colliding_paths", None)),
            "硬门模块里有第 16 条这个函数（能单独喂合成清单，才谈得上反向对照）")
    _at = gate_src.find("collide = check_case_colliding_paths()")
    _assert(_at > 0, "硬门 main() 里真的调了它（函数在而不调＝这条门从没跑过）")
    _assert("bad += 1" in gate_src[_at:_at + 700],
            "报出来的撞车要并进不合格计数并影响退出码")

    # 2) 现状：仓库干净，本套件的夹具名也不撞车
    _assert_eq(gate.check_case_colliding_paths(), [],
               "第 16 条：入库路径里没有两份只在大小写/Unicode 变体上不同的路径")
    _assert_eq(_fixture_name_collisions(), [],
               "本套件写死的夹具名两两不撞车（撞了就是在读隔壁那一节的内容）")

    # 3) 反向对照：这一类要判得住，而相邻的几种「看着像」不能误报
    for _paths, _want, _why in (
        (("a/Foo.py", "a/foo.py"), 1, "只差大小写"),
        (("a/Foo.py", "a/FOO.py"), 1, "全大写同样算撞车"),
        (("caf\u00e9.py", "cafe\u0301.py"), 1, "同一个字的预组合与分解两种写法（mac 的文件名规则）"),
        (("a/Foo.py", "b/Foo.py"), 0, "不同目录不是同一个文件"),
        (("a/foo.py", "a/foo.txt"), 0, "扩展名不同不是同一个文件"),
        (("a/Foo.py",), 0, "孤零零一份不该响（只有成对才算撞车）"),
    ):
        _got = gate.check_case_colliding_paths(paths=list(_paths))
        _assert_eq(len(_got), _want, f"反向对照：{_why}（实际={_got}）")
    _assert(any("同一个文件" in p for p in
                gate.check_case_colliding_paths(paths=["a/Foo.py", "a/foo.py"])),
            "报的话要说清「在 mac/Windows 上是同一个文件」，不然下一个人会以为在说重名提交")

    # 4) 行为侧：这台机器的文件系统到底敏不敏感，不靠猜——真写一份再按另一个名字读。
    #    判据本身不许依赖这个结论（合成清单永远按同一把键判），这里要的是「下一来看见
    #    自己机器属于哪一类」，以及第 16 条的键和 OS 规则对得上。
    _tmp = Path(tempfile.mkdtemp(prefix="casefs_"))
    try:
        (_tmp / "probe.sh").write_text("LOWER", encoding="utf-8")
        _insensitive = False
        try:
            _insensitive = (_tmp / "PROBE.SH").read_text(encoding="utf-8") == "LOWER"
        except OSError:
            _insensitive = False
        if _insensitive:
            (_tmp / "PROBE.SH").write_text("UPPER", encoding="utf-8")
            _assert((_tmp / "probe.sh").read_text(encoding="utf-8") == "UPPER",
                    "本机大小写不敏感：按大写名写入改的就是那份小写文件（夹具撞车的现实形状）")
            _assert_eq(len(list(_tmp.iterdir())), 1, "本机确实只有一个 inode（两个名字一份内容）")
        else:
            print("   · 本机文件系统大小写敏感（Linux CI 就是这样）——云端全绿而开发机红的那一类"
                  "在这里测不出来，所以第 16 条只能按名字判、不能靠写文件试")
        # 键函数与 OS 规则的一致性：这一条在两种机器上都成立（判的是字符串口径，不是本机 FS）
        _assert(gate.fs_insensitive_key("probe.sh") == gate.fs_insensitive_key("PROBE.SH"),
                "第 16 条的比较键必须把只差大小写的两个名字当成同一个")
        # 两个名字必须**真的**是两种字节（写成转义就是为了防有人把它重打成一个字符：
        # 那样下面两条会双双「通过」，而这一类里最难的 NFD 那一半已经没人判了）。
        _nfc, _nfd = "caf\u00e9.py", "cafe\u0301.py"
        _assert(_nfc != _nfd and len(_nfc) != len(_nfd),
                f"夹具本身得真的是两种写法（否则这条判据在自证一件不存在的事：{_nfc!r} vs {_nfd!r}）")
        _assert(gate.fs_insensitive_key(_nfc) == gate.fs_insensitive_key(_nfd),
                "比较键必须把预组合与分解两种写法当成同一个（mac 的文件名规则就是这样）")
        _assert(gate.fs_insensitive_key("a/Foo.py") != gate.fs_insensitive_key("b/Foo.py"),
                "不同目录不能被归一成同一个名字（否则第 16 条会报一堆修不掉的假红）")
    finally:
        shutil.rmtree(_tmp, ignore_errors=True)


def test_pip_source_resilience_gate():
    print("\n═══ AB. 交付链不许挂在一个外部门口：第 18 条硬门（冗余 + 清单覆盖）═══")
    # 由来（2026-11-13 云端实发）：push 之后 Mac 与 Windows 两个 run 同时红，失败步都是
    # `Install dependencies`。日志里的字节是：
    #     Looking in indexes: https://pypi.tuna.tsinghua.edu.cn/simple
    #     5 次 ReadTimeout 重试耗尽
    #     ERROR: No matching distribution found for PyQt5 (from versions: none)
    # 那句话一个字都没提「源不通」，读日志的人先怀疑的是依赖写错了——而**代码没改坏、
    # 构建脚本没写错**，交付链只是挂在了一个外部门口。四份 workflow 当时全都写成
    # `pip install <一长串包名> -i 清华源`：一个镜像的可用性 = 整条交付链的单点；两份
    # b2bcollect 的包名清单还各自抄一份，靠注释「逐行对齐」人肉同步。
    # 大厂在这一格的做法只有两条：冗余（多源顺序回退）+ 有界（每个源都有尽头）。安装逻辑
    # 因此收进 .github/scripts/pip_install.py，清单收进 .github/requirements/*.txt。
    # 本节按三条线验：判据本身（文本结构）、判据的牙（反向对照必须红）、**实现真的那么跑**
    # （拿假 pip 真起子进程，验回退顺序、早停、有界超时、全挂才非零、命令行真的带上
    # --timeout/--retries/--no-input、stdin 真的拿到 EOF）。只看文本的「门」和没有门一样
    # 危险——它报绿的时候你分不清是安装器对，还是判据没看懂安装器。
    gate = _manifest_gate()
    gate_src = (_REPO / ".github" / "scripts" / "check_packaging_manifest.py").read_text(
        encoding="utf-8")
    installer_path = _REPO / ".github" / "scripts" / "pip_install.py"
    installer_src = installer_path.read_text(encoding="utf-8")

    # 1) 接线：两路判据都在 main() 里被调用，且结果并进不合格计数
    for fn in ("check_pip_source_resilience", "check_requirements_coverage"):
        _assert(callable(getattr(gate, fn, None)),
                f"硬门模块里有 {fn}（能被单独喂夹具，才谈得上反向对照）")
    _at = gate_src.find("pip_problems = check_pip_source_resilience()")
    _assert(_at > 0, "硬门 main() 里真的调了第 18 条（函数在而不调＝这条门从没跑过）")
    _assert("bad += 1" in gate_src[_at:_at + 1200],
            "报出来的问题要并进不合格计数并影响退出码")
    _assert("pip_problems += check_requirements_coverage()" in gate_src[_at:_at + 1200],
            "18e（清单覆盖进包源码的第三方 import）接在同一段判定里，不能只挂个函数名")
    _assert(installer_path.is_file(), "pip_install.py 在位（workflow 引用的实现本体）")
    for rel in gate.WORKFLOWS:
        _assert((_REPO / rel).is_file(), f"{rel} 在位：第 18 条登记了它却读不到文件，等于没判")
    for name in ("b2bcollect.txt", "riskbird.txt", "build-1688.txt"):
        _assert((_REPO / ".github" / "requirements" / name).is_file(),
                f"依赖清单 {name} 在位：清单不在位时安装器会红，但红在构建中途比红在门禁晚一步")

    # 2) 现状：四份 workflow + 安装器 + 清单都合格（这条红的时候，就是有人把单源又写回去）
    _assert_eq(gate.check_pip_source_resilience(), [],
               "第 18 条：交付链没有单点索引源、没有绕开安装器、清单都在位")
    _assert_eq(gate.check_requirements_coverage(), [],
               "第 18 条 e 路：进包源码 import 到的第三方包全在依赖清单里")
    _assert_eq(gate.check_pip_source_resilience(workflows=gate.WORKFLOWS), [],
               "同一判据按登记的 workflow 全量再跑一遍（默认参数与显式参数结果必须一致）")

    # 3) 自证：正例不是「什么都没扫所以全绿」。四条放行路线——pip 名≠import 名、由清单里
    #    某发行包连带提供、仓库内目录包、名字直接对上——都必须真的在当前这份代码上被走过。
    #    缺这一格，下一节新加的门即使把整张表读错也会报 ✅（本轮已经栽过五次这一类）。
    files = gate._packaged_source_files()
    _assert(len(files) >= 40,
            f"进包源码口径真的展开了（{len(files)} 个文件）：扫 0 个文件的「全覆盖」是自欺")
    names = {n for p in files for n in gate._top_level_imports(p)}
    dists = gate._requirement_dists(_REPO / gate.B2B_REQUIREMENTS)
    _alias = sorted(n for n in gate.IMPORT_TO_DIST if n in names and n.lower() not in dists)
    _assert(len(_alias) >= 3,
            f"正例走的必须包含「pip 名≠import 名」这一格：至少三条别名要真的在代码里出现、"
            f"且清单里写的是另一个名字（实际在用的={_alias}；表里全部={sorted(gate.IMPORT_TO_DIST)}）"
            f"——表里用不上的那几条不是缺陷，它们是给下一个加依赖的人留的路标")
    _assert(any(n in names and n not in dists and gate.IMPORT_FROM_LISTED.get(n)
                and gate._dist_key(gate.IMPORT_FROM_LISTED[n][0]) in dists
                for n in gate.IMPORT_FROM_LISTED),
            "正例走的必须包含「由清单里某发行包连带提供」这一格（否则这张表从没被执行过）")
    _dirpkg = sorted(n for n in names
                     if n not in dists and (_REPO / n / "__init__.py").is_file())
    _assert(_dirpkg,
            f"正例走的必须包含「仓库内目录包随 --add-data 进包」这一格（实际={_dirpkg}）")
    _direct = sorted(n for n in names if n.lower() in dists)
    # 这一格的职责是「扫到的不是一片空」，不是「必须是 6 条」：2026-11-19 需求 5 减重第一刀
    # 把 enhanced_cleaner 自测块里的 `import pandas` 换成 openpyxl 之后，这一列从 6 条降到
    # 5 条——降得对，正是这一刀想要的结果（pandas 曾从顶层 import 走进依赖对拍）。
    # 下限留 5 而不是改成 == 5：写死等值会让下一次正常加依赖也红，那只会教人把门改松。
    _assert(len(_direct) >= 5,
            f"直接同名的第三方 import 也扫到了一大片（{len(_direct)} 条，实际={_direct}）")

    _tmp = Path(tempfile.mkdtemp(prefix="pipgate_"))
    try:
        # ── 4) workflow 侧反向对照：七种坏写法各红一次，好写法放行 ──────────────
        def _wf(rel: str, body_lines: tuple) -> str:
            """拼一份最小 workflow 夹具：只有一个 run 步，脚本体就是传进来的那几行。"""
            p = _tmp / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(
                "name: fixture\non:\n  push:\n    branches: [main]\njobs:\n  build:\n"
                "    runs-on: ubuntu-latest\n    steps:\n      - name: Install dependencies\n"
                "        run: |\n"
                + "".join(f"          {ln}\n" for ln in body_lines), encoding="utf-8")
            return str(p)

        _REQ_OK = ".github/requirements/b2bcollect.txt"      # 仓库里那份真清单
        _good = _wf("good.yml", ("python .github/scripts/pip_install.py --upgrade pip",
                                 f"python .github/scripts/pip_install.py -r {_REQ_OK}"))
        _assert_eq(gate.check_pip_source_resilience(workflows=(_good,), installer_text=installer_src),
                   [], "正例：夹具里这两行（升级 pip + 按清单安装）就是当前链路的写法，必须放行")
        # a) 事故原句：直接 pip install 挂在单个镜像上
        _one = _wf("single.yml",
                   ("pip install PyQt5 selenium -i https://pypi.tuna.tsinghua.edu.cn/simple",))
        _got = gate.check_pip_source_resilience(workflows=(_one,), installer_text=installer_src)
        _assert_eq(len(_got), 2,
                   f"反向对照：单源直装要同时报「直接调用 pip」与「没调用安装器」两条（实际={_got}）")
        _assert(any("直接调用 pip" in x for x in _got) and any("没有调用" in x for x in _got),
                f"报的两条要各自可照做（实际={_got}）")
        # b) 调了安装器，但 -r 指向仓库里没有的清单
        _miss = _wf("missing.yml", ("python .github/scripts/pip_install.py "
                                    "-r .github/requirements/nope_fixture.txt",))
        _got = gate.check_pip_source_resilience(workflows=(_miss,), installer_text=installer_src)
        _assert_eq(len(_got), 1, f"反向对照：清单不在仓库里要恰好报一条（实际={_got}）")
        _assert("不在仓库里" in _got[0], f"报的必须是清单缺席（实际={_got[0][:60]}）")
        # c) 清单在，但落在 requirements/ 之外：两个平台读同一份这件事就没人判得得了
        _elsewhere = _tmp / "misc" / "deps_elsewhere.txt"
        _elsewhere.parent.mkdir(parents=True, exist_ok=True)
        _elsewhere.write_text("requests\n", encoding="utf-8")
        _loc = _wf("location.yml", (f"python .github/scripts/pip_install.py -r {_elsewhere}",))
        _got = gate.check_pip_source_resilience(workflows=(_loc,), installer_text=installer_src)
        _assert_eq(len(_got), 1, f"反向对照：清单落点散开要恰好报一条（实际={_got}）")
        _assert("requirements" in _got[0], f"报的必须是落点（实际={_got[0][:60]}）")
        # d) 空清单：只有注释。装得出个寂寞，产物缺包却在用户点上才崩
        _empty_dir = _tmp / "requirements"
        _empty_dir.mkdir(parents=True, exist_ok=True)
        (_empty_dir / "deps_empty.txt").write_text("# 还没写包名\n", encoding="utf-8")
        _em = _wf("empty.yml", (f"python .github/scripts/pip_install.py "
                                f"-r {_empty_dir / 'deps_empty.txt'}",))
        _got = gate.check_pip_source_resilience(workflows=(_em,), installer_text=installer_src)
        _assert_eq(len(_got), 1, f"反向对照：空清单要恰好报一条（实际={_got}）")
        _assert("一个包都没有" in _got[0], f"报的必须是清单内容为空（实际={_got[0][:60]}）")
        # e) 清单路径由 Actions 表达式现拼：判据确认不了文件在不在，等于这一路没判
        _expr = _wf("expr.yml", (r"python .github/scripts/pip_install.py "
                                 r"-r .github/requirements/${{ matrix.os }}.txt",))
        _got = gate.check_pip_source_resilience(workflows=(_expr,), installer_text=installer_src)
        _assert_eq(len(_got), 1, f"反向对照：运行时才拼出来的清单路径要恰好报一条（实际={_got}）")
        _assert("运行时才拼出来" in _got[0], f"报的必须是路径不可静态确认（实际={_got[0][:60]}）")
        # f) 整条链压根不调安装器（新加一个 workflow 时最容易忘的一步）
        _none = _wf("noinstaller.yml", ("echo 直接开始构建",))
        _got = gate.check_pip_source_resilience(workflows=(_none,), installer_text=installer_src)
        _assert_eq(len(_got), 1, f"反向对照：没调用安装器要恰好报一条（实际={_got}）")
        _assert("没有调用 pip_install.py" in _got[0],
                f"报的必须是这一路没人管冗余（实际={_got[0][:60]}）")
        # g) 登记了 workflow 名字却文件不在：静默少判一条
        _got = gate.check_pip_source_resilience(workflows=(".github/workflows/zz_absent.yml",),
                                                installer_text=installer_src)
        _assert_eq(len(_got), 1, f"反向对照：登记表里的文件不在位必须红，不能跳过（实际={_got}）")
        _assert("没跑" in _got[0], f"报的要说明这条门在这份文件上从没执行（实际={_got[0][:60]}）")
        # h) 两份 b2bcollect 各指一份清单：人肉同步就此回来，加一个包漏一处照样全绿
        _other = _empty_dir.parent / "requirements" / "deps_other.txt"
        _other.write_text("selenium\n", encoding="utf-8")
        _wfwin = _wf("build-b2bcollect-win.yml",
                     (f"python .github/scripts/pip_install.py -r {_REQ_OK}",))
        _wfmac = _wf("build-b2bcollect-mac.yml",
                     (f"python .github/scripts/pip_install.py -r {_other}",))
        _got = gate.check_pip_source_resilience(workflows=(_wfwin, _wfmac),
                                                installer_text=installer_src)
        _assert_eq(len(_got), 1, f"反向对照：两个平台读不同清单要恰好报一条（实际={_got}）")
        _assert("不是同一份" in _got[0], f"报的必须是「同一份清单」这件事破了（实际={_got[0][:70]}）")
        # i) 注释顶替真调用：整份 workflow 只在 run 步的注释里提了一句 pip_install.py，
        #    真正执行的行只有 echo。第一版判据是拿**整份 yml 文本** grep 的，这一份照样
        #    报绿——同一族的另一半是 `-r` 也能被注释顶替（把不存在的清单说成在位）。这是
        #    本轮夹具第一次验第 18 条时抓到的真缺陷（另一处由 docstring 顶替 --timeout，
        #    见下面 5) 段），所以现在两路都只认 run 步里真会执行的行。
        _cmt = _wf("comment.yml", ("# 为什么走 pip_install.py 而不是直接 pip：见门禁说明",
                                   "# python .github/scripts/pip_install.py -r nope.txt",
                                   "echo 假装装好了"))
        _got = gate.check_pip_source_resilience(workflows=(_cmt,), installer_text=installer_src)
        _assert_eq(len(_got), 1,
                   f"反向对照：注释里的调用不算调用，要恰好报「没调用安装器」一条（实际={_got}）")
        _assert("没有调用 pip_install.py" in _got[0],
                f"报的必须是「没人管冗余」这件事（实际={_got[0][:60]}）")

        # ── 5) 安装器文本反向对照：九种坏样子各拆一次，拆了就红 ────────────────
        #    第 4 条与第 9/10 条是本轮夹具自己抓出来的：判据第一版拿整份文本 grep，
        #    `install()` 的 docstring 里那句「没有 --timeout」就把删掉的参数顶替了（假绿），
        #    现在两路都改读 ast 的字面值/去注释后的代码，注释再也替代码说不了话。
        _MUT = (
            (("INDEX_SOURCES = (\n"
              '    ("pypi（pip 默认源）", ""),\n'
              '    ("清华", "https://pypi.tuna.tsinghua.edu.cn/simple"),\n'
              '    ("阿里云", "https://mirrors.aliyun.com/pypi/simple/"),\n'
              ")",
              "INDEX_SOURCES = (\n"
              '    ("清华", "https://pypi.tuna.tsinghua.edu.cn/simple"),\n'
              ")"),
             "只有 1 个源", "整张表只剩一条路：单点可用性又回来了"),
            (("    (\"清华\", \"https://pypi.tuna.tsinghua.edu.cn/simple\"),",
              "    (\"清华镜像二号\", \"https://mirrors.aliyun.com/pypi/simple/\"),"),
             "重复 URL", "看着两条路其实是一条"),
            (("timeout=PER_SOURCE_TIMEOUT", "timeout=None"),
             "timeout=PER_SOURCE_TIMEOUT", "挂住的源会把 job 拖到 Actions 超时"),
            (('PIP_NET_FLAGS = ("--timeout", "30", "--retries", "2")',
               'PIP_NET_FLAGS = ()'),
             "--timeout", "单个连接自己就是无界的"),
            (("stdin=subprocess.DEVNULL", "stdin=None"),
             "stdin=DEVNULL", "pip 觉得需要答复就回头问人，后台没人答"),
            (("    if len(INDEX_SOURCES) < 2:", "    if False:"),
             "少了一道自守卫", "源被删到一条时 workflow 那边看不出来"),
            (("        return 1\n    return 0", "        return 0\n    return 0"),
             "没有 return 1", "只会逐源打印失败、退出码照样 0"),
            (("    if not attempted:", "    if False:"),
             "一个源都没试", "sources 为空时一次 pip 都没跑却被当成装上了"),
            # 9/10 是把「写死的字面量」改成运行时拼出来的：判据读不到那张表就必须明说
            # 读不到，而不是接着数出「0 个源」——数出来的那句会把人引去改源清单，
            # 真要修的却是「这张表不再是字面量」。
            (('PIP_NET_FLAGS = ("--timeout", "30", "--retries", "2")',
              'PIP_NET_FLAGS = tuple(x for x in ("--timeout", "30", "--retries", "2"))'),
             "读不到 PIP_NET_FLAGS 的字面值", "网络参数变成运行时拼出来的，判据只能读注释"),
            (("    (\"清华\", \"https://pypi.tuna.tsinghua.edu.cn/simple\"),",
              "    (\"清华\", str(\"https://pypi.tuna.tsinghua.edu.cn/simple\")),"),
             "读不到 INDEX_SOURCES 的字面值",
             "源清单里出现一次调用，整张表就不再是字面量了（判据只认写死的那份）"),
        )
        for (old, new), keyword, why in _MUT:
            _assert(old in installer_src,
                    f"夹具自证：安装器源码里得有 {old[:40]!r} 这一段，不然下面的反向对照是空跑")
            _got = gate.check_pip_source_resilience(workflows=(_good,),
                                                    installer_text=installer_src.replace(old, new))
            _assert_eq(len(_got), 1,
                       f"反向对照：{why}必须恰好报一条（实际={_got}）")
            _assert(keyword in _got[0], f"报的必须点名 {keyword}（实际={_got[0][:70]}）")

        # ── 6) 行为仿真：真起子进程跑 pip_install.install()，判据是调用序列 ─────────
        #    上面两节判的是「话术与结构」，这一节判的是「真的那么跑没有」。假 pip 只记录
        #    被叫到的顺序、带上的参数、按剧本决定成不成功/磨蹭多久——它一次网络都不碰，
        #    所以这一节在无网 CI 上同样可靠。
        spec = importlib.util.spec_from_file_location("_pip_install_sim", installer_path)
        sim = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sim)

        def _fake_pip(plan: dict) -> Path:
            script = _tmp / "fake_pip.py"
            script.write_text(textwrap.dedent(f"""\
                # -*- coding: utf-8 -*-
                \"\"\"假 pip：把每次被调用的事实写进 calls.jsonl，再按剧本决定成败与耗时。\"\"\"
                import json, sys, time
                LOG = {str(_tmp / 'calls.jsonl')!r}
                PLAN = {json.dumps(plan, ensure_ascii=False)!r}
                a = sys.argv[1:]
                url = a[a.index("-i") + 1] if "-i" in a else "(默认源)"
                rc, _, nap = str(json.loads(PLAN).get(url, json.loads(PLAN).get("*", "0"))).partition(":")
                with open(LOG, "a", encoding="utf-8") as f:
                    f.write(json.dumps({{
                        "url": url,
                        "flags": [x for x in a if x.startswith("--")],
                        "reqs": [a[i + 1] for i, x in enumerate(a) if x == "-r"],
                        "upgrade": "--upgrade" in a,
                        "stdin": "tty" if sys.stdin.isatty() else repr(sys.stdin.read(1)),
                    }}, ensure_ascii=False) + "\\n")
                if nap:
                    time.sleep(float(nap))
                if int(rc):
                    sys.stdout.write("Looking in indexes: " + url + "\\n")
                    sys.stdout.write("ERROR: No matching distribution found for PyQt5 "
                                     "(from versions: none)\\n")
                sys.exit(int(rc))
                """), encoding="utf-8")
            return script

        def _calls() -> list:
            p = _tmp / "calls.jsonl"
            if not p.is_file():
                return []
            out = [json.loads(ln) for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]
            p.unlink()
            return out

        _req = _empty_dir / "deps_sim.txt"
        _req.write_text("requests\nselenium\n", encoding="utf-8")
        _SRC3 = (("pypi（pip 默认源）", ""), ("清华", "https://tuna"), ("阿里", "https://ali"))

        # a) 第一个源挂了要换下一个，且成功就早停（不许把三个源都装一遍）
        _r = sim.install(requirements=(_req,), sources=_SRC3,
                         pip=(sys.executable, str(_fake_pip({"(默认源)": "1", "https://tuna": "0"}))))
        _c = _calls()
        _assert_eq(_r, [], f"正例：默认源失败、清华成功，install 必须报「装上了」（实际={_r}）")
        _assert_eq([x["url"] for x in _c], ["(默认源)", "https://tuna"],
                   "回退按顺序试、装上就停（第三个源不该被叫到：白烧一遍下载时间）")
        _assert(set(_c[1]["flags"]) >= {"--timeout", "--retries", "--no-input",
                                        "--disable-pip-version-check"},
                f"每次调用都真的带上网络参数（第 18 条的文本判据在字节上兑现，实际={_c[1]['flags']}）")
        _assert_eq(_c[1]["reqs"], [str(_req)],
                   f"清单是以 -r 传下去的（不是把包名重新拼回命令行，实际={_c[1]['reqs']}）")
        _assert(all(x["stdin"] == "''" for x in _c),
                f"子进程拿到的 stdin 必须是 EOF（问人就会挂住；第 12/11 条那一族的实证，"
                f"实际={[x['stdin'] for x in _c]}）")

        # b) 全挂才非零：三条路各留一句「它说了什么」，最后一句直接抄 pip 的原话
        _r = sim.install(requirements=(_req,), sources=_SRC3,
                         pip=(sys.executable, str(_fake_pip({"*": "1"}))))
        _c = _calls()
        _assert_eq(len(_r), 3, f"反向对照：三个源全挂必须逐源点名（实际={_r}）")
        _assert_eq([x["url"] for x in _c], ["(默认源)", "https://tuna", "https://ali"],
                   "全部试过一遍才允许判失败（少试一个源就报「全挂」是把结论押在猜上）")
        _assert(all("No matching distribution found" in x for x in _r),
                f"每条都要带上 pip 的最后一句原话，否则下一轮还是要人重新猜根因（实际={_r[0][:80]}）")

        # c) 有界：一个源装到一半不响应，到点就换下一个，不许把 job 拖死
        #    接缝（PER_SOURCE_TIMEOUT）在这里被真的喂进夹具现场：真跑 30 秒的慢源，
        #    3 秒就该被踢掉——不是我把 900 这个数字读了一遍。
        with _patch(sim, "PER_SOURCE_TIMEOUT", 3):
            _t0 = time.monotonic()
            _r = sim.install(requirements=(_req,),
                             sources=(("慢", "https://slow"), ("快", "https://fast")),
                             pip=(sys.executable, str(_fake_pip({"https://slow": "0:30",
                                                                  "https://fast": "0"}))))
            _spent = time.monotonic() - _t0
        _c = _calls()
        _assert_eq(_r, [], f"正例：慢源被超时踢掉之后，快源装上就算成功（实际={_r}）")
        _assert_eq([x["url"] for x in _c], ["https://slow", "https://fast"],
                   "慢源必须留下一次尝试记录，然后换下一个（没试过就说「换」是押注在猜上）")
        _assert(_spent < 12,
                f"整段必须在有界时间内结束：慢源剧本是 30 秒、上限 3 秒，实测 {_spent:.1f} 秒"
                f"（接近 30 秒就说明 timeout 没生效，是等子进程自己跑完的）")

        # d) 只有慢源时，超时要变成一句说得清的话，而不是空白的成功
        with _patch(sim, "PER_SOURCE_TIMEOUT", 2):
            _r = sim.install(requirements=(_req,), sources=(("慢", "https://slow"),),
                             pip=(sys.executable, str(_fake_pip({"https://slow": "0:30"}))))
        _calls()
        _assert_eq(len(_r), 1, f"反向对照：唯一的源超时必须报一条（实际={_r}）")
        _assert("超过" in _r[0] and "2 秒" in _r[0],
                f"报的要写清等了多久、下一步是什么（实际={_r[0][:80]}）")

        # e) fail-closed 三格：没有源、没有清单、清单为空——都不能被当成「装上了」
        _r = sim.install(requirements=(_req,), sources=(), pip=(sys.executable, str(_fake_pip({"*": "0"}))))
        _assert_eq([x["url"] for x in _calls()], [], "空源清单时一次 pip 都不许起（起了就是口径漂移）")
        _assert(_r, "反向对照：一个源都没试绝不能返回空问题清单（空清单在调用方等于「装上了」）")
        _r = sim.install(requirements=(_tmp / "nope.txt",), sources=_SRC3,
                         pip=(sys.executable, str(_fake_pip({"*": "0"}))))
        _assert_eq([x["url"] for x in _calls()], [],
                   "反向对照：清单不在位时连网络都不该碰（先问文件在不在，再谈装什么）")
        _assert(_r and "不在位" in _r[0], f"清单缺席要说得出口（实际={_r}）")
        _r = sim.install(requirements=(_tmp / "requirements" / "deps_empty.txt",), sources=_SRC3,
                         pip=(sys.executable, str(_fake_pip({"*": "0"}))))
        _assert(_r and "一个包都没有" in _r[0], f"空清单要说得出口（实际={_r}）")
        _r = sim.install(requirements=(), sources=_SRC3, pip=(sys.executable, str(_fake_pip({"*": "0"}))))
        _calls()
        _assert(_r and "不知道该装什么" in _r[0], f"既没清单也没包名时必须红（实际={_r}）")

        # f) 退出码这一路真跑：main() 只认 install 的结果，成功 0 / 失败 1 / 源太少不干
        with _patch(sim, "install", lambda **kw: ["夹具：所有源都失败"]):
            _assert_eq(sim.main(["-r", str(_req)]), 1, "反向对照：全挂时退出码必须非零")
        with _patch(sim, "install", lambda **kw: []):
            _assert_eq(sim.main(["-r", str(_req)]), 0, "正例：装上时退出码为 0")
        with _patch(sim, "INDEX_SOURCES", (("清华", "https://tuna"),)):
            _assert_eq(sim.main(["-r", str(_req)]), 1,
                       "反向对照：源少于两条时安装器自己就不干（不靠 workflow 替它守）")

        # g) 差分实测：同一行 `sys.stdin.read(1)`，接 DEVNULL 拿到 EOF、接一根没人写的管道
        #    就真的挂住。这一格是给上面那条 stdin=DEVNULL 判据**补证据**的：没有它，那句
        #    判据的理由就还是我写在注释里的一句话，而不是实测。
        _ask = _fake_pip({"*": "0"})
        _t0 = time.monotonic()
        _r = subprocess.run([sys.executable, str(_ask)], stdin=subprocess.DEVNULL,
                            capture_output=True, timeout=20, encoding="utf-8", errors="replace")
        _calls()
        _assert_eq(_r.returncode, 0, "正例：DEVNULL 下假 pip 读完 EOF 就往下走")
        _assert(time.monotonic() - _t0 < 5, "DEVNULL 那一次必须秒回（慢了就说明差分没跑起来）")
        # 第一版这里我写的是 `subprocess.run(stdin=PIPE)` 不加 input，实测**不挂**：
        # run() 走 communicate(input=None)，一进门就把写端关了，等于给了个更快的 EOF。
        # 真形状要自己留着写端——这才是「后台那条路没人答」的现实（管道在、没人写、也没关）。
        _hr, _hw = os.pipe()
        _hp, _hang = None, False
        try:
            _hp = subprocess.Popen([sys.executable, str(_ask)], stdin=_hr,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                _hp.wait(timeout=2)
                _hang = False                      # 2 秒内自己退了 = 根本没挂住
            except subprocess.TimeoutExpired:
                _hang = True
        finally:
            os.close(_hr)                          # 先把读端的父进程副本收掉
            if _hp is not None:                    # Popen 自己炸了就不该再拿 AttributeError 掩盖它
                if _hang:
                    _hp.kill()
                _hp.wait()                         # 不留孤儿进程
            os.close(_hw)
        _calls()
        _assert(_hang,
                "反向对照：没人应答的那根管道必须真的挂住——否则「pip 会回头问人」整条理由就"
                "是空的，第 18 条的 stdin 判据也就成了押注在想象上的门")
    finally:
        shutil.rmtree(_tmp, ignore_errors=True)

    # ── 7) 清单覆盖（18e）反向对照：漏一个包、名字对不上、连带关系断掉，各红一次 ──
    #    方向只有一个（代码里有 import 而清单里没有），反向不判：清单里的 pyinstaller 是
    #    构建工具、xlrd 是被 pandas 间接读的，按「没 import 就删」判会得到一堆修不掉的假红。
    _cov = _tmp / "coverage"
    (_cov / "requirements").mkdir(parents=True, exist_ok=True)
    _src_py = _cov / "mod_a.py"

    def _cov_case(req_lines: tuple, src_text: str) -> list:
        rq = _cov / "requirements" / "deps_cov.txt"
        rq.write_text("".join(f"{x}\n" for x in req_lines), encoding="utf-8")
        _src_py.write_text(textwrap.dedent(src_text), encoding="utf-8")
        return gate.check_requirements_coverage(requirement_files=(str(rq),),
                                                source_files=(_src_py,))

    _assert_eq(_cov_case(("requests", "pillow", "beautifulsoup4", "websocket-client",
                          "PyQt5"), "import requests\nfrom PIL import Image\nimport bs4\n"
                                     "import websocket\nimport sip\nimport os\n"),
               [], "正例：直名 + 三条别名 + 连带(sip←PyQt5) + stdlib 混在一起，全部放行")
    _got = _cov_case(("requests",), "import requests\nimport pandas\n")
    _assert_eq(len(_got), 1, f"反向对照：清单漏一个包要恰好报一条（实际={_got}）")
    _assert("pandas" in _got[0] and "依赖清单" in _got[0],
            f"报的要点名缺哪个包、该写进哪份文件（实际={_got[0][:80]}）")
    _got = _cov_case(("PIL",), "from PIL import Image\n")
    _assert_eq(len(_got), 1,
               f"反向对照：清单里写 import 名（PIL）而不是发行名（pillow）也要报——"
               f"装不上才是这一格的真语义（实际={_got}）")
    _got = _cov_case(("pillow",), "from photo_import import x\n")
    _assert_eq(len(_got), 1, f"反向对照：代码里的第三方名一个都对不上清单时报一条（实际={_got}）")
    _assert("photo_import" in _got[0], f"报的必须是那个 import 名（实际={_got[0][:70]}）")
    _got = _cov_case(("requests",), "import sip\n")
    _assert(_got and "连带" in _got[0],
           f"反向对照：连带关系的宿主不在清单里时，要报「连带关系断了」而不是「sip 没登记」"
           f"（后者没法照做，实际={_got}）")
    _got = _cov_case((), "import requests\n")
    _assert(_got, f"反向对照：空清单上必须红（实际={_got}）")
    _got = gate.check_requirements_coverage(requirement_files=(str(_cov / "nope.txt"),),
                                            source_files=(_src_py,))
    _assert(_got and "不在位" in _got[0], f"反向对照：清单文件不在位要说出口（实际={_got}）")
    _got = gate.check_requirements_coverage(source_files=())
    _assert(_got and "一个源文件都没扫到" in _got[0],
            f"反向对照：扫不到任何源码时绝不能报「覆盖完全」（实际={_got}）")
    _bad_py = _cov / "broken_syntax.py"
    _bad_py.write_text("def (:\n", encoding="utf-8")
    _assert_raises(SystemExit, lambda: gate._top_level_imports(_bad_py),
                   "反向对照：源码读不进去时必须炸，不能安静少扫一个文件（少扫一个的下场是"
                   "「✅ 全部覆盖」底下藏着一个从没被看过的模块）")


def test_section_pointer_gate():
    print("\n═══ AC. 注释里那句「见某套件 第 N 节」得走得通：第 19 条硬门 ═══")
    # 由来（2026-11-13 本机自审）：把「择路不许跟着本机历史走」写进 tool_config 与
    # runtime_paths 时，两边都补了一句「对拍判据见 test_data_dir_offline.py 第 10~13 节」，
    # 而那个文件当时只编到第 9 节。清点下来一共 13 处这样的指路，全都指向从没写出来的小节；
    # 顺着这份缺口查下去，_page_tail / _has_any_contact / CONTACT_RETRY_MAX / results_live
    # 四个函数在**全部**离线套件里 0 覆盖——正是用户报「以前能采到、现在一直是 0」那条链上
    # 的守卫缺口。
    # 「判据在哪一节」这句话本身不是判据：读注释的人按节号走过去，找不到也只会以为是自己没
    # 翻到。本节把那句话钉成可达性核对，并且先证明这条门**看得见新写法**（区间、并列、全角、
    # 折行四种），再证明它不误报（句号断开、间距超限、套件自己的内部编号三格放行）。
    gate = _manifest_gate()
    gate_src = (_REPO / ".github" / "scripts" / "check_packaging_manifest.py").read_text(
        encoding="utf-8")

    # ── 1) 接线：函数在、被 main() 调用、结果并进不合格计数、云端留下 annotation ──
    for fn in ("check_section_pointer_references", "_expand_section_items",
               "_load_ref_texts", "_load_ref_suites", "_ref_scan_text"):
        _assert(callable(getattr(gate, fn, None)),
                f"硬门模块里有 {fn}（能被单独喂夹具，才谈得上反向对照）")
    _at = gate_src.find("section_refs = check_section_pointer_references()")
    _assert(_at > 0, "硬门 main() 里真的调了第 19 条（函数在而不调＝这条门从没跑过）")
    _assert("bad += 1" in gate_src[_at:_at + 1200],
            "报出来的问题要并进不合格计数并影响退出码（只 print 不改退出码的门等于公告栏）")
    _assert("::error::指路节号对拍" in gate_src[_at:_at + 1200],
            "云端每条问题都要写成 annotation：这一步在 CI 上红的时候，日志里得能直接照做")
    for d in gate.SECTION_REF_DIRS:
        _assert((_REPO / d).is_dir(),
                f"{d}：第 19 条登记了这份扫描目录却不在位（登记了却不判＝这条门安静空转，"
                f"而日志照样打 ✅）")

    # ── 2) 现状：当前仓库里每一句指路都真的走得通 ──
    _assert_eq(gate.check_section_pointer_references(), [],
               "第 19 条：交付链注释里的指路节号全部对得上真实小节")

    # ── 3) 自证：全绿不是因为「什么都没扫到」。三个量都要有数：扫到的文本份数、被指路
    #    一侧的套件、仓库里真实存在的指路条数。AB 节量过同一格——扫 0 个文件的「全覆盖」
    #    是自欺，而这里更隐蔽：交付目录一旦改名，默认口径会扫到 0 份并照样报绿。
    texts, load_problems = gate._load_ref_texts()
    _assert_eq(load_problems, [], "扫描范围内没有读不进去的文件（读不到≠不用判）")
    _assert(len(texts) >= 40,
            f"扫描口径真的展开了（{len(texts)} 份文本）：份数掉下 40 就是目录口径和交付脱节了")
    suites = gate._load_ref_suites(texts)
    _assert("test_data_dir_offline.py" in suites,
            "本轮补写小节的那份套件在「被指路」的一侧（不在＝那句指路谁也走不通）")
    real_refs = sum(len(list(gate.SECTION_REF_RE.finditer(gate._ref_scan_text(t))))
                    for rel, t in texts.items()
                    if not rel.rsplit("/", 1)[-1].startswith(gate.TEST_PREFIX))
    _assert(real_refs >= 10,
            f"仓库里现役的指路注释有 {real_refs} 处：这条门看守的是正在被引用的话术，"
            f"不是空表（数出 0 处却报 ✅ 与没有这条门等价）")

    # ── 4) 展平口径：四种写法都要落到逐个节号上 ──
    _assert_eq(gate._expand_section_items("10~13"), ["10", "11", "12", "13"],
               "区间展平成每一个节号：只取两端会把中间那两节盖住，而本轮悬空的正是 10~13 整段")
    _assert_eq(gate._expand_section_items("1、2、3、8"), ["1", "2", "3", "8"], "顿号并列逐个判")
    _assert_eq(gate._expand_section_items("10、12"), ["10", "12"], "并列里缺哪个报哪个")
    _assert_eq(gate._expand_section_items("１０"), ["10"], "全角数字折算半角（源码里两种都出现过）")
    _assert_eq(gate._expand_section_items("13~10"), ["10", "11", "12", "13"],
               "倒着写的区间不猜作者想说什么，两端都并进待验集合：当成只有一个数会把笔误盖住")
    _assert_eq(gate._expand_section_items("7"), ["7"], "单号")
    # 反向对照：第 19 条**初版**（只认单号与顿号）对历史原句不相——留这份旧口径在案，是为了
    # 让下一位改正则的人看得见「加区间支持」这一步到底补上了什么，而不是只看见新口径全绿。
    _old_re = re.compile(r"([A-Za-z0-9_]{4,}\.py)(?:(?!\.py)[^。；\n\"]){0,12}?第\s*"
                         r"([0-9０-９]+(?:\s*[、,，]\s*[0-9０-９]+)*)\s*节")
    _hist = "对拍判据见 test_data_dir_offline.py 第 10~13 节"
    _assert(not _old_re.search(_hist),
            "反向对照：旧口径对区间那句根本不相（若第 19 条没带区间展开，本轮悬空的那一句"
            "照样会被安静放行——那正是它唯一要抓的东西）")
    _assert(gate.SECTION_REF_RE.search(_hist), "新口径同一句要匹配得到（上面那条差分才成立）")

    # ── 5) 夹具注入的正反对照：一句走不通的指路必须红，且要把缺的节逐个点名 ──
    _SUITE_AC = "".join(f"# ── {i}. 第{i}节夹具 ──\nx{i} = {i}\n" for i in (1, 2, 3, 7))
    _LETTER_AC = "# ═══ A. 甲 ═══\n\n# ═══ B. 乙 ═══\n"

    def _case_ac(comment: str, suite_name: str = "test_fixture_ac.py",
                 suite_body: str = _SUITE_AC) -> list:
        """把一条注释喂给第 19 条：夹具自己拼，不依赖本机仓库状态（第 16/17 条同规矩）。"""
        return gate.check_section_pointer_references(
            texts={"pkg_ac/mod_ac.py": f"# {comment}\nx = 1\n"},
            suites={suite_name: suite_body})

    _assert_eq(_case_ac("择路口径的对拍见 test_fixture_ac.py 第 1 节"), [],
               "正例：指到真实存在的节必须放行")
    _got = _case_ac("择路口径的对拍见 test_fixture_ac.py 第 9 节")
    _assert_eq(len(_got), 1, f"反向对照：悬空单号要恰好报一条（实际={_got}）")
    _assert("第 9 节" in _got[0] and "[1, 2, 3, 7]" in _got[0],
            f"报的要同时给出「指的是哪一节」和「那套件里实际有哪些节」（实际={_got[0]}）")
    _got = _case_ac("接管三规矩见 test_fixture_ac.py 第 7~9 节")
    _assert_eq(len(_got), 1, f"反向对照：区间右端越界要报一条（实际={_got}）")
    _assert("第 8、9 节" in _got[0],
            f"缺的两节要逐个点名，不能只说「区间越界」（实际={_got[0]}）")
    _got = _case_ac("四节一起看 test_fixture_ac.py 第 1、2、3、8 节")
    _assert("第 8 节" in (_got[0] if _got else ""),
            f"反向对照：并列里缺哪一节就点哪一节（实际={_got}）")
    _got = _case_ac("全角写法见 test_fixture_ac.py 第８节")
    _assert_eq(len(_got), 1, f"反向对照：全角节号也要判到（旧写法在这里会静默放行）（实际={_got}）")
    _got = _case_ac("指错文件见 test_nope_ac.py 第 1 节")
    _assert_eq(len(_got), 1, f"反向对照：被指路的套件文件不在案要报一条（实际={_got}）")
    _assert("没有这份文件" in _got[0], f"报的要是「文件不存在」而不是「节不存在」（实际={_got[0]}）")
    _got = _case_ac("字母编号的套件见 test_fixture_ac.py 第 1 节", suite_body=_LETTER_AC)
    _assert(_got and "用字母编的" in _got[0],
            f"反向对照：数字指路撞到一份按字母编号的套件上必须红——那句「见第 1 节」"
            f"在那里根本不存在（实际={_got}）")

    # 折行：中文注释按列折行是常态，逐行匹配会把「折了行」当成「没引用」放过，而放过
    # 的方向是漏报。这一格是把 _ref_scan_text 的存在理由钉成行为判据。
    _got = gate.check_section_pointer_references(
        texts={"pkg_ac/mod_ac.py": "# 择路口径的对拍见\n# test_fixture_ac.py 第\n"
                                   "# 9 节，改动要两边一起\nx = 1\n"},
        suites={"test_fixture_ac.py": _SUITE_AC})
    _assert_eq(len(_got), 1, f"反向对照：折成三行的同一条指路也要抓到（实际={_got}）")
    _assert("第 9 节" in _got[0], f"折行抓到之后报的仍是那一节（实际={_got[0]}）")

    # 三条不许误报的口径。误报的代价和漏报一样：人开始跳过这一步（第 9 条同款教训）。
    _assert_eq(_case_ac("这段说明在 test_fixture_ac.py。下一句才说第 9 节不存在"), [],
               "放行：句号把话说断了，后半句和那份文件不是同一条指路")
    _assert_eq(_case_ac("见 test_fixture_ac.py 里那份夹具自带的说明文字这里刻意"
                        "把间距写到十二个字符以外 第 9 节"), [],
               "放行：离得太远的「第 N 节」不是给这句 `.py` 指的路（收紧间距才不误伤叙述文字）")
    _assert_eq(gate.check_section_pointer_references(
        texts={"b2bcollect_build/test_fixture_ac.py": "# 内部编号见 test_fixture_ac.py 第 99 节\n"},
        suites={"test_fixture_ac.py": _SUITE_AC}), [],
        "放行：套件自己注释里的节号是本文件内部编号，改动时同文件一起改，不构成跨文件指路")

    # ── 6) 本轮真发生过的那一次：一句「第 10~13 节」撞到只有 1~9 节的套件上 ──
    _nine = "".join(f"# ── {i}. 第{i}节 ──\ny{i} = {i}\n" for i in range(1, 10))
    _got = gate.check_section_pointer_references(
        texts={"b2bcollect_build/tool_config.py":
               "# 对拍判据见 test_data_dir_offline.py 第 10~13 节，改口径要两边一起\n"},
        suites={"test_data_dir_offline.py": _nine})
    _assert_eq(len(_got), 1, f"反向对照：本轮那 13 处里的任意一处形状都要红（实际={_got}）")
    _assert("第 10、11、12、13 节" in _got[0],
            f"缺的四节逐个点名，读的人才知道要补写哪几节（实际={_got[0]}）")
    _assert("[1, 2, 3, 4, 5, 6, 7, 8, 9]" in _got[0],
            f"要把那套件现有的节号一并印出来（实际={_got[0]}）")

    # ── 7) fail-closed：扫描目录被改名 / 扫到 0 份文本时，这条门必须红而不是报绿 ──
    with _patch(gate, "SECTION_REF_DIRS", ("nope_dir_ac",)):
        _got = gate.check_section_pointer_references()
    _assert(any("扫描目录" in x for x in _got),
            f"反向对照：登记的扫描目录不在位要说出口（默认口径全靠这几个目录，静默少一路＝"
            f"少一路没人知道）（实际={_got}）")
    _assert(any("只读到 0 份" in x for x in _got),
            f"反向对照：一份文本都没扫到时绝不能报「全部走得通」（实际={_got}）")


def test_curl_bounds_gate():
    print("\n═══ AD. 每一次 curl 都得自己会停、写进文件的字节得跨次能用：第 20 条硬门 ═══")
    # 由来（2026-11-13 本机实发，连着两趟一样的红）：取制品跑到外层超时被杀（rc=124），日志
    # 停在「[$key] 下载中」之后，一句原因都没留下。字节级看是三件事叠在一起，缺任何一件都不会
    # 让这件事「永远完不成」：
    #   ① curl 默认只在**连不上**时自己退。实测同一个 artifact（14034890976）单连接 Range 取
    #      2 MiB 用了 182.98 秒（11.5 KB/s），而它认为这完全正常；
    #   ② 慢挂在单条连接上而不是链路上。实测四并发各取 2 MiB、总 166 秒拿到 8 MiB（≈50 KB/s），
    #      其中一路还会半路报 `Recv failure: Connection reset by peer`，只落
    #      1899440/2097152 字节。所以「把 -m 调大」既救不了这件事，又会顺手弄死正常的慢链路；
    #   ③ zip 落在 mktemp 出来的工作目录里，trap EXIT 连它一起删——每次重跑都从第 0 字节开始。
    # 同一趟还在派发脚本上抓到同族的裸 curl（派发失败长得和派发成功一模一样，人就开始等一个
    # 从没起来过的 run）。慢可以接受，「永远完不成」不行：这一条判的是结构，不是这一分钟的网络。
    gate = _manifest_gate()
    gate_src = (_REPO / ".github" / "scripts" / "check_packaging_manifest.py").read_text(
        encoding="utf-8")

    def _one(sh: str) -> list:
        return gate.check_curl_bounds({"ad": sh})

    # ── 1) 接线：判据在、被 main() 调用、结果进退出码、云端留下 annotation ──
    for fn in ("check_curl_bounds", "shell_gate_inputs", "curl_sites", "_shell_tokens",
               "_logical_lines", "_mktemp_vars", "_curl_out_value"):
        _assert(callable(getattr(gate, fn, None)),
                f"硬门模块里有 {fn}（能被单独喂夹具，才谈得上反向对照）")
    _at = gate_src.find("curl_bounds = check_curl_bounds()")
    _assert(_at > 0, "硬门 main() 里真的调了第 20 条（函数在而不调＝这条门从没跑过）")
    _assert("bad += 1" in gate_src[_at:_at + 1200],
            "报出来的问题要并进不合格计数并影响退出码（只 print 不改退出码的门等于公告栏）")
    _assert("::error::curl 有界与可续传" in gate_src[_at:_at + 1200],
            "云端每条问题都要写成 annotation：这一步红的时候，日志里得能直接照做")

    # ── 1b) 「同一张登记表」要用行为证明，不是用函数名证明 ──
    # 我在 20d 的注释里写过「两族用的是同一张登记表」——那句话本身不构成判据：真口径换成
    # 两份各抄一遍的读取循环时，函数名照样都在，而加一份脚本只会补到其中一处（第 10 条同族）。
    # 所以这里把登记表里的一份换成一个**不存在的名字**，要求 20a~20c 与 20d **同时**点名同一
    # 个缺口。注意方向：从表里「摘掉」一份脚本两族都会安静放行（那本来就是不判），只有「登记
    # 了却读不到」才是两族共用的那条 fail-closed。
    _swapped = tuple("debug/zz_missing_ad.sh" if x == "debug/fetch_run_log.sh" else x
                     for x in gate.SHELL_SOURCES)
    with _patch(gate, "SHELL_SOURCES", _swapped):
        _miss_curl = gate.check_curl_bounds()
        _miss_hang = gate.check_no_prompt_hang()
    _assert(any("zz_missing_ad.sh 不在位" in x for x in _miss_curl),
            f"登记了却读不到，curl 那一族要说「不在位」（实际={_miss_curl}）")
    _assert(any("zz_missing_ad.sh 不在位" in x for x in _miss_hang),
            f"同一格缺口在 20d 上也要响一次——只有一族响，说明两族各自抄了表（实际={_miss_hang}）")

    # ── 2) 现状 + 覆盖面：全绿不是因为什么都没扫到 ──
    _assert_eq(gate.check_curl_bounds(), [],
               "第 20 条：交付链今天每一次 curl 都自己会停，写进文件的字节都不在本次即弃的落点上")
    texts, unreadable = gate.shell_gate_inputs()
    _assert_eq(unreadable, [], "登记进第 20 条的脚本与 workflow 全部读得到（读不到≠不用判）")
    # 覆盖面不写死「几份」：那份数量会随交付链加减，写成一个魔法数就等着下一次假红（今天
    # 实测 5 份 / 11 条 curl，而我上一版钉的是 6 份，正是这样红的）。真正要担保的是
    # 「登记表里含 curl 的每一份脚本都被扫到」，这个口径是从表本身派生的，永远不会过期。
    _expect_shell = sorted(rel for rel in gate.SHELL_SOURCES
                           if "curl" in (_REPO / rel).read_text(encoding="utf-8", errors="replace"))
    _assert_eq(sorted(k for k in texts if k.endswith(".sh")), _expect_shell,
               "登记表里每一份含 curl 的脚本都要出现在扫描结果里：多一份少一份都是扫描口径"
               "和登记表分叉（第 10 条同族：抄两遍迟早分叉）")
    _assert(any("build-b2bcollect.yml" in k and "Provision NSIS" in k for k in texts),
            "workflow 那一侧必须收进 NSIS 装工具那一步（云端两处真下载之一，红过一整轮交付）；"
            "这里按步骤名认、不写行号——行号会随上面加删行漂移，漂移的期望就是下一次假红")
    _assert(any("build-b2bcollect.yml" in k and "Provision VC++ redist" in k for k in texts),
            "需求 10 之后云端的第二处真下载（VC++ redist provision）也必须在扫描视野里："
            "缺了它，redist 那一路的 curl 就不在 20a/20b/20c 的判据之下")
    sites = sum(len(gate.curl_sites(t)) for t in texts.values())
    _assert(sites >= 4,
            f"这一条今天看到 {sites} 次 curl：下界只钉 4 次（四族脚本各至少一次），数出 0 次却报 ✅ "
            "与没有这条门等价（AB 节量过同一格）")
    # 本轮修好的那两条真字节也要能被点名：体下载那一条必须同时有停顿判定和按块 Range，
    # 缺任何一个，「慢链路 + 断流」就还是会把整趟拖成永远完不成。
    fetch_text = texts["debug/fetch_b2bcollect_artifacts.sh"]
    body_dl = [(seg, {t for t in toks if t.startswith("-")})
               for _, seg, toks in gate.curl_sites(fetch_text) if "--speed-limit" in seg]
    _assert_eq(len(body_dl), 1,
               f"取制品里靠停顿判定收口的那次体下载恰好一条（实际 {len(body_dl)} 条）：多了说明口径漂了")
    _assert("--speed-time" in body_dl[0][1] and "-r" in body_dl[0][1],
            f"那一条必须同时带 --speed-time 与按块 Range（实际={body_dl[0][0][:120]}）")

    # ── 3) 反向对照用的是 HEAD 那两版的**原句**，不是我编的坏例子 ──
    _OLD_FETCH = r"""WORK=$(mktemp -d)
  SHA=$(curl -sS -m 30 -H "Authorization: Bearer $TOK" -H "Accept: application/vnd.github+json" \
        "$API/git/ref/heads/main" 2>/dev/null)
  curl -sL --retry 8 --retry-all-errors -H "Authorization: Bearer $TOK" -o "$WORK/$key.zip" \
    "$API/actions/artifacts/$AID/zip"
  if ! extract_zip "$WORK/$key.zip" "$WORK/$key"; then
    echo "解压失败"
  fi
"""
    _OLD_DISPATCH = r"""  CODE=$(curl -s -o /dev/null -w '%{http_code}' -X POST \
    -H "Authorization: Bearer $TOK" "$API/actions/workflows/$WF/dispatches")
  curl -s -H "Authorization: Bearer $TOK" \
    "$API/actions/workflows/$WF/runs?per_page=1"
"""
    _got = gate.check_curl_bounds({"旧取制品": _OLD_FETCH, "旧派发": _OLD_DISPATCH})
    _assert_eq(sum(1 for x in _OLD_DISPATCH.splitlines() if "curl" in x), 2,
               "夹具里派发那两句是真的两句（少切一句，下面那个数就没意义）")
    _assert_eq(sum(1 for x in _got if "谁也不会让它停" in x), 3,
               f"三条裸 curl 全响（实际={_got}）：带 -m 30 那一条不响，说明这条门不是见 curl 就报")
    _assert(any("mktemp" in x for x in _got),
            f"③那一格必须点名：写进 $WORK（本次即弃）（实际={_got}）")
    _assert(any("既不续传" in x for x in _got),
            f"同一条下载里 20c 也要各自响一次：光换落点不续传，断了还是整段重下（实际={_got}）")

    # ── 4) 20a 的四种写法：粘写、成对、缺半边 ──
    _assert_eq(_one('curl -sS -m 40 -C - -o "$CACHE/a.zip" "$u"\n'), [], "放行：-m 40")
    _assert_eq(_one('curl -sS -m40 -C - -o "$CACHE/a.zip" "$u"\n'), [], "放行：-m40 粘写也算")
    _assert_eq(_one('curl -sS --speed-limit 2048 --speed-time 180 -C - -o "$CACHE/a.zip" "$u"\n'),
               [], "放行：--speed-limit + --speed-time 成对也算")
    _got = _one('curl -sS --speed-limit 2048 -C - -o "$CACHE/a.zip" "$u"\n')
    _assert_eq(len(_got), 1, f"反向对照：只有 --speed-limit、没有 --speed-time 必须响（实际={_got}）")
    _assert("谁也不会让它停" in _got[0], "一个数不构成停顿判定，报的也得是这句话")

    # ── 5) 折行与「逐行旧口径」的差分 ──
    _folded = r"""curl -sS \
  --max-time 60 \
  -C - -o "$CACHE/a.zip" "$u"
"""
    _assert_eq(len(gate.curl_sites(_folded)), 1, "折成三行的一条 curl 只算一条")
    _assert_eq(_one(_folded), [], "放行：--max-time 写在第二行也算有界")
    _naive = [ln for ln in _folded.splitlines() if ln.strip().startswith("curl")]
    _assert(len(_naive) == 1 and "--max-time" not in _naive[0],
            "反向对照：逐行口径会把这一行判成「没界」——假红逼人在命令中间插一行注释，而那条"
            "注释什么也不担保（第 11 条同一族教训）")

    # ── 6) 20b 认落点的六种写法 + 三种不许误报 ──
    _MK = "WORK=$(mktemp -d)\n"
    for _spell in ('-o "$WORK/a.zip"', '-o "${WORK}/a.zip"', '-o"$WORK/a.zip"',
                   '--output="$WORK/a.zip"', '--output "$WORK/a.zip"', '-sSo "$WORK/a.zip"'):
        _assert(any("mktemp" in x for x in _one(_MK + f'curl -sS -m 40 -C - {_spell} "$u"\n')),
                f"20b：`{_spell}` 这种写法也要认出落点是本次即弃")
    # 旧写法（只看「前四个字符里有没有小写 o」）在粘写那一格会把**下一个 token（URL）**当成
    # 落点：假归因正好绕开 mktemp 那一判，是漏报的方向。留这段差分给下一位改这里的人看。
    def _old_out_value(toks):
        for i, t in enumerate(toks):
            if t in gate.CURL_OUT_FLAGS:
                return toks[i + 1].strip("\"'") if i + 1 < len(toks) else ""
            if t.startswith("-") and len(t) > 1 and t[1] != "-" and "o" in t[1:5]:
                return toks[i + 1].strip("\"'") if i + 1 < len(toks) else ""
        return ""
    _toks = gate._shell_tokens('-sS -m 40 -o"$WORK/a.zip" "$u"')
    _assert_eq(_old_out_value(_toks), "$u", "反向对照：旧口径在粘写那一格拿到的落点是 URL")
    _assert_eq(gate._curl_out_value(_toks), ("path", "$WORK/a.zip"),
               "新口径同一句拿到的是真落点（上面那条 mktemp 要抓的就是它）")
    _assert_eq(_one(_MK + 'curl -sS -m 40 -o /dev/null -w "%{http_code}" "$u"\n'), [],
               "放行：体没落地（-o /dev/null），那里没有要续传的字节")
    _assert_eq(_one(_MK + 'curl -sS -m 40 -o - "$u" >> "$p"\n'), [], "放行：-o - 交给调用方")
    _assert_eq(_one('curl -sS -m 40 -C - -o "$CACHE/a.zip" "$u"\n'), [],
               "放行：落点是可复用的缓存目录，不是本次即弃")
    _assert_eq(_one(_MK + 'curl -sS -m 40 -C - -XPOST "$u"\n'), [],
               "放行：这一句压根没写文件（把 -X 取值 POST 里那个 O 当成 -O 会得到一句假归因）")
    _assert_eq(_one(_MK + 'curl -sS -m 40 -C - -c cookies.txt -o "$CACHE/a.zip" "$u"\n'), [],
               "放行：-c 的取值 cookies.txt 里那个 o 不是落点开关（带参数的短开关一律不当组合开关）")
    _assert_eq(_one('curl -sS -m 40 -C - -O "$u"\n'), [],
               "放行：-O 落到当前目录，20b 归因不出路径就不硬猜")
    _got = _one('curl -sS -m 40 -O "$u"\n')
    _assert_eq(len(_got), 1, f"反向对照：-O 只是判不出落点前缀，不豁免续传/完整性那一半（实际={_got}）")
    _assert("既不续传" in _got[0], f"-O 那一格报的必须是「既不续传也没有收口」（实际={_got[0]}）")

    # ── 7) 20c 的两条合法路，以及「就近」到底多近 ──
    _assert_eq(_one('curl -sS -m 40 -C - -o "$CACHE/a.zip" "$u"\n'), [], "放行：会接着要（-C -）")
    _assert_eq(_one('curl -sS --speed-limit 2048 --speed-time 180 -r 0-100 -o "$CACHE/a.zip" "$u"\n'),
               [], "放行：按块 Range 取")
    _got = _one('curl -sS -m 40 -o "$CACHE/a.zip" "$u"\n')
    _assert(any("既不续传" in x for x in _got), f"反向对照：既不续传、近处也没收口 → 红（实际={_got}）")
    # 云端 NSIS / VC++ redist 两步走的是后者（有界 + 就近核 sha256，没有 -C）：拿**真字节**钉住
    # 这条路合法，免得逼人为了消红去补一个用不上的 -C——没核过哈希的续传反而更危险。
    _wf = (_REPO / ".github" / "workflows" / "build-b2bcollect.yml").read_text(encoding="utf-8")
    _curl_steps = [s for _, _, sh, s in gate.run_blocks_in_workflow(_wf) if sh == "bash" and "curl" in s]
    # 登记制（与第 14 条 SHELL_SOURCES 同一纪律）：Windows workflow 里每一次真实网络取件
    # 都要在这里在案。需求 10 之后是两步——NSIS provision 与 VC++ redist provision：
    # 两个独立网络依赖各占一步，Actions 页面上失败才归因得开（合并进一步，挂了就分不清
    # 是编译器还是运行库没取到）。多一步＝没登记的新下载步；少一步＝哪一步被顺手删了。
    _assert_eq(len(_curl_steps), 2,
               f"Windows workflow 里带 curl 的 bash 步按登记应当是两步（NSIS + VC++ redist provision），实际 {len(_curl_steps)} 步")
    for _i, _s in enumerate(_curl_steps, 1):
        _assert_eq(_one(_s), [], f"第 {_i} 步真字节：有界 + 就近核 sha256，没有 -C")
        _assert("--max-time" in _s and "-C " not in _s,
                f"第 {_i} 步确实是「有界但不续传」那一类（两个条件缺一个，上面那条放行就没在验这格）")
        _assert("sha256" in _s,
                f"第 {_i} 步必须就地核 sha256（下载进安装包的每一批字节都要当场对哈希）")
    _head = 'curl -sS -m 40 -o "$CACHE/a.zip" "$u"\n'
    _assert_eq(_one(_head + "".join(f"echo {i}\n" for i in range(38))
                   + 'sha256sum "$CACHE/a.zip"\n'), [],
               "放行：收口隔到窗口边界那一行仍算（NSIS 的 curl 与 sha256 之间夹着换镜像的 if 块）")
    _got = _one(_head + "".join(f"echo {i}\n" for i in range(39)) + 'sha256sum "$CACHE/a.zip"\n')
    _assert(any("既不续传" in x for x in _got),
            "反向对照：再隔一行就不算了——窗口没有边界，一句收口就能给全文件的下载当免检牌")
    _got = _one(_head + "".join(f"# 说明 {i}\n" for i in range(10)) + "# 这里跑的也是 sha256sum\n")
    _assert(any("既不续传" in x for x in _got),
            "反向对照：注释里那句「也核哈希」不算收口。旧版取制品脚本第 244 行那条无续传下载，"
            "就是被第 279 行这样一句免掉的，而真正那次调用在第 288 行——已经出了窗口")
    _got = _one('sha256sum old.zip\n'
                + "\n".join(f'curl -sS -m 40 -o "$CACHE/{i}.zip" "$u"' for i in range(3)) + "\n")
    _assert_eq(sum(1 for x in _got if "既不续传" in x), 3,
               "反向对照：文件顶上一句收口不给下面三次下载当护身符，要各自挨着各自写")

    # ── 8) fail-closed：读到 0 份 / 扫到 0 条 curl / 登记的文件不在位，都必须是红 ──
    _got = gate.check_curl_bounds({})
    _assert_eq(len(_got), 1, f"反向对照：一份文本都没读到时不能报绿（实际={_got}）")
    _assert("都没读到" in _got[0], f"报的要是「没读到」而不是「都合格」（实际={_got[0]}）")
    _got = _one('echo 这里只有 echo\n')
    _assert(any("一条 curl 都没找到" in x for x in _got),
            f"反向对照：扫到 0 次 curl 也是红，不是绿（实际={_got}）")
    with _patch(gate, "SHELL_SOURCES", ("debug/zz_nope_ad.sh",)):
        _got = gate.check_curl_bounds()
    _assert(any("不在位" in x for x in _got),
            f"反向对照：登记的脚本读不到要说出口（默认口径全靠这张表）（实际={_got}）")

    # ── 9) 判不到的形状写在案上，免得这条门给人「所有下载都被管住了」的错觉 ──
    _assert_eq(gate.curl_sites('wget -q -O "$CACHE/a.zip" "$u"\n'), [],
               "已知盲区：wget 不在这一条的视野里（它只会撞上上面那条「一条 curl 都没找到」，"
               "而不是被安静放行）；同族盲区还有靠别的变量名间接拼出来的 curl 参数")

    # ── 10) 20d：会「开口问人」的外部命令必须显式关掉交互 ──
    # 由来（2026-11-13 本机自审，就在把 20a~20c 接上之后）：`git credential fill` 在
    # helper 里没有 github.com 凭证时**不是报错**，而是去开 /dev/tty 问用户名密码。它读的
    # 不是脚本喂给 stdin 的那条 printf，所以「管道里已经喂了东西」一点也挡不住它。这和 20a
    # 判的是同一件事：进程不会自己停，日志一个字都不留，人只能对着「取制品」那行标题等。
    # 清点下来仓库里四处调用（派发 / 取制品 / 轮询 / 取证）全都只喂了 stdin、没关交互。
    for fn in ("check_no_prompt_hang", "shell_gate_inputs", "_drop_comment_lines"):
        _assert(callable(getattr(gate, fn, None)), f"硬门模块里有 {fn}（能被单独喂夹具）")
    _at = gate_src.find("prompt_hangs = check_no_prompt_hang()")
    _assert(_at > 0, "硬门 main() 里真的调了 20d（函数在而不调＝这条门从没跑过）")
    _assert("bad += 1" in gate_src[_at:_at + 1200],
            "20d 报出来的问题要并进退出码（只 print 不改退出码的门等于公告栏）")
    _assert("::error::外部命令交互收口" in gate_src[_at:_at + 1200],
            "云端每条问题都要写成 annotation：这一步红的时候日志里得能直接照做")
    _assert_eq(gate.check_no_prompt_hang(), [],
               "20d：交付链今天每一处 credential fill 都带 GIT_TERMINAL_PROMPT=0")
    _hang_texts, _hang_miss = gate.shell_gate_inputs(("credential fill",))
    _assert_eq(_hang_miss, [], "20d 读的仍是第 20 条那张登记表（各抄一遍就会和交付链分叉）")
    # 逐份点名「这一份里确实有一句真在取凭证」，而不是钉一个总数：总数会随交付链加减而假红
    # （同一格里 20a 的份数就是这么红的），而逐份的期望只在自己担保的那一路失效时才响。
    for _rel in ("debug/dispatch_b2bcollect_builds.sh", "debug/fetch_b2bcollect_artifacts.sh",
                 "debug/poll_b2bci.sh", "debug/fetch_run_log.sh",
                 # 2026-11-20 交接段加进来的一路：Win7 兼容通道不在 poll_b2bci.sh 的名单里，
                 # 它跑到第几步只能靠这份脚本现敲 API。手工结论不可复跑，但它照样取凭证，
                 # 所以必须在这张表里点名——「多一路要写进这张表」就是这个意思。
                 "debug/ci_status.sh"):
        _n = sum(1 for logical, _ln in
                 gate._logical_lines(gate._drop_comment_lines(_hang_texts[_rel]))
                 if "credential fill" in logical)
        _assert(_n >= 1, f"{_rel} 里那句真取凭证的调用还在扫描视野里（数出 {_n} 句）")
    _assert_eq(sum(1 for x in _hang_texts if x.endswith(".sh")), 5,
               "今天取凭证的就是派发/取制品/轮询/取证/看状态这五份脚本：少一路没人知道，多一路要写进这张表")

    def _hang(sh: str) -> list:
        return gate.check_no_prompt_hang({"ad": sh})

    # HEAD 那三句是原字节（取证脚本从未入库，它的原句和取制品那句同形）：反向对照必须响
    _OLD_HANG = {
        "派发（HEAD 第 5 行）":
            "CRED=$(printf 'protocol=https\\nhost=github.com\\n\\n' | git credential fill)\n",
        "取制品（HEAD 第 14 行）":
            "TOK=$(printf 'protocol=https\\nhost=github.com\\n\\n' | git credential fill"
            " | sed -n 's/^password=//p')\n",
        "轮询（HEAD 第 30~31 行，折行）":
            "TOK=$(printf 'protocol=https\\nhost=github.com\\n\\n' | git credential fill \\\n"
            "      | sed -n 's/^password=//p')\n",
    }
    _got = gate.check_no_prompt_hang(_OLD_HANG)
    _assert_eq(len(_got), 3, f"HEAD 那三句原字节必须三句全响（实际={_got}）")
    _fold_hit = [x for x in _got if "轮询" in x]
    _assert_eq(len(_fold_hit), 1,
               f"折行那一句只响一次：拼回同一句再判，写在下一行的 `| sed` 不算第二条命令"
               f"（实际={_fold_hit}）")
    _assert("第 1 行" in _fold_hit[0],
            f"行号要报**起始行**，让人一眼走到（实际={_fold_hit[0]}）")
    _assert_eq(_hang("TOK=$(printf 'x\\n' | GIT_TERMINAL_PROMPT=0 git credential fill)\n"), [],
               "放行：粘在命令前的 GIT_TERMINAL_PROMPT=0")
    _assert_eq(_hang("export GIT_TERMINAL_PROMPT=0\n"
                     "TOK=$(printf 'x\\n' | git credential fill)\n"), [],
               "放行：文件级 export 一次写清，逐行不必再带（这条豁免只认 export 那种写法）")
    _got = _hang("# helper 里没凭证时 git credential fill 会去开 /dev/tty\n"
                 "TOK=$(printf 'x\\n' | GIT_TERMINAL_PROMPT=0 git credential fill)\n")
    _assert_eq(_got, [],
               "注释里那句「git credential fill」不是调用：不滤掉就会得到一条与真缺陷一模一样的"
               "假红，而假红的代价从来都是人开始跳过这一步")
    _got = gate.check_no_prompt_hang({})
    _assert_eq(len(_got), 1, f"反向对照：一份含凭证调用的 shell 都没读到不能报绿（实际={_got}）")
    _assert("都没读到" in _got[0], f"报的要是「没读到」而不是「都关好了」（实际={_got[0]}）")

    # ── 11) 20e：交付链目录里的每一份 shell 都必须在登记表里 ──
    # 由来（紧接着 20d，同一天）：取证脚本 debug/fetch_run_log.sh 一直不在 SHELL_SOURCES 里，
    # 于是文案卫生、bash -n、curl 有界、外部输出出处这四道门在它身上**全是空的**——它是本轮
    # 唯一一份「天天有人跑、却从没被任何一道门看过」的交付链脚本。20c 一接上就在那份脚本里
    # 红了一条真缺陷（日志包下坏一半照样以 PK 开头，错误响应会被当成「日志里没有错误」）。
    # 登记缺口不需要出事才修，需要的是让「新写一份没登记」当场红。
    _at = gate_src.find("shell_reg = check_shell_registration()")
    _assert(_at > 0, "硬门 main() 里真的调了 20e")
    _assert("bad += 1" in gate_src[_at:_at + 1200], "20e 报出来的问题要并进退出码")
    _assert("::error::交付链 shell 登记" in gate_src[_at:_at + 1200],
            "云端 annotation 要能直接照做：写明是「没登记」而不是「内容有问题」")
    _assert_eq(gate.check_shell_registration(), [],
               "20e：扫描目录里每一份 .sh 都在登记表或豁免表里")
    _seen = sum(1 for d in gate.SHELL_SCAN_DIRS for _p in (_REPO / d).glob("*.sh"))
    _assert_eq(_seen, len(gate.SHELL_SOURCES) + len(gate.SHELL_EXEMPT),
               f"扫到 {_seen} 份 .sh、登记 {len(gate.SHELL_SOURCES)} 份、豁免 "
               f"{len(gate.SHELL_EXEMPT)} 份：三个数对不上就是表和目录已经分叉")
    with _patch(gate, "SHELL_SOURCES",
                tuple(x for x in gate.SHELL_SOURCES if x != "debug/fetch_run_log.sh")):
        _got = gate.check_shell_registration()
    _assert(any("fetch_run_log.sh" in x for x in _got),
            f"反向对照：把取证脚本从表里摘掉必须当场点名（这就是本轮那个真实缺口）（实际={_got}）")
    with _patch(gate, "SHELL_SOURCES",
                tuple(x for x in gate.SHELL_SOURCES if x != "debug/fetch_run_log.sh")), \
            _patch(gate, "SHELL_EXEMPT", ("debug/fetch_run_log.sh",)):
        _assert_eq(gate.check_shell_registration(), [],
                   "豁免表也算登记：但只有写进表才行，「我觉得这份不用判」不构成豁免")
    with _patch(gate, "SHELL_SCAN_DIRS", ("debug/zz_no_such_dir_ad",)):
        _got = gate.check_shell_registration()
    _assert(any("不在位" in x for x in _got),
            f"反向对照：扫描目录读不到要说出口（默认口径全靠这几个目录）（实际={_got}）")
    _assert(any("一份 .sh 都没看到" in x for x in _got),
            f"反向对照：扫 0 个文件的「登记齐全」是自欺，两条都要响（实际={_got}）")
    _empty = Path(tempfile.mkdtemp(prefix="shell_reg_empty_"))
    with _patch(gate, "SHELL_SCAN_DIRS", (str(_empty),)):
        _got = gate.check_shell_registration()
    _assert_eq(sum(1 for x in _got if "一份 .sh 都没看到" in x), 1,
               f"空目录（目录在位、里面没 shell）也只响那一条自欺（实际={_got}）")

    # ── 12) 20f：独立起来的 python 必须自己带 fd 0 ──
    # 由来（2026-11-13 本机实发，就在分块续传把 261 MB 完整取回来之后）：脚本被后台启动、启动它
    # 的那个终端随后被回收，于是 fd 0 是一个**已关闭**的描述符；python 在解释器初始化阶段就崩：
    #   Fatal Python error: init_sys_streams: can't initialize sys standard streams
    #   OSError: [Errno 9] Bad file descriptor
    # 崩在 `-c`/脚本体第一行之前，所以门自己的 print 一句都不会出现，日志里只剩上一行的标题——
    # 「交付 zip 的门不合格」成了唯一线索，而真正的失败是门根本没跑起来。这一条和 20a/20d 同族
    # （都是「不会自己停 / 不会自己说话」），形状不同：那种是等待，这种是连启动都没完成。
    for fn in ("check_python_stdin", "_python_command_at", "_quote_masks", "_py_interp_vars"):
        _assert(callable(getattr(gate, fn, None)), f"硬门模块里有 {fn}（能被单独喂夹具）")
    _at = gate_src.find("stdin_gaps = check_python_stdin()")
    _assert(_at > 0, "硬门 main() 里真的调了 20f（函数在而不调＝这条门从没跑过）")
    _assert("bad += 1" in gate_src[_at:_at + 1200],
            "20f 报出来的问题要并进退出码（只 print 不改退出码的门等于公告栏）")
    _assert("::error::解释器 stdin 独立" in gate_src[_at:_at + 1200],
            "云端 annotation 要能直接照做：写明是「没带 fd 0」而不是「python 报错」")
    _assert_eq(gate.check_python_stdin(), [],
               "20f：交付链手写脚本今天每一次独立起 python 都自带 fd 0")
    _py_texts, _py_miss = gate.shell_gate_inputs(("python",))
    _assert_eq(_py_miss, [], "20f 读的仍是第 20 条那张登记表（各抄一遍就会和交付链分叉）")

    def _pyin(sh: str) -> list:
        return gate.check_python_stdin({"ad": sh})

    # 逐份证明「这一份真的在门里跑」，而不是钉一个调用点总数（钉总数会在交付链加减时假红，
    # 20a/20d 那两格都量过）。手法是把它的 stdin 界整段摘掉：摘完还全绿＝这份根本没被读到。
    for _rel in ("debug/build_b2bcollect_mac_local.sh", "debug/fetch_b2bcollect_artifacts.sh",
                 "debug/run_offline_regression.sh"):
        _t = _py_texts[_rel]
        _裸 = _t.replace(" < /dev/null", "").replace("</dev/null>", "")
        _assert(_裸 != _t, f"{_rel} 今天带着 < /dev/null（一句都没有＝这份不在 20f 视野里）")
        _got = gate.check_python_stdin({_rel: _裸})
        _assert(len(_got) >= 1,
                f"反向对照：{_rel} 摘掉 stdin 界必须当场响（实际={_got[:1]})")

    # 本轮真实坏掉的那一句（取制品里跑交付 zip 门的那句，去掉界就是当时的原字节）
    _zip_line = [ln for ln in _py_texts["debug/fetch_b2bcollect_artifacts.sh"].splitlines()
                 if "check_release_zip.py" in ln and "--os-extract" in ln
                 and ln.lstrip().startswith("if ! python3")]
    _assert_eq(len(_zip_line), 1, f"交付链里跑 zip 门的那一句应当只有一处（实际 {len(_zip_line)} 处）")
    _assert(" < /dev/null" in _zip_line[0],
            "今天这一句自带 fd 0——它崩过一次才有这条门")
    _got = _pyin(_zip_line[0].replace(" < /dev/null", "") + "\n")
    _assert_eq(len(_got), 1, f"去掉界的那一句原字节必须响（实际={_got}）")

    _assert_eq(_pyin('printf x | python3 -c "print(1)"\n'), [],
               "放行：管道右侧的 stdin 由上一条命令的 stdout 给出，本来就不是「独立起来」")
    _assert_eq(_pyin("python3 - <<'PY'\nprint(1)\nPY\n"), [],
               "放行：heredoc 显式喂了 stdin（`<<` 和 `< /dev/null` 是同一件事的两面）")
    _assert_eq(_pyin('echo "可选：PY=/path/to/python 指定解释器"\n'
                     'python3 x.py < /dev/null\n'), [],
               "放行：双引号里那句 PY=… 只是文案。把它当调用点就是一条假红，而假红的代价从来都是"
               "人开始跳过这一步（本轮实测第一版就红在这一句上）")
    _assert_eq(_pyin('PY=$(command -v python3 || true)\nwhich python3\n'
                     'python3 x.py < /dev/null\n'), [],
               "放行：`command -v` 与 `which` 只是在问解释器在哪儿，不启动解释器")
    _assert_eq(_pyin('# 这一路以前跑的是 python3 build.py，不带 stdin\n'
                     'python3 x.py < /dev/null\n'), [],
               "放行：注释里那句不是调用（与 20a/20d 同一个「注释不算判据」的口径）")
    _got = _pyin('PY=.venv/bin/python\n"$PY" x.py\n')
    _assert_eq(len(_got), 1,
               f"反向对照：装在变量里的解释器也要认——今天跑构建与批跑的正是 `\"$PY\"` 那几路，"
               f"只认字面 python3 得到的是一条彻底的假绿（实际={_got}）")
    _assert("第 2 行" in _got[0],
            f"响的必须是那一行调用而不是上面的赋值行（这条门的读者照着行号去改脚本）（实际={_got[0]}）")
    _assert_eq(_pyin('PY=python3\npython3 x.py < /dev/null\n'), [],
               "放行：`PY=python3` 那一行是赋值右值，不是执行")
    _got = _pyin('PY=${ROOT}/.venv/bin/python\n${PY} x.py\n')
    _assert_eq(len(_got), 1, "反向对照：`${PY}` 花括号写法同认（实际=" + str(_got) + "）")
    _got = _pyin('PY=.venv/bin/python\ntimeout -s ABRT -k 10 "$PY" -u -X faulthandler "$f" '
                 '>"$log" 2>&1\n')
    _assert_eq(len(_got), 1,
               f"反向对照：隔了一层看门狗包装器仍然是被执行（批跑那条就是这个形状），"
               f"按「前面有东西就不算」一刀切得到的就是假绿（实际={_got}）")

    _got = gate.check_python_stdin({})
    _assert_eq(len(_got), 1, f"反向对照：一份文本都没读到时不能报绿（实际={_got}）")
    _assert("都没读到" in _got[0], f"报的要是「没读到」而不是「都独立了」（实际={_got[0]}）")
    _got = _pyin('echo 这里只有 echo\n')
    _assert(any("一处 python 调用都没找到" in x for x in _got),
            f"反向对照：扫到 0 处调用也是红，不是绿（实际={_got}）")
    with _patch(gate, "SHELL_SOURCES", ("debug/zz_nope_ad.sh",)):
        _got = gate.check_python_stdin()
    _assert(any("不在位" in x for x in _got),
            f"反向对照：登记的脚本读不到要说出口（默认口径全靠这张表）（实际={_got}）")
    # 已知边界（写在案上，免得这条门给人「所有 python 都被管住了」的错觉）：workflow 的 bash 步
    # 刻意不判，因为 Actions 给每一步的 stdin 本身就是关好的——本机「终端被回收」这件事在云端
    # 不存在，把它写成云端要求得到的只是 11 条噪音（判据越界和判据缺失一样会逼人绕过它）。
    # 但「不判」不等于「看不到」：单独喂 workflow 那几段，它当场响，说明这一格过滤自己在做事。
    _wf_only = {k: v for k, v in _py_texts.items() if not k.endswith(".sh")}
    _assert(len(_wf_only) >= 2,
            f"两份 workflow 的 bash 步确实在 shell_gate_inputs 视野里（实际 {len(_wf_only)} 段）")
    _assert(len(gate.check_python_stdin(_wf_only)) >= 1,
            "边界要能自证：workflow 侧不是「没有 python 调用」，而是被默认口径过滤掉了")


def test_qt_shutdown_teardown_gate():
    print("\n═══ AE. 关工具不许弹系统崩溃报告：Qt 退出顺序（第 21 条硬门）═══")
    # 由来（2026-11-14 本机自审抓到，同一个根的两条形状）：
    # ① test_site_enrich_offline.py 打印完「全部通过 ✅ (127/127)」仍以 rc=139 收场，12 次里崩 1 次，
    #    而且崩在汇总**之后**。批跑只看退出码，一条全绿的套件就这样被读成一次失败；
    # ② 同一形状写在四个生产工具的入口上：`sys.exit(app.exec_())`——那句后面一个字都放不进去，
    #    主窗口只能留给解释器拆。app 比窗口**先**定义，解释器拆帧按定义顺序清局部变量，于是
    #    QApplication 先析构、窗口 wrapper 后析构，PyQt 拆剩下那些 wrapper 撞上的是一具已经随
    #    app 没掉的 C++ 对象。用户侧的表现是「关掉工具弹一次系统崩溃报告」，顺带 closeEvent 里
    #    最后一次自动保存可能根本没跑。
    # 这条门判的是**销毁顺序**，跑起来才知道的事测不到（崩不崩取决于 GC 时机），只能静态判；
    # 而静态判据最容易红在文案上，所以下面每一格都要证明「改真代码才响」：注释不算、弹窗不算、
    # 循环写法与连着赋不算、喂进来的源码读不到时不许安静报绿。
    gate = _manifest_gate()
    gate_src = (_REPO / ".github" / "scripts" / "check_packaging_manifest.py").read_text(
        encoding="utf-8")

    def _src(*lines: str) -> str:
        return "\n".join(lines) + "\n"

    def _one(text: str, name: str = "ae_entry.py") -> list:
        return gate.check_qt_shutdown_teardown({name: text})

    # ── 1) 接线：判据在、被 main() 调用、结果进退出码、云端留下 annotation ──
    for fn in ("check_qt_shutdown_teardown", "_qt_code_lines", "_qt_build_py_files", "_qt_exec_res"):
        _assert(callable(getattr(gate, fn, None)),
                f"硬门模块里有 {fn}（能被单独喂夹具，才谈得上反向对照）")
    _at = gate_src.find("qt_teardown = check_qt_shutdown_teardown()")
    _assert(_at > 0, "硬门 main() 里真的调了第 21 条（函数在而不调＝这条门从没跑过）")
    _assert("bad += 1" in gate_src[_at:_at + 1200],
            "报出来的问题要并进退出码（只 print 不改退出码的门等于公告栏）")
    _assert("::error::Qt 退出顺序" in gate_src[_at:_at + 1200],
            "云端 annotation 要能直接照做：写明是「窗口没在 app 之前交出去」，不是「关工具会崩」")

    # ── 2) 今天的真仓库：五个入口 + 所有造过主窗口的套件都在判据里，而且全绿 ──
    _assert_eq(gate.check_qt_shutdown_teardown(), [],
               "第 21 条：跑事件循环的入口都先接住返回值再收尾，造过主窗口的套件都带着同一条收尾")
    _files = gate._qt_build_py_files()
    _assert(len(_files) >= 100,
            f"扫描范围按目录发现（*_build/*.py）：今天 {len(_files)} 份——以后多一个工具不必登记，"
            f"它自动在判据里")

    # ── 3) 入口那一路：形态只认「先接住返回值，再在 sys.exit 之前收尾」──
    _HEAD = _src("import sys", "from PyQt5.QtWidgets import QApplication",
                 "from tool_gui import MainWindow", "")
    _ENTRY_OK = _HEAD + _src(
        "app = QApplication(sys.argv)", "window = MainWindow()", "window.show()",
        "rc = app.exec_()", "try:", "    if window.isVisible():", "        window.close()",
        "except Exception:", "    pass", "window = None", "sys.exit(rc)")
    _assert_eq(_one(_ENTRY_OK), [], "放行：先接住返回值，close 与置 None 都落在 sys.exit 之前")
    _got = _one(_HEAD + _src("app = QApplication(sys.argv)", "window = MainWindow()",
                             "window.show()", "sys.exit(app.exec_())"))
    _assert_eq(len(_got), 1, f"反向对照：`sys.exit(app.exec_())` 一律红（实际={_got}）")
    _assert("把返回值直接吞了" in _got[0],
            f"响的要是那一句本身，别的形态（没收尾）不该混在这一格（实际={_got[0]}）")
    # 同一种坏写法换一种建 app 的句式也必须响，而且响在同一句结论上：b2bcollect_gui.py 一份文件里
    # 两种句式都在用，只认 `QApplication(` 开头的会让它掉进「套件」那一路——报的错与实际要改的行不符。
    _got = _one(_HEAD + _src("app = QApplication.instance() or QApplication(sys.argv)",
                             "window = MainWindow()", "window.show()", "sys.exit(app.exec_())"))
    _assert_eq(len(_got), 1, f"反向对照：`.instance() or` 那种建法同样是入口（实际={_got}）")
    _assert("把返回值直接吞了" in _got[0],
            f"结论必须是入口那一条，不是「补个 hide 就行」（实际={_got[0]}）")
    _got = _one(_HEAD + _src("app = QApplication(sys.argv)", "window = MainWindow()",
                             "app.exec_()", "sys.exit(0)"))
    _assert_eq(len(_got), 1, f"反向对照：不先接住返回值，收尾就没有落脚的地方（实际={_got}）")
    _assert("先接住返回值" in _got[0], f"要教的是那句写法本身（实际={_got[0]}）")
    _got = _one(_HEAD + _src("app = QApplication(sys.argv)", "window = MainWindow()",
                             "rc = app.exec_()", "sys.exit(rc)"))
    _assert_eq(len(_got), 2, f"反向对照：close 与置 None 是两条各判各的（实际={_got}）")
    _assert(any("close/hide" in x for x in _got) and any("置 None" in x for x in _got),
            f"两条都要点名，缺一行人只会照着其中一条改（实际={_got}）")
    _assert_eq(len(_one(_HEAD + _src("app = QApplication(sys.argv)", "window = MainWindow()",
                                     "rc = app.exec_()", "window.close()", "sys.exit(rc)"))), 1,
               "只缺置 None 时只响一条：引用还在，refcount 不当场拆 C++ 对象，那次 close 不算收尾")
    _assert_eq(len(_one(_HEAD + _src("app = QApplication(sys.argv)", "window = MainWindow()",
                                     "rc = app.exec_()", "window = None", "sys.exit(rc)"))), 1,
               "只缺 close/hide 时也只响一条：对象还在树上，置 None 也拆不动它")
    _got = _one(_HEAD + _src("app = QApplication(sys.argv)", "window = MainWindow()",
                             "rc = app.exec_()", "sys.exit(rc)", "window.close()", "window = None"))
    _assert_eq(len(_got), 2,
               f"反向对照：收尾写在 sys.exit **之后**等于没写——那句已经不会再执行了（实际={_got}）")
    _got = _one(_HEAD + _src("app = QApplication(sys.argv)", "rc = app.exec_()",
                             "window = None", "sys.exit(rc)"))
    _assert_eq(len(_got), 1, f"反向对照：跑着循环却认不出主窗口变量也要响，不许安静跳过（实际={_got}）")
    _assert("认不出主窗口变量" in _got[0],
            f"结论要写清「要么写清楚，要么连判据一起改」，安静跳过才是这条门最坏的形状（实际={_got[0]}）")

    # ── 4) 注释与文案不构成判据（这条门最容易红在自己写的说明上）──
    _assert_eq(_one(_HEAD + _src("app = QApplication(sys.argv)", "window = MainWindow()",
                                 "rc = app.exec_()",
                                 "# 以前这里是 sys.exit(app.exec_())，现在先接住返回值再收尾",
                                 "window.close()", "window = None", "sys.exit(rc)")), [],
               "放行：注释里那句旧写法不是现场代码（门读注释＝门在判文案，本轮第一版就红在这里）")
    _assert_eq(_one(_HEAD + _src("app = QApplication(sys.argv)", "window = MainWindow()",
                                 "rc = app.exec_()", "window.close()  # 趁 app 还活着",
                                 "window = None  # 交出去", "sys.exit(rc)")), [],
               "放行：行尾注释不该把收尾读没（拆代码时要连着尾注释一起剥）")
    _got = _one(_HEAD + _src("app = QApplication(sys.argv)", "window = MainWindow()",
                             "rc = app.exec_()", "# window.close()", "# window = None",
                             "sys.exit(rc)"))
    _assert_eq(len(_got), 2, f"反向对照：只把两行注释掉不算收尾（实际={_got}）")

    # ── 5) 弹窗不是入口循环：套件里 dlg.exec_() / QMessageBox.exec_ 到处都是 ──
    _BOUND = _HEAD + _src(
        "def _ask(text):", "    dlg = QMessageBox()", "    n = dlg.exec_()", "    return text",
        "", "app = QApplication(sys.argv)", "window = MainWindow()", "window.show()",
        '_ask("确认吗")', "rc = app.exec_()", "window.close()", "window = None", "sys.exit(rc)")
    _assert_eq(_one(_BOUND), [],
               "放行：`n = dlg.exec_()` 那行不是入口——旧口径按「任意 x = y.exec_()」取第一处，"
               "于是主窗口去弹窗上方找，找不到就报「判据范围没覆盖到」，是一条彻头彻尾的假红")
    _assert(len([x for x in _one(_BOUND) if "范围没覆盖" in x]) == 0,
            "同上一条的直白版：这份夹具绝不能报「范围没覆盖到」")
    _assert_eq(_one(_HEAD + _src("app = QApplication.instance() or QApplication(sys.argv)",
                                 "w = MainWindow()", "n = QMessageBox.exec_", "w.hide()",
                                 "w = None")), [],
               "放行：套件里出现 exec_ 字样但**不是它自己那个 app**，仍按套件一路判收尾")

    # ── 6) 套件那一路（不进 exec_）：hide/close + 单独置 None 才算交出窗口 ──
    _SUITE_OK = _HEAD + _src(
        "app = QApplication.instance() or QApplication(sys.argv)", "w = MainWindow()",
        "check('窗口建得出', w is not None)", "w.hide()", "w = None", "app.processEvents()")
    _assert_eq(_one(_SUITE_OK, "ae_suite.py"), [], "放行：套件按生产同一条口径收尾")
    _got = _one(_HEAD + _src("app = QApplication.instance() or QApplication(sys.argv)",
                             "w = MainWindow()", "check('窗口建得出', w is not None)"),
                "ae_suite.py")
    _assert_eq(len(_got), 1, f"反向对照：不收尾的套件退出码会随机 139（实际={_got}）")
    _assert("`w = MainWindow(`" in _got[0], f"要报出是哪个变量（实际={_got[0]}）")
    _assert_eq(len(_one(_HEAD + _src("app = QApplication(sys.argv)", "w = MainWindow()",
                                     "w2 = MainWindow()", "w.hide()", "w = None"), "ae_suite.py")), 1,
               "两个窗口只收一个：响的还是没收那一个，别把已收尾的也报进去")
    _got = _one(_HEAD + _src("app = QApplication(sys.argv)", "w = MainWindow()",
                             "w2 = MainWindow()", "w.hide()", "w = None"), "ae_suite.py")
    _assert("`w2 = MainWindow(`" in _got[0], f"点名的必须是 w2（实际={_got[0]}）")
    _got = _one(_HEAD + _src("app = QApplication(sys.argv)", "w = MainWindow()", "w2 = MainWindow()",
                             "for _x in (w, w2):", "    _x.hide()", "w = None", "w2 = None"),
                "ae_suite.py")
    _assert_eq(len(_got), 2,
               f"反向对照：绕成 for 循环收尾，门认不出来（本轮实测撞上过）。这条口径**故意**严："
               f"让门去猜循环目标，就要再猜元组、列表、生成器、别的函数；写两行比猜便宜（实际={_got}）")
    _got = _one(_HEAD + _src("app = QApplication(sys.argv)", "w = MainWindow()", "w.hide()",
                             "w = w2 = None"), "ae_suite.py")
    _assert_eq(len(_got), 1,
               f"反向对照：`w = w2 = None` 连着赋同样不算——判的是这个变量自己有没有被单独置 None"
               f"（本轮实测也撞上过）（实际={_got}）")
    _got = _one(_src("from m import MainWindow", "", "w = MainWindow()", "w.show()"),
                "ae_nohelper.py")
    _assert_eq(len(_got), 1,
               f"没有 QApplication 也照判收尾：套件常从隔壁模块拿到 app，认不出 app 不等于窗口"
               f"就交给了解释器（实际={_got}）")

    # ── 7) 门自己不许 fail-open：读不到源码时不能报绿（本轮反向对照抓到的真缺陷）──
    _got = gate.check_qt_shutdown_teardown({})
    _assert_eq(len(_got), 1, f"反向对照：喂进来的源码是空的，必须响（实际={_got}）")
    _assert("0 份源码" in _got[0], f"响的要还是「没读到」（实际={_got[0]}）")
    _got = gate.check_qt_shutdown_teardown({"ae_plain.py": "print(1)\n"})
    _assert_eq(len(_got), 1, f"反向对照：读到了源码但一个 Qt 形状都没判到，同样响（实际={_got}）")
    _assert("一个 Qt 入口/主窗口都没判到" in _got[0], f"（实际={_got[0]}）")
    _empty = Path(tempfile.mkdtemp(prefix="qt_gate_empty_root_"))
    with _patch(gate, "ROOT", _empty):
        _got = gate.check_qt_shutdown_teardown()
    _assert_eq(len(_got), 1,
               f"反向对照：扫描目录落空（默认口径全靠 *_build 那几个目录）时要响（实际={_got}）")

    # ── 8) 逐份证明「真仓库里那几份确实在门里跑」：把两行各摘一次，当场要响 ──
    # 钉调用点总数会在交付链加减时假红，量「这一份被摘掉关键行后响不响」才说明这一格真读到了它。
    _by_name = {}
    for _p, _t in _files.items():
        _by_name.setdefault(_p.name, _t)
    _none_re = re.compile(r"^\s*\w+\s*=\s*None\s*$", re.M)
    _tear_re = re.compile(r"^\s*\w+\s*\.\s*(?:close|hide)\s*\(\)\s*$", re.M)
    _watch = ("b2bcollect_gui.py", "riskbird_gui.py", "ali1688_gui.py", "phone_verifier_gui.py",
              "wechat_helper_gui.py", "test_keyword_resume_offline.py",
              "test_site_enrich_offline.py", "test_waterfall_leads_offline.py",
              "test_integration_offline.py", "test_gui.py", "gen_guide_images.py")
    # 名单里只放**随仓库走**的文件。原先还带着 debug_dual_open_check.py，而它命中
    # riskbird_build/.gitignore 的 `debug_*` 规则（本地临时复现脚本，本来就不交付）：
    # 于是这条「不许靠登记表」的点名在干净克隆里永远满足不了（2026-11-20 实测：
    # 克隆里 AE 节就在 phone_verifier_gui.py 这一步响，而同一份代码本机绿）。
    # 拿不存在的文件当证人，比不列它更糟——它让「克隆跑不通」看起来像产品写坏了。
    # （同期发现：phone_verifier_build/ 与 wechat_helper_build/ 两个工具整体没入库，
    # 已于本日补回；那条才是真正该红的——产品代码进不了仓库就不是「登记不全」而是「少交工具」。）
    for _n in _watch:
        _assert(_n in _by_name, f"{_n} 在门视野里（按目录发现，挪目录或改名要在这里响，不许靠登记表）")
        _t = _by_name[_n]
        _assert(_tear_re.search(_t) is not None and _none_re.search(_t) is not None,
                f"{_n} 今天就带着那两行——摘得到，才谈得上证明它真被读到")
        _got = _one(_none_re.sub("", _t), _n)
        _assert(len(_got) >= 1, f"摘掉「置 None」那行，{_n} 当场响（实际={_got[:1]}）")
        _got = _one(_tear_re.sub("", _t), _n)
        _assert(len(_got) >= 1, f"摘掉 close/hide 那行，{_n} 当场响（实际={_got[:1]}）")
    # 反方向的自证：把某一份从输入里拿掉，别的影响不到它——说明报的每一条都归得到具体文件
    _without = {p: t for p, t in _files.items() if p.name != "riskbird_gui.py"}
    _assert_eq(gate.check_qt_shutdown_teardown(_without), [],
               "摘掉一份真文件不会让别人变红：归属是干净的（一份一条，不是一锅糊结论）")
    _withbad = dict(_without)
    _withbad[_REPO / "riskbird_build" / "zz_bad_entry.py"] = _HEAD + _src(
        "app = QApplication(sys.argv)", "window = MainWindow()", "sys.exit(app.exec_())")
    _got = gate.check_qt_shutdown_teardown(_withbad)
    _assert_eq(len(_got), 1, f"喂一份坏的进去，只响那一份（实际={_got}）")
    _assert("zz_bad_entry.py" in _got[0], f"结论要带上文件名，否则批跑里没人知道改哪（实际={_got[0]}）")


def test_state_file_protection_gate():
    print("\n═══ AF. 凭证与登录态不许进历史：状态文件防护（第 22 条硬门）═══")
    # 由来（2026-11-14 本轮提交时自审抓到）：一句**常规**命令 `git add b2bcollect_build
    # riskbird_build`——不带 -A、不是危险命令、当时还专门为了「只加该加的文件」而列出目录名——
    # 索引里当场多出 `_云展账号.json`（绑定手机号）、`acl/.acl_claim.json`（三个账号各自的
    # 32 位 session，键就是手机号）、`acl/.acl_device.json`（设备指纹）、chromedriver 二进制
    # 和一个 10 MB 的 zip。拦下它的不是我眼尖，是我在提交前把那几行读了一遍键名；而它们能被
    # 扫进来只因为一件事：工具跑几天后会在包目录里**自己写出**这些文件。
    # 这一类比「有人手滑提交了密钥」更危险，因为前者的触发条件是日常操作，后者需要人刻意犯错；
    # 而且历史里的凭证删不掉——push 一次就永久公开，只能作废重发。
    # 所以这条门的两个判据都不许安静通过：①名单从代码常量里抽（抄一份文件名就会在某天改名时
    # 变成「门还在、名单已过期」）；②「挡住没有」问 git 自己的 check-ignore，不在 Python 里
    # 重抄一遍 gitignore 匹配——重抄就分叉，而分叉的方向永远是「你以为挡住了」。
    gate = _manifest_gate()
    gate_src = (_REPO / ".github" / "scripts" / "check_packaging_manifest.py").read_text(
        encoding="utf-8")

    # ── 0) 夹具用的合成口径：把「git 眼里被跟踪的文件」换成给定清单 ──
    @contextlib.contextmanager
    def _fake_index(paths):
        """两条判据里只有这一条的输入能合成（另一条问的是 git 的 ignore 解析）。

        真仓库那一格（下面第 8 节）验的是另一件事——把 .gitignore 拿走之后 git 怎么说。
        """
        _old = gate._tracked_sets
        gate._tracked_sets = lambda: (set(paths), set(paths))
        try:
            yield
        finally:
            gate._tracked_sets = _old

    # ── 1) 接线：函数在、main 真调、调了要进退出码、云端 annotation 能照做 ──
    for _fn in ("state_names_from_code", "check_state_files_not_tracked", "_check_state_shape"):
        _assert(callable(getattr(gate, _fn, None)), f"门模块里有 {_fn}（能被单独喂夹具）")
    _at = gate_src.find("state_gaps = check_state_files_not_tracked()")
    _assert(_at > 0, "硬门 main() 里真的调了第 22 条（函数在而不调＝这条门从没跑过）")
    _assert("bad += 1" in gate_src[_at:_at + 1200],
            "第 22 条报出来的问题要并进退出码（只 print 不改退出码的门等于公告栏）")
    _assert("::error::状态文件防护" in gate_src[_at:_at + 1200],
            "云端 annotation 要能直接照做：写明是「状态文件没被挡住」而不是「git 报错」")

    # ── 2) 真仓库全绿，而且绿得有内容：名单确实是从代码里抽出来的 ──
    _names = gate.state_names_from_code()
    _assert_eq(gate.check_state_files_not_tracked(), [],
               "第 22 条：今天的仓库既没有凭证被跟踪，也没有凭证形状漏出 .gitignore")
    _want = {"config.json", ".acl_cache.json", ".acl_device.json", ".acl_claim.json",
             "_云展账号.json", "_采集任务索引.json", "采集进度.json", "mapping_history.json",
             "deepseek_cache.json"}
    _assert(_want <= set(_names),
            f"凭证/登录态/状态文件一个都不许漏出名单（漏了就是那一类文件永远没人判）"
            f"（实际缺 {sorted(_want - set(_names))}）")
    _assert(all(v.rsplit(":", 1)[-1].endswith("_NAME") for v in _names.values()),
            f"每条名单都要报得出是哪个常量给的（实际={_names}）")

    # ── 3) 派生口径的正反：改名自动跟、注释不算、非 .json 不算（别把用户数据拦掉） ──
    _got = gate.state_names_from_code({"m.py": 'NEW_STATE_NAME = "zz_af_new.json"\n'})
    _assert_eq(_got, {"zz_af_new.json": "m.py:NEW_STATE_NAME"},
               "反向对照：今天新声明一份状态文件，名单当场就带上它（手写清单才会漏）")
    _assert("zz_af_new.json" not in gate.state_names_from_code(),
            "合成常量不落到真名单里（夹具只喂那一份文本）")
    _assert_eq(gate.state_names_from_code(
                   {"m.py": '# OLD_STATE_NAME = "zz_af_comment.json"\n'
                            '    # INDENT_STATE_NAME = "zz_af_indent.json"\n'}), {},
               "放行：注释里的常量声明不算——门读代码不读文案（与第 21 条同一条口径）")
    _assert_eq(gate.state_names_from_code(
                   {"m.py": 'RAW_FILE_NAME = "企业数据_原始.xlsx"\n'
                            'DIR_NAME = "zz_af_dir"\n'
                            'NESTED_NAME = "sub/zz_af_nested.json"\n'}), {},
               "放行：非 .json 的产出名（采集到的数据、目录名）不进名单——这条门不许把用户"
               "真正要保存的东西挡在提交外面")

    # ── 4) 「挡住没有」问的是 git：同一条形状的正反差分 ──
    _assert_eq(gate.check_state_files_not_tracked({".acl_claim.json": "夹具"}), [],
               "放行：真被 .gitignore 挡住的名字，喂进夹具也不该响（挡住是 git 说的，不是我们说的）")
    _got = gate.check_state_files_not_tracked({"zz_af_not_ignored.json": "夹具"})
    _assert_eq(len(_got), 1,
               f"反向对照：没被挡住的名字必须响；两个探法（根目录与嵌套）合起来只报一条，"
               f"同一条规则漏了报两遍是噪音（实际={_got}）")
    _assert("没被 .gitignore 挡住" in _got[0] and "、" in _got[0],
            f"要说清是「没挡住」并把两个探法都列出来（实际={_got[0]}）")
    _assert("a/b/zz_af_not_ignored.json" in _got[0],
            f"嵌套那一路也要探：只在根上写一条规则的 ignore 挡不住包目录里生成的文件"
            f"（实际={_got[0]}）")

    # ── 5) 已经在历史里的那种红：另一条判据，形状不同、处置也不同 ──
    with _fake_index({"b2bcollect_build/acl/.acl_claim.json", "b2bcollect_build/_云展账号.json"}):
        _got = gate.check_state_files_not_tracked({".acl_claim.json": "夹具",
                                                    "_云展账号.json": "夹具"})
    _assert_eq(len(_got), 2, f"反向对照：被跟踪的凭证逐条点名（实际={_got}）")
    _assert(all("已经被 git 跟踪" in p for p in _got),
            f"结论要是「已跟踪」而不是「没挡住」——两者处置完全不同：前者要 rm --cached 并重发"
            f"凭证，后者只要补一行 ignore（实际={_got[0]}）")
    _assert("acl/.acl_claim.json" in _got[0], f"要把真实路径写出来，人才知道删哪（实际={_got[0]}）")

    # ── 6) fail-closed：三种「读不到」都不许被读成「没问题」 ──
    _got = gate.check_state_files_not_tracked({})
    _assert(any("一个状态文件名都没抽到" in p for p in _got),
            f"反向对照：名单为空时不许报绿——常量写法一换，空名单下的「全部合格」就是假绿"
            f"（实际={_got}）")
    with _fake_index(set()):
        _got = gate.check_state_files_not_tracked({".acl_claim.json": "夹具"})
    _assert(any("只拿到 0 个被跟踪文件" in p for p in _got),
            f"反向对照：git 清单读空时「没被跟踪」这条自动成立，那种绿没有意义（实际={_got}）")
    with _patch(gate, "in_work_tree", lambda: False):
        _got = gate.check_state_files_not_tracked()
    _assert_eq(len(_got), 1, f"反向对照：不在 work tree 里就直接收，两个判据都问不到（实际={_got}）")
    _real_git = gate._git

    def _git_blind_checkignore(*args):
        # 只把 check-ignore 问不出结论这一条模拟出来：整扇 _git 一起打回 128 的话，
        # in_work_tree() 先失败，门会在上一格就直接收——那一格已经验过了，这一格验不到
        # 「退出码既非 0 也非 1 时按红算」这句本身（本轮实测红在这一句上）。
        return (128, "") if "check-ignore" in args else _real_git(*args)

    with _patch(gate, "_git", _git_blind_checkignore):
        _got = gate.check_state_files_not_tracked({".acl_claim.json": "夹具"})
    _assert(any("问不出结论" in p and "按不合格算" in p for p in _got),
            f"反向对照：check-ignore 退出码既不是 0 也不是 1 时按红算（实际={_got}）")
    # 一个名字两个探法（根目录 + 嵌套），形状那些各自按 DEFAULT_SHAPES 里登记的探法数
    _probe_count = sum(len(s[1]) for s in gate.DEFAULT_SHAPES) + 2
    _assert(sum(1 for p in _got if "问不出结论" in p) == _probe_count,
            f"每一个探法都要各自报一次「问不出」，不许合并成一句「git 有问题」"
            f"（期望 {_probe_count} 条，实际 {len(_got)} 条：{_got}）")
    _assert(gate._git is _real_git, "补丁收回：漏收会把后面所有用例的 git 都问成 128")

    # ── 7) 豁免要登记：「我觉得这份不用判」不构成豁免 ──
    with _patch(gate, "STATE_NAME_EXEMPT", {"zz_af_not_ignored.json": "夹具：本来就应当入库"}):
        _assert_eq(gate.check_state_files_not_tracked({"zz_af_not_ignored.json": "夹具"}), [],
                   "写进豁免表才算豁免")
    _assert(bool(gate.STATE_NAME_EXEMPT) is False, "今天没有任何豁免：任何一条都是当场加上去的")

    # ── 8) 摘掉 .gitignore 必须响：在临时目录里建一个**真** git 仓库复现当时的现场 ──
    # 前七格问的都是函数逻辑；这一格问的是「把那份 .gitignore 从这台机器上拿走，门还判得
    # 出来吗」。用真 `git init` + 真 `git add`，判据走的就是 git 自己的两条命令——也就是说，
    # 这一格重演的正是 2026-11-14 那句差点被提交的 `git add b2bcollect_build`。
    _bare = Path(tempfile.mkdtemp(prefix="state_gate_no_ignore_"))
    _saved_index, _saved_head = dict(gate._INDEX), dict(gate._HEAD)
    try:
        with _patch(gate, "ROOT", _bare):
            _assert(subprocess.run(["git", "init", "--quiet"], cwd=str(_bare),
                                   capture_output=True).returncode == 0, "夹具仓库建得起来")
            (_bare / "b2bcollect_build" / "acl").mkdir(parents=True)
            # 第三格（配置目录整目录防护）在本夹具里也要拿到**完整输入**：五个扫描目录都建出来、
            # 里面没有 Chrome 自己写的标志，于是它判到的结论是「这里确实没有配置目录」，
            # 而不是「扫不到目录」。少了这一句，本节计数会被 5 条扫描层问题污染，而那种红
            # 长得像「形状探针算错了」，逼人回去改期望值——改错了对象。
            for _d in gate.PROFILE_SCAN_DIRS:
                (_bare / _d).mkdir(parents=True, exist_ok=True)
            for _f, _c in ((".acl_claim.json", '{"13800000000":{"session":"夹具不是真号"}}'),
                           ("_云展账号.json", '{"phone":"13800000000"}'),
                           ("acl/.acl_device.json", '{"device_id":"夹具"}')):
                (_bare / "b2bcollect_build" / _f).write_text(_c, encoding="utf-8")
            _assert(subprocess.run(["git", "add", "b2bcollect_build"], cwd=str(_bare),
                                   capture_output=True).returncode == 0, "夹具 add 得动")
            gate._INDEX.clear()
            gate._HEAD.clear()
            _three = {".acl_claim.json": "夹具", "_云展账号.json": "夹具",
                      ".acl_device.json": "夹具"}
            _got = gate.check_state_files_not_tracked(_three)
        # 期望值从门自己登记的两组数据算出来，不写死数字：写死了就等于在钉「今天有几条
        # 形状」，将来加一类（比如新的登录态文件）时这一格会假红，而假红的代价是回来把
        # 期望值改大——那正好教会人跳过这一步。
        _n_names, _n_shapes = len(_three), len(gate.DEFAULT_SHAPES)
        _assert_eq(len(_got), _n_names + (_n_names + _n_shapes),
                   f"反向对照：没有 .gitignore 时，三个名字各响两条（「已经被跟踪」+「没被挡住」），"
                   f"另外 {_n_shapes} 类形状各响一条「没被挡住」——它们本来也是同一条 ignore "
                   f"挡住的（实际 {len(_got)} 条）")
        _assert(sum(1 for p in _got if "没被 .gitignore 挡住" in p) == _n_names + _n_shapes,
                f"「没挡住」要一条不少：名字与形状两侧都归这条门管（实际={_got}）")
        _assert(sum(1 for p in _got if "已经被 git 跟踪" in p) == _n_names,
                f"「已跟踪」三条一条不许少：当时真就差一步进历史（实际={_got}）")
        _assert(not any("只拿到 0 个" in p for p in _got),
                f"夹具仓库里确实有被跟踪的文件，fail-closed 那条不该被触发（实际={_got}）")
        _assert(not any("扫不到" in p for p in _got),
                f"第三格在本夹具里是「扫到了、没有配置目录」，不是「扫不到」：上面那句计数"
                f"等式才只归名字与形状两侧管（实际={_got}）")
    finally:
        gate._INDEX.clear()
        gate._INDEX.update(_saved_index)
        gate._HEAD.clear()
        gate._HEAD.update(_saved_head)

    # ── 9) 第三格：工作区里每一份 Chrome 配置目录，git 都必须看不见它下面的文件 ──
    # 由来（2026-11-15 本机自审抓到）：上面 1~8 格问的都是「按形状列出来的那几类文件」。
    # 而本机四个开发用 profile 里，`Local State`、`Default/Account Web Data`、
    # `Default/Web Data`、`Default/Session Storage/`、各种缓存与 .crx 一共 **1528 个文件**
    # 都没被挡住，门却报绿——形状清单式判据的失效方式从来不是报错，是列漏一类就安静漏一类。
    # 这一格判性质：认「这是一份 user-data-dir」（里面有 Chrome 自己写的 Local State 或
    # Default/），然后要求 `git status -uall` 对目录里每个文件都答「看不见」。
    # 夹具必须是**真 git 仓库**：判据问的是 git 自己的解析结果，在 Python 里合成一份清单
    # 就等于把 gitignore 又重抄一遍（正是第 1 段注释里说的那种分叉）。
    _pd_root = Path(tempfile.mkdtemp(prefix="state_gate_profile_dirs_"))
    _saved_idx, _saved_head = dict(gate._INDEX), dict(gate._HEAD)

    def _pinit(ignore_body=None):
        """夹具仓库：五个扫描目录都在位，.gitignore 默认取今天这份真规则。"""
        (_pd_root / ".gitignore").unlink(missing_ok=True)
        if ignore_body is not None:
            (_pd_root / ".gitignore").write_text(ignore_body, encoding="utf-8")
        for _d in gate.PROFILE_SCAN_DIRS:
            (_pd_root / _d).mkdir(parents=True, exist_ok=True)

    def _pmk(rel, names):
        d = _pd_root / rel
        for n in names:
            (d / n).parent.mkdir(parents=True, exist_ok=True)
            (d / n).write_text("夹具", encoding="utf-8")

    _REAL_IGNORE = (_REPO / ".gitignore").read_text(encoding="utf-8")
    # 本轮之前那份「按内部文件名一条条列」的规则，原样搬进夹具当反向对照的用材
    _OLD_IGNORE = ("**/Default/Cookies\n**/Default/Login Data\n"
                   "**/Default/History\n**/Default/Local Storage/\n")
    try:
        with _patch(gate, "ROOT", _pd_root):
            _assert(subprocess.run(["git", "init", "--quiet"], cwd=str(_pd_root),
                                   capture_output=True).returncode == 0,
                    "配置目录夹具仓库建得起来")
            (_pd_root / "README.md").write_text("夹具仓库\n", encoding="utf-8")
            _pinit(_REAL_IGNORE)
            _pmk("debug/ab_cdp_profile", ["Local State", "Default/Cookies", "junk.txt",
                                          "component_crx_cache/a.crx"])
            _pmk("b2bcollect_build/chrome_tool_profile", ["Local State", "Default/History"])
            # 认标志不认名字：一个恰好带 _profile 的**源码目录**不该被当成配置目录
            _pmk("riskbird_build/chrome_profile_src", ["main.py"])
            gate.reset_git_cache()
            _found, _miss = gate.find_profile_dirs()
            _assert_eq(_miss, [], f"五个扫描目录都在位时不该有扫描层问题（实际={_miss}）")
            _assert_eq(_found, ["b2bcollect_build/chrome_tool_profile", "debug/ab_cdp_profile"],
                       "配置目录按 Chrome 自己写的标志认（Local State / Default/），与目录叫"
                       "什么无关；只有 .py 的那个同名目录不算配置目录")
            _assert_eq(gate.check_profile_dirs_ignored(), [],
                       "带着今天这份 .gitignore，两份配置目录 git 一个文件都看不见")

            # 反向对照①：整份 .gitignore 拿走 → 两份目录各响一条，一条都不许合并
            _pinit("")
            gate.reset_git_cache()
            _got = gate.check_profile_dirs_ignored()
            _assert_eq(len(_got), 2, f"两份配置目录各响一条「没被整目录挡住」（实际={_got}）")
            _assert(all("没被 .gitignore 整目录挡住" in x for x in _got),
                    f"报的每一条都要给下一步能照着做的句子（实际={_got}）")

            # 反向对照②：只列内部文件名的旧规则（本轮之前的真实状态）——这就是 1528 那种量级
            _pinit(_OLD_IGNORE)
            gate.reset_git_cache()
            _got = gate.check_profile_dirs_ignored()
            _assert_eq(len(_got), 2, f"旧规则下两目录各自「看得见若干文件」（实际={_got}）")
            _dbg = [x for x in _got if "debug/ab_cdp_profile" in x][0]
            _assert("3 个文件" in _dbg,
                   f"旧规则漏掉的是 Local State / junk.txt / a.crx 这三个形状，报数必须是 3"
                   f"（列不全这件事要被量出来，而不是笼统一句「没挡住」）（实际={_dbg}）")
            _assert("永远会漏" in _dbg,
                    f"红话里要带上「为什么不是再补一行文件名的事」，否则下次还是补一行"
                    f"（实际={_dbg}）")

            # 反向对照③：真的进了历史 → 「已被跟踪」这一条要单独响
            _pinit("")
            gate.reset_git_cache()
            _assert(subprocess.run(["git", "add", "-f", "debug/ab_cdp_profile"],
                                   cwd=str(_pd_root), capture_output=True).returncode == 0,
                    "夹具能把这些文件真加进索引（真仓库里这一步只差一句命令）")
            gate.reset_git_cache()
            _got = gate.check_profile_dirs_ignored()
            _assert(any("debug/ab_cdp_profile" in x and "已经被 git 跟踪" in x for x in _got),
                    f"登录态进了历史要说得出来并给出 git rm --cached（实际={_got}）")

            # 反向对照④：扫描目录读不到 ≠「这里没有配置目录」
            _pinit(_REAL_IGNORE)
            gate.reset_git_cache()
            (_pd_root / "debug").rename(_pd_root / "debug_moved")
            _found2, _miss2 = gate.find_profile_dirs()
            _assert(any("debug" in x and "按不合格算" in x for x in _miss2),
                    f"扫描范围里少一个目录必须响，而不是安静地少判一路（实际={_miss2}）")
            (_pd_root / "debug_moved").rename(_pd_root / "debug")

            # 第三格确实接在第 22 条主函数上（不接就是写了一个没人调的判据）
            _got = gate.check_state_files_not_tracked({}, (), ["b2bcollect_build/no_such_x"])
            _assert(any("no_such_x" in x for x in _got) is False,
                    f"喂一个空目录进主函数不该凭空造红（实际={_got}）")
            _pinit("")
            gate.reset_git_cache()
            _got = gate.check_state_files_not_tracked({}, (), ["debug/ab_cdp_profile"])
            _assert(any("ab_cdp_profile" in x for x in _got),
                    f"第 22 条主函数要把第三格的结论并进自己那份列表里（实际={_got}）")
    finally:
        gate._INDEX.clear()
        gate._INDEX.update(_saved_idx)
        gate._HEAD.clear()
        gate._HEAD.update(_saved_head)
        gate.reset_git_cache()

    # 真仓库这一侧：今天的结论 + 「这条门不是只在夹具里咬人」
    _real_found, _real_miss = gate.find_profile_dirs()
    _assert_eq(_real_miss, [], f"本机的五个扫描目录都在位（实际={_real_miss}）")
    _assert_eq(gate.check_profile_dirs_ignored(), [],
               f"工作区里每一份 Chrome 配置目录都被整目录挡住（本机发现 {len(_real_found)} 份）")

def test_suite_exit_code_guard_gate():
    print("\n═══ AG. 全绿不许被读成失败：套件退出码口径（第 23 条硬门）═══")
    # 由来（2026-11-14 本机自审抓到）：test_keyword_resume_offline.py 打印完「全部通过：88 项
    # 断言」仍以 rc=139 收场——Qt 与线程的析构留在解释器收尾里，那次段错误与判定毫无关系，却
    # 把退出码改成非零。批跑只看退出码，一条全绿的套件就这样被读成一次失败，根因只能人工翻
    # 日志定位。同族的另一格长在批跑入口自己身上（挂死被配成 134/137，而实测看门狗返回它自己
    # 的 124），那一格由同一份文件里的 5c-IIB 用真信号钉住；这一节钉的是**静态口径**：清单里
    # 每一份碰过 Qt 的套件都要在判定之后自己 flush + os._exit，而且范围必须来自批跑清单——
    # 在门里重抄一份「哪些套件要跑」的表，就是下一次分叉的开始。
    gate = _manifest_gate()
    gate_src = (_REPO / ".github" / "scripts" / "check_packaging_manifest.py").read_text(
        encoding="utf-8")

    def _run_gate(texts, suites=None, root=None):
        """在临时目录里复现「清单就是这几份」：门读的是 ROOT/rel，夹具就得按相对路径落盘。"""
        root = root or Path(tempfile.mkdtemp(prefix="ag_exit_gate_"))
        for rel, body in texts.items():
            dst = root / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text(body, encoding="utf-8")
        with _patch(gate, "ROOT", root):
            return gate.check_suite_exit_code_guard(list(texts) if suites is None else suites)

    # ── 1) 接线：判据在、main 真调、调了进退出码、云端 annotation 能照做 ──
    for _fn in ("check_suite_exit_code_guard", "regression_suites", "_regression_scope_problem"):
        _assert(callable(getattr(gate, _fn, None)),
                f"门模块里有 {_fn}（能被单独喂夹具，才谈得上反向对照）")
    _at = gate_src.find("exit_guard = check_suite_exit_code_guard()")
    _assert(_at > 0, "硬门 main() 里真的调了第 23 条（函数在而不调＝这条门从没跑过）")
    _assert("bad += 1" in gate_src[_at:_at + 900],
            "第 23 条报出来的问题要并进退出码（只 print 不改退出码的门等于公告栏）")
    _assert("::error::套件退出码口径" in gate_src[_at:_at + 900],
            "云端 annotation 要能直接照做：写明是「判定与退出码不同源」，不是「套件失败」")

    # ── 2) 范围与批跑同源，而且真仓库今天全绿 ──
    _rc, _files, _err, _out, _chan = gate.regression_suites()
    _assert_eq(_rc, 0, f"批跑清单取得到（退出码｜{len(_files)} 行｜{_chan}）")
    _assert(len(_files) >= 50,
            f"--list 展开出 {len(_files)} 项：少于 50 说明发现逻辑或 --list 的 stdout 契约坏了")
    _assert(all(x.endswith(".py") and not x.startswith("/") and (_REPO / x).is_file()
                for x in _files),
            "--list 的 stdout 契约：每行一个仓库相对、磁盘上真实存在的 .py 路径（进度与诊断走 stderr）")
    _qt = [x for x in _files
           if gate._QT_SUITE_HINT.search(gate._qt_code_lines(
               (_REPO / x).read_text(encoding="utf-8", errors="replace")))]
    _assert(len(_qt) >= 20,
            f"清单里 {len(_qt)} 份碰过 Qt（少于 20 = 「Qt 字样」这一口径已经和现实脱节，"
            f"门会在自己没注意到的地方空转）")
    _assert_eq(gate.check_suite_exit_code_guard(), [],
               "第 23 条：今天清单里每一份 Qt 套件都以 sys.stdout.flush() + os._exit 收尾")

    # ── 3) 逐份自证：把真文件里那行 os._exit 摘掉，门当场要响并且点它的名 ──
    # 只钉「调用点在」是不够的：真仓库那条 ✅ 可能只是在判一个空集合。
    _exit_re = re.compile(r"^\s*os\._exit\(.*$", re.M)
    _shared_root = Path(tempfile.mkdtemp(prefix="ag_exit_gate_real_"))
    for _rel in _qt:
        _body = (_REPO / _rel).read_text(encoding="utf-8", errors="replace")
        _assert(_exit_re.search(_body) is not None,
                f"{_rel} 今天就带着 os._exit——摘得到，才谈得上证明它真在门里被读到")
        _got = _run_gate({_rel: _exit_re.sub("", _body)}, root=_shared_root)
        _assert(any(_rel in x and "没有 os._exit 收尾" in x for x in _got),
                f"摘掉收尾那行，{_rel} 当场响（实际={_got[:1]}）")

    # ── 4) 形态正反：口径严到「写法」这一层，否则同样的坑换一种写法就又躲过去了 ──
    _QT_HEAD = ("import os\nimport sys\nfrom PyQt5.QtWidgets import QApplication\n\n")
    _GOOD = (_QT_HEAD + "app = QApplication.instance() or QApplication(sys.argv)\n"
             "print('结果：7 通过 / 0 失败')\nrc = 0\n"
             "sys.stdout.flush()\nsys.stderr.flush()\nos._exit(rc)\n")
    _assert_eq(_run_gate({"ag_ok.py": _GOOD}), [],
               "放行：判定打完 → flush → os._exit 收尾（这一份就是清单里 25 份的共同形状）")
    _assert_eq(_run_gate({"ag_ok2.py": _GOOD.replace("os._exit(rc)\n",
                                                     "os._exit(rc)  # 把结论交给操作系统\n")}), [],
               "放行：行尾注释不该把收尾读没（拆代码要连着尾注释一起剥，与第 21 条同一口径）")
    _got = _run_gate({"ag_noclose.py": _GOOD.replace("os._exit(rc)\n", "")})
    _assert_eq(len(_got), 1, f"反向对照：没有 os._exit 收尾要响（实际={_got}）")
    _assert("sys.stderr.flush()" in _got[0],
            f"要把「最后一行代码是什么」原样打出来，人才知道差在哪一句（实际={_got[0][:90]}）")
    # 本轮 frozen 套件自己就是这一形状：结论打完交给解释器收尾，rc 随机 139。
    _got = _run_gate({"ag_oldshape.py": _QT_HEAD + "app = QApplication(sys.argv)\n"
                                                  "print('结果：7 通过 / 0 失败')\n"
                                                  "sys.exit(main())\n"})
    _assert_eq(len(_got), 1, f"反向对照：`sys.exit(main())` 结尾同样要响（实际={_got}）")
    _assert("sys.exit(main())" in _got[0], f"报的要就是那一句（实际={_got[0][:90]}）")
    _got = _run_gate({"ag_after.py": _GOOD + "print('这段永远跑不到')\n"})
    _assert_eq(len(_got), 1, f"反向对照：os._exit 之后还有代码＝收尾是死代码（实际={_got}）")
    _assert("之后还有会执行的代码" in _got[0], f"（实际={_got[0][:80]}）")
    _got = _run_gate({"ag_noflush.py": _QT_HEAD + "print('结果：7 通过 / 0 失败')\nrc = 0\n"
                                                 "os._exit(rc)\n"})
    _assert_eq(len(_got), 1, f"反向对照：不 flush 就交退出码＝屏幕上是一套话、机器读到另一套（实际={_got}）")
    _assert("sys.stdout.flush()" in _got[0], f"要指名缺哪一个动作（实际={_got[0][:90]}）")
    # 只 flush stderr 不算：那段汇总打在 stdout 上，而 CPython 的 stderr 从 3.9 起就是行缓冲。
    _got = _run_gate({"ag_stderr.py": _QT_HEAD + "print('结果：7 通过 / 0 失败')\nrc = 0\n"
                                                 "sys.stderr.flush()\nos._exit(rc)\n"})
    _assert_eq(len(_got), 1, f"反向对照：只 flush stderr 也要响（实际={_got}）")
    _assert("sys.stdout.flush()" in _got[0],
            f"报的话要与判据一致：这一格缺的是 stdout 那一路（实际={_got[0][:90]}）")
    _got = _run_gate({"ag_far.py": _QT_HEAD + "print('结果：7 通过 / 0 失败')\nrc = 0\n"
                                              "sys.stdout.flush()\na = 1\nb = 2\nc = 3\nd = 4\n"
                                              "os._exit(rc)\n"})
    _assert_eq(len(_got), 1,
               f"反向对照：flush 离交退出码隔了 4 行也算没收尾——中间那几步随时可能自己崩（实际={_got}）")
    _got = _run_gate({"ag_commented.py": _QT_HEAD + "print('结果：7 通过 / 0 失败')\nrc = 0\n"
                                                    "sys.stdout.flush()\n# os._exit(rc)\n"})
    _assert_eq(len(_got), 1, f"反向对照：把 os._exit 注释掉不算收尾（实际={_got}）")
    _assert("没有 os._exit 收尾" in _got[0], f"（实际={_got[0][:80]}）")

    # ── 5) 判据范围：只有注释提到 Qt 的文件不算「碰过 Qt」 ──
    _assert_eq(_run_gate({"ag_ok3.py": _GOOD,
                          "ag_qt_comment.py": "# from PyQt5.QtWidgets import QApplication\n"
                                              "print('结果：1 通过 / 0 失败')\n"}), [],
               "放行：那份只在注释里提 Qt 的套件不进判定（门读代码不读文案，与第 21/22 条同口径）")
    _got = _run_gate({"ag_qt_comment.py": "# from PyQt5.QtWidgets import QApplication\n"
                                          "print('结果：1 通过 / 0 失败')\n"})
    _assert_eq(len(_got), 1, f"反向对照：一份 Qt 套件都没判到时要响，不许报绿（实际={_got}）")
    _assert("都没判到" in _got[0], f"响的要是「口径脱节」而不是「这份没收尾」（实际={_got[0][:80]}）")

    # ── 6) fail-closed：范围取不到时三条门一条都没跑，这种「没问题」必须响 ──
    for _cmd, _want in (
            (("bash", "-c", "echo 清单解析炸了 >&2; exit 3"),
             ["批跑清单取不到", "退出码 3", "清单解析炸了"]),
            (("bash", "-c", "exit 0"), ["清单是空的"]),
            (("zz_ag_这个命令不存在",), ["起不来"]),
    ):
        with _patch(gate, "REGRESSION_LIST_CMD", _cmd):
            _got = gate.check_suite_exit_code_guard()
        _assert(len(_got) >= 1, f"反向对照：清单命令换成 {_cmd} 之后不许安静报绿")
        for _w in _want:
            _assert(_w in _got[0], f"清单坏掉的报告要带上「为什么」与「原始 stderr」（缺 {_w}，实际={_got[0][:120]}）")
    with _patch(gate, "REGRESSION_LIST_CMD", ("bash", "-c", "printf 'debug/zz_ag_ghost.py\\n'")):
        _got = gate.check_suite_exit_code_guard()
    _assert(any("读不到源码" in x for x in _got),
            f"反向对照：stdout 契约被污染（混进不存在的路径）要响在「这一路判不了」上（实际={_got}）")
    _got = _run_gate({}, suites=["debug/ag_gate23_absent.py"])
    _assert(any("读不到源码" in x for x in _got), f"在清单里、磁盘上没有：要说这一路判不了（实际={_got}）")
    _assert(any("都没判到" in x for x in _got), f"同一条红链上 judged=0 也要响：空集合上的合格不算（实际={_got}）")


def test_regression_suites_tracked_gate():
    print("\n═══ AH. 本机绿的那一路回归，CI 里得真有：套件入库跟踪（第 24 条硬门）═══")
    # 由来（2026-11-14 本轮清点批跑清单抓到）：54 路离线回归里有 **5 份只活在这台开发机上**
    # ——git 历史里从来没有它们。批跑照跑、计数照对、日志一句缺失都不留，而 CI 检出的仓库因此
    # 永远少 5/54 路覆盖。这一类比「套件坏了」更坏：坏套件会红，少一套件只会让「绿」的含义
    # 悄悄变小。所以判据的范围直接取自批跑清单（与第 23 条同源），问的却是 git 自己的答案：
    # 「CI 检出这个提交之后，这一路到底在不在」。
    gate = _manifest_gate()
    gate_src = (_REPO / ".github" / "scripts" / "check_packaging_manifest.py").read_text(
        encoding="utf-8")

    @contextlib.contextmanager
    def _git_state(index=(), head=(), dirty=()):
        """把「git 眼里被跟踪/被改动的是哪些」换成给定清单（不碰真仓库的索引）。"""
        _old_ts, _old_dp = gate._tracked_sets, gate._dirty_paths
        gate._tracked_sets = lambda: (set(index), set(head))
        gate._dirty_paths = lambda: set(dirty)
        try:
            yield
        finally:
            gate._tracked_sets, gate._dirty_paths = _old_ts, _old_dp

    # ── 1) 接线 ──
    _assert(callable(getattr(gate, "check_regression_suites_tracked", None)),
            "门模块里有 check_regression_suites_tracked（能被单独喂清单）")
    _at = gate_src.find("suite_track = check_regression_suites_tracked()")
    _assert(_at > 0, "硬门 main() 里真的调了第 24 条（函数在而不调＝这条门从没跑过）")
    _assert("bad += 1" in gate_src[_at:_at + 900],
            "第 24 条报出来的问题要并进退出码（只 print 不改退出码的门等于公告栏）")
    _assert("::error::离线套件入库跟踪" in gate_src[_at:_at + 900],
            "云端 annotation 要能直接照做：写明是「本机有、提交里没有」")

    # ── 2) 真仓库：清单里每一路都已在提交里；当年漏掉的那 5 份单独钉一次 ──
    _rc, _files, _err, _out, _chan = gate.regression_suites()
    _assert_eq(_rc, 0, f"批跑清单取得到（退出码｜{len(_files)} 行｜{_chan}）")
    _assert_eq(gate.check_regression_suites_tracked(), [],
               "第 24 条：清单展开出来的每一路套件都已在提交里（红了就照每条前面的 git 命令做）")
    _scar = ("debug/verify_gys_offline.py", "debug/gi_mp_offline_check.py",
             "debug/gi_pace_offline_test.py", "debug/local_scan_offline_test.py",
             "debug/qzd_channel_offline_test.py")
    for _rel in _scar:
        _assert(_rel in _files, f"{_rel} 仍在批跑清单里（这一节钉的就是它们，移出清单要一并改这里）")
        _assert_eq(gate.tracked_state(_REPO / _rel), "ok",
                   f"{_rel} 曾经只活在这台开发机上（本机绿、CI 检出后压根没这个文件）")

    # ── 3) 三种 git 状态各响一次，措辞与处置一一对应 ──
    # 清单为空时必须**留下一条红并跳过**，不能 `_files[0]`：2026-11-19 那次 `--list` 退出码 1、
    # 0 行，IndexError 把本节后面的几百条判据一起掩盖掉了，屏幕上只剩一段 traceback——
    # 而这一节自己恰恰是「范围断了」最有说服力的证人（上面那条「清单取得到」的红被它盖住了）。
    # 跳过不能是沉默：这里主动记一条红，不然「没跑」会被读成「跑了且绿」。
    if not _files:
        _assert(False, "清单为空：第 3/4 节拿清单里的真文件做 git 状态夹具，没得可做，"
                       "整段跳过（取不到清单上面已记过一次红，这里是第二个证人）")
    else:
        _one = _files[0]
        _assert_eq(gate.check_regression_suites_tracked(_files), [],
                   "放行（合成）：清单里每一份都在提交里、都没被改过")
        with _git_state(index=[x for x in _files if x != _one],
                        head=[x for x in _files if x != _one]):
            _got = gate.check_regression_suites_tracked(_files)
        _assert_eq(len(_got), 1, f"反向对照：少一份就响一条，不多不少（实际={_got}）")
        _assert(_one in _got[0] and "从未被 git 跟踪" in _got[0] and "git add" in _got[0],
                f"「压根没跟踪」的措辞要给 git add（实际={_got[0][:100]}）")
        with _git_state(index=_files, head=[x for x in _files if x != _one]):
            _got = gate.check_regression_suites_tracked(_files)
        _assert_eq(len(_got), 1, f"反向对照：只 add 没提交也要响（实际={_got}）")
        _assert("已 git add 但还没提交" in _got[0],
                f"这一类的处置是 commit，不是 add——写错一步就白跑一轮（实际={_got[0][:100]}）")
        with _git_state(index=_files, head=_files, dirty=[_one]):
            _got = gate.check_regression_suites_tracked(_files)
        _assert_eq(len(_got), 1, f"反向对照：在提交里但工作区改过也要响（实际={_got}）")
        _assert("两份内容不是同一份" in _got[0],
                f"要说清为什么「本机跑绿」对交付没有担保（实际={_got[0][:110]}）")
        _assert("它是批跑清单里的一路回归" in _got[0],
                f"要把「为什么这一份值得判」带上，否则读起来像通用提交检查（实际={_got[0][:140]}）")

        # ── 4) 范围真的等于批跑清单（门里没有第二套发现逻辑） ──
        # 这一节也必须在非空清单上跑：空清单时 `len(_got) == len(_files) == 0` 会**天然通过**，
        # 那正是「能在空集上通过的判据就是没有判据」——绿得比红更贵。
        with _git_state():
            _got = gate.check_regression_suites_tracked(_files)
        _assert_eq(len(_got), len(_files),
                   f"清单里 {len(_files)} 项在空 git 状态下要逐条点名（实际 {len(_got)} 条）"
                   f"——只报一句「有文件没提交」就没人知道少的是哪几路")
        _named = [x for x in _files if any(x in p for p in _got)]
        _assert_eq(len(_named), len(_files),
                   f"每一条都要归得到具体文件（归属不干净＝批跑里少一路时看不出来）")

    # ── 5) fail-closed：问不到 git 与问不到清单，都不许按合格返回 ──
    with _patch(gate, "in_work_tree", lambda: False):
        _got = gate.check_regression_suites_tracked()
    _assert_eq(len(_got), 1, f"反向对照：不在 work tree 里就直接收（实际={_got}）")
    _assert("不许按合格返回" in _got[0], f"响的话要说清这是「问不到」而不是「没问题」（实际={_got[0][:90]}）")
    with _patch(gate, "REGRESSION_LIST_CMD", ("bash", "-c", "exit 7")):
        _got = gate.check_regression_suites_tracked()
    _assert(any("批跑清单取不到" in x for x in _got),
            f"反向对照：清单取不到时第 24 条也要响（范围断了＝这一条从没跑过）（实际={_got}）")
    _got = gate.check_regression_suites_tracked(["debug/ag_gate24_absent.py"])
    _assert(any("磁盘上没有" in x for x in _got),
            f"在清单里但磁盘上没有：批跑入口会拦，门也要同步点名（实际={_got}）")
    # 与上面第 3/4 节那个「空清单就跳过」的守卫钉的是同一个子规则：**范围断了不等于合格**。
    # 这里拿它做永久证人（不是一次性实验）：把空表退成「一份都没问题」就是最阴的那类假绿。
    _got = gate.check_regression_suites_tracked([])
    _assert(any("一份套件都没判" in x for x in _got),
            f"空清单喂给门：不许按「合格」返回（范围断了与没问题必须是两回事）（实际={_got}）")


def _list_only_probe(gate, files, build_rels):
    """第 25 条注入实验的**证人选择**：只走批跑清单进视野、且这份证人随仓库走。

    由来（2026-11-20 干净克隆第二轮）：这里原来写死 `riskbird_build/test_qizhidao.py`。那一份
    从未进过 git（`riskbird_build/` 顶层本机 111 份 .py，HEAD 里只有 32 份），本机之所以侥幸绿，
    是 `_dev_path_inputs()` 按磁盘 glob 收 `*_build/*.py` 时恰好捞到了它——它进视野压根不是走
    清单，这一格当时判的已经不是它声称的那条通道；换到干净克隆上证人直接不存在，症状长成
    「克隆坏了」，把排查方向带到相反的一侧。**判据的证人自己不出货，比少一条判据更误导人。**
    三条筛选各挡一种假绿，缺一格就有一格白判：
      ① 出自批跑清单——本节验的就是「清单」这条通道能不能把文件送进门的视野；
      ② 不在 `*_build/` 下——那种文件走不走清单都在视野里，拿它当证人等于没有证人；
      ③ 必须在 HEAD 里（`ok`/`dirty`）——否则换一个检出证人就消失。（`dirty` 算通过：内容改过
         没提交由第 24 条单独管，这里只问「这台机器之外它还在不在」。）
    返回 `(证人, 落选原因)`：落选原因跟着红话一起报出去，别让人对着 sorted() 的第一个名字猜。
    这个函数放在模块级而不是塞回节里，是因为下面两格反向对照要复用同一份实现：选择规则如果
    在判据里复刻一遍，「改了真的、判了假的」这类变异就永远红不了。
    """
    kept, drops = [], []
    for rel in sorted(files):
        top = rel.split("/")[0] if "/" in rel else ""
        state = gate.tracked_state(_REPO / rel)
        if not top:
            drops.append(f"{rel}：裸在仓库根，不是清单通道要验的那种文件")
        elif rel in build_rels:
            # 只拿门禁真正的磁盘 glob 集合做判据，不用「目录名后缀」启发式：后者与前者今天
            # 完全等价（互相替换跑不出差分），等于一条杀不死的分支；而哪一天 `_dev_path_inputs()`
            # 改了收法，跟着集合走的那份会自动跟进，跟着名字走的那份就开始替一个不存在的规则背书。
            drops.append(f"{rel}：在 {top}/ 下，磁盘 glob 本来就会把它收进视野（与清单无关）")
        elif state not in ("ok", "dirty"):
            drops.append(f"{rel}：不在 HEAD 里（tracked_state={state}），换台机器这份证人就不存在")
        else:
            kept.append(rel)
    return (kept[0] if kept else None), drops


def test_no_dev_machine_paths_gate():
    print("\n═══ AI. 只对一台机器成立的绝对路径：交付链禁止写死路径（第 25 条硬门）═══")
    # 由来（2026-11-14 本轮自审）：批跑入口的第 20 行曾经写死一个开发机绝对路径再 `|| exit 1`
    # ——换一台机器（含 CI 打包机）第一步就退；同族的写法散在 19 份套件（sys.path.insert(0,
    # "/Users/…")）与几份 shell 里。这类缺陷的坏，不在于它难修，而在于**它在这台机器上永远不
    # 响**：所有断言都是绿的，只有换机器那一刻才炸，而那时没人怀疑得到一行路径。判据要覆盖的
    # 也不止 python：交付 shell、workflow、门禁脚本自己都在同一台机器上被读进来。
    gate = _manifest_gate()
    gate_src = (_REPO / ".github" / "scripts" / "check_packaging_manifest.py").read_text(
        encoding="utf-8")

    def _shape(*parts: str) -> str:
        """把「门要抓的那串字节」在运行时拼出来，源码里一个字面量都不留。

        这一节的夹具本身就是第 25 条要拦的形状，而门扫的是**源码文本**：坏写法一字不差地写在
        测试文件里，真仓库那一格就会响在自己的测试文本上（本轮实测响了 10 行）。拼起来之后喂进
        门的字节完全一样，判据不打折，但源码里再没有一行会被扫成开发机路径——静态文本类判据的
        夹具都该这么写。用豁免表给测试自己开后门是另一种做法，代价是那张表从此变成漏口的入口。
        """
        return "".join(parts)

    # ── 1) 接线 ──
    for _fn in ("check_no_dev_machine_paths", "_dev_path_inputs"):
        _assert(callable(getattr(gate, _fn, None)), f"门模块里有 {_fn}（能被单独喂夹具）")
    _at = gate_src.find("dev_paths = check_no_dev_machine_paths()")
    _assert(_at > 0, "硬门 main() 里真的调了第 25 条（函数在而不调＝这条门从没跑过）")
    _assert("bad += 1" in gate_src[_at:_at + 900],
            "第 25 条报出来的问题要并进退出码（只 print 不改退出码的门等于公告栏）")
    _assert("::error::开发机绝对路径" in gate_src[_at:_at + 900],
            "云端 annotation 要能直接照做：给出按脚本位置推 / 问环境这两种改法")

    # ── 2) 真仓库全绿 ──
    _assert_eq(gate.check_no_dev_machine_paths(), [],
               "第 25 条：交付链的源码、批跑套件、shell 与 workflow 里没有写死某台机器的路径")

    # ── 3) 四种形状都要抓（类 unix 家目录 ×2、Windows 家目录 ×2） ──
    _UP = _shape("/", "Users", "/someone/Desktop/Repo/x")            # 类 unix：macOS 家目录
    _HP = _shape("/", "home", "/someone/Repo/x")                     # 类 unix：Linux 家目录
    _WB = _shape("C:", "\\", "Users", "\\", "Someone\\Repo\\x")     # Windows：反斜杠写法
    _WF = _shape("C:/", "Users", "/Someone/Repo/x")                   # Windows：正斜杠写法
    for _rel, _line in (
            ("ag_users.py", 'sys.path.insert(0, "' + _UP + '")'),
            ("ag_home.py", 'p = "' + _HP + '"'),
            ("ag_win_b.py", 'p = r"' + _WB + '"'),
            ("ag_win_f.py", 'p = "' + _WF + '"'),
    ):
        _got = gate.check_no_dev_machine_paths({_rel: _line + "\n"})
        _assert_eq(len(_got), 1, f"反向对照：{_rel} 那种写法要响（实际={_got}）")
        _assert("只对一台机器成立的绝对路径" in _got[0],
                f"结论要说的是「换台机器它指向不存在的文件」（实际={_got[0][:90]}）")
        _assert(_line.split("=")[-1].strip()[:18] in _got[0] or "sys.path.insert" in _got[0],
                f"要把现场那一行原样打出来，人才不用回去找（实际={_got[0][:110]}）")

    # ── 4) 不是路径的东西不许响（判据越界和判据缺失一样会逼人跳过这一步） ──
    _assert_eq(gate.check_no_dev_machine_paths({
        "ag_ok.py": (
            'from pathlib import Path\n'
            'HERE = Path(__file__).resolve().parent\n'
            'DESKTOP = Path.home() / "Desktop" / "B2B三站企业数据自动采集工具"\n'
            'legacy = os.path.expanduser("~/Desktop/x")\n'
            'url = "https://cdn.example.com/home/a/b.png"\n'
            'api = "https://api-site/Users/list"\n'
            'rel = "b2bcollect_build/b2bcollect_data/x.json"\n'
        )}), [],
        "放行：按脚本位置推、问环境、URL 里的 /home/ 与 /Users/ 都不是文件系统路径")
    _assert_eq(gate.check_no_dev_machine_paths({
        "ag_doc.py": '# 旧写法是 "' + _shape("/", "Users", "/someone/Desktop/Repo")
                     + '"，现在按 __file__ 推\np = 1\n'}), [],
        "放行：整行注释里的举例路径不算现场代码（与第 21/23 条同一条「门读代码不读文案」）")
    _assert_eq(len(gate.check_no_dev_machine_paths(
                   {"ag_str.py": 'TIP = "在 ' + _shape("/", "Users", "/someone/Desktop")
                                  + ' 那台机器上手改一次"\n'})), 1,
               "反向对照（边界写在案上）：判据不区分注释与字符串——**说明文字**里的例子如果非要"
               "留着，就去 DEV_PATH_EXEMPT 登记字样和理由，而不是把判据放宽到抓不住真代码")

    # ── 5) 豁免按「文件 + 字样」生效，且登记表不许过期 ──
    _JF = _shape("/", "Users", "/jhnoplan/Desktop/AI", "云展")       # 与真表里那条同款的形状
    _two = ("p = '" + _JF + "/m'\n"
            "q = '" + _shape("/", "Users", "/someone/Desktop/other") + "'\n")
    with _patch(gate, "DEV_PATH_EXEMPT", {"ag_ex.py": [(_JF, "夹具：打包态绝不读它")]}):
        _got = gate.check_no_dev_machine_paths({"ag_ex.py": _two})
    _assert_eq(len(_got), 1,
               f"反向对照：豁免只免那一行，同文件里的下一条同类写法照样响（实际={_got}）")
    _assert("someone/Desktop/other" in _got[0], f"响的要是没登记的那一条（实际={_got[0][:110]}）")
    with _patch(gate, "DEV_PATH_EXEMPT", {"ag_ex.py": [("zz_ag_字样已经改名", "夹具")]}):
        _got = gate.check_no_dev_machine_paths({"ag_ex.py": _two})
    _assert(any("今天已经不命中" in x for x in _got),
            f"反向对照：豁免登记的字样今天找不到＝名单过期，要响（实际={_got}）")
    # 登记给一份**根本不在视野里**的文件：子集扫描不判（不然每喂一份夹具都要连带报四行与本次
    # 判定无关的「过期」，而噪音会把真信号盖掉——本轮第一跑就红在这一格上），但**全量**那一格
    # 必须管得住：文件改了名、挪了目录，挂着理由的表就是下一条同类写法的入口。
    with _patch(gate, "DEV_PATH_EXEMPT",
                dict(gate.DEV_PATH_EXEMPT, **{"ag_never_here.py": [("anything", "夹具：没这份文件")]})):
        _got = gate.check_no_dev_machine_paths({"ag_ex.py": _two})
    _assert(not any("ag_never_here.py" in x for x in _got),
            f"子集扫描不许替整张表背书（实际={_got}）")
    with _patch(gate, "DEV_PATH_EXEMPT",
                dict(gate.DEV_PATH_EXEMPT, **{"ag_never_here.py": [("anything", "夹具：没这份文件")]})):
        _got = gate.check_no_dev_machine_paths()
    _assert(any("ag_never_here.py" in x and "不在第 25 条的视野里" in x for x in _got),
            f"反向对照：全量扫描下，登记给一份读不到的文件要当场响（实际={_got[:1]}）")
    _got = gate.check_no_dev_machine_paths({"ag_ex.py": _two})
    _assert_eq(len(_got), 2,
               "反向对照：上面几格的豁免都是合成输入，不落到真名单里——同一份字节交给真表再判"
               "一次，两条都得响（今天真表里没有 ag_ex.py 这一项）")

    # ── 6) 表上每一条豁免都得是**承重**的：撤掉它，真仓库当场要响在那一行 ──
    _table = dict(gate.DEV_PATH_EXEMPT)
    _assert(_table, "豁免表非空（本节的第 5、7 格都靠它自证，空表时那两格在判空气）")
    with _patch(gate, "DEV_PATH_EXEMPT", {}):
        _got = gate.check_no_dev_machine_paths()
    for _rel, _entries in _table.items():
        for _token, _why in _entries:
            _assert(any(_rel in x and _token in x for x in _got),
                    f"{_rel} 里那条「{_token}」的豁免必须在承重：撤掉豁免后门要响在这一行上，"
                    f"否则登记的是一条已经不存在的理由（实际={_got[:1]}）")

    # ── 7) 范围：交付目录 + 批跑清单 + 交付 shell + workflow + 门禁自己，一个都不许漏 ──
    _inputs = gate._dev_path_inputs()
    _rels = {gate._rel_posix(p) for p in _inputs}
    _rc, _files, _err, _out, _chan = gate.regression_suites()
    _assert_eq(_rc, 0, f"批跑清单取得到（退出码｜{len(_files)} 行｜{_chan}）")
    _assert(set(_files) <= _rels,
            f"清单里每一份套件都在第 25 条的视野内（缺 {sorted(set(_files) - _rels)[:4]}）"
            f"——门与批跑分叉的方向永远是「少判一侧」")
    _build = {p.relative_to(_REPO).as_posix() for d in sorted(_REPO.iterdir())
              if d.is_dir() and d.name.endswith("_build") for p in d.glob("*.py")}
    _assert(_build <= _rels, f"所有 *_build/*.py 进包源码都在视野内（缺 {sorted(_build - _rels)[:4]}）")
    for _rel in tuple(gate.SHELL_SOURCES) + tuple(gate.WORKFLOWS):
        _assert(_rel in _rels, f"{_rel} 在视野内：交付 shell 与 workflow 写死路径时，"
                               f"坏的是**构建机**上那一步，不是本机")
    _assert(".github/scripts/check_packaging_manifest.py" in _rels,
            "门禁脚本自己也在视野内（它自己写死一台机器，其余 26 条判据就全都在那台机器上）")
    # 证人不再写死文件名，改成从批跑清单里现场选（选择规则与它的由来均见 `_list_only_probe`）。
    _probe, _drops = _list_only_probe(gate, _files, _build)
    if _probe is None:
        # 这一格响了下面三格就没证人；按「没发现问题」过去是最阴的一类假绿，所以单独响一条后
        # 直接跳过，而不是让下一行 `next(...)` 抛 StopIteration 把整节掩成 traceback。
        _assert(False, "批跑清单里取不到合规证人（① 出自清单 ② 不在 *_build/ 下 ③ 在 HEAD 里）："
                       + "；".join(_drops[:4]) + "——下面三格失去证人，不许当成「没有开发机路径」")
    else:
        _assert(_probe in _rels and _probe not in _build,
                f"证人必须「只从批跑清单进视野」：{_probe}｜在视野里={_probe in _rels}｜"
                f"磁盘 glob 也捞到它={_probe in _build}——后者为真时这一格判的不是清单那条通道")
        _txt = _inputs[next(p for p in _inputs if gate._rel_posix(p) == _probe)]
        _got = gate.check_no_dev_machine_paths(
            {_probe: _txt + '\nLEAK = "' + _shape("/", "Users", "/someone/Desktop/x.py") + '"\n'})
        _assert_eq(len(_got), 1, f"反向对照：往真套件文本里注一行开发机路径要响（实际={_got}）")
        _assert(_probe in _got[0], f"要报到文件名（实际={_got[0][:80]}）")

    # ── 7b) 上面那三条筛选都得**承重**：不合格的种类喂进选择函数，它要拒、且要点名 ──
    # 没有这一节，`_list_only_probe` 退化成「sorted() 的第一个名字」：筛选删干净它也绿着返回。
    _fake_rel = "debug/zz_handover_probe_offline_test.py"
    (_REPO / _fake_rel).write_text("# 交接闭包反向对照夹具：本机有、HEAD 里没有\n", encoding="utf-8")
    try:
        # 条件 ③：一份「没提交」的套件不许当证人——这就是本轮干净克隆里踩中的那一坑。
        # 这里只喂它一份（而不是拼在真清单前面）：拼-list 时证人取 sorted() 的第一个，
        # 伪造项排在后面就永远选不中，那一格会变成「只有名字捣乱时才响」的位置依赖判据。
        _p2, _d2 = _list_only_probe(gate, [_fake_rel], _build)
        _assert(_p2 is None,
                f"反向对照：只有一份「没提交」的套件时必须选不出证人（实际={_p2}）"
                "——收下它就是「本机绿、换台机器证人消失」")
        _assert(any(_fake_rel in x for x in _d2), f"落选要点名到具体文件（实际={_d2[:2]}）")
        # 条件 ②：拿真清单里的 *_build/ 条目做正控（不造假货），它必须被拒。
        _b_rel = sorted(r for r in _files if "/" in r and r.split("/")[0].endswith("_build"))
        _assert(_b_rel, "夹具：批跑清单里本来就该有 *_build/ 下的套件（没有则下面这一格在判空气）")
        if _b_rel:
            _p3, _d3 = _list_only_probe(gate, [_b_rel[0]], _build)
            _assert(_p3 is None, f"反向对照：*_build/ 下的套件不许当「清单通道」的证人（实际={_p3}）")
            _assert(any(_b_rel[0] in x for x in _d3), f"落选要点名到具体文件（实际={_d3[:2]}）")
    finally:
        (_REPO / _fake_rel).unlink(missing_ok=True)

    # ── 8) fail-open 不许：一份源码都没读到时，「没有开发机路径」这句话没有含义 ──
    _got = gate.check_no_dev_machine_paths({})
    _assert_eq(len(_got), 1, f"反向对照：输入为空时必须响（实际={_got}）")
    _assert("扫描范围断了" in _got[0], f"响的要是「范围断了」而不是「都合格」（实际={_got[0][:90]}）")
    _empty = Path(tempfile.mkdtemp(prefix="ai_dev_path_empty_root_"))
    with _patch(gate, "ROOT", _empty):
        _assert_eq(gate._dev_path_inputs(), {}, "夹具：空仓库根下真的读不到任何输入")
        _got = gate.check_no_dev_machine_paths()
    _assert_eq(len(_got), 1, f"反向对照：ROOT 落空时同样不许报绿（实际={_got}）")



def test_temp_dir_single_source_gate():
    print("\n═══ AJ. 落点不许跟着兜底串漂：TEMP 取值一处口径（第 26 条硬门）═══")
    # 由来（2026-11-15 本机自审抓到）：交付链里九处写成 ``Path(os.environ.get("TEMP",
    # r"C:\\temp"))``。Mac/Linux 上根本没有 TEMP 这个环境变量，兜底串于是变成一个**相对**
    # 路径——本机仓库里真长出过两个名为 ``C:\\temp`` 的目录（仓库根一个、b2bcollect_build/
    # 一个，都是空的，谁建的看不出来）。难看只是表面：这些目录是当「老登录态在哪」传给
    # browser_profile_dir 的，而那条链上的 choose_writable 会先 mkdir 写探针、再挑**第一个
    # 真能写的**目录。持久位置一旦写不进去（只读挂载、权限策略、App Translocation），付费
    # 账号的 Cookie 就落到工作区里，等着下一次 ``git add <目录>``——第 22 条判的正是那一刻，
    # 但第 22 条只能兜住「已经长出来」，兜不住「为什么会长在这儿」。这条门判的是取值口径。
    # 判法刻意不判「这一行外面有没有 win32 守卫」：静态判守卫迟早误报，而误报的代价是人
    # 开始跳过这一步；换成「TEMP 只许在 legacy_temp_root 那一处被读」之后，判据是**位置**。
    gate = _manifest_gate()
    gate_src = (_REPO / ".github" / "scripts" / "check_packaging_manifest.py").read_text(
        encoding="utf-8")

    # ── 1) 接线：判据在、被 main() 调用、结果进退出码、云端留下能照做的 annotation ──
    for _fn in ("check_temp_dir_single_source", "_temp_read_sites", "_helper_body"):
        _assert(callable(getattr(gate, _fn, None)),
                f"硬门模块里有 {_fn}（能被单独喂夹具，才谈得上反向对照）")
    _at = gate_src.find("temp_dir_gaps = check_temp_dir_single_source()")
    _assert(_at > 0, "硬门 main() 里真的调了第 26 条（函数在而不调＝这条门从没跑过）")
    _assert("bad += 1" in gate_src[_at:_at + 1200],
            "报出来的问题要并进退出码（只 print 不改退出码的门等于公告栏）")
    _assert("::error::临时目录口径" in gate_src[_at:_at + 1200],
            "云端 annotation 要能直接照做：写明是「又多了一处 TEMP 取值」并点名改法")
    _assert_eq(sorted(gate.TEMP_HELPER_FILES),
               ["b2bcollect_build/tool_config.py", "riskbird_build/runtime_paths.py"],
               "唯一允许读 TEMP 的两处就是那两份同名镜像实现（登记表本身就是口径声明）")

    # ── 2) 今天的真仓库：全绿，而且「只有一处」这句结论是有内容的 ──
    _assert_eq(gate.check_temp_dir_single_source(), [],
               "第 26 条：交付链里 TEMP 只在同口径的那两处被读")
    for _rel, _mod_name in sorted(gate.TEMP_HELPER_FILES.items()):
        _txt = (_REPO / _rel).read_text(encoding="utf-8")
        _hits, _spans, _err = gate._temp_read_sites(_txt)
        _assert_eq(_err, None, f"{_rel} 要解析得过，否则这条门在这一侧是空跑")
        _assert(_hits, f"{_rel} 里要真能找到 TEMP 读取点（找不到＝扫描口径与源码写法已经分叉，"
                       "「只有一处」就成了在判空气）")
        _assert_eq(sorted({_spans.get(h) for h in _hits}), [gate.TEMP_HELPER_NAME],
                   f"{_rel} 的每一处 TEMP 读取都必须在 {gate.TEMP_HELPER_NAME}() 函数体内"
                   "（别处多一处就是又开一个口径）")

    # ── 3) 疤痕判据：本轮真改过的那八个调用点，现在都必须走 helper ──
    # 只判「没有坏写法」不够：把调用点整段删掉也能全绿，而那正好丢掉接管老登录态的功能
    # （2026-11-13 Windows 实机那次「换个账号 1688 就 0 家」的回归就此复活）。
    _must_call = ("b2bcollect_build/shared_chrome.py", "ali1688_build/ali1688_crawler.py",
                  "riskbird_build/aiqicha_crawler_v2.py", "riskbird_build/riskbird_crawler_v2.py",
                  "riskbird_build/qizhidao_crawler.py", "riskbird_build/dual_session.py",
                  "riskbird_build/riskbird_gui.py", "riskbird_build/riskbird_crawler.py")
    _rel_of = {(_p if isinstance(_p, str) else _p.relative_to(_REPO).as_posix()): _t
               for _p, _t in gate._dev_path_inputs().items()}
    for _rel in _must_call:
        _txt = _rel_of.get(_rel)
        _assert(_txt is not None, f"{_rel} 在第 26 条的视野里（不在＝这份从来没被判过）")
        _assert("legacy_temp_root()" in _txt,
                f"{_rel} 现在真的从 helper 取临时目录（本轮之前它自己读过 TEMP）")

    # ── 4) 行为对拍：两侧 helper 在四种运行形态下给同一个答案 ──
    # 静态判据只保证「一处口径」，保证不了那一处**算得对**。这里直接调生产函数，把
    # sys.platform 与 TEMP 两件事都拨到给定形态。判的性质：非 Windows 上算出来的落点必须
    # 是绝对路径（相对＝跟着当前工作目录跑，本轮的仓库污染就是这么来的）；Windows 那一支
    # 在本机没法判绝对性（posixpath 不认识盘符），但它的答案本身被钉死成 %TEMP% / C:\\temp。
    import types
    # 这一节必须自己把 riskbird_build 导到手：sys.path 上那条路径是 site_adapters 在
    # import 时 append 的，本节原先指望「前面某节已经导过」——单节驱动（只跑 AF+AJ）时
    # 那条路径根本不存在，当场 ModuleNotFoundError。跨目录对拍的用例要能单独跑，
    # 否则「这一节绿」取决于别的节跑没跑过。
    import site_adapters as _sa       # noqa: F401  只借它把 riskbird_build 挂上 sys.path
    import runtime_paths as _rp
    _cases = (("win32", "D:\\work\\tmp", "D:\\work\\tmp", False),
              ("win32", None, "C:\\temp", False),
              ("darwin", "/should/be/ignored", "/tmp", True),
              ("linux", None, "/tmp", True))
    # 这一格里整个 os.environ 用 _patch 换掉，不写 environ[...] 那种字面形状：第 26 条对
    # 「environ 下标」是一刀切的（它不去猜这一处是读还是写），夹具自踩这道门就是给自己找一条
    # 解释不通的红；而给测试开门槛（豁免表加一行）更不行——那等于把这条门的视野弄假。
    # 顺带好处：这一格从此不会真的改到这台机器的 TEMP。
    for _plat, _temp, _want, _need_abs in _cases:
        _env = {} if _temp is None else {"TEMP": _temp}
        _vals = []
        with _patch(os, "environ", _env):
            for _mod in (tc, _rp):
                with _patch(_mod, "sys", types.SimpleNamespace(platform=_plat)):
                    _p = _mod.legacy_temp_root()
                    _vals.append(str(_p))
                    if _need_abs:
                        _assert(_p.is_absolute(),
                                f"{_mod.__name__} 在 {_plat} 给出的是相对路径「{_p}」——"
                                "它会跟着当前工作目录跑（本轮就是长在仓库里）")
                        _assert(not str(_p).lower().startswith("c:"),
                                f"{_mod.__name__} 在 {_plat} 还带着 Windows 兜底串「{_p}」")
        _assert_eq(_vals, [_want, _want],
                   f"运行形态 {_plat}/TEMP={_temp}：两份同名实现要给同一个答案"
                   "（不一样＝镜像实现分叉，而调用点各引一侧）")

    # ── 5) 反向对照：坏写法各响一条，并点名行号与改法 ──
    def _one(name, body):
        return gate.check_temp_dir_single_source({name: body})

    _head = "x = 1\n\ndef f():\n    "
    for _tag, _src in (
            ("environ.get 带 Windows 兜底", _head + 'p = os.environ.get("TEMP", r"C:\\temp")\n'),
            ("environ.get 没有兜底串（外面套着 win32 守卫也算）",
             _head + 'if sys.platform == "win32":\n        p = os.environ.get("TEMP")\n'),
            ("os.getenv", _head + 'p = os.getenv("TEMP", "/tmp")\n'),
            ("下标写法", _head + 'p = os.environ["TEMP"]\n'),
            ("顶层（不在任何函数里）", 'import os\np = os.environ.get("TEMP")\n')):
        _got = _one("aj_probe.py", _src)
        _assert_eq(len(_got), 1, f"「{_tag}」要响恰好一条（实际={_got}）")
        _assert("aj_probe.py 第" in _got[0] and "legacy_temp_root()" in _got[0],
                f"「{_tag}」报的要带行号与改法，云端照着这一行就能动手（实际={_got[0]}）")

    # ── 6) 放行格：注释与 docstring 里的举例不算判据（这条门其它格同一个口径） ──
    _assert_eq(_one("aj_ok.py",
                    '# 以前这里写 os.environ.get("TEMP", r"C:\\temp")\n'
                    'p = os.getcwd()\n'), [],
               "放行：注释里那句不是调用（红在文案上的门，最后都会被人整条跳过）")
    _assert_eq(_one("aj_ok2.py",
                    'def f():\n    """说明：os.environ.get("TEMP") 在 Mac 上取不到。"""\n'
                    '    return 1\n'), [],
               "放行：docstring 里的举例在 AST 上没有调用节点")
    # helper 本体的文本先摆在这里：第 6、7 两节都要拿它当「合格那一侧」的基准，
    # 定义必须挨着第一次用到它的地方，否则读的人要跳到几十行外才知道基准长什么样。
    _good = ("def legacy_temp_root():\n"
             '    if sys.platform == "win32":\n'
             '        return Path(os.environ.get("TEMP") or r"C:\\temp")\n'
             '    return Path("/tmp")\n')
    # helper 本体那一处必须放行，而且只能在**登记文件 + 自己的函数体内**放行
    _assert_eq(gate.check_temp_dir_single_source(
                   {"b2bcollect_build/tool_config.py": _good}), [],
               "放行：登记文件里 helper 函数体内那一处就是口径本体")
    # 夹具必须**带着 helper 本体**再多一处读取：少了 helper，这一格会同时收到「helper 判不出结构」
    # 那条真红，而本格要问的只是「豁免按函数还是按文件」。两件事混在一格里，计数就得接受 2 条
    # （本机上一版正是这样），那时验的已经不是这一格说的那件事了。
    _extra_read = "def other():\n    return os.environ.get(\"TEMP\")\n"
    _got = gate.check_temp_dir_single_source(
        {"b2bcollect_build/tool_config.py": _good + "\n" + _extra_read})
    _assert_eq(len(_got), 1,
               f"反向对照：登记文件里、helper 函数体之外的那一处不放行（豁免按函数不按文件）"
               f"（实际={_got}）")
    # 读取点落在哪一行从基准文本算出来（helper 那 4 行 + 1 个空行 + def 那 1 行），不写死数字
    _assert(f"第 {len(_good.splitlines()) + 3} 行" in _got[0],
            f"边界要报到行号，人才能在编辑器里落到那一行（实际={_got[0]}）")
    _assert("legacy_temp_root()" in _got[0],
            f"改法要直接点名 helper（实际={_got[0]}）")
    _assert_eq(_one("aj_other_build/probe.py", 'import os\nx = os.environ.get("PATH")\n'
                                             'y = os.getenv("TMPDIR")\n'), [],
               "放行：读别的环境变量不在这条门范围内（判据越界和判据缺失一样会逼人绕过它）。"
               "os.getenv 也要放行到**变量名**这一层：这一支认的是写法，不是「凡是 getenv 都响」")
    _assert_eq(_one("aj_probe.yml", 'run: echo "$TEMP"\n'), [],
               "放行：非 .py 不送去 ast.parse（门不在自己的视野边界上造噪音）")

    # ── 7) 两侧实现分叉、helper 被改名/复制/搬进类里——都要响 ──
    _fork = ("def legacy_temp_root():\n"
             '    if sys.platform == "win32":\n'
             '        return Path(os.environ.get("TEMP") or r"C:\\tmp2")\n'
             '    return Path(tempfile.gettempdir())\n')
    _files = {rel: _good for rel in gate.TEMP_HELPER_FILES}
    _assert_eq(gate.check_temp_dir_single_source(dict(_files)), [],
               "两份结构一致的 helper：这条门不响（先证明它不会因为「只剩 helper」而误报）")
    _files["riskbird_build/runtime_paths.py"] = _fork
    _got = gate.check_temp_dir_single_source(dict(_files))
    _assert(any("结构不一致" in x for x in _got),
            f"反向对照：只改一侧的镜像实现必须响（实际={_got}）")
    _files["riskbird_build/runtime_paths.py"] = _good.replace("legacy_temp_root", "temp_root_x")
    _got = gate.check_temp_dir_single_source(dict(_files))
    _assert(any("恰好有一个" in x and "riskbird_build/runtime_paths.py" in x for x in _got),
            f"反向对照：helper 被改名＝口径没了，要响在改名那一份文件上（实际={_got}）")
    _files["riskbird_build/runtime_paths.py"] = _good + "\n" + _good
    _got = gate.check_temp_dir_single_source(dict(_files))
    _assert(any("实际 2 个" in x for x in _got),
            f"反向对照：同名定义出现两次（后一份悄悄覆盖前一份）也要响（实际={_got}）")
    _files["riskbird_build/runtime_paths.py"] = "class C:\n    " + _good.rstrip().replace("\n", "\n    ") + "\n"
    _got = gate.check_temp_dir_single_source(dict(_files))
    _assert(any("恰好有一个" in x for x in _got),
            f"反向对照：搬进类里就不再是「那一处顶层口径」（实际={_got}）")

    # ── 8) fail-closed：视野断了不许报绿 ──
    _got = gate.check_temp_dir_single_source({})
    _assert_eq(len(_got), 1, f"一份源码都没读到要说出口（实际={_got}）")
    _assert("没有任何含义" in _got[0],
            f"报的要是「视野断了」而不是「口径没问题」（实际={_got[0]}）")
    _saved_inputs = gate._dev_path_inputs
    try:
        gate._dev_path_inputs = lambda: {"aj_a.py": "print(1)\n"}
        _got = gate.check_temp_dir_single_source()
        _assert(any("一处 TEMP 读取都没看到" in x for x in _got),
                f"全量扫描下空集合要响（口径被人整段删掉时这条门不许绿）（实际={_got}）")
        _got = gate.check_temp_dir_single_source({"aj_a.py": "print(1)\n"})
        _assert_eq([x for x in _got if "一处 TEMP 读取都没看到" in x], [],
                   "子集扫描不许替整条链背书（第 25 条同一课：拿全链的数量去要求一份夹具，"
                   "得到的只是噪音）")
        gate._dev_path_inputs = lambda: {"b2bcollect_build/tool_config.py": "def f(:\n"}
        _got = gate.check_temp_dir_single_source()
        _assert(any("解析不过" in x for x in _got),
                f"反向对照：坏源码上判不出结构要响（实际={_got[:1]}）")
    finally:
        gate._dev_path_inputs = _saved_inputs
    _saved_root = gate.ROOT
    try:
        gate.ROOT = _REPO / "aj_这个目录不存在"
        _got = gate.check_temp_dir_single_source({"aj_x.py": "print(1)\n"})
        _assert(sum(1 for x in _got if "读不到" in x) == 2,
                f"反向对照：两份 helper 都读不到时各响一条（实际={_got}）")
    finally:
        gate.ROOT = _saved_root

def test_module_name_binding_gate():
    print("\n═══ AK. 引导段不许用当场还没绑定的名字（第 27 条硬门 + 批跑理由渲染）═══")
    # 由来（2026-11-15 本机自审抓到）：按第 25 条把批跑套件引导段里写死的 /Users/... 换成
    # 按 __file__ 推时，五份套件的新代码用了当场还没绑定的名字——四份用了没导入的 Path、
    # 一份用了从未定义的 _BUILD。同一批回归里五路一起崩，屏幕上给的是五条**没有任何理由**的
    # FAIL：引导段一行判定都没打，而批跑入口的判定行只认「套件汇总的最后一行」。
    # 所以本节判两半：①门禁第 27 条（缺陷本身）；②批跑入口的理由渲染（症状被吞掉这件事）。
    # 少了②，下一次任何引导段崩溃仍然是一条无名 FAIL；少了①，同类写法还会再来一遍。
    gate = _manifest_gate()
    gate_src = (_REPO / ".github" / "scripts" / "check_packaging_manifest.py").read_text(
        encoding="utf-8")

    # ── 1) 接线：判据在、被 main() 调用、结果进退出码、云端留下能照做的 annotation ──
    for _fn in ("check_module_name_binding", "_module_level_uses",
                "_import_first_line", "_any_binding_name"):
        _assert(callable(getattr(gate, _fn, None)),
                f"硬门模块里有 {_fn}（能被单独喂夹具，才谈得上反向对照）")
    _at = gate_src.find("name_binding = check_module_name_binding()")
    _assert(_at > 0, "硬门 main() 里真的调了第 27 条（函数在而不调＝这条门从没跑过）")
    _assert("bad += 1" in gate_src[_at:_at + 1200],
            "报出来的问题要并进退出码（只 print 不改退出码的门等于公告栏）")
    _assert("::error::名字取用早于绑定" in gate_src[_at:_at + 1200],
            "云端 annotation 要能直接照做：写明是「取用早于绑定」并给出改法")
    _assert("模块级没有取用早于绑定的名字" in gate_src,
            "全绿的总结文案要把这一条也算进「过了一件事」（漏在文案里＝下次没人知道它存在）")

    # ── 2) 今天的真仓库：全绿，而且视野不是空的 ──
    _assert_eq(gate.check_module_name_binding(), [],
               "第 27 条：交付链每份 .py 的模块级都只取用当场已绑定的名字"
               "（本轮五处已修完，这一格把它们钉成回归判据）")
    _scope = gate._dev_path_inputs()
    _py_in_scope = [k for k in (_p if isinstance(_p, str) else _p.relative_to(_REPO).as_posix()
                                for _p in _scope) if k.endswith(".py")]
    _assert(len(_py_in_scope) >= 50,
            f"第 27 条的视野里要有交付链的源码（今天 {len(_py_in_scope)} 份 .py）："
            "范围断了，「没有取用早于绑定」就是一句空话")
    # 疤痕判据：本轮真崩掉的那五份必须在视野里，而且带着当时缺的那一行
    _ak_scars = {"debug/solarbe_fullslice_offline_test.py": "import os",
                 "debug/block_guard_offline_test.py": "from pathlib import Path",
                 "debug/local_scan_offline_test.py": "from pathlib import Path",
                 "b2bcollect_build/test_block_triage_offline.py": "from pathlib import Path",
                 "debug/gui_ux_offline_test.py": "_BUILD = "}
    for _rel, _needle in sorted(_ak_scars.items()):
        _assert(_rel in _py_in_scope,
                f"{_rel} 在第 27 条的视野里（不在＝这一份从来没被这条门判过）")
        _txt = Path(_REPO, _rel).read_text(encoding="utf-8", errors="replace")
        _assert(_needle in _txt,
                f"{_rel} 现在真的有 {_needle}（本轮就是缺它才崩在引导段）")
        _ln_use = min([i + 1 for i, s in enumerate(_txt.splitlines())
                       if ("Path(" in s or _needle.rstrip() + ")" in s) and not s.startswith("#")]
                      or [1])
        _assert(_ln_use >= 1, f"{_rel} 的使用点行号要算得出来（算不出来这一格就没在判东西）")

    # ── 3) 反向对照：两种坏形状都要红，四种合法形状都不许红 ──
    def _fed(_src, _name="debug/zz_ak_fixture.py"):
        return gate.check_module_name_binding({_name: _src})

    # (a) 取用早于 import：本轮四份套件的原件形状
    _ak_a = ("import sys\n"
             "sys.path.insert(0, str(Path(__file__).parent))\n"
             "from pathlib import Path\n")
    _got = _fed(_ak_a)
    _assert_eq(len(_got), 1, f"(a) 取用早于 import 要红一条：{_got}")
    _assert("第 2 行取用 Path" in _got[0] and "第 3 行才 import" in _got[0],
            f"(a) 要说清是哪个名字、哪两行（只说「有问题」等于让人全文件翻）：{_got[0][:120]}")

    # (b) 从未绑定：gui_ux 的 _BUILD 是这种
    _ak_b = ("import os\nimport sys\n"
             "sys.path.insert(0, _BUILD)\n"
             "print(os.getcwd())\n")
    _got = _fed(_ak_b)
    _assert_eq(len(_got), 1, f"(b) 从未绑定的名字要红一条：{_got}")
    _assert("没有任何地方绑定它" in _got[0],
            f"(b) 要说清「挪 import 救不了」：{_got[0][:120]}")

    # 合法形状一：函数体里的前向引用（晚于 import 才执行）
    _ak_ok_fn = ("import sys\n"
                 "def go():\n"
                 "    return Path(sys.argv[0])\n"
                 "from pathlib import Path\n")
    _assert_eq(_fed(_ak_ok_fn), [],
               "函数体里的取用不算：def 那一刻不执行函数体，判它就是把好代码逼坏")

    # 合法形状二：lambda 体（被调用时才执行）
    _ak_ok_lam = "f = lambda: LateName\nimport sys\nprint(sys.version)\n"
    _assert_eq(_fed(_ak_ok_lam), [], "lambda 体里的取用不算（同上一条同一个理由）")

    # 坏形状三：类体要算——建类那一刻类体真的执行
    _ak_cls = ("import sys\n"
               "class C:\n"
               "    P = Path(sys.argv[0])\n"
               "from pathlib import Path\n")
    _got = _fed(_ak_cls)
    _assert_eq(len(_got), 1, f"类体里的取用要算（类体在建类时执行）：{_got}")
    _assert("第 3 行取用 Path" in _got[0], f"类体那一行要被点名：{_got[0][:120]}")

    # 坏形状四：装饰器/默认值/返回值注解都在 def 语句执行时求值
    _ak_dec = ("import sys\n"
               "@staticmethod\n"
               "def f(a=Path(sys.argv[0]), *, b=Path('.')) -> Path:\n"
               "    return a\n"
               "from pathlib import Path\n")
    _got = _fed(_ak_dec)
    _assert_eq(len(_got), 1, f"默认值与注解里的取用要算：{_got}")
    _assert("第 3 行取用 Path" in _got[0],
            f"要报在 def 那一行（不是函数体里那行）：{_got[0][:120]}")

    # 合法形状三：带 ``from __future__ import annotations`` 的文件里注解不求值
    _ak_fut = ("from __future__ import annotations\n"
               "def f(a: LateName) -> LateName2:\n"
               "    return a\n")
    _assert_eq(_fed(_ak_fut), [],
               "注解延迟求值的文件不许判注解（判了就是假红，而假红的代价是整条门被跳过）")

    # 判得到的边界要在案：有 star import 的文件整份不判
    _ak_star = ("from os import *\nimport sys\n"
                "sys.path.insert(0, str(Path(__file__).parent))\n")
    _assert_eq(_fed(_ak_star), [],
               "有 ``from x import *`` 的文件整份不判（名字来源判不准，猜出来的多是假红）")
    _assert_eq(len(_fed(_ak_star.replace("from os import *\n", ""))), 1,
               "同一份代码去掉 star 就要红：证明上一格是「按在案的规则放行」，不是判漏了")

    # 内置名与模块 dunder 不误报
    _ak_bi = ("import sys\n"
              "print(open, __file__, __name__, getattr(sys, 'argv', []))\n")
    _assert_eq(_fed(_ak_bi), [], "内置名与 __file__/__name__ 不是缺陷（判它们＝满屏假红）")

    # 一处红不许掩盖下一处：同一名字只报一次，不同名字各报一次
    _ak_multi = ("import sys\n"
                 "sys.path.insert(0, str(Path(__file__)))\n"
                 "os.getcwd()\n"
                 "from pathlib import Path\nimport os\n")
    _got = _fed(_ak_multi)
    _assert_eq(len(_got), 2, f"两个名字各红一条（Path 被用了两次也只报一次）：{_got}")

    # fail-closed：坏源码、空视野、非 .py 三种「看不到」的处置必须不同
    _got = _fed("def broken(:\n")
    _assert_eq(len(_got), 1 and len(_got), "解析不过要报，不该安静当作没问题")
    _assert("解析不过" in _got[0] and "按不合格算" in _got[0],
            f"坏源码要按不合格算：{_got}")
    _got = gate.check_module_name_binding({})
    _assert_eq(len(_got), 1, f"视野为空不许报绿：{_got}")
    _assert("没有任何含义" in _got[0], f"空视野要说清为什么这条结论没内容：{_got[0][:100]}")
    _assert_eq(gate.check_module_name_binding({"debug/x.sh": "sys.path.insert(Path)\n"}), [],
               "shell 与 workflow 不在这一条的视野里（那是另一种语言的名字规则）")

    # ── 4) 批跑入口那一半：一条 FAIL 判定行必须自带理由（端到端，真起子进程）──────
    # 只判门禁不够：本轮症状是「失败了但屏幕上什么都没有」，那是批跑入口的渲染问题。
    # 这一格真跑一次批跑入口（只跑一份合成夹具），验的是**屏幕上那一行字**。
    import shutil
    import subprocess
    import tempfile
    _runner = _REPO / "debug" / "run_offline_regression.sh"
    _rsrc = _runner.read_text(encoding="utf-8")
    _assert("_pick_reason()" in _rsrc, "批跑入口要有「取理由」这一步")
    _assert('reason=$(_pick_reason "$verdict" "$last" "$body" "$out")' in _rsrc,
            "理由要按 汇总行 → 异常行 → 末行输出 的次序取，且四个输入都传进去")
    _assert('printf \'FAIL  %-52s %s\\n\' "$f" "$reason"' in _rsrc,
            "FAIL 判定行打印的是 reason（打印 $last 就是回到那条无名 FAIL）")
    _assert("$EXC_RE" in _rsrc and "$RED_RE" in _rsrc,
            "异常行与红判定行共用一份模式（计数用一份、打印用另一份就会各自漂移）")
    # 取 bash 只走第 35 条那个把手：上一版这里用 `shutil.which("bash")` 做守卫、里面却拿
    # 裸名字起子进程，两句话在 Windows 上说的是两个程序（那一行守卫于是放行了一个跑不成
    # 的存根，而报回来的理由会变成「没有 NameError」这种与真缺陷无关的话）。
    _ak_argv, _ak_note = _bash_argv()
    if _ak_argv and Path(sys.executable).exists():
        _tmp = tempfile.mkdtemp(prefix="ak_runner_")
        try:
            _fx = Path(_tmp) / "fx_boot_nameerror.py"
            # 夹具本身不用嵌套引号：上一层转义写错时，崩的是**夹具**而不是被测的渲染逻辑，
            # 而报出来的理由会变成一条与本轮缺陷无关的 SyntaxError（本机第一跑就撞上了）。
            _fx_src = ["# 崩在引导段：用了没导入的名字，且前面打过含「失败」的中文标题",
                         "import sys",
                         'print("[② 采集失败重试与断点续采]")',
                         "sys.path.insert(0, str(Path(__file__).parent))",
                         'print("===== 通过 1 / 失败 0 =====")']
            _fx.write_text("\n".join(_fx_src) + "\n", encoding="utf-8")
            _env = dict(os.environ, B2B_REGRESSION_ONLY=str(_fx), PER_TEST_TIMEOUT="60",
                        B2B_REGRESSION_SANDBOX=tempfile.mkdtemp(prefix="ak_sandbox_"))
            _cp = subprocess.run(_bash_or_die(str(_runner)), cwd=str(_REPO), env=_env,
                                 capture_output=True, text=True, timeout=300)
            _line = [x for x in _cp.stdout.splitlines() if x.startswith("FAIL ")]
            _assert_eq(len(_line), 1, f"合成夹具应留下一条 FAIL（批跑输出见下）\n{_cp.stdout[-800:]}")
            _assert("NameError" in _line[0],
                    f"FAIL 行必须点名异常（本轮实况是这一行只有文件名，什么都没有）：{_line[0]}")
            _assert("采集失败重试" not in _line[0],
                    f"不许把栈里回显的中文标题当结论报（那是「走到过这里」，不是判定）：{_line[0]}")
            _assert("一条判定都没打就异常退出" in _line[0],
                    f"要说清「一条判定都没打」这层含义：{_line[0]}")
        finally:
            shutil.rmtree(_tmp, ignore_errors=True)
    else:
        _assert(False, "端到端这一格没法判：找不到 bash 或当前解释器（静默跳过＝这条格从没跑过）")


def test_shell_errexit_posture_gate():
    print("\n═══ AL. 手写 shell 必须自己对 errexit 表态：第 28 条硬门要有牙 ═══")
    # 由来（2026-11-14 Windows 云端实发，本机逐字节复现过）：打包前硬门那一步红，红话是
    # 「批跑清单取不到（bash debug/run_offline_regression.sh --list 退出码 1）」，而同一条命令
    # 在本机怎么跑都是绿的。把云端那次 job 的日志读到底才看见真正的签名：**退出码 1、stdout
    # 0 字节、stderr 0 字节**——连批跑入口第一句走 stderr 的诊断都没来得及打。本机照着 Actions
    # 给 bash 步的那对选项起它（`bash -e -o pipefail debug/run_offline_regression.sh --list`）
    # 当场复现，`bash -x` 停在 ``hits=$(ls "$d"/test_*_offline.py 2>/dev/null)``：ali1688_build
    # 按登记就是没有 *_offline.py，ls 无匹配返回非零、报错又被自己的 2>/dev/null 吃掉，裸赋值
    # 把这个非零原样交给**从调用方继承来的** errexit，脚本一声不响地结束。
    #
    # 为什么只有 Windows 红：两步都是 `shell: bash`，但 mac 上那一位是 /bin/bash 3.2，实测不
    # 把 -e 交给子 bash；Git Bash 5.2 会交。**同一份脚本、一个平台绿一个平台红，差的不是代码，
    # 是拉起它的人带了什么选项。** 所以第 15 条原来那句「脚本里没写 set -e 就一路不设」的口径
    # 被这件事正面证伪了：一份不表态的 shell 有两种生死，而这两种生死都不会在日志里留下第二句
    # 话。本节要钉的就是「生死必须由脚本自己宣布」，以及宣布的时机必须早于第一句可能失败的命令。
    gate = _manifest_gate()
    gate_src = (_REPO / ".github" / "scripts" / "check_packaging_manifest.py").read_text(
        encoding="utf-8")
    runner_rel = "debug/run_offline_regression.sh"
    runner = _REPO / runner_rel

    # ── 1) 接线：判据在、main 真调、调了进退出码、云端 annotation 能照做 ──
    _assert(callable(getattr(gate, "check_shell_errexit_posture", None)),
            "硬门模块里有第 28 条这个函数（能被单独喂夹具，才谈得上反向对照）")
    _at = gate_src.find("posture = check_shell_errexit_posture()")
    _assert(_at > 0, "硬门 main() 里真的调了第 28 条（函数在而不调＝这条门从没跑过）")
    _assert("bad += 1" in gate_src[_at:_at + 900],
            "第 28 条报出来的问题要并进不合格计数（只 print 不改退出码的门等于公告栏）")
    _assert("::error::shell errexit 立场" in gate_src[_at:_at + 900],
            "云端 annotation 要能直接照做：写明要补的是表态，不是「重跑一次」")
    _assert(runner_rel in gate.SHELL_SOURCES,
            "死在云端的那份批跑入口在这条门的登记表里（不在表里的判据等于没判）")

    # ── 2) 真仓库全绿，而且绿得有内容：视野不是空的 ──
    _assert_eq(gate.check_shell_errexit_posture(), [],
               "第 28 条：交付链每一份手写 shell 都在第一条真命令之前对 errexit 表了态")
    _absent = [rel for rel in gate.SHELL_SOURCES if not (_REPO / rel).is_file()]
    _assert_eq(_absent, [],
               f"登记表里每一份都在磁盘上——不在位的那份会被这条门 continue 掉，"
               f"视野悄悄缺一格而结论照绿：{_absent}")

    # ── 3) 逐份承重：把某一份真脚本的表态那一行删掉，门必须当场响在这一份上 ──
    # 只看「今天返回 []」是不够的：登记表现在有 7 份，判据也可能对其中几份压根没生效。
    for rel in gate.SHELL_SOURCES:
        text = (_REPO / rel).read_text(encoding="utf-8", errors="replace")
        _ok, _on, _ln, _fr = gate._errexit_posture(text)
        _assert(_ok, f"{rel}：现状就该有表态（_errexit_posture 读不到＝上面那条 [] 是空转）")
        if not _ln:
            continue
        _stripped = "\n".join(ln for i, ln in enumerate(text.splitlines(), 1) if i != _ln)
        _got = gate.check_shell_errexit_posture(sources=(), texts=((rel, _stripped),))
        _assert_eq(len(_got), 1,
                   f"承重判据：删掉 {rel} 第 {_ln} 行那句表态后，门必须当场响在这一份上"
                   f"（不响=这条门对真仓库的这一格没有覆盖）")
        _assert(rel in _got[0] and "第一条真命令" in _got[0],
                f"响的那句要点名是哪一份、以及为什么（实际={_got[0][:110]}）")

    # ── 4) 反向对照：表态与不表态、表态早与表态晚，八种形状各判一次 ──
    def _post(script: str) -> list:
        return gate.check_shell_errexit_posture(sources=(), texts=(("夹具", script),))

    for _txt, _want, _why in (
        ("X=$(ls /nope)\necho hi\n", 1, "完全没表态：生死交给调用方"),
        ("set +e\nX=$(ls /nope)\necho hi\n", 0, "第一句就把继承来的 errexit 关掉并写明走显式分支"),
        ("set -Eeuo pipefail\nX=$(ls /nope) || true\n", 0, "开起来并逐条接管非零，同样算表态"),
        ("set +o errexit\necho hi\n", 0, "长写法算表态（只认 `set +e` 的字面会逼人写假表态）"),
        ("set -u\nset -x\nX=$(ls /nope)\n", 1, "路过的 set 里没提 errexit 不算表态"),
        ("#!/bin/bash\n# set -e\nX=$(ls /nope)\n", 1, "注释里的表态不算（否则解释都能过门）"),
        ("X=$(ls /nope)\nset +e\necho hi\n", 1, "表态来晚了：它之前那句已经有机会把脚本带走"),
        ("#!/bin/bash\n# 只有说明\n", 1, "既没表态也没真命令＝这份被清空了，不该留在登记表里"),
    ):
        _got = _post(_txt)
        _assert_eq(len(_got), _want,
                   f"反向对照：{_why}（夹具={_txt.splitlines()[:2]}，实际={_got[:1]}）")

    # ── 5) 与第 15 条联动：默认口径不许跟着表态走（那是本轮证伪出来的假绿口子）──
    # 表态**之后**的段落由 _errexit_segments 逐行跟着脚本自己的 set 切换；表态**之前**那几行
    # 仍然交在调用方手里，所以那一段必须按 -e 生效判。这一格分开测两条边：早于表态的裸赋值
    # 必须红，晚于表态的裸赋值必须放行；而且两条门要一起响、一起放行。
    _tmp = tempfile.mkdtemp(prefix="al_prologue_")
    try:
        _early = Path(_tmp) / "fx_prologue_early.sh"
        _early.write_text("#!/bin/bash\nV=$(ls /nope 2>/dev/null)\nset +e\necho hi\n",
                          encoding="utf-8")
        _late = Path(_tmp) / "fx_prologue_late.sh"
        _late.write_text("#!/bin/bash\nset +e\nV=$(ls /nope 2>/dev/null)\necho hi\n",
                         encoding="utf-8")
        for _fx, _want, _why in (
            (_early, 1, "表态之前的裸赋值——那一段的生死不归脚本自己定，按最坏情形判"),
            (_late, 0, "作者第一句就明确关掉 errexit 之后的裸赋值——这一段本来不会静默死"),
        ):
            _got = gate.check_shell_errexit_hazards(sources=(_fx.as_posix(),), workflows=())
            _assert_eq(len(_got), _want,
                       f"第 15 条默认口径：{_why}（实际={_got[:1]}）")
            _p28 = gate.check_shell_errexit_posture(sources=(_fx.as_posix(),))
            _assert_eq(len(_p28), 1 if _fx is _early else 0,
                       f"两条门要一起响/一起放行，不许出现「立场绿了而静默死没人管」的中间态"
                       f"（{_fx.name} 实际={_p28[:1]}）")
    finally:
        shutil.rmtree(_tmp, ignore_errors=True)

    # ── 6) 端到端：真按 Actions 那对选项起批跑入口的 --list（本机不带选项永远是绿的）──
    # 这一格是整节的核心：前面五格判的都是「读源码的门」，而本轮的事故只有在**把脚本用调用方
    # 那两种方式各起一次**时才现形。少了这一格，第 28 条就只是一句注释。
    _argv28, _note28 = _bash_argv()
    _bash = _argv28[0] if _argv28 else ""
    if not _bash:
        print(f"   ⏭ 本机没有可用的 bash（{_note28}）：这一格要真起脚本，判不了就明说判不了，"
              "不许拿「静态那几格过了」当这格过了")
    elif runner.is_file():
        _plain = subprocess.run([_bash, str(runner), "--list"], cwd=str(_REPO),
                                capture_output=True, stdin=subprocess.DEVNULL, timeout=300)
        _strict = subprocess.run([_bash, "-e", "-o", "pipefail", str(runner), "--list"],
                                 cwd=str(_REPO), capture_output=True,
                                 stdin=subprocess.DEVNULL, timeout=300)
        _assert_eq(_plain.returncode, 0,
                   f"不带选项这一路必须绿（批跑入口自己的退出码）：\n{_plain.stderr[-600:]}")
        _assert_eq(_strict.returncode, 0,
                   f"带上 Actions 给 bash 步的那对选项也必须绿——本轮云端的签名是「退出码 1、"
                   f"stdout 0 字节、stderr 0 字节」，现在是（退出码 {_strict.returncode}｜"
                   f"stdout {len(_strict.stdout)} 字节｜stderr {len(_strict.stderr)} 字节）："
                   f"\n{_strict.stderr[-600:]}")
        _assert_eq(_strict.stdout, _plain.stdout,
                   "两种调用方式的 stdout 必须**逐字节一致**：批跑清单的内容不许取决于谁把它"
                   "拉起来（少了这条，上一格可能只是在比两份同样被截断的输出）")
        _lines = [x for x in _strict.stdout.decode("utf-8", errors="replace").splitlines()
                  if x.strip()]
        _assert(len(_lines) >= 50,
                f"-e 下也要展开出完整清单（实际 {len(_lines)} 行；少于 50 说明发现逻辑半路就断了）")
        _assert(all(x.endswith(".py") for x in _lines),
                "--list 的 stdout 契约：只有套件路径，进度与诊断一律走 stderr")

        # 6b) 这一格的判据自己得能红：同一条裸赋值在 -e 下必须当场静默死。不放这条反向对照，
        # 上面那句「_strict.returncode == 0」有可能是在测一件根本测不出来的事（这台机器的 bash
        # 恰好不把 -e 交给子进程，而脚本里也已经没有任何会非零的取值）。
        _tmp2 = tempfile.mkdtemp(prefix="al_sig_")
        try:
            _fx = Path(_tmp2) / "fx_silent_death.sh"
            _fx.write_text("#!/bin/bash\nV=$(ls /definitely-not-here-xyz 2>/dev/null)\n"
                           "echo SHOULD-NOT-PRINT\n", encoding="utf-8")
            _p = subprocess.run([_bash, str(_fx)], capture_output=True,
                                stdin=subprocess.DEVNULL, timeout=60)
            _s = subprocess.run([_bash, "-e", "-o", "pipefail", str(_fx)], capture_output=True,
                                stdin=subprocess.DEVNULL, timeout=60)
            _assert_eq(_p.returncode, 0, "不带选项：这份夹具正常跑完（echo 都打出来了）")
            if _s.returncode == 0:
                # macOS 的 /bin/bash 3.2 就是这一侧（实测不把 -e 交给子 bash）。这不是缺陷，而
                # 是本轮「同一份脚本两个平台两种生死」的另一半，所以如实打出来但不判红——判红
                # 会让 mac 上的回归永远红着一句与代码无关的话。
                print("   ℹ 本机 bash 不把调用方的 -e 交给子脚本（/bin/bash 3.2 的实测行为），"
                      "6b 在这一侧只能验到「不带选项」；Git Bash/云端会验到静默死")
            else:
                _assert_eq(_s.stdout, b"",
                           f"静默死的签名要复现得出来：非零退出且一个字都不打"
                           f"（实际 stdout={_s.stdout[:80]!r}）")
        finally:
            shutil.rmtree(_tmp2, ignore_errors=True)
    else:
        _assert(False, "端到端这一格没法判：找不到 bash 或批跑入口不在位（静默跳过＝这条格从没跑过）")


def test_line_ending_stance_gate():
    print("\n═══ AM. 行尾立场必须写在仓库里：第 29 条硬门要有牙 ═══")
    # 由来（Windows 云端实发）：GitHub 的 Windows runner 镜像把 git 全局 core.autocrlf 设成
    # true，actions/checkout@v4 不覆盖它，而本仓库此前对行尾一个字都没说——同一份提交在
    # Windows 工作区里被检出成 CRLF，在 mac/Linux 里是 LF。本机把批跑入口整份转成 CRLF 实测
    # 过后果：rc=2｜stdout 0 字节｜stderr 360 字节，`line 31: set: +: invalid option`、
    # `line 85: syntax error near unexpected token 'do'`。
    # 最要紧的一格是：**`bash -n` 对同一份 CRLF 文件是过的**（第 12 条只解析不执行），所以
    # 行尾差异骗得过语法门，只在真跑那一刻炸——云端红、构建全绿、本机永远绿。这一节钉的就是
    # 「解析得过、跑不起来」那一段空白，外加「立场必须写在仓库里」：写在某台机器的全局配置上
    # 的 autocrlf 换一台机器就没了，那正是本轮踩到的形状。
    gate = _manifest_gate()
    gate_src = (_REPO / ".github" / "scripts" / "check_packaging_manifest.py").read_text(
        encoding="utf-8")

    # ── 1) 接线：判据在、main 真调、调了进退出码、云端 annotation 能照做 ──
    _assert(callable(getattr(gate, "check_line_ending_stance", None)),
            "硬门模块里有第 29 条这个函数（能被单独喂夹具，才谈得上反向对照）")
    _at = gate_src.find("line_ending = check_line_ending_stance()")
    _assert(_at > 0, "硬门 main() 里真的调了第 29 条（函数在而不调＝这条门从没跑过）")
    _assert("bad += 1" in gate_src[_at:_at + 900],
            "第 29 条报出来的问题要并进不合格计数（只 print 不改退出码的门等于公告栏）")
    _assert("::error::行尾立场" in gate_src[_at:_at + 900],
            "云端 annotation 要能直接照做：写明缺的是行尾立场，不是「重跑一次」")
    _assert_eq(gate.LINE_ENDING_STANCE_FILE, ".gitattributes",
               "立场文件的名字要说死：换一个名字等于换一台机器再重新找一遍是谁在管行尾")

    # ── 2) 视野：交付 shell 每一份都得在这条门的范围内（登记了却不被看＝Windows 上没人管）──
    _scope = gate._line_ending_scope()
    for rel in gate.SHELL_SOURCES:
        _assert(rel in _scope,
                f"{rel} 在第 29 条的视野里（它在第 11/20e 条登记了，这一条不看它，"
                f"检出的行尾就归某台机器的 git 配置管）")
    for rel in gate.WORKFLOWS:
        _assert(rel in _scope, f"{rel} 在第 29 条的视野里（workflow 里的 shell 步同样只在 LF 上能跑）")

    # ── 3) 判一（CR）承重：把每一份真脚本换成 CRLF 喂进去，必须当场响在这一份上 ──
    def _cr_of(label: str, text: str) -> list:
        # 标签用真路径：门对内存夹具报的是「喂进来的那一份叫什么」，叫错了下一个读红话的人
        # 就照着错的名字去找文件（第 11 条同一道理：归因错了比不归因更费时间）。
        return [p for p in gate.check_line_ending_stance(sources=(), texts=((label, text),))
                if "处 CR" in p]

    for rel in gate.SHELL_SOURCES:
        _lf = (_REPO / rel).read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")
        _crlf = _lf.replace("\n", "\r\n")
        _got = _cr_of(rel, _crlf)
        _assert_eq(len(_got), 1,
                   f"{rel} 整份转成 CRLF 必须响（不响＝真仓库那条 ✅ 只是在判一个空集合）")
        if _got:
            _assert(rel in _got[0] and str(_lf.count("\n")) in _got[0],
                    f"响的那句要点名是哪一份、有几处 CR（实际={_got[0][:130]}）")
        _assert_eq(_cr_of(rel, _lf), [],
                   f"{rel} 同一份内容按 LF 喂进去不许响（红了说明判的是文件名而不是字节）")
        # 只改一行行尾才是真难查的那种：本机照样跑得起来，换台机器检出后未必。
        _half = _lf.replace("echo hi\n", "echo hi\r\n", 1) if "echo hi\n" in _lf \
            else _lf.replace("\n", "\r\n", 1)
        _assert_eq(len(_cr_of(rel, _half)), 1,
                   f"{rel} 只有**一处**行尾是 CRLF 也要响（按「整份是不是 CRLF」判会放过这种）")

    # ── 4) 判二：git 问不到 / 答非 lf / 立场文件没进仓库，三格各响一次 ──
    with _patch(gate, "_line_ending_stances", lambda paths: {}):
        _got = gate.check_line_ending_stance()
        _assert(any("问不到 git 的行尾立场" in p for p in _got),
                "问不到 git 的回答时不许按合格返回（这一条判的就是检出长什么样）")
    _crlf_map = {rel: {"text": "set", "eol": "crlf"} for rel in gate.SHELL_SOURCES}
    with _patch(gate, "_line_ending_stances", lambda paths: dict(_crlf_map)):
        _got = gate.check_line_ending_stance()
        _assert(any("eol 不是 lf" in p and gate.LINE_ENDING_STANCE_FILE in p for p in _got),
                f"git 答 crlf 时必须响，并且把修法说成改仓库立场而不是某台机器的配置"
                f"（实际={_got[:1]}）")
    with _patch(gate, "tracked_state", lambda p: "missing"):
        _got = gate.check_line_ending_stance()
        _assert(any(gate.LINE_ENDING_STANCE_FILE in p and "git add" in p for p in _got),
                "立场文件没进仓库时必须响：写在某台机器上的全局配置换一台就没了，那正是本轮事故")
    # 空视野不许绿：登记表里的路径都不在位、夹具也是空的，这时候「行尾合格」没有任何含义。
    _tmpE = Path(tempfile.mkdtemp(prefix="am_empty_scope_"))
    try:
        with _patch(gate, "ROOT", _tmpE), \
                _patch(gate, "_line_ending_stances", lambda paths: {}):
            _got = gate.check_line_ending_stance(sources=("zz_不在位.sh",))
        _assert(any("一份交付文本都没读到" in p for p in _got),
                f"视野是空的却不许报绿（实际={_got[:1]}）")
    finally:
        shutil.rmtree(_tmpE, ignore_errors=True)

    # ── 5) 真仓库今天：CR 一格都不许有，eol 一格都不许有；立场文件那一格如实说 ──
    _today = gate.check_line_ending_stance()
    _assert_eq([p for p in _today if "处 CR" in p], [],
               "第 29 条判一：交付链里没有一份文本带着 CR（红了就照那句改文件，不是改 git 配置）")
    _assert_eq([p for p in _today if "eol 不是 lf" in p], [],
               "第 29 条判二：git 对每一份交付文本都答 eol=lf（立场真的在仓库里生效了）")
    _stance = [p for p in _today if gate.LINE_ENDING_STANCE_FILE in p]
    _assert(len(_stance) <= 1,
            f"今天剩下的行尾问题只能是立场文件在 git 里的那一格（实际={_stance}）")
    if _stance:
        # 这一格不掩盖也不重复判红：门禁自己会一直响到那次提交为止，本节只保证它响的是这句话。
        print("   ℹ 此刻 .gitattributes 还没进提交（只 add 没 commit）——门禁第 29 条会一直红到"
              "提交那一刻，本节的夹具不把它读成绿")

    # ── 6) 端到端：真起一个临时 git 仓库，把 .gitattributes 装上/摘下，门的答案必须跟着变 ──
    # 前五格判的都是「读字节的门」和「喂进内存的字典」。本轮的事故只在**换一台机器之后**才
    # 现形，所以这一格必须让 git 真的回答：同一份仓库、同一批文件，多的只有那份被提交的
    # .gitattributes——判二从红转绿，才证明门读的是仓库的立场，而不是自己写的字典。
    _tmpR = Path(tempfile.mkdtemp(prefix="am_real_repo_"))
    try:
        _gi = subprocess.run(["git", "init", "-q", str(_tmpR)],
                             capture_output=True, text=True, check=False)
        if _gi.returncode != 0:
            _assert(False, f"端到端这一格没法判：git init 起不来（{(_gi.stderr or '')[:120]}）")
        else:
            (_tmpR / "fx_lean.sh").write_text("#!/bin/bash\nset +e\necho hi\n", encoding="utf-8")

            def _commit():
                subprocess.run(["git", "add", "-A"], cwd=str(_tmpR), capture_output=True,
                               text=True, check=False)
                subprocess.run(["git", "-c", "user.email=fx@example.invalid",
                                "-c", "user.name=fx", "commit", "-q", "-m", "fx"],
                               cwd=str(_tmpR), capture_output=True, text=True, check=False)

            with _patch(gate, "ROOT", _tmpR):
                _commit()
                gate.reset_git_cache()
                _before = gate.check_line_ending_stance(sources=("fx_lean.sh",))
                (_tmpR / gate.LINE_ENDING_STANCE_FILE).write_text(
                    "* text=auto eol=lf\n*.sh text eol=lf\n", encoding="utf-8")
                _commit()
                gate.reset_git_cache()
                _after = gate.check_line_ending_stance(sources=("fx_lean.sh",))
            _assert(any("eol 不是 lf" in p for p in _before),
                    f"仓库里没有 .gitattributes 时判二必须响（这正是本轮云端的现场；实际={_before[:2]}）")
            _assert_eq([p for p in _after if "eol 不是 lf" in p
                        or gate.LINE_ENDING_STANCE_FILE in p], [],
                       f"只多了那份被提交的立场文件，判二就要转绿（实际={_after[:2]}）")
    finally:
        gate.reset_git_cache()
        shutil.rmtree(_tmpR, ignore_errors=True)


def test_workflow_yaml_ctrl_gate():
    print("\n═══ AM2. workflow 作为 YAML 必须能被读懂：第 45 条硬门要有牙 ═══")
    # 由来（2026-11-21 legacy run#6 实发）：生成器注释里的示例字节 \\fcdce… 写在普通
    # 三引号串里，反斜杠+f 被 Python 解成换页符 0x0C 写进 yml——YAML 拒收控制字符，
    # 整条流水 jobs=0 静默失败（run 名退化成文件路径，连 job 都不展开）。而本机所有门
    # 全绿：第 11 条 bash -n 量的是步骤里的 shell，量不到 yml 本身。这一节钉的就是
    # 「shell 都合格、workflow 却读不懂」那一段空白。
    gate = _manifest_gate()
    gate_src = (_REPO / ".github" / "scripts" / "check_packaging_manifest.py").read_text(
        encoding="utf-8")

    # ── 1) 接线：判据在、main 真调、调了进退出码、云端 annotation 能照做 ──
    _assert(callable(getattr(gate, "check_workflow_yaml_ctrl", None)),
            "硬门模块里有第 45 条这个函数（能被单独喂夹具，才谈得上反向对照）")
    _at = gate_src.find("yaml_ctrl = check_workflow_yaml_ctrl()")
    _assert(_at > 0, "硬门 main() 里真的调了第 45 条（函数在而不调＝这条门从没跑过）")
    _assert("bad += 1" in gate_src[_at:_at + 900],
            "第 45 条报出来的问题要并进不合格计数（只 print 不改退出码的门等于公告栏）")
    _assert("::error::workflow 控制字符" in gate_src[_at:_at + 900],
            "云端 annotation 要能直接照做：写明缺的是哪一格，不是「重跑一次」")

    # ── 2) 承重：每一份真 workflow 注入一个换页符喂进去，必须当场响在这一份上 ──
    for rel in gate.WORKFLOWS:
        _clean = (_REPO / rel).read_text(encoding="utf-8")
        _assert_eq(gate.check_workflow_yaml_ctrl(workflows=(), texts=((rel, _clean),)), [],
                   f"{rel} 干净字节喂进去不许响（红了说明判的是文件名而不是字节）")
        # 这里故意不用字面转义：真实换页字节写进源码字符串会被第 34 条那个
        # 「反斜杠已被解掉一次」自查扫到（它拦的就是本仓库自己踩过的 0x0C 那族）；
        # chr(0x0C) 语义相同、意图显式，且不会让判据套件自己被自己的门拦停。
        _dirty = _clean.replace("\n", "\n" + chr(0x0C), 1)
        _got = gate.check_workflow_yaml_ctrl(workflows=(), texts=((rel, _dirty),))
        _assert_eq(len(_got), 1,
                   f"{rel} 注入一个换页符必须响（不响＝真仓库那条 ✅ 只是在判一个空集合）")
        if _got:
            _assert(rel in _got[0] and "U+000C" in _got[0] and "第 2 行" in _got[0],
                    f"响的那句要点名哪一份、哪一行、哪个字符（实际={_got[0][:130]}）")

    # ── 3) 空视野不许绿；tab/换行/回车是合法空白不许误伤 ──
    _assert(any("一份 workflow 都没读到" in p
                for p in gate.check_workflow_yaml_ctrl(workflows=(), texts=())),
            "视野是空的却不许报绿（这时「YAML 干净」没有任何含义）")
    _assert_eq(gate.check_workflow_yaml_ctrl(workflows=(), texts=(("fx", "a:\n\tb\r\nc\t"),)), [],
               "tab/换行/回车是 YAML 合法空白，误伤它们等于把正常 workflow 拦在门外")

    # ── 4) 真仓库今天：六份 workflow 一个控制字符都不许有 ──
    _assert_eq(gate.check_workflow_yaml_ctrl(), [],
               "第 45 条：交付链的 workflow 里没有 YAML 拒收的控制字符（红了就照那句查生成器转义）")

    # ── 5) 变异复验：把生成器的修复退回原样（双写反斜杠→单写），重跑生成器，
    #      第 45 条必须精准红在 legacy 那一份上；判完逐字节还原两份文件 ──
    _gen_path = _REPO / "debug" / "gen_legacy_workflow.py"
    _leg_path = _REPO / ".github" / "workflows" / "build-b2bcollect-legacy.yml"
    _gsrc, _ysrc = _gen_path.read_bytes(), _leg_path.read_bytes()
    _anchor = "\\\\fcdce"          # 生成器里的双写反斜杠（修复态）
    if _gsrc.decode("utf-8").count(_anchor) != 1:
        _assert(False, f"变异锚点在生成器里出现 {_gsrc.decode('utf-8').count(_anchor)} 次"
                       "（不是 1 次就不知道改到哪一格）")
    else:
        try:
            _gen_path.write_bytes(_gsrc.decode("utf-8").replace(_anchor, "\\fcdce", 1).encode("utf-8"))
            _r = subprocess.run([sys.executable, str(_gen_path)], capture_output=True,
                                text=True, encoding="utf-8", errors="replace")
            _assert_eq(_r.returncode, 0,
                       f"变异后重跑生成器必须能跑（rc={_r.returncode}）：{_one_line(_r.stdout + _r.stderr)[-160:]}")
            _got = gate.check_workflow_yaml_ctrl(workflows=(".github/workflows/build-b2bcollect-legacy.yml",))
            _assert_eq(len(_got), 1,
                       "把双写反斜杠退回单写（run#6 原样），第 45 条必须精准红一条——"
                       "否则换页符再漏进 yml 时没人知道，整条流水又是 jobs=0 静默失败")
        finally:
            _gen_path.write_bytes(_gsrc)
            _leg_path.write_bytes(_ysrc)
        _assert(_gen_path.read_bytes() == _gsrc and _leg_path.read_bytes() == _ysrc,
                "变异还原后必须逐字节回到原样（还原不干净＝下一节的输入被污染）")


def test_forensic_job_log_channel_gate():
    print("\n═══ AN. 取证脚本判「日志拿到没有」必须按 HTTP 状态，不许按魔法字节 ═══")
    # 由来（本轮本机实发）：debug/fetch_run_log.sh 用 `[ "$(head -c 2 落点)" = "PK" ]`
    # 判「这是不是日志」，不是就当错误响应 continue 掉。本机对着 run 43664465393 的 job
    # 132660669361 实测取了一次：HTTP 200｜Content-Type: text/plain｜53545 字节｜开头 EF BB BF。
    # GitHub 的 /actions/jobs/{id}/logs 返回的**本来就是纯文本**，zip 只属于
    # /actions/runs/{id}/logs 那个归档接口。于是这份取证脚本每一次正常取证都在自己的判据上
    # 被丢掉，而它下面读日志的 python 早就写了纯文本分支——永远跑不到。
    # 症状不是报错，是一句「命中 0 行错误线索」：人拿着它去找一个不存在的 bug。同一天
    # Windows 门禁那条红（硬门 23/24/25 的范围全长在那份清单上）就是这么被绕过去的。
    #
    # 为什么这条写在离线套件而不是编号硬门里：它判的是一份**只在本机跑的取证脚本**的内部
    # 形状，不参与打包与检出；编号硬门的每一条都要在构建机上成立才有意义。这里要的是「下一
    # 次有人把 PK 判据写回来就当场响」，交付前本机必跑这一份，就够了。
    rel = "debug/fetch_run_log.sh"
    sh = (_REPO / rel).read_text(encoding="utf-8")

    def _problems(text: str) -> list:
        """这份取证脚本对「拿到的是不是一份可用的日志」表了哪几种态（缺一种就算一条）。"""
        out = []
        if "curl" not in text:
            return out                        # 没有下载这一回事：这一节对它没有结论
        if "%{http_code}" not in text:
            out.append("没问过 HTTP 状态码：curl 的退出码只能发现「连都没连上」，"
                       "发现不了连上了却回 401/404/限流——那种响应体是一段 JSON，"
                       "最容易被读成「云端没报错」")
        if not re.search(r"^\s*2\?\?\)", text, re.M):
            out.append("没有「2xx 才算拿到」这一分档：状态码问到了却按别的字节决定丢弃，等于没问")
        # PK 只能用来决定「要不要真解一遍」，不许用来决定「这是不是日志」。判的是语句形状：
        # 「比头两字节的 if」带一个 else 分支、那个分支当场丢弃这个 job——这正是上一版的原话，
        # 而现在的 `is_zip "$dst"` 只在 then 分支里决定要不要 unzip -t，没有 else 丢弃。
        for m in re.finditer(r'\[\s*"\$\(head -c 2[^\n]*"PK"\s*\][^\n]*then(.{0,600}?)\belse\b'
                             r'\s*\n\s*(continue|return|exit|rm -f)', text, re.M | re.S):
            out.append(f"第 {text[:m.start()].count(chr(10)) + 1} 行拿头两字节是不是 PK 决定要不要"
                       "丢掉日志（else 分支当场丢弃这个 job）：这个端点返回的是 text/plain，"
                       "纯文本日志会被当成错误响应扔掉")
        if not re.search(r"wc -c|wc_bytes|size_download", text):
            out.append("没有「状态码 2xx 但响应体 0 字节」这一格：那同样是什么都没取到，"
                       "交给分析段去猜就会得到「没有错误行」")
        if re.search(r"\.read\(\)\.decode\(|raw\.decode\(", text) and "utf-8-sig" not in text:
            out.append("整份日志体按 utf-8 解：实测这份日志以 EF BB BF 开头，BOM 会留在第一行"
                       "第一个字符上，行首特征（::error::、##[group]）从此永远匹配不到")
        return out

    _now = _problems(sh)
    _assert_eq(_now, [],
               f"{rel} 现状要对「拿到日志没有」五格全表态（实际={_now[:1]}）")

    # ── 反向对照：把上一版（真在本机错过的取证形状）与各缺格的写回一次，门必须各响一次 ──
    _OLD = ('  curl -sSL -m 180 -o "$L" "$U"\n'
            '  if [ "$(head -c 2 "$L")" = "PK" ]; then\n'
            '    unzip -tqq "$L" >/dev/null 2>&1 || continue\n'
            '  else\n'
            '    continue\n'
            '  fi\n'
            '  /usr/bin/python3 -c \'print(open("$L","rb").read().decode("utf-8"))\'\n')
    _got = _problems(_OLD)
    _assert_eq(len(_got), 5, f"上一版那一份要五格全响（实际={_got}）")
    for _needle, _why in (("HTTP 状态码", "没问状态码"), ("2xx 才算拿到", "没有 2xx 分档"),
                          ("决定要不要丢掉日志", "PK 当门禁——本轮实际错过取证的就是那一句"),
                          ("0 字节", "没有空响应体那一格"), ("EF BB BF", "按 utf-8 解带 BOM 的日志")):
        _assert(any(_needle in p for p in _got), f"{_why}的那一版要响（实际={_got[:1]}）")
    # 说明文字加回去、缺陷留着，仍然要五格全响：这一格把上面五条从「数关键词」里摘出来。
    _assert_eq(len(_problems("# 这里本来会核对方返回的错误响应与编码\n" + _OLD)), 5,
               "判的是代码形状，不是文案里出现没有这些词（能靠注释过关的门等于没有门）")
    # 只修一半不算修好：一格一格补上去，每补一格少一条，且不是一上来就全绿。
    _CODE = '  code=$(curl -sSL -m 180 -o "$L" -w \'%{http_code}\' "$U")\n'
    _CASE = '  case "$code" in\n    2??) ;;\n    *) return 2 ;;\n  esac\n'
    _SIZE = '  [ "$(wc -c < "$L")" = "0" ] && { echo "响应体是空的"; continue; }\n'
    _SIG = _OLD.replace('decode("utf-8")', 'decode("utf-8-sig", "replace")')
    _ladder = [len(_problems(_p + _OLD)) for _p in
               ("", _CODE, _CODE + _CASE, _CODE + _CASE + _SIZE)]
    _assert_eq(_ladder, [5, 4, 3, 2],
               f"每补一格少一条：{_ladder}（一次补满或原地不动，都说明判据没在逐格承重）")
    _assert_eq(len(_problems(_CODE + _CASE + _SIZE + _SIG)), 1,
               "编码那一格要真能靠 utf-8-sig 消掉，剩下的 1 条必须是 PK 那一格"
               f"（实际={_problems(_CODE + _CASE + _SIZE + _SIG)[:1]}）")
    # 取证脚本自己也在第 20/28 条的视野里（登记表、curl 有界、errexit 立场），这里别重复判。
    gate = _manifest_gate()
    _assert(rel in gate.SHELL_SOURCES, f"{rel} 在交付链 shell 登记表里（不在表里的取证脚本，"
                                       "其余四道门在它身上全是空的）")


def test_reason_one_handle_gate():
    print("\n═══ AO. 失败原因只准一个把手：第 30 条硬门要有牙（**包括判据自己**）═══")
    # 由来（2026-11-16 同类查全）：实机报告里念出过 `【连不上·无应答】b2b.11467.com:443｜
    # Connection to b2b.11467.com timed out. (con）`——半截词＋补完又被剪的右括号。这一族在
    # 交付链里有 41 处各写各的截断，收进一个把手之后，第 30 条负责「下一个人在这里再写一刀」
    # 时当场被拦住。
    # 本节最要紧的一格是 4)：门最初那版形状判据只认 `ast.Index`，而 Python 3.9 起的解析树里
    # `[0]` 直接是 Constant——**一条也匹配不上，于是全仓报绿**。绿得毫无根据的门比没有门更贵：
    # 它让下一个人以为自己已经被人管住了。所以每一条判据都要喂一份「改回旧写法」的字节，
    # 看它是不是真的响在这一份上。
    gate = _manifest_gate()
    gate_src = (_REPO / ".github" / "scripts" / "check_packaging_manifest.py").read_text(
        encoding="utf-8")
    _rs = gate._runtime_reason_sources()

    # ── 1) 接线：判据在、main 真调、调了进退出码、云端 annotation 能照做 ──
    _assert(callable(getattr(gate, "check_reason_one_handle", None)),
            "硬门模块里有第 30 条这个函数（能被单独喂夹具，才谈得上反向对照）")
    _at = gate_src.find("reason_handle = check_reason_one_handle()")
    _assert(_at > 0, "硬门 main() 里真的调了第 30 条（函数在而不调＝这条门从没跑过）")
    _assert("bad += 1" in gate_src[_at:_at + 1200],
            "第 30 条报出来的问题要并进不合格计数（只 print 不改退出码的门等于公告栏）")
    _assert("::error::原因收口把手" in gate_src[_at:_at + 1200],
            "云端 annotation 要能直接照做：写明缺的是收口把手，不是「重跑一次」")
    _assert_eq(gate.REASON_HANDLE, "b2bcollect_build/text_shape.py",
               "真源的文件名要说死：换到别处就等于又开一份实现")

    # ── 2) 视野：上一轮漏掉的那几份必须在这一条的范围里（视野比缺陷小＝等于没修）──
    for rel in ("b2bcollect_build/text_shape.py", "b2bcollect_build/cdp_base.py",
                "b2bcollect_build/b2bcollect_gui.py", "b2bcollect_build/parallel_session.py",
                "b2bcollect_build/site_adapters.py", "b2bcollect_build/access_guard.py",
                "b2bcollect_build/channel_health.py", "b2bcollect_build/local_scan.py",
                "b2bcollect_build/oss_pool_client.py", "ali1688_build/ali1688_crawler.py",
                "riskbird_build/chrome_driver.py"):
        _assert(any(str(k).endswith(rel) for k in _rs),
                f"{rel} 在第 30 条的视野里（上一轮 11p 只扫 *_crawler.py，这十份一刀没动）")
    _assert_eq([str(k) for k in _rs if Path(k).name.startswith(("test_", "debug_", "probe_"))],
               [], "测试/一次性脚本不进这条门的视野：它们的话术不会被念给用户听")

    # ── 3) 判一（30a 形状）：旧写法喂进去必须响，且三种历史形状各响一次 ──
    def _shape_problems(name, body):
        got = gate.check_reason_one_handle(files={_REPO / "b2bcollect_build" / name: body})
        return [p for p in got if "按字符数切了一刀" in p]

    _OLD_SHAPES = (
        ("gui 告警那一档（异常取首行又切 120）",
         "def f(e):\n    x = str(e).splitlines()[0][:120]\n    return x\n", "取完首行"),
        ("会话日志那一档（[:70]）",
         "def f(e):\n    return str(e).splitlines()[0][:70]\n", "取完首行"),
        ("把手结果又被补一刀（[:90]，本轮实测的漏网形状）",
         "def f(e):\n    return _short_err(e)[:90]\n", "调完把手"),
        ("收口函数再被切（[:40]）",
         "def f(why):\n    return short_reason(why, 70)[:40]\n", "调完把手"),
    )
    for label, body, kind in _OLD_SHAPES:
        _got = _shape_problems("zz_shape_a.py", body)
        _assert_eq(len(_got), 1, f"{label}：喂进去必须响一条（不响＝这条判据在 3.13 的解析树上是空壳）")
        if _got:
            _assert(kind in _got[0], f"{label}：响的话要指明是哪一种形状（实际={_got[0][:150]}）")

    # 反向的另一半：走把手的写法、以及本来就该限宽的公司名/URL 切片都不许误响。
    _false = _shape_problems(
        "zz_shape_b.py",
        "def f(e, why, rec):\n"
        "    a = short_reason(why, 70)\n"
        "    b = first_line(e, 140)\n"
        "    c = rec['公司名称'][:24]\n"
        "    d = url[:80]\n"
        "    return a, b, c, d\n")
    _assert_eq(_false, [], f"合格写法不许误响（误响的代价是人们把整条门关掉，实际={_false[:1]}）")

    # ── 4) 判二（30b 两份同字节）：内联那份被改动必须响，改回去又不响 ──
    _mirror_rel = gate.REASON_MIRRORS[0]
    _mirror_real = str(_rs[[k for k in _rs if str(k).endswith(_mirror_rel)][0]])
    _drift = _mirror_real.replace('_REASON_BREAKS = "｜.：，、；)） ·"',
                                  '_REASON_BREAKS = "｜.：，、；)）"', 1)
    _assert(_drift != _mirror_real,
            "夹具没能把镜像的断点表改短（真源那一行换了写法，这一格要跟着换，不能悄悄空跑）")
    _got = gate.check_reason_one_handle(
        files=_rs, mirrors={_mirror_rel: _drift})
    _assert(any("不是逐字节相同" in p and _mirror_rel in p for p in _got),
            f"内联那份的断点表被改短必须响（实际={_got[:1]}）")
    _assert_eq([p for p in gate.check_reason_one_handle(
        files=_rs, mirrors={_mirror_rel: _mirror_real}) if "不是逐字节相同" in p], [],
        "同一份字节喂回去不许响（响了说明判的是文件名而不是内容）")

    # ── 5) 判三（30c 性质）：把把手换成三种「看起来还在收口」的坏实现，每种都要被抓住 ──
    _NAIVE = '''import re
_REASON_BREAKS = "｜.：，、；)） ·"
def short_reason(text, limit=60):
    s = re.sub(r"\\s+", " ", str(text or "")).strip()
    return s if len(s) <= limit else s[:limit] + "…"
def first_line(text, limit=120):
    s = str(text or "").strip()
    return "" if not s else short_reason(s.splitlines()[0], limit)
'''
    _NOHEAD = '''import re
_REASON_BREAKS = "｜.：，、；)） ·"
def _is_break(s, k):
    return s[k - 1] in _REASON_BREAKS
def short_reason(text, limit=60):
    s = re.sub(r"\\s+", " ", str(text or "")).strip()
    if len(s) <= limit:
        return s
    stop = -1
    for k in range(1, limit + 1):
        if _is_break(s, k):
            stop = k
    return (s[:stop] + "…") if stop > 0 else (s[:limit] + "…")
def first_line(text, limit=120):
    s = str(text or "").strip()
    return "" if not s else short_reason(s.splitlines()[0], limit)
'''
    _NOPAREN = '''import re
_REASON_BREAKS = "｜.：，、；)） ·"
def _is_break(s, k):
    return s[k - 1] in _REASON_BREAKS
def short_reason(text, limit=60):
    s = re.sub(r"\\s+", " ", str(text or "")).strip()
    if len(s) <= limit:
        return s
    head = 0
    if s.startswith("【"):
        i = s.find("｜")
        if 0 < i:
            head = i + 1
    stop = -1
    for k in range(head + 1, limit + 1):
        if _is_break(s, k):
            stop = k
    return (s[:stop] + "…") if stop > 0 else (s + "…")
def first_line(text, limit=120):
    s = str(text or "").strip()
    return "" if not s else short_reason(s.splitlines()[0], limit)
'''
    for label, mutant, need in (
            ("按字符数一刀切（旧写法的原样）", _NAIVE, "切在词中间"),
            ("断句照做、但不整段留住【类别】前缀", _NOHEAD, "把前缀"),
            ("断句照做、括号不成对也上日志", _NOPAREN, "不成对")):
        _got = gate.check_reason_one_handle(files=_rs, handle_src=mutant, mirrors={})
        _assert(any(need in p for p in _got),
                f"把手换成「{label}」必须被性质判据抓住（要看到含「{need}」的那条，"
                f"实际={_got[:2]}）")
        # 变异本身要真的改到了行为：同一句真异常，坏把手确实长出一句好把手不会长的话。
        _ns = {"re": re}
        exec(compile(mutant, "fx_" + need, "exec"), _ns)
        _bad_out = _ns["short_reason"](
            "【连不上·无应答】b2b.11467.com:443｜Connection to b2b.11467.com timed out. "
            "(connect timeout=10)", 70)
        _assert(_bad_out != text_shape.short_reason(
            "【连不上·无应答】b2b.11467.com:443｜Connection to b2b.11467.com timed out. "
            "(connect timeout=10)", 70),
            f"{label}：夹具没造出差别（那这一格的「响」只是碰巧，实际={_bad_out!r}）")

    # ── 6) 判四（30d 空视野）：视野断了不许报绿 ──
    _assert(any("一份源码都没读到" in p for p in gate.check_reason_one_handle(files={})),
            "视野为空时报的是「没有含义」而不是合格（空集合上的绿最容易被当成修完了）")
    _tmpO = Path(tempfile.mkdtemp(prefix="ao_empty_scope_"))
    try:
        with _patch(gate, "ROOT", _tmpO):
            _got = gate.check_reason_one_handle()
        _assert(any("第 30 条" in p for p in _got),
                f"三个 *_build 目录都不在位时必须响（实际={_got[:1]}）")
    finally:
        shutil.rmtree(_tmpO, ignore_errors=True)
    # 解析不过的源码按不合格算，不能按「这一份没问题」算（与第 27 条同一句口径）。
    _got = gate.check_reason_one_handle(files={_REPO / "b2bcollect_build/zz_broken.py":
                                               "def f(:\n"})
    _assert(any("解析不过" in p for p in _got),
            f"语法坏掉的运行时代码要响成一条缺陷（实际={_got[:1]}）")

    # ── 7) 真仓库今天：这一条门在交付链上一次都不许响（夹具全绿不代表今天也绿）──
    _today = gate.check_reason_one_handle()
    _assert_eq(_today, [], f"第 30 条今天必须真绿：交付链里既没有自己切的写法，两份把手也同字节"
                           f"（实际={_today[:2]}）")

    # ── 8) 本段新增两判（30a-3 裸 `str(异常)[:N]`、28 处一族；30a-4 读原因账又切一刀）──
    #    这一格的夹具全部**改真树的字节**而不是造合成代码：合成样本只能证明判据认得
    #    想得到的形状，证明不了「今天已经修好的那几处」从此还有人看着。
    #    其中第一行就是本段立项那一行：第十一轮实机报告念出过
    #    `深链被拒: china.makepolo.com 42 次（… 仅这一条深链，通道入）`。
    #    「健康牌上最后一次取页失败」那一行本来也在这里，2026-11-16 迁到了 AX 节（第 37 条）：
    #    健康牌现在不自己收口原因了，那一格改读采集器渲染好的 `page_ledger`，因此本族钉子
    #    在真树上已经是 0 处（不是它漏了，是口径换了主人）——拿旧写法反证第 30 条也不会响，
    #    因为 `short_reason(...)` 本来就是把手调用；它真正违反的是「念法只住把手」。
    _RETIRE = (
        ("实机报告的渲染层（立项那一行）", "debug/platform_live_check.py",
         "short_reason(v[1], limit)", "str(v[1])[:46]", "读一本"),
        ("本地压缩包展开失败", "b2bcollect_build/local_scan.py",
         'f"展开时出错：{first_line(e, 80)}"', 'f"展开时出错：{str(e)[:80]}"', "str(e)"),
    )
    for _label, _rel, _now, _old, _need in _RETIRE:
        _k = next((k for k in _rs if str(k).endswith(_rel)), None)
        _assert(_k is not None, f"{_rel} 在第 30 条视野里（读不到就没法做这一格的对照）")
        if _k is None:
            continue
        _body = str(_rs[_k])
        _assert_eq(_body.count(_now), 1,
                   f"{_label}：现写法片段在真树上应恰好一处（片段漂了＝这一格在空跑）")
        _got = [p for p in gate.check_reason_one_handle(
            files={**_rs, _k: _body.replace(_now, _old, 1)}) if "按字符数切了一刀" in p]
        _assert_eq(len(_got), 1, f"{_label}：退回旧写法必须响一条（实际={_got[:1]}）")
        if _got:
            _assert(_need in _got[0], f"{_label}：响的话要点名哪一种形状（实际={_got[0][:160]}）")

    # ── 9) 豁免表也要被审：钉原文、换衣服必响、抽掉必报两处（否则表会长成没人敢删的抽屉）──
    _ex = sorted(gate.REASON_SLICE_EXEMPT.items())
    _assert_eq(len(_ex), 2, "今天挂在豁免表上的「切的不是原因」恰好两处（数目变了要重新解释每一条）")
    _arel, _aline = _ex[0][0]
    _ak = next((k for k in _rs if str(k).endswith(_arel)), None)
    _assert(_ak is not None and str(_rs[_ak]).count(_aline) == 1,
            f"豁免表里那一条必须仍在真树原位：{_arel}")
    _mut = re.sub(r"\[:\d+\]", "[:99]", _aline, count=1)
    _assert(_mut != _aline, "夹具没能把那一行的上限改掉（这一格就会空跑）")
    with _patch(gate, "_runtime_reason_sources",
                lambda: {**_rs, _ak: str(_rs[_ak]).replace(_aline, _mut, 1)}):
        _got = gate.check_reason_one_handle()
    _assert(any("不在原位" in p for p in _got),
            f"豁免那一行换了衣服必须响「已不在原位」（不响＝表只被读一次，往后再没人管）：{_got[:1]}")
    _assert(any("按字符数切了一刀" in p for p in _got),
            "同一行还要作为缺陷本体被点名，不能只报表过期（只报表＝下一个人只会删表不会改代码）")

    # ── 10) 视野里额外登记的渲染层：读不到要响，不能当成「这一行不存在」默默过去 ──
    _rk = next((k for k in _rs if str(k).endswith("debug/platform_live_check.py")), None)
    _assert(_rk is not None, "实机报告的渲染层在第 30 条视野里（按目录名把 debug/ 排除掉，"
                             "那一行就永远没人判——上一轮的绿正是这样过的）")
    if _rk is not None:
        with _patch(gate, "_runtime_reason_sources", lambda: {**_rs, _rk: ""}):
            _got = gate.check_reason_one_handle()
        _assert(any("读不到内容" in p for p in _got),
                f"渲染层被读空时必须响（实际={_got[:1]}）")


def test_tab_body_identity_gate():
    print("\n═══ AP. 标签正文身份门：第 31 条硬门要有牙（十处摘门逐条必须响，含把门掏空）═══")
    # 由来（2026-11-15 第九轮实机马可波罗 + 随后的同类查全）：「借浏览器标签取回的那一页」在
    # 五个站各长了一遍，五处各防了不同的东西（麦麦只防重载回哪一页、顺企网只看主机名、MIC 只拒
    # errordocs、gys 只断域名、马可波罗那条腿干脆把已经跑完挑战的标签扔掉）。串页假数据比采不到
    # 更难发现——电话打过去才知道填错了人。这一条门的用处就是把「这里要不要问一句身份」从每次
    # 现场评审变成改代码时必须在 TAB_BODY_EXITS 写下的在案声明。
    # 本节最要紧的是 4)：这一条门的判据在开发过程中被自己的自查翻出过四个盲区（关键字豁免、
    # 域名级门对深链不够、看不见元组出口、污点不跨转手传播）。每一个都是「门报绿而缺陷还在」。
    # 所以这里不喂好话，只喂把已修好的门逐处退回修复前的字节：响不出来的那一格就是下一个盲区。
    gate = _manifest_gate()
    gate_src = (_REPO / ".github" / "scripts" / "check_packaging_manifest.py").read_text(
        encoding="utf-8")
    _rs = gate._runtime_reason_sources()

    # ── 1) 接线：判据在、main 真调、调了进退出码、云端 annotation 能照做 ──
    _assert(callable(getattr(gate, "check_tab_body_identity", None)),
            "硬门模块里有第 31 条这个函数（能被单独喂夹具，才谈得上反向对照）")
    _at = gate_src.find("tab_gate = check_tab_body_identity()")
    _assert(_at > 0, "硬门 main() 里真的调了第 31 条（函数在而不调＝这条门从没跑过）")
    _assert("bad += 1" in gate_src[_at:_at + 1600],
            "第 31 条报出来的问题要并进不合格计数（只 print 不改退出码的门等于公告栏）")
    _assert("::error::标签正文身份门：" in gate_src[_at:_at + 1600],
            "云端 annotation 要能直接照做：写明缺的是身份门/在案登记，不是「重跑一次」")
    _assert_eq(gate.TAB_ID_SOURCE, "b2bcollect_build/cdp_base.py",
               "身份门的真源文件名要说死：换到别处就等于又开一份实现")

    # ── 2) 登记表自己也要能被审：登记的每一份源码必须在视野里，登记的每一档门必须有夹具 ──
    for _rel, _row in gate.TAB_BODY_EXITS.items():
        _assert(any(str(k).endswith(_rel) for k in _rs),
                f"{_rel} 在第 31 条的视野里（登记表挂着它、门脚本却读不到它＝这一条对它失明）")
        for _fn, _g in _row.items():
            _assert(_g in gate.TAB_ID_GATES,
                    f"{_rel}::{_fn} 登记的门 {_g} 不是认可的那三档之一（表里写一个不存在的手势，"
                    "等于给这条出口发了张空白通行证）")
    _declared = {g for _r in gate.TAB_BODY_EXITS.values() for g in _r.values()}
    _covered = {g for _l, _f, _t, _w in gate.TAB_ID_CASES for g in _t.values()}
    _assert_eq(sorted(_declared - _covered), [],
               "登记表用到的每一档门都要有 31c 夹具对拍过（没对拍过的门型＝从没验过的门）")

    # ── 3) 今天的真树：这一条门在交付链上一次都不许响（夹具全绿不代表今天也绿）──
    _today = gate.check_tab_body_identity()
    _assert_eq(_today, [], f"第 31 条今天必须真绿（实际={_today[:2]}）")

    # ── 4) 十处摘门逐条必须响。锚点不在源码里＝夹具过期，这一格不许空跑 ──
    def _mutate(fname_end, old, new, need, label):
        keys = [k for k in _rs if str(k).endswith(fname_end)]
        _assert_eq(len(keys), 1, f"{label}：视野里 {fname_end} 应该有且只有一份（实得 {len(keys)}）")
        if len(keys) != 1:
            return
        key = keys[0]
        _assert(old in _rs[key], f"{label}：夹具锚点已经不在源码里（真写法改了而夹具没跟着改，"
                                 "这一格就变成空跑——报绿的是夹具而不是代码）")
        if old not in _rs[key]:
            return
        files = dict(_rs)
        files[key] = _rs[key].replace(old, new, 1)
        _assert(files[key] != _rs[key], f"{label}：替换没产生任何差别（这一格喂进去的是原件）")
        got = [p for p in gate.check_tab_body_identity(files=files) if p not in _today]
        _assert(got, f"{label}：把已修好的门退回修前原样，第 31 条却没响——判据在这一形上有盲区")
        if got:
            _assert(need in got[0], f"{label}：响的话要念得出是哪一处（要含「{need}」，"
                                    f"实际={got[0][:170]}）")

    # ①②③④ 四处「把调用点的那道门摘掉」——四个站各有各的摘法，全是本轮真实修过的形状
    _mutate("gys_crawler.py",
            '                if deep:\n                    body, why = self._tab_body_for'
            '(url, body=rendered)',
            '                if False:\n                    body, why = "", ""',
            "源码里却已经找不到", "gys 深链那道门摘掉（退回「_goto 只断域名」）")
    _mutate("mic_crawler.py",
            "            ok, why = self._tab_page_is(alt, token=user, same_host=False)",
            '            ok, why = True, ""',
            "源码里却已经找不到", "mic 英文兜底摘门（退回「子域是猜的却谁都不问」）")
    _mutate("mic_crawler.py", "        ok, why = self._tab_page_is(url, token=user)",
            '        ok, why = True, ""',
            "源码里却已经找不到", "mic 联系页摘门（退回「只要不是 errordocs 就交出去」）")
    _mutate("maimaib2b_crawler.py",
            "                body, why = self._tab_body_for(url, body=html)",
            '                body, why = html, ""',
            "源码里却已经找不到", "麦麦摘门（正文经元组转手直接交出：钉「污点传播只认 Call」那个盲区）")
    # ⑤ 表写错：深链那一档登记成域名级门
    _mutate("cdp_base.py", "    def _iter_keyword(self, kw):",
            "    def _iter_keyword(self, kw, rec=None):",
            "只登记了域名级", "列表页函数开始拿「这一家」的把手（域名级门就不够了）")
    # ⑥ 新增一条通路却没人写过门
    _mutate("mic_crawler.py", "    def _fallback_en(self, user, host, rec):",
            "    def _another_tab_exit(self, url, rec):\n        html = self._html()\n"
            "        self._parse_contact(html, rec)\n\n"
            "    def _fallback_en(self, user, host, rec):",
            "不在 TAB_BODY_EXITS 登记里", "全交付链多出一条没人管的出口")
    # ⑦ 门的真源被改名/搬走
    _mutate("cdp_base.py", "    def _tab_page_is(self, want_url",
            "    def _tab_page_is_v2(self, want_url",
            "身份门的真源被删了", "门被改名（各站调用点会一起崩，但门要先自己发现）")
    # ⑧ 登记过期：那条腿不再读标签了，表里还占着一个名次
    _mutate("gys_crawler.py", "            rendered = self._html()", '            rendered = ""',
            "这一行登记作废", "浏览器那条腿整个不再读标签正文（过期登记要响，"
                             "否则下一条同类缺陷会顶用它的名次）")
    # ⑨ 门被掏空：地址语义不再拦跨主机——31d 的行为夹具必须拦住
    _mutate("cdp_base.py",
            '        if key_cur[0] != key_want[0] and (token == "" or same_host):',
            "        if False:",
            "31d", "门还在、里面那一步被掏空（只查在不在＝门可以随便长个样子骗过静态判据）")
    # ⑩ `_tab_body_for` 自己不过身份门
    _mutate("cdp_base.py",
            "        ok, why = self._tab_page_is(want_url, token=token, same_host=same_host)",
            '        ok, why = True, ""',
            "源码里却已经找不到", "门自己把里面那道问地址的步摘掉（登记与源码分叉）")

    # ── 5) 空视野不许绿（与第 27/30 条同一句口径）──
    _assert(any("一份源码都没读到" in p for p in gate.check_tab_body_identity(files={})),
            "视野为空时报的是「这句话没有含义」而不是合格")
    _tmpP = Path(tempfile.mkdtemp(prefix="ap_empty_scope_"))
    try:
        with _patch(gate, "ROOT", _tmpP):
            _got = gate.check_tab_body_identity()
        _assert(any("第 31 条" in p for p in _got),
                f"三个 *_build 目录都不在位时必须响（实际={_got[:1]}）")
    finally:
        shutil.rmtree(_tmpP, ignore_errors=True)
    _got = gate.check_tab_body_identity(files={_REPO / "b2bcollect_build/zz_broken.py": "def f(:\n"})
    _assert(any("解析不过" in p for p in _got),
            f"语法坏掉的运行时代码要响成一条缺陷（实际={_got[:1]}）")


def test_nav_evidence_gate():
    print("\n═══ AQ. 导航证据与环境开关：31e~31g 逐处退回修前原样（15 处必须响）═══")
    # 由来（2026-11-14 实机马可波罗「借浏览器标签把 JS 质询壳跑完」那条腿 0/3）：拦住我们的
    # 不是站点，是共享 Chrome 的 HTTPS-First 自动升级——`Page.navigate` **不抛异常**，把 Chrome
    # 的原话放在响应的 `errorText` 里，页面落在 `chrome-error://chromewebdata/`。旧写法把整份
    # 响应丢掉了，于是「我们自己的浏览器没把这一跳发出去」在下游一律长成「站点不给过」：
    # 方向整个说反，照着那句话去查的人会去改站点，而该改的是启动参数。
    # 这一节审两件事，缺一件都只是把问题挪了个地方：
    #   ① 这句话**还有没有必要说**（31g）：那枚 `--disable-features=HttpsUpgrades,…` 是不是
    #      真进了启动参数列表，而且三条拉起浏览器的路径（b2bcollect / 1688 / riskbird）带的是
    #      **同一份**；被明确否决的 `--ignore-certificate-errors` 不许以参数形态回来；
    #   ② 这句话**有没有证据**（31e/31f）：导航响应、错误页判据、结论句口径，任何一处退回
    #      修前原样都必须响。
    # 与 AP 同一句话：这一族的判据是边修边长出来的，不喂「退回修前字节」就等于没验。
    gate = _manifest_gate()
    _rs = gate._runtime_reason_sources()
    _today = gate.check_tab_body_identity(files=dict(_rs))
    _assert_eq(_today, [], f"第 31 条（含 31e/31f/31g）今天必须真绿（实际={_today[:2]}）")

    def _m(fname_end, old, new, need, label):
        """把一处已修好的写法退回修前原样：第 31 条必须响，而且要念得出是哪一处。

        这里问的是「那一条在不在」而不是「第一条是不是它」：31g 的按处判据与按覆盖面判据
        会对同一个改动一起响（少了开关既是这一处的问题，也让这份文件在覆盖面那节失格），
        把它们排成一个顺序再断言第一条，等于把「多重报告」这条口径自己钉死成单重。
        """
        keys = [k for k in _rs if str(k).endswith(fname_end)]
        _assert_eq(len(keys), 1, f"{label}：视野里 {fname_end} 应该有且只有一份（实得 {len(keys)}）")
        if len(keys) != 1:
            return
        key = keys[0]
        _assert(old in _rs[key], f"{label}：夹具锚点已经不在源码里（真写法改了而夹具没跟着改，"
                                 "这一格就变成空跑——报绿的是夹具而不是代码）")
        if old not in _rs[key]:
            return
        files = dict(_rs)
        files[key] = _rs[key].replace(old, new, 1)
        _assert(files[key] != _rs[key], f"{label}：替换没产生任何差别（这一格喂进去的是原件）")
        got = [p for p in gate.check_tab_body_identity(files=files) if p not in _today]
        _assert(got, f"{label}：把已修好的写法退回修前原样，第 31 条却没响——判据在这一形上有盲区")
        _assert(any(need in p for p in got),
                f"{label}：响的话要念得出是哪一处（要含「{need}」，实际={got[:2]}）")

    # ── 1) 31f 结构证据：三处各退一步，每一处都必须响 ──
    _m("cdp_base.py",
       '            err = str(((resp or {}).get("result") or {}).get("errorText") or "").strip()',
       '            err = ""',
       "源码里没有 errorText",
       "31f：导航响应里那句 Chrome 原话又被丢掉了（「这一跳被浏览器拦下」重新变成没人留痕）")
    _m("cdp_base.py",
       '        if not any(m in cur for m in CHROME_ERROR_URL_MARKS):\n'
       '            return ""                      # 先按地址筛掉，免得每页多读一次整页 HTML',
       '        if "chrome-error://" not in cur:\n            return ""',
       "CHROME_ERROR_URL_MARKS",
       "31f：错误页判据被就地重抄一份字面量（将来加一档错误页，把手先聋掉）")
    _m("cdp_base.py",
       '        self._log("   ⚠ 标签里那页已经不是质询壳了，这条腿却还是帮不上',
       '        self._log("   ⚠ 挑战跑完了，这条腿还是帮不上',
       "跑完了",
       "31f-2：结论句又把推测当观察念给用户（那一跳根本没发出去时，这句话把方向指反）")

    # ── 2) 31e 行为：门还站着，但它已经不念 Chrome 的原话 ──
    _m("cdp_base.py",
       "            return False, (tab_load_failed_line(cur, self._last_nav_error) or",
       '            return False, ("" or',
       "31e 归因不符",
       "31e：地址语义没坏、门也在，可「这一跳没加载出来」六档归因重新退回那句「标签不在本站」")

    # ── 3) 镜像闭包与实现分家：实现新拉一个名字，判据要报成一条缺陷而不是一栈 ──
    _m("cdp_base.py",
       "        key_cur = self._page_key(cur)",
       "        key_cur = self._page_key(cur) or _TAB_ID_NEVER_DEFINED",
       "镜像取不到的名字",
       "31d：身份门多了一个模块级依赖，镜像（按依赖闭包从真源抠）补不出来时必须说得出方向")

    # ── 4) 31g 环境侧：那枚开关的每一格 ──
    _m("shared_chrome.py",
       'NO_HTTPS_UPGRADE_FEATURES = "HttpsUpgrades,HttpsFirstModeV2,HttpsFirstBalancedMode"',
       'NO_HTTPS_UPGRADE_FEATURES = "HttpsUpgrades"',
       "少了 ['HttpsFirstModeV2', 'HttpsFirstBalancedMode']",
       "31g-1：三档升级路径只关一档（症状与完全没关一模一样，而那枚开关看起来是带上了的）")
    _m("shared_chrome.py",
       'CHROME_NO_UPGRADE_FLAGS = (f"--disable-features={NO_HTTPS_UPGRADE_FEATURES}",)',
       'CHROME_NO_UPGRADE_FLAGS = ("--disable-features=HttpsUpgrades,HttpsFirstModeV2,'
       'HttpsFirstBalancedMode",)',
       "没读 NO_HTTPS_UPGRADE_FEATURES",
       "31g-2：启动参数就地重抄字面量（将来加一档只补到一侧，两份从此分叉）")
    _m("shared_chrome.py", "        *CHROME_NO_UPGRADE_FLAGS,\n", "",
       "启动参数列表里没有",
       "31g-3：常量在、没人用它（这一条门整个作废，浏览器照旧把明文 http 升级后拦掉）")
    _m("shared_chrome.py",
       "        if missing_no_upgrade_flag() and not _flag_warned:",
       "        if not _flag_warned:",
       "没问 missing_no_upgrade_flag()",
       "31g-4：复用一个没带开关的旧实例时再也没人提醒（用户看到的仍是「这个站采不到」）")
    _m("shared_chrome.py",
       '            log("⚠ 共享浏览器是先前就开着的，没带「不强制升级 https」的开关：明文 http 的"\n'
       '                "商铺页/详情页会被浏览器拦在加载之前（不是站点在拦）。退出本工具再重新打开"\n'
       '                "即可自动带上，之后无需再做别的")',
       "            _flag_warned.append(2)",
       "问到了却没说",
       "31g-4：调用了把手却把那句话删了（只给自己记账，不给用户交代）")
    _m("shared_chrome.py",
       "        *CHROME_NO_UPGRADE_FLAGS,\n",
       '        *CHROME_NO_UPGRADE_FLAGS,\n        "--ignore-certificate-errors",\n',
       "当参数交出去了",
       "31g-6：被明确否决的那枚开关回来了（拿 1688／riskbird 登录态的中间人防护换一次取页）")
    _m("ali1688_crawler.py",
       'NO_HTTPS_UPGRADE_FEATURES = "HttpsUpgrades,HttpsFirstModeV2,HttpsFirstBalancedMode"',
       'NO_HTTPS_UPGRADE_FEATURES = "HttpsUpgrades,HttpsFirstModeV2"',
       "与真源不再逐字节相同",
       "31g-5：1688 那份内联副本少一档 feature（同机两个 Chrome 只差一枚开关的差分，"
            "这回差在两条路径之间）")
    _m("dual_session.py", "        *CHROME_NO_UPGRADE_FLAGS,\n", "",
       "启动参数列表里没有",
       "31g-3/5：riskbird 那份副本抄来了却没进自己的启动列表——「同一个环境开关只在一条路径"
            "上生效」正是这一族事故的原话")

    # ── 5) 覆盖面：新冒出一条没人管的浏览器路径 / 过期豁免 ──
    files = dict(_rs)
    files[_REPO / "b2bcollect_build/zz_new_browser.py"] = (
        'import subprocess\n'
        'def launch(profile, port):\n'
        '    cmd = ["chrome", f"--remote-debugging-port={port}", f"--user-data-dir={profile}"]\n'
        '    subprocess.Popen(cmd)\n')
    got = [p for p in gate.check_tab_body_identity(files=files) if p not in _today]
    _assert(any("也不在豁免表里" in p for p in got),
            f"31g-7：多出一条配了 Chrome 却没带开关、也没登记的路径时必须响（实际={got[:2]}）")
    _assert(any("zz_new_browser" in p for p in got),
            "31g-7：那句话要念得出是哪一份文件（只说「有一条漏了」等于把排查推回读源码）")
    _m("qizhidao_crawler.py",
       'opts.add_argument(f"--user-data-dir={temp_profile}")',
       'opts.add_argument(f"--profile-dir-clone={temp_profile}")',
       "这一行作废",
       "31g-7：豁免表里那一行对应的启动点已经没了，还留着它——下一条同类漏网会顶着它的名次"
       "被当成合法的")

    # ── 6) 登记表自己：每一项都要有理由，视野断了不许绿 ──
    _assert(any(str(k).endswith(gate.TAB_ENV_SOURCE) for k in _rs),
            f"{gate.TAB_ENV_SOURCE} 在第 31 条的视野里（31g 的对照面读不到＝这一节等于没跑）")
    for _rel in gate.TAB_ENV_MIRRORS:
        _assert(any(str(k).endswith(_rel) for k in _rs),
                f"{_rel} 登记为内联副本，却在视野里读不到：31g-5 这一条被静默跳过")
    for _rel, _why in gate.TAB_ENV_EXEMPT.items():
        _assert(len(str(_why)) >= 12,
                f"{_rel} 的豁免理由太短（{_why!r}）：这一张表的唯一用处就是逼着写下一个理由，"
                "空转的理由＝没有登记")
    _assert(not any(gate.TAB_ENV_FORBIDDEN in v
                    for _rel, _why in gate.TAB_ENV_EXEMPT.items() for v in (_why,)),
            "豁免理由里也不许出现那枚被否决的开关（理由讲成处方，下一条就会照抄）")
    _assert_eq(gate.TAB_ENV_FEATURES,
               ("HttpsUpgrades", "HttpsFirstModeV2", "HttpsFirstBalancedMode"),
               "三档升级路径的名单要说死：少一档就是 31g-1 从此不响")
    _assert(any("一份源码都没读到" in p for p in gate.check_tab_body_identity(files={})),
            "视野为空时报的是「这句话没有含义」而不是合格")


def test_no_contact_accountability_gate():
    print("\n═══ AR. 无联系方式分档／砍单记名／标签归还：第 32 条逐处退回修前原样（必须响）═══")
    # 由来（2026-11-16 第九、十轮实机收报）：三本账的共同形状是「在内存里累加、从不外读」——
    #   ① 四项全空一律被归给「先修取号通路」，而世界工厂网那一页 `联系电话` 明写 `-`（站方
    #      自己没登）、马可波罗那一页要「联系客服登记」（站方藏着）：说词把人支使去改本来
    #      正确的解析代码，改一次就引入一批假数据；
    #   ② 三个 ❌ 全站都只说「该复核判定口径」，可复核的人手上没有名单——处方不可执行；
    #   ③ 六站把 close_browser 覆写成空壳，降级取页借来的标签一路握到收工之后，用户的共享
    #      Chrome 里挂着枚没人认领的商铺页，而日志一句没说。
    # 这一节只审一件事：**判据是不是真的在管事**。第 32 条是边修边长出来的（32a~32g 七问），
    # 不喂「退回修前的字节」就等于没验——AP／AQ 两次都是这样把判据自己的盲区逼出来的：
    # 子串判据会被注释与 docstring 哄成绿、方法名会替空壳说话、只按函数名登记会被改名绕过。
    gate = _manifest_gate()
    _rs = gate._runtime_reason_sources()
    _tx = {rel: (_REPO / rel).read_text(encoding="utf-8", errors="replace")
           for rel in gate.NC_READERS}
    _today = gate.check_no_contact_accountability(dict(_rs), dict(_tx))
    _assert_eq(_today, [], f"第 32 条今天必须真绿（实际={_today[:2]}）")

    def _m(fname_end, old, new, need, label, cnt=1):
        """把一处已修好的写法退回修前原样：第 32 条必须响，而且要念得出是哪一处。

        两条通道**哪里有锚点就改哪里**（而不是二选一）：两份镜像同时在 files 与 texts
        里——32f 读的是 texts，只改 files 时这一格会静默不响，报绿的是夹具而不是代码。
        渲染层（debug/ 里的体检器）只在 texts 里：只喂 files 同样不响。
        判据自己有两条读法，夹具就必须两条都喂。"""
        files, texts, done = dict(_rs), dict(_tx), 0
        for k in [x for x in _rs if str(x).endswith(fname_end)]:
            if old in _rs[k]:
                files[k] = _rs[k].replace(old, new, cnt)
                done += 1
        for k in [x for x in _tx if x.endswith(fname_end)]:
            if old in _tx[k]:
                texts[k] = _tx[k].replace(old, new, cnt)
                done += 1
        _assert(done, f"{label}：{fname_end} 在两条通道的哪一份里都对不上这个锚点"
                      "（真写法改了而夹具还在替旧代码作证，这一格就变成空跑）")
        if not done:
            return
        _assert(files != _rs or texts != _tx, f"{label}：替换没产生任何差别（喂进去的是原件）")
        got = [p for p in gate.check_no_contact_accountability(files, texts) if p not in _today]
        _assert(got, f"{label}：把已修好的写法退回修前原样，第 32 条却没响——判据在这一形上有盲区")
        _assert(any(need in p for p in got),
                f"{label}：响的话要念得出是哪一处（要含「{need}」，实际={got[:2]}）")

    def _syn(src, want_fire, label):
        """合成形状：只拦「说谎的那一种」，不拦「怎么说谎的形状」——正反两面都要喂。"""
        files = dict(_rs)
        files[_REPO / "b2bcollect_build/zz_fixture_ar_crawler.py"] = src
        got = [p for p in gate.check_no_contact_accountability(files, dict(_tx))
               if "zz_fixture_ar" in p]
        _assert(bool(got) is want_fire,
                f"{label}：期望{'响' if want_fire else '不响'}，实际={got[:1]}")

    # ── 1) 32a 证据：详情取页不留正文，分档就只能说「没证据」，谁的责任立刻分不开 ──
    _m("aipage_crawler.py", "self._keep_detail(", "self._keep_detail_x(",
       "取了详情页却没留证据", "32a：把 aipage 那一路的证据摘掉")
    # mic 有两条取页腿（裸 HTTP `_fetch_contact_http` + 浏览器 `_fetch_contact_page`），
    # 32a 沿调用闭包找证据，所以修前原样是**两条一起抹掉**（只抹一条时闭包另一条还留着）。
    # 这一格同下面那条「只抹一条不许响」一起把口径钉住：32a 判的是「这一路到底能不能
    # 分档」，不是「每一取页腿都得留正文」——后者会把正当理由判成漏（mic 的 `_fallback_en`
    # 取的是英文子域页，拿它当证据会把「我们没看中文栏」误报成「这一页没中文联系方式栏」）。
    _m("mic_crawler.py", "self._keep_detail(", "self._keep_detail_x(",
       "取了详情页却没留证据", "32a：mic 两条取页腿一起退回不留证据", -1)

    # 口径边界也要作证：只抹一条腿时这一条**不许响**（不是盲区，是本条定的范围）。
    _half = dict(_rs)
    for _k in [x for x in _rs if str(x).endswith("mic_crawler.py")]:
        _half[_k] = _rs[_k].replace("            self._keep_detail(r.text)", "            pass", 1)
    _assert(_half != _rs, "32a 的边界夹具连 mic 那一格 HTTP 腿都没找到"
                  "（真写法改了而夹具还在替旧代码作证）")
    _assert_eq([p for p in gate.check_no_contact_accountability(_half, dict(_tx))
                if "mic_crawler" in p and "取了详情页" in p], [],
               "只抹一条腿时不许响：这是 32a 定的口径边界（按闭包判能不能分档），"
               "而不是「每一取页腿都得留正文」；谁要把判据收紧到逐腿，就得先回答英文页那一形")

    # ── 2) 32b 记名：只有一本计数时，「该复核判定口径」是一句无法执行的处方 ──
    _m("cdp_base.py", 'self._note_early_skip(kw, e.get("公司名称", ""))',
       "self._note_early_skip(kw)", "没把公司名传进去", "32b：早过滤那一刀又不记名")
    _m("cdp_base.py", "self._cut_sample(self.kw_early_names, kw, name)", "pass",
       "没把名单写进", "32b：调用点传了名字、把手本体半路丢掉")

    # ── 3) 32c 外读：账只累加不外读，正是通路账那一族的原病 ──
    _m("cdp_base.py", '"nc_note": nc_kinds_text(', '"nc_note_x": nc_kinds_text(',
       "没透出 `nc_note`", "32c：stats() 里把人话那一格改个名（界面与体检器一起取不到）")
    _m("cdp_base.py", "｜无联系方式分档：", "｜无联系方式合计：",
       "没念「无联系方式分档」", "32c：收尾那一行退回只报总数")
    # 红牌这一行退回「只报总数」那一形：把读取那两行整体抹掉，但把旧注释留在原地——
    # 注释里句句都写着 nc_note，拿子串判据时这一格能静默过关（而 AST 里没有注释）。
    _m("channel_health.py",
       '''    if str(s.get("nc_note") or "").strip():
        bits.append(f"无联系方式分档：{s['nc_note']}")
''', "",
       "没把分档账读出去", "32c：GUI 红牌那一行不再读这本账")
    _m("platform_live_check.py", "nc_kinds_text", "nc_say_it_my_way",
       "没把分档账读出去", "32c：体检报告自己拼说词（import 与调用点一起摘才算不读）", -1)

    # ── 4) 32d/32e 唯一把手与归还本体 ──
    _syn("NC_KIND_ORDER = ('miss',)\nclass A:\n    def crawl_detail(self, s):\n"
         "        return self._keep_detail(self._html())\n", True,
         "32d：第二份分档标签表出现了（两种说词时「谁的责任」没唯一答案）")
    _m("cdp_base.py", "    def release_tab(self, why=\"\"):", "    def release_tab_x(self, why=\"\"):",
       "没有 `release_tab` 把手", "32e：归还把手被改名（子串判据会被后缀哄成绿）")
    _m("makepolo_crawler.py", '        self.release_tab("收工")\n        return None\n',
       "        return None\n", "是空壳", "32e：close_browser 又退成只有一行 return 的空壳")
    _m("makepolo_crawler.py", 'self.release_tab("收工")', "self._borrowed_tab = False",
       "没调用任何归还动作", "32e：close_browser 里改成只置一个标志位")

    # ── 5) 32f 两份镜像：1688 是独立包不能 import，只能内联一份拷贝 ──
    _m("ali1688_crawler.py", "站方藏着（点击查看", "站方藏着呢（点击查看",
       "开始分叉", "32f：两份镜像只改一个字（1688 那一包不会自己报分叉）")
    _m("ali1688_crawler.py", "# ── [NC-BLOCK-END]", "# ── [NC-BLOCK-EDN]",
       "取不出分档判据那一段", "32f：镜像标记被改名（对拍两边都抓不到，必须响而不是默默相等）")

    # ── 6) 32g 关失败不许被抹平：本轮实机抓到 18 处，全靠这一问自己看得见 ──
    _m("shared_chrome.py",
       '    except Exception as e:\n        return f"关闭请求没送达（{type(e).__name__}）：'
       '{first_line(e, 80)}"',
       "    except Exception:\n        pass",
       "里 except 只有 pass", "32g：最底层重新吞掉失败（上层每一句「关失败必须开口」都变死分支）")
    _m("site_adapters.py",
       '            except Exception as e:\n'
       '                self._log(f"⚠ {self.PLATFORM_NAME} 结束 chromedriver 会话没成功："\n'
       '                          f"{short_reason(first_line(e, 80), 60)}"\n'
       '                          f"——若下一轮打不开浏览器，请手动关掉 Chrome 窗口再试")\n',
       "            except Exception:\n                pass\n",
       "里只有 pass", "32g：适配器收工那一腿又静默（会话没断开直接影响下一轮附加）")
    _m("ali1688_crawler.py",
       '                except Exception as e:\n'
       '                    # 不能吞：quit 没成功等于会话还挂着，下一轮 _attach 就可能\n'
       '                    # 「session not created」；只说一句失败在哪，比事后猜强（第 32g 条）\n'
       '                    self._log(f"⚠ 收工结束浏览器会话没成功（Ali1688Crawler）："\n'
       '                              f"{short_reason(first_line(e, 80), 60)}"\n'
       '                              f"——若下次打不开浏览器，请手动关掉所有 Chrome 窗口再试")\n',
       "                except Exception:\n                    pass\n",
       "里只有 pass", "32g：1688 独立包的 quit 兜底又吞回去")

    # ── 7) 形状的正反两面：误伤一次，下一个真漏的人就会被当成噪音放过去 ──
    _syn("class A:\n    def crawl_detail(self, s):\n        \"\"\"子类实现。\"\"\"\n"
         "        raise NotImplementedError\n", False, "32a：基类模板桩（它一页都不取）")
    _syn("class A:\n    def crawl_detail(self, s):\n        return self._parse(self._html())\n",
         True, "32a：具体实现取了页却没留证据")
    _syn("class A:\n    def crawl_detail(self, s):\n        return self._one()\n"
         "    def _one(self):\n        return self._keep_detail(self._html())\n", False,
         "32a：证据在它调下去的第二条腿里（沿调用闭包找）")
    _syn('class A:\n    NO_DETAIL_EVIDENCE = "详情不取页"\n'
         "    def crawl_detail(self, s):\n        return {}\n", False,
         "32a：确实没有单店正文可判，但写了理由（诚实声明也是合格答案）")
    _syn("class A:\n    def tidy_up(self):\n        try:\n            self._close_own_tab()\n"
         "        except Exception:\n            pass\n", True,
         "32g：归还动作换了名字（只按函数名登记表发判时这一形就能逃掉）")
    _syn("class A:\n    def _nav(self):\n        try:\n            self.driver.close()\n"
         "        except Exception:\n            pass\n", True, "32g：Selenium 写法 driver.close() 被吞")
    _syn("class A:\n    def _read_cfg(self):\n        try:\n            f = open('x')\n"
         "            f.close()\n        except Exception:\n            pass\n", False,
         "32g：文件的 f.close() 不是关标签（误伤就会把真漏当噪音）")
    _syn("class A:\n    def _nav(self):\n        try:\n            self.driver.get('u')\n"
         "        except Exception:\n            pass\n        finally:\n"
         "            self._close_own_tab()\n", False,
         "32g：归还放在 finally、except 只包导航（那一格不负责关标签）")

    # ── 8) 视野断了不许绿 ──
    _assert(any("一份源码都没读到" in p
                for p in gate.check_no_contact_accountability({}, {})),
            "32：视野为空时报的是「这句话没有含义」而不是合格")
    _assert(any(rel in gate.NC_READERS for rel in gate.NC_MIRROR_FILES)
            and len(gate.NC_READERS) == 4,
            "32c 的四处读家登记表要说死（少一处渲染＝那一处又只看得到总数）")
    # 分档定序取的是**源码里那一段常量**而不是 import 进来的值：门禁进程里交付链模块
    # 不一定导得动，而这一格要防的正是「文件被改坏」，不是「运行期运气」。
    _base_src = [v for k, v in _rs.items() if str(k).endswith("cdp_base.py")]
    _assert(bool(_base_src), "分档定序这一格连 cdp_base.py 都没读到：视野断了不许假绿")
    if not _base_src:
        return
    _ord_tree = ast.parse(_base_src[0])
    _order = None
    _labvals = None
    for _n in _ord_tree.body:
        if not (isinstance(_n, ast.Assign) and len(_n.targets) == 1):
            continue
        _nm = getattr(_n.targets[0], "id", "")
        if _nm == "NC_KIND_ORDER":
            _order = tuple(getattr(_e, "value", None) for _e in _n.value.elts)
        elif _nm == "NC_KIND_LABEL":
            _labvals = {getattr(_k, "value", None): _v.value
                        for _k, _v in zip(_n.value.keys, _n.value.values)
                        if isinstance(_v, ast.Constant) and isinstance(_v.value, str)}
    _assert(_order is not None and _labvals is not None,
            "分档定序/分档说词这两段常量在 cdp_base 顶层取不到：视野断了不许假绿")
    if _order is None or _labvals is None:
        return
    # 不钉「一共几档」而钉性质：钉数字的话，新增一档的人会把这格和源码一起改掉（那等于
    # 没有门）；真正会出事故的是两件事：定了档却说不出人话，以及「先认自己的锅」这一序被排错。
    _assert_eq(set(_order), set(_labvals),
               "定序表与说词表必须是同一批档位（多出来的那一档渲染时只能念档位本名，"
               "少掉的那一档会在账上静默失踪）")
    _assert(len(set(_order)) == len(_order), "定序表里同一档出现两次：账会被重复念")
    _assert(_order[:2] == ("miss", "unknown"),
            "分档定序要说死：把 miss 挪出第一位，「先认我们自己的锅」这条就作废")
    _station = [k for k in _order if k in ("hidden", "blank", "no_slot")]
    _assert(len(_station) == 3 and "no_page" in _order
            and _order.index("no_page") < min(_order.index(k) for k in _station),
            "设计边界那一档（本站详情不取页）必须排在站方三档之前，否则读的人第一眼"
            "看到的又是「不是缺陷」（实际序=" + str(_order) + "）")
    _assert("no_page" in _labvals and "unknown" in _labvals
            and "_keep_detail" in _labvals["unknown"]
            and "_keep_detail" not in _labvals["no_page"],
            "两档「拿不到证据」的说词不许合成一句：unknown 要接把手、no_page 不许接把手"
            "（把已声明详情不取页的站说成「忘了接」，开的是一张执行不了的假处方）")


def test_listing_accountability_gate():
    print("\n═══ AS. 列表条目账：第 33 条逐处退回修前原样（必须响）═══")
    # 由来（2026-11-16 第十二、十三轮实机 中国制造网「窗帘导轨」）：某一词零净新增时，归因句
    # 只会说「站方回的货有、该复核相关性口径」。真去查页面，站方自报「共找到 1 个」——约束项
    # 在货源而不在本机闸门，那句话把人支使去改一个改了也没用的东西。要分得开「只回 1 条」与
    # 「回 30 条全被砍」，账上必须有「这一词站方回了几条」，而这一笔以前**只以全局 stat_listed
    # 存在**、且各站自己 `+=`（漏写一路＝那一词的条目账永远是空的）。
    # 第 33 条管六问：33a 写只许住在一个把手、把手必须三本账一起落、两份镜像同行为、报告钩子
    # 不许顺手记账、不许拿 getattr 的默认值当证据；33b 按词账本按轮起账、四处渲染真取用；
    # 33c 归因句真读它并且四档各指一种责任方。
    # 这一节与 AR 节同一立场：**只审判据是不是真的在管事**。每一处都退回修前那几个字节，
    # 门不响就是判据在这一形上闭着眼（AP／AQ／AR 三节都是这样把判据自己的盲区逼出来的）。
    gate = _manifest_gate()
    _rs = gate._runtime_reason_sources()
    _today = gate.check_listing_accountability(dict(_rs))
    _assert_eq(_today, [], f"第 33 条今天必须真绿（实际={_today[:2]}）")

    def _m(fname_end, old, new, need, label, cnt=1):
        """把一处已修好的写法退回修前原样：第 33 条必须响，而且要念得出是哪一处。

        只喂 `files` 一条通道不是偷工：`check_listing_accountability` 的视野就是第 30 条那一份
        `_runtime_reason_sources()`（已含 debug/ 里的体检器），判据没有第二种读法，夹具就不该
        多一条腿——腿一多就会长出「改的那条通道其实不生效」的假响（AR 节为此吃过一次）。"""
        files, done = dict(_rs), 0
        for k in [x for x in _rs if str(x).endswith(fname_end)]:
            if old in _rs[k]:
                files[k] = _rs[k].replace(old, new, cnt)
                done += 1
        _assert(done, f"{label}：{fname_end} 里对不上这个锚点（真写法改了而夹具还在替旧代码"
                      "作证，这一格就变成空跑）")
        if not done:
            return
        _assert(files != _rs, f"{label}：替换没产生任何差别（喂进去的是原件）")
        got = [p for p in gate.check_listing_accountability(files) if p not in _today]
        _assert(got, f"{label}：把已修好的写法退回修前原样，第 33 条却没响——判据在这一形上有盲区")
        _assert(any(need in p for p in got),
                f"{label}：响的话要念得出是哪一处（要含「{need}」，实际={got[:2]}）")

    def _syn33(src, want_fire, label, need=""):
        """合成形状：只拦「说谎的那一种」，不拦「怎么说谎的形状」——正反两面都要喂。

        响的那一面还要问一句「响的是不是这一格」：33b-2 的措辞是**全链**性质（整条交付链
        没人读这本账），行首根本没有文件名——只按「响里含不含夹具名」筛就会把这一问当成
        不响（本轮就是这么错过一次）。所以默认按文件名算，全链那一格额外点名 `need`。"""
        files = dict(_rs)
        files[_REPO / "b2bcollect_build/zz_fixture_as_crawler.py"] = src
        got = [p for p in gate.check_listing_accountability(files) if p not in _today]
        if not want_fire:
            _assert_eq(got, [], f"{label}：合格形状不许响（误伤一次，下一个真漏的人就会被当噪音）")
            return
        _assert(got, f"{label}：把说谎那一形喂进去，第 33 条却没响")
        _anchor = need or "zz_fixture_as"
        _assert(any(_anchor in p for p in got), f"{label}：响的话要念得出这一格（需含「{_anchor}」）")
        _else = [p for p in got if "zz_fixture_as" not in p and not (need and need in p)]
        _assert_eq(_else, [], f"{label}：新响的每一条都得能算到这一格头上（别处连带响＝这一格在赌运气）")

    # ── 1) 33a 一把刀：逐站各退一次 ──
    # 为什么要一圈都退而不是抽一站：判据是「扫每个函数体里的 `self.stat_listed +=`」，
    # 谁要把扫法改窄（只认 crawl、只认某个目录），只退某一站的那一格仍会响；逐站退过，
    # 改窄的人当场看见有几格哑掉。
    for _f33 in ("cdp_base.py", "b2b168_crawler.py", "gys_crawler.py", "solarbe_crawler.py",
                 "gi_crawler.py", "qihuo11467_crawler.py", "maimaib2b_crawler.py",
                 "makepolo_crawler.py", "aipage_crawler.py",
                 "ali1688_crawler.py"):
        _src33 = next(v for k, v in _rs.items() if str(k).endswith(_f33))
        _old33 = next((l for l in _src33.splitlines() if "self._note_listed(" in l), "")
        _assert(_old33, f"33a：{_f33} 里找不到 `self._note_listed(` 的调用点（这一路何时退过门？）")
        if not _old33:
            continue
        _m(_f33, _old33.strip(), "self.stat_listed += len(entries)", "第二个写点",
           f"33a：{_f33} 退回自记总账（这一词的条目账从此永远是空的）")

    # ── 2) 33a-2/33a-3 把手本体：三本账一起落，两份镜像同行为 ──
    _m("cdp_base.py", "        self.kw_items[key] = self.kw_items.get(key, 0) + n",
       "        pass", "只记总账", "33a-2：把手退回只加全局数（旧各站写法搬进把手）")
    _m("ali1688_crawler.py", "        self.kw_items[key] = self.kw_items.get(key, 0) + n",
       "        pass", "只记总账", "33a-2：1688 那一包的把手同样不许少记一本")
    _m("ali1688_crawler.py", "self.kw_claimed[key] = max(self.kw_claimed.get(key, 0), c)",
       "self.kw_claimed[key] = c", "行为不同",
       "33a-3：只改一边（每页报的都是整个查询的总数，取小时翻页会把它当上一轮的账求和）")
    _m("cdp_base.py", "    def _note_listed(self, kw, count, claimed=None):",
       "    def _note_listed_x(self, kw, count, claimed=None):", "没有 `_note_listed`",
       "33a-3：把手改名——后缀会让「按名字找把手」的判据静默过关")

    # ── 3) 33a-4 报告钩子只许开口，不许记账 ──
    _m("mic_crawler.py", '        i = (html or "").find(LISTED_TOTAL_MARK)',
       '        self.stat_listed += 0\n        i = (html or "").find(LISTED_TOTAL_MARK)',
       "自己动了条目账", "33a-4：MIC 的自报数钩子顺手记一笔（两处写＝两本账）")

    # ── 4) 33a-5 拿默认值当证据：旧 1688 那句「一页都没取到」就是这么变成永久真话的 ──
    _m("ali1688_crawler.py", "if self._page_fail and not self.stat_listed:",
       'if self._page_fail and not getattr(self, "stat_listed", 0):', "兜了默认值",
       "33a-5：收尾那句退回 getattr 兜 0（缺账被念成「这本账是 0」）")

    # ── 5) 33b 按轮起账：上一轮的回货量不该冒充本轮的「货源薄」证据 ──
    _m("cdp_base.py", "        self.kw_items = {}\n        self.kw_claimed = {}",
       "        self.kw_claimed = {}", "却没清 kw_items",
       "33b：三处起账点都少带一本（跨轮脏账）", -1)
    _m("ali1688_crawler.py", "        self.kw_items = {}\n        self.kw_claimed = {}",
       "        self.kw_claimed = {}", "却没清 kw_items",
       "33b：1688 那一包的起账点同样少带一本", -1)
    _m("cdp_base.py", "        self.kw_filtered = {}", "        pass", "整本换掉",
       "33b：锚的清零点被整处删掉（所有按词证据都跳进本轮那句处方）", -1)

    # ── 6) 33b-3 四处渲染真取用：只删一处就能验证「少一处念不到货量」──
    _m("site_adapters.py",
       '"kw_items": dict(self.kw_items), "kw_claimed": dict(self.kw_claimed),', "",
       "真取用", "33b-3：适配器不再把条目账透给看板与体检器")
    _m("platform_live_check.py", 'st.get("kw_items")', 'st.get("kw_items_x")',
       "真取用", "33b-3：验收报告自己取错了键（拿不到那一本只会说「货没记上」）")
    # 口径细化（本轮 `_scan_attrs` 的 reads 那一课）：只把账本当**写入地址**碰一下（`self.kw_items[k] = …`）
    # 不算取用——拿「Load 上下文」当「读」时，渲染层可以靠一行写入哄绿（那一行本来就只会
    # 把上一笔加上去，一句货量也没念）。正向形状由上面两条退修前原样的夹具反面作证。
    _m("ali1688_crawler.py", "            _it, _cl = self.kw_items.get(k), self.kw_claimed.get(k)",
       "            _it, _cl = 0, 0\n            self.kw_items[k] = 0\n            self.kw_claimed[k] = 0",
       "真取用", "33b-3：1688 收尾只把两本账当写入地址碰一下（念出口的还是 0）")

    # ── 7) 33c 归因句：读了吗／分叉了吗／旋钮从哪来／把手还在被叫吗 ──
    _m("cdp_base.py", "        items = self.kw_items.get(kw)", "        items = None",
       "没读 kw_items", "33c：归因句不再看条目账（四档塌回旧那一档）")
    _m("cdp_base.py", "该换的是关键词或搜索索引", "该复核的是相关性口径",
       "再也没人说这几句处方", "33c：两档处方合回一句（只数 return 条目哄不过这一问）")
    _m("cdp_base.py", "if items <= self.KW_THIN_SUPPLY_ITEMS:", "if items <= 3:",
       "KW_THIN_SUPPLY_ITEMS", "33c-3：边界写死在句子里（改常量的人不会通知归因句）")
    _m("cdp_base.py", "    def _kw_zero_reason(self, kw):", "    def _kw_zero_reason_x(self, kw):",
       "没有 `_kw_zero_reason`", "33c-2：把手被改名（上面几问都是「函数不在就返 []」的形状）")
    _m("cdp_base.py", "                    _why = self._kw_zero_reason(kw)",
       '                    _why = ""', "从没有被调用",
       "33c-2：四档写好了却没人念（定义进不了眼睛，调用才能）")

    # ── 8) 形状的正反两面：误伤一次，下一个真漏的人就会被当成噪音放过去 ──
    _syn33("class A:\n    def crawl(self):\n        self.stat_listed += len(self.entries)\n",
           True, "33a：站里第二把刀（总账与分词账分家）")
    _syn33("class A:\n    def crawl(self):\n        self._note_listed(self.kw, len(self.entries))\n",
           False, "33a：走把手的合格写法（不许因为名字里有把手字样就误伤）")
    _syn33("class A:\n    def _note_listed(self, kw, count, claimed=None):\n"
           "        self.stat_listed += int(count or 0)\n", True,
           "33a-2：把手只记总账")
    _syn33("class A:\n    def _note_listed(self, kw, count, claimed=None):\n"
           "        self.stat_listed += int(count or 0)\n"
           "        self.kw_items[kw] = self.kw_items.get(kw, 0) + int(count or 0)\n"
           "        self.kw_claimed[kw] = int(claimed or 0)\n", False,
           "33a-2：三本账一起落（不许把新写的把手一律判成漏）")
    _syn33("class A:\n    def stats(self):\n        self.kw_zz_dead = {}\n"
           "        self.kw_zz_dead['x'] = 1\n        return 1\n", True,
           "33b-2：新加一本按词账却全链没人读（记了不念＝没记）", need="kw_zz_dead")
    _syn33("class A:\n    def stats(self):\n        self.kw_zz_live = {}\n"
           "        self.kw_zz_live['x'] = 1\n        return dict(self.kw_zz_live)\n", False,
           "33b-2：同一本账透给了 stats()（新账不是罪，没人读才是）")
    _syn33("class A:\n    def _kw_zero_reason(self, kw):\n"
           "        return \"零净新增：该换的是关键词或搜索索引\"\n", True,
           "33c：四档合回一句死写（两种相反的局交给同一个动作去改）")
    _syn33("class A:\n    def tail(self):\n        return getattr(self, \"_pf_unknown\", 0)\n", False,
           "33a-5：另一本账用 getattr 兜默认值不归这一条管（误伤就会把真漏当噪音）")

    # ── 9) 视野断了不许绿，登记表空转也不许绿 ──
    _assert(any("一份源码都没读到" in p for p in gate.check_listing_accountability({})),
            "33：视野为空时报的是「这句话没有含义」而不是合格")
    _broken = dict(_rs)
    _broken[next(k for k in _broken if str(k).endswith("cdp_base.py"))] = "def x(:\n    pass\n"
    _assert(any("解析不过" in p for p in gate.check_listing_accountability(_broken)),
            "33：某一份被改坏到解析不过时要按不合格算，而不是那一整份从此没人管")
    # 登记表与视野必须同源：LEDGER_READERS 里那一处如果不在这一条的视野里（文件被搬走），
    # 33b-3 会对它**一个字都不说**——表还在、判据却扫不到，这一格就变成摆设。
    _labels33 = {gate._rel_label(p) for p in _rs}
    _assert_eq([rel for rel in gate.LEDGER_READERS if rel not in _labels33], [],
               "四处读家必须全部在第 33 条的视野里（搬走一处＝那一处静默脱管）")
    _assert_eq(len(gate.LEDGER_READERS), 4, "读家登记表要说死四处（少登记一处＝那一处又只看得"
                                          "到砍单数，而这一条拦的正是「只看砍单数」）")
    # 四句处方登记表自己也要有性质：拿句子与源码对拍时，把两边**一起**改掉就能把 33c 腾空。
    # 这里钉的是「每一句指的必须是不同的责任方」——不是数句子条数。
    _own33 = ("解析", "_note_listed", "关键词", "相关性")
    _hit33 = []
    for _p33 in gate.LISTED_PRESCRIPTIONS:
        _who33 = [t for t in _own33 if t in _p33]
        _assert(len(_who33) == 1,
                f"处方「{_p33}」指到的责任方要唯一（候选={_who33}）：两句指同一方＝有一档被"
                "合回同一顶帽子，登记表也跟着空转")
        _hit33 += _who33
    _assert_eq(sorted(_hit33), sorted(_own33),
               "四句处方与四种责任方必须一一对上（我们的解析／我们缺账／站方货源／相关性阈值）")
    _assert_eq(gate.LISTED_KNOBS, ("KW_THIN_SUPPLY_ITEMS", "SEARCH_INDEX_NOTE"),
               "旋钮登记表要说死：个位数那一档的边界与索引说明都得从类属性取")
    _assert_eq(len(set(gate.LISTED_KW_BUCKETS)), 2,
               "分词账两本要说死（我们解析到的／站方自报的）：只钉一本时，渲染层可以只念一本"
               "而把「卡片改版只认出 1 家」与「站方本就只回 1 条」念成同一句")

    # ── 9) 本条只许一个解析入口（派生事实缓存那一层的性质判据）──
    # 由来：`check_listing_accountability` 旧形状是「每个函数 ast.walk 四次」，85 份源码一次
    # 6.1 秒；本节把整条门跑 40 多轮，整套门禁因此从 8 分钟涨到 10 分钟并把批跑看门狗（600s）
    # 拦断。修法是「一遍扫描 + 按 (文件名, 内容指纹) 缓存派生事实」——加了缓存就必须管它：
    # ① 判据里不许再出现第二处 ast.walk／ast.parse（有第二把刀＝缓存可以被绕过，而绕过那一
    #    条正是上一轮 33b-2／33b-3 空转的形状）；
    # ② 缓存必须按**内容**而不是按文件名生效：上面几十轮里同一批文件名反复换过写法，若旧账
    #    没随内容退干净，真源原样再喂一次就会拿着最后一次的坏写法念绿。
    import inspect as _insp33
    _body33 = "".join(_insp33.getsource(getattr(gate, _fn33)) for _fn33 in (
        "_listed_write_problems", "_listed_handle_problems", "_ledger_cycle_problems",
        "_listed_read_default_problems", "_listed_reader_problems",
        "_listed_zero_reason_problems", "check_listing_accountability"))
    for _bad33 in ("ast.walk(", "ast.parse("):
        _assert(_bad33 not in _body33,
                f"第 33 条的六问里出现了第二处 `{_bad33}`：派生事实缓存可以被绕过（而绕过那一条"
                "就是「判据空转」那一族原病），本条只许从 `_listed_facts` 拿事实")
    _assert("_listed_facts(" in _body33, "本条必须从 `_listed_facts` 取事实")
    _assert_eq(gate.check_listing_accountability(dict(_rs)), [],
               "把全部真源原样再喂一次必须还是绿的：派生事实缓存在这里按内容生效，"
               "任何一处旧账没退干净都会在这一句上露出来")


def test_tree_cache_gate():
    print("\n═══ AT. 取树只一个把手：门禁内部的语法树层（绕过／登记空转／改脏共享树 各必须响）═══")
    # 由来（2026-11-16 frozen 套件超 600s 被批跑看门狗掐断）：第 30/31/32 条每跑一次都把同一批
    # 交付链源码重新 `ast.parse` 一遍，再对每个节点重走一次 `ast.walk`（实测：第 32 条一次调用
    # 展开 390 万棵树呈 O(n²)，第 30 条把同一个作用域重建 14572 次）。修法是在门禁里加一层
    # 公共设施：同一份字节只解析一次（`_file_tree`）、同一个节点只展开一次（`_walk`、
    # `_scope_nodes` 的 memo）。优化后 frozen 从 588.8s 降到 201.7s，且 33 项门禁改前改后
    # stdout 逐字节相同。
    # 但**公共设施没有判据就会被绕过**：下一个加条款的人各 parse 各的，这一层就只盖住了
    # 四分之一（与第 33 条管「条目账只住在一个把手」完全同一个道理）。反过来，谁把一份
    # **会就地改节点**的写法接进缓存，缓存就会替所有人把证据改脏——本仓真实就有这一处：
    # `_code_only_text` 靠 `body.pop(0)` 摘 docstring（第 18 条判的正是「docstring 不许替代码
    # 作证」，它自己反倒改树）。所以这一节两半都要：①绕过与登记空转必须响；②按内容生效、
    # 不改共享树、有上界这三条性质都要有行为夹具，而不是只读一段源码字符串。
    gate = _manifest_gate()
    _src = (_REPO / ".github" / "scripts" / "check_packaging_manifest.py").read_text(encoding="utf-8")

    # 登记表：每一处「不许吃 `_file_tree`」的 `ast.parse` 都要写明理由（与 DEV_PATH_EXEMPT、
    # DIVERGENT_VENDORED 同一族规矩：豁免必须带理由，且必须真对得上——对不上就是空转）。
    _allowed = {
        "got = ast.parse(text, filename=label)": "把手本体：整层只许这一处真的去解字节",
        "tree = ast.parse(text)": "`_code_only_text` 会就地摘 docstring（body.pop），故意吃一份私有树",
        'return ast.parse("")': "`_ast_safe_parse` 的坏语法兜底：交一份空树，不让异常带走整条门",
        # 第 42 条甲：同一份源码要按**两个**语法口径各解一次，3.8 那一次吃不了把手——
        # `_file_tree` 是按本机解释器（今天 3.13）解的，拿它的结论替 3.8 担保恰好就是这一
        # 条要防的事（feature_version=(3,8) 才是「3.8 能不能读」的那个问法）。
        # 同一函数里「后面三道 AST 判据」那一次已经改走 `_file_tree`，不在这张表里。
        "ast.parse(text, filename=str(path), feature_version=PY38_FLOOR)":
            "第 42 条甲：要的是「3.8 能不能解」，把手按本机语法解、口径不对，不许共享",
        'got = _tab_exit_problems("夹具", ast.parse(frag), dict(table))': "内联夹具片段，不是磁盘源码",
        'got = _tab_nav_evidence_problems("夹具", ast.parse(frag))': "内联夹具片段，不是磁盘源码",
        'got = _tab_chrome_env_problems("夹具", frag, ast.parse(frag))': "内联夹具片段，不是磁盘源码",
    }

    def _tree_layer_problems(src, allowed=_allowed, whole=False):
        """只认**代码里的调用**：取树只一个把手，新 parse 点要么改调 `_file_tree`、要么登记理由。

        拿 AST 而不是正则抓行：注释与 docstring 里写一句 `ast.parse(` 不算绕过（本仓第 18 条
        同一口径），而正则恰好会在那种句子上假响一次、逼下一个人把判据改松。

        `whole` 决定要不要查「登记表空转」：那一问的口径是「**整份门禁真源**里每一处登记都还
        对得上」，合成片段里本来就只有几行，拿它去要求六条全在就是误伤（本轮第一跑就红在这一句，
        红得对：判据错了就得修判据，而不是给夹具补上六条假登记）。
        """
        import ast as _A
        try:
            tree = _A.parse(src)
        except SyntaxError as e:
            return [f"这份源码解析不过（第 {e.lineno} 行 {e.msg}）：读不到写法就按不合格算，"
                    "不许当作「没有绕过」"]
        lines = src.splitlines()
        hits = []
        for n in _A.walk(tree):
            if not (isinstance(n, _A.Call) and isinstance(n.func, _A.Attribute)
                    and isinstance(n.func.value, _A.Name) and n.func.value.id == "ast"
                    and n.func.attr in ("parse", "walk")):
                continue
            hits.append((n.lineno, n.func.attr, lines[n.lineno - 1].strip()))
        out = [f"第 {ln} 行又自己 `ast.walk(`：语法树展开只许走 `_walk`，绕过这一层就是第二把刀"
               f"（代价已经量过：单次调用 390 万次展开）：{txt}"
               for ln, kind, txt in hits if kind == "walk"]
        used = set()
        for ln, kind, txt in hits:
            if kind != "parse":
                continue
            if txt in allowed:
                used.add(txt)
                continue
            out.append(f"第 {ln} 行出现未登记的 `ast.parse(`：要进这一层就改调 `_file_tree`，"
                       f"确实不许共享的就在登记表里写清理由：{txt}")
        if whole:
            idle = sorted(set(allowed) - used)
            if idle:
                # 把登记的理由一起念出来：只报一行代码文本时，读的人还得自己去找那是哪个函数
                # （而这一格拦的恰好就是「豁免必须带理由」，理由不在报告里就等于没登记）
                out.append("登记表里这几条 `ast.parse(` 在真源已经找不到了（登记了却没人在用＝这一格"
                           "在空转，下一次真绕过不会被报出来）："
                           + "；".join(f"{k}（登记的理由：{allowed[k]}）" for k in idle))
        return out

    _got26 = _tree_layer_problems(_src, whole=True)
    _assert_eq(_got26, [], f"门禁真源的取树口径必须收干净（实际={_got26[:2]}）")

    # 正向不许误伤：只在注释／docstring 里提到 `ast.parse(` 的写法不算绕过
    _assert_eq(_tree_layer_problems(
        'def f(tree):\n    """旧写法是 ast.parse(text)，现在走把手"""\n'
        '    # 这里曾经自己 ast.walk(tree)\n    return list(_walk(tree))\n'), [],
        "误伤一次，下一个真绕过就被当噪音（本仓多处吃过这一条）")
    for _tag26, _frag26 in (
            ("条款自己解一份磁盘源码",
             "def check_x(files):\n    tree = ast.parse(files['a.py'])\n    return list(_walk(tree))\n"),
            ("条款自己走一遍语法树",
             "def check_y(tree):\n    return [n for n in ast.walk(tree) if isinstance(n, ast.Call)]\n")):
        _p26 = _tree_layer_problems(_frag26)
        _assert(bool(_p26), f"反向对照（{_tag26}）：绕过把手必须响，否则这一层只盖住了四分之一")
        _assert(any("未登记" in x or "ast.walk" in x for x in _p26),
                f"反向对照（{_tag26}）：响的话要念得出是哪一行、该改调哪个把手（实际={_p26[:1]}）")
    _idle_src = _src.replace("    tree = ast.parse(text)\n    for node in list(_walk(tree)):",
                             "    tree = _file_tree('', text)\n    for node in list(_walk(tree)):", 1)
    _assert(_idle_src != _src,
            "夹具没换到一个字节＝这一档在空转（本轮第一版就是这样写错的：缩进按 8 个空格找，"
            "真源是 4 个，于是报「判据没响」而其实是模拟没发生）")
    _idle26 = _tree_layer_problems(_idle_src, whole=True)
    _assert(any("已经找不到了" in x and "_code_only_text" in x for x in _idle26),
            "反向对照（登记的那一处被接进缓存）：登记表空转必须响，而且要点名到 `_code_only_text`"
            "——这一句同时钉住了「它不许吃共享树」那个决策（吃了就会把全仓的 docstring 摘干净）"
            f"（实际={_idle26[:1]}）")

    # 行为层①：`_walk` 与 `ast.walk` 同序同集合，但同一个节点只展开一次
    import ast as _A26
    _t28 = _A26.parse("def f():\n    g(1)\n\nclass C:\n    def m(self):\n        h(2)\n")
    _assert_eq(gate._walk(_t28), list(_A26.walk(_t28)),
               "`_walk` 必须与 `ast.walk` 交同一批节点、同一个顺序（口径不许被偷偷换成带剪枝的）")
    _assert(gate._walk(_t28) is gate._walk(_t28),
            "同一个节点只许展开一次（这一层存在的全部理由）")

    # 行为层②：`_scope_nodes` 不下钻嵌套函数，且同一个作用域只算一次
    _sc_src = "def outer():\n    a = 1\n    def inner():\n        b = 2\n    return a\n"
    _outer = next(n for n in _A26.walk(_A26.parse(_sc_src))
                  if isinstance(n, _A26.FunctionDef) and n.name == "outer")
    _sc28 = gate._scope_nodes(_outer)
    _assert(not any(isinstance(x, _A26.Name) and x.id == "b" for x in _sc28),
            "`_scope_nodes` 不许下钻嵌套函数（否则某个函数里接过原因的短名会把原因色传染全仓，"
            "本轮实测误报 127 处，其中真缺陷只 7 处）")
    _assert(gate._scope_nodes(_outer) is _sc28,
            "同一个作用域只许算一次（本条款的「原因色」是不动点迭代八轮，重建表就是那 14572 次）")

    # 行为层③：缓存按**内容**取证据，改一个字节就得换一份事实
    _lab28 = "_probe_tree_cache.py"
    _fa28 = gate._listed_facts(_lab28, "class C:\n    def m(self):\n        self.aa = 1\n")
    _fb28 = gate._listed_facts(_lab28, "class C:\n    def m(self):\n        self.bb = 1\n")
    _assert_eq(sorted(_fa28["file"]["assigned"]), ["aa"], "同一个 label 的第一份字节给第一份事实")
    _assert_eq(sorted(_fb28["file"]["assigned"]), ["bb"],
               "同一个 label 换了字节必须换事实（按文件名取缓存的话，这一句会念回上一份的账）")
    _assert(gate._listed_facts(_lab28, "class C:\n    def m(self):\n        self.aa = 1\n") is _fa28,
            "同一份字节第二次取必须复用那份事实（不然这一层没帮上忙）")

    # 行为层④：`_code_only_text` 不许吃 `_file_tree`——先拿反证演示把危害演示出来
    _demo28 = "def f():\n    \"\"\"证言\"\"\"\n    g(1)\n"
    _sh28 = gate._file_tree("_probe_shared_tree.py", _demo28)
    _sh28.body[0].body.pop(0)                     # 模拟「就地改共享树」那种写法
    _sh28b = gate._file_tree("_probe_shared_tree.py", _demo28)
    _assert(_sh28b is _sh28 and not any(
        isinstance(x, _A26.Expr) and isinstance(x.value, _A26.Constant)
        and isinstance(x.value.value, str) for x in _sh28b.body[0].body),
        "这一条是**反证演示**：`_file_tree` 交出的就是同一份共享树，谁 pop 一下，后面每一条款读到的"
        "都是被摘掉 docstring 的那一份——所以「只有不改树的写法许进来」不是洁癖，上面登记表那条"
        "理由得一直被咬住")
    _said28 = gate._code_only_text(_demo28)
    _assert("证言" not in _said28 and "g(1)" in _said28,
            "`_code_only_text` 自己的本职必须还在（剔 docstring、留真会执行的代码）："
            "万一有人把它改成不剔了，下面那条「不许吃缓存」的豁免就成了无成本的洁癖")

    # 行为层⑤：缓存自己得收口（一个进程跑 45 节，每节几十份夹具）
    for _i28 in range(600):
        gate._file_tree(f"_probe_cap_{_i28}.py", f"x = {_i28}\n")
    _assert(len(gate._TREE_CACHE) <= 512,
            f"`_TREE_CACHE` 必须有上界（宁可重算，不许把上一轮的树当本轮证据）："
            f"实际={len(gate._TREE_CACHE)}")

    # 行为层⑥：四个热条款真源上跑绿，且重复跑不许飘（缓存不许让判定随调用次数变化）
    for _fn28 in ("check_reason_one_handle", "check_tab_body_identity",
                  "check_no_contact_accountability", "check_listing_accountability"):
        _r28 = getattr(gate, _fn28)()
        _assert_eq(_r28, [], f"{_fn28} 今天必须真绿")
        _assert_eq(getattr(gate, _fn28)(), _r28,
                   f"{_fn28} 第二次跑必须与第一次逐条相同（判定不许随调用次数漂移）")
    print("AT 取树只一个把手：绕过必响／登记空转必响／四条性质有行为夹具 ✓")


def test_python_floor_gate():
    print("\n═══ AU. 本机比打包机新不算绿：语法地板一处口径（第 34 条硬门）═══")
    # 由来（2026-11-16 云端：Windows 与 Mac 两个 run 同时停在构建前硬门）：离线套件里
    # 写了一句 `f"{helper('… \"…\" …')}"`——花括号里带反斜杠，那是 PEP 701（3.12）才放开的
    # 写法，而四份 workflow 都声明 `python-version: '3.11'`。本机 3.13 解析它毫无障碍：全量
    # 回归 53/53 绿、frozen 两千多项绿、门禁前三十三条绿，push 上去两个平台一起红在第 26/27 条
    # （那两条各自 ast.parse 这一份，读到 SyntaxError 就按不合格算）。
    # 真正的结构性盲区是：**门禁的语法视野等于跑它的那台机器的解释器能力**，本机越新、门越钝。
    # 所以地板要从 workflow 里取出来显式判，并且每张「哪个写法要哪个版本」的表都要有出处：
    # 下面这份对照表是拿一个真更老的解释器（3.9.6）当裁判逐档跑出来的——3.9 拒绝的必须被点名，
    # 3.9 接受的必须放行；两边不一致就是判据错了（本轮就是这样抓到「格式域只能嵌一层」这一档，
    # 它不在 PEP 701 正文里，靠猜是猜不到的）。
    gate = _manifest_gate()
    # 上一版「域内换行」那一档写的是 `f"{a⏎b}"`：它同时带了两个变量（换行 **加上** 两个名字并列），
    # 3.13 光因「并列」这一个就先在 `ast.parse` 那一档把整份文件判死了，根本走不到 f-string 分词，
    # 于是夹具报成「判据没点名」——错的不是判据，是夹具自己把两个缺陷叠在一起。
    # 本节的每一档都只许含**被考察的那一个变量**（换行那三档就是为这一条立的：单引号里两种摆法都该响，
    # 三引号里同样的换行 3.9 也接受，必须不许响）。
    gate_src = (_REPO / ".github" / "scripts" / "check_packaging_manifest.py").read_text(
        encoding="utf-8")
    BS, NL, Q3 = chr(92), chr(10), chr(34) * 3

    # ── 1) 接线：判据在、被 main() 调用、结果进退出码、云端留下能照做的 annotation ──
    for _fn in ("check_python_floor", "python_floor", "_fstring_floor_problems"):
        _assert(callable(getattr(gate, _fn, None)),
                f"硬门模块里有 {_fn}（能被单独喂夹具，才谈得上反向对照）")
    _at = gate_src.find("floor_gaps = check_python_floor()")
    _assert(_at > 0, "硬门 main() 里真的调了第 34 条（函数在而不调＝这条门从没跑过）")
    _assert("bad += 1" in gate_src[_at:_at + 1400],
            "报出来的问题要并进退出码（只 print 不改退出码的门等于公告栏）")
    _assert("::error::语法地板" in gate_src[_at:_at + 1400],
            "云端 annotation 要能直接照做：写明是「用了打包机读不懂的写法」并点名地板")
    # 不是只搜一句新文案：上一版就是这么写的，把自己拼错的「第 34 条…」当成了期望，结果
    # 报了一条假红（表里真写的是「34 交付链每份 .py 都必须被打包机那个解释器读得懂」）。
    # 改成两边都按模式提号数：凡是某个 check_* 函数的 docstring 里写了「第 N 条」，
    # 文件头那张索引表里就必须有 N——漏的不只今天这一条，而是以后每一条新门。
    # 边界取「模块文档字符串」而不是取某个句子当错位针：上一版就是拿“口径（谁是真源）”
    # 切头，而那句话在编号表**前面**，于是提出来永远是空集——空集减任何数都是空，门当场假绿。
    _head = ast.get_docstring(ast.parse(gate_src)) or ""
    _listed = {int(n) for n in re.findall(r"^\s*(\d{1,2})\s", _head, re.M)}
    _listed |= {int(n) for n in re.findall(r"[/／]\s*(\d{1,2})\s", _head)}
    _claimed = set()
    for _nd in ast.walk(ast.parse(gate_src)):
        if isinstance(_nd, ast.FunctionDef) and _nd.name.startswith("check"):
            _dc = ast.get_docstring(_nd) or ""
            _claimed |= {int(x) for x in re.findall(r"第 (\d{1,2}) 条", _dc)}
    _assert(_claimed and not (_claimed - _listed),
            "文件头那张编号表与真源里的条款号对齐（只看到 20 的人会以为后面没有了）："
            "实得表上 " + str(sorted(_listed)[-6:]) + " 缺登记：" + str(sorted(_claimed - _listed)))
    # 上面那条在「两个集合都是空」时会假绿，而今天提不出号数完全可能是因为提法写错。
    # 所以把两个集合自身的量与形状也钉住：紧凑那一段（「21 之后（21 … / 22 … / 34 …）」）
    # 只能靠分隔符提到，只认行号开头的提法会整段漏掉——那就是我刚踩过的坑。
    _assert(len(_listed) >= 34 and {1, 21, 22, 33, 34} <= _listed,
            "编号表确实被提到了 34 条且含紧凑段里的号数（实得 " + str(sorted(_listed)) + "）："
            "只认行号开头的提法会把「21 之后（… / 22 … / 34 …）」整段漏成空集")
    _assert(len(_claimed) >= 20,
            "真源里确实有 " + str(len(_claimed)) + " 个 check_* 函数在 docstring 里点了条款号"
            "（声称集合为空时上面那条减法永远为空，等于没判）")
    _assert("这张编号表不是全集" in gate_src,
            "表末尾那句「这张编号表不是全集」要在（漏的那一句正是这一条要防的事）")

    # ── 2) 地板只有一个来源：各份 workflow 的**门禁**解释器必须说同一种版本 ──
    # （份数不写在文案里：那一句「四份」在第五份接进来之后就成了谎话，与 DD 节那个
    #   写死的「3」是同一处缺陷。）
    _fl, _fp = gate.python_floor()
    _assert_eq(_fp, [], "今天的各份 workflow 的**门禁**解释器口径一致")
    _assert(_fl >= (3, 10), f"地板读出来是个真版本（实际 {_fl}）")
    _assert("python-version" in gate_src and "_PY_FLOOR_PAT" in gate_src,
            "地板是**从 workflow 派生**的，不是在本条款里又抄了一遍数字（抄的那一份会过期）")

    # ── 3) 地板口径的两个反向对照：不一致必响、取不到必响（都不许默默拿默认值跑）──
    _tmp = Path(tempfile.mkdtemp(prefix="frozen34_"))
    _saved_root, _saved_wk = gate.ROOT, gate.WORKFLOWS
    try:
        gate.ROOT = _tmp
        wdir = _tmp / ".github" / "workflows"
        wdir.mkdir(parents=True, exist_ok=True)

        def _write_wf(pairs):
            for name, ver in pairs:
                body = ("- uses: actions/setup-python@v5\n"
                        "  with:\n    python-version: '" + ver + "'\n") if ver else \
                    "- uses: actions/setup-python@v5\n  with:\n    python-version: 3\n"
                (wdir / name).write_text(body, encoding="utf-8")

        _write_wf([("a.yml", "3.11"), ("b.yml", "3.12"), ("c.yml", "3.11"), ("d.yml", "3.11")])
        gate.WORKFLOWS = tuple(".github/workflows/" + n for n in ("a.yml", "b.yml", "c.yml", "d.yml"))
        _f2, _p2 = gate.python_floor()
        _assert(_p2 and "不止一种" in "".join(_p2),
                "两份 workflow 各说一种版本：必响（打包机语法能力不一致时，「本机绿」与「云端绿」"
                "又不是同一件事）")
        _assert_eq(_f2, (3, 11), "不一致时按**最低**那档当地板（最严的说话，判据不许眼盲）")

        for f in wdir.iterdir():
            f.unlink()
        _f3, _p3 = gate.python_floor()
        _assert(_p3 and "地板悬空" in "".join(_p3),
                "workflow 里一份 python-version 都读不到：必响，不许拿一个默认版本当作实测结论")
        _assert_eq(_f3, (0, 0), "悬空时不编地板（报的是「读不到」而不是「读到了某个值」）")
    finally:
        gate.ROOT, gate.WORKFLOWS = _saved_root, _saved_wk

    # ── 4) 21 档差分对照表：3.9 拒绝的必须被点名，3.9 接受的必须放行 ──
    _T34 = [
        ("表达式里嵌套串带反斜杠", ("反斜杠",), 'x = f"{d[' + chr(39) + 'a' + BS + 'nb' + chr(39) + ']}"' + NL),
        ("join 分隔符带反斜杠", ("反斜杠",), "x = f\"{'}" + BS + "n'.join(a)}\"" + NL),
        ("单引号域里复用外层双引号", ("同种引号",), 'x = f"{d[' + chr(34) + 'k' + chr(34) + ']}"' + NL),
        ("单引号域里物理换行（只含这一个变量）", ("域内换行",),
         'x = f"{1' + NL + '+ 2}"' + NL),
        ("单引号域两侧各一个换行", ("域内换行",), 'x = f"{' + NL + '1' + NL + '}"' + NL),
        ("三引号域里的换行（3.9 也接受）", (),
         "x = f" + Q3 + "{1" + NL + "+ 2}" + Q3 + NL),
        ("三引号域里写真注释", ("域内注释",),
         "x = f" + Q3 + "{a  # 注释" + NL + "}" + Q3 + NL),
        ("带格式域但反斜杠在表达式里", ("反斜杠",),
         'x = f"{d[' + chr(39) + 'x' + BS + 'ty' + chr(39) + ']:>3}"' + NL),
        ("三引号域里带反斜杠", ("反斜杠",),
         "x = f" + Q3 + "{d['a" + BS + "nb']}" + Q3 + NL),
        ("格式域嵌两层（3.9：nested too deeply）", ("嵌套域超一层",), 'x = f"{a:{b:{c}}}"' + NL),
        ("三引号里格式域嵌两层", ("嵌套域超一层",), "x = f" + Q3 + "{a:{b:{c}}}" + Q3 + NL),
        ("嵌串里的井号（配色那类写法）", (),
         'x = f"{' + chr(39) + '#ff6b6b' + chr(39) + ' if a else ' + chr(39) + '#c62828' + chr(39) + '}"' + NL),
        ("格式域里的井号（十六进制）", (), 'x = f"{v:#08x}"' + NL),
        ("反斜杠住在格式域里", (), 'x = f"{a:' + BS + 'n}"' + NL),
        ("格式域嵌一层（上一档的对照）", (), 'x = f"{a:{b}}"' + NL),
        ("域里的 dict 字面量", (), 'x = f"{ {' + chr(39) + 'k' + chr(39) + ': 1} }"' + NL),
        ("相邻隐式拼接 + 正文孤引号", (),
         'x = f"don' + chr(39) + 't {a}" f"y {b}"' + NL),
        ("转换符加宽度", (), 'x = f"{a!r:>10}"' + NL),
        ("转义大括号", (), 'x = f"{{1:2}} {a}"' + NL),
        ("切片下标", (), 'x = f"{a[1:2]}"' + NL),
        ("域里裸可迭代解包", (), 'x = f"{*a,}"' + NL),
        ("域里带括号的 lambda", (), 'x = f"{(lambda q: q)}"' + NL),
        ("域里未加括号的元组", (), 'x = f"{1, 2}"' + NL),
    ]
    _why34 = tuple(gate.FLOOR_FSTRING_CASES)
    _got_none = gate._fstring_floor_problems("x = f\"{a}\"" + NL) is None
    if _got_none:
        _assert(True,
                "本机分词器没有 FSTRING_START（≤ 3.11）：这一档在本机根本解析不出超地板的写法，"
                "由「连本机都读不懂」那一档接住——**明确跳过**而不是把跳过念成扫过且全绿")
    else:
        for _name, _exp, _src in _T34:
            _probs = gate.check_python_floor(files={"f34.py": _src})
            _txt = "".join(_probs)
            _hit = tuple(w for w in _why34 if ("花括号里有" + w + "：") in _txt)
            _assert_eq(sorted(_hit), sorted(_exp),
                       f"34 档对照「{_name}」：判据点名要与 3.9.6 的真实裁决一致")
        _named = set()
        for _row in _T34:
            _named |= set(_row[1])
        _assert_eq(_named, set(_why34),
                   "表上每一档 f-string 家用法都至少被一档夹具真点名（只在纸上的类目＝空转）")
        _silent = [r for r in _T34 if not r[1]]
        _assert(len(_silent) >= 10,
                f"对照表里「3.9 也接受、判据不许响」的那一半必须成规模（实得 {len(_silent)} 档）："
                "只有正例的表会把过度判据也念成绿——上一版就是靠这种表冤枉了配色代码")
        _assert(any("f34.py" in p for p in gate.check_python_floor(
                      files={"f34.py": 'x = f"{d[' + chr(39) + 'a' + BS + 'nb' + chr(39) + ']}"' + NL})),
                "报出来要点名是哪一份（不点名的话，下一个人只能自己全仓找）")

    # ── 5) 新语法节点那一半：地板降下去就要响，地板够新就不许响 ──
    _m34 = "match a:" + NL + "    case 1:" + NL + "        pass" + NL
    _saved_root2 = gate.ROOT
    try:
        gate.ROOT = _tmp
        (wdir / "a.yml").write_text("python-version: '3.9'\n", encoding="utf-8")
        gate.WORKFLOWS = (".github/workflows/a.yml",)
        _low = gate.check_python_floor(files={"m34.py": _m34})
        _assert(any("match" in p for p in _low),
                "地板降到 3.9 时，match 语句必被点名（PEP 634 是 3.10 才有的）")
        gate.WORKFLOWS = _saved_wk
        gate.ROOT = _saved_root2
        _high = gate.check_python_floor(files={"m34.py": _m34})
        _assert_eq(_high, [], "按真地板（workflow 里那份）判，match 是合法写法——不许误报")
    finally:
        gate.ROOT, gate.WORKFLOWS = _saved_root2, _saved_wk

    # ── 6) 空转与断链：三种「看不到东西」都不许报绿 ──
    _assert(any("一份源码都没读到" in p for p in gate.check_python_floor(files={})),
            "喂进来的视野是空的：必响（在空集合上报绿等于没判）")
    _assert(any("一份 .py 都没有" in p for p in gate.check_python_floor(files={"a.txt": "x\n"})),
            "视野里只有非 .py：必响，不许当成「都读得懂」")
    _assert(any("解析" in p or "读不懂" in p for p in gate.check_python_floor(
                  files={"b34.py": "def broken(:\n"})),
            "本机都解析不过的那一份：按不合格算而不是按「这处没问题」算")
    _first = gate.check_python_floor()               # 第一次全量
    _ff = dict(gate._FLOOR_FACTS)                    # 必须**复制**读数：拿引用比＝自己跟自己比，永真
    _second = gate.check_python_floor()              # 第二次全量
    # 「真有量」这一格不许把**跑门禁的那台解释器**当成常数（2026-11-20 干净检出泳道实测）。
    # 交付链的地板就是 workflow 里那份 3.11，而 3.11 的 tokenize 没有 FSTRING_START：门本体
    # 有一条写明了的降级路（fstring_mode 那句「本机判不了」），走它时 fstrings 恒为 0。
    # 旧断言无条件要求 >100，等于要求「跑门禁的机器必须比自家地板更先进」：本机 3.13 绿，
    # 而真正的门禁档 3.11 必红（实测得 173 份 / 0 个字面量，与云端逐字节相同）。
    # 这里要守的性质是「这一路真的接触过现场」，而不是「用了哪一套读法」；所以换一条
    # 与版本无关的同路证据（escape_files：转义那一路同一圈里编译过的份数）兼做下限，
    # 再把 f-string 那一档按本机分词器到底认不认得分情形要求——两边都不许静默地拿
    # 「本机判不了」顶替「扫过且没问题」（那正是门自己写在注释里禁止的事）。
    _assert(_ff["files"] > 100 and _ff["escape_files"] > 100,
            "全量跑一遍后事实表里真有量（实得 " + str(_ff["files"]) + " 份 .py / "
            + str(_ff["escape_files"]) + " 份编译过转义）：这一路真的接触过现场，不是在空转")
    if hasattr(tokenize, "FSTRING_START"):
        _assert(_ff["fstrings"] > 100,
                "本机分词器认得 PEP 701（有 FSTRING_START），就必须真扫到 f-string（实得 "
                + str(_ff["fstrings"]) + " 个 / 口径=" + str(_ff["fstring_mode"]) + "）："
                "认得却报 0 就是这一路默默断了")
    else:
        _assert("FSTRING_START" in str(_ff["fstring_mode"]),
                "本机分词器不认 PEP 701（地板那一档就是这样）：必须走门本体那条写明了的"
                "降级路并把理由留在口径里（实际口径=" + str(_ff["fstring_mode"])
                + "）；拿「本机判不了」冒充「扫过且没问题」就是这条门要治的病")
    _assert(_ff["fstring_mode"],
            "f-string 那一路的口径要留痕（是 tokenize 扫的还是本机判不了，✅ 那行会原样念出来）")
    _assert_eq(_ff, dict(gate._FLOOR_FACTS),
               "同一份真源喂两次得到同一张读数（这一路的视野不随机漂移）")
    _assert_eq(_first, _second,
               "同一份真源喂两次得到同一个判定（不稳定的判据会把真违规看成偶发噪音）")
    _assert_eq(_first, [],
               "今天交付链里真的没有超地板的写法——这一条把「本机绿≠云端绿」那层盲区也一并钉住："
               "夹具自己写的每一行都得先过自己的门")

    # ── 7) 终极对照：让**地板那一档解释器自己**把交付链读一遍 ──
    # 前六块测的都是「判据怎么判」，这一块测的是「判据与打包机是否一致」：拿一个真按
    # workflow 那个版本号建出来的解释器把每份 .py  compile 一次。本仓扫过的那两条 CI 红
    # 就是这句话没人做过——本机 3.13 读得懂，打包机 3.11 读不懂，而「本机绿」被当成了结论。
    # 只走 PATH 与 sys.executable 发现解释器（写死开发机绝对路径是第 25 条管的）。
    _want = "%d.%d" % _fl
    _exe, _how = "", ""
    if "%d.%d" % sys.version_info[:2] == _want:
        _exe, _how = sys.executable, "本机解释器就是地板版本"
    else:
        _cand = shutil.which("python" + _want)
        if _cand:
            _v = subprocess.run([_cand, "-c", "import sys;print('%d.%d' % sys.version_info[:2])"],
                                capture_output=True, text=True)
            if _v.stdout.strip() == _want:
                _exe, _how = _cand, "PATH 上的 python" + _want
    if not _exe:
        _assert(True,
                "本机找不到 python" + _want + "（地板那一档）：这一档只能由 tokenize 判据代判。"
                "**没实测不等于实测通过**，所以这里明说没实测；打包机上这一块会自动跑起来")
    else:
        _probe = (
            "import sys, json\n"
            "items = json.load(open(sys.argv[1], encoding='utf-8'))\n"
            "bad = []\n"
            "for name, src in items:\n"
            "    try:\n"
            "        compile(src, name, 'exec')\n"
            "    except SyntaxError as exc:\n"
            "        bad.append([name, exc.lineno, exc.msg])\n"
            "print(json.dumps({'total': len(items), 'bad': bad}, ensure_ascii=False))\n"
        )
        _pd = Path(tempfile.mkdtemp(prefix="frozen34_floor_"))
        try:
            _jl = _pd / "chain.json"
            _chain = {}
            for _k, _t in gate._dev_path_inputs().items():
                _nm = gate._rel_posix(_k) or str(_k)
                if _nm.endswith(".py"):
                    _chain[_nm] = _t
            _jl.write_text(json.dumps([[n, s] for n, s in sorted(_chain.items())],
                                       ensure_ascii=False), encoding="utf-8")
            (_pd / "probe.py").write_text(_probe, encoding="utf-8")
            _pr = subprocess.run([_exe, str(_pd / "probe.py"), str(_jl)],
                                 capture_output=True, text=True, encoding="utf-8")
            _rep = json.loads(_pr.stdout) if _pr.stdout.lstrip().startswith("{") else {}
            _assert(_rep.get("total", 0) > 100,
                    "地板解释器（" + _how + "，" + _want + "）真读了 " + str(_rep.get("total")) +
                    " 份交付链 .py（不是只抽了一份看）")
            _oldbad = [b for b in _rep.get("bad", [])]
            _assert_eq(_oldbad, [],
                       "按打包机那一档解释器实测：交付链里没有一份读不懂（实际："
                       + str(_oldbad[:3]) + "）")
            _judge = {m.group(1) for m in
                      (re.match(r"(\S+\.py) 第 (\d+) 行", p) for p in gate.check_python_floor()) if m}
            for _b in _oldbad:
                _assert(_b[0] in _judge,
                        "地板解释器拒了 " + _b[0] + "，第 34 条却得同样点名它"
                        "（两边不一致＝判据比打包机还瞎，那正是本条款要治的病）")
        finally:
            shutil.rmtree(_pd, ignore_errors=True)

    # ── 8) 转义立场（34e/34f/34g，2026-11-16 一次抓到三条，全住在注释与文档里）──
    #     这一族比超地板的写法更阴：它不需要新解释器就能写出来，也不需要被跑到。
    #     `System32\bash.exe` 在非 raw 串里是一个退格吃掉那个 b，`D:\a\...` 是响铃+回车，
    #     拿它当引用写的 `\x00` 是真 NUL，而 `\.` 现在是警告、以后是 SyntaxError。
    #     三条都是本轮写其它条款时留下的，所以「今天不许误伤」那一半与「必须响」同样重要：
    #     拿误伤当判据，下一个人会先把门改松。
    _b8 = chr(92)
    for _name, _src, _must, _key in (
        ("反斜杠被解成退格", 'x = "System32' + _b8 + 'bash.exe"' + NL, True, "真的有一个退格 BS"),
        ("反斜杠被解成 NUL", 'x = "W' + _b8 + 'x00i"' + NL, True, "真的有一个NUL"),
        ("不成转义的那个点", 'x = "D:' + _b8 + '."' + NL, True, "不成转义的字"),
        ("docstring 里引用一个换行",
         'def f():' + NL + '    """裸 `' + _b8 + 'n` 就这样。"""' + NL + '    return 1' + NL,
         True, "反引号引用的"),
        # 上面那一档拿长度就能拦，下面两档拿长度就拦不住——它们正是旧口径「反引号里那段
        # 超过 24 字符就放过」留给的那条缝，而缝里装的恰好是 Windows 路径（`\t`、`\n`）。
        # 2026-11-16 写完 34f 第一版后拿真树扫了一遍：仓里 6 处「该放过」的写法全都只含
        # 真换行，所以区分器应该是「这个控制字符是不是转义解出来的」而不是「这段有多长」。
        ("长路径里被解出的空行（旧口径按长度放过的那一档）",
         'def f():' + NL + '    """见 `C:' + _b8 + 'network' + _b8 + 'new_dir` 这个目录"""'
         + NL + '    return 1' + NL,
         True, "真空行 LF"),
        ("长路径里被解出的制表符（同一档，`C:\\temp` 那一类）",
         'def f():' + NL + '    """配置在 `C:' + _b8 + 'tools' + _b8 + 'temp_build_x` 下面"""'
         + NL + '    return 1' + NL,
         True, "制表符 TAB"),
        # 反向：三种「写对了就不许响」的形状。拿误伤当判据，下一个人会先把门改松。
        ("合法：反引号里真的引用了一段跳行代码（真源现有 6 处就长这样）",
         'def f():' + NL + '    """卡片 `<a title="x"' + NL + '     href="y"> 一行一个属性"""'
         + NL + '    return 1' + NL,
         False, ""),
        ("合法：反斜杠打成两条再引用（那两个字符就是要显示出来）",
         'def f():' + NL + '    """源码里写的是 `' + _b8 + _b8 + 'n` 这两个字"""'
         + NL + '    return 1' + NL,
         False, ""),
        ("合法：raw docstring 里引用 `\\n`（本来就不该被解）",
         'def f():' + NL + '    r"""裸 `' + _b8 + 'n` 就这样。"""' + NL + '    return 1' + NL,
         False, ""),
        ("整个常量就是一枚分隔符（写它的人真要那个字节）", 'SEP = "' + _b8 + 'x1e"' + NL, False, ""),
        ("反斜杠打成两条（本轮给出的修法）", 'x = "System32' + _b8 + _b8 + 'bash.exe"' + NL, False, ""),
        ("raw 串里的 Windows 路径", 'x = r"D:' + _b8 + 'a' + _b8 + 'riskbird"' + NL, False, ""),
    ):
        _probs = gate.check_python_floor(files={"f34e.py": _src})
        _txt = "".join(_probs)
        if _must:
            _assert(_key in _txt,
                    f"转义对照「{_name}」必须被点名（实际={_probs[:1] or '一声不响'}）")
            _assert("f34e.py" in _txt, f"转义对照「{_name}」报出来要点名是哪一份")
        else:
            _assert_eq([p for p in _probs if "f34e.py" in p], [],
                       f"转义对照「{_name}」不许误伤（写对了就不许响）：{_probs[:1]}")

    # 真树反证：本轮那三处全部长在门禁自己那份文件里，所以「改回旧写法」不拿合成代码，
    # 而是拿今天已改准的那一句倒回去——两者都要在（只测合成样品的话，真树再长出第四处也没人拦）。
    _esc_today = [p for p in gate.check_python_floor() if "控制字符" in p or "不成转义" in p]
    _assert_eq(_esc_today, [], f"第 34 条转义那一族今天在真树上全绿（实际={_esc_today[:1]}）")
    _assert(gate._FLOOR_FACTS["escape_files"] == gate._FLOOR_FACTS["files"] > 100,
            "编译那一路扫的份数要与取树那一路一致且成规模（实得 "
            + str(gate._FLOOR_FACTS["escape_files"]) + "/" + str(gate._FLOOR_FACTS["files"])
            + "）：两个计数分叉就是有一类文件在某一问里隐身")
    _in8 = dict(gate._dev_path_inputs())
    _k8 = next((p for p in _in8 if str(p).endswith("check_packaging_manifest.py")), None)
    _assert(_k8 is not None, "门禁真源本身要在第 34 条视野里（它自己也是交付链的一份 .py）")
    if _k8 is not None:
        _b8src = _in8[_k8]
        _assert(_b8src.count("System32" + _b8 + _b8 + "bash.exe") >= 1,
                "夹具钉的那一句必须还在真树上（漂了＝这一格在空跑）")
        _in8[_k8] = _b8src.replace("System32" + _b8 + _b8 + "bash.exe",
                                   "System32" + _b8 + "bash.exe", 1)
        _got8 = [p for p in gate.check_python_floor(files=_in8) if "退格" in p]
        _assert(any("check_packaging_manifest.py" in p for p in _got8),
                f"把门禁自己那句改回旧写法必须响（实际={_got8[:1] or '一声不响'}）")

    shutil.rmtree(_tmp, ignore_errors=True)


def test_bash_lookup_gate():
    """AV. 取 bash 只一把刀（第 35 条硬门）。"""
    print("\n═══ AV. 同一个名字在两个平台不能解析成两个程序：bash 取值把手（第 35 条硬门）═══")
    gate = _manifest_gate()

    def _fx(name: str, body: str) -> dict:
        return {Path(f"b2bcollect_build/{name}"): body}

    # 1) 反向：四种「自己取 bash」的形状必须各自响。只验「现在绿」不验「坏了会响」的
    #    判据等于没有判据（这一条在 11/23/25/30 条上都付过钱）。
    _bad = (
        ("字面量直接起子进程", 'import subprocess\nsubprocess.run(["bash", "x.sh"])\n',
         ("裸名字", "resolve_shell_cmd")),
        ("换个写法：先赋变量再起", 'import subprocess\n\n\ndef f():\n'
         '    cmd = ["bash", "-e"] + ["-c", "x"]\n    subprocess.run(cmd)\n',
         ("同一层作域", "resolve_shell_cmd")),
        ("拼接只接到右边也算", 'import subprocess\n\n\ndef f(x):\n'
         '    cmd = ["bash"] + x\n    subprocess.run(cmd)\n', ("同一层作域",)),
        ("which 只取第一个", 'import shutil\nshutil.which("bash")\n',
         ("PATH 上第一个", "bash_executable")),
        ("新模板没登记", 'REGRESSION_LIST_CMD9 = ("bash", "x.sh")\n',
         ("_SHELL_TEMPLATE_NAMES",)),
    )
    for why, src, wants in _bad:
        hits = gate.check_bash_single_lookup(_fx("zz_av_bad.py", src))
        _assert(hits, f"反向对照「{why}」必须响（实得 {hits}）")
        for w in wants:
            _assert(any(w in h for h in hits), f"「{why}」的响话里要能读出 {w}（实得 {hits[:2]}）")

    # 2) 正向：接到把手上的写法不许响。这一半是必要的——只有反向的门会把人也拦在门外。
    _good = (
        ("执行前过 resolve_shell_cmd", 'import subprocess\n\n\ndef f(gate):\n'
         '    argv, _ = gate.resolve_shell_cmd(("bash", "x.sh"))\n'
         '    subprocess.run(argv)\n'),
        ("which 逐目录查（带 path=）", 'import shutil\nshutil.which("bash", path="/bin")\n'),
        ("relative_to 不是裁前缀", 'from pathlib import Path\n'
         'Path("x").relative_to(ROOT)\n'),
    )
    for why, src in _good:
        _assert_eq(gate.check_bash_single_lookup(_fx("zz_av_good.py", src)), [],
                   f"合规写法「{why}」不该被第 35 条拦住")

    # 3) 两份把手 / 空视野：两条防空转。
    _two = "def bash_executable():\n    return \"\", \"\"\n"
    _hits = gate.check_bash_single_lookup({Path("b2bcollect_build/zz_a.py"): _two,
                                           Path("b2bcollect_build/zz_b.py"): _two})
    _assert(any("2 份定义" in h for h in _hits),
           f"同名把手有两份定义时必须点名（哪份生效交给 import 顺序）：{_hits[:2]}")
    _hits = gate.check_bash_single_lookup({Path("b2bcollect_build/zz_av.txt"): "bash"})
    _assert(any("一份 .py 都没读到" in h for h in _hits),
            f"视野里没有 .py 时不许报绿（空集合上的「合格」等于没判）：{_hits[:2]}")

    # 4) 行为侧真验：在 Mac 上**造一枚 WSL 存根**——它不是「我以为 Windows 长什么样」，
    #    而是字节形状与云端那 504 字节同类的一份真产物：输出 UTF-16LE、rc=1、名字就叫 bash。
    _tmp = Path(tempfile.mkdtemp(prefix="av_wsl_stub_"))
    _old_path = os.environ.get("PATH", "")
    try:
        _stub = _tmp / "bash"
        _stub.write_text("#!/bin/sh\n"
                         "printf 'Windows Subsystem for Linux has no installed distributions'"
                         " | iconv -f UTF-8 -t UTF-16LE\n"
                         "echo\nexit 1\n", encoding="utf-8")
        _stub.chmod(0o755)
        _real, _ = _bash_argv()
        if not _real or not shutil.which("iconv"):
            print(f"   ⏭ 本机造不出这一族（bash 把手：{_real and '有'}｜iconv："
                  f"{bool(shutil.which('iconv'))}），存根与 Windows 仿真那四档一并跳过；"
                  "静态那几格照跑")
            return
        _prog = _real[0]
        # 4a) 存根排在 PATH 最前：把手必须跨过它拿到真 bash，并在现场里把被淘汰的那枚说出来
        os.environ["PATH"] = f"{_tmp}{os.pathsep}{_old_path}"
        gate._BASH_CACHE.clear()
        _got, _note = gate.bash_executable()
        _assert(_got == _prog, f"把手不能让存根赢（实得 {_got!r}，真的应是 {_prog!r}）："
                               "那正是本轮云端把 --list 交给一个不会跑它的程序的那一步")
        _assert(str(_stub) in _note and "淘汰" in _note,
                f"现场要指名被淘汰的那枚存根（实得 {_note!r}）：只说「找到了」不说明「跳过了谁」，"
                "下一个人还是只能靠算术反推")
        _assert("Subsystem for Linux" in _note or "WSL" in _note,
                f"淘汰理由要说「它为什么不是 bash」（实得 {_note!r}）")
        _argv, _ = gate.resolve_shell_cmd(("bash", "x.sh"))
        _assert(_argv[0] == _prog, f"执行位点拿到的必须是同一个程序（实得 {_argv[0]!r}）："
                                   "取证与执行分叉就是本轮事故本身")
        # 4b) 只留存根：把手必须说「没有」，而且红话要把它说成存根而不是默默递回裸名字
        os.environ["PATH"] = str(_tmp)
        gate._BASH_CACHE.clear()
        _got2, _note2 = gate.bash_executable()
        _assert(not _got2, f"只剩存根时不许报出一个程序（实得 {_got2!r}）")
        _assert("Subsystem for Linux" in _note2 or "WSL" in _note2 or "版本横幅" in _note2,
                f"取不到的理由要说清它不是 bash（实得 {_note2!r}）")
        _kept, _ = gate.resolve_shell_cmd(("bash", "--version"))
        _assert(_kept[0] == "bash",
                f"解析不出时不许偷换成存根去跑（实得 {_kept[0]!r}）：调用方必须能看出来没执行")
        # 4c) NUL 签名必须被读成 UTF-16：同一批字节交给 _decode_maybe_utf16 要能拼回人话
        _raw = ("Windows Subsystem for Linux has no installed distributions"
                .encode("utf-16le"))
        _dec = gate._decode_maybe_utf16(_raw)
        _assert("Subsystem for Linux" in _dec and "\x00" not in _dec,
                f"UTF-16LE 要解回人话（实得 {_dec[:60]!r}）：带着 NUL 去拼红话就是第二轮猜谜")
        # 4d) 装了发行版的 WSL：横幅是**真**的 GNU bash、rc=0，只有括号里的三元组露出它住在
        #     Linux 内核。在 Mac 上把 Windows 那一半仿真出来真跑选路——不仿真就等于把最要紧的
        #     一档写进永远执行不到的分支。
        _wsl_dir = _tmp / "wsl_distro"
        _wsl_dir.mkdir()
        _fake = _wsl_dir / "bash"
        _fake.write_text("#!/bin/sh\n"
                         "echo 'GNU bash, version 5.2.15(1)-release (x86_64-pc-linux-gnu)'\n"
                         "exit 0\n", encoding="utf-8")
        _fake.chmod(0o755)
        _host_ver, _host_why = gate._bash_identity(_prog, on_windows=True)
        if _host_ver is None and "Linux 内核" in _host_why:
            print("   ⏭ 本机真 bash 自己就是 linux-gnu 那份（在 WSL/Linux 上跑这一轮），"
                  "Windows 仿真那一档的「不误杀」无从对照，跳过 4d；存根三档照跑")
        else:
            os.environ["PATH"] = f"{_wsl_dir}{os.pathsep}{_old_path}"
            gate._BASH_CACHE.clear()
            _got3, _note3 = gate.bash_executable(windows=True)
            _assert(_got3 == _prog,
                    f"真横幅不能替 Linux 那份 bash 开路（实得 {_got3!r}，应是 {_prog!r}）："
                    "它进了 WSL，看不见 Windows 形状的工作目录，脚本会从第一个 cd 就坏")
            _assert(str(_fake) in _note3 and "淘汰" in _note3,
                    f"现场要指名被淘汰的那枚 WSL bash（实得 {_note3!r}）")
            _assert("Linux" in _note3,
                    f"淘汰理由要点名它是 Linux 内核那份（实得 {_note3!r}）："
                    "只说「不合适」人就又要重新发现一次")
            # 同一枚程序、同一个 PATH，在 Linux 立场上必须**不被**淘汰：否则这一刀是把两条腿
            # 都砍了（真在 Linux 上跑门禁时会一个 bash 都找不到）。顺带证明缓存键带了平台。
            _got4, _ = gate.bash_executable(windows=False)
            _assert(_got4 == str(_fake),
                    f"同一枚 linux-gnu bash 在非 Windows 立场下应被选中（实得 {_got4!r}）："
                    f"这条判据只属于 Windows；且与 windows=True 的结论必须不同（{_got3!r}）")
            # Windows 立场 + 只有真 bash：不许误杀（Git 装在非常规位置也该活）
            os.environ["PATH"] = str(Path(_prog).parent)
            gate._BASH_CACHE.clear()
            _got5, _note5 = gate.bash_executable(windows=True)
            _assert(_got5 == _prog,
                    f"Windows 立场不许把平台的真 bash 也抖掉（实得 {_got5!r}，理由 {_note5!r}）")
            # Windows 立场 + 只有 WSL 那枚：必须说「没有」，不许递一个能过横幅就交出去的程序
            os.environ["PATH"] = str(_wsl_dir)
            gate._BASH_CACHE.clear()
            _got6, _note6 = gate.bash_executable(windows=True)
            _assert(not _got6, f"只剩 WSL bash 时不许报出一个程序（实得 {_got6!r}）："
                              "报出去就等于把 --list 交给看不见工作目录的那一位")
            _assert("Linux" in _note6, f"取不到的理由要说破三元组（实得 {_note6!r}）")
    finally:
        os.environ["PATH"] = _old_path
        gate._BASH_CACHE.clear()
        shutil.rmtree(_tmp, ignore_errors=True)

    # 5) 生产现场：真模板命令的 argv[0] 必须是把手那一个，而且红话里不能再出现两个 bash
    _argv, _note = gate.resolve_shell_cmd(gate.REGRESSION_LIST_CMD)
    _assert(_argv[0] != "bash", f"模板递到执行位点前必须被换成绝对路径（实得 {_argv[0]!r}）")
    _assert(gate.BASH_TOKEN not in str(_argv[0]) or Path(_argv[0]).is_file(),
            f"实发的必须是一个存在的程序（{_argv[0]!r}）")
    _facts = gate._list_channel_facts()
    # 取证那句里的 bash 必须与实发那一个**逐字相同**：本轮云端就是同一句红话里写着
    # System32 存根的输出、又写着 cygwin 的版本号，两句各自是真话、合起来把方向带偏。
    _m = re.search(r"bash=(.+?)｜", _facts + "｜")
    _assert(_m is not None and _m.group(1) == _argv[0],
            f"取证与执行必须是同一个程序（现场读到 {_m.group(1) if _m else '无 bash='!r}，"
            f"实发 {_argv[0]!r}）")


def test_path_label_gate():
    """AW. 报告用的路径写法只一把刀（第 36 条硬门）。"""
    print("\n═══ AW. 同一把尺子只裁一次：报告路径与 git 写法同源（第 36 条硬门）═══")
    gate = _manifest_gate()
    from pathlib import PureWindowsPath as _PWP

    # 1) 真登记表的键：两种分隔符形状都必须能算回那个键。这不是「看起来像」，而是
    #    真拿它们去查表——本轮云端那一路假红就是因为表里每条登记都正确、键却查不到。
    _keys = sorted(set(gate.DEV_PATH_EXEMPT) | set(gate.TAB_BODY_EXITS))
    _assert(len(_keys) >= 5, f"参照表共 {len(_keys)} 个键太少了，这一格可能在空转")
    for _k in _keys:
        _assert_eq(gate._rel_posix(gate.ROOT / _k), _k, f"POSIX 形状上 {_k} 必须能算回登记的键")
        _w = _PWP(gate.CI_WIN_ROOT + "\\" + _k.replace("/", "\\"))
        _assert_eq(gate._rel_posix(_w, root=_PWP(gate.CI_WIN_ROOT)), _k,
                   f"Windows 形状（反斜杠、带盘符）上 {_k} 也必须能算回登记的键："
                   "那一档算不回来就是 2026-11-16 云端那一路假红")
        # 旧写法（自己按 str(ROOT)+"/" 裁）在同一输入上必须给出**不同**的结果：否则
        # 上面那一判在本机上恒真，看上去绿、实际判不到分隔符这件事。
        _assert(str(_w).replace(gate.CI_WIN_ROOT + "/", "") != _k,
                f"旧写法在 {_k} 上必须算错（算对了就说明这一格空转）")

    # 2) 反向对照：把 `_rel_label` 改回旧写法，第 36 条必须两头都响（没委托 + 自己提 ROOT）
    _old = ("ROOT = 1\n\n\ndef _rel_label(path):\n    text = str(path)\n"
            "    root = str(ROOT) + \"/\"\n    return text.replace(root, \"\")\n")
    _hits = gate.check_report_path_label({Path("b2bcollect_build/zz_aw_old.py"): _old})
    _assert(any("没把归一交给 _rel_posix" in h for h in _hits),
            f"旧写法必须被读出「没委托」：{_hits[:2]}")
    _assert(any("直接提了 ROOT" in h for h in _hits),
            f"旧写法必须被读出「自己碰了 ROOT」：{_hits[:2]}")

    # 3) 第二把刀的形状：不在 _rel_label 里、但在另一个函数里拿 ROOT 自己裁，也要响
    _knife = ("ROOT = 1\n\n\ndef other_label(p):\n"
              "    return str(p).replace(str(ROOT) + \"/\", \"\")\n")
    _hits = gate.check_report_path_label({Path("b2bcollect_build/zz_aw_knife.py"): _knife})
    _assert(any("自己裁了一遍前缀" in h for h in _hits),
            f"别的函数里再造一把尺子也要点名：{_hits[:2]}")
    # 反向的反向：同样的活交给 _rel_posix 干，就不该响（否则这条门会把修法本身拦在门外）
    _ok = ("ROOT = 1\n\n\ndef other_label(p):\n    return _rel_posix(p)\n")
    _assert_eq(gate.check_report_path_label({Path("b2bcollect_build/zz_aw_ok.py"): _ok}), [],
               "把归一交给 _rel_posix 的写法不该被拦住")

    # 4) 性质判据：`_rel_label` 的输出必须与 `_rel_posix` 逐项同值（真源只一处就不许分叉）
    _cases = [str(gate.ROOT / "b2bcollect_build/local_scan.py"),
              str(gate.ROOT / "b2bcollect_build/cdp_base.py"),
              gate.CI_WIN_ROOT + "\\b2bcollect_build\\gys_crawler.py",
              "a.py", "", "/etc/hosts"]
    for _c in _cases:
        _a = gate._rel_label(_c)
        _b = gate._rel_posix(Path(_c))
        _assert(_a == (_b if _b is not None else _c.replace("\\", "/")),
                f"输入 {_c!r}：_rel_label 与 _rel_posix 必须同值（前者 {_a!r}，"
                f"后者 {_b!r}；仓外时按反斜杠归一比对）")
    _assert_eq(gate._rel_label(str(gate.ROOT / "b2bcollect_build/local_scan.py")),
               "b2bcollect_build/local_scan.py", "仓内路径要给出登记表的键那种写法")
    _assert_eq(gate._rel_label("a.py"), "a.py", "仓外路径保持原样（语义与旧版一致这一句要真）")

    # 5) 空转保险：参照表空 / 视野里一份 .py 都没有，都必须响而不是报绿
    _empty = gate._rel_posix(Path("a.py"))
    _assert_eq(_empty, "a.py", "仓外相对路径归一后仍是它本身（上面那一判的参照）")
    _hits = gate.check_report_path_label({Path("b2bcollect_build/zz_aw.txt"): "ROOT"})
    _assert(any("一份 .py 都没读到" in h for h in _hits),
            f"第 36 条不许在空视野上报绿：{_hits[:2]}")


def _ax_files():
    """拿一份真视野的完整快照：反向对照只改其中那一刀（只喂一份会撞「缺镜像」的噪点）。"""
    return dict(_manifest_gate()._runtime_reason_sources())


def _ax_patch(files, rel, old, new):
    """把某个文件里的 old 换成 new；old 不在位就直接响——静默没改到等于这一格空转。"""
    gate = _manifest_gate()
    key = next((p for p in files if gate._rel_label(p) == rel), None)
    _assert(key is not None, f"这一格反证要改的文件 {rel} 必须在视野里（不在就只能空转）")
    text = files[key]
    _assert(old in text, f"{rel} 里必须还钉得住这一刀（{old[:44]}）——"
                         "拿不到就是夹具钉的已不是现在那份代码，这一格反证不成立")
    files[key] = text.replace(old, new)
    return files


def test_page_ledger_gate():
    """AX. 通道账：两张口径表同源分列、那句念法只住把手（第 37 条硬门）。"""
    print("\n═══ AX. 取页失败那一格不许说谎：口径两张表、念法一个把手（第 37 条硬门）═══")
    gate = _manifest_gate()

    # 1) 现状：真源全绿；且行为对拍真的跑了那几档事实（不是视野空了才绿）
    _assert_eq(gate.check_page_ledger_gate(), [], "第 37 条：通道账在真源上必须全绿")
    _assert(len(gate.PAGE_LEDGER_CASES) >= 4,
            f"行为对拍只有 {len(gate.PAGE_LEDGER_CASES)} 档，判不到本轮修的那几类形状就是在空转")
    _assert_eq(len([c for c in gate.PAGE_LEDGER_CASES if "本轮那一档" in c[0]]), 1,
               "夹具里必须恰好有一档是「本轮那一档」（少了这一格就在判另一件事）")

    # 2) 本轮那个谎的形状：两份把手同时退化成「拿计数决定原因说不说」。
    # 逐字节仍相同、调用处也没动，只有行为对拍能看见这一刀——这正是 AX-2 存在的理由。
    _f = _ax_patch(_ax_files(), "b2bcollect_build/cdp_base.py",
                   "    if not pf and not err:", "    if not pf:")
    _f = _ax_patch(_f, "ali1688_build/ali1688_crawler.py",
                   "    if not pf and not err:", "    if not pf:")
    _hits = gate.check_page_ledger_gate(_f)
    _assert(any("详情腿整条不通" in h for h in _hits),
            f"把手退化成「计数为 0 就闭嘴」时必须响：{_hits[:3]}")
    _assert(not any("不是逐字节相同" in h for h in _hits),
            f"两份同步改，逐字节对拍不该抢行为对拍的案子（报了就是拿它冒充行为判据）：{_hits[:3]}")

    # 3) 口径借错表：深链那一支去问拉黑门，就是顺企网那 14 次 TLS 失败掉在账外的正身
    _hits = gate.check_page_ledger_gate(_ax_patch(
        _ax_files(), "b2bcollect_build/cdp_base.py",
        f"not {gate.PAGE_LEDGER_ACCOUNT_GATE}(last)", f"not {gate.PAGE_LEDGER_CONNECT_GATE}(last)"))
    _assert(any("_note_fetch_fail" in h for h in _hits),
            f"深链那一支借拉黑表必须被点名：{_hits[:3]}")

    # 4) 两张表合回一张：拉黑纪律会再次把账拦在门外（两个分支各验一次，缺一边就是空判）
    _hits = gate.check_page_ledger_gate(_ax_patch(
        _ax_files(), "b2bcollect_build/cdp_base.py",
        'PAGE_FAIL_KINDS = DEAD_HOST_KINDS + ("TLS握手失败",)', "PAGE_FAIL_KINDS = DEAD_HOST_KINDS"))
    _assert(any("那个形状" in h for h in _hits),
            f"两张表直接合一（不再同源分列）要响：{_hits[:3]}")
    _hits = gate.check_page_ledger_gate(_ax_patch(
        _ax_files(), "b2bcollect_build/cdp_base.py",
        'PAGE_FAIL_KINDS = DEAD_HOST_KINDS + ("TLS握手失败",)',
        "PAGE_FAIL_KINDS = DEAD_HOST_KINDS + ()"))
    _assert(any("多出 0 档" in h for h in _hits),
            f"形状对、但比拉黑表多不出任何一档也要响（那一档就是 TLS）：{_hits[:3]}")

    # 5) 第二把刀与少给键：新写一路自己拼那句话 / stats 只给计数不给念法
    _f = _ax_files()
    _f[Path("b2bcollect_build/zz_ax_inline.py")] = (
        'def page_ledger_line(page_fail, last_page_error, cap=60):\n'
        '    return ""\n\n\ndef render(pf, err):\n'
        '    return f"取页失败 {pf} 页（最后一次：{err}）" if pf else ""\n')
    _hits = gate.check_page_ledger_gate(_f)
    _assert(any("zz_ax_inline.py 第 6 行自己拼了一句" in h for h in _hits),
            f"再拼一句「计数+原因」那个形状必须响：{_hits[:3]}")
    _f = _ax_files()
    _f[Path("b2bcollect_build/zz_ax_stats.py")] = (
        'class C:\n    def stats(self):\n        return {"page_fail": 0, "got": 0}\n')
    _hits = gate.check_page_ledger_gate(_f)
    _assert(any("没透出 page_ledger" in h for h in _hits),
            f"stats 给了计数不给念法必须响：{_hits[:3]}")

    # 5b) 藏在 f-string **占位符里面**的第二处拼法也要响（这一格是 2026-11-16 补的）：
    #     上一版 37e 只收 `node.values` 顶层那几段字面，于是探针里那句
    #     `f"…{_pf_line(st) or '取页失败 0 页（最后一次：未记录）'}…"` 直接逃掉——
    #     它比顶层写法更像把手（连括号都同），却藏在判据看不见的地方。
    _f = _ax_files()
    _f[Path("b2bcollect_build/zz_ax_nested.py")] = (
        "def render(st, line):\n"
        "    return f\"  链路: {line or '取页失败 0 页（最后一次：未记录）'}｜\"\n")
    _hits = gate.check_page_ledger_gate(_f)
    _assert(any("zz_ax_nested.py 第 2 行自己拼了一句" in h for h in _hits),
            f"拼法装在占位符里也得点名（否则这一族只要换个写法就永久逃掉）：{_hits[:3]}")

    # 5c) 反向的反向：如实说「没有」的那一句不该被误伤——它只许提计数，不许配原因槽。
    #     今天真树上的两处就长这个形状（健康牌与实机体检），把它们喂回去必须不响，
    #     否则下一班会把「如实交空」那一格也改成借把手拼一句。
    _f = _ax_files()
    _f[Path("b2bcollect_build/zz_ax_honest_zero.py")] = (
        "def render(st):\n"
        "    return f\"链路: {st.get('page_ledger') or '本轮没有取页失败记录'}｜\"\n")
    _hits = gate.check_page_ledger_gate(_f)
    _assert_eq([h for h in _hits if "zz_ax_honest_zero.py" in h], [],
               f"只提计数、不配原因槽的那句不许误伤：{_hits[:3]}")

    # 5d) 真树反证（不是合成代码）：今天已经改成「只念渲染好的那一格」的两处，
    #     倒回自己拼那一刀必须被第 37 条当场抓住。这一格是从第 30 条的 _RETIRE 里
    #     迁过来的：那边钉的是「`short_reason(…40)` 在真树恰好一处」，而健康牌现在
    #     一个 `short_reason(` 都不留（读的是 page_ledger），钉子留在旧口径上只会假响。
    _RS = dict(gate._runtime_reason_sources())
    for _label, _rel, _now, _old in (
        ("健康牌那一格", "b2bcollect_build/channel_health.py",
         'bits.append(str(s["page_ledger"]))',
         "bits.append(f\"取页失败 {s['page_fail']} 页（最后一次："
         "{short_reason(s.get('last_page_error'), 40)}）\")"),
        ("实机体检的链路行", "debug/platform_live_check.py",
         "or '本轮没有取页失败记录'", "or '取页失败 0 页（最后一次：未记录）'"),
    ):
        _k = next((p for p in _RS if gate._rel_label(p) == _rel), None)
        _assert(_k is not None, f"{_rel} 必须在第 37 条视野里（读不到就没法做这一格对照）")
        if _k is None:
            continue
        _body = _RS[_k]
        _assert_eq(_body.count(_now), 1,
                   f"{_label}：现写法片段在真树上应恰好一处（漂了＝这一格在空跑）")
        _hits = [h for h in gate.check_page_ledger_gate({**_RS, _k: _body.replace(_now, _old, 1)})
                 if "自己拼了一句" in h]
        _assert_eq(len(_hits), 1,
                   f"{_label}：倒回自己拼必须响一条第 37 条（实际={_hits[:1]}）")

    # 6) 不误伤：中文里带「取页失败」四个字但不是那一格（日志文案／聚合总数）
    _f = _ax_files()
    _f[Path("b2bcollect_build/zz_ax_words.py")] = (
        'def log(last, url, pfail):\n'
        '    print(f"  ⚠ HTTP 取页失败（{last}）：{url[:70]}")\n'
        '    print(f"｜取页失败 {pfail} 页")\n'
        '    print(f"｜{pfail} 路最后一次取页失败但未计入页数")\n')
    _hits = gate.check_page_ledger_gate(_f)
    _assert_eq([h for h in _hits if "zz_ax_words.py" in h], [],
               f"日志文案与聚合行不该被误伤（误伤一次，下一个真漏接的人就被当噪音放过）：{_hits[:3]}")

    # 7) 空转保险：视野一份都没有、只有一份解析不过的 .py、真源不在位，都不许报绿
    _hits = gate.check_page_ledger_gate({})
    _assert(any("一份源码都没读到" in h for h in _hits), f"空视野不许绿：{_hits[:2]}")
    _hits = gate.check_page_ledger_gate({Path("b2bcollect_build/zz_ax_broken.py"): "def ("})
    _assert(any("扫了 0 份源码" in h for h in _hits),
            f"全解不过不许默默少一份，要报「等于没跑」：{_hits[:2]}")
    _hits = gate.check_page_ledger_gate({Path("b2bcollect_build/other.py"): "x = 1\n"})
    _assert(any("真源不在位" in h for h in _hits), f"真源缺席不许绿：{_hits[:2]}")


def test_human_wait_lamp_gate():
    """AY. 等人那盏灯只能由把手点亮（第 38 条硬门）。"""
    print("\n═══ AY. 灯不亮就是把「等到超时」说成「采不到」：等人灯只由把手点亮（第 38 条硬门）═══")
    gate = _manifest_gate()

    # 1) 现状全绿，且措辞档位与灯的状态机都真跑过
    _assert_eq(gate.check_human_wait_lamp_gate(), [], "第 38 条：等人灯在真源上必须全绿")
    _assert(len(gate.WAIT_CASES) >= 6,
            f"措辞对拍只有 {len(gate.WAIT_CASES)} 档，分不出登录/过码两档就是在空转")

    # 2) 本轮那个谎的形状：灯只被 init/清空/读取，从没有一处点亮它（形状判据全绿）。
    # 三处点灯都在名单上：两份镜像各自那一处 + 名单通道（爱企查）那一处，少改一处就不响。
    _f = _ax_patch(_ax_files(), "b2bcollect_build/cdp_base.py",
                   "_waiting_human = human_wait_label(", "_wait_off = human_wait_label(")
    _f = _ax_patch(_f, "ali1688_build/ali1688_crawler.py",
                   "_waiting_human = human_wait_label(", "_wait_off = human_wait_label(")
    _f = _ax_patch(_f, "b2bcollect_build/site_adapters.py",
                   "_waiting_human = cdp_base.human_wait_label(",
                   "_wait_off = cdp_base.human_wait_label(")
    _hits = gate.check_human_wait_lamp_gate(_f)
    _assert(any("从没亮过" in h for h in _hits),
            f"灯不亮必须响（本轮那个谎就是这个形状）：{_hits[:3]}")
    _assert(any("没有 _waiting_human 这一格" in h for h in _hits),
            f"灯被改名时状态机对拍也得点名（它比「没亮」更难靠肉眼看出来）：{_hits[:3]}")

    # 3) 恢复写成旁路赋值（上一轮 site_adapters 的真实形状），与硬写一句措辞
    _f = _ax_patch(_ax_files(), "b2bcollect_build/site_adapters.py",
                   "    prev = cdp_base.human_wait_hold(owner, reason)",
                   '    prev = str(getattr(owner, "_waiting_human", "") or "")\n'
                   "    owner._waiting_human = prev")
    _hits = gate.check_human_wait_lamp_gate(_f)
    _assert(any("赋成了 'prev'" in h for h in _hits),
            f"「恢复上一个值」写成旁路赋值也要响：{_hits[:3]}")
    _hits = gate.check_human_wait_lamp_gate(_ax_patch(
        _ax_files(), "b2bcollect_build/site_adapters.py", "def _shell_check(result):",
        'def _bad_lamp(owner):\n    owner._waiting_human = "登录"\n\n\ndef _shell_check(result):'))
    _assert(any("赋成了 \"'登录'\"" in h for h in _hits),
            f"硬写一个措辞进那一格必须响：{_hits[:3]}")

    # 4) 旁路再养一枚措辞把手（上一轮 site_adapters 那个形状）必须响
    _hits = gate.check_human_wait_lamp_gate(_ax_patch(
        _ax_files(), "b2bcollect_build/site_adapters.py", "def _shell_check(result):",
        'def human_wait_label(reason, login=None):\n    return "登录"\n\n\n'
        'def _shell_check(result):'))
    _assert(any("又养了一枚 human_wait_label" in h for h in _hits),
            f"非镜像那两份再定义措辞把手必须响：{_hits[:3]}")

    # 5) 两份镜像分叉：措辞常量改一个字，逐字节与行为两道都得各自响一次
    _hits = gate.check_human_wait_lamp_gate(_ax_patch(
        _ax_files(), "ali1688_build/ali1688_crawler.py",
        'HUMAN_WAIT_MANUAL_LABEL = "人工过验证"', 'HUMAN_WAIT_MANUAL_LABEL = "人工验证"'))
    _assert(any("不是逐字节相同" in h for h in _hits), f"镜像分叉要逐字节响：{_hits[:3]}")
    _assert(any("档不同答" in h for h in _hits), f"镜像分叉要行为也响（两道彼此独立）：{_hits[:3]}")

    # 6) 两种生命周期谎：收口不关灯（长亮）/ 归还退化成清空（该亮时不亮）
    _hits = gate.check_human_wait_lamp_gate(_ax_patch(
        _ax_files(), "b2bcollect_build/cdp_base.py",
        '        self._waiting_human = ""\n        self._release_verify_tab()',
        '        self._release_verify_tab()'))
    _assert(any("里没把 _waiting_human 置空" in h for h in _hits),
            f"收口不关灯必须响（人早走了、报告还说在等人）：{_hits[:3]}")
    _f = _ax_patch(_ax_files(), "b2bcollect_build/cdp_base.py",
                   'owner._waiting_human = str(prev or "")', 'owner._waiting_human = ""')
    _f = _ax_patch(_f, "ali1688_build/ali1688_crawler.py",
                   'owner._waiting_human = str(prev or "")', 'owner._waiting_human = ""')
    _hits = gate.check_human_wait_lamp_gate(_f)
    _assert(any("human_wait_release 走出" in h for h in _hits),
            f"「归还」退化成「清空」：形状合法（在块内）、逐字节仍相同，"
            f"只有灯的状态机对拍能看见这一刀：{_hits[:3]}")
    _assert(not any("不是逐字节相同" in h for h in _hits),
            f"这一刀两份同步改，逐字节对拍不该抢状态机对拍的案子：{_hits[:3]}")

    # 7) 词表被清空（两份同步）：「等登录」退到保守那一档，两道措辞判据必须响
    _f = _ax_patch(_ax_files(), "b2bcollect_build/cdp_base.py",
                   'HUMAN_WAIT_LOGIN_WORDS = ("登录", "扫码", "未登录", "会话失效", "空壳")',
                   "HUMAN_WAIT_LOGIN_WORDS = ()")
    _f = _ax_patch(_f, "ali1688_build/ali1688_crawler.py",
                   'HUMAN_WAIT_LOGIN_WORDS = ("登录", "扫码", "未登录", "会话失效", "空壳")',
                   "HUMAN_WAIT_LOGIN_WORDS = ()")
    _hits = gate.check_human_wait_lamp_gate(_f)
    _assert(any("念成同一句" in h for h in _hits),
            f"词表清空后「等登录」与「过验证」塌成一句，必须响：{_hits[:3]}")

    # 8) 空转保险
    _hits = gate.check_human_wait_lamp_gate({})
    _assert(any("一份源码都没读到" in h for h in _hits), f"空视野不许绿：{_hits[:2]}")
    _hits = gate.check_human_wait_lamp_gate({Path("b2bcollect_build/zz_ay_broken.py"): "def ("})
    _assert(any("扫了 0 份源码" in h for h in _hits),
            f"全解不过不许默默少一份，要报「等于没跑」：{_hits[:2]}")
    _hits = gate.check_human_wait_lamp_gate({Path("b2bcollect_build/other.py"): "x = 1\n"})
    _assert(any("真源不在位" in h for h in _hits), f"真源缺席不许绿：{_hits[:2]}")


def test_trap_handler_defined_gate():
    """AZ. trap 触发时它调到的函数必须已经存在（第 39 条硬门）。"""
    print("\n═══ AZ. 陷阱不许撞 command not found：trap 只许调到已定义的函数（第 39 条硬门）═══")
    gate = _manifest_gate()

    # 1) 现状：真源全绿，而且这一格真的看到了陷阱（看不到就会假绿）
    _assert_eq(gate.check_trap_handler_defined(), [], "第 39 条：trap 处理器在真源上必须全绿")
    _seen = 0
    for _rel in gate.SHELL_SOURCES:
        _p = gate.ROOT / _rel
        if not _p.is_file():
            continue
        _seen += sum(1 for _ln in _p.read_text(encoding="utf-8", errors="replace").splitlines()
                     if gate.SHELL_TRAP_PAT.match(_ln))
    _assert(_seen >= 5, f"这一格在真源上只看到 {_seen} 句 trap，与交付链实际登记的量级不符："
                        "视野断了它也会报绿（5 是本轮逐份点名数出来的下限，取制品 2 句、"
                        "批跑 1 句、本机打包 2 句）")

    # 2) 本轮那个缺陷的原形：函数写在 trap 之后。逐字节只差一个位置，行为完全两样，
    # 所以只能拿这种成对夹具对拍——拿「真源现在绿」当判据的下一位会把这一族改回去。
    _old = ("#!/usr/bin/env bash\nset -Eeuo pipefail\n"
            "on_exit() {\n  bg_reap\n  return 0\n}\n"
            "trap 'on_exit' EXIT\n"
            "bg_reap() {\n  :\n}\nexit 7\n")
    _hits = gate.check_trap_handler_defined(texts=[("zz_trap_old.sh", _old)])
    _assert(any("bg_reap" in h and "第 8 行" in h for h in _hits),
            f"「函数写在 trap 之后」必须响、要点名是哪个函数（实际={_hits[:1] or '一声不响'}）")
    _new = ("#!/usr/bin/env bash\nset -Eeuo pipefail\n"
            "bg_reap() {\n  :\n}\n"
            "on_exit() {\n  bg_reap\n  return 0\n}\n"
            "trap 'on_exit' EXIT\nexit 7\n")
    _assert_eq(gate.check_trap_handler_defined(texts=[("zz_trap_new.sh", _new)]), [],
               "同样的调用关系、只是把函数搬到 trap 之前，就不许响（本轮给出的修法）")

    # 3) 传递闭包：只看直接调用的判据会在这一档假绿
    _deep = ("set -Eeuo pipefail\na() {\n  b\n}\n"
             "trap 'a' EXIT\nb() {\n  :\n}\n")
    _hits = gate.check_trap_handler_defined(texts=[("zz_trap_deep.sh", _deep)])
    _assert(any("b()" in h for h in _hits),
            f"隔一层的调用也要查到（实际={_hits[:1] or '一声不响'}）")

    # 4) 注释不是调用：真源里就有一句「调用点在下面的 on_exit」，拿它当调用会在合格脚本上假响
    _doc = ("set -Eeuo pipefail\non_exit() {\n  # 收口统一走 later()，见下面那一段\n  :\n}\n"
            "trap 'on_exit' EXIT\nlater() {\n  :\n}\n")
    _assert_eq(gate.check_trap_handler_defined(texts=[("zz_trap_doc.sh", _doc)]), [],
               "只出现在注释里的名字不算调用（误伤一次，下一个人就把判据改松）")

    # 5) 函数体切不出来要报「判不了」，不许静默跳过（25/26/28 条同一课）
    _broken = "set -Eeuo pipefail\non_exit() {\n  later\n}\ntrap 'on_exit' EXIT\nlater() {\n  {\n"
    _hits = gate.check_trap_handler_defined(texts=[("zz_trap_unbalanced.sh", _broken)])
    _assert(any("没配平" in h for h in _hits),
            f"花括号配不了平要响「这一格判不了」（实际={_hits[:1] or '一声不响'}）")

    # 6) 空视野不许绿
    _hits = gate.check_trap_handler_defined(sources=(), texts=())
    _assert(any("不许报绿" in h for h in _hits),
            f"一份 shell 都没读到时不许绿（实际={_hits[:1] or '一声不响'}）")

    # 7) 本轮那次搬迁必须还在：取制品脚本里 bg_reap 的定义要在 trap 登记之上。
    # 这一档不拿合成代码对拍（上面那些已经够），它只钉「真源今天修的那一处没漂回去」。
    _t = (gate.ROOT / "debug" / "fetch_b2bcollect_artifacts.sh").read_text(
        encoding="utf-8", errors="replace").splitlines()
    _trapln = next((i for i, ln in enumerate(_t, 1) if "trap 'on_exit' EXIT" in ln), 0)
    _defln = next((i for i, ln in enumerate(_t, 1) if ln.startswith("bg_reap() {")), 0)
    _assert(0 < _defln < _trapln,
            f"bg_reap 的定义必须在 trap 登记之前（实得定义第 {_defln} 行、trap 第 {_trapln} 行）："
            "倒回去就是本轮那三件代价（退码被 127 顶掉、清理不跑、零异常日志）")


def test_package_weight_gate():
    print("\n═══ BA. 产物重量门（第 40 条硬门）═══")
    # 需求 5「工具减重」的守卫。写这一节的理由和它拦的东西同族：上一轮 pandas 就是
    # 靠着「清单里写了一行 --hidden-import pandas」+「某个进包模块的 `if __name__` 里有一句
    # import pandas」两头一起混进产物的，而当时 39 条硬门全绿——因为 import 对拍那只扫
    # 顶层。一条拦不住的形状，就得两面都钉：构建前的参数与闭包、构建后的真实字节。
    gate = _manifest_gate()
    gate_src = (_REPO / ".github" / "scripts" / "check_packaging_manifest.py").read_text(encoding="utf-8")
    for _fn in ("check_package_weight_static", "check_package_weight_artifact",
                "_weight_analyzed_sources", "artifact_resource_root"):
        _assert(callable(getattr(gate, _fn, None)), f"门模块里有 {_fn}（能被单独喂夹具）")
    _at = gate_src.find("weight = check_package_weight_static()")
    _assert(_at > 0, "硬门 main() 里真的调了构建前那一面（函数在而不调＝这条门从没跑过）")
    _assert("bad += 1" in gate_src[_at:_at + 700],
            "构建前那一面的结论要并进退出码（只 print 不改退出码的门等于公告栏）")
    _assert("--artifact-weight" in gate_src and "::error::产物重量" in gate_src,
            "构建后那一面要有能被打包脚本调起的开关，且红话要进云端 annotation")

    # ── 1) 真仓库全绿，而且绿得有内容（名单读空时的「全部合格」是假绿，第 22 条同族）──
    _srcs = gate._weight_analyzed_sources()
    _assert_eq(gate.check_package_weight_static(), [], "今天的仓库与三处入口都不欠重量")
    # 拿 posix 风格比后缀，不去猜「这台机器上 _rel 会给我正斜杠还是反斜杠」：分隔符
    # 口径写进判据就是给自己埋一个「换平台就假红/假绿」的坑。
    _names = {p.as_posix() for p in _srcs}
    _assert(len(_srcs) >= 50, f"分析闭包名单不许空转（实际 {len(_srcs)} 份）")
    _assert(any(n.endswith("enhanced_cleaner.py") for n in _names),
            "enhanced_cleaner 必须在名单里——它正是上一轮混进 pandas 那一份")
    _assert(any(n.endswith("enf_clean/normalize.py") for n in _names),
            "带点的包名（enf_clean.normalize）也要解析成文件进了名单")
    _assert(not any("clean_three_files" in n for n in _names),
            "--add-data 整目录带走、但运行时没人 import 的那三份不计算闭包：把它们算进来，"
            "这条门就会为了三份死文件要求全仓不许出现 import pandas（没人能照着修的红）")

    # ── 2) 参数面：三个禁写法各响一条，换写法绕过也要响 ──
    _orig_callers = gate.CALLERS
    try:
        with tempfile.TemporaryDirectory() as td:
            fake = Path(td) / "entry.sh"
            for flag, value, needle in (("--hidden-import", "pandas", "pandas 连带 numpy"),
                                        ("--collect-all", "PyQt5", "collect-all 连 qml"),
                                        ("--collect-submodules", "PyQt5", "换写法绕过")):
                fake.write_text(f"pyinstaller {flag} {value} --name X entry.py\n", encoding="utf-8")
                gate.CALLERS = {"合成入口": fake}
                _got = gate.check_package_weight_static()
                _assert_eq(len(_got), 1, f"喂进 `{flag} {value}` 只响那一条（实际={_got}）")
                _assert(needle in _got[0], f"红话要点名为什么它是白拿（实际={_got[0]}）")
            # 反向对照：同一句话写在注释里不算违规（本机脚本里就写着「不再 --collect-all
            # PyQt5」这句理由，误判它就是把人往错方向带）
            fake.write_text("# 不再 --collect-all PyQt5（已删）\npyinstaller --name X entry.py\n",
                            encoding="utf-8")
            gate.CALLERS = {"合成入口": fake}
            _assert_eq(gate.check_package_weight_static(), [],
                       "注释里提到禁写法不许响：判的是参数，不是这几个字有没有出现过")
    finally:
        gate.CALLERS = _orig_callers

    # ── 3) 闭包面：藏在函数体里的一句 import 也要抓住（上一轮就是从这一缝溜进去的）──
    _orig_sources = gate._weight_analyzed_sources
    try:
        with tempfile.TemporaryDirectory() as td:
            bad = Path(td) / "sneaky.py"
            bad.write_text("def _selftest():\n    import pandas as pd\n    return pd\n",
                           encoding="utf-8")
            gate._weight_analyzed_sources = lambda: [bad]
            _got = gate.check_package_weight_static()
            _assert_eq(len(_got), 1, f"函数体内的 import 同样算带进产物（实际={_got}）")
            _assert("写在函数体" in _got[0] and "第 2 行" in _got[0],
                    f"红话要点名行号与「为什么顶层门看不见它」（实际={_got[0]}）")
    finally:
        gate._weight_analyzed_sources = _orig_sources

    # ── 4) 实测面：拿真字节验白拿名单、未登记、越上限与「读不到根」四种形状 ──
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "B2bCollectTool" / "_internal"
        (root / "pandas").mkdir(parents=True)
        (root / "PyQt5" / "Qt5" / "qml").mkdir(parents=True)
        (root / "PyQt5" / "Qt5" / "translations").mkdir(parents=True)
        for rel, mb in (("pandas/in.csv", 1), ("PyQt5/Qt5/qml/a", 1),
                        ("PyQt5/Qt5/translations/b", 1), ("stranger/c", 4),
                        ("selenium/d", gate.WEIGHT_BUDGET_MB["selenium"]["win"] + 2)):
            f = root / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            with f.open("wb") as fh:                 # 稀疏文件：只花逻辑字节，不落盘真数据
                fh.truncate(int(mb * 1024 * 1024))
        _got, _rep = gate.check_package_weight_artifact(root.parent, "夹具")
        _assert(sum(1 for x in _got if "产物里有" in x) == 3,
                f"pandas/qml/translations 三处白拿各响一条（实际={_got}）")
        _assert(any("stranger" in x and "却没登记" in x for x in _got),
                "超过披露线却没登记的新成分要指名道姓（这就是「包为什么胖了」那句问句的答案）")
        _assert(any("selenium" in x and "登记的上限" in x and "Windows" in x for x in _got),
                f"已登记的成分长过自己的上限也要响，而且要点名是哪一平台的上限（实得 {_got}）："
                "登记一次不是永久通行证")
        _assert(any("合计" in x for x in _rep) and any("未登记" in x for x in _rep),
                f"不管红绿都要把成分账打出来（实际报告={_rep[:2]}）")
        # 干净产物：只留一份在簿的小成分，两面都不许响
        import shutil
        shutil.rmtree(root / "pandas")
        shutil.rmtree(root / "PyQt5")
        shutil.rmtree(root / "stranger")
        shutil.rmtree(root / "selenium")
        (root / "ok").mkdir()
        with (root / "ok" / "e").open("wb") as fh:
            fh.truncate(1024 * 1024)
        _assert_eq(gate.check_package_weight_artifact(root.parent, "夹具")[0], [],
                   "去掉白拿与越限成分后就该全绿（拦得住也要放得行）")
        _probs, _rep2 = gate.check_package_weight_artifact(Path(td) / "不存在的产物", "夹具")
        _assert(_probs and "读不到产物体积视野" in _probs[0],
                "路径给错时必须开口，不能默默交一份「零问题」的答卷（产物不在就别宣布减重成功；"
                "这一句文案随口径从「资源根」改成「体积视野」，判据跟着真源走）")

    # ── 5) 剪裁链路（需求 5 第三刀）：清单单一真源，三处入口只调不抄，先剪后称 ──
    # 由来（2026-11-19 产物 otool 实测）：前两刀之后 numpy(约 10MB)、translations(约 6MB)
    # 与 QtQuick 一族(约 11MB) 仍常驻产物。numpy 是从 PIL._typing 的 TYPE_CHECKING 块被
    # 拖进（那段运行时永不执行，--exclude-module numpy 排除即可）；其余是 Qt hook 的
    # 连带收件——platforms/libqwebgl.dylib（WebGL 平台插件，桌面 Widgets 工具永不选用）
    # 链着整条 QML 渲染栈，translations 按 hook 的 metadata 规则收，hook 都没有排除
    # 开关，只能构建后剪。清单写死在门模块单一真源，三处入口都调 --prune-artifact 走
    # 同一个实现——抄三遍迟早分叉（本项目被咬过最多次的形状）；剪完由白拿名单守着。
    _assert(callable(getattr(gate, "prune_artifact", None)),
            "门模块里有 prune_artifact（可被三处入口调起；函数在而入口不接线＝这条链从没跑过）")
    _PRUNE_KEY = (
        "PyQt5/Qt5/plugins/platforms/libqwebgl.dylib",
        "PyQt5/Qt5/lib/QtQuick.framework",
        "PyQt5/Qt5/plugins/platforms/qwebgl.dll",
        "PyQt5/Qt5/translations",
    )

    def _prune_problems(items):
        got = []
        for rel in _PRUNE_KEY:
            if rel not in items:
                got.append(f"剪裁清单里没有 {rel}：webgl 插件拖进的 QML 栈与从未生效过的"
                           "Qt 翻译就会常驻产物")
        if any("\\" in x or x.startswith("/") for x in items):
            got.append("剪裁清单条目必须是相对 posix 路径（joinpath 按斜杠拆）：绝对路径"
                       "或反斜杠在另一台机器上是剪不到东西的哑条目")
        return got

    _assert_eq(_prune_problems(gate.ARTIFACT_PRUNE), [],
               "今天的剪裁清单四条关键收件都在位（mac 与 Windows 两套形状共用同一份清单）")
    # 白拿名单与剪裁清单要对账：translations 两边都挂着——剪掉让它这轮不红，白名单
    # 守「再长出来就红」。只挂一边，另一边迟早变成没人接的空枪。
    _assert("--prune-artifact" in gate.WEIGHT_FORBIDDEN_ARTIFACT.get("PyQt5/Qt5/translations", ""),
            "translations 的白拿理由要交代它是被 --prune-artifact 剪掉的：白拿名单与"
            "剪裁清单互为对账，只写一边另一边就成了没人接的空枪")
    # 函数级夹具：清单内的真被剪、清单外一个不碰、读不到产物返回 -1 好让入口并退出码。
    with tempfile.TemporaryDirectory() as td:
        _r = Path(td) / "B2bCollectTool" / "_internal"
        for rel in ("PyQt5/Qt5/plugins/platforms/libqwebgl.dylib",
                    "PyQt5/Qt5/translations/qtbase_zh_cn.qm", "keep.txt"):
            _f = _r / rel
            _f.parent.mkdir(parents=True, exist_ok=True)
            with _f.open("wb") as _fh:               # 稀疏 2MB：真实剪裁是 MB 级，
                _fh.truncate(2 * 1024 * 1024)        # int 截断后返回 0 会与「没剪到」分不开
        _cut = gate.prune_artifact(_r.parent, "夹具")
        _assert(_cut > 0, f"夹具里确有清单内成分，剪裁要报告正数（实际={_cut}）")
        _assert(not (_r / "PyQt5" / "Qt5" / "translations").exists(), "清单内的目录必须真被剪掉")
        _assert((_r / "keep.txt").exists(), "清单外的成分一个不许碰（剪裁不是格式化）")
        _assert(gate.prune_artifact(Path(td) / "不在的产物", "夹具") == -1,
                "读不到产物时返回 -1：入口把这一路并进退出码，而不是报个喜就过")

    def _order_problems(src):
        p_at = src.find("if args.prune_artifact:")
        w_at = src.find("if args.artifact_weight:")
        if p_at <= 0 or w_at <= p_at:
            return ["main() 里剪裁分支必须排在重量分支之前：先剪后称，顺序反了称重"
                    "称的就是没剪的产物"]
        if "bad += 1" not in src[p_at:p_at + 300]:
            return ["剪裁失败（-1）要并进退出码：只 print 不改退出码，红装绿卖"]
        return []

    _assert_eq(_order_problems(gate_src), [], "main() 的剪裁接线在位且先剪后称")
    _assert("TYPE_CHECKING" in gate.WEIGHT_FORBIDDEN_ARTIFACT.get("numpy", ""),
            "numpy 的白拿理由要写真根因（PIL._typing 的 TYPE_CHECKING 块拖进、运行时"
            "永不执行）：写「只有 pandas 要它」那种旧账会误导下一个想删 import 的人")
    # 三处入口接线（吃文本的判据函数：变异复验喂变异字符串，不动真交付物）。
    _entries = {
        "本机脚本": (_REPO / "debug" / "build_b2bcollect_mac_local.sh").read_text(encoding="utf-8"),
        "mac workflow": (_REPO / ".github" / "workflows" / "build-b2bcollect-mac.yml").read_text(encoding="utf-8"),
        "Windows workflow": (_REPO / ".github" / "workflows" / "build-b2bcollect.yml").read_text(encoding="utf-8"),
    }
    _PRUNE_CALL = {
        "本机脚本": "--prune-artifact dist/B2bCollectTool.app --artifact-weight dist/B2bCollectTool.app",
        "mac workflow": "--prune-artifact dist/B2bCollectTool.app --artifact-weight dist/B2bCollectTool.app",
        "Windows workflow": "--prune-artifact dist/B2bCollectTool --artifact-weight dist/B2bCollectTool",
    }

    def _entry_problems(texts):
        got = []
        for label, t in texts.items():
            if "--exclude-module numpy" not in t:
                got.append(f"{label}：没有 --exclude-module numpy——PIL._typing 的 "
                           "TYPE_CHECKING 块运行时永不执行，排除零风险；摘了它 numpy(约"
                           "10MB) 就回流")
            if _PRUNE_CALL[label] not in t:
                got.append(f"{label}：没有「{_PRUNE_CALL[label]}」这条同次调用——剪裁"
                           "（--prune-artifact）与称重（--artifact-weight）必须同一次调用"
                           "指向同一产物：剪完不马上称重，剪裁链路就没有验收")
            # 拦的是「抄清单本体」＝把条目路径抄进入口；注释与 echo 里提到清单名只是
            # 指路，不算（与第 2 小节同款教训：判的是参数，不是这几个字有没有出现过）。
            for rel in _PRUNE_KEY:
                if rel in t:
                    got.append(f"{label}：出现了剪裁清单条目「{rel}」的字节——剪裁清单是"
                               "单一真源，入口只调不抄（抄一遍就是第二份真源，改清单时"
                               "入口那份不会跟着改，迟早分叉）")
        return got

    _assert_eq(_entry_problems(_entries), [],
               "三处入口的排除/剪裁/称重接线齐全，且都在只调不抄")
    # 反向对照：改一个与接线无关的字段不许响（误报的门会被当噪音关掉）。
    _assert_eq(_entry_problems({**_entries, "mac workflow":
               _entries["mac workflow"].replace("--artifact-label Mac", "--artifact-label macOS", 1)}),
               [], "改产物标签这种无关字段不许响")
    # ── 变异复验：清单、接线、main 三层各挑要害，红话点名代价 ──
    for label, items, needle in (
        ("删 translations 条目",
         tuple(x for x in gate.ARTIFACT_PRUNE if x != "PyQt5/Qt5/translations"),
         "没有 PyQt5/Qt5/translations"),
        ("删 libqwebgl 条目",
         tuple(x for x in gate.ARTIFACT_PRUNE if "libqwebgl" not in x),
         "没有 PyQt5/Qt5/plugins/platforms/libqwebgl.dylib"),
        ("写成反斜杠条目", ("PyQt5\\Qt5\\translations",), "相对 posix 路径"),
    ):
        _got = _prune_problems(items)
        _assert(any(needle in g for g in _got),
                f"清单变异「{label}」必须被杀红（实际={_got}）")
    for label, texts, needle in (
        ("mac 摘掉剪裁调用", {**_entries, "mac workflow": _entries["mac workflow"].replace(
            _PRUNE_CALL["mac workflow"] + " \\", "")}, "同次调用"),
        ("mac 剪完不称重", {**_entries, "mac workflow": _entries["mac workflow"].replace(
            _PRUNE_CALL["mac workflow"], "--prune-artifact dist/B2bCollectTool.app", 1)}, "同次调用"),
        ("Windows 摘掉 exclude numpy", {**_entries, "Windows workflow": _entries[
            "Windows workflow"].replace("--exclude-module numpy ", "")}, "没有 --exclude-module numpy"),
        ("本机脚本抄清单条目", {**_entries, "本机脚本": _entries["本机脚本"] +
            "\nPyQt5/Qt5/plugins/platforms/libqwebgl.dylib"}, "只调不抄"),
    ):
        _mut = _entry_problems(texts)
        _assert(any(needle in g for g in _mut),
                f"入口变异「{label}」必须被杀红（实际={_mut}）")
    # main() 接线三路：分支摘掉 / 失败不进退出码 / 先称后剪，每一路都要红得点名。
    _mut = gate_src.replace("    if args.prune_artifact:", "    if False:", 1)
    _assert(_mut != gate_src, "变异「main 剪裁分支摘掉」锚点没对上（变异等于空跑）")
    _got = _order_problems(_mut)
    _assert(any("剪裁分支" in g for g in _got),
            f"变异「main 剪裁分支摘掉」必须被杀红（实际={_got}）")
    _mut = gate_src.replace("if _mb < 0:\n            bad += 1", "if _mb < 0:\n            pass", 1)
    _assert(_mut != gate_src, "变异「-1 不进退出码」锚点没对上（变异等于空跑）")
    _got = _order_problems(_mut)
    _assert(any("并进退出码" in g for g in _got),
            f"变异「-1 不进退出码」必须被杀红（实际={_got}）")
    _p_at = gate_src.find("if args.prune_artifact:")
    _w_at = gate_src.find("if args.artifact_weight:")
    _end = gate_src.find("    if bad:", _w_at)
    _mut = gate_src[:_p_at] + gate_src[_w_at:_end] + gate_src[_p_at:_w_at] + gate_src[_end:]
    _assert(_mut != gate_src, "变异「先称后剪」锚点没对上（变异等于空跑）")
    _got = _order_problems(_mut)
    _assert(any("排在重量分支之前" in g for g in _got),
            f"变异「先称后剪」必须被杀红（实际={_got}）")

    # ── 6) 体积口径（2026-11-19 拿真产物对账后补）：视野要含三处、链接不双计、主程序入账 ──
    # 由来：旧口径只看 Contents/Resources，实测把「合计」报成 109MB（真身 138MB），还把
    # QtGui/QtCore/libcrypto 这些**符号链接别名**当成 8 个「未登记新成分」——而真身所在的
    # Frameworks/PyQt5（39MB）反倒因为不在视野里没进账，14MB 的主程序（MacOS/）压根没被
    # 看过。数字错，登记的上限与「减了多少重」就全是假的，所以这一节比前三刀都重要。
    for _fn in ("artifact_measure_roots", "artifact_candidate_paths", "measure_artifact"):
        _assert(callable(getattr(gate, _fn, None)), f"门模块里有 {_fn}（体积口径的唯一把手）")

    def _build_fake_app(td):
        """造一个 mac 形状的最小产物：真身在 Frameworks、Resources 只放链接壳、主程序在 MacOS。"""
        cont = Path(td) / "FakeTool.app" / "Contents"
        body = cont / "Frameworks" / "PyQt5" / "Qt5" / "lib" / "QtCore.framework" / "Versions" / "5"
        body.mkdir(parents=True)
        (body / "QtCore").write_bytes(b"q" * (3 * 1024 * 1024))            # 真身 3MB
        shell = cont / "Resources" / "PyQt5" / "Qt5" / "lib"
        shell.mkdir(parents=True)
        os.symlink("../../../../Frameworks/PyQt5/Qt5/lib/QtCore.framework",
                   shell / "QtCore.framework")                             # 链接壳：同一份字节
        sel = cont / "Frameworks" / "selenium" / "webdriver"
        sel.mkdir(parents=True)
        (sel / "driver.bin").write_bytes(b"q" * (4 * 1024 * 1024))         # 子目录不许当成分名
        exe = cont / "MacOS"
        exe.mkdir(parents=True)
        (exe / "FakeTool").write_bytes(b"q" * (2 * 1024 * 1024))           # 主程序要在账
        return cont

    with tempfile.TemporaryDirectory() as td:
        _build_fake_app(td)
        _comps, _total = gate.measure_artifact(Path(td) / "FakeTool.app")
        _assert_eq(round(_comps.get("PyQt5", 0)), 3,
                   "链接壳不许让真身算两遍（3MB 就是 3MB）：旧口径正是靠跟随链接把 QtGui 多算了 6MB")
        _assert(not ({"Versions", "webdriver", "QtCore.framework"} & set(_comps)),
                f"成分名必须取「视野下的第一段」：拿子目录名当成分会把 selenium 拆成 webdriver、"
                f"把 framework 拆成 Versions（实际={sorted(_comps)}）——本轮真实踩到，旧写法"
                "多剥一层就把真成分全弄散了")
        _assert_eq(round(_comps.get("selenium", 0)), 4,
                   "Frameworks/selenium/webdriver/* 要归到同一个 selenium")
        _assert_eq(round(_comps.get("FakeTool", 0)), 2,
                   "MacOS/ 下的主程序必须在账（旧口径漏掉的就是那一格 14MB）")
        _assert_eq(round(_total), 9, f"合计就是真实字节 3+4+2（实际={_total:.1f}）")
        _assert_eq(sorted(r.name for r in gate.artifact_measure_roots(Path(td) / "FakeTool.app")),
                   ["Frameworks", "MacOS", "Resources"],
                   "mac 的 .app 体积视野必须三处齐：少一处就少一类成分")
        _win = Path(td) / "WinTool" / "_internal" / "pandas"
        _win.mkdir(parents=True)
        (_win / "x.pyd").write_bytes(b"q" * (4 * 1024 * 1024))
        _assert_eq([r.name for r in gate.artifact_measure_roots(Path(td) / "WinTool")], ["_internal"],
                   "Windows onedir 仍只看 _internal（那里不用符号链接，旧行为不许变）")
        # 白拿名单也要跨视野：真身只在 Frameworks 一侧时，只看 Resources 就会「自宣布干净」。
        _pd = Path(td) / "FakeTool.app" / "Contents" / "Frameworks" / "pandas"
        _pd.mkdir(parents=True)
        (_pd / "x.pyd").write_bytes(b"q" * 1024)
        _gp, _ = gate.check_package_weight_artifact(Path(td) / "FakeTool.app", "夹具")
        _assert(any("pandas" in x for x in _gp),
                f"白拿名单要在全部视野里问过：pandas 只在 Frameworks 一侧也必须响（实际={_gp}）")

    def _prune_two_sides(td):
        """两侧各一份 translations：只剪一侧就是「剪了壳、留了身」，白拿名单还会报脏。"""
        cont = Path(td) / "FakeTool.app" / "Contents"
        for side in ("Frameworks", "Resources"):
            d = cont / side / "PyQt5" / "Qt5" / "translations"
            d.mkdir(parents=True)
            (d / "a.qm").write_bytes(b"q" * (2 * 1024 * 1024))
        return cont

    with tempfile.TemporaryDirectory() as td:
        cont = _prune_two_sides(td)
        _cand = gate.artifact_candidate_paths(
            "PyQt5/Qt5/translations", gate.artifact_measure_roots(Path(td) / "FakeTool.app"))
        _assert_eq(len(_cand), 2, f"两侧都要有候选落点（实际={_cand}）")
        _cut = gate.prune_artifact(Path(td) / "FakeTool.app", "夹具")
        _assert(_cut >= 2, f"两侧各 2MB，剪掉的要按真字节合计（实际={_cut}）")
        for side in ("Frameworks", "Resources"):
            _assert(not (cont / side / "PyQt5" / "Qt5" / "translations").exists(),
                    f"{side} 侧的 translations 必须真被剪掉")

    # 真实产物里两侧不是「两份真字节」，而是「一份真身 + 一个链接目录」：2026-11-19 本机
    # 打包就崩在这一格（`shutil.rmtree` 对符号链接目录直接抛 Cannot call rmtree on a
    # symbolic link）。上一段那个夹具造的是两个真目录，恰好碰不到这一档——判据里的
    # 夹具要按**真产物形状**造，否则「两侧都剪」这句话只覆盖了形状的一半。
    with tempfile.TemporaryDirectory() as td:
        cont = Path(td) / "FakeTool.app" / "Contents"
        real = cont / "Resources" / "PyQt5" / "Qt5" / "translations"
        real.mkdir(parents=True)
        (real / "a.qm").write_bytes(b"q" * (2 * 1024 * 1024))
        (cont / "Frameworks" / "PyQt5" / "Qt5").mkdir(parents=True)
        _link = cont / "Frameworks" / "PyQt5" / "Qt5" / "translations"
        # 相对链接的层数基准是「链接所在的那一个目录」：从 Contents/Frameworks/PyQt5/Qt5 往回
        # 三层才到 Contents。本轮这里先写成了四层，链子虚 → `exists()` 为假 → 整格被跳过，
        # 看起来像「判据过了」而其实什么也没判——先自己验一句再往下跑。
        _link.symlink_to(os.path.relpath(real, _link.parent))
        _assert(_link.is_symlink() and _link.is_dir(),
                "夹具自证：链接目录要真能解析到真身（虚了就测不到 rmtree 那一格）")
        _crash = ""
        try:
            _cut2 = gate.prune_artifact(Path(td) / "FakeTool.app", "夹具")
        except OSError as e:
            _cut2, _crash = -1, f"{type(e).__name__}: {e}"
        _assert(not _crash, f"链接目录不许让剪裁崩掉（旧写法就是这么崩的，实际={_crash}）")
        _assert(_cut2 == 2, f"字节只许算真身那一侧（链接壳不重复计费，期望 2，实际={_cut2}）")
        _assert(not _link.is_symlink(), "链接壳也必须摘掉：留着就是一指向不存在路径的挂链接")
        _assert(not real.exists(), "真身那一侧要真被剪掉")
        # 再跑一遍：上一轮只剩「真身已删、链接还在」的挂链接状态时也不许崩
        # （重量门与剪裁是两次独立调用，挂链接在真产物里长期存在）。
        _link.symlink_to("./不存在")
        _again = gate.prune_artifact(Path(td) / "FakeTool.app", "夹具")
        _assert(_again == 0, f"挂链接那一格不重复计费、也不报错（实际={_again}）")

    # 反向对照：把口径改回「只看 Resources」那一版旧写法，同一份夹具必须量出缺项。
    # 这一路证明上面的断言不是恒真：旧口径真的会漏，判据真的能分辨。
    _orig_roots = gate.artifact_measure_roots
    try:
        gate.artifact_measure_roots = lambda a: [Path(a) / "Contents" / "Resources"]
        with tempfile.TemporaryDirectory() as td:
            _build_fake_app(td)
            _c2, _t2 = gate.measure_artifact(Path(td) / "FakeTool.app")
            _assert(_t2 < 9 or "FakeTool" not in _c2 or "PyQt5" not in _c2,
                    f"旧口径（只看 Resources）量出来就是少东西（实际合计={_t2:.1f}、成分={sorted(_c2)}）："
                    "如果这一路也报 9MB，上面那些断言就是永真式")
    finally:
        gate.artifact_measure_roots = _orig_roots

    # 本轮实测回填的账要钉住：上限不是拍脑袋，是**两平台各自量出来的**；谁改回旧数字就得解释。
    # 2026-11-19 云端两路同红之后，这一格的形状本身就是判据对象：旧写法把「本机那一份」的名字
    # 与数字抄进表里，换一台打包机（Mac runner 是 Python 3.11，本机是 3.13）或换一个平台形状
    # （Windows 的 OpenSSL 叫 libcrypto-3.dll），门就报「未登记」——而拦住的正是交付。
    for name, mac_cap, win_cap, why_in_cap in (
            ("PyQt5", 45, 70, "framework"),          # mac 是 framework，win 是 bin 下一排 dll
            ("B2bCollectTool", 18, None, "bootloader"),
            ("python3NN", 10, 10, "CPython"),
            ("libcryptoN", 8, 8, "OpenSSL")):
        entry = gate.WEIGHT_BUDGET_MB.get(name)
        _assert(isinstance(entry, dict),
                f"成分账里 {name} 必须是「mac/win/why」那一格（实得 {type(entry).__name__}）："
                f"退回旧写法（只填本机一套数字）就是那一轮云端两路红的形状")
        if not isinstance(entry, dict):
            continue
        _assert(entry["mac"] == mac_cap and entry["win"] == win_cap,
                f"{name} 的两平台上限要停在实测那一档（mac {mac_cap} / win {win_cap}，"
                f"实得 mac {entry['mac']} / win {entry['win']}）：旧上限 95 是 --collect-all 时代的"
                f"整包 Qt，拿它当今日上限＝这道门对 PyQt5 永不生效；只填一台＝另一平台必红")
        _assert(why_in_cap in entry["why"],
                f"{name} 的理由要念得出它凭什么值这几兆（要含「{why_in_cap}」，实得 {entry['why']}）")
    _assert(not any("/" in k for k in gate.WEIGHT_BUDGET_MB),
            "登记表全部用「视野下第一段」的成分名：写带斜杠的路径永远碰不到 measure_artifact 的键，"
            "那条登记就是挂着但从不生效的空账")

    # ── 7) 两平台各量各的账：拿同一份超过 mac 上限、但 win 允许的数字，两边结果必须不同 ──
    # 为什么这一路非验不可：「表里有两列」不算修好，只有当查账真的按产物那一侧取上限，
    # 云端那两路才不会再被同一把错误的尺卡住。反向也验：mac 专属那一格长到 Windows 里必须响。
    _selenium_win = gate.WEIGHT_BUDGET_MB["selenium"]["win"]      # 32
    _selenium_mac = gate.WEIGHT_BUDGET_MB["selenium"]["mac"]      # 26
    # 夹具取的值：严格大于 mac 上限、又不超 win 上限。拿「正好等于 mac 上限」造现场是假绿：
    # 门的判据是严格大于，那一路永远不响，看上去验了其实什么都没验。
    _probe = _selenium_mac + 2
    _assert(_probe <= _selenium_win,
            f"夹具要拿得到「两边不一样」的那一档（mac {_selenium_mac} / win {_selenium_win}，"
            f"探针 {_probe}）：两边相等就验不出尺用错了哪一列")
    with tempfile.TemporaryDirectory() as td:
        # Windows 形状（_internal）：selenium 取超过 mac 上限那一档→ 按 win 尺子必须放行
        win = Path(td) / "WinTool"
        (win / "_internal" / "selenium").mkdir(parents=True)
        with (win / "_internal" / "selenium" / "a").open("wb") as fh:
            fh.truncate(int(_probe * 1024 * 1024))
        _assert_eq(gate._artifact_platform(win), "win", "_internal 布局判为 Windows 形状")
        _assert_eq(gate.check_package_weight_artifact(win, "夹具")[0], [],
                   f"Windows 产物里 selenium {_probe}MB（超 mac 上限、未超 win 上限）"
                   f"→ 按 win 那一格放行：拿 mac 的尺卡 win 就是云端那一路报的假话")
        # 同一数字装进 macOS 形状（Contents/*）：必须响，而且要点名是哪个平台
        mac = Path(td) / "MacTool.app"
        (mac / "Contents" / "Resources" / "selenium").mkdir(parents=True)
        with (mac / "Contents" / "Resources" / "selenium" / "a").open("wb") as fh:
            fh.truncate(int(_probe * 1024 * 1024))
        _assert_eq(gate._artifact_platform(mac), "mac", "Contents 布局判为 macOS 形状")
        _mp, _mr = gate.check_package_weight_artifact(mac, "夹具")
        _assert(any("selenium" in x and "macOS" in x for x in _mp),
                f"同一份数字在 macOS 侧必须按 mac 上限响（实得 {_mp}）：两平台共用一把尺"
                f"就是把刚修好的那一半再弄坏")
        _assert(any("macOS 形状" in x for x in _mr),
                f"成分账抬头要念得出这是哪一份产物（实得 {_mr[:1]}）")
        # mac 专属那一格长到 Windows 里：必须响，且不许说成「未登记」（说错方向就没人能修）
        _cd = gate.WEIGHT_BUDGET_MB["chromedriver"]["win"]
        _assert(_cd is None, "前置：chromedriver 在 Windows 这一格登记的是 None")
        (win / "_internal" / "chromedriver").mkdir(parents=True)
        with (win / "_internal" / "chromedriver" / "a").open("wb") as fh:
            fh.truncate(int(18 * 1024 * 1024))
        _wp, _ = gate.check_package_weight_artifact(win, "夹具")
        _assert(any("chromedriver" in x and "从没量到过" in x for x in _wp),
                f"mac 专属成分长进 Windows 要说清是「这一平台没登记上限」（实得 {_wp}）："
                f"报成「未登记」或「白拿」都会把人往剪掉它那一刀带——剪掉就是没功能")

    # 别名必须认得两平台、两版本形状（这一格就是云端那一红的直接根因）
    for _real, _alias in (("python3__dot__11", "python3NN"), ("python3__dot__13", "python3NN"),
                         ("python311.dll", "python3NN"), ("python313.dll", "python3NN"),
                         ("libcrypto.3.dylib", "libcryptoN"), ("libcrypto-3.dll", "libcryptoN"),
                         # 兼容通道首运那一红的名义形状：CPython 3.8 链 OpenSSL 1.1（run
                         # 44598716037 实测 3MB）。不带这一例，正则改回去也是全绿。
                         ("libcrypto-1_1.dll", "libcryptoN")):
        _assert(gate._budget_key(_real) == _alias,
                f"产物里的 `{_real}` 要归到 `{_alias}` 那一格（实得 {gate._budget_key(_real)}）："
                f"归一化只认本机那一台机器看到的名字，就是云端那一轮的红")
        _assert(_alias in gate.WEIGHT_BUDGET_MB,
                f"别名 `{_alias}` 必须在成分账里在册")
    for _un in ("python3.11", "libcrypto10", "python3abc"):
        _assert(gate._budget_key(_un) == _un,
                f"别的成分名一律照原名对账（{_un} 被动了＝归一化正则泄到了不该管的地方）")
    # 反方向也要钉：`-x64` 那一档是**故意没放行**的（至今没有任何一份产物里出现过这个名字）。
    # 不钉住它，「顺手把正则放宽」会把这条门唯一的用处拆掉：新形状应当红一次、把真名
    # 念出来、逼来的人回答「这几兆凭什么进包」；预防性放行则是把这句话永远吞掉。
    # 将来云端真量到这个名字，就改正则并**同时**把这一例撑到上面那张正例表里去——
    # 两步必须一起动，只做一半的改动会在这里响。
    _unseen = "libcrypto-1_1-x64.dll"
    _assert(gate._budget_key(_unseen) == _unseen,
            f"没在云端量过的形状 `{_unseen}` 不许被归一（要放行就得先拿出一份真产物账本）")

    # 静态那一半：拿合成账本喂四形，每形都要响（只靠构建后那一面，本机永远绿、云端才红）
    _orig_budget = gate.WEIGHT_BUDGET_MB
    _orig_callers2 = gate.CALLERS
    _orig_sources2 = gate._weight_analyzed_sources
    try:
        gate.CALLERS = {}
        gate._weight_analyzed_sources = lambda: []
        _base = gate.check_package_weight_static()
        _assert_eq(_base, [], "今天的成分账形状合格（基线先坐实，下面比的是只多不少那一条）")
        for _tag, _mut, _needle in (
            ("旧形状：只填本机一套数字",
             {"PyQt5": (45, "framework")}, "形状不对"),
            ("把某个平台上限改成「不限」（0）",
             {"PyQt5": {"mac": 0, "win": 70, "why": "x"}}, "大于零的正整数"),
            ("两平台都写 None（这一格事实上消失了）",
             {"PyQt5": {"mac": None, "win": None, "why": "x"}}, "都登记 None"),
            ("理由空白（忘了登记与有意带这几兆分开）",
             {"PyQt5": {"mac": 45, "win": 70, "why": "  "}}, "没写登记理由"),
            ("登记了带版本号的具体名字（云端那一红的形状）",
             {"python3__dot__13": {"mac": 8, "win": 8, "why": "libpython"}}, "会被归一成"),
            ("别名被从账本里删掉（归一化指向一个没登记格）",
             {"PyQt5": {"mac": 45, "win": 70, "why": "framework"}}, "python3NN")):
            gate.WEIGHT_BUDGET_MB = _mut
            _got = gate.check_package_weight_static()
            _assert(any(_needle in x for x in _got),
                    f"{_tag}：静态那一半要响得出（实得 {_got}）")
    finally:
        gate.WEIGHT_BUDGET_MB = _orig_budget
        gate.CALLERS = _orig_callers2
        gate._weight_analyzed_sources = _orig_sources2


def test_installer_env_gate():
    print("\n═══ BB. 安装器运行环境体检门（需求 10）═══")
    # 需求 10「任何电脑都无需额外去解决这些问题」的守卫。安装器是一段本机编译不了
    # 的脚本（makensis 只在 Windows CI 上跑），判据只能钉在文本结构上——但钉什么
    # 有讲究：钉「这行字存在」拦不住写反，钉「执行顺序与参数顺序」才拦得住。本轮
    # 自己就写反过一次 IntCmp 的三个跳转（相等→老系统），后果是全量 Win10 用户被
    # 当成老系统拦下，而本机怎么试都绿——所以这一节对那行参数逐字钉死，不只钉存在。
    nsi = _REPO / "b2bcollect_build" / "installer" / "b2bcollect_setup.nsi"
    _real = nsi.read_text(encoding="utf-8-sig")
    _gi = (_REPO / ".gitignore").read_text(encoding="utf-8")

    def _code_lines(text):
        # 判据剔掉整行注释再判：解释纪律的那行注释里写着旧写法（把「为什么不能这样
        # 写」判成「这样写了」的门，第 31 条同族教训）。
        return [(n, ln.strip()) for n, ln in enumerate(text.splitlines(), 1)
                if ln.strip() and not ln.lstrip().startswith((";", "#"))]

    def _problems(text, gi):
        got = []
        code = _code_lines(text)
        strs = [s for _n, s in code]

        # 0) .onInit 恰好一个：0 个＝体检从没跑过；2 个＝makensis 直接编不过，
        #    那句报错只在云端看得见，离线先把它说成人话。
        oi = [i for i, s in enumerate(strs) if s.startswith("Function .onInit")]
        if len(oi) != 1:
            got.append(f".onInit 应该恰好一个（实际 {len(oi)} 个）：0 个＝体检从没跑过，"
                       "2 个＝makensis 编不过，红话只能等云端编译器给")
            return got
        oi = oi[0]
        fe = next((i for i in range(oi + 1, len(strs)) if strs[i] == "FunctionEnd"), None)
        if fe is None:
            got.append(".onInit 没有配对的 FunctionEnd")
            return got
        body = code[oi + 1:fe]

        # 1) 判据链的执行顺序：切 64 位视图 → 清 flag → 读 Win10+ 那个值 → 立刻判 flag
        #    → 读不到就退到老口径（MajorVersion/MinorVersion）→ 两条路汇到同一段读取。
        #    IfErrors 夹在别的注册表读取后面就作废（flag 被顶掉），顺序本身是判据。
        # 【需求 10B】IfErrors 的跳转目标从 envchk_old 改成 envchk_prewin10：旧写法把
        # 「读不到 CurrentMajorVersionNumber」直接等同于「拦下」，于是 Win7 永远走不到
        # 那句地板比较——兼容通道传什么地板都没用，安装器会自己拦死自己。
        seq = ["SetRegView 64", "ClearErrors", "ReadRegDWORD $R0",
               "IfErrors envchk_prewin10", "envchk_prewin10:", "ReadRegDWORD $R0",
               "envchk_common:", "ReadRegStr $8"]
        # 逐项**向后**找：允许同一个 needle 出现两次（两条读取路径上都是
        # ReadRegDWORD $R0）。上一版用「从开头找第一个」的 next()，第二个 $R0 又落回
        # 第一处的下标，于是 pos != sorted(pos) 永远成立——基线自己就红，而且它把
        # 后面每一路变异的红话都污染成同一句「顺序不对」（2026-11-20 实跑才暴露）。
        pos, _cur = [], -1
        for needle in seq:
            i = next((j for j in range(_cur + 1, len(body))
                      if body[j][1].startswith(needle)), None)
            if i is None:
                got.append(f".onInit 里找不到「{needle}」这句（或它没排在上一句之后）："
                           "体检链断了，Win7/8 会漏拦或 Win10 会误拦")
            else:
                _cur = i
            pos.append(i)
        if None not in pos and pos != sorted(pos):
            got.append(f"体检链顺序不对（{' → '.join(seq)} 必须按这个次序执行）："
                       "IfErrors 不紧跟在读版本那一句后面，error flag 就被中间那句读取顶掉，"
                       "老系统判据作废")
        # 2) 两级比较逐字钉：major 比完还要比 minor。三个跳转依次「相等/小于/大于」，
        #    两行都必须「相等→往下、小于→拦、大于→放行」。
        #    为什么必须有第二行：Vista 是 6.0、Win7 是 6.1、Win8 是 6.2，地板 6.1 若只比
        #    major，Vista 会在「相等」那一格被放行，然后到用户机器上变成一个打不开的图标。
        ic = next((s for _n, s in body if s.startswith("IntCmp $R0")), None)
        if ic != "IntCmp $R0 ${MIN_WIN_MAJOR} envchk_same_major envchk_old envchk_ok":
            got.append(f"major 那一格 IntCmp 必须逐字是「相等→envchk_same_major 小于→envchk_old "
                       f"大于→envchk_ok」，实际：{ic}——相等那一格写反，major=10 的正常 Win10 "
                       "会被当成老系统拦下；直接写 envchk_ok 则 Vista(6.0) 会被 6.1 的地板放行")
        ic2 = next((s for _n, s in body if s.startswith("IntCmp $R7")), None)
        if ic2 != "IntCmp $R7 ${MIN_WIN_MINOR} envchk_sp envchk_old envchk_ok":
            got.append(f"minor 那一格 IntCmp 必须逐字是「相等→envchk_sp 小于→envchk_old "
                       f"大于→envchk_ok」，实际：{ic2}——少了这一行，地板里的 minor 就是装饰品，"
                       "Vista(6.0) 与 Win7(6.1) 在判据里是同一台机器")
        # 3) 空串兜底 + 先记日志再下结论
        if not any(s.startswith('StrCmp $R0 "" envchk_old') for _n, s in body):
            got.append("IntCmp 前没有「空串也按老系统拦」的兜底：flag 之外的失败表示法"
                       "（不同 NSIS 版本口径有差异）会让判据漏空")
        first_log = next((j for j, (_n, s) in enumerate(body) if s.startswith("Call EnvLog")), None)
        verdict = next((j for j, (_n, s) in enumerate(body) if s.startswith("IntCmp")), None)
        if first_log is None or verdict is None or first_log > verdict:
            got.append("体检日志要写在结论之前（第一条 Call EnvLog 必须在 IntCmp 之前）："
                       "日志里没有这台机器的底细，「下次装不动问一句是什么系统」就问不出来")
        # 4) 视图还原：64 位视图不许带出 .onInit
        if not any(s == "SetRegView default" for _n, s in body):
            got.append(".onInit 结尾没有 SetRegView default：64 位视图会带进安装段，"
                       "连坐后面所有注册表写入")
        # 5) 老系统分支：静默不弹窗 + 文案给出的兼容包名不许烂尾
        om = next((j for j, (_n, s) in enumerate(body)
                   if s == 'MessageBox MB_OK|MB_ICONSTOP "$R6"'), None)
        isl = next((j for j, (_n, s) in enumerate(body)
                    if s.startswith("IfSilent envchk_abort")), None)
        if om is not None and (isl is None or isl > om):
            got.append("老系统分支里 IfSilent 必须排在 MessageBox 之前：静默安装（运维推送 /S）"
                       "里没人能点那个「确定」，安装进程会一直挂到被超时杀掉")
        if "B2bCollectToolSetup_Win7.exe" not in text:
            got.append("老系统文案里没有兼容版包名（B2bCollectToolSetup_Win7.exe）："
                       "给用户的出路要么兑现要么删掉，不许留一句指向不存在的东西的话")
        # 6) redist：在安装段里，InitPluginsDir 先于 $PLUGINSDIR，双目录判据都在
        sec = [i for i, s in enumerate(strs) if s.startswith("Section ") and "Uninstall" not in s]
        if not sec:
            got.append('找不到安装段（Section "主程序"）：redist 补装没有归属')
        else:
            s0 = sec[0]
            se = next((i for i in range(s0 + 1, len(strs)) if strs[i] == "SectionEnd"), None)
            sbody = code[s0 + 1:se]
            ipd = next((j for j, (_n, s) in enumerate(sbody) if s == "InitPluginsDir"), None)
            pusd = next((j for j, (_n, s) in enumerate(sbody)
                         if "$PLUGINSDIR" in s and s != "InitPluginsDir"), None)
            if pusd is not None and (ipd is None or ipd > pusd):
                got.append("InitPluginsDir 必须先于 $PLUGINSDIR 的第一次使用：没有它那个目录"
                           "还不存在，运行库会被 File 解到一个名字就叫「$PLUGINSDIR」的真实目录里去")
            for probe in ("$SYSDIR\\vcruntime140.dll", "$WINDIR\\Sysnative\\vcruntime140.dll"):
                if not any(probe in s for _n, s in sbody):
                    got.append(f"安装段里没有「{probe}」这条存在性判据：64 位 Windows 的系统目录得从 "
                               "Sysnative 问（$SYSDIR 在 32 位安装包里指 SysWOW64）——少这一问，"
                               "缺运行库的机器就会装出双击报错的产物")
            ex = next((j for j, (_n, s) in enumerate(sbody) if s.startswith("nsExec::ExecToLog")), None)
            pp = next((j for j, (_n, s) in enumerate(sbody) if s.startswith("Pop $")), None)
            if ex is not None and (pp is None or pp != ex + 1):
                got.append("nsExec::ExecToLog 的下一行必须是 Pop：退出码不收走就记不进体检日志，"
                           "装成没成只剩一句人话")
        # 7) 编译期开关：包内自带由 !if /FileExists 决定，缺文件走降级而不是编不过
        if "!if /FileExists" not in text:
            got.append("没有 !if /FileExists 的编译期开关：本机手工编译没下运行库时 makensis "
                       "直接编不过，降级分支（记日志继续装）就丢了")
        # 8) 25MB 的官方二进制不许入库：CI 现取，.gitignore 必须挡住整个 deps 目录
        if "b2bcollect_build/installer/deps/" not in gi:
            got.append(".gitignore 没有挡 b2bcollect_build/installer/deps/：25MB 的微软运行库"
                       "一旦被谁 git add 进来，clone 从此大 25MB，微软换版还要跟二进制 diff 斗")
        # 9) 【需求 10B】地板必须「可覆盖 + 有默认 + 文案同源」，且两条包共用同一份 nsi。
        #    为什么不复制一份 nsi：两份文件会把「缺依赖当场补装」「体检日志」「旧实例
        #    探测」这些踩过坑的逻辑再抄一遍，抄第二遍的东西一定会漂（同一教训见依赖
        #    清单收成一份文件那一格）。
        for _name in ("MIN_WIN_MAJOR", "MIN_WIN_MINOR"):
            if f"!ifndef {_name}" not in text:
                got.append(f"{_name} 没有包在 !ifndef 里：makensis 命令行上的 -D 先于脚本生效，"
                           "直接 !define 会把兼容通道传进来的地板**静默盖掉**——地板回到 10，"
                           "兼容包在 Win7 上照样拦，而构建全绿、日志一句不报")
        # 拦下文案的第一句必须引用地板宏。写死"Windows 10"的那一版，在兼容包里会把
        # 「这台明明够格」的用户按一句话拦走——弹窗说的必须就是 IntCmp 判的那件事。
        _first_line = next((s for _n, s in code
                            if s.startswith('StrCpy $R6 "') and "版本低于" in s), None)
        if _first_line is None:
            got.append("找不到「系统版本低于 …」那句拦下文案：体检拦下时不给原因，"
                       "用户只会看到一个没装成功的开始菜单图标")
        elif "${MIN_WIN_TEXT}" not in _first_line:
            got.append(f"拦下文案第一句没引用 ${{MIN_WIN_TEXT}}（实际：{_first_line[:48]}…）："
                       "地板可以改、文案写死，两边就会各说一套")
        if "!define MIN_WIN_TEXT" not in text:
            got.append("没有 ${MIN_WIN_TEXT} 这一处唯一定义：系统名散落在多条文案里，"
                       "改地板时必漏其中一条")
        # 「去索取兼容版安装包」这句指路只许出现在主包分支里：兼容包自己不许把用户
        # 指回「去拿一个他手上正在用的东西」。
        # 【为什么不用 text.find("!ifdef HAS_LEGACY_HINT")】——上一版就是这么写的，于是
        # 宏名写成 HAS_LEGACY_HINT_TYPO 的变异**完全骗过了它**（前者是后者的前缀，find
        # 照样命中），而宏名写错恰恰是这句话漏出主包分支的头一种方式。子串判据在
        # 「名字可能被加后缀」的地方等于没有判据：这里按行取出指令的第一个 token 做全等。
        _lines = [ln.strip() for ln in text.splitlines()]
        # 对**每一处**命中都回溯：只查第一处的话，「把这句复制一份到 !else 那一支」这种
        # 走法永远检不到——一条只盯首次出现的判据，等于给「多写一处」开了后门。
        _his = [i for i, s in enumerate(_lines)
                if "B2bCollectToolSetup_Win7.exe" in s and s.startswith("StrCpy")]
        if not _his:
            got.append("找不到「向管理员索取兼容版安装包」那句指路：主包拦下 Win7 用户时，"
                       "手里没有一条能走通的出路")
        for _hi in _his:
            _depth, _guard, _saw_else = 0, None, False
            for _i in range(_hi - 1, -1, -1):
                _s = _lines[_i]
                if _s.startswith("!endif"):
                    _depth += 1
                elif _s.startswith("!else") and _depth == 0:
                    # 同层见过 !else ⇒ 这句在**另一支**里（也就是兼容包编出来会带上它），
                    # 后面再撞上 !ifdef 也不能算过关。
                    _saw_else = True
                elif _s.startswith(("!ifdef ", "!ifndef ", "!if ")):
                    if _depth == 0:
                        _guard = _s
                        break
                    _depth -= 1
            _tok = _guard.split(None, 1)[1].strip() if _guard and " " in _guard else None
            if _saw_else:
                got.append(f"兼容版包名那句指路在 !else 那一支里（行 {_hi + 1}）：它会被编进"
                           "**兼容包**，而兼容包的用户手里拿的就是兼容版")
            elif _tok != "HAS_LEGACY_HINT":
                got.append(f"兼容版包名那句指路（行 {_hi + 1}）没被 !ifdef HAS_LEGACY_HINT 直接包住"
                           f"（实际外层是「{_guard or '没有编译期开关'}」）：兼容通道编出来的包也会"
                           "喊「去索取兼容版」，而用户手里拿的就是兼容版")
        # 10) 标签唯一：重复标签是 makensis 的编译错误，而本机（Mac）编译不了 NSIS——
        #     这一格是那段脚本**唯一**的本地防线。2026-11-20 加 Win7 缺 SP1 分支时，我就
        #     这么造出了一个重复的 envchk_old_abort，靠的正是这条而不是云端试错。
        _seen = {}
        for _n, _ln in enumerate(text.splitlines(), 1):
            if _ln[:1].isspace() or not _ln.rstrip():
                continue
            _m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*):\s*$", _ln.rstrip())
            if _m:
                _seen.setdefault(_m.group(1), []).append(_n)
        _dup = {k: v for k, v in _seen.items() if len(v) > 1}
        if _dup:
            got.append("标签重复：" + "、".join(f"{k}（行 {v}）" for k, v in _dup.items())
                       + "——makensis 会直接报「label already defined」，而本机编译不了 NSIS，"
                       "这一格就是它唯一的本地防线")
        # 11) 跳 Call EnvLog 活着的寄存器不许是 $9：EnvLog 内部拿 $9 当文件句柄。
        #     新加的小版本/SP 字段若寄在 $9，会被下一句 Call 静默换成一个句柄数字。
        _el = re.search(r"Function EnvLog\n(.*?)\nFunctionEnd", text, re.S)
        if _el and "$9" in _el.group(1) and re.search(r"ReadReg(DWORD|Str) \$9\b", text):
            got.append("有版本字段寄在 $9，而 EnvLog 用 $9 当文件句柄：Call 一过值就变成句柄号")
        return got

    _assert_eq(_problems(_real, _gi), [],
               "今天的安装器体检齐全：判据链、视图还原、静默分支、redist 窗口都在位")
    # 反向对照：改一个与体检无关的字段不许响——这一节拦的是结构漂移，不是「文件被改过」。
    _assert_eq(_problems(_real.replace('APP_VERSION "0.0"', 'APP_VERSION "0.1"', 1), _gi), [],
               "改一个与体检无关的字段不许响（误报的门会被当噪音关掉）")

    # ── 变异复验：每一路都必须被杀红，且红话要点名当初的代价 ──
    _muts = (
        ("IntCmp 顺序写反",
         "IntCmp $R0 ${MIN_WIN_MAJOR} envchk_same_major envchk_old envchk_ok",
         "IntCmp $R0 ${MIN_WIN_MAJOR} envchk_old envchk_ok envchk_ok",
         "会被当成老系统拦下"),
        ("minor 那一行删掉（地板只比 major）",
         "    IntCmp $R7 ${MIN_WIN_MINOR} envchk_sp envchk_old envchk_ok\n", "",
         "Vista"),
        ("IfErrors 判据丢失", "    IfErrors envchk_prewin10 0\n", "",
         "体检链断了"),
        ("老系统退路读回 envchk_old（10B 前的写法）",
         "    IfErrors envchk_prewin10 0\n", "    IfErrors envchk_old 0\n",
         "体检链断了"),
        ("InitPluginsDir 删掉", "    InitPluginsDir\n", "",
         "名字就叫「$PLUGINSDIR」的真实目录"),
        ("SetRegView default 删掉", "    SetRegView default\n", "",
         "64 位视图会带进安装段"),
        ("静默分支删掉", "    IfSilent envchk_abort 0\n", "",
         "安装进程会一直挂到被超时杀掉"),
        ("Sysnative 判据删掉",
         '    IfFileExists "$WINDIR\\Sysnative\\vcruntime140.dll" redist_done 0\n', "",
         "缺运行库的机器就会装出双击报错的产物"),
        # 【需求 10B 新增的四路】每一路都是「构建全绿、只有真机才会知道」那一类：
        # 拦不住它们的代价不是一句报错，是一个发给用户、在他机器上打不开的图标。
        ("地板写成直接 !define（-D 会被静默盖掉）",
         "!ifndef MIN_WIN_MAJOR\n  !define MIN_WIN_MAJOR 10\n!endif",
         "!define MIN_WIN_MAJOR 10",
         "静默盖掉"),
        ("拦下文案写死系统名",
         'StrCpy $R6 "这台电脑的系统版本低于 ${MIN_WIN_TEXT}',
         'StrCpy $R6 "这台电脑的系统版本低于 Windows 10',
         "各说一套"),
        ("指路那句挪出主包分支",
         "!ifdef HAS_LEGACY_HINT", "!ifdef HAS_LEGACY_HINT_TYPO",
         "用户手里拿的就是兼容版"),
        ("指路那句挪到 !else 那一支（会被编进兼容包）",
         '    StrCpy $R6 "$R6这个兼容版安装包已经把运行环境降到',
         '    StrCpy $R6 "兼容版 B2bCollectToolSetup_Win7.exe 也救不了这台；$R6这个兼容版安装包已经把运行环境降到',
         "在 !else 那一支里"),
        ("造一个重复标签（本轮真犯过的错）",
         "envchk_abort:\n", "envchk_abort:\nenvchk_old:\n",
         "标签重复"),
    )
    for label, old, new, needle in _muts:
        _mut = _real.replace(old, new, 1)
        _assert(_mut != _real, f"变异「{label}」没有真的改变文本（锚点没对上，变异等于空跑）")
        _got = _problems(_mut, _gi)
        _assert(_got, f"变异「{label}」必须被杀红（现在全绿＝这一路判据没有牙）")
        _assert(any(needle in g for g in _got),
                f"变异「{label}」的红话要点名代价「{needle}」（实际={_got[:1]}）")


def test_feature_table_gate():
    print("\n═══ CC. 功能清单表格与能力矩阵对拍（需求 9）═══")
    # 「工具功能清单.md」是给用户与权限管理员看的交付物，它的价值全在「与代码一致」：
    # 管理员照第二节那张矩阵授权——表上写了矩阵里没有的能力，就是把没有的功能承诺
    # 出去；矩阵里有的能力表上漏了，管理员就不知道那一格该不该勾。所以这张表不许靠
    # 手抄印象维护，每一处能力名与等级口径都要与 edition.py 的真源逐字对拍：改了
    # 矩阵忘了改表（或反过来），交付链当场红，而不是下一个管理员照着假话授权。
    import edition as ed

    def _problems(text: str) -> list:
        got = []
        # ① 矩阵 8 个能力键全部在表（反引号形式）；表里写的能力键不许超出矩阵。
        for cap in ed.ALL_CAPS:
            if f"`{cap}`" not in text:
                got.append(f"能力 `{cap}` 在表格里缺席：矩阵里有的能力表上漏了，"
                           "管理员就不知道这一格该不该勾")
        # 反引号 token 过滤：只审「长得像能力键」的——全小写、至少一个下划线、无点
        # 无斜杠无反斜杠。这条正则天然排除一切文件名（b2bcollect_gui.py 带点）、路径
        # （b2bcollect_data/riskbird_data 带斜杠）与命令行，不需要去罗列后缀。
        _tokens = set(text.split("`")[1::2])
        for tok in sorted(_tokens):
            if (re.fullmatch(r"[a-z]+(?:_[a-z]+)+", tok)
                    and tok not in ed.ALL_CAPS and tok not in ("pro", "lite")):
                got.append(f"表格里出现「{tok}」：长得像能力键却不在 edition.ALL_CAPS 里——"
                           "把没有的功能承诺出去了，要么改表要么先往矩阵里加能力")
        # ② 等级名与等级口径与 LEVEL_NAMES / LEVEL_DESCS 逐字一致。
        if not all(s in text for s in ("管理员（等级 0）", "标准（等级 1）", "受限（等级 2）")):
            got.append("矩阵表头的三个等级名要写全「管理员（等级 0）/标准（等级 1）/"
                       "受限（等级 2）」——权限管理页上按的就是这三个名字，表上写别的话就对不上")
        for _phrase in ("全部功能 + 权限管理入口", "不能管理权限", "只有精简版那一组功能"):
            if _phrase not in text:
                got.append(f"等级口径「{_phrase}」在表格里缺席：这句与 edition.LEVEL_DESCS "
                           "逐字对拍，改了矩阵文案忘了改表，管理员就是照着假话在授权")
        # ③ 能力的人话名（CAP_LABELS）全部在表：矩阵表用的是这些词，表格必须同源。
        for label in ed.CAP_LABELS.values():
            if label not in text:
                got.append(f"能力人话名「{label}」在表格里缺席：权限面板显示的是这个词，"
                           "表上换成另一种说法两边就对不上")
        return got

    _p = _REPO / "工具功能清单.md"
    _assert(_p.is_file(), "工具功能清单.md 在仓库根（它是需求 9 的交付物，不在位＝这张表丢了没人知道）")
    _real = _p.read_text(encoding="utf-8")
    _assert_eq(_problems(_real), [],
               "今天的功能清单表格与 edition.py 能力矩阵全量对齐（能力键/等级名/口径/人话名）")

    # ── 变异复验：每一路都必须被杀红，且红话要点名当初的代价（BB 节同款形状：变异
    #    只动字符串，真文件一个字节都不碰——拿交付物做变异实验是把工具当沙盒）──
    _muts = (
        # 删行变异的锚点刻意选 list_all_channels：它是表格里唯一只出现一次的能力键
        # （acl_admin/pool_ui 等都在矩阵表和正文里各出现一次，删矩阵行 token 仍在，
        # ① 的缺席判据不红——那就是一个假变异）。
        ("删一个能力行", "| 风鸟/爱企查名单补采（`list_all_channels`） | ✅ | ✅ | — |\n", "",
         "能力 `list_all_channels` 在表格里缺席"),
        ("塞一个矩阵外的能力名", "| 数据池四个入口（`pool_ui`） | ✅ | ✅ | — |",
         "| 数据池四个入口（`pool_ui`） | ✅ | ✅ | — |\n| 数据池导出（`pool_export`） | ✅ | ✅ | — |",
         "长得像能力键却不在 edition.ALL_CAPS 里"),
        ("等级名改写", "标准（等级 1）", "标准版（等级 1）",
         "三个等级名要写全"),
        ("等级口径改字", "全部功能，不能管理权限", "全部功能，不能管权限",
         "等级口径「不能管理权限」在表格里缺席"),
        # 人话名变异必须真把那个词移除（「历史任务续采→历史任务」）：改成加字的话
        # 子串仍在，③ 的缺席判据不红。
        ("人话名改写", "历史任务续采（`history_tasks`）", "历史任务（`history_tasks`）",
         "能力人话名「历史任务续采」在表格里缺席"),
    )
    for label, old, new, needle in _muts:
        _mut = _real.replace(old, new, 1)
        _assert(_mut != _real, f"变异「{label}」没有真的改变文本（锚点没对上，变异等于空跑）")
        _got = _problems(_mut)
        _assert(_got, f"变异「{label}」必须被杀红（现在全绿＝这一路判据没有牙）")
        _assert(any(needle in g for g in _got),
                f"变异「{label}」的红话要点名代价「{needle}」（实际={_got[:1]}）")


# ── DE. 老系统那一路的驱动货源（需求 10 / 10B 前置） ────────────────────────
def _drv_mod():
    """把 .github/scripts/fetch_chromedrivers.py 当模块载进来（判据对象就是它本身）。

    不抄一份提取逻辑进用例：_manifest_gate 同一课——两边各写一套时，同一颗坑要踩两次。
    """
    path = _REPO / ".github" / "scripts" / "fetch_chromedrivers.py"
    _assert(path.is_file(), f"驱动下载脚本必须在位（不在位这一节就是在空转）：{path}")
    spec = importlib.util.spec_from_file_location("_fetch_chromedrivers", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_chromedriver_source_gate():
    """DE. 两套货源、一个命名、一道「别把跑不起来的东西注进包」（需求 10）。"""
    print("\n═══ DE. 兼容通道的 chromedriver：两套货源与一道驱动 sanity ═══")
    # 由来：需求 10 要出一个能在 Win7/8 上跑的兼容包。那类机器上 Chrome 最高 109（Google 从
    # 110 起不支持 Win7/8/8.1），而 Chrome for Testing 只发 115+：拿 141 的驱动去驱动装不上
    # 的浏览器没有意义，只能去旧存储桶 chromedriver.storage.googleapis.com 取。
    # 这一节的全部意义都在「同一个运行时只能有一套命名与落点」：crawler._driver_path 是根
    # 据浏览器实际版本挑 chromedriver.<大版本>.exe，两套货源如果最后注进包里的名字不一样，
    # 兼容包在用户机器上就是「驱动找不到」——而那正好是需求 10 里「装完用不了」的原形。
    #
    # 出处不是背文档：初版这里写的包内名字是 `chromedriver_win32/chromedriver.exe`（照包名
    # 猜的一层目录），本机拉真包才发现 chromedriver.exe 直接在顶层。同一个错发生在 CI 上
    # 是一句「构建脚本挂了」，发生在用户机器上就是「装完用不了」，所以下面每一格都用**真布
    # 局的内存 zip**跑一遍，同时验「结构不对必须红在这里」。
    import io as _io
    import zipfile as _zip
    drv = _drv_mod()
    _exe = b"MZ" + b"\x00" * (2 * 1024 * 1024)      # 过 sanity 的最小合法形状

    def _mkzip(mapping):
        buf = _io.BytesIO()
        with _zip.ZipFile(buf, "w") as z:
            for name, data in mapping.items():
                z.writestr(name, data)
        return buf.getvalue()

    # ── 1) 两套包布局各自能取到驱动，而且取的是实测那一格的名字 ──
    # 比较不交给 _assert_eq：它的红话会 repr 出期望与实际，而这里两边都是 2MB 字节，
    # 一句 ✅ 就能刷出几 MB 屏（本轮真实撞过：日志 33 行、20MB）。只要不等就点名那一段。
    _legacy_raw = _mkzip({"chromedriver.exe": _exe, "LICENSE.chromedriver": b"MIT"})
    _cft_raw = _mkzip({"chromedriver-win64/chromedriver.exe": _exe})
    _assert(drv.extract_legacy_driver(_legacy_raw) == _exe,
            "旧桶的包：chromedriver.exe 在顶层（本机拉真包 109.0.5414.74 实测的布局）")
    _assert(drv.extract_win64_driver(_cft_raw) == _exe,
            "CfT 的包：驱动在 chromedriver-win64/ 一层目录里")
    # 反向对照：布局互换必须红。写成「猜个目录」的那一版就是把两套货源当成一套，
    # 它在真包上永远得不到结果——红的必须是它，不能是安静的空文件。
    for _fn, _raw, _name in ((drv.extract_legacy_driver, _cft_raw, "旧桶拿到 CfT 布局"),
                             (drv.extract_win64_driver, _legacy_raw, "CfT 拿到旧桶布局")):
        try:
            _fn(_raw)
            _assert(False, f"{_name} 必须报「包里没有我要的那一格」（现在被放过了）")
        except KeyError:
            # 走 _assert 而不是裸 print：这一行是**判定行**（「红在对的异常上」就是一个结论），
            # 绕过计数器就会让「✅ 的条数」和「断言条数」两个口径分家——按 ✅ 数断言的人
            # 多算一条从没发生过的判定，而按计数找漏网的人永远看不见它。
            _assert(True, f"{_name}：红在 KeyError，不是静默拿到不相干的文件")

    # ── 2) 驱动 sanity：名字对了不代表能跑 ──
    for _bad, _why in ((b"MZ" + b"x" * 100, "只拿到页头的空壳"),
                       (b"PK\x03\x04" + b"y" * len(_exe), "根本不是 PE")):
        try:
            drv._as_driver(_bad, "夹具")
            _assert(False, f"{_why}必须被拦下（现在被放过了）")
        except SystemExit as e:
            _assert("宁可不打包" in str(e), f"红话要说清代价（实际={str(e)[:60]}）")
    # 反向的反向：真形状不许被拦，否则这条门会把唯一能用的驱动也挡在包外。
    _assert(drv._as_driver(_exe, "夹具") == _exe, "正经驱动不该被 sanity 拦下")

    # ── 3) 封顶与货源分界：114/115 这一刀切在对的地方 ──
    _assert_eq(drv.legacy_majors(109), [109, 108, 107],
               "Win7/8 那一档取 109 往回三个大版本（与主链路同一跨度）")
    _assert_eq(drv.CFT_MIN_MAJOR, 115,
               "两套货源的分界：Chrome for Testing 的第一个大版本就是 115")

    # ── 4) main(--max-major) 的真端到端（网络全部桩掉，落点跟着临时 cwd 走）──
    _old = (drv.fetch_legacy, drv.fetch_json, drv._bytes)
    _trace = []

    def _run_main(argv):
        import os as _os
        with tempfile.TemporaryDirectory() as td:
            _cwd = _os.getcwd()
            _os.chdir(td)
            try:
                drv.main(argv)
                return sorted(_os.listdir(drv.OUT_DIR))
            finally:
                _os.chdir(_cwd)

    try:
        # 旧桶那一路：只伪 fetch_legacy，不伪 main 的分支判定——否则「走哪一路」这一件事
        # 就没人在判（这一节要拦的正是走错货源）。
        # 这份伪 JSON 的形状不是编的：2026-11-19 拉真 known-good-versions 验过——
        # `downloads` 是个 **dict**，键为 chrome / chrome-headless-shell / chromedriver，
        # 每个键下面才是 [{platform, url}]。初版这里把它写成了 list，当场 AttributeError：
        # 假夹具的形状与真货源不一致时，报出来的是「构建脚本挂了」而不是驱动不对。
        drv.fetch_legacy = lambda mj: (_trace.append("legacy") or ("%d.0.0" % mj, _exe))
        drv.fetch_json = lambda url: _trace.append("cft") or {
            "channels": {"Stable": {"version": "141.0.7310.0"}},
            "versions": [{"version": "%d.0.0.1" % mj,
                          "downloads": {"chromedriver": [{"platform": p, "url": "u"}
                                                         for p in ("linux64", "win64", "mac-x64")]}}
                         for mj in (141, 140, 139, 115, 114)],
        }
        drv._bytes = lambda url: _cft_raw
        _names = _run_main(["--max-major", "109"])
        _assert_eq(_names, ["chromedriver.107.exe", "chromedriver.108.exe", "chromedriver.109.exe"],
                   "封顶 109 时旧桶那一路必须产出三个注版驱动，命名与主链路一致"
                   "（crawler._driver_path 只认 chromedriver.<大版本>.exe）")
        _assert_eq(set(_trace), {"legacy"}, f"≤114 必须只走旧桶（实际={_trace}）")
        _trace.clear()
        _names = _run_main(["--max-major", "115"])
        _assert_eq(_trace, ["cft", "cft"], "115 就是 CfT 的第一档，不许误走旧桶")
        _assert("chromedriver.115.exe" in _names,
                f"封顶走 CfT 时要拿到不大于 115 的那一档（实际={_names}）")
        _assert(not any(n.endswith((".141.exe", ".140.exe")) for n in _names),
                f"封顶了就不许把 141/140 也带进包（实际={_names}）")
        # 拿不到足够驱动就中止：兼容包里只揣一个驱动的话，用户的浏览器刚好错开那一版
        # 就是「工具打不开网页」，而这正是需求 10 要修的那件事。
        _trace.clear()

        def _only_109(mj):
            if mj != 109:
                raise SystemExit("旧桶里没有这个版本（桩）")
            return "109.0.5414.74", _exe

        drv.fetch_legacy = _only_109
        try:
            _run_main(["--max-major", "109"])
            _assert(False, "只拿到一个驱动时必须中止（现在它打算带着一个驱动出包）")
        except SystemExit as e:
            _assert("only 1 drivers bundled" in str(e), f"红话要说清拿到几个（实际={e}）")
    finally:
        drv.fetch_legacy, drv.fetch_json, drv._bytes = _old


# ── DD. 第 41 条：剪裁/重量门的执行位置契约 ────────────────────────────────
def test_artifact_step_cwd_gate():
    """DD. 同一行 `dist/...` 在两种基准下指两个位置：第 41 条硬门要有牙。"""
    print("\n═══ DD. 剪裁/重量门在哪一格执行：第 41 条硬门要有牙（两个方向都要红）═══")
    # 由来：2026-11-19 云端 Windows run #69 与 Mac run #73 同时红——剪裁+重量那一步的参数是
    # 照着本机脚本抄进 workflow 的，却没抄那一句 `cd b2bcollect_build`，于是第 40 条构建后
    # 那一面在仓库根读 `dist/...`，读不到视野。云端红是**事后**：这一节要的是「开建之前就能
    # 说清这句话在哪一格执行」，而且两个方向的错位都要红（只红一个方向的门，换个方向写错
    # 就静默放行）。
    gate = _manifest_gate()
    _ok = ("set -u\ncd b2bcollect_build\n"
           "python ../.github/scripts/check_packaging_manifest.py \\\n"
           "  --prune-artifact dist/B2bCollectTool --artifact-weight dist/B2bCollectTool \\\n"
           "  --artifact-label Windows\n")

    # 0) 参照自证：规范写法必须绿。没有这一格，下面每一条红都可能只是「判据根本读不懂
    #    这件事」；同时它钉住修法本身能被接受，否则这条门会把唯一的正确写法关在门外。
    _assert_eq(gate.check_artifact_step_cwd({"夹具": _ok}), [],
               "规范写法不该被拦（拦了就是把修法本身关在门外）")
    # 真仓库口径必须绿、而且**每个入口恰好一处**：少一处就是有一个平台的门在空转。
    # 期望值不写死数字：写死「3」的那一版在需求 10B 把兼容通道接进来的当场就红了（实得 4）。
    # 本仓这一族（写死的数量）已经付过三次学费：依赖清单份数、workflow 份数、这里是调用点数。
    # 入口名单以 CALLERS 登记表为准，而「哪几个平台必须在场」钉**名字**而不是钉个数：
    # 名字改了会当场响，个数对了但名单漂了不会。
    _texts, _absent = gate.artifact_gate_texts()
    _assert_eq(_absent, [], "登记的打包入口都必须在位（登记了却读不到＝这个平台的产物没人管）")
    _by_caller = {}
    for _k in _texts:
        _by_caller.setdefault(_k.split(" · ")[0], []).append(_k)
    _assert_eq({k: len(v) for k, v in sorted(_by_caller.items())},
               {k: 1 for k in _by_caller},
               "每个打包入口恰好判到一处剪裁+重量调用（两处＝入口写法已经分叉、"
               "同一产物被量了两遍；零处不会出现在这张表里，由上面那条与「现在没人执行」兜住）")
    _assert(set(_by_caller) >= {"Windows workflow", gate.LEGACY_WORKFLOW_LABEL,
                                "Mac workflow", "本机 Mac 打包脚本"},
            f"四个平台入口都必须在视野里（实得={sorted(_by_caller)}）：少一处就是那个平台的产物没人管")
    _assert_eq(gate.check_artifact_step_cwd(), [], f"真仓库现在就该是绿的：{_texts.keys()}")

    # 1) 七路变异：每一路都要红，而且红话里要点名代价（只要求「非空」的门迟早被不相干的
    #    噪音哄绿）。
    _muts = (
        ("本轮真缺陷：从仓库根执行却写 dist/",
         "set -u\npython .github/scripts/check_packaging_manifest.py \\\n"
         "  --prune-artifact dist/B2bCollectTool --artifact-weight dist/B2bCollectTool\n",
         "从来没 cd 过（仓库根）"),
        ("反过来：cd 进去之后还写 b2bcollect_build/dist/",
         "cd b2bcollect_build\npython ../.github/scripts/check_packaging_manifest.py \\\n"
         "  --prune-artifact b2bcollect_build/dist/B2bCollectTool \\\n"
         "  --artifact-weight b2bcollect_build/dist/B2bCollectTool\n",
         "只许写 `dist/<产物名>`"),
        ("只带剪裁、把重量门摘了",
         "cd b2bcollect_build\npython ../.github/scripts/check_packaging_manifest.py"
         " --prune-artifact dist/B2bCollectTool\n",
         "剪裁与重量是两道门"),
        ("只带重量、把剪裁摘了",
         "cd b2bcollect_build\npython ../.github/scripts/check_packaging_manifest.py"
         " --artifact-weight dist/B2bCollectTool\n",
         "剪裁与重量是两道门"),
        ("cd 跟着改了、脚本路径没改",
         "cd b2bcollect_build\npython .github/scripts/check_packaging_manifest.py \\\n"
         "  --prune-artifact dist/B2bCollectTool --artifact-weight dist/B2bCollectTool\n",
         "门脚本写成"),
        ("认不出目标的 cd（带变量）",
         'cd "$SOME_DIR" || exit 1\npython ../.github/scripts/check_packaging_manifest.py \\\n'
         "  --prune-artifact dist/B2bCollectTool --artifact-weight dist/B2bCollectTool\n",
         "一条我认不出目标的 cd"),
        ("产物名再往深一层",
         "cd b2bcollect_build\npython ../.github/scripts/check_packaging_manifest.py \\\n"
         "  --prune-artifact dist/B2bCollectTool/_internal \\\n"
         "  --artifact-weight dist/B2bCollectTool\n",
         "只许写 `dist/<产物名>`"),
    )
    for label, script, needle in _muts:
        _got = gate.check_artifact_step_cwd({f"夹具·{label}": script})
        _assert(_got, f"变异「{label}」必须被杀红（全绿＝这一路判据没有牙）")
        _assert(any(needle in p for p in _got),
                f"变异「{label}」的红话要点名代价「{needle}」（实际={_got[:1]}）")

    # 2) 本轮缺陷的**原位**反证：真拿 workflow 的分步口径跑一遍。这一步才是这一条存在的
    #    理由——PyInstaller 那一步 cd 了，剪裁那一步没 cd：跨步的 cwd 不继承，判据必须按
    #    「这一步自己的 cd」说话。把它退回修前原样喂进去，必须同时报出两处错位（cd 与
    #    脚本路径），因为修前那一句两个基准都拿错了。
    _wf = ("name: probe\non:\n  push: [main]\njobs:\n  build:\n    runs-on: windows-latest\n"
           "    steps:\n    - name: Build with PyInstaller\n      shell: bash\n"
           "      run: |\n        cd b2bcollect_build\n        python -m PyInstaller --name X\n"
           "    - name: Prune + weight\n      shell: bash\n      run: |\n"
           "        set -u\n        python .github/scripts/check_packaging_manifest.py \\\n"
           "          --prune-artifact dist/X --artifact-weight dist/X\n")
    _blocks = gate.run_blocks_in_workflow(_wf)
    _assert_eq(len(_blocks), 2, "反证夹具必须切出两步（切少了就是分步判据在空转）")
    _pre = {f"修前·{_n}": _s for _n, _l, _sh, _s in _blocks if "--prune-artifact" in _s}
    _assert_eq(len(_pre), 1, "剪裁那一步要被读到，不然这一格反证不成立")
    _got = gate.check_artifact_step_cwd(_pre)
    _assert(any("从来没 cd 过（仓库根）" in p for p in _got),
            f"上一步的 cd 不许被当成这一步的 cd（跨步不继承）：{_got[:2]}")
    _assert(any("门脚本写成" in p for p in _got),
            f"脚本路径也要在同一格的 cwd 下能落地：{_got[:2]}")
    # 反向的反向：把修后的写法原样贴回夹具，必须绿——否则上面那一红可能只是「夹具本来就错」。
    _post = {"修后·Prune + weight":
             "set -u\ncd b2bcollect_build\n"
             "python ../.github/scripts/check_packaging_manifest.py \\\n"
             "  --prune-artifact dist/X --artifact-weight dist/X\n"}
    _assert_eq(gate.check_artifact_step_cwd(_post), [], "修后写法不该再被拦")

    # 3) 空转保险：注入空表必须红。「三处入口都不调剪裁门」与「判据的视野断了」在症状上
    #    完全一样，两者都不能在空集合上报绿（第 20/25/34 条同一课）。
    _got = gate.check_artifact_step_cwd({})
    _assert(any("现在没人执行" in p for p in _got),
            f"空视野不许报绿：{_got[:1]}")
    # 只调门、不带产物旗标（=只跑静态那一面）不算这一条的视野，但也不算绿得没根据：
    # 它不该被误报，否则真正的错位会被噪音盖住。
    _assert_eq(gate.check_artifact_step_cwd(
        {"只跑静态": "python ../.github/scripts/check_packaging_manifest.py\n"}), [],
        "没带产物旗标的调用不属于这一条的视野（误报就是把门变成噪音）")


# ── DF. 第 42 条·乙丙：两条通道「同源」这句话要有牙 ────────────────────
def test_legacy_channel_gate():
    """DF. 兼容通道与主通道逐字节同源：单独改一边必须红，而注释不能被当成调用。"""
    print("\n═══ DF. 第 42 条乙丙：同源对拍（11 路变异 + 3 句话术）═══")
    # 由来：需求 10B 的兼容通道是从主通道**切片生成**的（debug/gen_legacy_workflow.py）。
    # 生成只在施工那一刻成立，真正防漂的是这一条门：今后任何一边改了共享步骤，当场红。
    # 不这么做会得到什么形状的事故，本仓已经付过三次学费（依赖清单、hidden-import、自报对拍）：
    # 主通道修好了、兼容包还带着旧 bug，而**两条流水都是绿的**。
    g = _manifest_gate()
    _main = g.MAIN_WORKFLOW.read_text(encoding="utf-8")
    _leg = g.LEGACY_WORKFLOW.read_text(encoding="utf-8")
    _tool = (g.ROOT / g.LEGACY_TOOLCHAIN_SCRIPT).read_text(encoding="utf-8")

    # 0) 真仓库必须绿，接线两件必须在位。登记缺一件，这一条管不到兼容通道的清单/shell。
    _assert_eq(g.check_legacy_channel(), [], "真仓库现在就该绿（不绿就是两条通道已经漂了）")
    _assert(g.LEGACY_WORKFLOW_LABEL in g.CALLERS, "兼容通道必须进 CALLERS（不进它的打包清单没人对拍）")
    _assert(g.LEGACY_WORKFLOW_REL in g.WORKFLOWS, "兼容通道必须进 WORKFLOWS（不进它的 shell 不过真解析器）")
    # 0b) 同源门红的时候给的修法是一句「重跑生成器即可对齐」——那这条逃生通道本身必须在位。
    # 只判两份 yml 逐字节相同是不够的：生成器丢了/没提交时这一条门**照样绿**，而 CI 检出的
    # 是提交态——那里没有未跟踪的文件，于是没人能执行那句修法，只能手改 yml（正是本条门要防的
    # 动作）。路径从兼容通道头部注释里**读出来**再核对，不在这里抄一遍：以后改名要么连注释
    # 一起改、要么这一格当场红，不许它静默指向一个不存在的文件。
    _hdr = re.search(r"由\s+(debug/[\w.]+\.py)\s+从主通道", _leg)
    _assert(_hdr is not None, "兼容通道头部注释要写明生成器是谁（没写=这一格只能靠人回忆）")
    if _hdr:
        _gen_rel = _hdr.group(1)
        _gen = g.ROOT / _gen_rel
        # 措辞统一成「被验的性质」而不是「失败描述」：_assert 的 msg 在绿的时候也会原样打印，
        # 写成「不在位：没人能对齐」就会刷出一行 `✅ …不在位…` 这种自相矛盾的话。
        _assert(_gen.is_file(),
                f"修法指向的生成器 {_gen_rel} 在位（同源门红了重跑它就走得通）")
        _gen_state = g.tracked_state(_gen)
        # 提交态的话术只在红的时候拼：_state_hint 对 ok 返回的是「从未被跟踪」那一档，
        # 无条件拼上去就是拿一句错话去描述一个通过的结果。
        _assert(_gen_state == "ok",
                f"生成器 {_gen_rel} 与提交态一致（干净克隆里也拿得到那句修法）"
                + ("" if _gen_state == "ok" else f"：{g._state_hint(_gen_rel, _gen_state)}"))
    # 注入面与真读盘同结论：不同就是判据偷偷依赖了其它现场状态。
    _assert_eq(g.check_legacy_channel(main_text=_main, legacy_text=_leg, toolchain_text=_tool), [],
               "三份文本原样喂进去必须与读盘同结论")

    # 1) 三句红话术各自钉住。上一版这里只会说「行数都不同（14 vs 14）」——一句自相矛盾的
    #    谎话（splitlines() 丢了尾部换行）；一条说不清真因的红比不红更糟，因为下一个
    #    人会去改打包参数而不是重跑生成器。
    _assert("只差**尾部空行**" in g._body_diff_hint("a\nb\n", "a\nb"),
            "行内容全同、只差尾部换行时要说出真相（不许说成行数不同）")
    _assert("第 1 行内容不同" in g._body_diff_hint("a\n", "b\n"),
            "行内容不同要指到具体那一行")
    _assert("行数不同" in g._body_diff_hint("a\n", "a\nb\n"),
            "真行数不同要敢说行数")

    # 2) 十一路变异：每一路都要红，而且红话里要点名代价（只要求「非空」的门迟早被噪声哄绿）。
    #    锚点一律用**程序化变换**而不是抄长字面量：长字面量在主通道改一个字之后会静默失配，
    #    于是夹具什么也没改、判据什么也没判，而报告显示绿。
    def _step_cut(text: str, name: str) -> str:
        r"""整步删掉（含注释与 env），用来模拟「兼容通道漏抄了一步」。

        按行切而不拿正则猜缩进：上一版这里写的是 `(?:    (?:- |)|      |\n)*`，那个分支
        在步骤体中间的缩进上就停了——删不干净整步，夹具变成「什么都没改」的恒真夹具。
        步头只有一个口径：四空格加 `- `；步骤内部再怎么缩进也不会撞这个前缀。
        """
        out, dropping = [], False
        for ln in text.splitlines(keepends=True):
            if ln.startswith("    - "):
                dropping = ln.strip() == f"- name: {name}"
            if not dropping:
                out.append(ln)
        assert "".join(out) != text, f"夹具没命中步骤「{name}」（锚点漂了，这条变异是恒真的）"
        return "".join(out)

    def _step_shell_swap(text: str, name: str) -> str:
        """只把**指定那一步**的 `shell: bash` 换成 pwsh，别的一步不动。

        上一版写的是 `text.replace("      shell: bash", ..., 1)`：全文**第一处**那个缩进根本不
        在 SHARED_STEPS 的比对视野里——于是夹具什么都没改，判据什么都不判，而报告不会告诉你
        这件事。这是比「判据没有牙」更糟的形状：**假绿**（实测本轮就是这样，直到我把这一路
        跑了一遍才逮出来）。
        """
        lines = text.splitlines(keepends=True)
        start = None
        for i, ln in enumerate(lines):
            if ln.startswith("    - "):
                if start is not None:
                    break
                if ln.strip() == f"- name: {name}":
                    start = i
        assert start is not None, f"夹具没找到步骤「{name}」（锚点漂了，这一路变异是恒真的）"
        for j in range(start, len(lines)):
            if lines[j].startswith("      shell: bash"):
                lines[j] = lines[j].replace("shell: bash", "shell: pwsh", 1)
                return "".join(lines)
        raise AssertionError(f"步骤「{name}」里没有 shell: bash 那一行（夹具写法已漂）")

    _INJECT = "        python .github/scripts/inject_build_config.py\n"
    # 每一路 = （标题, 改哪份文本, 怎么改, 红话必须点名代价）。不预先算好文本再塞进表里：
    # 改成传函数，是为了下面循环里能统一断一句「真的改动了」——上一版只有 _step_cut 自带
    # 这句保险，其余每一路都可能因为锚点漂掉而变成恒真夹具。
    _cases = (
        ("单独改兼容通道的共享步骤", "legacy_text",
         lambda t: t.replace(_INJECT, "        echo 我先加了一行\n" + _INJECT, 1),
         "不再逐字节相同"),
        ("单独改主通道的共享步骤（反向）", "main_text",
         lambda t: t.replace(_INJECT, _INJECT + "        echo 主通道多加一行\n", 1),
         "不再逐字节相同"),
        ("只在兼容通道多一个排版空行", "legacy_text",
         lambda t: t.replace(_INJECT, _INJECT + "\n", 1), "只差**尾部空行**"),
        ("共享步骤的 shell 被改成 pwsh", "legacy_text",
         lambda t: _step_shell_swap(t, "Write bundled config.json from Secrets"), "shell 不同"),
        ("兼容通道漏抄一步（整步删掉）", "legacy_text",
         lambda t: _step_cut(t, "Package Windows artifacts"), "兼容通道里没有步骤"),
        ("PyInstaller 参数漂了一个（归一化不许放过）", "legacy_text",
         lambda t: t.replace('"$TARGET_PY" -m PyInstaller',
                             '"$TARGET_PY" -m PyInstaller --noupx', 1),
         "除解释器 token 外不再相同"),
        ("驱动上限从 109 漂到 120", "legacy_text",
         lambda t: t.replace("--max-major 109", "--max-major 120", 1), "109"),
        ("安装器地板改回 Win10", "legacy_text",
         lambda t: t.replace('MIN_WIN_MAJOR: "6"', 'MIN_WIN_MAJOR: "10"', 1), "major=6"),
        ("地板只留在注释里（注释不算在场）", "legacy_text",
         lambda t: t.replace('        OUT_FILE: "B2bCollectToolSetup_Win7.exe"',
                             "        # OUT_FILE: \"B2bCollectToolSetup_Win7.exe\"", 1),
         "B2bCollectToolSetup_Win7.exe"),
        ("兼容通道真的开始发清单了", "legacy_text",
         lambda t: t + "    - name: Publish manifest\n      shell: bash\n"
                       "      run: python .github/scripts/make_release_manifest.py\n",
         "开始生成发布清单"),
        ("体检脚本里的目标档漂成 3.9", "toolchain_text",
         lambda t: t.replace("TARGET_FLOOR = (3, 8)", "TARGET_FLOOR = (3, 9)", 1),
         "(3, 8)"),
    )
    _SRC = {"legacy_text": _leg, "main_text": _main, "toolchain_text": _tool}
    for label, key, mutate, needle in _cases:
        _new = mutate(_SRC[key])
        # 恒真夹具保险：没改动文本的变异，就算判据报红了也不算数（红的原因可能与这一路无关）。
        _assert(_new != _SRC[key],
                f"变异「{label}」必须真的改动文本（没改动＝这一路夹具是恒真的）")
        _got = g.check_legacy_channel(main_text=_new if key == "main_text" else _main,
                                      legacy_text=_new if key == "legacy_text" else _leg,
                                      toolchain_text=_new if key == "toolchain_text" else _tool)
        _assert(_got, f"变异「{label}」必须被杀红（全绿＝这一路判据没有牙）")
        _assert(any(needle in p for p in _got),
                f"变异「{label}」的红话要点名代价「{needle}」（实际={_got[:1]}）")

    # 3) 本轮真正跑出来的那条**假红**：交付边界说明里写着「不发清单」，里面点名了那两份
    #    脚本的名字。注释里的指路话不算调用（本仓在陷阱门上早有此约），所以这一路必须绿。
    _green = _leg + ("    # 这一路不发 OSS 直链也不发清单：make_release_manifest.py 与 "
                     "upload_release_to_oss.py 都等需求 10C 一起接\n")
    _assert_eq(g.check_legacy_channel(main_text=_main, legacy_text=_green, toolchain_text=_tool), [],
               "只在注释里提到那两份脚本不该红（拦了解释性注释，就是拦下「把边界写清楚」这件事）")


# ── DG. 第 42 条·甲：3.8 地板门与双解释器体检脚本 ────────────────────
def _legacy_toolchain_gate():
    """把双解释器体检脚本当模块载进来（判据本体，不在这里抄一份）。"""
    global _LEGACY_TC_MOD
    if _LEGACY_TC_MOD is None:
        _p = _REPO / ".github" / "scripts" / "check_legacy_toolchain.py"
        _spec = importlib.util.spec_from_file_location("_legacy_toolchain_gate", _p)
        _mod = importlib.util.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        _LEGACY_TC_MOD = _mod
    return _LEGACY_TC_MOD


def test_py38_floor_gate():
    """DG. 进包源码必须落在 3.8 地板之下；双解释器体检的四档判定逐档验。"""
    print("\n═══ DG. 第 42 条甲：3.8 地板门 + 双解释器体检（含真跑一次冒烟）═══")
    g = _manifest_gate()
    tc = _legacy_toolchain_gate()
    _p = g.Path("b2bcollect_build/zz_地板夹具.py")

    # 0) 真仓库绿；空视野必须红（在空集合上报「没有不合格」是一句谎话，第 20 条同族）。
    _assert_eq(g.check_py38_floor(), [], "进包闭包 + 被目标解释器执行的工具脚本现在就该绿")
    _assert(any("读不到任何进包源码" in x for x in g.check_py38_floor(sources=[])),
            "空视野不许报绿")
    # 被目标解释器跑的脚本不在位也要红：兼容通道会红成一句 SyntaxError，而不是「没人判」。
    _saved = g.FLOOR_BOUND_TOOL_SCRIPTS
    try:
        g.FLOOR_BOUND_TOOL_SCRIPTS = (".github/scripts/zz_不存在的工具脚本.py",)
        _got = g.check_py38_floor(sources=[_p])
        _assert(any("不在位" in x for x in _got), f"工具脚本缺席必须响：{_got[:1]}")
    finally:
        g.FLOOR_BOUND_TOOL_SCRIPTS = _saved

    # 1) 三道检查各自有牙。前两条靠真解析器（feature_version=(3,8)），后两条靠 AST：
    #    拿本机（比 CI 新）的解释器结论替 3.8 担保，就是这一条要防的事。
    _assert(any("解析不了" in x for x in g._floor_problems(_p,
               "x = 1\nmatch x:\n    case 1:\n        pass\n")), "match/case 在 3.8 解析不了→红")
    _assert(any("PEP 585" in x for x in g._floor_problems(_p,
               "def f(a: list[str]):\n    return a\n")), "无 future import 的 list[str] 要红")
    _assert(any("PEP 604" in x for x in g._floor_problems(_p,
               "def f(a: int | None):\n    return a\n")), "无 future import 的 int | None 要红")
    _assert(any("ast.unparse" in x for x in g._floor_problems(_p,
               "import ast\ndef f(t):\n    return ast.unparse(t)\n")), "3.9+ API 要红")
    _assert(any("graphlib" in x for x in g._floor_problems(_p, "import graphlib\n")),
            "3.9+ 才有的标准库要红")
    # 反面（同样是判据的一部分）：三种**写法本身合法**的形状不许红，否则这条门会把修法关在门外。
    _assert_eq(g._floor_problems(_p,
               "from __future__ import annotations\ndef f(a: list[str], b: int | None) -> None:\n"
               "    return None\n"), [], "有 future import 时注解不求值，PEP585/604 无害")
    _assert_eq(g._floor_problems(_p, "if (n := 3) > 2:\n    pass\ndef f(a, /, b):\n    return a\n"),
               [], "joker 与只位参数都是 3.8 合法写法，不许误报")
    _assert_eq(g._floor_problems(_p, "def f(a, b):\n    return a | b\n"), [],
               "函数体里的 dict | 静态判不出来（这一档诚实边界写在门脚本里，不在这里假装能判）")

    # 2) 双解释器体检：四档判定是纯函数，逐档喂假数据验（不必真有两台解释器）。
    _T38 = {"version": [3, 8, 18], "bits": 64, "banner": "3.8.18", "executable": "/t/python.exe"}
    _S311 = {"version": [3, 11, 9], "bits": 64, "banner": "3.11.9", "executable": "/s/python.exe"}
    _assert_eq(tc.evaluate((3, 11, 9), _T38, _S311, False), [], "四档全对时不许红")
    for _label, _args, _needle in (
            ("裸 python 落到 3.8", ((3, 8, 18), _T38, _S311, False), "裸 python"),
            ("目标是 3.9（Win7 不再支持）", ((3, 11, 9), dict(_T38, version=[3, 9, 13]), _S311, False), "不是 3.9".replace("不是", "是")),
            ("目标是 32 位", ((3, 11, 9), dict(_T38, bits=32), _S311, False), "32 位"),
            ("工具链只有 3.10", ((3, 11, 9), _T38, dict(_S311, version=[3, 10, 11]), False), "3.10"),
            ("两个解释器是同一个", ((3, 11, 9), _T38, _S311, True), "同一个可执行文件"),
            ("目标解释器问不出话", ((3, 11, 9), {"error": "FileNotFoundError"}, _S311, False), "问不出")):
        _got = tc.evaluate(*_args)
        _assert(_got, f"体检变异「{_label}」必须红（不红＝那格判据是空的）")
        _assert(any(_needle in x for x in _got), f"「{_label}」的红话要说出代价「{_needle}」（实际={_got[:1]}）")

    # 3) 运行时对版：Qt6 / 缺包 / 目标不是 3.8 都要红，正常那一档必须绿。
    _OK = {"version": [3, 8, 18], "python": "3.8.18", "Qt": "5.15.11", "PyQt5": "5.15.11"}
    _OK.update({m: "0.0" for m in tc.INVENTORY_MODULES})
    _assert_eq(tc.evaluate_inventory(_OK), [], "Qt5 + 全部包在位不该红")
    _assert(any("Qt 5" in x for x in tc.evaluate_inventory(dict(_OK, Qt="6.7.2"))),
            "带 Qt6 的「Win7 包」必须当场红（runner 是 Win11，下游每一格都会绿）")
    _assert(any("import 失败" in x for x in tc.evaluate_inventory(
        dict(_OK, PIL="IMPORT-FAILED ModuleNotFoundError"))), "少装一个包要红（只会少装、不会报错）")
    _assert(any("不是 3.8" in x or "不是 3.8" in x for x in tc.evaluate_inventory(
        dict(_OK, version=[3, 11, 9], python="3.11.9"))), "进包环境本身不是 3.8 要红")
    _assert(any("答不出" in x for x in tc.evaluate_inventory({"error": "boom"})),
            "读不到运行时不许当「这一格没问题」")

    # 4) 真跑一次冒烟：拿本仓解释器同时当两个参数——分工不成立，必须退 1 并说出哪一档。
    #    只看单测会放过「脚本根本跑不起来」这一类（argparse 写错、顶层报异常……）。
    _exe = str(_REPO / ".github" / "scripts" / "check_legacy_toolchain.py")
    _r = subprocess.run([sys.executable, _exe, "--pin", "--target-py", sys.executable,
                         "--tool-py", sys.executable], capture_output=True, text=True,
                        encoding="utf-8", errors="replace")
    _assert(_r.returncode == 1, f"同一台机器当两个解释器必须退 1（实际 rc={_r.returncode}）")
    # 子进程原文进文案时由报告层压成一行（`_one_line`）：它们的输出本身就带 `❌ ` 与
    # `::error::` 前缀，多行直接嵌进来会让日志凭空多出几条「看起来像本套件判定」的行。
    _assert("同一个可执行文件" in _r.stdout, f"真跑那一趟要说出为什么红：{_one_line(_r.stdout)[-200:]}")
    # --pin 与 --inventory 都不给：这一格什么都不判，必须自己红（而不是退 0 报绿）。
    # 两个条件分开断：合在一条里失败时分不清是「退码错」还是「没说为什么」。
    _r2 = subprocess.run([sys.executable, _exe, "--target-py", sys.executable],
                         capture_output=True, text=True, encoding="utf-8", errors="replace")
    _assert(_r2.returncode == 1,
            f"不给判定项的调用不许退 0（实际 rc={_r2.returncode}）：{_one_line(_r2.stdout)[-200:]}")
    _assert("什么也没判" in _r2.stdout,
            f"不给判定项的调用要说出它什么都没判：{_one_line(_r2.stdout)[-200:]}")


# ── DH. PE 地板门自己的地板（合成 PE，需求 10B 第 14 步） ────────────────────
_PE_PATH = _REPO / ".github" / "scripts" / "check_pe_floor.py"


def _pe_ns(text=None):
    """把 check_pe_floor.py 当模块执行，返回它的命名空间。

    给 text 就是执行一份**被改过的源码**（变异复验用）。判据对象必须是脚本自己：
    在测试里另抄一份 PE 解析逻辑，两边各自演化时这一节就只在「证明自己和自己一致」。
    """
    src = _PE_PATH.read_text(encoding="utf-8") if text is None else text
    ns = {"__name__": "_pe_floor_under_test", "__file__": str(_PE_PATH)}
    exec(compile(src, str(_PE_PATH), "exec"), ns)
    return ns


def _pe_build(major=6, minor=1, subsystem=3, bits=64, imports=None, exports=None,
              cut=0, bad_import_rva=False, dll=False) -> bytes:
    """合成一个**字段位置严格按 PE 规范**的最小镜像（本机是 Mac，没有真 Windows 二进制可扫）。

    布局照 COFF 规范：e_lfanew → PE 签名 → 20 字节 COFF 头 → 可选头 → 节表 → 节数据。
    两格刻意跟着「文件自己声明的字节数」走而不是写死：SizeOfOptionalHeader（决定节表
    在哪）与数据目录起点（PE32 在 96、PE32+ 在 112）——这两格正是本脚本真栽过的地方。
    imports: {dll 名: [函数名…]}，列表里放 None 表示**按序号导入**（名字读不出那一形）。
    dll: 置位 IMAGE_FILE_DLL（0x2000）。子系统地板豁免按**这一位**判、不按文件名判
    （2026-11-21 台账第 11 项），所以夹具必须能把这一位单独拨出来，两个方向都要能造。
    """
    magic = 0x20B if bits == 64 else 0x10B
    size_opt = 240 if bits == 64 else 224
    machine = 0x8664 if bits == 64 else 0x14C
    pe_off, sec_va, sec_ptr = 0x80, 0x1000, 0x400
    tsz = 8 if bits == 64 else 4
    ordinal_flag = 1 << (63 if bits == 64 else 31)
    imports = imports or {}
    exports = exports or []
    desc_len = 20 * (len(imports) + 1)          # 每个描述符 20 字节 + 一个全零终止项
    pos, blobs, plan = desc_len, {}, []
    for dll, funcs in imports.items():
        ilt = pos
        pos += tsz * (len(funcs) + 1)
        name = pos
        pos += len(dll.encode("ascii")) + 1
        vals = []
        for f in funcs:
            if f is None:                        # 序号导入：最高位为 1，低 16 位是序号
                vals.append(ordinal_flag | 1)
            else:
                hn = pos
                rec = struct.pack("<H", 0) + f.encode("ascii") + b"\x00"
                rec += b"\x00" if len(rec) % 2 else b""
                blobs[hn] = rec
                pos += len(rec)
                vals.append(sec_va + hn)
        plan.append((dll, ilt, name, vals))
    # 导出表（数据目录[0]）：IMAGE_EXPORT_DIRECTORY 40 字节 + 三张平行表 + 名字串。
    exp_at = fn_at = nm_at = ord_at = 0
    name_at = []
    if exports:
        exp_at = pos
        pos += 40
        fn_at = pos
        pos += 4 * len(exports)
        nm_at = pos
        pos += 4 * len(exports)
        ord_at = pos
        pos += 2 * len(exports)
        for x in exports:
            name_at.append(pos)
            blob = x.encode("ascii") + b"\x00"
            blob += b"\x00" if len(blob) % 2 else b""
            blobs[pos] = blob
            pos += len(blob)
    body = bytearray(pos)
    for i, (dll, ilt, name, vals) in enumerate(plan):
        struct.pack_into("<IIII", body, 20 * i, sec_va + ilt, 0, 0, sec_va + name)
        struct.pack_into("<I", body, 20 * i + 16, sec_va + ilt)
        for k, v in enumerate(vals):
            body[ilt + k * tsz:ilt + k * tsz + tsz] = struct.pack("<Q" if tsz == 8 else "<I", v)
        nb = dll.encode("ascii")
        body[name:name + len(nb)] = nb
    for off, rec in blobs.items():
        body[off:off + len(rec)] = rec
    if exports:
        # 名字表才是被测那一格（parse_pe 只读它）；Name RVA / 函数 RVA 本工具不读，留零。
        struct.pack_into("<II", body, exp_at + 20, len(exports), len(exports))
        struct.pack_into("<III", body, exp_at + 28, sec_va + fn_at, sec_va + nm_at, sec_va + ord_at)
        for i, off in enumerate(name_at):
            body[nm_at + i * 4:nm_at + i * 4 + 4] = struct.pack("<I", sec_va + off)
            struct.pack_into("<H", body, ord_at + i * 2, i)
    data = bytearray(sec_ptr + len(body) + 16)
    data[0:2] = b"MZ"
    struct.pack_into("<I", data, 0x3C, pe_off)
    data[pe_off:pe_off + 4] = b"PE\x00\x00"
    struct.pack_into("<HHIIIHH", data, pe_off + 4, machine, 1, 0, 0, 0, size_opt,
                     0x102 | (0x2000 if dll else 0))
    opt = pe_off + 24
    struct.pack_into("<H", data, opt, magic)
    struct.pack_into("<HH", data, opt + 48, major, minor)      # Major/MinorSubsystemVersion
    struct.pack_into("<H", data, opt + 68, subsystem)
    dd = opt + (96 if magic == 0x10B else 112)
    if exports:
        struct.pack_into("<II", data, dd, sec_va + exp_at, 40)
    if imports:                                                # 不给 imports = 数据目录[1] 全零
        struct.pack_into("<II", data, dd + 8, 0x9FFF0000 if bad_import_rva else sec_va, desc_len)
    sec = pe_off + 24 + size_opt
    data[sec:sec + 6] = b".text"
    struct.pack_into("<IIII", data, sec + 8, len(body), sec_va, len(body), sec_ptr)
    data[sec_ptr:sec_ptr + len(body)] = body
    return bytes(data[:cut]) if cut else bytes(data)


def _pe_said(fn, *a, **kw):
    """跑一个会 print 的判定函数，返回 (返回值, 它说过的话)。

    「说过什么」必须一起拿：这一节的多数判据说的是**话术**（少判一格要说出来、读不出
    要点名文件），只看返回值会把「静默跳过」和「判过且合格」读成同一件事。
    """
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        got = fn(*a, **kw)
    return got, buf.getvalue()


_SWAP_PATH = _REPO / ".github" / "scripts" / "swap_win7_ucrt.py"


def _swap_ns(text=None):
    """把 swap_win7_ucrt.py 当模块执行（与 _pe_ns 同一口径：判据对象必须是脚本自己）。"""
    src = _SWAP_PATH.read_text(encoding="utf-8") if text is None else text
    ns = {"__name__": "_swap_ucrt_under_test", "__file__": str(_SWAP_PATH)}
    exec(compile(src, str(_SWAP_PATH), "exec"), ns)
    return ns


def _ucrt_wiring_problems(leg_text):
    """UCRT 替换的接线判据（12c 与变异复验共用同一个把手，不写两份）。

    由来（2026-11-21 legacy run#4 取证）：42 项存根红只是第一层，Win11 版 ucrtbase
    自己就依赖 Win8 契约模块——替换必须发生在 PE 地板门**之前**，否则门先红在
    存根上，替换根本没机会跑。第二轮（run#5 证伪 SDK 来源后）再加三格：替换源必须
    先经钉死 sha256 的下载步、下载在替换之前、替换真走 --redist（不许退回自动找
    SDK 那条已被云端证伪的路）。第三轮（run#8）把 core 剪枝那一格反向：替换步已从
    a10 redist 整套覆盖 core 桩，其中 synch 转发器是替换后 ucrtbase 的加载依赖，
    PE 门里再留同一条 glob 会把刚带进包的支撑桩剪掉（run#8 门绿而产物自检
    fastfail 0xC0000409，首嫌即此）。返回问题列表，空 = 接线合格。
    """
    probs = []
    steps = [(m.group(1), m.start()) for m in
             re.finditer(r"^    - name: (.+)$", leg_text, re.M)]
    idx = {n: i for i, (n, _) in enumerate(steps)}
    swap = next((n for n in idx if n.startswith("Swap in Win7-compatible UCRT")), "")
    pe_step = next((n for n in idx if n.startswith("PE floor gate (")), "")
    if not swap:
        probs.append("兼容通道没有「装配期换 Win7 UCRT」那一步：Win11 版 ucrtbase 与 api-ms "
                     "存根会留在包里，Win7 加载器当场拒载（台账第 11 项）")
        return probs
    if not pe_step:
        probs.append("找不到 PE 地板门那一步：UCRT 替换的先后顺序没了参照")
        return probs
    if idx[swap] >= idx[pe_step]:
        probs.append("UCRT 替换排在了 PE 地板门之后：门会先红在 42 项存根上，替换根本没机会跑")
    dl = next((n for n in idx if n.startswith("Provision VS2015U3 redist")), "")
    if not dl:
        probs.append("兼容通道没有钉死 sha256 的 VS2015U3 redist 下载步：替换源字节不再可信"
                     "（run#5 证伪 SDK 来源后，完整 Win7 UCRT 只剩这一条路，台账第 11 项）")
    else:
        if idx[dl] >= idx[swap]:
            probs.append("redist 下载排在了 UCRT 替换之后：替换那一步拿不到原件")
        j = idx[dl]
        dbody = leg_text[steps[j][1]:steps[j + 1][1]] if j + 1 < len(steps) else leg_text[steps[j][1]:]
        if "da66717784c192f1004e856bbcf7b3e13b7bf3ea45932c48e4c9b9a50ca80965" not in dbody:
            probs.append("redist 下载步没钉住 VS2015U3 的 sha256：历史版字节不该变，哈希漂了就是替换源被换了")
    s = idx[swap]
    sbody = leg_text[steps[s][1]:steps[s + 1][1]] if s + 1 < len(steps) else leg_text[steps[s][1]:]
    if '--redist "$GITHUB_WORKSPACE/vc2015u3_x64.exe"' not in sbody:
        probs.append("UCRT 替换没走 --redist 钉死的原件：退回自动找 SDK 那条路已被云端 run#5 证伪"
                     "（runner 只有 26100 一档，那份 ucrtbase 是 Win10 构建）")
    i = idx[pe_step]
    body = leg_text[steps[i][1]:steps[i + 1][1]] if i + 1 < len(steps) else leg_text[steps[i][1]:]
    if "--prune-match api-ms-win-core-*.dll" in body:
        probs.append("PE 地板门里还留着 api-ms-win-core-*.dll 剪枝：换完 UCRT 后 dist 里那些文件"
                     "是替换源 a10 整套带进来的支撑转发器（ucrtbase 加载就依赖其中的 synch 桩），"
                     "同一条 glob 会反手剪掉刚带进包的东西——run#8 门绿而产物自检 fastfail，"
                     "首嫌就是这条剪枝把 UCRT 掹空（第三轮反转，台账第 11 项）")
    return probs


def test_pe_floor_gate():
    print("\n═══ DH. PE 地板门自己的地板：合成 PE 夹具（兼容通道第 14 步那道门）═══")
    # 由来：第 14 步那道 PE 地板门是本轮新加的，交付时**一条离线判据也没有**，云端首运
    # 就暴露了 43 条假红——api-ms-win-crt-*.dll 这类 API-set 契约桩与 python3.dll 这类
    # 转发 DLL 结构上就没有导入表，旧写法一律 raise 成「读不出地板」。交付链上别的门都有
    # 夹具，只有这道靠云端真跑当测试，而云端的代价是一个还没打出来的 Win7 包。
    # 2026-11-20 兼容通道 run#2 实测：检了 123 个镜像报 49 项，其中 43 项是这一格判错。
    _assert(_PE_PATH.is_file(), f"PE 地板门脚本必须在位（不在位这一节就是在空转）：{_PE_PATH}")
    pe = _pe_ns()

    # ── 1) 无导入表的镜像：标出来，不抛异常，地板字段照读 ──
    stub = _pe_build()                                  # api-ms-win-crt-*.dll 的真实形状
    d = pe["parse_pe"](stub)
    _assert(d["no_imports"] is True, "无导入表要标成 no_imports（旧写法在这里 raise，43 条假红的起点）")
    _assert_eq(len(d["dlls"]), 0, "无导入表 ⇒ 依赖集合是空集（不许凭空造一个依赖出来）")
    _assert_eq((d["subsystem_major"], d["subsystem_minor"]), (6, 1),
               "无导入表也要把子系统版本读出来：地板判定只需要 OptionalHeader")

    # ── 2) 地板内的契约桩判为达标，而且必须把「这一格筛不到」说出来 ──
    p, said = _pe_said(pe["check_one"], Path("api-ms-win-core-file-l1-2-0.dll"), stub, (6, 1))
    _assert_eq(p, [], "地板内的无导入表镜像必须判为达标（run#2 那 43 项假红就判在这一格）")
    _assert("不声明导入表" in said, "契约桩那一行要说明它为什么不声明导入表（读日志的人不必再去翻规范）")
    _assert("API 名单筛查对这一格没有输入" in said,
            "少判一格必须说出来：静默给个 ✅ 会让「API 名单筛过了」这句话没人能核对")

    # ── 3) 无导入表 ≠ 免检：同一形状但要求 Win10 的镜像仍然必须红 ──
    p, _ = _pe_said(pe["check_one"], Path("needs10.exe"), _pe_build(major=10, minor=0), (6, 1))
    _assert(any("要求 Windows 10.0" in x for x in p),
            "修假红时不许把地板判定一起免掉（那样这一格就从假红翻成假绿，更贵）")

    # ── 4) 整目录扫描：43 个契约桩 + 一个正常主程序必须全绿（run#2 现场的直接复现） ──
    root = Path(tempfile.mkdtemp(prefix="pe_floor_"))
    payload = root / "payload"
    payload.mkdir()
    # 真空目录单独建一个：--scan-dir 是**递归**扫的，拿 root 当「没有 PE」那一格的输入，
    # 测的其实是「root 下面那个 payload 能不能扫干净」（第一版就写错了这一格）。
    empty = root / "empty"
    empty.mkdir()
    for i in range(43):
        (payload / f"apiset_stub_{i}.dll").write_bytes(_pe_build())
    (payload / "B2bCollectTool.exe").write_bytes(_pe_build(
        imports={"kernel32.dll": ["Sleep"], "python38.dll": ["Py_Main"]}))
    rc, out = _pe_said(pe["main"], ["--scan-dir", str(payload), "--floor", "6.1"])
    _assert_eq(rc, 0, f"43 个契约桩 + 主程序整目录扫描必须退 0（实际 rc={rc}）")
    _assert("全部达标" in out and "::error::" not in out,
            f"这一趟一条 ::error:: 都不许有——它就是 run#2 第 14 步那 43 项：{_one_line(out)[-160:]}")

    # ── 5) 真缺陷照样必须红：run#2 那 6 项 B 类的形状逐条钉住 ──
    #     这一组的立场是「不许为了消红放宽门」：下面两条正是云端报出来的真发现，
    #     判据先把它们钉成红，将来任何白名单改动都必须同时改这条判据（改一半就红）。
    p, _ = _pe_said(pe["check_one"], Path("selenium-manager.exe"), _pe_build(
        imports={"kernel32.dll": ["GetSystemTimePreciseAsFileTime", "WaitOnAddress"]}), (6, 1))
    _assert(any("GetSystemTimePreciseAsFileTime" in x and "0xc0000139" in x for x in p),
            "导入 Win8 才有的函数必须红并给出错误码（老系统上是「找不到入口点」，构建机上一句错都不报）")
    p, _ = _pe_said(pe["check_one"], Path("ucrtbase.dll"), _pe_build(
        imports={"api-ms-win-core-synch-l1-2-0.dll": ["WaitOnAddress"]}), (6, 1))
    _assert(any("api-ms-win-core-synch-l1-2-0.dll" in x and "连模块都加载不到" in x for x in p),
            "依赖 Win8+ 才存在的契约模块必须红：这一格比「函数找不到」更早发生")

    # ── 6) 同一个 DLL 里「按序号」与「按名字」混在一起时不许炸 ──
    #     2026-11-20 拿线上安装包首跑就栽在这里：排序键比较到 None 与 str 不可比。
    #     夹具必须把两种导入放在**同一个 DLL 名**下：排序键先比 DLL 名，名字不同就
    #     永远走不到比较函数名那一步——第一版这里写成两个 DLL，下面那条「拆掉排序
    #     兜底」的变异于是杀不红，本组判据其实是假绿（由变异复验当场逮住的）。
    mixed = _pe_build(imports={"kernel32.dll": [None, "Zed", "Aaa"]})
    try:
        _, said = _pe_said(pe["check_one"], Path("mixed.dll"), mixed, (6, 1))
        _assert("按序号导入" in said, "按序号导入的项要说「名字读不出、API 名单筛查对它们瞎」")
    except TypeError as e:
        _assert(False, f"排序键遇到序号导入的 None 不许抛 TypeError（{e}）")

    # ── 7) 两个「看起来可疑但不该拦」与「看起来正常但必须拦」的版本号 ──
    p, _ = _pe_said(pe["check_one"], Path("packed.exe"), _pe_build(major=0, minor=0), (6, 1))
    _assert(any("0.00" in x for x in p), "子系统版本 0.00（被壳/被改过的常见形态）必须红：这句话不能替它担保")
    p, said = _pe_said(pe["check_one"], Path("setup.exe"), _pe_build(major=4, minor=0), (6, 1))
    _assert_eq(p, [], "NSIS 写的 4.00 不许误伤：它比任何还在卖的 Windows 都老，语义是更宽不是更严")
    _assert("早于本表登记的最老一项" in said, "名字表没登记就直说没登记，不许猜一个系统名")

    # ── 7b) DLL 豁免子系统地板：只豁这一格，而且必须说出来（2026-11-21 用户拍板）──
    #     由来（台账第 11 项第二轮取证）：微软官方给 Win7 的 app-local UCRT 转发器
    #     （VS2015U3 redist 附加容器 / SDK Redist\ucrt / .NET win7 ApiSets NuGet，三条
    #     渠道本机实测）子系统版本全是 10.0——加载器创建进程时只校验主镜像（EXE）的
    #     子系统版本，DLL 走另一条映射路径。旧版对 DLL 也量地板，会把微软自己的
    #     Win7 部署件全判红。这一组把豁免的**边界**钉死：只豁 DLL、只豁地板这一格。
    _dll10 = _pe_build(major=10, minor=0, dll=True)
    _assert(pe["parse_pe"](_dll10)["is_dll"] is True,
            "IMAGE_FILE_DLL 位要真被读出来（这一位是豁免的唯一依据）")
    _assert(pe["parse_pe"](_pe_build(major=10, minor=0))["is_dll"] is False,
            "没置位的镜像不许被读成 DLL（否则豁免会漏到 EXE 上）")
    p, said = _pe_said(pe["check_one"], Path("api-ms-win-crt-runtime-l1-1-0.dll"), _dll10, (6, 1))
    _assert_eq(p, [], "官方 Win7 转发器的真实形状（DLL、子系统 10.0）必须放行——旧版在这里假红")
    _assert("地板这一格对 DLL 不适用" in said and "IMAGE_FILE_DLL" in said,
            "豁免必须说出来并点名依据：静默给绿会让「地板筛过了」这句话没人能核对")
    # 判的是 COFF 那一位，不是文件名：两个方向各造一个「名字与位不符」的镜像。
    p, _ = _pe_said(pe["check_one"], Path("forwarder.exe"), _dll10, (6, 1))
    _assert_eq(p, [], "名字叫 .exe 但 IMAGE_FILE_DLL 在位 → 按位豁免（判名字就是两份真相）")
    p, _ = _pe_said(pe["check_one"], Path("fake.dll"), _pe_build(major=10, minor=0), (6, 1))
    _assert(any("要求 Windows 10.0" in x for x in p),
            "名字叫 .dll 但没置 DLL 位 → 照红：豁免不许按文件名发（被壳改名的 EXE 就钻过去了）")
    # 豁免只豁地板这一格：导入表黑名单、0.00 兜底一格不松。
    p, _ = _pe_said(pe["check_one"], Path("bad.dll"), _pe_build(
        major=10, minor=0, dll=True,
        imports={"api-ms-win-core-synch-l1-2-0.dll": ["WaitOnAddress"]}), (6, 1))
    _assert(any("0xc0000139" in x for x in p) and any("连模块都加载不到" in x for x in p),
            "DLL 豁免不许把导入表黑名单一起免掉：Win8+ 函数与模块两格都还要红")
    p, _ = _pe_said(pe["check_one"], Path("packed.dll"), _pe_build(major=0, minor=0, dll=True), (6, 1))
    _assert(any("0.00" in x for x in p), "DLL 的 0.00（被壳/被改过）照红：豁免范围只有「高于地板」那一格")

    # ── 8) 畸形镜像：三类截断 + 一类越界都要落成「带文件名的红话」，一条都不许抛栈 ──
    #     探这一组时当场抓到一条真缺陷：DLL 名被切断时 bytes.index(b"\x00") 抛 ValueError
    #     把整道门炸掉——日志里既没文件名也没「哪一格不合格」。判据自己抛栈比误判更贵。
    full = _pe_build(imports={"kernel32.dll": ["Sleep"]})
    cases = (
        ("没有 PE 签名", 0x40, "找不到 PE 签名"),
        ("节区头越界", 360, "节区头越界"),
        ("导入表 DLL 名被切断", 1069, "没有 NUL 结束符"),
        ("导入函数名被切断", 1096, "没有 NUL 结束符"),
    )
    for label, cut, needle in cases:
        try:
            p, _ = _pe_said(pe["check_one"], Path("t.dll"), _pe_build(
                imports={"kernel32.dll": ["Sleep"]}, cut=cut), (6, 1))
            _assert(any(needle in x for x in p), f"{label}要判红并点名「{needle}」（实际={p}）")
        except Exception as e:
            _assert(False, f"{label}不许把门炸掉：抛的是 {type(e).__name__}: {_one_line(e)}")
    _assert(len(full) > 1096,
            f"夹具自身得真的比第 8 组最大的那个 cut 还长（否则那四条是在比空气）：{len(full)} 字节")
    p, _ = _pe_said(pe["check_one"], Path("r.dll"), _pe_build(
        imports={"a.dll": ["f"]}, bad_import_rva=True), (6, 1))
    _assert(any("落在任何节区之外" in x for x in p), "导入表 RVA 指向节区外要判红：镜像被改过不能算扫过")

    # ── 9) 命令行层：五种「没输入 / 输入读不到」的调用都不许退 0 ──
    for label, argv, needle in (
        ("一个目标都没给", [], "一个目标都没给"),
        ("--floor 写法错", [str(payload / "B2bCollectTool.exe"), "--floor", "6,1"], "--floor 写法不对"),
        ("目标文件不在位", [str(root / "不存在.exe")], "文件不在位"),
        ("--scan-dir 目录不在位", ["--scan-dir", str(root / "没有这个目录")], "指向的目录不在位"),
        ("--scan-dir 里没有 PE", ["--scan-dir", str(empty)], "没有任何 PE"),
    ):
        rc, out = _pe_said(pe["main"], argv)
        _assert_eq(rc, 1, f"{label}必须退 1（喊了错还退 0，workflow 只看退出码）")
        _assert(needle in out, f"{label}要说清为什么红：{_one_line(out)[-140:]}")
    rc, out = _pe_said(pe["main"], [str(payload / "apiset_stub_0.dll")])
    _assert_eq(rc, 0, "只给一个契约桩文件（不带 --scan-dir）也要退 0：同一条判据不该认两种口径")

    # ── 10) 随包交叉核对：把「加载不到」这句话拿包里的字节对拍 ──
    #     由来：run#2 那 6 项 B 类里有 5 项说的是 api-ms-win-core-synch-l1-2-0.dll
    #     「连模块都加载不到」，而同一次扫描显示**这个桩自己就在包里**。“加载不到”
    #     于是从一句要靠回忆微软 API-set 支持矩阵才敢说出的话，变成一个可实测的事实。
    xdir = root / "xcheck"
    xdir.mkdir()
    _stub_exports = ["WaitOnAddress", "WakeByAddressAll", "WakeByAddressSingle"]
    (xdir / "api-ms-win-core-synch-l1-2-0.dll").write_bytes(_pe_build(exports=_stub_exports))
    (xdir / "ucrtbase.dll").write_bytes(_pe_build(
        imports={"api-ms-win-core-synch-l1-2-0.dll": ["WaitOnAddress"]}))
    rc, out = _pe_said(pe["main"], ["--scan-dir", str(xdir), "--floor", "6.1"])
    _assert_eq(rc, 0, f"依赖名能在随包桩的导出表里实测到 → 不该判红（实际 rc={rc}）：{_one_line(out)[-200:]}")
    _assert("随包桩实测**导出了这一格**" in out, "放行那几格要把实测依据打出来，不能只留一个绿")
    _assert("1 格经随包桩导出表实测放行" in out, "结论行要报出实测放行了几格（区分「筛过了」与「筛不到」）")
    _assert("随包交叉核对：开" in out, "本地扇扫要声明交叉核对是开着的")

    # 10b) 实测不到就照红：随包桩在位、但名字表里根本没这一格
    x2 = root / "xcheck_missing"
    x2.mkdir()
    (x2 / "api-ms-win-core-synch-l1-2-0.dll").write_bytes(_pe_build(exports=["SomethingElse"]))
    (x2 / "ucrtbase.dll").write_bytes(_pe_build(
        imports={"api-ms-win-core-synch-l1-2-0.dll": ["WaitOnAddress"]}))
    rc, out = _pe_said(pe["main"], ["--scan-dir", str(x2), "--floor", "6.1"])
    _assert_eq(rc, 1, "随包桩在位但导出表里没有这一格，必须照红（在位≠放行）")
    _assert("它的导出表里没有这一格" in out, f"红话要说清是「实测过了、没有」：{_one_line(out)[-160:]}")

    # 10c) 系统 DLL 不随包 → 交叉核对救不了它（这就是 selenium-manager.exe 的真结局）
    rc, out = _pe_said(pe["main"], ["--scan-dir", str(payload), "--floor", "6.1"])
    _assert("包里没有同名模块可比对，按最严口径判" not in out,
            "这一扫里没有命中项，不许凭空报出一句「按最严口径」（话只能为真事说）")
    k2 = root / "kernel32_case"
    k2.mkdir()
    (k2 / "helper.exe").write_bytes(_pe_build(imports={"kernel32.dll": ["GetSystemTimePreciseAsFileTime"]}))
    rc, out = _pe_said(pe["main"], ["--scan-dir", str(k2), "--floor", "6.1"])
    _assert_eq(rc, 1, "kernel32 不随包 → 交叉核对不替它兜底，这一格必须还是红")
    _assert("包里没有同名模块可比对" in out, "红话要分清是「实测没有」还是「没东西可实测」——后者才能推出去剪")

    # 10d) 随包桩在位但读不出 → 退回最严口径，不许当成「筛过了」
    x3 = root / "xcheck_unreadable"
    x3.mkdir()
    (x3 / "api-ms-win-core-synch-l1-2-0.dll").write_bytes("这不是一个 PE 镜像".encode("utf-8"))
    (x3 / "ucrtbase.dll").write_bytes(_pe_build(
        imports={"api-ms-win-core-synch-l1-2-0.dll": ["WaitOnAddress"]}))
    rc, out = _pe_said(pe["main"], ["--scan-dir", str(x3), "--floor", "6.1"])
    _assert_eq(rc, 1, "随包桩自己读不出时不许退成绿：读不出是「筛不到」，不是「通得了」")
    # 三条红话各自钉死。上一版这里只断言了「导出表读不出」这几个字，而**函数级与模块级
    # 两条分支都会打出含这几个字的话** —— 那条判据分不清是谁放的红，拆掉其中一条它照样绿。
    # 这是变异复验逐条打回去时暴露的（本段第 6 条假绿）。
    _assert("随包桩的导出表读不出" in out,
            f"函数名那一格要说清是「读不出」而不是「实测没有」：{_one_line(out)[-160:]}")
    _assert("随包桩在位但导出表读不出" in out,
            "模块名那一格同样要退回红并说明原因（两条分支各自一条红话，不许共用一句含糊的）")
    _assert("交叉核对这一格退回最严口径" in out,
            "「读不出」这条路径本身要留一行日志：静默缓存 None 与报错后缓存 None 在产物里长得一样")

    # ── 11) 实测剪：只在显式给了名单时才动产物，四道框逐道验 ──
    def _fresh_prune(tag):
        d = root / tag
        d.mkdir()
        (d / "B2bCollectTool.exe").write_bytes(_pe_build(imports={"python38.dll": ["Py_Main"]}))
        (d / "selenium-manager.exe").write_bytes(_pe_build(imports={"kernel32.dll": ["WaitOnAddress"]}))
        return d

    d = _fresh_prune("prune_ok")
    rc, out = _pe_said(pe["main"], ["--scan-dir", str(d), "--floor", "6.1",
                                    "--prune-match", "selenium-manager.exe",
                                    "--prune-keep", "B2bCollectTool.exe"])
    _assert_eq(rc, 0, f"实测剪掉那块加载不了的 helper 后复检必须退 0（实际 rc={rc}）")
    _assert(not (d / "selenium-manager.exe").exists() and (d / "B2bCollectTool.exe").exists(),
            "只该剪掉实测加载不了的那一个，入口 exe 一个字节都不能动")
    _assert("实测剪：selenium-manager.exe —— 依据" in out,
            "剪之前要把**剪它的实测依据**一同打出来（只说「剪了」等于静默改产物）")
    _assert("复检剩下 1 个镜像" in out, "剪完必须真的复检一次，而不是剪了就把本趟结论改成绿")

    d = _fresh_prune("prune_none")
    rc, out = _pe_said(pe["main"], ["--scan-dir", str(d), "--floor", "6.1",
                                    "--prune-match", "这一版不存在的名字.exe"])
    _assert_eq(rc, 1, "名单没命中不等于合格：那块加载不了的还在包里，就得还是红")
    _assert("一个都没命中" in out, "名单今天没做上要说出来（将来 selenium 换布局/不再随包时只看绿区分不出来）")

    d = _fresh_prune("prune_keep")
    rc, out = _pe_said(pe["main"], ["--scan-dir", str(d), "--floor", "6.1",
                                    "--prune-match", "*.exe", "--prune-keep", "B2bCollectTool.exe"])
    _assert((d / "B2bCollectTool.exe").exists(), "入口 exe 即使被通配命中也不许剪")

    d = _fresh_prune("prune_over")
    for i in range(6):
        (d / f"helper_{i}.exe").write_bytes(_pe_build(imports={"kernel32.dll": ["WaitOnAddress"]}))
    rc, out = _pe_said(pe["main"], ["--scan-dir", str(d), "--floor", "6.1",
                                    "--prune-match", "helper*.exe", "--max-prune", "4"])
    _assert_eq(rc, 1, "一次要剪超过上限必须停下来问人，而不是接着删")
    _assert(all((d / f"helper_{i}.exe").exists() for i in range(6)),
            "上限触发时一个字节都不该被删（问人之前先把东西删了，就不是问人了）")

    d = _fresh_prune("prune_named")
    rc, out = _pe_said(pe["main"], [str(d / "selenium-manager.exe"), "--floor", "6.1",
                                    "--prune-match", "selenium-manager.exe"])
    _assert_eq(rc, 1, "被明确点名的单个文件不许被剪（那是要判的目标，不是产物目录里的零件）")
    _assert((d / "selenium-manager.exe").exists(), "上面那条不能只是嘴上说，文件得真的还在")

    # ── 12) 流水线接线：安装包必须在**它被编出来之后**才检，剪的名单要写在脸上 ──
    #     由来（2026-11-20 取证时读到）：生成器把「扫 dist 目录」与「检安装包」写在了同
    #     一步（第 14 步），可安装包是第 21 步才由 makensis 造出来的——那一行只能得到一句
    #     「文件不在位」。那是**顺序错**，不是产物错，而且它被那 49 项假红盖住了：不先修
    #     假红，这一条还要再藏一轮。这里把顺序钉成判据，否则下次合并又会被摸回去。
    leg_path = _REPO / ".github" / "workflows" / "build-b2bcollect-legacy.yml"
    leg = leg_path.read_text(encoding="utf-8")
    _steps = [(m.group(1), m.start()) for m in
              re.finditer(r"^    - name: (.+)$", leg, re.M)]
    _idx = {n: i for i, (n, _) in enumerate(_steps)}
    _pe_step = next((n for n in _idx if n.startswith("PE floor gate (")), "")
    _pe_setup = next((n for n in _idx if "PE floor gate on the installer" in n), "")
    _assert(bool(_pe_step) and bool(_pe_setup),
            f"兼容通道要同时有「扫产物目录」与「检安装包」那两步（实际={_pe_step!r}/{_pe_setup!r}）")
    if _pe_step and _pe_setup:
        _assert(_idx[_pe_setup] > _idx[next(n for n in _idx if n.startswith("Build Windows installer"))],
                "安装包的 PE 地板门必须排在「编安装包」之后：在第 14 步检一个还不存在的文件，"
                "得到的只会是一句「文件不在位」这种假红")
        # 只取那一步**自己的**正文（到下一步起点为止）。上一版这里一路切到「检安装包」
        # 那一步，中间隔着 doctor / 窗口冒烟 / 编安装包三步——编安装包那步当然合法地写
        # 着 OUT_FILE，于是这条判据把别人的正文当成嫌疑对象，报出一条假红：「前提超出
        # 适用范围」这一族缺陷，判据自己也躲不过。
        def _body(name):
            i = _idx[name]
            return leg[_steps[i][1]:_steps[i + 1][1]] if i + 1 < len(_steps) else leg[_steps[i][1]:]

        _scan_body = _body(_pe_step)
        _assert("B2bCollectToolSetup" not in _scan_body,
                "扫目录那一步不该再检安装包：那一步跑的时候它还没被编出来，只能得到一句「文件不在位」")
        _assert("--prune-match selenium-manager.exe" in _scan_body
                and "--prune-keep B2bCollectTool.exe" in _scan_body,
                "实测剪的名单要写在流水线上：只看 yml 就该知道包里少了一个什么、为什么少")
        _out_file = re.search(r'OUT_FILE:\s*"([^"]+)"',
                              leg[_steps[_idx[next(n for n in _idx if n.startswith("Build Windows installer"))]][1]:
                                  _steps[_idx[_pe_setup]][1]])
        _assert(_out_file is not None,
                "编安装包那一步要把产出文件名钉在 env 里（否则下一句对拍没有参照）")
        if _out_file:
            _assert(_out_file.group(1) in _body(_pe_setup),
                    f"安装包 PE 门检的名字要和流水线自己声明的 OUT_FILE 是同一个（{ _out_file.group(1)}）："
                    "改名只改一边就是「检了一个不存在的包」")

    # 12b) yml 是生成物的真值：重跑生成器不许让工作区改动（改了就是两边已经飘了）
    _before = leg_path.read_bytes()
    _gen = _REPO / "debug" / "gen_legacy_workflow.py"
    _r = subprocess.run([sys.executable, str(_gen)], capture_output=True, text=True,
                        encoding="utf-8", errors="replace")
    _after = leg_path.read_bytes()
    if _after != _before:                       # 红也要把工作区还原：这一节不改交付物
        leg_path.write_bytes(_before)
    _assert_eq(_r.returncode, 0, f"重跑生成器要成功（实际 rc={_r.returncode}）：{_one_line(_r.stdout)[-160:]}")
    # 不能用 _assert_eq 比两个大字节对象：它把两边都 !r 打进日志、**通过也打**，
    # 等于每跑一次就往交付日志里灌两遍整份 workflow（将近 800 行）。这里只保留
    # 「一致 / 不一致 + 第一个差异字节号」，后者才是修东西的人需要的那一格。
    if _after == _before:
        _assert(True, "兼容通道 yml 与生成器输出逐字节一致（不一致=有人只改了 yml，下次重跑生成器会把它抹掉）")
    else:
        _d = next((i for i, (a, b) in enumerate(zip(_before, _after)) if a != b),
                  min(len(_before), len(_after)))
        _assert(False, f"兼容通道 yml 与生成器输出不再逐字节一致：第一个差异在第 {_d} 字节、"
                      f"两边分别 {len(_before)}/{len(_after)} 字节——只改 yml 不改生成器的话，"
                      "下次重跑生成器会把那一改整个抹掉")

    # ── 12c) UCRT 替换的接线：必须在 PE 地板门之前，且 core 剪枝不许复活 ──
    #     由来（2026-11-21 legacy run#4 取证）：42 项存根红只是第一层，Win11 版 ucrtbase
    #     自己就依赖 Win8 契约模块——光剪存根不够，必须装配期整套换 Win7 版 UCRT。
    #     run#8 又证伪了反方向：替换已整套覆盖 core 桩，留着剪枝反倒剪掉支撑桩。
    _wprobs = _ucrt_wiring_problems(leg)
    _assert_eq(_wprobs, [], "UCRT 替换的接线不合格：" + "；".join(_wprobs))

    # ── 12d) 替换脚本自己的行为：合成 PE 夹具，不合格替换件不许进包 ──
    #     第二轮重设计（2026-11-21 用户拍板修门后）：旧 _bad10 格拿「DLL 子系统 10.0」
    #     当拒因——新门下 DLL 豁免地板（微软官方 Win7 转发器就是 10.0），那一格语义已失效。
    #     fail-closed 改钉仍然成立的形状：替换件导入 Win8+ 函数而随包桩喂不饱、依赖
    #     Win8 契约模块而桩不在、半套替换、布局缺失；另加「带桩表放行」正格与覆盖差分格。
    sw = _swap_ns()
    _assert(_SWAP_PATH.is_file(), f"UCRT 替换脚本必须在位（不在位这一组就是在空转）：{_SWAP_PATH}")

    def _ucrt_src(d, major=6, minor=1, imports=None, synch=False):
        d.mkdir(parents=True, exist_ok=True)
        (d / "ucrtbase.dll").write_bytes(_pe_build(major=major, minor=minor, dll=True, imports=imports))
        for n in ("api-ms-win-crt-runtime-l1-1-0.dll", "api-ms-win-crt-stdio-l1-1-0.dll"):
            (d / n).write_bytes(_pe_build(dll=True))
        if synch:   # a10 的真实形状：自带 synch 桩（实测导出 sleep/waitonaddress 等 17 个名字）
            (d / "api-ms-win-core-synch-l1-2-0.dll").write_bytes(_pe_build(dll=True, exports=["WaitOnAddress"]))
        return d

    def _ucrt_dist(d):
        (d / "_internal").mkdir(parents=True, exist_ok=True)
        (d / "_internal" / "ucrtbase.dll").write_bytes(_pe_build(major=10, minor=0, dll=True))
        for n in ("api-ms-win-crt-runtime-l1-1-0.dll", "api-ms-win-crt-stdio-l1-1-0.dll"):
            (d / "_internal" / n).write_bytes(_pe_build(major=10, minor=0, dll=True))
        return d

    _sw_root = Path(tempfile.mkdtemp(prefix="ucrt_swap_"))
    _good_src = _ucrt_src(_sw_root / "src_good", imports={"kernel32.dll": ["Sleep"]})
    _d = _ucrt_dist(_sw_root / "dist_ok")
    _old_bytes = (_d / "_internal" / "ucrtbase.dll").read_bytes()
    _rc, _out = _pe_said(sw["swap"], _d, _good_src)
    _assert_eq(_rc, 0, f"合格替换件（地板 6.1、只依赖 kernel32）必须放行（实际 rc={_rc}）：{_one_line(_out)[-160:]}")
    _assert((_d / "_internal" / "ucrtbase.dll").read_bytes() == (_good_src / "ucrtbase.dll").read_bytes()
            and (_d / "_internal" / "ucrtbase.dll").read_bytes() != _old_bytes,
            "替换必须真的落到字节上：只改日志不改文件等于没换")
    _assert((_d / "_internal" / "api-ms-win-crt-runtime-l1-1-0.dll").read_bytes()
            == (_good_src / "api-ms-win-crt-runtime-l1-1-0.dll").read_bytes(),
            "crt 转发器也要整套换：留一个 Win11 版 PE 门照样红")
    _assert("已替换 _internal/ucrtbase.dll" in _out, "逐件报账：换了哪几件要写在日志里")

    # 带桩表放行（run#5 教训的正面那一半）：VS2015 版 ucrtbase 也依赖 synch-l1-2-0，
    # 但 a10 自带那个桩——无桩表的最严口径会把微软官方件误判成不合格。
    # 真实 a10 里 ucrtbase 导入的是 Sleep（老函数，不走函数级黑名单，靠模块在位+覆盖对拍放行）；
    # 这里故意造「导入 WaitOnAddress（Win8 起）且桩导出它」的合成形状，才能量到函数级
    # 放行话术那一格——拿 Sleep 造夹具量不到它（首版就造错了，真跑当场逮到）。
    _bsrc = _ucrt_src(_sw_root / "src_bundle", imports={"api-ms-win-core-synch-l1-2-0.dll": ["WaitOnAddress"]}, synch=True)
    _bdist = _ucrt_dist(_sw_root / "dist_bundle")
    # 真实 dist 里 42 个存根都在（PyInstaller 收进来的），synch 桩也在、只是 Win11 版
    # 没导出——夹具必须照这个形状造：dist 里没有桩的话 core 替换不会发生（只换 dist 已有的），
    # 那不是 a10 的场景，是另一族问题。
    (_bdist / "_internal" / "api-ms-win-core-synch-l1-2-0.dll").write_bytes(_pe_build(major=10, minor=0, dll=True))
    _rc, _out = _pe_said(sw["swap"], _bdist, _bsrc)
    _assert_eq(_rc, 0, f"替换件依赖 synch 但随包桩实测导出 WaitOnAddress → 必须放行（实际 rc={_rc}）：{_one_line(_out)[-160:]}")
    _assert("随包桩实测**导出了这一格**" in _out, "放行要把实测依据打出来（与 PE 门同一句话术，不抄第二份）")

    # 旧 _bad10 格的新形状：DLL 地板豁免后，fail-closed 钉在「导入 Win8+ 函数而桩喂不饱」。
    # 选 WakeByAddressSingle（同为 Win8 起、与桩导出的 WaitOnAddress 不同名）：桩在位但
    # 没有这一格，函数级黑名单是唯一拦它的层——体检拆掉后红改由覆盖对拍报（13b 探针靠这个）。
    _bad10 = _ucrt_src(_sw_root / "src_badfn",
                       imports={"api-ms-win-core-synch-l1-2-0.dll": ["WakeByAddressSingle"]}, synch=True)
    _rc, _out = _pe_said(sw["swap"], _ucrt_dist(_sw_root / "dist_bad10"), _bad10)
    _assert_eq(_rc, 1, "替换件导入 Win8+ 函数而随包桩没有那一格时必须 fail-closed：没量过的 DLL 不许进包")
    _assert("过不了 Win7 地板门" in _out and "0xc0000139" in _out,
            "拒绝替换件要说清是哪一格（函数级红话带错误码），不许只给一个退出码")

    _bad8 = _ucrt_src(_sw_root / "src_win8dep",
                      imports={"api-ms-win-core-synch-l1-2-0.dll": ["Sleep"]})
    _rc, _out = _pe_said(sw["swap"], _ucrt_dist(_sw_root / "dist_bad8"), _bad8)
    _assert_eq(_rc, 1, "替换件依赖 Win8 契约模块而桩不在时必须 fail-closed：这正是 run#4 那第二层雷的形状")
    _assert("api-ms-win-core-synch-l1-2-0.dll" in _out, "拒绝话术要点名是哪个模块（照着能查）")

    _half = _ucrt_src(_sw_root / "src_half")
    (_half / "api-ms-win-crt-stdio-l1-1-0.dll").unlink()
    _rc, _out = _pe_said(sw["swap"], _ucrt_dist(_sw_root / "dist_half"), _half)
    _assert_eq(_rc, 1, "src 缺 crt 转发器时不许做半套替换：留下的 Win11 存根会继续红")
    _assert("api-ms-win-crt-stdio-l1-1-0.dll" in _out, "缺哪几个转发器要点名")

    _rc, _out = _pe_said(sw["swap"], _sw_root / "dist_nolayout", _good_src)
    _assert_eq(_rc, 1, "产物目录没有 _internal 时必须红：布局变了先查 PyInstaller，不许静默跳过")

    # 覆盖差分：dist 里有一张嘴替换前喂不饱（存量）、替换后仍喂不饱 → 不是新增，放行但报数；
    # 换进去的桩把存量那张嘴喂上了、却又新增另一张喂不饱的嘴 → 必须红。
    _gap_dist = _ucrt_dist(_sw_root / "dist_gap")
    (_gap_dist / "_internal" / "helper.dll").write_bytes(_pe_build(
        dll=True, imports={"api-ms-win-crt-heap-l1-1-0.dll": ["malloc"]}))
    _rc, _out = _pe_said(sw["swap"], _gap_dist, _good_src)
    _assert_eq(_rc, 0, f"存量缺口替换前后一模一样 → 不是这一步的责任，放行（实际 rc={_rc}）：{_one_line(_out)[-160:]}")
    _assert("存量缺口" in _out, "存量要如实报数不吞：静默放过等于把另一族问题藏进这一步的绿里")
    _gap_src = _ucrt_src(_sw_root / "src_gap")
    (_gap_src / "api-ms-win-crt-heap-l1-1-0.dll").write_bytes(_pe_build(dll=True, exports=["malloc"]))
    _gap_dist2 = _ucrt_dist(_sw_root / "dist_gap2")
    # 存量那张嘴替换前必须真喂得饱（dist 的 heap 桩导出 malloc+calloc），换进去的 src 桩
    # 只导出 malloc——calloc 才是**新增**缺口。若 dist 桩本来就没导出，calloc 替换前后
    # 都是同一条存量，差分口径放行是对的（首版夹具就造错了这个前提，真跑当场逮到）。
    (_gap_dist2 / "_internal" / "api-ms-win-crt-heap-l1-1-0.dll").write_bytes(
        _pe_build(major=10, minor=0, dll=True, exports=["malloc", "calloc"]))
    (_gap_dist2 / "_internal" / "helper.dll").write_bytes(_pe_build(
        dll=True, imports={"api-ms-win-crt-heap-l1-1-0.dll": ["malloc", "calloc"]}))
    _rc, _out = _pe_said(sw["swap"], _gap_dist2, _gap_src)
    _assert_eq(_rc, 1, "替换把存量喂上了却又新增缺口（calloc 没人导出）→ 必须 fail-closed")
    _assert("新增" in _out and "calloc" in _out, "红话要说清是「新增」并点名缺的那一格（照着能查）")

    # ── 13) 变异复验：上面每一格都要能被「退回修前原样」精准杀红 ──
    #     只验新代码通过是不够的：那 43 条假红当初也是「绿的」。这里把源码改回出事那一版
    #     （以及改坏比较方向、拆掉 API 筛查、拆掉 NUL 保护、拆掉排序兜底），逐条要求判据转红。
    def _mutate(old, new, label, probe, expect, note):
        src = _PE_PATH.read_text(encoding="utf-8")
        if src.count(old) != 1:
            _assert(False, f"变异「{label}」的锚点出现 {src.count(old)} 次（不是 1 次就不知道改到哪一格）")
            return
        got = probe(_pe_ns(src.replace(old, new, 1)))
        _assert_eq(got, expect, f"变异「{label}」{note}")

    _mutate('    out["no_imports"] = not imp_rva\n    if out["no_imports"]:',
            '    if not imp_rva:\n        raise PeError("导入表 RVA 为 0：真产物不可能这样")\n    if False:',
            "退回「无导入表就 raise」那一版",
            lambda m: _pe_said(m["main"], ["--scan-dir", str(payload), "--floor", "6.1"])[1].count("::error::"),
            43, "必须让那 43 个契约桩重新报红（否则第 4 组是在自我证明）")
    _mutate("if got > tuple(floor):", "if got < tuple(floor):",
            "把地板比较方向反过来",
            lambda m: len(_pe_said(m["check_one"], Path("needs10.exe"), _pe_build(major=10, minor=0), (6, 1))[0]),
            0, "会让要求 Win10 的镜像被判为达标——本轮取证时我先入为主怀疑的就是这一格，反着改才发现方向本来就是对的")
    _mutate("if fn and fn in POST_WIN7_API:", "if False and fn in POST_WIN7_API:",
            "拆掉 Win8+ API 名单筛查",
            lambda m: len(_pe_said(m["check_one"], Path("selenium-manager.exe"), _pe_build(
                imports={"kernel32.dll": ["GetSystemTimePreciseAsFileTime"]}), (6, 1))[0]),
            0, "会让 run#2 那 6 项 B 类真发现一起消失（假绿比假红贵）")
    _mutate('    end = data.find(b"\\x00", off)\n    if end < 0:',
            '    end = data.index(b"\\x00", off)\n    if False:',
            "把 NUL 保护退回裸 index",
            lambda m: "raised" if _raises(lambda: m["check_one"](Path("t.dll"), _pe_build(
                imports={"kernel32.dll": ["Sleep"]}, cut=1069), (6, 1))) else "red",
            "raised", "会让第 8 组那两条截断判据重新变成炸栈")
    _mutate('key=lambda x: (x[0], x[1] or "")', 'key=lambda x: (x[0], x[1])',
            "拆掉排序键对 None 的兜底",
            lambda m: "raised" if _raises(lambda: m["check_one"](Path("mixed.dll"), mixed, (6, 1))) else "ok",
            "raised", "会让线上安装包首跑那次 TypeError 复现（第 6 组必须接住它）")

    # DLL 豁免是 2026-11-21 新改的判定分支，四个方向各打一遍：拆豁免（退回旧假红）、
    # 豁免漏到 EXE（假绿，比假红贵）、按文件名而不按位判（被壳改名就钻过去）、静默豁免。
    _mutate('        if pe["is_dll"]:', '        if False:',
            "拆掉 DLL 豁免（退回 2026-11-21 修前原样）",
            lambda m: len(_pe_said(m["check_one"], Path("crt.dll"), _dll10, (6, 1))[0]),
            1, "必须让官方 Win7 转发器形状重新假红——否则 7b 那格是在自我证明")
    _mutate('        if pe["is_dll"]:', '        if True:',
            "把豁免扩到 EXE（主镜像也免检地板）",
            lambda m: len(_pe_said(m["check_one"], Path("needs10.exe"), _pe_build(major=10, minor=0), (6, 1))[0]),
            0, "会让要求 Win10 的主程序被判达标——豁免的边界就是 EXE/DLL 这条线")
    _mutate('"is_dll": bool(chars & 0x2000),', '"is_dll": False,',
            "把 IMAGE_FILE_DLL 位读成恒 False",
            lambda m: len(_pe_said(m["check_one"], Path("crt.dll"), _dll10, (6, 1))[0]),
            1, "位读不出来豁免就失效：7b 第一格必须能逮住这一种坏法")
    _mutate('            print(f"· {path.name}: 子系统版本 {got[0]}.{got[1]:02d} 高于地板，但它是 DLL"\n'
            '                  f"（IMAGE_FILE_DLL 在位）——加载器不按这一格拒载 DLL（微软官方 Win7 "\n'
            '                  f"app-local UCRT 转发器就是 10.0）：地板这一格对 DLL 不适用，导入表照筛")',
            '            pass  # 变异：静默豁免',
            "豁免不再说出来（拆掉那行日志）",
            lambda m: "地板这一格对 DLL 不适用" in _pe_said(
                m["check_one"], Path("crt.dll"), _dll10, (6, 1))[1],
            False, "静默给绿会让「地板筛过了」没人能核对——少判一格必须说出来")

    # 交叉核对那几格是本轮新写的，越新越要按同一套规矩复验：绿不算数，坏回去必须红。
    _mutate("            if exps is not None and fn.lower() in exps:",
            "            if exps is not None:",
            "实测放行只看「桩在位且读得出来」，不看名字表里到底有没有这一格",
            lambda m: _pe_said(m["main"], ["--scan-dir", str(x2), "--floor", "6.1"])[0],
            0, "会让 10b 那格退成绿——把「在位」当成「有这一格」正是这一族缺陷的原形")
    _mutate("            if in_bundle and exps is not None:",
            "            if False:",
            "拆掉模块级（POST_WIN7_DLL）的随包实测放行",
            lambda m: _pe_said(m["main"], ["--scan-dir", str(xdir), "--floor", "6.1"])[0],
            1, "会让 10 那格重新报红：函数名实测到了、模块名却仍靠名单猜，等于只修了一半")
    _mutate('                    else "随包桩的导出表读不出" if in_bundle',
            '                    else "" if in_bundle',
            "把「读不出」那一格的红话抹成空串",
            lambda m: "随包桩的导出表读不出" in _pe_said(
                m["main"], ["--scan-dir", str(x3), "--floor", "6.1"])[1],
            False, "会让红话重新变成一句含糊的尾巴，读日志的人分不清是实测没有还是读不出")
    _mutate('                print(f"· 随包桩 {dll_lower} 的导出表读不出（{type(e).__name__}: "\n'
            '                      f"{str(e).strip()}）：交叉核对这一格退回最严口径")',
            '                pass  # 变异：读不出时静默缓存',
            "读不出时不再说明原因（把那一行日志整段拆掉）",
            lambda m: "退回最严口径" in _pe_said(
                m["main"], ["--scan-dir", str(x3), "--floor", "6.1"])[1],
            False, "会让「读不出」静默退成 None：日志里看不到这一格为什么按最严口径判红")

    # 13b) UCRT 替换那几条判据（12c/12d）同样要能被打红：从未红过的判据等于没有判据。
    #      接线几条的变异对象是生成器（yml 是生成物，只改 yml 会被 12b 抹掉）：改完重跑
    #      生成器再判，判完逐字节还原两份文件（不用 git checkout，备份还原法）。
    _gen_path = _REPO / "debug" / "gen_legacy_workflow.py"

    def _mutate_gen(old, new, label, probe, expect, note):
        gsrc = _gen_path.read_bytes()
        ysrc = leg_path.read_bytes()
        if gsrc.decode("utf-8").count(old) != 1:
            _assert(False, f"变异「{label}」的锚点在生成器里出现 "
                           f"{gsrc.decode('utf-8').count(old)} 次（不是 1 次就不知道改到哪一格）")
            return
        try:
            _gen_path.write_bytes(gsrc.decode("utf-8").replace(old, new, 1).encode("utf-8"))
            _r = subprocess.run([sys.executable, str(_gen_path)], capture_output=True,
                                text=True, encoding="utf-8", errors="replace")
            if _r.returncode != 0:
                _assert(False, f"变异「{label}」重跑生成器失败（rc={_r.returncode}）："
                               f"{_one_line(_r.stdout + _r.stderr)[-160:]}")
                return
            got = probe(leg_path.read_text(encoding="utf-8"))
            _assert_eq(got, expect, f"变异「{label}」{note}")
        finally:
            _gen_path.write_bytes(gsrc)
            leg_path.write_bytes(ysrc)
        _assert(_gen_path.read_bytes() == gsrc and leg_path.read_bytes() == ysrc,
                f"变异「{label}」还原后必须逐字节回到原样（还原不干净＝下一节的输入被污染）")

    _mutate_gen("        UCRT_SWAP,\n        PE_FLOOR,", "        PE_FLOOR,",
                "拆掉装配期换 UCRT 那一步（退回 run#4 原样）",
                lambda t: len(_ucrt_wiring_problems(t)), 1,
                "必须让 12c 精准报出「没有那一步」——否则替换被摸掉时没人知道")
    _mutate_gen("          --prune-match selenium-manager.exe \\\\\n",
                "          --prune-match selenium-manager.exe \\\\\n"
                "          --prune-match api-ms-win-core-*.dll \\\\\n",
                "把退役的 core 存根剪枝加回去（退回 run#4 形状）",
                lambda t: len(_ucrt_wiring_problems(t)), 1,
                "必须让反转后的 12c 精准报出「剪枝还留着」：加回去就会剪掉 swap 带进来的 a10 支撑桩")
    _mutate_gen("        UCRT_REDIST,\n        UCRT_SWAP,", "        UCRT_SWAP,",
                "拆掉钉死 sha256 的 redist 下载步（退回自动找 SDK 那条被证伪的路）",
                lambda t: len(_ucrt_wiring_problems(t)), 1,
                "必须让 12c 报出「没有下载步」：替换源字节不再可信时没人知道")
    _mutate_gen("da66717784c192f1004e856bbcf7b3e13b7bf3ea45932c48e4c9b9a50ca80965",
                "0" * 64,
                "把 redist 的 sha256 钉漂掉",
                lambda t: len(_ucrt_wiring_problems(t)), 1,
                "必须让 12c 报出「哈希漂了」：历史版字节不该变，静默换源就是这一格")
    _mutate_gen('          --redist "$GITHUB_WORKSPACE/vc2015u3_x64.exe"',
                '          --ucrt-src "$GITHUB_WORKSPACE/sdk_ucrt"',
                "把替换改回自动找 SDK 的参数形状（run#5 证伪的那条路）",
                lambda t: len(_ucrt_wiring_problems(t)), 1,
                "必须让 12c 报出「没走 --redist」：runner 只有 26100 一档，那份 ucrtbase 是 Win10 构建")

    def _swap_mutate(old, new, label, probe, expect, note):
        src = _SWAP_PATH.read_text(encoding="utf-8")
        if src.count(old) != 1:
            _assert(False, f"变异「{label}」的锚点出现 {src.count(old)} 次（不是 1 次就不知道改到哪一格）")
            return
        got = probe(_swap_ns(src.replace(old, new, 1)))
        _assert_eq(got, expect, f"变异「{label}」{note}")

    # 探针不能拿 rc 量：DLL 地板豁免后 _bad10 改钉函数级缺口，拆掉体检后覆盖对拍
    # 仍会拦它（WaitOnAddress 没人导出，rc 还是 1）——拿 rc 当探针会把「变异没生效」
    # 误读成「判据没在看」。改钉拒因话术本身：体检在 → 红话里有「过不了 Win7 地板门」；
    # 体检被拆 → 这句拒因消失（红改由覆盖对拍那一层报，话术不同）。
    def _floor_reject_gone(m):
        rc, out = _pe_said(m["swap"], _ucrt_dist(_sw_root / "dist_mut10"), _bad10)
        return "过不了 Win7 地板门" in out
    _swap_mutate("    if problems:", "    if False:",
                 "拆掉替换件的地板体检（没量过也敢往包里塞）",
                 _floor_reject_gone, False,
                 "必须让 12d 那格的体检拒因消失——fail-closed 是这一步存在的理由")
    _swap_mutate("    new = sorted(set(probs) - before)", "    new = sorted(set(probs))",
                 "把覆盖对拍从差分退回绝对口径（存量也算进替换责任）",
                 lambda m: _pe_said(m["swap"], _gap_dist, _good_src)[0], 1,
                 "必须让存量缺口那格重新变红：判据前提超出适用范围就是 run#5 正控首跑那 21 条假红的由来")


def _raises(fn) -> bool:
    """只为变异复验服务：把「抛异常」折成一个可比较的事实（判据自己不许靠抛栈报结论）。"""
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            fn()
    except Exception:
        return True
    return False


# 小节表放在所有 test_ 定义之后：上一版它在这里而新小节插在它下面，模块顶层求值时就
# NameError，整份套件一条也没跑（而只盯「有没有报红」的人会把这条读成「没红＝通过」）。
SECTIONS = (
    test_config_chain, test_oss_credentials, test_failure_taxonomy, test_gui_wording,
    test_deepseek_and_cache, test_doctor, test_delivery_chain, test_state_dir,
    test_clock_skew, test_time_basis, test_console_encoding, test_doctor_flag_parity,
    test_workflow_shell_syntax, test_packaging_manifest_gate,
    test_windows_onedir_delivery, test_state_file_never_relative_cwd,
    test_relaunch_chain_off_the_bundle, test_channel_failure_kind,
    test_app_translocation_relocation, test_path_discipline_gate,
    test_switch_discipline_gate,
    test_window_smoke_gate,
    test_launch_ledger_gate,
    test_protected_dir_never_blocks_startup,
    test_batch_state_watch_gate,
    test_bash_lookup_gate,
    test_path_label_gate,
    test_errexit_silent_death_gate,
    test_case_insensitive_filesystem_gate,
    test_pip_source_resilience_gate,
    test_section_pointer_gate,
    test_curl_bounds_gate,
    test_qt_shutdown_teardown_gate,
    test_state_file_protection_gate,
    test_package_weight_gate,
    test_artifact_step_cwd_gate,
    test_legacy_channel_gate,
    test_py38_floor_gate,
    test_chromedriver_source_gate,
    test_installer_env_gate,
    test_feature_table_gate,
    test_suite_exit_code_guard_gate,
    test_regression_suites_tracked_gate,
    test_no_dev_machine_paths_gate,
    test_temp_dir_single_source_gate,
    test_module_name_binding_gate,
    test_shell_errexit_posture_gate,
    test_line_ending_stance_gate,
    test_workflow_yaml_ctrl_gate,
    test_forensic_job_log_channel_gate,
    test_reason_one_handle_gate,
    test_tab_body_identity_gate,
    test_nav_evidence_gate,
    test_no_contact_accountability_gate,
    test_listing_accountability_gate,
    test_tree_cache_gate,
    test_python_floor_gate,
    test_page_ledger_gate,
    test_human_wait_lamp_gate,
    test_trap_handler_defined_gate,
    test_pe_floor_gate,
)


def main():
    print("═══ 打包态交付链 离线测试 ═══")
    for section in SECTIONS:
        _run(section)
    _cost = sorted(_SECTION_COST.items(), key=lambda kv: -kv[1])
    print("\n═══ 最慢小节（秒）：" + "、".join(f"{n} {s:.1f}" for n, s in _cost[:8]) + " ═══")
    print(f"    合计 {sum(s for _, s in _cost):.1f}s / {len(_cost)} 节")
    # 收尾硬判据：每一节拨走的运行形态开关都必须自己收回去。不查这一条的话，「邻居
    # 没收拾」这种缺陷要等下一节撞出一个与产品毫不相干的栈才会被发现——本轮 T 节把
    # sys.executable 还原成 None，红的是隔了一整节的 U 节（Path(None) 的 TypeError），
    # 排查的人第一反应是「新加的门禁有 bug」。当场点名比隔节报错便宜得多。
    _assert_eq(_switches_left(), [],
               "跑完全部小节，运行形态开关要和开跑前一模一样（拨走不收回＝下一节的输入不再由夹具决定）")
    # 夹具名的撞车检查放在收尾而不是某一节里：它是**跨节**的性质——AA 节跑到的时候，后面
    # 新加那一节的夹具名已经在文件里了，但那一节还没跑，按节判就会漏掉「下一节和这一节同名」。
    # 同一族的另一半是残留：哪一节的 finally 没清干净，下一节读到的是上一节的字节。
    _assert_eq(_fixture_name_collisions(), [],
               "全文件的夹具名在大小写不敏感文件系统上也不撞车（撞车＝两节读同一份字节）")
    _left = sorted(p.name for p in (_REPO / "debug").glob("zz_*"))
    _assert_eq(_left, [], "跑完全部小节，debug/ 下不该留下任何 zz_* 夹具（残留＝下一节的输入）")
    # 每一节都真的被跑到：新增一节却忘了登记进 SECTIONS，它就永远不会执行，而且从输出上
    # 看不出来——这正是 riskbird 那 13 个套件的形状（不是坏了，是压根没在跑）。
    _defined = {n for n, v in globals().items() if n.startswith("test_") and callable(v)}
    _missing = sorted(_defined - {s.__name__ for s in SECTIONS})
    _assert_eq(_missing, [], f"文件里每个 test_ 小节都在 SECTIONS 里（漏登记＝这一节从没跑过）")
    _assert_eq(len({s.__name__ for s in SECTIONS}), len(SECTIONS),
               "SECTIONS 里没有重名小节（重名会让一节被跑两遍、另一遍掩盖真红）")
    print(f"\n═══ 结果：{_PASS} 通过 / {_FAIL} 失败 ═══")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    rc = main()
    # 这一份自己也建过 QApplication（AE/AG 那些小节），而它是「套件的判定与退出码同不同源」
    # 的对拍者——自己把最后一段留给解释器拆，就是举着一条自己正在拦的缺陷。硬门第 23 条对它
    # 同样成立：结论打完，flush，然后由套件自己把退出码交给操作系统。
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(rc)
