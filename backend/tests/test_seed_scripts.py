"""種子資料腳本：抓公開資料時要誠實表明身分，不偽裝成瀏覽器（不繞過網站的機器人防護）。"""
import importlib.util
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_statute_download_identifies_itself_honestly(monkeypatch, tmp_path):
    m = load_script("build_seed_statutes")
    agents = []

    class Resp:
        def read(self):
            return b"<html></html>"

    def fake_urlopen(req, timeout):
        agents.append(req.get_header("User-agent"))
        return Resp()

    monkeypatch.setattr(m.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(m, "RAW", tmp_path)
    monkeypatch.setattr(m, "STATUTES", [("民法", "B0000001", ["184"])])
    monkeypatch.setattr(m.time, "sleep", lambda s: None)
    m.download()
    assert agents and "Mozilla" not in agents[0] and agents[0].startswith("legaltech-seed-builder")


def test_judgment_builder_rejects_bad_split():
    import pytest
    m = load_script("build_seed_judgment")
    bad = {"citation": "測試字第1號", "full_text": "事實及理由\n一、原告主張：被告應給付10萬元。\n"}
    with pytest.raises(SystemExit) as e:
        m.check_split(bad)
    assert "no_court_reasoning" in str(e.value) and "測試字第1號" in str(e.value)
