"""notify.py の差分ロジック（純関数）のテスト。"""
import notify


def test_new_entrants_detects_only_new_and_keeps_order():
    today = ["1301", "7203", "4555", "9984"]
    prev = ["7203", "9984"]
    assert notify.new_entrants(today, prev) == ["1301", "4555"]


def test_new_entrants_empty_when_no_change():
    today = ["1301", "7203"]
    assert notify.new_entrants(today, ["7203", "1301"]) == []


def test_new_entrants_all_new_on_first_run():
    today = ["1301", "7203"]
    assert notify.new_entrants(today, []) == ["1301", "7203"]
