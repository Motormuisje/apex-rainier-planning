from types import SimpleNamespace

import pytest

import ui.replay as replay


pytestmark = pytest.mark.no_fixture


class FakeResponse:
    def __init__(self, payload=None):
        self.payload = payload or {"success": True}

    def get_json(self, silent=False):
        return self.payload


def test_recalculate_value_results_calls_value_engine(monkeypatch):
    calls = []

    class RecordingValuePlanningEngine:
        def __init__(self, data, results, aux_overrides=None):
            calls.append((data, results, aux_overrides))

        def calculate(self):
            return {"value": ["rows"]}

    monkeypatch.setattr(replay, "ValuePlanningEngine", RecordingValuePlanningEngine)
    engine = SimpleNamespace(data=SimpleNamespace(id="data"), results={"rows": []}, value_results={}, _iq_cache=object())
    sess = {"value_aux_overrides": {"01. Demand forecast||MAT-1": {"new_value": "2.5"}}}

    replay.recalculate_value_results(engine, sess)

    assert calls == [(engine.data, engine.results, {"01. Demand forecast||MAT-1": 2.5})]
    assert engine.value_results == {"value": ["rows"]}
    assert engine._iq_cache is None


def test_get_value_aux_override_values_skips_invalid_values():
    sess = {
        "value_aux_overrides": {
            "dict-value": {"new_value": "3.5"},
            "raw-value": "4.5",
            "bad-dict": {"new_value": "not-a-number"},
            "bad-raw": object(),
        },
    }

    assert replay.get_value_aux_override_values(sess) == {
        "dict-value": 3.5,
        "raw-value": 4.5,
    }


def test_replay_pending_edits_recalculates_value_results_for_aux_overrides(monkeypatch):
    calls = []

    class RecordingValuePlanningEngine:
        def __init__(self, data, results, aux_overrides=None):
            calls.append((data, results, aux_overrides))

        def calculate(self):
            return {"value": ["rows"]}

    monkeypatch.setattr(replay, "ValuePlanningEngine", RecordingValuePlanningEngine)
    engine = SimpleNamespace(data=SimpleNamespace(id="data"), results={}, value_results={})
    sess = {"pending_edits": {}, "value_aux_overrides": {"key": {"new_value": 2.0}}}

    replay.replay_pending_edits(
        sess,
        engine,
        lambda *args, **kwargs: pytest.fail("volume edit should not be replayed"),
        lambda e, overrides: False,
        lambda e, s: pytest.fail("capacity should not recalculate"),
    )

    assert calls == [(engine.data, engine.results, {"key": 2.0})]
    assert engine.value_results == {"value": ["rows"]}


def test_replay_pending_edits_replays_all_volume_edits_in_order():
    sess = {
        "pending_edits": {
            "01. Demand forecast||MAT-1||||2025-12": {"original": 10.0, "new_value": 15.0},
            "01. Demand forecast||MAT-2||||2025-12": {"original": 20.0, "new_value": 25.0},
        },
    }
    engine = SimpleNamespace()
    calls = []
    capacity_calls = []

    def apply_volume_change_fn(*args, **kwargs):
        calls.append((args, kwargs))
        return FakeResponse()

    replay.replay_pending_edits(
        sess,
        engine,
        apply_volume_change_fn,
        lambda e, overrides: False,
        lambda e, s: capacity_calls.append((e, s)),
    )

    assert [call[0][3] for call in calls] == ["MAT-1", "MAT-2"]
    assert capacity_calls == []


def test_replay_pending_edits_applies_machine_overrides_when_present():
    sess = {"pending_edits": {}, "machine_overrides": {"M1": {"oee": 0.9}}}
    engine = SimpleNamespace()
    override_calls = []
    capacity_calls = []

    def apply_machine_overrides_fn(engine_arg, overrides):
        override_calls.append((engine_arg, overrides))
        return True

    replay.replay_pending_edits(
        sess,
        engine,
        lambda *args, **kwargs: pytest.fail("volume edit should not be replayed"),
        apply_machine_overrides_fn,
        lambda e, s: capacity_calls.append((e, s)),
    )

    assert override_calls == [(engine, {"M1": {"oee": 0.9}})]
    assert capacity_calls == [(engine, sess)]


def test_replay_pending_edits_skips_recalc_when_no_edits_and_no_overrides():
    sess = {"pending_edits": {}, "machine_overrides": {}}
    engine = SimpleNamespace()
    calls = []

    replay.replay_pending_edits(
        sess,
        engine,
        lambda *args, **kwargs: calls.append("volume"),
        lambda e, overrides: calls.append("machine") or False,
        lambda e, s: calls.append("capacity"),
    )

    assert calls == []


def test_replay_pending_edits_passes_correct_args_to_apply_volume_change():
    sess = {
        "pending_edits": {
            "01. Demand forecast||MAT-1||Aux||2025-12": {"original": 10.0, "new_value": 15.5},
        },
    }
    engine = SimpleNamespace()
    calls = []

    def apply_volume_change_fn(*args, **kwargs):
        calls.append((args, kwargs))
        return FakeResponse()

    replay.replay_pending_edits(
        sess,
        engine,
        apply_volume_change_fn,
        lambda e, overrides: False,
        lambda e, s: None,
    )

    args, kwargs = calls[0]
    assert args[:6] == (
        sess,
        engine,
        "01. Demand forecast",
        "MAT-1",
        "2025-12",
        15.5,
    )
    assert kwargs == {"aux_column": "Aux", "push_undo": False}


def test_replay_pending_edits_ignores_malformed_keys():
    sess = {"pending_edits": {"not||valid": {"new_value": 1.0}}}
    calls = []

    replay.replay_pending_edits(
        sess,
        SimpleNamespace(),
        lambda *args, **kwargs: calls.append("volume"),
        lambda e, overrides: False,
        lambda e, s: None,
    )

    assert calls == []


def test_replay_pending_edits_logs_failed_and_unsuccessful_edits(capsys):
    sess = {
        "pending_edits": {
            "01. Demand forecast||MAT-1||||2025-12": {"new_value": "not-a-number"},
            "01. Demand forecast||MAT-2||||2025-12": {"new_value": 2.0},
        },
    }

    def apply_volume_change_fn(*args, **kwargs):
        return FakeResponse({"success": False, "error": "no row"})

    replay.replay_pending_edits(
        sess,
        SimpleNamespace(),
        apply_volume_change_fn,
        lambda e, overrides: False,
        lambda e, s: None,
    )

    captured = capsys.readouterr()
    assert 'failed "01. Demand forecast||MAT-1||||2025-12"' in captured.out
    assert 'skipped "01. Demand forecast||MAT-2||||2025-12"' in captured.out


def test_replay_pending_edits_recalculates_values_after_volume_edits_with_aux_overrides(monkeypatch):
    calls = []

    class RecordingValuePlanningEngine:
        def __init__(self, data, results, aux_overrides=None):
            calls.append((data, results, aux_overrides))

        def calculate(self):
            return {"value": ["rows"]}

    monkeypatch.setattr(replay, "ValuePlanningEngine", RecordingValuePlanningEngine)
    engine = SimpleNamespace(data=SimpleNamespace(id="data"), results={}, value_results={})
    sess = {
        "pending_edits": {
            "01. Demand forecast||MAT-1||||2025-12": {"new_value": 1.0},
        },
        "value_aux_overrides": {"key": {"new_value": 2.0}},
    }

    replay.replay_pending_edits(
        sess,
        engine,
        lambda *args, **kwargs: FakeResponse(),
        lambda e, overrides: False,
        lambda e, s: None,
    )

    assert calls == [(engine.data, engine.results, {"key": 2.0})]
