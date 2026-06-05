# O'Neil 成長株アナライザー（GitHubだけで自動運用）

『オニールの成長株発掘法』(CAN-SLIM) のスクリーニングと、マーケットの天井（分配日）検出を、
**毎朝自動で更新し、新しく条件を満たした銘柄はメールで通知**する個人用システム。
使うサービスは **GitHub だけ**（AWS等は不要）。データ源は **J-Quants Light**（過去5年・遅延なし）。

## できること
- **成長株ランキング** … EPS成長(Q0/Q1/通期)・2年CAGR・ROE・自己資本比率・RS(相対強度1-99)でスコア化。
  ブラウザ上で閾値や重みを変えて即再ランキング。銘柄をクリックするとローソク足＋決算マーカー＋財務チャートの詳細へ。
- **マーケット天井検出** … TOPIXの「分配日（下落×出来高増）」を数え、天井が近いと警戒表示。
- **通知** … 新規ランクイン銘柄・天井警戒を GitHub Issue 化 → 所有者にメール。気に入らなければcloseすれば確認ログになる。

## 仕組み（アーキテクチャ）
```
[GitHub Actions 日次cron 07:00 JST]
  1. data ブランチから前回の oneil.db を復元
  2. J-Quants Light から「DB内最新日より後」を増分取得（株価/財務/TOPIX）
  3. SQLiteに追記 → オニール指標を計算（metrics）→ 分配日を計算
  4. data ブランチへ oneil.db を force-push（単一コミット＝履歴が太らない）
  5. site/（HTML）＋ oneil.db.gz を GitHub Pages にデプロイ
  6. 新規ランクイン/天井警戒があれば GitHub Issue を作成（→メール通知）

[GitHub Pages]  https://<ユーザー>.github.io/<repo>/
  index.html  : ランキング＋銘柄詳細（sql.js でDBをブラウザ内クエリ、lightweight-charts で描画）
  market.html : マーケット天井検出
  oneil.db.gz : 同一オリジン配信（ブラウザが取得・解凍してキャッシュ）
```
- ブログ版の S3 を **GitHub Pages 同一オリジン配信**に置換（Releasesはブラウザfetchが CORS で不可のため）。
- ライブラリ（sql.js / lightweight-charts）は `site/vendor/` に同梱（CDN・社内FW非依存で堅牢）。
- 生データのアーカイブ不要：DBはいつでも J-Quants から再構築できる。

## ファイル構成
```
oneil_analyzer/
├─ .github/workflows/daily.yml  日次cron + 手動実行（workflow_dispatch）
├─ batch/
│  ├─ jquants.py        J-Quants V2クライアント（x-api-key, /equities/master, /prices/daily_quotes, /fins/summary, /indices/topix）
│  ├─ build_db.py       増分DBビルダー（取得→SQLite→指標→分配日→gzip）
│  ├─ metrics.py        オニール指標（単四半期復元・YoY・CAGR・ROE・RSレーティング・SCORE）
│  ├─ distribution.py   分配日カウント（天井検出）
│  ├─ notify.py         前日差分→GitHub Issue
│  └─ requirements.txt
├─ site/
│  ├─ index.html        ランキング＋詳細SPA
│  ├─ market.html       マーケット天井検出
│  └─ vendor/           sql.js / lightweight-charts（同梱）
├─ tests/               モックでの決定論ユニットテスト（19件）+ サンプルDB生成
├─ SETUP_ja.md          GitHub初心者向けセットアップ手順
└─ README.md
```

## ローカル開発・検証（このリポジトリを改造するとき）
Python 3.12 と依存（requests/pandas/numpy/pytest）が必要。Windowsの例:
```
py -m venv .venv ; .venv\Scripts\python -m pip install -r batch/requirements.txt pytest
# ユニットテスト
.venv\Scripts\python -m pytest tests -q
# サンプルDBを生成（site/oneil.db.gz）→ ローカル配信して画面確認
.venv\Scripts\python tests/make_sample_db.py
.venv\Scripts\python -m http.server 8000 --directory site
#   → http://localhost:8000/index.html / market.html
```
※ J-Quants の実データを使うフル実行は GitHub Actions 上で行う（鍵は GitHub Secrets）。

## オニール指標の対応（CAN-SLIM）
- **C** 当四半期EPS … Q0 EPS YoY（単四半期、+25%目安）／加速確認に Q1 も併記
- **A** 年間EPS … 通期EPS YoY・2年CAGR（+25%目安）、ROE +17%目安
- **N** 新高値 … 52週高値からの位置
- **S** 需給 … 直近出来高の増加
- **L** 主導株 … RSレーティング（全銘柄パーセンタイル1-99、85以上目安）
- **M** 相場 … マーケット天井検出ページ（分配日）

> 日本の決算短信は累計開示のため、**単四半期 = 当期累計 − 同一会計年度の前期累計** で復元し、
> 同じ会計期(CurPerType)を1年前と比較してYoYを算出している（半期開示の会社でも整合）。
