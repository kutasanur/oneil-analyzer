"""
J-Quants 日次株価（全銘柄）を過去N年分まとめてダウンロードする一括バックフィルツール。

目的:
  Standardプラン（保存期間10年）が有効なうちに、過去の日次株価を確保しておく。
  ダウングレード(Light=5年)後でも、ローカルに残したCSVでバックテストができる。

出力:
  1営業日 = 1ファイル  quotes_YYYY-MM-DD.csv
  既存の JQuants_Data_5Years/quotes_*.csv と完全に同じカラム・形式。
  → 取得後にそのフォルダへマージすれば、そのまま既存ツールで読める。

特徴:
  - 再開可能: 出力先に既に存在する quotes_*.csv はスキップ（中断しても続きから）。
  - 土日は最初からスキップ（J-Quantsに非営業日データは無い）。
  - 祝日や保存期間外の日は API が空を返す → ファイルを作らず次へ。
  - レート制限対応: batch/jquants.py のスロットル＆指数バックオフをそのまま利用。

使い方（ローカル）:
  set JQUANTS_API_KEY=...   （環境変数。GitHub Actionsでは Secrets から渡る）
  python download_history.py --out ./history_out --years 10 --to 2024-12-22

引数:
  --out     出力ディレクトリ（既定: ./history_out）
  --years   今日から何年分さかのぼるか（既定: 10。Standardの保存期間）
  --from    取得開始日 YYYY-MM-DD（指定時は --years より優先）
  --to      取得終了日 YYYY-MM-DD（既定: 2024-12-22 = 既存データの直前まで＝ギャップのみ取得）
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import logging
import sys
from pathlib import Path

# 同リポジトリの実績ある J-Quants クライアントを再利用
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "batch"))
import jquants as jq  # noqa: E402

logger = logging.getLogger("backfill")

# 既存 quotes_*.csv と完全一致のカラム順
COLUMNS = ["Date", "Code", "O", "H", "L", "C", "UL", "LL",
           "Vo", "Va", "AdjFactor", "AdjO", "AdjH", "AdjL", "AdjC", "AdjVo"]

# float として書く列（既存CSVは価格・出来高が "4220.0" 形式）。
# Date/Code は文字列、UL/LL はフラグ整数のまま。
_FLOAT_COLS = {"O", "H", "L", "C", "Vo", "Va",
               "AdjFactor", "AdjO", "AdjH", "AdjL", "AdjC", "AdjVo"}


def _cell(row: dict, col: str):
    """1セルの値を既存CSV形式に整形して返す。"""
    v = row.get(col)
    if v is None or v == "":
        return ""
    if col in _FLOAT_COLS:
        f = jq.to_float(v)
        return "" if f is None else f
    return v


def write_day_csv(rows: list[dict], path: Path) -> int:
    """1営業日分の行を quotes_YYYY-MM-DD.csv として書き出す。書いた行数を返す。"""
    tmp = path.with_suffix(".csv.tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(COLUMNS)
        for r in rows:
            w.writerow([_cell(r, c) for c in COLUMNS])
    tmp.replace(path)  # 原子的に確定（途中失敗で半端なファイルを残さない）
    return len(rows)


def daterange(start: dt.date, end: dt.date):
    d = start
    while d <= end:
        yield d
        d += dt.timedelta(days=1)


def main(args):
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    today = dt.date.today()
    to_date = dt.date.fromisoformat(args.to) if args.to else today
    if args.from_:
        from_date = dt.date.fromisoformat(args.from_)
    else:
        from_date = today - dt.timedelta(days=int(round(args.years * 365.25)))

    logger.info("バックフィル範囲: %s 〜 %s（最大%.0f年分）", from_date, to_date, args.years)
    logger.info("出力先: %s", out.resolve())

    # キー存在チェック（早期に分かりやすく失敗させる）
    jq.get_api_key()

    n_written = n_skip_exist = n_empty = n_err = 0
    earliest_with_data: str | None = None
    total_rows = 0

    bdays = [d for d in daterange(from_date, to_date) if d.weekday() < 5]
    logger.info("対象営業日（土日除く）: %d 日", len(bdays))

    for i, d in enumerate(bdays, 1):
        ds = d.isoformat()
        path = out / f"quotes_{ds}.csv"
        if path.exists():
            n_skip_exist += 1
            continue
        try:
            rows = jq.fetch_daily_quotes(ds)
        except Exception as e:  # noqa: BLE001
            n_err += 1
            logger.warning("  %s: 取得失敗 → スキップ (%s)", ds, e)
            continue
        if not rows:
            n_empty += 1  # 祝日 or 保存期間外
            continue
        cnt = write_day_csv(rows, path)
        total_rows += cnt
        n_written += 1
        if earliest_with_data is None:
            earliest_with_data = ds
        if n_written % 50 == 0:
            logger.info("  進捗 %d/%d: %s まで書込（直近 %d 行）",
                        i, len(bdays), ds, cnt)

    logger.info("=" * 60)
    logger.info("完了: 新規書込 %d 日 / 既存スキップ %d 日 / 空(祝日等) %d 日 / 失敗 %d 日",
                n_written, n_skip_exist, n_empty, n_err)
    logger.info("取得できた最古の営業日: %s", earliest_with_data or "(なし)")
    logger.info("総レコード数: %s 行", f"{total_rows:,}")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="J-Quants 日次株価の過去N年一括ダウンロード")
    p.add_argument("--out", default="./history_out", help="出力ディレクトリ")
    p.add_argument("--years", type=float, default=10.0, help="今日から何年分さかのぼるか")
    p.add_argument("--from", dest="from_", default=None, help="開始日 YYYY-MM-DD（--yearsより優先）")
    p.add_argument("--to", default="2024-12-22", help="終了日 YYYY-MM-DD（既定=既存データ直前）")
    main(p.parse_args())
