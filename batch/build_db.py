"""
J-Quants Light → SQLite(oneil.db) 増分ビルダー。

ブログの方針を踏襲:
- 「DB内の最新日より後」だけを取得する増分更新（CI失敗による欠損を防ぐ）。
- メタ情報(meta)の日付更新と確定は最後に行う。
- 価格は直近 PRICES_KEEP_DAYS 営業日に制限してファイルを軽く保つ。

使い方:
  python build_db.py                 # 既存DBを増分更新（無ければ初回フルビルド）
  python build_db.py --max-codes 60  # 小ユニバースでスモークテスト
  python build_db.py --days-prices 30 --days-stmts 120   # 取得範囲の手動指定
出力: oneil.db（同ディレクトリ）。CIでgzipしてPagesに同梱する。
"""

from __future__ import annotations

import argparse
import datetime as dt
import gzip
import logging
import shutil
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

import jquants as jq
import metrics as M
import distribution as D

logger = logging.getLogger("build_db")

DB_PATH = Path(__file__).resolve().parent / "oneil.db"

PRICES_KEEP_DAYS = 400        # 価格を保持する営業日数（200MA+52週高値+RSに十分）
INIT_PRICE_CAL_DAYS = 600     # 初回ビルドで遡る価格の暦日数
INIT_STMT_CAL_DAYS = 1300     # 初回ビルドで遡る財務開示の暦日数（FY3期+四半期）

# ETF/REIT/優先株などを除外する業種コード（bullflag_screener.py と同じ方針）
INVALID_SECTOR_CODES = {"-", "0000", "", "None", "nan", "9999"}


# ---------------------------------------------------------------------------
# スキーマ
# ---------------------------------------------------------------------------

DDL = """
CREATE TABLE IF NOT EXISTS companies(
  code TEXT PRIMARY KEY, code4 TEXT, name TEXT, market TEXT, sector TEXT
);
CREATE TABLE IF NOT EXISTS prices(
  code TEXT, date TEXT, open REAL, high REAL, low REAL, close REAL, volume REAL,
  PRIMARY KEY(code, date)
);
CREATE TABLE IF NOT EXISTS statements(
  code TEXT, disclosed TEXT, period_type TEXT, fy_end TEXT, period_end TEXT,
  sales REAL, np REAL, eps REAL, ta REAL, eq REAL, bps REAL, shares_net INTEGER,
  PRIMARY KEY(code, disclosed, period_type, fy_end)
);
CREATE TABLE IF NOT EXISTS index_prices(
  date TEXT PRIMARY KEY, open REAL, high REAL, low REAL, close REAL, volume REAL
);
CREATE TABLE IF NOT EXISTS metrics(
  code TEXT PRIMARY KEY, code4 TEXT, name TEXT, sector TEXT, market TEXT, asof TEXT,
  q0_eps_yoy REAL, q1_eps_yoy REAL, q0_sales_yoy REAL, cagr_2y REAL,
  annual_eps_yoy REAL, roe REAL, equity_ratio REAL, market_cap REAL,
  rs_rating INTEGER, last_close REAL, pct_from_high REAL, vol_ratio REAL,
  score REAL, pass INTEGER
);
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE INDEX IF NOT EXISTS idx_prices_date ON prices(date);
CREATE INDEX IF NOT EXISTS idx_stmt_code ON statements(code);
"""


def connect(db_path: Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.executescript(DDL)
    return conn


def get_meta(conn, key, default=None):
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def set_meta(conn, key, value):
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES(?,?)", (key, str(value)))


def code4_of(code: str) -> str:
    """5桁コード(4桁+チェック桁0)を表示用4桁に。"""
    code = str(code)
    if len(code) == 5 and code.endswith("0"):
        return code[:4]
    return code


def business_days(start: dt.date, end: dt.date) -> list[dt.date]:
    out, d = [], start
    while d <= end:
        if d.weekday() < 5:
            out.append(d)
        d += dt.timedelta(days=1)
    return out


# ---------------------------------------------------------------------------
# 取得・投入
# ---------------------------------------------------------------------------

def refresh_companies(conn, max_codes: int | None = None) -> set[str]:
    rows = jq.fetch_equities_master()
    valid: set[str] = set()
    upserts = []
    for r in rows:
        code = str(jq.pick(r, "Code", "code", default=""))
        if not code:
            continue
        sector = str(jq.pick(r, "S33", "Sector33Code", default=""))
        if sector in INVALID_SECTOR_CODES:
            continue
        name = jq.pick(r, "CoName", "CompanyName", "Name", default="")
        market = jq.pick(r, "MktNm", "MarketCodeName", default="")
        sector_name = jq.pick(r, "S33Nm", "Sector33CodeName", default=sector)
        upserts.append((code, code4_of(code), name, market, sector_name))
        valid.add(code)
    if max_codes:
        valid = set(sorted(valid)[:max_codes])
        upserts = [u for u in upserts if u[0] in valid]
    conn.executemany(
        "INSERT OR REPLACE INTO companies(code,code4,name,market,sector) VALUES(?,?,?,?,?)",
        upserts)
    conn.commit()
    logger.info("companies: %d 銘柄（個別株）", len(valid))
    return valid


def ingest_prices_for_date(conn, date_str: str, valid: set[str]) -> float:
    rows = jq.fetch_daily_quotes(date_str)
    recs, total_vol = [], 0.0
    for r in rows:
        code = str(jq.pick(r, "Code", "code", default=""))
        if code not in valid:
            continue
        close = jq.to_float(jq.pick(r, "AdjC", "AdjustmentClose", "Close"))
        if close is None:
            continue
        vol = jq.to_float(jq.pick(r, "AdjVo", "AdjustmentVolume", "Volume")) or 0.0
        recs.append((
            code, date_str,
            jq.to_float(jq.pick(r, "AdjO", "AdjustmentOpen", "Open")),
            jq.to_float(jq.pick(r, "AdjH", "AdjustmentHigh", "High")),
            jq.to_float(jq.pick(r, "AdjL", "AdjustmentLow", "Low")),
            close, vol,
        ))
        total_vol += vol
    conn.executemany(
        "INSERT OR REPLACE INTO prices(code,date,open,high,low,close,volume) VALUES(?,?,?,?,?,?,?)",
        recs)
    return total_vol


def ingest_statements_for_date(conn, date_str: str, valid: set[str]) -> int:
    rows = jq.fetch_fins_summary(date_str)
    recs = []
    for r in rows:
        code = str(jq.pick(r, "Code", "LocalCode", "code", default=""))
        if code not in valid:
            continue
        s = M.normalize_statement(r)
        if not s or not s.period_type:
            continue
        recs.append((code, s.disclosed, s.period_type, s.fy_end, s.period_end,
                     s.sales, s.np, s.eps, s.ta, s.eq, s.bps, s.shares_net))
    conn.executemany(
        "INSERT OR REPLACE INTO statements"
        "(code,disclosed,period_type,fy_end,period_end,sales,np,eps,ta,eq,bps,shares_net)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", recs)
    return len(recs)


def ingest_topix(conn, from_date: str, to_date: str, vol_by_date: dict[str, float]):
    rows = jq.fetch_topix(from_date, to_date)
    recs = []
    for r in rows:
        date = str(jq.pick(r, "Date", "date", default=""))
        if not date:
            continue
        recs.append((
            date,
            jq.to_float(jq.pick(r, "O", "Open")),
            jq.to_float(jq.pick(r, "H", "High")),
            jq.to_float(jq.pick(r, "L", "Low")),
            jq.to_float(jq.pick(r, "C", "Close")),
            vol_by_date.get(date),   # TOPIX自体は出来高なし→市場全体出来高で代替
        ))
    conn.executemany(
        "INSERT OR REPLACE INTO index_prices(date,open,high,low,close,volume) VALUES(?,?,?,?,?,?)",
        recs)


def prune_prices(conn, keep_days: int = PRICES_KEEP_DAYS):
    dates = [r[0] for r in conn.execute(
        "SELECT DISTINCT date FROM prices ORDER BY date DESC LIMIT ?", (keep_days,))]
    if len(dates) >= keep_days and dates:
        cutoff = dates[-1]
        conn.execute("DELETE FROM prices WHERE date < ?", (cutoff,))


# ---------------------------------------------------------------------------
# 指標計算
# ---------------------------------------------------------------------------

def compute_and_store_metrics(conn, asof: str):
    companies = pd.read_sql("SELECT * FROM companies", conn).set_index("code")
    prices = pd.read_sql("SELECT code,date,close,volume FROM prices ORDER BY code,date", conn)
    stmt_rows = pd.read_sql("SELECT * FROM statements", conn)

    stmts_by_code: dict[str, list[M.Stmt]] = {}
    for code, g in stmt_rows.groupby("code"):
        lst = [M.Stmt(disclosed=r.disclosed, period_type=r.period_type, fy_end=r.fy_end,
                      period_end=r.period_end, sales=r.sales, np=r.np, eps=r.eps,
                      ta=r.ta, eq=r.eq, bps=r.bps, shares_net=r.shares_net)
               for r in g.itertuples()]
        stmts_by_code[str(code)] = lst

    out = []
    for code, g in prices.groupby("code"):
        code = str(code)
        if code not in companies.index:
            continue
        close = g["close"].to_numpy(dtype=float)
        volume = g["volume"].to_numpy(dtype=float)
        m = M.compute_stock_metrics(stmts_by_code.get(code, []), close, volume)
        c = companies.loc[code]
        out.append({"code": code, "code4": c.code4, "name": c["name"],
                    "sector": c.sector, "market": c.market, **m})

    if not out:
        logger.warning("metrics: 対象銘柄なし")
        return
    df = pd.DataFrame(out)
    df = M.finalize(df)
    df["asof"] = asof
    cols = ["code", "code4", "name", "sector", "market", "asof",
            "q0_eps_yoy", "q1_eps_yoy", "q0_sales_yoy", "cagr_2y", "annual_eps_yoy",
            "roe", "equity_ratio", "market_cap", "rs_rating", "last_close",
            "pct_from_high", "vol_ratio", "score", "pass"]
    df["pass"] = df["pass"].astype(int)
    conn.execute("DELETE FROM metrics")
    df[cols].to_sql("metrics", conn, if_exists="append", index=False)
    n_pass = int(df["pass"].sum())
    logger.info("metrics: %d 銘柄を計算（合格 %d）", len(df), n_pass)


def compute_distribution_state(conn) -> dict:
    idx = pd.read_sql("SELECT date,close,volume FROM index_prices ORDER BY date", conn)
    if idx.empty:
        return {"active_count": 0, "caution": False, "date": None}
    rec = D.analyze(idx["date"].tolist(),
                    idx["close"].tolist(),
                    idx["volume"].fillna(0).tolist())
    st = D.latest_state(rec)
    set_meta(conn, "dist_active_count", st["active_count"])
    set_meta(conn, "dist_caution", int(st["caution"]))
    set_meta(conn, "dist_date", st["date"])
    return st


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------

def main(args):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    conn = connect()
    today = dt.date.today()

    valid = refresh_companies(conn, args.max_codes)

    # 取得範囲（増分: DB最新日の翌日から。無ければ初回ルックバック）
    last_price = get_meta(conn, "last_price_date")
    last_stmt = get_meta(conn, "last_stmt_date")
    price_start = (dt.date.fromisoformat(last_price) + dt.timedelta(days=1)) if last_price \
        else today - dt.timedelta(days=args.days_prices or INIT_PRICE_CAL_DAYS)
    stmt_start = (dt.date.fromisoformat(last_stmt) + dt.timedelta(days=1)) if last_stmt \
        else today - dt.timedelta(days=args.days_stmts or INIT_STMT_CAL_DAYS)

    # 財務（広い範囲）
    stmt_days = business_days(stmt_start, today)
    logger.info("statements 取得: %s〜%s (%d営業日)", stmt_start, today, len(stmt_days))
    for d in stmt_days:
        ds = d.isoformat()
        n = ingest_statements_for_date(conn, ds, valid)
        if n:
            logger.info("  %s: statements %d件", ds, n)
    conn.commit()

    # 価格（直近範囲）＋市場全体出来高の集計
    price_days = business_days(price_start, today)
    logger.info("prices 取得: %s〜%s (%d営業日)", price_start, today, len(price_days))
    vol_by_date: dict[str, float] = {}
    last_price_done = last_price
    for d in price_days:
        ds = d.isoformat()
        tv = ingest_prices_for_date(conn, ds, valid)
        if tv > 0:
            vol_by_date[ds] = tv
            last_price_done = ds
    conn.commit()

    # TOPIX（価格と同じ範囲・市場出来高を流用）
    if price_days:
        ingest_topix(conn, price_start.isoformat(), today.isoformat(), vol_by_date)
        conn.commit()

    prune_prices(conn)
    conn.commit()

    # 指標と分配日
    asof = last_price_done or today.isoformat()
    compute_and_store_metrics(conn, asof)
    st = compute_distribution_state(conn)
    logger.info("分配日: active=%d caution=%s (%s)", st["active_count"], st["caution"], st["date"])

    # メタ確定（最後）
    if last_price_done:
        set_meta(conn, "last_price_date", last_price_done)
    if stmt_days:
        set_meta(conn, "last_stmt_date", today.isoformat())
    set_meta(conn, "asof", asof)
    set_meta(conn, "updated_at", dt.datetime.now().isoformat(timespec="seconds"))
    conn.commit()
    conn.close()

    # gzip 出力（Pages配信用）
    if args.gzip:
        with open(DB_PATH, "rb") as f_in, gzip.open(str(DB_PATH) + ".gz", "wb", compresslevel=6) as f_out:
            shutil.copyfileobj(f_in, f_out)
        logger.info("gzip 出力: %s.gz", DB_PATH)

    logger.info("完了: %s (asof=%s)", DB_PATH, asof)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--max-codes", type=int, default=None, help="ユニバースを先頭N銘柄に制限（スモークテスト用）")
    p.add_argument("--days-prices", type=int, default=None, help="価格の初回取得暦日数")
    p.add_argument("--days-stmts", type=int, default=None, help="財務の初回取得暦日数")
    p.add_argument("--gzip", action="store_true", help="oneil.db.gz も出力する")
    main(p.parse_args())
