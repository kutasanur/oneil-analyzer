"""
PBR (Price-to-Book Ratio) 履歴の計算・格納モジュール。

/fins/summary の BPS（1株純資産）+ 株価終値で PBR を計算する。
FY（通期）決算ごとの年次 PBR 推移を pbr_history テーブルに格納。

将来 /fins/details (Standard/Premium) が利用可能になれば
グレアム流 NCAV = 流動資産 × 割引係数 − 全負債 に拡張予定。
"""
from __future__ import annotations

import bisect
import logging
import sqlite3
from datetime import date

logger = logging.getLogger("netnet")

DDL_PBR = """
CREATE TABLE IF NOT EXISTS pbr_history(
    code        TEXT,
    fy_end      TEXT,
    disclosed   TEXT,
    bps         REAL,
    close       REAL,
    pbr         REAL,
    PRIMARY KEY (code, fy_end)
);
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(DDL_PBR)


def _nearest_price_date(target: str, sorted_dates: list[str], window: int = 7) -> str | None:
    """ソート済み価格日付リストから target に最も近い日付を返す（window 暦日以内）。
    target 以降の最初の営業日を優先し、なければ直前の最終営業日。
    開示日が土日祝の場合でも最寄り営業日の終値を取得できる。
    """
    if not sorted_dates:
        return None
    t = date.fromisoformat(target)
    idx = bisect.bisect_left(sorted_dates, target)
    best: str | None = None
    best_diff = window + 1

    # target 以降の最初の営業日（開示日翌営業日・引け後開示は翌日反映）
    if idx < len(sorted_dates):
        d = date.fromisoformat(sorted_dates[idx])
        diff = (d - t).days
        if 0 <= diff <= window:
            best = sorted_dates[idx]
            best_diff = diff

    # target 直前の最終営業日（開示日当日の引け前に出た場合）
    if idx > 0:
        d = date.fromisoformat(sorted_dates[idx - 1])
        diff = (t - d).days
        if diff <= window and diff < best_diff:
            best = sorted_dates[idx - 1]

    return best


def compute_and_store_pbr(conn: sqlite3.Connection) -> None:
    """
    FY（通期）決算の開示 BPS × 最寄り株価終値 で PBR 履歴を計算し、
    pbr_history テーブルに格納する。

    同一 fy_end に複数の開示（修正等）がある場合は最新開示を採用。
    BPS が NULL / 0 の行（業績予想修正などの空行）はスキップ。
    """
    ensure_schema(conn)
    conn.execute("DELETE FROM pbr_history")

    # FY × 年度ごとに最新開示の BPS を取得
    stmt_rows = conn.execute("""
        SELECT s.code, s.fy_end, s.disclosed, s.bps
        FROM statements s
        JOIN (
            SELECT code, fy_end, MAX(disclosed) AS max_disc
            FROM statements
            WHERE period_type = 'FY' AND bps IS NOT NULL AND bps > 0
            GROUP BY code, fy_end
        ) latest
          ON latest.code     = s.code
         AND latest.fy_end   = s.fy_end
         AND latest.max_disc = s.disclosed
        WHERE s.period_type = 'FY'
          AND s.bps IS NOT NULL AND s.bps > 0
        ORDER BY s.code, s.fy_end
    """).fetchall()

    prev_code: str | None = None
    price_dates: list[str] = []
    batch: list[tuple] = []
    inserted = 0

    for code, fy_end, disclosed, bps in stmt_rows:
        # 銘柄が変わったらバッチをフラッシュし、価格日付リストを再取得
        if code != prev_code:
            if batch:
                conn.executemany(
                    "INSERT OR REPLACE INTO pbr_history"
                    "(code,fy_end,disclosed,bps,close,pbr) VALUES(?,?,?,?,?,?)",
                    batch,
                )
                inserted += len(batch)
                batch = []
            price_dates = [
                r[0] for r in conn.execute(
                    "SELECT date FROM prices WHERE code=? ORDER BY date", (code,)
                )
            ]
            prev_code = code

        close_date = _nearest_price_date(disclosed, price_dates)
        if close_date is None:
            continue

        row = conn.execute(
            "SELECT close FROM prices WHERE code=? AND date=?",
            (code, close_date),
        ).fetchone()
        if not row or not row[0] or float(row[0]) <= 0:
            continue

        close = float(row[0])
        pbr = round(close / float(bps), 4)
        batch.append((code, fy_end, disclosed, float(bps), close, pbr))

    # 最後のバッチ
    if batch:
        conn.executemany(
            "INSERT OR REPLACE INTO pbr_history"
            "(code,fy_end,disclosed,bps,close,pbr) VALUES(?,?,?,?,?,?)",
            batch,
        )
        inserted += len(batch)

    conn.commit()
    logger.info("pbr_history: %d 件（FY通期）を計算・格納", inserted)
