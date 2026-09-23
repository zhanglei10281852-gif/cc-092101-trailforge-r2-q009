# TrailForge

TrailForge 是一个面向山野训练、徒步路线和探险活动的离线优先后端平台。它把用户运动档案、训练计划、当地路线数据、活动报名、装备库存、安全签到和统计审计放在同一个 SQLite 数据库中，适合作为后续功能迭代和缺陷修复的长期基础项目。

系统不接入地图、天气、短信或救援服务。经纬度和路线保存在本地；天气数据是用户明确录入的离线快照，不代表实时天气；紧急事件功能只做记录和风险提示，不声称能够发起真实救援。

## 运行要求

- Python 3.11 或更新的兼容版本
- SQLite 3（由 Python 标准库提供）
- Linux、macOS、Windows 或普通 Docker 环境

项目没有 MySQL、PostgreSQL、MongoDB、Redis、消息队列等外部运行依赖。

## 安装

在仓库根目录执行：

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

Windows PowerShell 的激活命令是：

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

配置全部使用 `TRAILFORGE_` 前缀环境变量。可以复制 `.env.example` 为 `.env`，本地 `.env` 已被 Git 忽略。默认数据库是 `./data/trailforge.db`。

## 初始化数据库

```bash
python -m trailforge.cli init-db
python -m trailforge.cli migration-status
python -m trailforge.cli check-db
```

初始化命令可重复执行；已应用的迁移不会重复写入。SQLite 连接会启用外键约束、WAL、busy timeout 和写入重试。所有业务时间在数据库中保存为 UTC ISO-8601 文本，API 接受和返回带时区的时间。

使用临时位置时可以覆盖数据库 URL：

```bash
TRAILFORGE_DATABASE_URL=sqlite:///./data/demo.db python -m trailforge.cli init-db
```

## 启动服务

```bash
uvicorn trailforge.main:app --host 0.0.0.0 --port 8000
```

健康检查和交互文档：

- `http://127.0.0.1:8000/health`
- `http://127.0.0.1:8000/docs`
- `http://127.0.0.1:8000/openapi.json`

Docker 启动：

```bash
docker build -t trailforge .
docker run --rm -p 8000:8000 -v "$(pwd)/data:/app/data" trailforge
```

## 测试与检查

```bash
pytest
ruff check .
python -m compileall -q trailforge tests
python tools/count_production_lines.py
```

测试使用独立的临时 SQLite 文件，不读写默认开发数据库。覆盖正常流程、非法参数、外键与唯一约束、事务回滚、幂等、活动冲突与容量、装备库存、签到超时、重启恢复、并发写入、统计和 API 集成。

## API 示例

下面的命令假定服务运行在 `127.0.0.1:8000`。

创建用户：

```bash
curl -sS -X POST http://127.0.0.1:8000/api/v1/users \
  -H 'Content-Type: application/json' \
  -d '{"email":"lin@example.com","display_name":"Lin","timezone":"Asia/Shanghai"}'
```

建立运动档案（假定用户 ID 为 1）：

```bash
curl -sS -X PUT 'http://127.0.0.1:8000/api/v1/users/1/sport-profile?actor_id=1' \
  -H 'Content-Type: application/json' \
  -d '{"height_cm":172,"weight_kg":65,"fitness_level":"intermediate","outdoor_experience":"周末徒步","weekly_training_minutes":240}'
```

创建本地路线：

```bash
curl -sS -X POST 'http://127.0.0.1:8000/api/v1/routes?actor_id=1' \
  -H 'Content-Type: application/json' \
  -d '{"name":"青峰环线","region":"测试山区","distance_km":12.5,"elevation_gain_m":680,"elevation_loss_m":680,"min_altitude_m":300,"max_altitude_m":980,"estimated_duration_minutes":300,"difficulty":"moderate","is_loop":true,"is_published":true,"segments":[{"sequence":1,"name":"主线","distance_km":12.5,"elevation_gain_m":680,"estimated_duration_minutes":300,"difficulty":"moderate","start_latitude":30.1,"start_longitude":120.1,"end_latitude":30.1,"end_longitude":120.1}],"points":[],"risk_tag_ids":[]}'
```

列表接口都支持 `page`、`page_size`、`sort` 和 `direction`；各资源只接受文档中列出的排序字段，未知字段会返回明确的 422 业务错误。创建报名、打卡、紧急事件和库存变更时，正文包含 `idempotency_key`。同一作用域下用相同键和相同请求会返回原资源，用相同键发送不同请求会返回 409。

## 目录

```text
trailforge/
  api/             FastAPI 路由和依赖
  database/        SQLite 连接、事务、迁移和 UTC 类型
  domain/          枚举与状态转换规则
  models/          SQLAlchemy 2.x 数据模型和数据库约束
  repositories/    查询、分页、筛选和持久化读取
  schemas/         Pydantic 请求、响应和组合校验
  services/        业务事务、状态机、幂等、审计和统计
  cli.py           初始化、状态、完整性检查和安全重建命令
  main.py          应用工厂和统一错误响应
tests/             单元、事务、并发和 API 集成测试
tools/             本地验证工具
```

## SQLite 配置

常用环境变量：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `TRAILFORGE_DATABASE_URL` | `sqlite:///./data/trailforge.db` | 只接受 SQLite URL |
| `TRAILFORGE_SQLITE_TIMEOUT_SECONDS` | `30` | SQLite busy timeout |
| `TRAILFORGE_SQLITE_BUSY_RETRIES` | `5` | 可重试写操作次数 |
| `TRAILFORGE_SQLITE_BUSY_BACKOFF_SECONDS` | `0.05` | 指数退避基数 |
| `TRAILFORGE_API_TITLE` | `TrailForge API` | OpenAPI 标题 |

数据库旁可能暂时出现 `-wal` 和 `-shm` 文件，这是 WAL 模式的正常行为。`data/`、SQLite 文件、缓存、覆盖率输出和 `.env` 都在 `.gitignore` 中。

## 清理并重建开发数据库

下面的命令只允许删除 `.db`、`.sqlite` 或 `.sqlite3` 后缀的已配置数据库，并且要求显式确认：

```bash
python -m trailforge.cli reset-db --confirm
python -m trailforge.cli check-db
```

如果 `TRAILFORGE_DATABASE_URL` 指向自定义文件，重建的也是该文件。执行前应确认环境变量没有指向需要保留的数据；该操作不可恢复。

## 事务、并发与审计

每个 HTTP 请求使用独立 SQLAlchemy Session，成功时统一提交，异常时统一回滚。外键约束在每条 SQLite 连接上开启；文件数据库使用 WAL 和 busy timeout。可重试的后台写操作可使用 `Database.run_write`，它只对 SQLite busy/locked 错误做有界指数退避，不会吞掉业务冲突。

训练计划、训练记录、活动、报名、装备借还、风险和签到等关键变更都会写结构化审计日志。日志包含操作者、UTC 时间、对象、动作、前后状态和必要上下文；审计工具会过滤密码、令牌、密钥等敏感字段。
