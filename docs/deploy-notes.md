# 部署注意事項

計畫 5（正式部署）會把這份整理成完整的部署手冊；目前先記下各階段累積、部署時一定要做的手動步驟。

## 判決庫寫入帳號 corpus_writer（子專案丙）

「律師貼司法院網址加入判決」用一個權限很小的資料庫帳號 `corpus_writer` 寫入判決庫：只能讀取、新增判決與段落，不能碰任何事務所的資料。

- **開發環境**：全新的資料庫由 `init.sql` 建立這個帳號並設開發用密碼；既有的資料庫由 `dev.ps1` 每次啟動時補上登入權限與密碼。不用手動處理。
- **試用（pilot）與正式環境**：migration 為了不把密碼放進公開的程式庫，只會把帳號建成「不能登入」。部署時要另外執行一次：

  ```sql
  ALTER ROLE corpus_writer LOGIN PASSWORD '<從秘密管理服務取得的密碼>';
  ```

  並把同一組密碼放進後端的 `DATABASE_CORPUS_URL` 環境變數（例如 `postgresql+psycopg://corpus_writer:<密碼>@<主機>:5432/<資料庫>`）。
- **沒設定會怎樣**：律師按「加入」會看到「判決庫目前無法加入新判決，請聯絡系統管理員」，後端日誌記下 `corpus_writer 無法寫入判決庫` 與連線錯誤。判決檢索、釘選等其他功能不受影響。

## 判決索引與要旨（計畫 3 丙）

- **部署或升級後執行一次**：`python -m app.cli reindex-judgments`（在 backend 目錄，用 admin 連線）。它替每份判決建條文索引、引用關係，並補上比對鍵與規則版本。沒執行的話「依法條」找不到舊判決；後端啟動時會記警告「有 N 份判決的索引需要重算」。開發環境由 `dev.ps1` 自動執行。
- **權限（遷移 0021 會設好）**：
  - `app_user`：三張新表 `judgment_article_refs`、`judgment_case_refs`、`judgment_summaries` 只能讀；
  - `corpus_writer`：三張新表可讀、可新增；`judgment_summaries` 另外只能改 `status, claim_token, claimed_at, fingerprint, issues, excerpts, model, finished_at, error_kind`（欄位層級授權）。其他表維持只能新增。
- **沒設定 corpus_writer 會怎樣**：整份判決頁照常，要旨區顯示「要旨暫時無法產生，請聯絡系統管理員」。
- **規則改版**：改了 `app/corpus/article_refs.py` 或 `case_refs.py` 的規則時，把 `app/corpus/ref_index.py` 的 `REFS_VERSION` 加 1，部署後再執行一次重算指令。
- **要旨的費用**：記在第一次打開該判決的事務所與案件（用量頁步驟「判決要旨」）；做好的要旨所有事務所共用。

## 啟動時中斷辨識工作

開發環境啟動時（`app/main.py` 的 lifespan，用超級使用者連線）會把所有事務所還標著 running 的辨識工作標成中斷，讓「繼續辨識」立刻出現；試用與正式環境不執行，維持 10 分鐘心跳逾時。若同時跑多個 uvicorn worker 或滾動重啟，這段啟動復原可能把另一個還活著的 worker 的辨識工作也標成中斷，所以計畫 5 必須把它改成由單一協調者執行（並改用權限較小的做法，網頁伺服器不該持有超級使用者帳號）。

開發環境也要注意：lifespan 在 uvicorn 綁定連接埠之前執行，所以不小心再啟動第二個後端（連接埠已被占用）時，它會先把正在運作的那個後端的辨識工作標成中斷，然後才因為連接埠衝突結束；畫面會出現「繼續辨識」，按下去會重辨識。用 `dev.ps1` 啟動前先確認沒有舊的後端在跑。
