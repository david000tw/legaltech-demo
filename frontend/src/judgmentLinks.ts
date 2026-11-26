// 判決庫（計畫 3 丙）共用的網址與提示文字
export const judgmentPath = (caseId: string, judgmentId: string) => `/cases/${caseId}/judgments/${judgmentId}`
export const citedPath = (caseId: string, key: string) => `/cases/${caseId}/cited/${encodeURIComponent(key)}`
export const searchByArticlePath = (caseId: string, article: string) =>
  `/cases/${caseId}?tab=search&article=${encodeURIComponent(article)}`

export const IMPORT_HINT = '可以到司法院裁判書查詢找找看；找得到就把網址貼到判決檢索的「加入判決」。'
  + '很舊的判決、判例或最高法院判決可能查不到或無法加入。'
export const NOT_IMPORTABLE = '目前無法加入判決庫'

// 庫外判決的加入提示：最高法院判決在伺服器驗證過不能加入時，不給貼網址的提示
export const importHint = (supreme: boolean, supremeImportable: boolean) =>
  supreme && !supremeImportable ? NOT_IMPORTABLE : IMPORT_HINT

export const AI_WRITTEN = 'AI 撰寫'

// 「法院・日期・案型」：案型用後端給的中文名稱，沒有就省略
export const courtLine = (court: string, decidedOn: string, labels: string[]) =>
  [court, decidedOn, labels.join('、')].filter(Boolean).join('・')
