"""build.py pass-failure registry: soft failures are reported, critical ones fail the build."""
import build


def _reset():
    build._PASS_FAILURES.clear()


def test_no_failures_exit_zero():
    _reset()
    assert build.report_pass_failures() == 0


def test_soft_failure_reported_but_not_fatal():
    _reset()
    build._pass_failed("milestones", ValueError("boom"))
    assert build.report_pass_failures() == 0
    assert build._PASS_FAILURES == [("milestones", False, "ValueError: boom")]
    _reset()


def test_critical_failure_is_fatal_unless_partial_allowed(tmp_path, monkeypatch):
    _reset()
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    build._pass_failed("gen_stats.py", KeyError("x"), critical=True)
    assert build.report_pass_failures() == 1
    assert build.report_pass_failures(allow_partial=True) == 0
    assert "CRITICAL: gen_stats.py" in summary.read_text(encoding="utf-8")
    _reset()
