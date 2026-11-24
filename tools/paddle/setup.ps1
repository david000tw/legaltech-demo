# 安裝校對員（PaddleOCR）到 tools\paddle\.venv（約 800 MB，與後端分開的獨立環境）。可重跑。
# 用法：在 legaltech-app 資料夾執行  .\tools\paddle\setup.ps1
# 授權：paddlepaddle、paddleocr、opencc-python-reimplemented 皆為 Apache-2.0（安裝後會印出確認）。
# 不用 'Stop'：Windows PowerShell 5.1 在輸出被導向時，會把 uv 寫到 stderr 的進度訊息當成錯誤；
# 每個外部指令改用結束碼判斷成敗。
$ErrorActionPreference = 'Continue'
$here = $PSScriptRoot
$env:PYTHONUTF8 = '1'
$py = "$here\.venv\Scripts\python.exe"

Write-Host '1/4 建立獨立環境（Python 3.12）'
if (-not (Test-Path $py)) {
    uv venv --python 3.12 "$here\.venv"
    if ($LASTEXITCODE -ne 0) { throw '建立環境失敗：請確認已安裝 uv 與 Python 3.12' }
}

Write-Host '2/4 安裝 paddlepaddle、paddleocr、opencc-python-reimplemented'
# 固定為驗收測過的版本（2026-11-24）：新版輸出格式若改變，每頁都會變成「校對員未執行」；paddlex 是 paddleocr 的相依套件
uv pip install --python $py paddlepaddle==3.3.1 paddleocr==3.7.0 paddlex==3.7.2 opencc-python-reimplemented==0.1.7
if ($LASTEXITCODE -ne 0) { throw '安裝失敗' }

Write-Host '3/4 授權'
# 傳給 python -c 的程式只用單引號：Windows PowerShell 5.1 傳參數給外部程式時會吃掉雙引號
& $py -c @'
from importlib.metadata import metadata
for name in ('paddlepaddle', 'paddleocr', 'opencc-python-reimplemented'):
    m = metadata(name)
    lic = m.get('License-Expression') or m.get('License') or ''
    cls = [c.split('::')[-1].strip() for c in (m.get_all('Classifier') or []) if c.startswith('License')]
    print('    ' + name + ' ' + m['Version'] + ': ' + lic[:60] + ' ' + str(cls))
'@
if ($LASTEXITCODE -ne 0) { throw '讀取授權失敗' }

Write-Host '4/4 試跑（第一次會下載辨識模型）'
$tmp = Join-Path ([System.IO.Path]::GetTempPath()) "paddle-setup-$PID"
New-Item -ItemType Directory -Force $tmp -ErrorAction Stop | Out-Null
try {
    & $py -c @"
from PIL import Image, ImageDraw, ImageFont
img = Image.new('RGB', (360, 90), 'white')
font = None
for name in ('msjh.ttc', 'mingliu.ttc', 'kaiu.ttf'):
    try:
        font = ImageFont.truetype(name, 40)
        break
    except OSError:
        pass
ImageDraw.Draw(img).text((12, 20), '合計 5,780元', fill='black', font=font or ImageFont.load_default())
img.save(r'$tmp\test.png')
"@
    & $py "$here\paddle_proofread.py" "$tmp\out.json" "$tmp\test.png"
    if ($LASTEXITCODE -ne 0) { throw '試跑失敗' }
    Write-Host ('    讀到：' + (Get-Content "$tmp\out.json" -Encoding UTF8 -Raw))
} finally {
    Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
}
Write-Host '完成。下次執行 .\dev.ps1 時後端會自動使用校對員。'
