# 展会数据清洗工具 — 跨 Mac 部署指南

## 一、工具概述

本工具将展会/企业名录原始数据清洗为**客户库导入模板**标准格式，核心能力：

- **手机号/固话严格分列**：基于 Google libphonenumber 判定，中国手机号 (1[3-9]\d{9}) 进手机号列，座机/400/800/境外号码进固话列
- **国内外智能分类**：国家/地区字段 → 电话归属区域 → 中国手机号兜底，三层优先级判定
- **多源数据适配**：不同展会的列结构差异自动映射
- **每个源文件 → 3 个输出**：国内、国内只留手机号、国外

---

## 二、文件清单（需要拷贝的文件）

```
enf_clean/                      ← 核心代码目录
├── __init__.py                 ← Python 包标识（空文件即可）
├── normalize.py                ← 文本规范化 + 国家名→ISO区域映射
├── phone.py                    ← 电话号码智能识别（libphonenumber）
├── clean_three_files.py        ← 清洗主脚本（本次任务产物）
├── clean_enf_customers.py      ← ENF光伏清洗脚本（参考模板）
├── validate_output.py          ← 输出质量校验
└── requirements.txt            ← Python 依赖清单
```

---

## 三、目标 Mac 环境准备

### 3.1 前置条件

| 项目 | 要求 | 检查命令 |
|------|------|---------|
| macOS | 12.0+ | `sw_vers` |
| Python | 3.9+ | `python3 --version` |
| Xcode CLT | 已安装 | `xcode-select --install`（若未装会弹窗提示） |
| Qoder IDE | 已安装 | 从官网下载即可 |

### 3.2 创建项目目录

```bash
# 在目标 Mac 上创建项目根目录
mkdir -p ~/PyCharmMiscProject
cd ~/PyCharmMiscProject
```

### 3.3 创建 Python 虚拟环境

```bash
# 创建虚拟环境（隔离依赖，不污染系统 Python）
python3 -m venv .venv

# 激活虚拟环境（每次打开新终端都需要执行）
source .venv/bin/activate

# 验证激活成功（命令行前面会出现 (.venv) 前缀）
which python
# 应输出: /Users/你的用户名/PyCharmMiscProject/.venv/bin/python
```

### 3.4 安装依赖

```bash
# 确保虚拟环境已激活，然后安装依赖
pip install -r enf_clean/requirements.txt

# 验证安装成功
python -c "import phonenumbers, pandas, openpyxl; print('✅ 依赖安装完成')"
```

### 3.5 准备源数据文件

将待清洗的 Excel 文件放入 `~/Downloads/` 目录，文件名需与脚本中的配置匹配：

| 默认文件名 | 说明 |
|-----------|------|
| `海洋展11.5数据.xlsx` | 海洋展原始数据 |
| `台球展11.5数据.xlsx` | 台球展原始数据 |
| `深海装备四关键词数据.xlsx` | 深海装备原始数据 |

> **如果文件名不同**，通过命令行参数指定（见第四节）。

---

## 四、运行清洗脚本

### 4.1 使用默认路径运行

```bash
cd ~/PyCharmMiscProject
source .venv/bin/activate
python -m enf_clean.clean_three_files
```

### 4.2 自定义源文件路径

```bash
python -m enf_clean.clean_three_files \
  --ocean ~/Downloads/海洋展新文件名.xlsx \
  --billiard ~/Desktop/台球展数据.xlsx \
  --deepsea /Volumes/U盘/深海装备.xlsx \
  --base-dir ~/Desktop/清洗结果
```

### 4.3 查看所有可用参数

```bash
python -m enf_clean.clean_three_files --help
```

---

## 五、输出结果

清洗完成后，输出目录结构如下：

```
~/PyCharmMiscProject/output/          ← 可通过 --base-dir 修改
├── 海洋展/
│   ├── 海洋展_国内_20261105_151532.xlsx
│   ├── 海洋展_国内只留手机号_20261105_151532.xlsx
│   └── 海洋展_国外_20261105_151532.xlsx
├── 台球展/
│   ├── 台球展_国内_20261105_151538.xlsx
│   ├── 台球展_国内只留手机号_20261105_151538.xlsx
│   └── 台球展_国外_20261105_151538.xlsx
└── 深海装备/
    ├── 深海装备_国内_20261105_151546.xlsx
    ├── 深海装备_国内只留手机号_20261105_151546.xlsx
    └── 深海装备_国外_20261105_151546.xlsx
```

每个文件严格遵循**客户库导入模板**格式：18 列双区块结构（两组 9 列完全相同）。

---

## 六、通过 Qoder IDE 使用

### 6.1 打开项目

1. 启动 Qoder IDE
2. `File → Open...` → 选择 `~/PyCharmMiscProject` 目录
3. 等待 IDE 索引完成

### 6.2 配置 Python 解释器

1. `Qoder IDE → Settings（⌘,）→ Project → Python Interpreter`
2. 点击齿轮图标 → `Add Interpreter → Existing`
3. 选择 `~/PyCharmMiscProject/.venv/bin/python`
4. 点击 `OK` 保存

### 6.3 在 IDE 中运行脚本

**方式一：右键运行**
1. 在项目树中找到 `enf_clean/clean_three_files.py`
2. 右键 → `Run 'clean_three_files'`

**方式二：终端运行**
1. 打开 IDE 内置终端（`View → Tool Windows → Terminal` 或按 `⌥F12`）
2. 虚拟环境会自动激活
3. 执行：`python -m enf_clean.clean_three_files`

**方式三：对话式运行**
1. 打开 Qoder AI 对话面板
2. 直接告诉 AI：`请运行展会数据清洗脚本`
3. AI 会自动执行并返回结果

### 6.4 修改源文件路径（无需改代码）

在 Qoder IDE 对话中直接说：
> "帮我用自定义路径运行清洗脚本，海洋展文件在 ~/Desktop/新数据.xlsx"

AI 会自动构造正确的命令行参数。

---

## 七、常见问题

### Q1: `ModuleNotFoundError: No module named 'phonenumbers'`
**原因**：虚拟环境未激活或依赖未安装。
**解决**：
```bash
source .venv/bin/activate
pip install -r enf_clean/requirements.txt
```

### Q2: `FileNotFoundError: [Errno 2] No such file or directory`
**原因**：源数据文件路径不对。
**解决**：用 `--ocean`/`--billiard`/`--deepsea` 参数指定正确路径，或把文件放到 `~/Downloads/`。

### Q3: 输出文件里的国家分类不对
**原因**：源数据的"国家/地区"列写法太特殊，映射表里没有。
**解决**：在 `enf_clean/normalize.py` 的 `COUNTRY_REGION` 字典中添加对应的国家名/代码映射。

### Q4: 如何在没有网络的环境安装依赖？
**解决**：在有网络的机器上先下载依赖包：
```bash
pip download -r enf_clean/requirements.txt -d ./packages
```
然后把 `packages/` 目录拷到目标机器离线安装：
```bash
pip install --no-index --find-links=./packages -r enf_clean/requirements.txt
```

### Q5: 想清洗其他展会的数据怎么办？
**方式一**：修改 `clean_three_files.py` 中的 `process_xxx()` 函数，适配新的列结构。
**方式二**：在 Qoder IDE 对话中告诉 AI：
> "帮我写一个新的清洗函数，源文件列名是：公司名、电话、邮箱、国家"

AI 会自动生成适配代码。

---

## 八、技术术语表

| 术语 | 含义 |
|------|------|
| **虚拟环境 (venv)** | Python 的隔离环境，每个项目的依赖互不干扰 |
| **依赖 (dependency)** | 脚本运行需要的第三方库，如 phonenumbers、pandas |
| **libphonenumber** | Google 开源的电话号码解析库，全球号码格式校验标准 |
| **E.164** | 国际电话号码格式标准，如 +8613800138000 |
| **ISO 3166-1 alpha-2** | 两位国家代码，如 CN（中国）、US（美国）、GB（英国） |
| **NFKC** | Unicode 规范化形式，统一全角/半角字符 |
| **客户库导入模板** | 18 列双区块 Excel 格式，前后 9 列内容完全一致 |
| **argparse** | Python 标准库，用于解析命令行参数 |
| **openpyxl** | Python 库，用于读写 Excel (.xlsx) 文件 |

---

## 九、快速部署命令（一键复制）

在目标 Mac 的终端中依次执行：

```bash
# 1. 创建项目目录
mkdir -p ~/PyCharmMiscProject && cd ~/PyCharmMiscProject

# 2. 从源 Mac 拷贝 enf_clean 目录（通过 AirDrop/U盘/网络）
# 假设已拷贝到当前目录

# 3. 创建虚拟环境
python3 -m venv .venv
source .venv/bin/activate

# 4. 安装依赖
pip install -r enf_clean/requirements.txt

# 5. 把源数据文件放到 ~/Downloads/

# 6. 运行清洗
python -m enf_clean.clean_three_files

# 7. 查看结果
ls -la ~/PyCharmMiscProject/output/*/
```

---

**最后更新**：2026-11-06
