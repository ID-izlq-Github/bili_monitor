<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10%2B-3776AB?style=flat&logo=python&logoColor=white">
  <img src="https://img.shields.io/badge/Bilibili-API-00A1D6?style=flat&logo=bilibili&logoColor=white">
  <img src="https://img.shields.io/badge/SQLite-003B57?style=flat&logo=sqlite&logoColor=white">
  <img src="https://img.shields.io/badge/license-MIT-green">
</p>

<h1 align="center">📺 BiliMonitor</h1>

<p align="center">
  <b>Bilibili 视频数据监控 CLI 工具</b><br>
  定时采集视频数据 · SQLite 存储 · 终端面板管理 · 数据导出 · 可视化
</p>

---

## 📋 功能

| 功能 | 说明 |
|------|------|
| **视频监控** | 给定 BV 号或视频 URL，定时记录播放量、点赞、投币、收藏、弹幕、在线人数等 |
| **别名系统** | 每个视频绑定唯一别名，后续全部用别名操作，告别 BV 号 |
| **灵活间隔** | 30s ~ 1h 可配置，默认 5min |
| **多任务并发** | 最多 5 个任务，所有网络请求串行（绝不并发） |
| **终端面板** | Rich 交互面板，实时查看任务状态，支持添加/删除 |
| **SQLite 存储** | 零配置，自动建表，WAL 模式 |
| **数据导出** | CSV / JSON 一键导出 |
| **可视化** | matplotlib + seaborn 趋势图/比值图，自动保存 |
| **守护进程** | Linux 后台运行，PID 文件管理 + SIGUSR1 实时通知 |
| **自动停启** | `create` 自动激活并启动 daemon；停用最后任务自动关 daemon |
| **自动提醒** | 数据超 180 天或 DB 超 30MB 时提示清理 |

---

## 🚀 快速开始

### 安装

```bash
# 1. 克隆
git clone https://github.com/your/bili-monitor.git
cd bili-monitor

# 2. 推荐：创建虚拟环境
conda create -n bili_data python=3.13
conda activate bili_data

# 3. 安装依赖
pip install -e .
```

### 使用

```bash
# 查看帮助
python -m bili_monitor --help

# 注册新视频
python -m bili_monitor create BV1GJ411x7h7 --name rick --interval 300

# 已注册的视频直接激活
python -m bili_monitor start rick

# 查看记录
python -m bili_monitor show rick --last 5

# 修改别名或间隔
python -m bili_monitor update rick --name rickroll --interval 600

# 列出所有任务
python -m bili_monitor list

# 停用指定任务（无活跃任务时自动停守护进程）
python -m bili_monitor stop rickroll

# 停用所有任务 + 关守护进程
python -m bili_monitor stop --all

# 启动守护进程（加载所有活跃任务）
python -m bili_monitor start

# 打开交互式面板
python -m bili_monitor panel

# 导出数据
python -m bili_monitor export rick --format csv

# 生成可视化
python -m bili_monitor viz rick --metrics views,likes,coins --type trend

# 查看守护进程状态
python -m bili_monitor daemon status
```

---

## 🧩 子命令详情

### `create` — 注册新视频

```
python -m bili_monitor create <BV号/URL> --name <别名> [选项]
```

参数：
| 参数 | 说明 | 默认 |
|------|------|------|
| `BV号或URL` | 支持 `BV1xx` 或完整视频链接 | **必填** |
| `-n, --name` | 别名（后续用别名操作，必须且唯一） | **必填** |
| `-i, --interval` | 记录间隔（秒） | 300 |
| `--inactive` | 创建后不自动激活 | 不设 |

支持 URL 格式：
```
https://www.bilibili.com/video/BV1GJ411x7h7
www.bilibili.com/video/BV1GJ411x7h7
BV1GJ411x7h7
```

### `start` — 激活任务 / 启动守护进程

```
python -m bili_monitor start [别名|BV号] [--all]
```

| 用法 | 行为 |
|------|------|
| `start` | 启动守护进程，加载所有活跃任务 |
| `start rick` | 激活该任务，守护进程自动启动 |
| `start --all` | 激活 DB 中所有任务 |

> `start` 无参仅启引擎不改变任务 active 状态。

### `stop` — 停用任务 / 关闭守护进程

```
python -m bili_monitor stop [别名|BV号] [--all]
```

| 用法 | 行为 |
|------|------|
| `stop rick` | 停用该任务；活跃数归零时自动停守护进程 |
| `stop --all` | 停用所有任务 + 关闭守护进程 |
| `stop`（无参） | 报错提示（防误触） |

### `update` — 修改别名或间隔

```
python -m bili_monitor update <别名|BV号> [选项]
```

| 参数 | 说明 |
|------|------|
| `-n, --name` | 新别名 |
| `-i, --interval` | 新记录间隔（秒） |

> 仅修改参数，不影响任务的 active 状态。修改后通过 SIGUSR1 立即通知守护进程。

### `show` — 查看记录

```
python -m bili_monitor show <别名|BV号> [选项]
```

| 参数 | 说明 | 默认 |
|------|------|------|
| `-l, --last` | 显示最近 N 条 | 10 |

### `panel` — 交互面板

在面板中：
| 快捷键 | 功能 |
|--------|------|
| `a` | 添加任务（输入 BV 和间隔） |
| `d` | 删除任务（输入任务 ID） |
| `q` | 退出面板 |
| `r` | 手动刷新 |

### `export` — 数据导出

```
python -m bili_monitor export <BV号> [选项]
```

| 参数 | 说明 | 默认 |
|------|------|------|
| `-f, --format` | `csv` 或 `json` | csv |
| `-o, --output` | 输出路径 | 自动生成 |

### `viz` — 可视化

```
python -m bili_monitor viz <BV号> [选项]
```

| 参数 | 说明 | 默认 |
|------|------|------|
| `-m, --metrics` | 指标列表（逗号分隔） | views,likes,coins |
| `-t, --type` | 图表类型：`trend` / `ratio` | trend |

图片自动保存至 `output/image/` 目录。

---

## ⚙️ 配置

通过环境变量 `BILI_DATA_DIR` 自定义数据目录：

```bash
export BILI_DATA_DIR=/path/to/data
# 之后运行的所有命令读写该目录下的 bili_monitor.db
```

默认数据目录为命令运行时的当前目录。

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| 最小间隔 | 30s | — |
| 最大间隔 | 3600s | — |
| 默认间隔 | 300s | — |
| 最大任务数 | 5 | — |
| DB 提醒阈值 | 30MB | 超出提示清理 |
| 数据保留天数 | 180 天 | 超出提示清理 |

---

## 🏗️ 技术栈

| 层级 | 技术 | 用途 |
|------|------|------|
| CLI 框架 | [typer](https://github.com/fastapi/typer) | 命令解析，类型安全 |
| 终端 UI | [rich](https://github.com/Textualize/rich) | 交互面板，表格渲染 |
| API 封装 | [bilibili-api-python](https://github.com/Passkou/bilibili-api-python) | Bilibili 数据接口 |
| 异步 HTTP | [aiohttp](https://github.com/aio-libs/aiohttp) | (库内部) TCP 连接池 |
| 数据库 | SQLite3 (stdlib) | 零依赖嵌入式存储 |
| 可视化 | [matplotlib](https://github.com/matplotlib/matplotlib) + [seaborn](https://github.com/mwaskom/seaborn) | 趋势图 / 比值图 |
| 数据处理 | [pandas](https://github.com/pandas-dev/pandas) | (预留) 数据聚合 |
| 日志 | [loguru](https://github.com/Delgan/loguru) | 结构化日志 |
| 任务调度 | 自研 async tick loop | 轻量可控，无外部依赖 |

---

## 📁 项目结构

```
bili_monitor/
├── pyproject.toml              # 项目元数据 & 依赖
├── src/bili_monitor/
│   ├── __main__.py             # python -m 入口
│   ├── cli.py                  # typer 命令定义
│   ├── config.py               # 配置 & 环境变量
│   ├── api/client.py           # Bilibili API 封装 (Semaphore 串行)
│   ├── core/scheduler.py       # 异步调度器 & 状态管理
│   ├── db/
│   │   ├── models.py           # 数据模型 & SQL
│   │   └── database.py         # SQLite (WAL, async-safe)
│   ├── ui/panel.py             # Rich TUI 交互面板
│   ├── export/exporter.py      # CSV/JSON 导出
│   ├── viz/plots.py            # matplotlib 可视化
│   └── daemon/daemon.py        # Linux 守护进程
├── output/
│   ├── image/                  # 可视化输出
│   └── export/                 # 导出文件
└── AGENT.md                    # 项目架构指南
```

---

## 🔒 数据说明

- **无需登录**：所有数据通过 Bilibili 公开 API 获取
- **串行请求**：`asyncio.Semaphore(1)` 保证全局任意时刻只有 1 个 HTTP 请求
- **请求间隔**：最低 30s，配合串行策略，不会触发频率限制
- **本地存储**：所有数据保存在本地 SQLite，不经过任何第三方
- **WAL 模式**：写操作不阻塞读，监控与导出/可视化可同时进行

---

## 🙏 致谢

感谢以下开源项目的出色工作：

- [typer](https://github.com/fastapi/typer) — 优雅的 CLI 框架
- [rich](https://github.com/Textualize/rich) — 强大的终端渲染库
- [bilibili-api-python](https://github.com/Passkou/bilibili-api-python) — Bilibili API Python 封装
- [aiohttp](https://github.com/aio-libs/aiohttp) — 高性能异步 HTTP
- [matplotlib](https://github.com/matplotlib/matplotlib) — 经典可视化库
- [seaborn](https://github.com/mwaskom/seaborn) — 统计数据可视化
- [pandas](https://github.com/pandas-dev/pandas) — 数据处理基础
- [loguru](https://github.com/Delgan/loguru) — Python 日志最佳实践
- [APScheduler](https://github.com/agronholm/apscheduler) — (库依赖) 任务调度

---

<p align="center">
  <sub>Built with ❤️ for Bilibili data enthusiasts</sub>
</p>
