import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "agent" / "claude-code"))
spec = importlib.util.spec_from_file_location("doctor", ROOT / "agent" / "claude-code" / "doctor.py")
doctor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(doctor)


def installed(folder, names):
    (folder / "installed_plugins.json").write_text(
        json.dumps({"version": 2, "plugins": {n: [{"scope": "user"}] for n in names}}), encoding="utf-8"
    )


def test_missing_plugin_is_only_a_warning(tmp_path, capsys):
    report = doctor.Report()
    doctor.check_plugin(report, {}, plugins_dir=tmp_path)
    assert report.failed == 0
    assert "WARN  Team plugin not installed" in capsys.readouterr().out


def test_plugin_without_build101_home_fails(tmp_path, capsys):
    installed(tmp_path, ["build101@build101"])
    report = doctor.Report()
    doctor.check_plugin(report, {}, plugins_dir=tmp_path)
    assert report.failed == 1
    assert "BUILD101_HOME is not set" in capsys.readouterr().out


def test_plugin_with_build101_home_is_ok(tmp_path, capsys):
    installed(tmp_path, ["otro@x", "build101@build101"])
    report = doctor.Report()
    doctor.check_plugin(report, {"BUILD101_HOME": str(tmp_path)}, plugins_dir=tmp_path)
    assert report.failed == 0
    assert "OK    Team plugin installed" in capsys.readouterr().out
