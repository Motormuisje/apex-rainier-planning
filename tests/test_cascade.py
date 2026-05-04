from types import SimpleNamespace

import pytest

import ui.cascade as cascade


pytestmark = pytest.mark.no_fixture


class RecordingInventoryEngine:
    def __init__(self, data):
        self.data = data


class RecordingBOMEngine:
    def __init__(self, data):
        self.data = data


@pytest.fixture
def cascade_engine(monkeypatch):
    monkeypatch.setattr("modules.inventory_engine.InventoryEngine", RecordingInventoryEngine)
    monkeypatch.setattr("modules.bom_engine.BOMEngine", RecordingBOMEngine)
    return SimpleNamespace(data=SimpleNamespace(periods=["2025-12"]))


def test_recalc_pap_material_calls_recalc_on_root_and_children(cascade_engine):
    calls = []

    def recalc_one_material_fn(engine, material, inv_eng, bom_eng, periods, override_forecast=False):
        calls.append((engine, material, type(inv_eng), type(bom_eng), periods, override_forecast))
        if material == "ROOT":
            return {"CHILD-1": {"2025-12": 5.0}}
        return {}

    cascade.recalc_pap_material(cascade_engine, "ROOT", recalc_one_material_fn)

    assert [(call[1], call[5]) for call in calls] == [("ROOT", True), ("CHILD-1", False)]
    assert all(call[0] is cascade_engine for call in calls)
    assert all(call[2] is RecordingInventoryEngine for call in calls)
    assert all(call[3] is RecordingBOMEngine for call in calls)
    assert all(call[4] == ["2025-12"] for call in calls)


def test_recalc_pap_material_stops_bfs_when_no_children(cascade_engine):
    calls = []

    def recalc_one_material_fn(engine, material, inv_eng, bom_eng, periods, override_forecast=False):
        calls.append(material)
        return {}

    cascade.recalc_pap_material(cascade_engine, "ROOT", recalc_one_material_fn)

    assert calls == ["ROOT"]


def test_recalc_pap_material_skips_already_visited_grandchild(cascade_engine):
    calls = []

    def recalc_one_material_fn(engine, material, inv_eng, bom_eng, periods, override_forecast=False):
        calls.append(material)
        if material == "ROOT":
            return {"CHILD-1": {}}
        if material == "CHILD-1":
            return {"ROOT": {}}
        return {}

    cascade.recalc_pap_material(cascade_engine, "ROOT", recalc_one_material_fn)

    assert calls == ["ROOT", "CHILD-1"]


def test_finish_pap_recalc_calls_capacity_and_values_with_engine_and_sess():
    engine = SimpleNamespace(id="engine")
    sess = SimpleNamespace(id="s1")
    calls = []

    cascade.finish_pap_recalc(engine, sess, lambda e, s: calls.append((e, s)))

    assert calls == [(engine, sess)]


def test_finish_pap_recalc_accepts_none_session():
    engine = SimpleNamespace(id="engine")
    calls = []

    cascade.finish_pap_recalc(engine, None, lambda e, s: calls.append(s))

    assert calls == [None]
