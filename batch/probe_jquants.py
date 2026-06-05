"""
J-Quants Lightキーで、どのデータがどの範囲まで取得できるかを診断する。
GitHub Actions（鍵あり）で実行し、各リクエストのHTTPステータスと件数を出力する。
失敗してもクラッシュせずステータスを表示する。
"""
import os
import datetime as dt
import requests

BASE = "https://api.jquants.com/v2"
KEY = os.environ.get("JQUANTS_API_KEY", "").strip()
H = {"x-api-key": KEY}

today = dt.date.today()


def bday(days_ago: int) -> str:
    d = today - dt.timedelta(days=days_ago)
    while d.weekday() >= 5:
        d -= dt.timedelta(days=1)
    return d.isoformat()


def probe(label: str, path: str, params: dict):
    try:
        r = requests.get(BASE + path, headers=H, params=params, timeout=60)
        n = "?"
        try:
            js = r.json()
            for k in ("data", "topix", "statements"):
                if isinstance(js.get(k), list):
                    n = len(js[k]); break
        except Exception:
            n = "(json parse失敗)"
        msg = r.text[:120].replace("\n", " ") if r.status_code != 200 else ""
        print(f"  [{r.status_code}] {label}: rows={n} {msg}")
    except Exception as e:
        print(f"  [ERR] {label}: {e}")


print(f"今日={today} key設定={'あり' if KEY else 'なし'}")
print("== A. daily_quotes を『日付指定（全銘柄）』で ==")
for d in (3, 7, 30, 90, 120, 200, 400):
    probe(f"date={bday(d)} ({d}日前)", "/prices/daily_quotes", {"date": bday(d)})

print("== B. daily_quotes を『銘柄コード指定』で（トヨタ 7203/72030）==")
for code in ("72030", "7203"):
    probe(f"code={code} 直近40日", "/prices/daily_quotes",
          {"code": code, "from": bday(40), "to": bday(3)})
    probe(f"code={code} 150-200日前", "/prices/daily_quotes",
          {"code": code, "from": bday(200), "to": bday(150)})

print("== C. indices/topix（マーケット天井ページ用）==")
probe("topix 直近40日", "/indices/topix", {"from": bday(40), "to": bday(3)})
probe("topix 150-200日前", "/indices/topix", {"from": bday(200), "to": bday(150)})

print("== D. 参考: fins/summary（業績・前回OKだった）==")
probe(f"fins date={bday(20)}", "/fins/summary", {"date": bday(20)})
