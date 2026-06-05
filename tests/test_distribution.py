"""distribution.py のユニットテスト（モック・決定論）。

意図(WHY):
- 分配日 = 「前日比 −0.2%以下の下落」かつ「出来高が前日より増加」の両方を満たす日のみ
- 下落でも出来高が増えていなければ分配日ではない
- 下落幅が小さければ（−0.2%未満の下落）分配日ではない
- 分配日は窓超過 or +5%上昇で失効する
- 規定本数に達すると caution=True
"""
import distribution
from distribution import analyze, latest_state


def test_distribution_day_requires_decline_and_volume_up():
    dates = ["d0", "d1", "d2", "d3"]
    close = [100.0, 99.0, 98.7, 98.6]   # d1: -1.0%, d2: -0.30%, d3: -0.10%
    volume = [100.0, 120.0, 110.0, 200.0]  # d1: up, d2: down, d3: up
    rec = analyze(dates, close, volume)
    by = {r["date"]: r for r in rec}
    assert by["d1"]["is_distribution"] is True    # 下落+出来高増
    assert by["d2"]["is_distribution"] is False   # 下落だが出来高減
    assert by["d3"]["is_distribution"] is False   # 下落−0.10%は閾値未満


def test_caution_when_threshold_reached():
    dates = [f"d{i}" for i in range(6)]
    close = [100.0, 99.0, 98.0, 97.0, 96.0, 95.0]      # 毎日約-1%
    volume = [100.0, 110.0, 120.0, 130.0, 140.0, 150.0]  # 毎日出来高増
    rec = analyze(dates, close, volume, {"count_threshold": 5})
    assert rec[5]["active_count"] == 5
    assert rec[5]["caution"] is True
    assert rec[4]["caution"] is False   # 4本ではまだ


def test_expire_on_5pct_gain():
    dates = ["d0", "d1", "d2"]
    close = [100.0, 99.0, 105.0]        # d1分配日, d2は+6%上昇→失効
    volume = [100.0, 120.0, 80.0]
    rec = analyze(dates, close, volume)
    assert rec[1]["is_distribution"] is True
    assert rec[1]["active_count"] == 1
    assert rec[2]["active_count"] == 0  # +5%超で失効


def test_expire_on_window():
    dates = [f"d{i}" for i in range(5)]
    close = [100.0, 99.0, 98.0, 97.0, 96.0]
    volume = [100.0, 110.0, 120.0, 130.0, 140.0]
    rec = analyze(dates, close, volume, {"window": 3})
    # i=4: j=1 は (4-1)=3 で窓(3)外→失効。active=[2,3,4]
    assert rec[4]["active_count"] == 3


def test_latest_state():
    dates = ["d0", "d1"]
    close = [100.0, 99.0]
    volume = [100.0, 120.0]
    rec = analyze(dates, close, volume)
    st = latest_state(rec)
    assert st["date"] == "d1"
    assert st["active_count"] == 1
