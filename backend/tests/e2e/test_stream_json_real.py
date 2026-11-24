"""計畫 2A Task 2：確認真的本機 Claude Code 用 stream-json 送圖時，result 事件的格式（會用訂閱額度，預設不跑）。

執行：PYTHONUTF8=1 uv run pytest -q -s -m real_ai tests/e2e/test_stream_json_real.py
"""
import io
import os
import subprocess
import tempfile

import pytest
from PIL import Image, ImageDraw, ImageFont

from app.ai.claude_code import build_stream_args, child_env, parse_stream, stream_stdin, structured_data
from app.config import Settings

pytestmark = pytest.mark.real_ai
SCHEMA = {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}


def _png() -> bytes:
    img = Image.new("RGB", (200, 80), "white")
    font = None
    for name in ("msjh.ttc", "mingliu.ttc", "kaiu.ttf"):
        try:
            font = ImageFont.truetype(name, 32)
            break
        except OSError:
            continue
    ImageDraw.Draw(img).text((10, 20), "測試123", fill="black", font=font or ImageFont.load_default())
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def test_real_stream_json_returns_structured_output():
    cmd = Settings(_env_file=None).claude_cmd
    with tempfile.TemporaryDirectory() as work:
        sp = os.path.join(work, "..", f"sp-{os.getpid()}.md")
        with open(sp, "w", encoding="utf-8") as f:
            f.write("逐字抄錄圖片中的文字。只輸出抄錄結果。")
        try:
            args = build_stream_args(cmd, model="opus", effort="low", system_prompt_path=sp, schema=SCHEMA)
            p = subprocess.run(args, input=stream_stdin("請抄錄這一頁。", [_png()]), capture_output=True,
                               cwd=work, env=child_env(os.environ), timeout=180)
        finally:
            os.remove(sp)
    event = parse_stream(p.stdout)
    assert event is not None, p.stderr.decode("utf-8", "replace")
    print("\nstructured_output 存在：", isinstance(event.get("structured_output"), dict))
    data = structured_data(event, allow_result_text=True)
    print("取得的資料：", data)
    assert data is not None and "123" in data["text"]
