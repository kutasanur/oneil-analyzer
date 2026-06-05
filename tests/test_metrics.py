"""metrics.py のユニットテスト（モックデータ・決定論）。

意図(WHY)を検証する:
- 決算短信の累計開示から「単四半期」を正しく復元できること
- 前年同期比が同一CurPerTypeで対前年度比較され、前年≤0ならNoneになること
- 通期EPS YoY / 2年CAGR / ROE / 自己資本比率 / 時価総額 が定義どおり
- RSレーティングが全銘柄パーセンタイル(1-99)であること
"""
import numpy as np
import pandas as pd

import metrics
from metrics import (
    Stmt, normalize_statement, single_quarter_series, _quarterly_yoy,
    yoy, cagr, annual_fy, price_features, weighted_perf,
    compute_stock_metrics, finalize,
)


def mk(period_type, fy_end, period_end, eps=None, sales=None, np_=None,
       ta=None, eq=None, disclosed=None, shares=None):
    return Stmt(disclosed=disclosed or period_end, period_type=period_type,
                fy_end=fy_end, period_end=period_end, sales=sales, np=np_,
                eps=eps, ta=ta, eq=eq, bps=None, shares_net=shares)


# ---- 単四半期の復元 ----

def test_single_quarter_reconstruction_from_cumulative():
    # 累計 1Q=10, 2Q=25, 3Q=45, FY=80 → 単四半期 10,15,20,35
    stmts = [
        mk("1Q", "2025-03-31", "2024-06-30", eps=10),
        mk("2Q", "2025-03-31", "2024-09-30", eps=25),
        mk("3Q", "2025-03-31", "2024-12-31", eps=45),
        mk("FY", "2025-03-31", "2025-03-31", eps=80),
    ]
    s = single_quarter_series(stmts, "eps")
    assert [round(x["value"], 6) for x in s] == [10, 15, 20, 35]


def test_single_quarter_semiannual_company():
    # 半期開示（2QとFYのみ）: 2Q=単=上期累計40, FY=120 → H2=80
    stmts = [
        mk("2Q", "2025-03-31", "2024-09-30", eps=40),
        mk("FY", "2025-03-31", "2025-03-31", eps=120),
    ]
    s = single_quarter_series(stmts, "eps")
    assert [x["value"] for x in s] == [40, 80]


# ---- 四半期YoY（同一CurPerTypeで対前年度） ----

def test_quarterly_yoy_q0_and_q1():
    stmts = [
        # 前年度 FY2024
        mk("1Q", "2024-03-31", "2023-06-30", eps=10),
        mk("2Q", "2024-03-31", "2023-09-30", eps=20),  # 単=10
        # 当年度 FY2025
        mk("1Q", "2025-03-31", "2024-06-30", eps=12),         # 単=12 → vs 10 = +20%
        mk("2Q", "2025-03-31", "2024-09-30", eps=12 + 13),    # 単=13 → vs 10 = +30%
    ]
    series = single_quarter_series(stmts, "eps")
    # 最新(Q0)=2025 2Q 単=13 → +30%、Q1=2025 1Q 単=12 → +20%
    assert round(_quarterly_yoy(series, 0), 1) == 30.0
    assert round(_quarterly_yoy(series, 1), 1) == 20.0


def test_quarterly_yoy_none_when_prior_nonpositive():
    stmts = [
        mk("1Q", "2024-03-31", "2023-06-30", eps=-5),  # 前年同期が赤字
        mk("1Q", "2025-03-31", "2024-06-30", eps=10),
    ]
    series = single_quarter_series(stmts, "eps")
    assert _quarterly_yoy(series, 0) is None


# ---- 通期 ----

def test_annual_eps_yoy_and_cagr():
    assert round(yoy(150, 100), 1) == 50.0
    assert yoy(150, 0) is None          # 前年0以下→None
    assert yoy(150, -10) is None
    # 2年CAGR: 100→144 で2年 → 20%
    assert round(cagr(144, 100, 2), 1) == 20.0
    assert cagr(144, -1, 2) is None


def test_annual_fy_sorted():
    stmts = [
        mk("FY", "2025-03-31", "2025-03-31", eps=3),
        mk("FY", "2023-03-31", "2023-03-31", eps=1),
        mk("FY", "2024-03-31", "2024-03-31", eps=2),
    ]
    fy = annual_fy(stmts)
    assert [s.eps for s in fy] == [1, 2, 3]


# ---- 正規化（V2キー） ----

def test_normalize_statement_v2_keys():
    raw = {
        "Code": "12345", "DiscDate": "2025-05-10", "CurPerType": "1Q",
        "CurFYEn": "2026-03-31", "CurPerEn": "2025-06-30",
        "Sales": "1000", "NP": "120", "EPS": "12.5", "TA": "5000",
        "Eq": "3000", "BPS": "300", "ShOutFY": "10000", "TrShFY": "1000",
    }
    s = normalize_statement(raw)
    assert s.period_type == "1Q"
    assert s.eps == 12.5
    assert s.np == 120.0
    assert s.shares_net == 9000           # 10000 - 1000
    assert s.eq == 3000.0


def test_normalize_handles_blank_and_fullwidth_dash():
    raw = {"Code": "1", "CurPerType": "FY", "EPS": "－", "NP": "", "ShOutFY": "100", "TrShFY": "－"}
    s = normalize_statement(raw)
    assert s.eps is None and s.np is None
    assert s.shares_net == 100            # 自己株式が'－'→0扱い


# ---- 価格系 ----

def test_price_features_basic():
    close = np.linspace(100, 200, 260)        # 単調増加
    volume = np.concatenate([np.full(255, 100.0), np.full(5, 300.0)])
    f = price_features(close, volume)
    assert f["last_close"] == 200.0
    assert f["high_52w"] == 200.0
    assert abs(f["pct_from_high"]) < 1e-9     # 高値=現値
    assert f["vol_ratio"] == 3.0              # 直近5日300 / 基準20日100


def test_weighted_perf_positive_for_uptrend():
    close = np.linspace(100, 200, 260)
    assert weighted_perf(close) > 0


# ---- 横断: finalize ----

def test_compute_stock_metrics_keys_present_even_with_short_history():
    # 価格履歴が60日未満でも、価格系の列は必ず存在する（DataFrame列欠落でto_sqlが落ちないこと）
    stmts = [mk("FY", "2025-03-31", "2025-03-31", eps=10, np_=100, eq=1000, ta=2000, shares=1000)]
    close = np.array([100.0, 101, 102, 103, 104])  # <60
    vol = np.array([1.0, 1, 1, 1, 1])
    m = compute_stock_metrics(stmts, close, vol)
    for k in ("last_close", "ma200", "high_52w", "pct_from_high", "vol_ratio",
              "weighted_perf", "market_cap", "roe", "equity_ratio"):
        assert k in m, f"key {k} missing"


def test_finalize_rs_rating_and_pass():
    # 3銘柄: weighted_perf 大きいほど rs_rating 高い
    rows = [
        dict(code="A", q0_eps_yoy=120, q1_eps_yoy=90, q0_sales_yoy=40,
             annual_eps_yoy=60, cagr_2y=50, roe=25, equity_ratio=70,
             market_cap=5e10, weighted_perf=0.9, last_close=1, ma200=1,
             high_52w=1, pct_from_high=0, vol_ratio=1.5),
        dict(code="B", q0_eps_yoy=10, q1_eps_yoy=5, q0_sales_yoy=5,
             annual_eps_yoy=5, cagr_2y=3, roe=8, equity_ratio=40,
             market_cap=1e10, weighted_perf=0.1, last_close=1, ma200=1,
             high_52w=1, pct_from_high=0, vol_ratio=1.0),
        dict(code="C", q0_eps_yoy=80, q1_eps_yoy=60, q0_sales_yoy=30,
             annual_eps_yoy=40, cagr_2y=35, roe=20, equity_ratio=55,
             market_cap=3e10, weighted_perf=0.5, last_close=1, ma200=1,
             high_52w=1, pct_from_high=0, vol_ratio=1.2),
    ]
    df = finalize(pd.DataFrame(rows))
    # RSレーティングは1-99
    assert df["rs_rating"].max() <= 99 and df["rs_rating"].min() >= 1
    a = df[df["code"] == "A"].iloc[0]
    b = df[df["code"] == "B"].iloc[0]
    assert a["rs_rating"] > b["rs_rating"]
    # B は閾値(ROE17/Q0 25等)を満たさず不合格、A は合格
    assert bool(a["pass"]) is True
    assert bool(b["pass"]) is False
    # スコアは合格銘柄で最大、Aが最上位
    assert df.iloc[0]["code"] == "A"
