"""
新規ランクイン銘柄・天井警戒を GitHub Issue で通知する。

ブログの方針: 毎日解析ページを見るのは手間なので、日次バッチで「新しく条件を満たした銘柄」を
GitHub Issue にする → リポジトリ所有者にメールが届く。close すれば確認ログになる。

- 前回通知した上位集合は meta テーブル（notified_codes / notified_caution）に保存し差分を取る。
- Issue作成は gh CLI（GitHub Actions上で GH_TOKEN により認証済み）。
  ローカルや GH_TOKEN 未設定時は作成をスキップし、内容を表示するだけ（テスト可能）。

環境変数:
  GH_TOKEN            gh の認証（Actionsの github.token を渡す）
  GITHUB_REPOSITORY   "owner/repo"
  PAGES_URL           解析ページのベースURL（例: https://owner.github.io/oneil-analyzer）
"""

from __future__ import annotations

import json
import os
import subprocess
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "oneil.db"
TOP_N = 30


def new_entrants(today_codes: list[str], prev_codes: list[str]) -> list[str]:
    """前回の上位集合に居なかった新規銘柄（順序は today_codes を維持）。"""
    prev = set(prev_codes)
    return [c for c in today_codes if c not in prev]


def _get_meta(conn, key, default=None):
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def _set_meta(conn, key, value):
    conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)", (key, str(value)))


def _gh_issue(title: str, body: str) -> bool:
    """gh CLIでIssue作成。未認証ならFalse（表示のみ）。"""
    if not os.environ.get("GH_TOKEN") and not os.environ.get("GITHUB_TOKEN"):
        print("[notify] GH_TOKEN未設定のためIssue作成をスキップ。内容:")
        print("  TITLE:", title)
        print(body)
        return False
    try:
        subprocess.run(["gh", "issue", "create", "--title", title, "--body", body],
                       check=True)
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[notify] gh issue create 失敗: {e}")
        return False


def run(db_path: Path = DB_PATH):
    conn = sqlite3.connect(db_path)
    pages = os.environ.get("PAGES_URL", "").rstrip("/")
    asof = _get_meta(conn, "asof", "")

    # 今日の上位（合格・score降順）
    rows = conn.execute(
        "SELECT code4,name,score,q0_eps_yoy,q1_eps_yoy,cagr_2y,roe,rs_rating "
        "FROM metrics WHERE pass=1 ORDER BY score DESC LIMIT ?", (TOP_N,)).fetchall()
    today_codes = [r[0] for r in rows]
    prev_codes = json.loads(_get_meta(conn, "notified_codes", "[]"))
    fresh = new_entrants(today_codes, prev_codes)

    if fresh:
        info = {r[0]: r for r in rows}
        lines = ["| コード | 企業名 | SCORE | Q0 EPS% | Q1 EPS% | 2Y CAGR% | ROE% | RS |",
                 "|---|---|---|---|---|---|---|---|"]
        for c in fresh:
            r = info[c]
            link = f"[{c}]({pages}/index.html?code={c})" if pages else c
            lines.append(f"| {link} | {r[1] or ''} | {r[2]:.0f} | {r[3]:.0f} | {r[4]:.0f} "
                         f"| {r[5]:.0f} | {r[6]:.0f} | {r[7]} |")
        body = (f"基準日 **{asof}**：オニール条件を新たに満たした銘柄が {len(fresh)} 件あります。\n\n"
                + "\n".join(lines)
                + (f"\n\nランキング全体: {pages}/index.html" if pages else ""))
        _gh_issue(f"[オニール] 新規ランクイン {len(fresh)}件 ({asof})", body)

    _set_meta(conn, "notified_codes", json.dumps(today_codes, ensure_ascii=False))

    # 天井警戒（OFF→ON のとき通知）
    cur_caution = int(_get_meta(conn, "dist_caution", "0") or 0)
    prev_caution = int(_get_meta(conn, "notified_caution", "0") or 0)
    if cur_caution and not prev_caution:
        cnt = _get_meta(conn, "dist_active_count", "?")
        body = (f"基準日 **{asof}**：マーケットが天井警戒域に入りました（有効な分配日 {cnt} 本）。\n\n"
                f"新規買いは慎重に。詳細: {pages}/market.html" if pages else "")
        _gh_issue(f"[オニール] 天井警戒シグナル ({asof})", body)
    _set_meta(conn, "notified_caution", cur_caution)

    conn.commit()
    conn.close()
    print(f"[notify] 新規{len(fresh)}件 / 天井警戒={cur_caution}")


if __name__ == "__main__":
    run()
