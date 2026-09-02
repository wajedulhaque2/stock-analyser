from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_windows_launcher_uses_shared_cached_environment_and_hash_gate():
    text = (ROOT / "run_windows.bat").read_text(encoding="utf-8").lower()
    assert "%localappdata%\\stockanalyser" in text
    assert "get-filehash" in text
    assert 'if /i "%req_hash%"=="%old_hash%" goto start_app' in text
    assert "pip install --upgrade pip" not in text


def test_mac_launcher_uses_shared_cached_environment_and_hash_gate():
    text = (ROOT / "run_mac_linux.sh").read_text(encoding="utf-8").lower()
    assert "stock-analyser" in text
    assert "requirements.sha256" in text
    assert 'if [ "$new_env" = "1" ] || [ "$req_hash" != "$old_hash" ]' in text
