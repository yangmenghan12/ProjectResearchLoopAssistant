# Project Research Loop Assistant

Project Research Loop Assistant / 工程调研 Loop 助手，是一个用于理解陌生工程和新代码库的 Loop Engineering 示例项目。系统围绕一个可验证目标持续采集资料、分析项目、记录证据、运行验证器并生成 Project Research Package / 项目调研包。

项目使用：

- FastAPI 提供 REST / SSE API。
- LangGraph `StateGraph` 编排可恢复 Agent Loop。
- MySQL 保存项目、输入源、运行、证据、报告、LLM 调用、运行事件和 graph checkpoint。
- 后台 worker 执行长任务，HTTP 请求只负责排队。
- Next.js 提供中文优先的工程调研工作台。
- OpenAI-compatible LLM provider 接入 DeepSeek、Qwen/DashScope 或自部署 vLLM。

## 能力边界

本项目只做只读工程调研，不修改被分析代码，不执行被分析项目脚本，不创建 commit、issue 或 PR。LLM 只作为增强能力，最终产物必须经过结构化解析、证据引用校验、确定性 verifier 和人工评审。

Project Research Package 包含：

```text
Executive Brief
Research Profile
Source Bundle Summary
Engineering Onboarding Guide
Source Inventory
Codebase Map
Architecture Map
Runbook
Findings
Risk Register
Open Questions
Reading Path
Evidence Index
Verifier Result
Run Timeline
LLM Call Summary
Report Snapshot
```

## 本地依赖

建议环境：

- uv 0.11+
- Python 3.11+
- Node.js 20+
- MySQL 8+

安装 uv：

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
uv --version
```

## 配置环境变量

在仓库根目录复制配置文件：

```powershell
Copy-Item .env.example .env
```

按本机情况修改 `.env`：

```env
DATABASE_URL=mysql+pymysql://root:<local-password>@localhost:3306/loop_engineering
WORKSPACE_ROOT=D:/loop-workspace/projects
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000

LLM_PROVIDER=deepseek
LLM_API_KEY=<your-api-key>
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-chat
LLM_API_KEY_REQUIRED=true
LLM_TIMEOUT_SECONDS=30
```

Provider 示例：

```env
# Qwen / DashScope OpenAI-compatible mode
LLM_PROVIDER=qwen
LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_MODEL=qwen-plus
LLM_API_KEY_REQUIRED=true

# Self-hosted vLLM OpenAI-compatible server
LLM_PROVIDER=vllm
LLM_BASE_URL=http://127.0.0.1:8001/v1
LLM_MODEL=<served-model-name>
LLM_API_KEY_REQUIRED=false
```

`.env` 不要提交到 Git。数据库和 LLM 密钥只放本机。

## 创建数据库

```powershell
mysql -uroot -p -e "CREATE DATABASE IF NOT EXISTS loop_engineering CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
```

## 启动后端 API

```powershell
Set-Location services/api
uv python install 3.11
uv sync --extra dev
uv run alembic upgrade head
uv run python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

后端依赖由 `services/api/pyproject.toml` 声明，由 `services/api/uv.lock` 锁定。`uv sync --extra dev` 会创建并同步本地 `.venv`，不需要手动激活虚拟环境。

健康检查：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```

## 启动后台 Worker

另开一个 PowerShell 终端：

```powershell
Set-Location services/api
$env:PYTHONPATH='.'
uv run python -m app.workers.worker_service --queue default --poll-interval 2
```

worker 会轮询 queued run job，使用 lease/heartbeat 防止重复领取，并把运行过程写入 durable run events。

## 启动前端

另开一个 PowerShell 终端，在仓库根目录执行：

```powershell
npm --prefix apps/web install
npm --prefix apps/web run dev -- --hostname 127.0.0.1 --port 3000
```

浏览器打开：

```text
http://127.0.0.1:3000/projects
```

## 推荐体验路径

1. 打开 `/projects/new` 创建一个调研项目。
2. 选择调研档位，例如“快速接手”“架构理解”“教学拆解”。
3. 在项目资料页添加本地目录、公开 GitHub URL、网页或 Markdown/PDF 资料。
4. 进入运行页启动调研 Loop，观察后台任务、运行事件和质量评分。
5. 进入报告页生成 Project Research Package。
6. 审阅报告，通过、驳回或要求补充。
7. 在 `/evaluations` 运行调研档位 benchmark，确认核心能力没有退化。

## 常用命令

后端测试：

```powershell
Set-Location services/api
uv run --extra dev python -m pytest tests -q
```

数据库迁移状态：

```powershell
Set-Location services/api
uv run python -m alembic -c alembic.ini current
```

前端构建：

```powershell
npm --prefix apps/web run build
```

运行评估：

```powershell
Invoke-RestMethod -Method POST http://127.0.0.1:8000/evaluations/run
```

## 主要页面

```text
/projects
/projects/new
/projects/:id
/projects/:id/sources
/projects/:id/inventory
/projects/:id/run
/projects/:id/findings
/projects/:id/codebase
/projects/:id/risks
/projects/:id/report
/settings/llm
/evaluations
```
