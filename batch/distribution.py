"""
オニールの「分配日（Distribution Day）」カウントによるマーケット天井検出。

定義（『オニールの成長株発掘法』）:
- 分配日 = 指数が前日比 −0.2% 以下で下落し、かつ出来高が前日より増えた日（＝機関の売り抜け）。
- 直近およそ25営業日（5週間）の窓に 分配日が 4〜5本 たまると、上昇相場の天井が近いサイン。
- 分配日は「失効」する: ①記録から25営業日が経過、または ②指数がその分配日の終値から +5% 以上上昇。

注意: 指数(TOPIX)自体は出来高を持たない場合があるため、出来高は
「全銘柄の出来高合計（市場全体の売買代金/出来高）」を用いるのが忠実（build_db側で集計）。

すべて純関数。numpyに依存せずリストで処理し、モックでテスト可能。
"""

from __future__ import annotations

DEFAULTS = {
    "decline_pct": 0.2,     # 分配日とみなす下落率（%）
    "window": 25,           # カウント窓（営業日）
    "expire_gain_pct": 5.0,  # 終値が分配日比でこの%上がると失効
    "count_threshold": 5,   # この本数以上で「天井警戒」
}


def analyze(dates: list[str], close: list[float], volume: list[float],
            cfg: dict | None = None) -> list[dict]:
    """
    日次の指数(close)と市場出来高(volume)から、各日のレコードを返す。
    各レコード: {date, close, volume, ret_pct, is_distribution, active_count, caution}
    入力は日付昇順。
    """
    c = {**DEFAULTS, **(cfg or {})}
    n = len(dates)
    out: list[dict] = []
    active: list[int] = []  # 失効していない分配日のインデックス

    for i in range(n):
        ret = None
        is_dist = False
        if i >= 1 and close[i - 1] not in (None, 0):
            ret = (close[i] / close[i - 1] - 1.0) * 100.0
            vol_up = (volume[i] is not None and volume[i - 1] is not None
                      and volume[i] > volume[i - 1])
            is_dist = (ret <= -c["decline_pct"]) and vol_up

        # 失効処理: 窓超過 or +X% 上昇
        active = [j for j in active
                  if (i - j) < c["window"] and close[i] < close[j] * (1.0 + c["expire_gain_pct"] / 100.0)]
        if is_dist:
            active.append(i)

        count = len(active)
        out.append({
            "date": dates[i],
            "close": close[i],
            "volume": volume[i],
            "ret_pct": ret,
            "is_distribution": is_dist,
            "active_count": count,
            "caution": count >= c["count_threshold"],
        })
    return out


def latest_state(records: list[dict]) -> dict:
    """最新日の警戒状態（通知判定用）。"""
    if not records:
        return {"active_count": 0, "caution": False, "date": None}
    last = records[-1]
    return {
        "active_count": last["active_count"],
        "caution": last["caution"],
        "date": last["date"],
    }
