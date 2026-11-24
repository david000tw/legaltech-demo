"""校對員指令工具：用 PaddleOCR 讀圖，結果寫成 JSON 檔，供後端與 AI 的辨識結果互相核對（計畫 2A）。

用法：python paddle_proofread.py <out.json> <img1.png> [img2.png ...]
輸出：[{"cells": [{"text", "score", "box": [x1, y1, x2, y2]}, ...]}, ...]，每張圖一筆，順序同輸入。
失敗時以非 0 結束。在 tools/paddle/.venv（setup.ps1 建立）執行，不與後端共用環境。
"""
import json
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) < 3:
        print("用法：paddle_proofread.py <out.json> <img1.png> [...]", file=sys.stderr)
        return 2
    out, *images = sys.argv[1:]
    from paddleocr import PaddleOCR

    # 與比較測試相同的設定；enable_mkldnn=False 避開 Windows 上 oneDNN 的 NotImplementedError
    ocr = PaddleOCR(use_doc_orientation_classify=False, use_doc_unwarping=False,
                    use_textline_orientation=False, enable_mkldnn=False)
    try:
        from opencc import OpenCC
        to_trad = OpenCC("s2t").convert  # PaddleOCR 會把部分字讀成簡體（例：项、装）
    except ImportError:
        to_trad = None
    pages = []
    for img in images:
        res = list(ocr.predict(img))[0]
        j = res.json
        r = j.get("res", j)
        cells = []
        for text, score, box in zip(r["rec_texts"], r["rec_scores"], r["rec_boxes"]):
            cells.append({"text": to_trad(text) if to_trad else text, "score": float(score),
                          "box": [float(v) for v in box]})
        pages.append({"cells": cells})
    Path(out).write_text(json.dumps(pages, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
