"""解析全國法規資料庫單條條文頁（LawSingle.aspx）的原始 HTML。"""
from __future__ import annotations

import html as htmllib
import re

_NAME_RE = re.compile(r"法規名稱：</th>\s*<td[^>]*>(.*?)</td>", re.S)
_NO_RE = re.compile(r'<div class="col-no">\s*第\s*([0-9]+(?:-[0-9]+)?)\s*條\s*</div>')
_ARTICLE_START = '<div class="law-article">'
_LINE_RE = re.compile(r'<div class="line-\d+[^"]*">(.*?)</div>', re.S)
_TAG_RE = re.compile(r"<[^>]+>")


def _clean(fragment: str) -> str:
    return htmllib.unescape(_TAG_RE.sub("", fragment)).strip()


def parse_law_single(html: str) -> dict:
    """回傳 {"name", "article_no", "text"}；各項以換行分隔，內容逐字取自原始 HTML。"""
    name_m = _NAME_RE.search(html)
    no_m = _NO_RE.search(html)
    start = html.find(_ARTICLE_START)
    if not (name_m and no_m) or start < 0:
        raise ValueError("不是單條條文頁，或頁面格式已變更")
    end = html.find('class="clearfix"', start)
    region = html[start:end if end > 0 else len(html)]
    name = _clean(name_m.group(1)).split()[0]
    lines = [_clean(x) for x in _LINE_RE.findall(region)]
    return {"name": name, "article_no": no_m.group(1), "text": "\n".join(x for x in lines if x)}
