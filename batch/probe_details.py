"""キーの有効性(summary=Light)とStandard権限(details)を切り分ける。"""
import os, re
import requests
BASE = "https://api.jquants.com/v2"
H = {"x-api-key": os.environ.get("JQUANTS_API_KEY", "").strip()}
key = os.environ.get("JQUANTS_API_KEY", "")
print("key 長さ:", len(key), " 末尾4:", key[-4:] if key else "(空)")

s = requests.get(BASE + "/fins/summary", headers=H, params={"code": "72030"}, timeout=60)
print("summary(Light) status:", s.status_code, "rows:",
      len((s.json().get('data') or [])) if s.status_code == 200 else s.text[:120])

d = requests.get(BASE + "/fins/details", headers=H, params={"code": "72030"}, timeout=60)
print("details(Standard) status:", d.status_code)
if d.status_code == 200:
    data = d.json().get("data") or []
    print("rows:", len(data))
    if data:
        last = data[-1]
        keys = sorted(last.keys())
        print("総フィールド数:", len(keys))
        pat = re.compile(r"cash|deposit|securit|receivable|notes|marketable|invest|liabilit|current|asset", re.I)
        for k in keys:
            if pat.search(k):
                print(f"  {k} = {str(last[k])[:40]}")
else:
    print("details body:", d.text[:160])
