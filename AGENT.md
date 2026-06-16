# BiliMonitor 项目指南

## 项目概述

Bilibili 视频数据监控 CLI 工具。多视频并发监控（序列化请求避免并发）、SQLite 存储、CSV/JSON 导出、matplotlib/seaborn 可视化、可选守护进程。

## 架构概览

```
┌─────────────┐  typer CLI（11 子命令, 内置 --install-completion）
│   cli.py    │  create / delete / start / stop / update / show
└──────┬──────┘  list / panel / export / viz / daemon status
       │
       ▼
┌──────────────┐  CommandQueue IPC + SIGUSR1
│  scheduler   │◄──── asyncio.Semaphore(1) 序列化所有请求
│  scheduler   │◄──── _check_external_changes() 同步DB变更 / SIGUSR1 立即 reload
└──────┬───────┘
       │
       ├──▶ api/client.py   ──▶ bilibili-api-python (HTTP)
       ├──▶ db/database.py  ──▶ sqlite3 (WAL, 别名查询, 迁移)
       ├──▶ ui/panel.py     ──▶ rich.Live (4Hz auto-refresh, 独立线程)
       ├──▶ export/         ──▶ csv/json
       └──▶ viz/            ──▶ matplotlib + seaborn → output/image/
```

## 目录结构

```
bilibili_record/
├── src/
│   └── bili_monitor/
│       ├── __init__.py
│       ├── __main__.py              # python -m bili_monitor
│       ├── cli.py                   # typer CLI 入口
│       ├── config.py                # 全局配置、常量
│       ├── api/
│       │   ├── __init__.py
│       │   └── client.py            # Bilibili API 封装
│       ├── core/
│       │   ├── __init__.py
│       │   └── scheduler.py         # 异步任务调度器
│       ├── db/
│       │   ├── __init__.py
│       │   ├── database.py          # SQLite 操作
│       │   └── models.py            # 数据模型/常量
│       ├── ui/
│       │   ├── __init__.py
│       │   └── panel.py             # rich 交互面板
│       ├── export/
│       │   ├── __init__.py
│       │   └── exporter.py          # CSV/JSON 导出
│       ├── viz/
│       │   ├── __init__.py
│       │   └── plots.py             # 可视化绘图
│       └── daemon/
│           ├── __init__.py
│           └── daemon.py            # 守护进程管理
├── output/
│   ├── image/                       # 可视化图片输出
│   └── export/                      # 导出文件输出 (可选)
├── pyproject.toml
└── AGENT.md
```

包名 `bili_monitor`，通过 `python -m bili_monitor` 直接运行。

## 数据模型

```sql
-- 视频元信息
videos (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    bvid        TEXT UNIQUE NOT NULL,      -- BV号
    title       TEXT NOT NULL,             -- 视频名称
    uploader    TEXT NOT NULL,             -- UP主
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    active      INTEGER DEFAULT 1          -- 是否活跃监控中
)

-- 时间序列记录
records (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id    INTEGER NOT NULL REFERENCES videos(id),
    timestamp   TIMESTAMP NOT NULL,        -- 记录时间
    views       INTEGER,                   -- 播放量
    likes       INTEGER,                   -- 点赞数
    coins       INTEGER,                   -- 投币数
    favorites   INTEGER,                   -- 收藏数
    danmaku     INTEGER,                   -- 弹幕数
    online      INTEGER,                   -- 同时在线 (可选)
    shares      INTEGER,                   -- 转发数 (可选)
    rank        INTEGER                    -- 全站排名 (可选)
)

CREATE INDEX idx_records_video_time ON records(video_id, timestamp);
```

## 模块职责

### `cli.py` — CLI 入口
- typer command group，包含子命令
- `create <bvid> --name X [--interval N] [--inactive]`：注册新视频
- `delete <bvid/name>`：彻底删除视频及所有记录
- `start [bvid/name] [--all]`：启动 daemon / 激活任务
- `stop [bvid/name] [--all]`：停用任务 / 关 daemon
- `update <bvid/name> [--name X] [--interval N]`：修改别名或间隔
- `show <bvid/name> [--last N]`：查看最近记录
- `list`：列出所有任务
- `panel`：打开交互式 TUI 面板
- `export <bvid/name> [--format csv|json] [--output PATH]`：导出数据
- `viz <bvid/name> [--metrics ...] [--type trend|ratio]`：生成可视化
- `daemon status`：查看守护进程状态

### `api/client.py` — Bilibili API 封装
- 封装 `bilibili-api-python` 调用
- 单例 `asyncio.Semaphore(1)` 保证全局序列化
- 接口：`fetch_video_info(bvid)`、`fetch_online(bvid)`、`fetch_rank()`
- 参数校验：BV号格式验证、URL 自动解析

### `core/scheduler.py` — 核心调度器
- 管理监控任务生命周期（添加/删除/暂停/恢复/更新）
- 主循环：每 ~2s tick，检查各任务到期时间，到期则执行记录
- warp-around Semaphore(1) 保证所有网络请求串行
- 状态管理：记录每个任务的下次执行时间、执行次数、错误计数
- CommandQueue 线程安全命令队列，支持面板/外部 IPC
- `_check_external_changes()` 每 30s 对比 DB 同步 active/interval 变更
- 最大 5 个并发任务
- 信号处理：SIGINT/SIGTERM 优雅退出

### `db/database.py` — 数据库层
- SQLite 连接管理（单例模式，文件锁安全）
- 建表、插入记录、查询（按视频、时间范围）
- 事务处理：批量插入时使用事务
- 提供 `contextmanager` 确保连接正确关闭

### `ui/panel.py` — 交互式 TUI 面板
- 基于 `rich.live.Live` + `rich.table.Table`，主线程渲染
- 调度器运行在独立后台线程（独立事件循环），通过 MonitorState + CommandQueue 通信
- 显示：任务ID、视频名称、活跃状态、上次记录时间、记录数
- 键盘操作：`a` 添加任务、`d <id>` 删除任务、`q` 退出
- 输入时 `live.stop()` 退出 alt screen，输入结束后 `live.start()` 恢复，避免闪动

### `export/exporter.py` — 导出
- CSV 导出：`csv.writer`，按字段顺序输出
- JSON 导出：`json.dump`，按记录列表输出
- 支持按时间范围过滤

### `viz/plots.py` — 可视化
- matplotlib + seaborn，保存到 `output/image/`
- 趋势图：单视频多指标随时间变化（折线图）
- 对比图：多视频同一指标对比（叠加折线）
- 比值图：指标间比值变化（如点赞/播放比）
- 函数签名统一的绘图接口，便于扩展

### `daemon/daemon.py` — 守护进程
- Linux：`os.fork()` + PID 文件，基本 daemon 模式
- Windows：`pythonw.exe` + 隐藏窗口
- 检测：CLI 启动时检查 PID 文件 + `os.kill(pid, 0)` 探活
- 使用 `loguru` 输出日志到文件

### `config.py` — 配置
- 默认值：间隔 300s，数据目录 `./data/`，图片目录 `./output/image/`
- 环境变量覆盖 `BILI_DATA_DIR`、`BILI_OUTPUT_DIR`
- 常量：最小间隔 30s，最大间隔 3600s，最大任务数 5

## 运行模式

```
# 交互式 CLI
$ python -m bili_monitor panel           # 打开 TUI 面板

# 命令行直接操作
$ python -m bili_monitor start BV1xx     # 添加任务立刻返回（后台运行）
$ python -m bili_monitor stop BV1xx      # 停止任务
$ python -m bili_monitor list            # 列出任务

# 导出与可视化
$ python -m bili_monitor export BV1xx --format csv
$ python -m bili_monitor viz BV1xx --metrics views,likes --type trend

# 守护进程模式
$ python -m bili_monitor daemon start
$ python -m bili_monitor daemon status
```

## 编码规范

- **类型注解**：所有函数必须标注类型
- **异步优先**：网络 I/O 用 async/await，DB 用 sync（stdlib sqlite3，足够快）
- **异常处理**：网络错误/API变更/BVID不存在等，用自定义异常层次
- **日志**：使用 `loguru`（已安装），按模块名区分 logger
- **模块单职责**：每个文件不超过 300 行，超限时拆分
- **无魔法数字**：常量在 `config.py` 或文件顶部定义
- **import 顺序**：stdlib → third-party → 本地模块

## 关键设计决策

| 决策 | 选择 | 理由 |
|------|------|------|
| API 层 | bilibili-api-python | 已有、成熟、无需自己维护 API 变更 |
| 请求序列化 | asyncio.Semaphore(1) | 轻量、精确控制、不依赖外部调度器 |
| 调度器 | 自研 async tick loop | 轻量、完全控制、无外部依赖 |
| DB 层 | stdlib sqlite3 | 零依赖、足够快（sync 非瓶颈） |
| TUI | rich.live | 已有、渲染美观、交互友好 |
| CLI 框架 | typer | 已有、类型安全、自动 --help |
| 进程 | `python -m bili_monitor` | 用户要求，src layout 原生支持 |
| 登录 | 不支持 | 用户确认，公开 API 即可 |
| 可视化 | 保存文件到 output/image/ | 用户确认，不弹窗 |
| 守护进程 | fork + PID file（首选 Linux） | 简单可靠 |

## 开发流程

1. 确定模块接口签名（根据本设计文档）
2. 实现核心数据层 `db/` + `api/`
3. 实现 `core/scheduler.py`
4. 实现 `cli.py` 串联核心流程
5. 实现 `ui/panel.py` 交互面板
6. 实现 `export/` + `viz/`
7. 实现 `daemon/`
8. 集成测试

## 已确认设计决策（2025-06-16）

| 问题 | 决策 |
|------|------|
| TUI 交互 | 方案A：独占终端，rich.live + 键盘操作（类htop），`panel` 子命令进入 |
| 数据库路径 | 默认 `./bili_monitor.db`，环境变量 `$BILI_DATA_DIR` 优先 |
| 数据保留 | 手动提醒：记录数超180天或 DB >30MB 时提示清理；`config` 中可配自动清理策略 |
| API 并发控制 | `api/client.py` 统一单例 wrapper，所有 bilibili-api-python 调用经其调度，全局 Semaphore(1) 保证串行 |
| 可选功能 | 必要时可砍掉：在线人数/转发/排名/弹幕 以及 daemon 模式 |
