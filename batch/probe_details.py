"""/fins/details（Standard）の全フィールドを確認し、ネットネットに使うBS科目名を特定する。"""
import os, re
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


r = requests.get(BASE + "/fins/details", headers=H, params={"code": "72030"}, timeout=60)
print("A) code=72030 status:", r.status_code)
if r.status_code == 200:
    data = r.json().get("data") or []
    print("rows:", len(data))
    if data:
        last = data[-1]
        keys = sorted(last.keys())
        print("総フィールド数:", len(keys))
        meta = [k for k in keys if re.search(r"date|period|type|code|^fy|standard|doctype", k, re.I)]
        print("--- メタ系 ---")
        for k in meta:
            print(f"  {k} = {str(last[k])[:30]}")
        pat = re.compile(r"cash|deposit|securit|receivable|notes|marketable|invest|liabilit|current|asset|inventor", re.I)
        print("--- BS候補（ネットネット用）---")
        for k in keys:
            if pat.search(k):
                print(f"  {k} = {str(last[k])[:40]}")
else:
    print("body:", r.text[:200])

r2 = requests.get(BASE + "/fins/details", headers=H, params={"date": bday(20)}, timeout=60)
print("\nB) date= status:", r2.status_code,
      "rows:", len((r2.json().get('data') or [])) if r2.status_code == 200 else r2.text[:120])
