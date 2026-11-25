"""資料庫連線與工作階段。

- tenant_session(firm_id)：一般請求用。以 app_user 連線，每個交易開始時設定
  app.firm_id（交易層級），中途 commit 後仍在同一事務所範圍。
- app_session()：以 app_user 連線、不設事務所，只用於登入查詢 auth_lookup。
- admin_session()：超級使用者連線，只給遷移、種子、CLI、測試準備資料。
- corpus_session()：corpus_writer 連線，只給律師貼網址加入判決用。
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from uuid import UUID

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import Session

from app.config import Settings

_engines: dict[str, Engine] = {}
_settings: Settings | None = None


def configure(settings: Settings) -> None:
    global _settings
    _settings = settings


def settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def engine_for(url: str) -> Engine:
    eng = _engines.get(url)
    if eng is None:
        eng = create_engine(url, pool_pre_ping=True)
        _engines[url] = eng
    return eng


@contextmanager
def _session(url: str, firm_id: UUID | None = None) -> Iterator[Session]:
    s = Session(engine_for(url), expire_on_commit=False)
    if firm_id is not None:
        fid = str(firm_id)

        @event.listens_for(s, "after_begin")
        def _set_firm(session, transaction, connection):
            connection.execute(text("select set_config('app.firm_id', :fid, true)"), {"fid": fid})

    try:
        yield s
        s.commit()
    except BaseException:
        s.rollback()
        raise
    finally:
        s.close()


# 案件列一律鎖成 FOR NO KEY UPDATE：案件鎖之間照樣互斥，但不擋「新增參照案件的列」的外鍵檢查（KEY SHARE）。
# 用 FOR UPDATE 時，段落鎖住後新增支持度檢查列（外鍵指向案件）會和「先鎖案件、再鎖段落」的辨識修正互等而死結。
CASE_LOCK = {"key_share": True}


def tenant_session(firm_id: UUID) -> Iterator[Session]:
    return _session(settings().database_url, firm_id)


def app_session() -> Iterator[Session]:
    return _session(settings().database_url)


def admin_session() -> Iterator[Session]:
    return _session(settings().database_admin_url)


def corpus_session() -> Iterator[Session]:
    """corpus_writer 帳號：只能讀、新增判決與段落，給 app/corpus/importer.py 加入判決用。"""
    return _session(settings().database_corpus_url)
