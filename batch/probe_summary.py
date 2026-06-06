"""Lightの /fins/summary が実際に返す全フィールドを確認（ネットネットに使える科目があるか）。"""
import os, re
import requests
H = {"x-api-key": os.environ.get("JQUANTS_API_KEY", "").strip()}
r = requests.get("https://api.jquants.com/v2/fins/summary",
                 headers=H, params={"code": "72030"}, timeout=60)
print("status:", r.status_code)
if r.status_code == 200:
    data = r.json().get("data") or []
    print("rows:", len(data))
    if data:
        last = data[-1]
        keys = sorted(last.keys())
        print("総フィールド数:", len(keys))
        print("--- 全フィールド ---")
        for k in keys:
            print(f"  {k} = {str(last[k])[:36]}")
        pat = re.compile(r"cash|deposit|securit|receivable|liabilit|current|inventor", re.I)
        print("--- ネットネット関連の有無 ---")
        hits = [k for k in keys if pat.search(k)]
        print("  該当:", hits if hits else "なし")
else:
    print(r.text[:200])
