"""
オニール（CAN-SLIM）成長株スクリーニングの指標計算。

すべて純関数で構成し、J-Quants鍵なしでモックデータからユニットテスト可能にする。
（既存 momentum_screener/scoring.py・filters.py の発想を、決算短信の累計開示に合わせて拡張）

要点:
- 日本の決算短信は「期首からの累計」で開示される（1Q=Q1, 2Q=上期累計, 3Q=9ヶ月累計, FY=通期）。
  そこで **単四半期 = 当期累計 − 同一会計年度の前期累計** で復元する。
- YoYは「同じ CurPerType を1会計年度前と比較」する（半期開示の会社でも整合する）。
- 前年同期が0以下のYoYは無意味なので None（除外）。momentum_screener と同じ符号配慮。

出力する主な指標（画像3〜5の列に対応）:
  q0_eps_yoy   最新単四半期EPSの前年同期比         （C: 閾値+25%）
  q1_eps_yoy   1つ前の単四半期EPSの前年同期比       （加速確認）
  q0_sales_yoy 最新単四半期売上の前年同期比
  cagr_2y      通期EPSの2年CAGR                    （A）
  annual_eps_yoy 直近通期EPS前年比                  （A）
  roe          通期 当期純利益 / 自己資本 ×100      （閾値+17%）
  equity_ratio 自己資本 / 総資産 ×100
  market_cap   終値 × （発行株数−自己株式）
  rs_rating    価格モメンタムの全銘柄パーセンタイル(1-99) （L: 閾値85）
  last_close, ma200, high_52w, pct_from_high, vol_ratio
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

import numpy as np
import pandas as pd

from jquants import pick, to_float, to_int

# 会計期間の並び順（1Q<2Q<3Q<FY）
_PERIOD_ORDER = {
    "1Q": 1, "2Q": 2, "3Q": 3, "FY": 4, "4Q": 4,
    "Q1": 1, "Q2": 2, "Q3": 3, "Q4": 4, "Annual": 4,
}

# ---- 既定の閾値・重み（画像のサイドバーと同じ）。ブラウザ側でも上書き可 ----
DEFAULTS = {
    "th_q0_eps": 25.0,      # Q0 EPS% >=
    "th_q1_eps": 25.0,      # Q1 EPS% >=
    "th_q0_sales": 25.0,    # Q0 売上% >=
    "th_annual_eps": 25.0,  # 年間EPS% >=
    "th_roe": 17.0,         # ROE% >=
    "th_rs": 85.0,          # RS >=
    "w_q0": 0.5, "w_q1": 0.3, "w_2y": 0.2,  # ランキング重み
}


# ---------------------------------------------------------------------------
# 正規化: J-Quants /fins/summary の生dict → Stmt
# ---------------------------------------------------------------------------

@dataclass
class Stmt:
    disclosed: str            # DiscDate
    period_type: str          # CurPerType: 1Q/2Q/3Q/FY
    fy_end: str               # CurFYEn（会計年度の識別キー）
    period_end: str           # CurPerEn（時系列ソート用）
    sales: float | None
    np: float | None          # 当期純利益
    eps: float | None
    ta: float | None
    eq: float | None
    bps: float | None
    shares_net: int | None    # ShOutFY − TrShFY


def normalize_statement(raw: dict) -> Stmt | None:
    code = pick(raw, "Code", "LocalCode", "code")
    if not code:
        return None
    out = to_int(pick(raw, "ShOutFY",
                      "NumberOfIssuedAndOutstandingSharesAtTheEndOfFiscalYearIncludingTreasuryStock"))
    tr = to_int(pick(raw, "TrShFY", "NumberOfTreasuryStockAtTheEndOfFiscalYear")) or 0
    shares_net = (out - tr) if out is not None else None
    pt = str(pick(raw, "CurPerType", "TypeOfCurrentPeriod", default="") or "").strip()
    return Stmt(
        disclosed=str(pick(raw, "DiscDate", "DisclosedDate", default="") or ""),
        period_type=pt,
        fy_end=str(pick(raw, "CurFYEn", "CurrentFiscalYearEndDate", default="") or ""),
        period_end=str(pick(raw, "CurPerEn", "CurrentPeriodEndDate", default="") or ""),
        sales=to_float(pick(raw, "Sales", "NetSales")),
        np=to_float(pick(raw, "NP", "Profit")),
        eps=to_float(pick(raw, "EPS", "EarningsPerShare")),
        ta=to_float(pick(raw, "TA", "TotalAssets")),
        eq=to_float(pick(raw, "Eq", "Equity")),
        bps=to_float(pick(raw, "BPS", "BookValuePerShare")),
        shares_net=(shares_net if (shares_net or 0) > 0 else None),
    )


# ---------------------------------------------------------------------------
# YoY（符号配慮: 前年同期が0以下なら None）
# ---------------------------------------------------------------------------

def yoy(cur: float | None, prev: float | None) -> float | None:
    if cur is None or prev is None:
        return None
    if prev <= 0:
        return None
    return (cur - prev) / prev * 100.0


# ---------------------------------------------------------------------------
# 単四半期の復元（field in {'eps','sales','np'}）
# ---------------------------------------------------------------------------

def single_quarter_series(stmts: list[Stmt], field: str) -> list[dict]:
    """
    各会計年度内で「単四半期値 = 当期累計 − 前期累計」を復元し、
    時系列（period_end昇順）で並べたレコード列を返す。
    各レコード: {fy_end, period_type, value, period_end, disclosed}
    """
    fy_map: dict[str, dict[str, Stmt]] = defaultdict(dict)
    for s in stmts:
        if s.period_type in _PERIOD_ORDER and s.fy_end:
            fy_map[s.fy_end][s.period_type] = s

    out: list[dict] = []
    for fy_end, pmap in fy_map.items():
        ordered = sorted(pmap.values(), key=lambda s: _PERIOD_ORDER[s.period_type])
        prev_cum = 0.0  # 期首=0からの累計
        for s in ordered:
            cum = getattr(s, field)
            if cum is None or prev_cum is None:
                single = None
            else:
                single = cum - prev_cum
            out.append({
                "fy_end": fy_end,
                "period_type": s.period_type,
                "value": single,
                "period_end": s.period_end,
                "disclosed": s.disclosed,
            })
            if cum is not None:
                prev_cum = cum
    out.sort(key=lambda d: (d["period_end"] or d["disclosed"] or ""))
    return out


def _quarterly_yoy(series: list[dict], back: int = 0) -> float | None:
    """
    series の末尾から back 個前の単四半期について、同じ period_type を1会計年度前と比較。
    back=0 → Q0（最新）, back=1 → Q1。
    """
    if len(series) < 1 + back:
        return None
    target = series[-1 - back]
    if target["value"] is None:
        return None
    # 会計年度の並び（昇順）
    fy_sorted = sorted({d["fy_end"] for d in series})
    try:
        idx = fy_sorted.index(target["fy_end"])
    except ValueError:
        return None
    if idx == 0:
        return None
    prev_fy = fy_sorted[idx - 1]
    yago = next((d["value"] for d in series
                 if d["fy_end"] == prev_fy and d["period_type"] == target["period_type"]), None)
    return yoy(target["value"], yago)


# ---------------------------------------------------------------------------
# 通期系（EPS YoY / 2年CAGR / ROE）
# ---------------------------------------------------------------------------

def annual_fy(stmts: list[Stmt]) -> list[Stmt]:
    fy = [s for s in stmts if s.period_type in ("FY", "4Q", "Annual")]
    fy.sort(key=lambda s: (s.fy_end or s.period_end or s.disclosed or ""))
    return fy


def cagr(latest: float | None, base: float | None, years: int) -> float | None:
    if latest is None or base is None or latest <= 0 or base <= 0 or years <= 0:
        return None
    return ((latest / base) ** (1.0 / years) - 1.0) * 100.0


# ---------------------------------------------------------------------------
# 価格系の特徴量（momentum_screener/filters.py を踏襲）
# ---------------------------------------------------------------------------

def price_features(close: np.ndarray, volume: np.ndarray) -> dict:
    close = np.asarray(close, dtype=float)
    close = close[~np.isnan(close)]
    if close.size < 60:
        return {}
    last = float(close[-1])
    ma200 = float(close[-200:].mean()) if close.size >= 60 else np.nan
    high_52w = float(close[-252:].max())
    pct_from_high = last / high_52w - 1.0 if high_52w else np.nan
    feats = {
        "last_close": last,
        "ma200": ma200,
        "high_52w": high_52w,
        "pct_from_high": pct_from_high,
        "vol_ratio": _vol_ratio(np.asarray(volume, dtype=float)),
    }
    return feats


def _vol_ratio(volume: np.ndarray, recent: int = 5, baseline: int = 20) -> float:
    vol = volume[~np.isnan(volume)]
    if vol.size < recent + baseline:
        return float("nan")
    r = vol[-recent:].mean()
    b = vol[-(recent + baseline):-recent].mean()
    if b <= 0:
        return float("nan")
    return float(r / b)


def weighted_perf(close: np.ndarray) -> float | None:
    """IBD流の重み付き相対力スコア（2×3M + 6M + 9M + 12M リターンの加重平均）。"""
    close = np.asarray(close, dtype=float)
    close = close[~np.isnan(close)]
    n = close.size

    def ret(days: int) -> float | None:
        if n > days and close[-1 - days] > 0:
            return close[-1] / close[-1 - days] - 1.0
        return None

    parts = [(2.0, ret(63)), (1.0, ret(126)), (1.0, ret(189)), (1.0, ret(252))]
    avail = [(w, p) for w, p in parts if p is not None]
    if not avail:
        return None
    wsum = sum(w for w, _ in avail)
    return sum(w * p for w, p in avail) / wsum


# ---------------------------------------------------------------------------
# 1銘柄分の基礎指標
# ---------------------------------------------------------------------------

def compute_stock_metrics(stmts: list[Stmt], close: np.ndarray, volume: np.ndarray) -> dict:
    eps_series = single_quarter_series(stmts, "eps")
    sales_series = single_quarter_series(stmts, "sales")

    q0_eps = _quarterly_yoy(eps_series, 0)
    q1_eps = _quarterly_yoy(eps_series, 1)
    q0_sales = _quarterly_yoy(sales_series, 0)

    fy = annual_fy(stmts)
    annual_eps_yoy = yoy(fy[-1].eps, fy[-2].eps) if len(fy) >= 2 else None
    cagr_2y = cagr(fy[-1].eps, fy[-3].eps, 2) if len(fy) >= 3 else None
    roe = (fy[-1].np / fy[-1].eq * 100.0) if (fy and fy[-1].np is not None
                                              and fy[-1].eq not in (None, 0)) else None

    latest = max(stmts, key=lambda s: (s.disclosed or s.period_end or "")) if stmts else None
    equity_ratio = (latest.eq / latest.ta * 100.0) if (latest and latest.eq is not None
                                                       and latest.ta not in (None, 0)) else None
    shares_net = next((s.shares_net for s in sorted(
        stmts, key=lambda s: (s.disclosed or ""), reverse=True) if s.shares_net), None)

    feats = price_features(close, volume)
    last_close = feats.get("last_close")
    market_cap = (shares_net * last_close) if (shares_net and last_close) else None

    return {
        "q0_eps_yoy": q0_eps,
        "q1_eps_yoy": q1_eps,
        "q0_sales_yoy": q0_sales,
        "annual_eps_yoy": annual_eps_yoy,
        "cagr_2y": cagr_2y,
        "roe": roe,
        "equity_ratio": equity_ratio,
        "shares_net": shares_net,
        "market_cap": market_cap,
        "weighted_perf": weighted_perf(close),
        **feats,
    }


# ---------------------------------------------------------------------------
# 横断処理: RSレーティング(1-99) と SCORE・合否
# ---------------------------------------------------------------------------

def finalize(df: pd.DataFrame, cfg: dict | None = None) -> pd.DataFrame:
    """
    1行=1銘柄の基礎指標DataFrameに、横断的な rs_rating と score・pass を付与。
    rs_rating: weighted_perf の全銘柄パーセンタイル → 1〜99。
    score: 生存銘柄内での Q0/Q1/2Y のパーセンタイル加重 ×100。
    """
    cfg = {**DEFAULTS, **(cfg or {})}
    df = df.copy()

    # RSレーティング（1-99）
    wp = df["weighted_perf"]
    rank = wp.rank(pct=True)  # 0..1（NaNはNaN）
    df["rs_rating"] = (rank * 98 + 1).round().astype("Int64")

    # ハードフィルタ（画像のサイドバー閾値）
    def ge(col, th):
        return df[col].fillna(-1e9) >= th
    df["pass"] = (
        ge("q0_eps_yoy", cfg["th_q0_eps"])
        & ge("q1_eps_yoy", cfg["th_q1_eps"])
        & ge("q0_sales_yoy", cfg["th_q0_sales"])
        & ge("annual_eps_yoy", cfg["th_annual_eps"])
        & ge("roe", cfg["th_roe"])
        & (df["rs_rating"].fillna(0).astype(float) >= cfg["th_rs"])
    )

    # SCORE: 生存集合内での成長率パーセンタイルを重み付け（×100）
    surv = df[df["pass"]]
    for col in ("q0_eps_yoy", "q1_eps_yoy", "cagr_2y"):
        pcol = f"_p_{col}"
        df[pcol] = np.nan
        if len(surv):
            df.loc[surv.index, pcol] = surv[col].rank(pct=True)
    df["score"] = (
        cfg["w_q0"] * df["_p_q0_eps_yoy"].fillna(0)
        + cfg["w_q1"] * df["_p_q1_eps_yoy"].fillna(0)
        + cfg["w_2y"] * df["_p_cagr_2y"].fillna(0)
    ) * 100.0
    df.loc[~df["pass"], "score"] = np.nan
    df = df.drop(columns=[c for c in df.columns if c.startswith("_p_")])
    return df.sort_values("score", ascending=False, na_position="last").reset_index(drop=True)
