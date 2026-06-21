# BiliMonitor 项目指南

## 项目概述

Bilibili 视频数据监控 CLI 工具。多视频并发监控（序列化请求避免并发）、SQLite 存储、CSV/JSON 导入导出、matplotlib 可视化（可选依赖）、守护进程。

## 架构概览

```
┌─────────────┐  typer CLI（12 子命令, 内置 --install-completion）
│   cli.py    │  create / delete / start / stop / update / show
└──────┬──────┘  export / import / viz / status
       │
       ▼
┌──────────────┐  SIGUSR1 实时 reload
│  scheduler   │◄──── asyncio.Semaphore(1) 序列化所有请求
│  scheduler   │◄──── _check_external_changes() 同步DB变更
└──────┬───────┘
       │
       ├──▶ api/client.py   ──▶ bilibili-api-python (HTTP)
       ├──▶ db/database.py  ──▶ sqlite3 (WAL, 别名查询, 迁移)
       ├──▶ data_import/    ──▶ csv/json 导入
       ├──▶ export/         ──▶ csv/json 导出
        └──▶ viz/            ──▶ matplotlib → output/image/  (可选依赖)
```

## 目录结构

```
bilibili_record/
├── src/
│   └── bili_monitor/
│       ├── __init__.py
│       ├── __main__.py              # python -m bili_monitor
│   ├── cli.py                   # typer CLI 入口（11 子命令）
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
│       ├── data_import/
│       │   ├── __init__.py
│       │   └── importer.py          # CSV/JSON 导入逻辑
│       ├── export/
│       │   ├── __init__.py
│       │   └── exporter.py          # CSV/JSON 导出
│       ├── viz/
│       │   ├── __init__.py
│       │   └── plots.py             # 可视化绘图
│       └── daemon/
│           ├── __init__.py
│           └── daemon.py            # 守护进程管理
├── test/
│   ├── __init__.py
│   ├── test_parse_count.py
│   └── test_import_export.py
├── output/
│   ├── image/                       # 可视化图片输出
│   └── export/                      # 导出文件输出
├── pyproject.toml
├── AGENT.md
└── README.md
```

包名 `bili_monitor`，通过 `python -m bili_monitor` 直接运行。

## 数据模型

```sql
-- 视频元信息
videos (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    bvid        TEXT UNIQUE NOT NULL,          -- BV号
    name        TEXT UNIQUE NOT NULL,          -- 别名 (用于 CLI 操作)
    title       TEXT NOT NULL,                 -- 视频标题
    uploader    TEXT NOT NULL,                 -- UP主
    created_at  TEXT DEFAULT (datetime('now','localtime')),
    active      INTEGER DEFAULT 0,             -- 是否活跃监控中
    pubdate     TEXT,                          -- 发布时间 (ISO, nullable)
    duration    INTEGER,                       -- 视频长度 (秒)
    tname       TEXT,                           -- 分区 (如"知识"/"游戏"/"音乐")
    videos      INTEGER DEFAULT 1               -- 分P数量
)

-- 时间序列记录
records (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id    INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    timestamp   TEXT    NOT NULL,               -- 记录时间 (ISO)
    views       INTEGER,                        -- 播放量
    likes       INTEGER,                        -- 点赞数
    coins       INTEGER,                        -- 投币数
    favorites   INTEGER,                        -- 收藏数
    danmaku     INTEGER,                        -- 弹幕数
    online      INTEGER,                        -- 同时在线 (可选)
    shares      INTEGER,                        -- 转发数 (可选)
    rank        INTEGER,                        -- 当前全站排名 (可选)
    reply       INTEGER,                        -- 评论数
    his_rank    INTEGER                         -- 历史最高排名 (可选)
)

-- 任务自定义间隔
task_intervals (
    video_id  INTEGER PRIMARY KEY,
    interval  INTEGER NOT NULL                  -- 秒
)

CREATE INDEX idx_records_video_time ON records(video_id, timestamp);
```

## 模块职责

### `cli.py` — CLI 入口
- typer command group，11 个子命令
- `create <bvid> [--name X] [--interval N] [--inactive]`：注册新视频，自动获取发布时数据
  - 间隔 >3600s 时二次确认
- `snap [bvid/name] [--all]`：立即记录一次数据（不经过调度器）
- `delete <bvid/name>`：彻底删除视频及所有记录
- `start [bvid/name] [--all]`：激活任务 / 启动 daemon
- `stop [bvid/name] [--all]`：停用任务 / 关 daemon
- `update <bvid/name> [--name X] [--interval N] [--refresh-meta]`：修改别名/间隔，或刷新标题/UP主/时长/分区等元数据
- `show <bvid/name> [--last N]`：查看最近记录（含评论、历史最高排名列）
- `export <bvid/name> [--format csv|json] [--output PATH] [-a/-A]`：导出数据（含视频元数据）；`-a` 所有活跃任务，`-A` 所有任务
- `import <file> --bvid <BV> [--format] [--dry-run] [--overwrite]`：导入 CSV/JSON 数据
- `viz <bvid/name> [--weights FILE] [--output DIR] [-a/-A] [--raw] [--parallel/--no-parallel]`：生成可视化报告；`-a` 所有活跃，`-A` 所有，`--raw` 不过滤异常，`--parallel` 并行（≥3 个自动启用）
- `status`：查看守护进程状态和监控任务列表

### `api/client.py` — Bilibili API 封装
- 封装 `bilibili-api-python` 调用
- 单例 `asyncio.Semaphore(1)` 保证全局序列化
- 接口：`fetch_video_meta(bvid)`（标题/UP主/发布时间/时长/分区）、`fetch_record_data(bvid)`（统计：播放/点赞/投币/收藏/弹幕/评论/转发/排名）
- `_parse_count()` 处理 `'4000+'`、`'1.2w'`、`'3.5K'` 等格式 → 整数
- 参数校验：BV 号格式验证、URL 自动解析

### `core/scheduler.py` — 核心调度器
- 管理监控任务生命周期（添加/删除/暂停/恢复/更新）
- 主循环：每 ~2s tick，检查各任务到期时间，到期则执行记录
- `asyncio.Semaphore(1)` 保证所有网络请求串行
- 状态管理：记录每个任务的下次执行时间、执行次数、错误计数
- `_check_external_changes()` 每 15 ticks 或收到 SIGUSR1 时对比 DB 同步 active/interval 变更
- 信号处理：SIGINT/SIGTERM 优雅退出

### `db/database.py` — 数据库层
- SQLite 连接管理（async-safe 文件锁）
- WAL 模式，写不阻塞读
- schema-as-code 迁移（`ALTER TABLE ADD COLUMN` try/except）
- 支持别名/BV 双向查找、记录去重、upsert

### `data_import/importer.py` — 数据导入
- CSV/JSON 解析，自动从扩展名推断格式
- 文件内 bvid 与命令行 `--bvid` 一致性校验
- 按 `(video_id, timestamp)` 去重，`--overwrite` 覆盖已有记录
- `--dry-run` 预览模式

### `export/exporter.py` — 数据导出
- CSV 导出：bvid + 所有记录字段 + 视频元数据列（title/uploader/name/pubdate/duration/tname）
- JSON 导出：`{"meta": {title, uploader, name, pubdate, duration, tname}, "records": [...]}`
- 文件名自动生成 `{bvid}-{name}/{timestamp}.csv/json`

### `viz/plots.py` — 可视化（可选依赖）
- 依赖 `matplotlib` + `numpy`，通过 `pip install bili-monitor[viz]` 安装
- matplotlib (Agg backend)，保存到 `output/image/`
- 单命令 `bili-monitor viz <name>` 一次性生成 7 张分析图表
- 输出路径：`{image_dir}/{bvid}-{name}/{last_ts}/`
- 7 张图表：
  1. **播放与互动** — 播放量(左) + 点赞·投币(右) 双轴趋势
  2. **互动增量** — 30min分桶 + SMA平滑，各互动指标增量变化
  3. **互动转化效率** — 加权互动深度 (HDS) + 移动平均 + 异常检测
  4. **三连率** — 10min增量比值实线 + 累计比值虚线（按记录点逐点绘制），双Y轴四条线
  5. **观看留存率** — 累积窗口 VDR，∑Δt≥视频时长时输出一个点
  6. **平均观看时长** — 平均观看秒数 + 视频全长红线
  7. **累计绝对值趋势** — 点赞/投币(左) 收藏(右) 总量增长曲线
- 内置热度权重 + 可外部 JSON 覆盖

### `daemon/daemon.py` — 守护进程
- Linux：`os.fork()` + PID 文件
- Windows：`pythonw.exe` + 隐藏窗口
- 探活：PID 文件 + `os.kill(pid, 0)`
- SIGUSR1：通知调度器立即 reload DB 配置

### `config.py` — 配置
- 默认值：间隔 900s，数据目录 `./bili_monitor.db`，图片目录 `./output/image/`
- 环境变量 `BILI_DATA_DIR` 覆盖数据目录

## 运行模式

```
# 创建（默认激活、启动 daemon）
$ python -m bili_monitor create BV1xx --name myvideo

# 查看记录
$ python -m bili_monitor show myvideo --last 5

# 导入/导出
$ python -m bili_monitor export myvideo --format csv
$ python -m bili_monitor export -a
$ python -m bili_monitor import data.csv --bvid BV1xx

# 可视化
$ python -m bili_monitor viz myvideo
$ python -m bili_monitor viz myvideo --weights my_weights.json
$ python -m bili_monitor viz -a --raw
$ python -m bili_monitor viz -a --parallel

# 立即记录
$ python -m bili_monitor snap myvideo
$ python -m bili_monitor snap --all

# 启用/停用
$ python -m bili_monitor start myvideo
$ python -m bili_monitor stop myvideo

# 状态
$ python -m bili_monitor status
```

## 编码规范

- **类型注解**：所有函数必须标注类型
- **异步优先**：网络 I/O 用 async/await，DB 用 sync（stdlib sqlite3，足够快）
- **异常处理**：网络错误/API 变更/BVID 不存在等，具体异常具体处理
- **日志**：stdlib `logging`，按模块名区分 logger
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
| CLI 框架 | typer | 已有、类型安全、自动 --help |
| 进程 | `python -m bili_monitor` | 用户要求，src layout 原生支持 |
| 登录 | 不支持 | 用户确认，公开 API 即可 |
| 可视化 | 保存文件到 output/image/ | 可选依赖 (matplotlib)，7 张图表 (report 模式)，Agg 后端 |
| 守护进程 | fork + PID file（首选 Linux） | 简单可靠 |
| 配置变更通知 | SIGUSR1 | CLI 直接 kill 发信号，零 IPC 依赖 |

## 开发流程

1. 确定模块接口签名
2. 实现核心数据层 `db/` + `api/`
3. 实现 `core/scheduler.py`
4. 实现 `cli.py` 串联核心流程
5. 实现 `export/` + `data_import/` + `viz/`
6. 实现 `daemon/`
## 依赖管理

| 依赖 | 类型 | 安装方式 |
|------|------|----------|
| typer, rich, bilibili-api-python | 核心 | `pip install bili-monitor` |
| matplotlib, numpy | 可视化（可选） | `pip install bili-monitor[viz]` |

通过 `pyproject.toml` 的 `[project.optional-dependencies]` 管理可选依赖。`viz/plots.py` 使用 try/except 兜底缺失的导入，运行时抛出清晰提示。
