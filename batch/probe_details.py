"""
/fins/details（BS/PL/CF明細）がLightで使えるか＆ネットネットに必要な科目があるかを診断。
GitHub Actionsで実行（鍵あり）。失敗してもステータスを表示する。
"""
import os, re, json
import datetime as dt
import requests

BASE = "https://api.jquants.com/v2"
H = {"x-api-key": os.environ.get("JQUANTS_API_KEY", "").strip()}
today = dt.date.today()


def bday(n):
    d = today - dt.timedelta(days=n)
    while d.weekday() >= 5:
        d -= dt.timedelta(days=1)
    return d.isoformat()


def get(path, params):
    r = requests.get(BASE + path, headers=H, params=params, timeout=60)
    return r


print("== A. /fins/details?code=72030 (トヨタ) ==")
r = get("/fins/details", {"code": "72030"})
print("status:", r.status_code)
if r.status_code == 200:
    js = r.json()
    data = js.get("data") or []
    print("rows:", len(data))
    if data:
        last = data[-1]
        keys = list(last.keys())
        print("総キー数:", len(keys))
        pat = re.compile(r"cash|deposit|securit|receivable|liabilit|inventor|asset|equity|current", re.I)
        hits = [k for k in keys if pat.search(k)]
        print("--- ネットネット関連の候補キー ---")
        for k in hits:
            v = last.get(k)
            print(f"  {k} = {str(v)[:40]}")
        print("--- 期間情報キー ---")
        for k in keys:
            if re.search(r"date|period|type|code", k, re.I):
                print(f"  {k} = {str(last.get(k))[:30]}")
else:
    print("body:", r.text[:200])

print("\n== B. /fins/details?date= (日付指定・全銘柄が取れるか) ==")
r2 = get("/fins/details", {"date": bday(20)})
print("status:", r2.status_code, " rows:",
      len((r2.json().get("data") or [])) if r2.status_code == 200 else "-")
if r2.status_code != 200:
    print("body:", r2.text[:200])
