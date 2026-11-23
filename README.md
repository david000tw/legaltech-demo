# legaltech-app

台灣 AI 訴訟輔助系統（計畫 1：核心流程）。設計見 [docs/spec.md](docs/spec.md)，實作計畫見 [docs/plan1.md](docs/plan1.md)。

> 開發期只用合成資料，不得放入任何真實案件。AI 透過開發者本機的 Claude Code（訂閱登入），只在本機、只對內部測試事務所開放。

## 需求

- Docker Desktop、Python 3.12 + uv、Node 22 + pnpm
- 本機已登入的 Claude Code CLI（`claude`）

## 啟動（PowerShell）

```powershell
$env:PYTHONUTF8=1            # 本機 Python 預設 cp950，一律改用 UTF-8
docker compose up -d --wait db   # 資料庫只綁 127.0.0.1:55432

cd backend
uv sync
uv run alembic upgrade head
uv run python -m app.serve   # 唯一啟動方式，只監聽 127.0.0.1:8000

cd ../frontend
pnpm install
pnpm dev                     # 只監聽 127.0.0.1
```

## 測試

```powershell
$env:PYTHONUTF8=1
cd backend; uv run pytest -q              # 不呼叫真的 AI
uv run pytest -m real_ai tests/e2e -v     # 呼叫本機 Claude Code，會用訂閱額度
cd ../frontend; pnpm vitest run
```

## 探測本機 Claude Code

```powershell
cd backend; python -X utf8 scripts/probe_claude.py
```
