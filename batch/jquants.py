"""
J-Quants V2 APIクライアント（オニール分析システム用）

- 認証: APIキー1枚（有効期限なし）。ヘッダー `x-api-key`。
  取得先: https://jpx-jquants.com/dashboard/api-keys
  鍵は環境変数 JQUANTS_API_KEY から読む（GitHub Secrets / ローカルの環境変数）。
- Lightプラン想定（過去5年・遅延なし・株価/財務/TOPIX/上場一覧すべて利用可）。
- 既存 bullflag_screener.py のV2パターンを踏襲（data キー + pagination_key、429リトライ、
  カラム名はV2略称をフォールバックリストで吸収）。

エンドポイント:
  GET /v2/equities/master                        上場銘柄一覧（銘柄名・業種・市場）
  GET /v2/equities/bars/daily?date=YYYY-MM-DD    日次株価四本値（指定日・全銘柄。code= も可）
  GET /v2/fins/summary?date=YYYY-MM-DD           財務サマリー（指定日開示・全銘柄）
  GET /v2/indices/bars/daily/topix?from=&to=     TOPIX指数の日次OHLC

  ※ V2では daily_quotes は /equities/bars/daily、TOPIXは /indices/bars/daily/topix に変更
    （旧 /prices/daily_quotes・/indices/topix はV2では 403「endpoint does not exist」になる）。
"""

from __future__ import annotations

import os
import time
import logging
from typing import Any, Iterable

import requests

logger = logging.getLogger("jquants")

JQ_BASE = "https://api.jquants.com/v2"
_TIMEOUT = 60
_MAX_PAGES = 200          # ページネーション安全弁
_MIN_INTERVAL = 0.4       # 全リクエスト間の最小間隔（秒）＝429回避の定常スロットル
_RETRY_429 = 8            # 429/5xx の再試行回数（指数バックオフ）
_last_req = 0.0           # 直近リクエスト時刻（モジュール内・スロットル用）


def get_api_key() -> str:
    key = os.environ.get("JQUANTS_API_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "環境変数 JQUANTS_API_KEY が未設定です。\n"
            "GitHub Secrets もしくはローカルの環境変数に設定してください。\n"
            "取得先: https://jpx-jquants.com/dashboard/api-keys"
        )
    return key


def _headers() -> dict:
    return {"x-api-key": get_api_key()}


def _throttle() -> None:
    """全リクエスト間に最小間隔を空け、429（レート制限）を予防する。"""
    global _last_req
    wait = _MIN_INTERVAL - (time.monotonic() - _last_req)
    if wait > 0:
        time.sleep(wait)
    _last_req = time.monotonic()


def _get(url: str, params: dict):
    """スロットル＋429/5xxの指数バックオフ再試行つきGET（Retry-Afterを尊重）。"""
    headers = _headers()
    for attempt in range(_RETRY_429):
        _throttle()
        r = requests.get(url, headers=headers, params=params, timeout=_TIMEOUT)
        if r.status_code == 429 or r.status_code >= 500:
            ra = r.headers.get("Retry-After", "")
            wait = float(ra) if ra.isdigit() else min(60.0, 1.5 * (2 ** attempt))
            logger.warning("HTTP %d: %.1fs待機して再試行 (試行%d/%d) %s",
                           r.status_code, wait, attempt + 1, _RETRY_429, url)
            time.sleep(wait)
            continue
        r.raise_for_status()
        return r
    raise RuntimeError(f"レート制限/サーバエラーが続いたため中断: {url}")


def pick(d: dict, *names: str, default: Any = None) -> Any:
    """dictから最初に見つかったキーの値を返す（V2略称/V1長名の差異を吸収）。"""
    for n in names:
        if n in d and d[n] not in (None, ""):
            return d[n]
    return default


def _paginate(path: str, params: dict | None = None,
              data_keys: tuple[str, ...] = ("data",)) -> list[dict]:
    """
    pagination_key を辿って全件取得する共通ヘルパ。
    data_keys: レスポンス本体が入っているキーの候補（V2は "data"）。
    """
    params = dict(params or {})
    rows: list[dict] = []
    url = f"{JQ_BASE}{path}"
    for _ in range(_MAX_PAGES):
        r = _get(url, params)
        js = r.json()
        chunk: list[dict] = []
        for k in data_keys:
            if k in js:
                chunk = js[k] or []
                break
        rows.extend(chunk)

        pkey = js.get("pagination_key")
        if not pkey:
            break
        params["pagination_key"] = pkey
    return rows


# ---------------------------------------------------------------------------
# 各エンドポイント
# ---------------------------------------------------------------------------

def fetch_equities_master() -> list[dict]:
    """上場銘柄一覧。V2略称: Code, CoName, MktNm, S33(=Sector33Code), S33Nm。"""
    return _paginate("/equities/master")


def fetch_daily_quotes(date_str: str) -> list[dict]:
    """指定日(YYYY-MM-DD)の全銘柄の株価四本値。V2: /equities/bars/daily。
    略称: Date, Code, O/H/L/C, Vo, Va, AdjFactor, AdjO/AdjH/AdjL/AdjC/AdjVo。"""
    return _paginate("/equities/bars/daily", {"date": date_str})


def fetch_fins_summary(date_str: str) -> list[dict]:
    """指定日(YYYY-MM-DD)に開示された財務サマリー。
    V2略称: DiscDate, Code, CurPerType(1Q/2Q/3Q/FY), CurPerEn, CurFYEn,
            Sales, OP, OdP, NP, EPS, TA, Eq, BPS, ShOutFY, TrShFY, FEPS。"""
    return _paginate("/fins/summary", {"date": date_str})


def fetch_topix(from_date: str | None = None, to_date: str | None = None) -> list[dict]:
    """TOPIX指数の日次OHLC。V2: /indices/bars/daily/topix。略称: Date, O, H, L, C（出来高なし）。"""
    params: dict = {}
    if from_date:
        params["from"] = from_date
    if to_date:
        params["to"] = to_date
    # レスポンス本体キーは "data"（念のため "topix" もフォールバック）
    return _paginate("/indices/bars/daily/topix", params, data_keys=("data", "topix"))


# ---------------------------------------------------------------------------
# 値変換ユーティリティ（財務値は文字列で来る・空欄/全角ダッシュ対策）
# ---------------------------------------------------------------------------

def to_float(x: Any) -> float | None:
    """財務値を float に。空欄・'－'・None は None。"""
    if x is None:
        return None
    if isinstance(x, (int, float)):
        return float(x)
    s = str(x).strip()
    if s in ("", "-", "－", "‐", "—", "nan", "None"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def to_int(x: Any) -> int | None:
    f = to_float(x)
    return int(f) if f is not None else None
