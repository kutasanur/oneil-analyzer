"""
合成データで oneil.db / oneil.db.gz を生成（HTML表示の目視確認用）。
J-Quants鍵なしで、本番と同じ build_db.compute_* パイプラインを通す。
出力先: site/oneil.db.gz （ローカル配信で同一オリジンfetchできるように）。※.gitignore対象。
実行: python tests/make_sample_db.py
"""
import sys, math, gzip, shutil, datetime as dt
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "batch"))
import build_db as B  # noqa: E402

OUT_DB = Path(__file__).resolve().parents[1] / "site" / "oneil.db"


def bdays(n, end=dt.date(2026, 6, 5)):
    out, d = [], end
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= dt.timedelta(days=1)
    return list(reversed(out))


COMPANIES = [
    # (code5, name, momentum, growth)  momentum/growth: 0..1 強さ
    ("13010", "サンプル成長A", 1.00, 1.00),
    ("72030", "サンプル成長B", 0.85, 0.80),
    ("99840", "サンプル中位C", 0.55, 0.45),
    ("45550", "サンプル中位D", 0.45, 0.55),
    ("61460", "サンプル低位E", 0.25, 0.15),
    ("28900", "サンプル低位F", 0.10, 0.05),
]


def main():
    if OUT_DB.exists():
        OUT_DB.unlink()
    B.DB_PATH = OUT_DB
    conn = B.connect(OUT_DB)
    days = bdays(300)
    dstr = [d.isoformat() for d in days]

    vol_by_date = {ds: 0.0 for ds in dstr}
    for (code, name, mom, grow) in COMPANIES:
        conn.execute("INSERT OR REPLACE INTO companies VALUES(?,?,?,?,?)",
                     (code, B.code4_of(code), name, "プライム", "情報・通信業"))
        base = 1000.0
        total = 0.3 + 1.7 * mom          # 期間トータルリターン（0.3〜2.0倍上昇）
        for i, ds in enumerate(dstr):
            t = i / (len(dstr) - 1)
            price = base * (1 + total * t) * (1 + 0.02 * math.sin(i / 9.0))
            o = price * 0.995; h = price * 1.012; l = price * 0.988; c = price
            v = 1_000_000 * (0.8 + 0.4 * mom) * (1 + 0.3 * math.sin(i / 5.0))
            conn.execute("INSERT OR REPLACE INTO prices VALUES(?,?,?,?,?,?,?)",
                         (code, ds, o, h, l, c, v))
            vol_by_date[ds] += v

        # 財務: 3会計年度 ×（1Q/2Q/3Q/FY 累計）
        g = 0.05 + 0.45 * grow           # 年成長率 5%〜50%
        for yi, fy in enumerate(("2024-03-31", "2025-03-31", "2026-03-31")):
            scale = (1 + g) ** yi
            base_q_eps = 10.0 * scale
            base_q_sales = 1.0e9 * scale
            base_np = 8.0e8 * scale
            cum_eps = cum_sales = cum_np = 0.0
            for qi, pt in enumerate(("1Q", "2Q", "3Q", "FY")):
                # 単四半期は年内でやや増加（直近Qを強める→Q0 YoYを高める）
                q_eps = base_q_eps * (1 + 0.06 * qi)
                q_sales = base_q_sales * (1 + 0.05 * qi)
                q_np = base_np * (1 + 0.06 * qi)
                cum_eps += q_eps; cum_sales += q_sales; cum_np += q_np
                pend = {"1Q": f"{fy[:4]}-06-30", "2Q": f"{fy[:4]}-09-30",
                        "3Q": f"{fy[:4]}-12-31", "FY": fy}[pt]
                disclosed = (dt.date.fromisoformat(pend) + dt.timedelta(days=40)).isoformat()
                eq = 4.0e9 * scale; ta = eq / 0.6   # 自己資本比率60%
                conn.execute(
                    "INSERT OR REPLACE INTO statements VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (code, disclosed, pt, fy, pend, cum_sales, cum_np, cum_eps,
                     ta, eq, None, 50_000_000))

    # TOPIX: ゆるやか上昇＋直近に「下落×出来高増」を数本仕込んで分配日を発生させる
    topix = 2500.0
    for i, ds in enumerate(dstr):
        topix *= 1.0006
        # 直近15営業日のうち数日を下落させる
        k = len(dstr) - 1 - i
        if k in (1, 3, 5, 7, 9):
            close = topix * 0.992      # -0.8%
            vol = vol_by_date[ds] * 1.4
        else:
            close = topix
            vol = vol_by_date[ds]
        conn.execute("INSERT OR REPLACE INTO index_prices VALUES(?,?,?,?,?,?)",
                     (ds, close * 0.999, close * 1.003, close * 0.997, close, vol))
    conn.commit()

    asof = dstr[-1]
    B.compute_and_store_metrics(conn, asof)
    st = B.compute_distribution_state(conn)
    B.set_meta(conn, "asof", asof)
    B.set_meta(conn, "updated_at", dt.datetime.now().isoformat(timespec="seconds"))
    conn.commit()

    npass = conn.execute("SELECT COUNT(*) FROM metrics WHERE pass=1").fetchone()[0]
    nmet = conn.execute("SELECT COUNT(*) FROM metrics").fetchone()[0]
    conn.close()

    with open(OUT_DB, "rb") as a, gzip.open(str(OUT_DB) + ".gz", "wb") as b:
        shutil.copyfileobj(a, b)
    print(f"OK sample db: {nmet} metrics, {npass} pass, distribution={st}")
    print(f"  -> {OUT_DB}.gz")


if __name__ == "__main__":
    main()
