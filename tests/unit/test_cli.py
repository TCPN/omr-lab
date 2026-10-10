from omr_lab.cli import main


def test_help_invocation_returns_zero(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["omr-lab", "--help"])
    try:
        result = main()
    except SystemExit as exc:
        result = exc.code
    assert result == 0
    assert "Local-first Optical Music Recognition pipeline" in capsys.readouterr().out


def test_no_command_prints_help(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["omr-lab"])
    assert main() == 0
    assert "usage:" in capsys.readouterr().out.lower()
