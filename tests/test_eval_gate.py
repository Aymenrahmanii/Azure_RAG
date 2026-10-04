import json

from eval.gate import check, lookup, main

SUMMARY = {
    "overall": {"n": 60, "correctness": 0.9, "false_refusal_rate": 0.02, "recall@all": None},
    "by_type": {"global": {"correctness": 0.8}},
}


def test_lookup_paths():
    assert lookup(SUMMARY, "overall.correctness") == 0.9
    assert lookup(SUMMARY, "by_type.global.correctness") == 0.8
    assert lookup(SUMMARY, "by_type.nope.correctness") is None
    assert lookup(SUMMARY, "overall.recall@all") is None  # present but null


def test_check_min_max_and_missing():
    rules = {
        "overall.correctness": {"min": 0.86},
        "overall.false_refusal_rate": {"max": 0.08},
        "by_type.global.correctness": {"min": 0.85},
        "overall.mrr": {"min": 0.5},
    }
    ok = {r["metric"]: r["ok"] for r in check(SUMMARY, rules)}
    assert ok == {
        "overall.correctness": True,
        "overall.false_refusal_rate": True,
        "by_type.global.correctness": False,  # 0.80 < 0.85
        "overall.mrr": False,  # missing counts as failure, never as a pass
    }


def test_boundary_value_passes():
    assert check({"overall": {"x": 0.86}}, {"overall.x": {"min": 0.86}})[0]["ok"]


def run_gate(tmp_path, monkeypatch, summary, n_rules):
    results = tmp_path / "r.json"
    results.write_text(json.dumps({"summary": summary, "config": {"label": "t"}}))
    thresholds = tmp_path / "t.json"
    thresholds.write_text(json.dumps({"generation": n_rules}))
    monkeypatch.setattr(
        "sys.argv",
        ["gate", str(results), "--tier", "generation", "--thresholds", str(thresholds)],
    )
    return main()


def test_exit_code_reflects_regression(tmp_path, monkeypatch, capsys):
    good = run_gate(tmp_path, monkeypatch, SUMMARY, {"overall.correctness": {"min": 0.86}})
    bad = run_gate(tmp_path, monkeypatch, SUMMARY, {"overall.correctness": {"min": 0.95}})
    assert (good, bad) == (0, 1)
    assert "FAIL" in capsys.readouterr().out


def test_partial_run_is_refused(tmp_path, monkeypatch):
    small = {"overall": {"n": 5, "correctness": 1.0}, "by_type": {}}
    assert run_gate(tmp_path, monkeypatch, small, {"overall.correctness": {"min": 0.5}}) == 1


def test_real_thresholds_pass_on_recorded_baseline_shape():
    rules = json.load(open("eval/thresholds.json", encoding="utf-8"))
    assert set(rules) >= {"retrieval", "generation"}
    for tier in ("retrieval", "generation"):
        for bound in rules[tier].values():
            assert set(bound) <= {"min", "max"}
