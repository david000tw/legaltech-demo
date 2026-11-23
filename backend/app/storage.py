"""本機檔案儲存。存放鍵只含 ASCII（事務所 id／案件 id／隨機 id＋副檔名），原檔名只存在資料庫。"""
from __future__ import annotations

import re
from pathlib import Path
from uuid import UUID, uuid4

_EXT_RE = re.compile(r"^\.[a-z0-9]{1,8}$")


class LocalStorage:
    def __init__(self, root: Path):
        self.root = Path(root)

    def put(self, firm_id: UUID, case_id: UUID, ext: str, data: bytes) -> str:
        ext = ext.lower()
        if not _EXT_RE.match(ext):
            raise ValueError(f"不合法的副檔名：{ext!r}")
        key = f"{firm_id}/{case_id}/{uuid4()}{ext}"
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return key

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def _path(self, key: str) -> Path:
        p = (self.root / key).resolve()
        if self.root.resolve() not in p.parents:
            raise ValueError("存放鍵超出儲存目錄")
        return p
