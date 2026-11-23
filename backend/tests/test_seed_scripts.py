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
