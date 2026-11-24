# AI 來源登錄表與新增來源檢查清單

系統所有 AI 呼叫只走兩個入口：

- `get_provider`（`backend/app/ai/provider.py`）依設定建立 AI 來源；
- `call_ai`（`backend/app/ai/usage.py`）呼叫它，並把每一次嘗試的用量寫進 `usage_records`。成功、失敗、內部重試都會記。

程式其他地方不直接呼叫各家的程式庫或指令（測試 `test_no_direct_vendor_calls_outside_ai_package` 會擋）。

## 登錄表（`backend/app/ai/registry.py`）

| 代碼 | 名稱 | 需要金鑰 | 計費 | 狀態 |
|---|---|---|---|---|
| `claude_code_local` | 本機 Claude Code（開發者訂閱） | 否 | 不按量（換算僅供參考） | 只在 dev、只綁本機、只對內部測試事務所 |
| `bedrock` | AWS Bedrock（日本區） | 是 | 按量 | 已預留，尚未啟用（計畫 5） |

`Settings.ai_provider` 可選的值必須和登錄表的代碼一致（測試 `test_registry_matches_settings`）。

## 新增一個 AI 來源＝三件事

1. **一個實作**：在 `backend/app/ai/` 底下加一個類別，要有：
   - `run(*, step, system, user, schema, effort, timeout_s) -> AIResult`；
   - `name`、`model` 屬性。

   失敗時丟 `AIError`，`attempts` 帶每次嘗試的用量；內部重試成功時，前幾次的用量放在 `AIResult.retried`。
2. **一列登錄**：在 `REGISTRY` 加一列（代碼、名稱、需不需要金鑰、按量或不按量、建立函式），並把代碼加進 `Settings.ai_provider` 的可選值。
3. **一列單價**：在 `backend/app/costs.py` 的 `DEFAULT_ROWS` 加預設單價（每百萬 token 的美元：讀入、寫出、快取讀、5 分鐘快取寫入、1 小時快取寫入），並寫明是否按量計費。事務所管理員之後可在「用量與費用」頁修改。

## 上線前檢查清單（設計 5.12）

- [ ] **支援 JSON 結構輸出**：每個步驟都用 JSON Schema 要求結構化輸出。不合格時要能重試，或回報 `invalid_output`。
- [ ] **回報用量**：每次呼叫回報讀入、寫出、快取讀、快取寫入的 token 數。能分開回報 5 分鐘與 1 小時快取寫入的，就分開回報。
- [ ] **確認不拿輸入資料去訓練**：取得供應商的書面條款（資料保留期間、是否用於訓練、資料所在地區），存檔備查。
- [ ] **重跑全部真 AI 驗收**：用新來源重跑 `backend/tests/e2e/` 的真實 AI 驗收，結果與既有來源比對。不得低於既有來源的通過標準。
- [ ] **決定啟動規則（設計 3.1）**：寫進 `check_startup` 與 `get_provider`。內容包括：
  - 哪些環境（dev、pilot、prod）可以用；
  - 是否只能本機使用；
  - 是否只限內部測試事務所。
- [ ] **金鑰保管**：需要金鑰的來源，金鑰只放部署環境的秘密管理，不放程式庫、不貼在對話或文件裡。
