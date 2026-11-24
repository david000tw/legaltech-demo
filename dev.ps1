# 一鍵啟動開發環境（PowerShell）：資料庫 → 更新資料表 → 後端、前端各開一個視窗 → 開瀏覽器。
# 用法：在 legaltech-app 資料夾執行  .\dev.ps1
# 已經在跑的伺服器會略過，不會重複啟動。關閉：直接關掉後端、前端那兩個視窗；資料庫用 docker compose stop db。
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$env:PYTHONUTF8 = '1'  # 本機 Python 預設 cp950，一律改用 UTF-8

function Test-Listening([int]$port) {
    $null -ne (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)
}

Write-Host '1/4 啟動資料庫（127.0.0.1:55432）'
docker compose -f "$root\docker-compose.yml" up -d --wait db
if ($LASTEXITCODE -ne 0) { throw '資料庫啟動失敗：請確認 Docker Desktop 已開啟' }

Write-Host '2/4 更新資料表'
Push-Location "$root\backend"
try {
    $env:PYTHONPATH = '.'
    uv run alembic upgrade head
    if ($LASTEXITCODE -ne 0) { throw '資料表更新失敗' }
    # 舊的資料庫磁碟區升級時，判決庫寫入帳號 corpus_writer 由 migration 建成不能登入（migration 不放密碼）；
    # 開發環境補上 init.sql 裡那組開發用密碼，「貼網址加入判決」才能寫入。可重跑
    docker compose -f "$root\docker-compose.yml" exec -T db psql -U postgres -v ON_ERROR_STOP=1 -q `
        -c "ALTER ROLE corpus_writer LOGIN PASSWORD 'corpuspass'"
    if ($LASTEXITCODE -ne 0) { throw '判決庫寫入帳號設定失敗' }
    uv run python -m app.cli load-calendar  # 行政機關辦公日曆（期限試算用），可重跑
    if ($LASTEXITCODE -ne 0) { throw '辦公日曆匯入失敗' }
} finally {
    Pop-Location
}

# 校對員（PaddleOCR，計畫 2A）：2026-11-25 起預設只用 AI 辨識，校對員不執行（設定 OCR_PROOFREAD=true 才會用）。
# 已安裝就讓後端知道怎麼執行，之後要恢復校對時不用再設定
$paddle = "$root\tools\paddle"
if (Test-Path "$paddle\.venv\Scripts\python.exe") {
    $env:PADDLE_CMD = ConvertTo-Json -Compress -InputObject @("$paddle\.venv\Scripts\python.exe", "$paddle\paddle_proofread.py")
} else {
    Write-Host '    校對員未安裝（執行 tools\paddle\setup.ps1 安裝）'
}

Write-Host '3/4 後端（127.0.0.1:8000）'
if (Test-Listening 8000) {
    Write-Host '    已在執行，略過'
} else {
    Start-Process powershell -WorkingDirectory "$root\backend" -ArgumentList '-NoExit', '-Command',
        "`$host.UI.RawUI.WindowTitle = 'legaltech 後端'; `$env:PYTHONUTF8 = '1'; uv run python -m app.serve"
}

Write-Host '4/4 前端（127.0.0.1:5173）'
if (Test-Listening 5173) {
    Write-Host '    已在執行，略過'
} else {
    Start-Process powershell -WorkingDirectory "$root\frontend" -ArgumentList '-NoExit', '-Command',
        "`$host.UI.RawUI.WindowTitle = 'legaltech 前端'; pnpm dev"
}

Write-Host '完成，開啟 http://127.0.0.1:5173'
Start-Process 'http://127.0.0.1:5173'
