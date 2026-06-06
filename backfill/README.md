# jquants-backfill — 過去株価の一括確保（Standard期限切れ前の保険）

Standardプラン（保存期間**10年**）が有効なうちに、過去の日次株価（全銘柄）を
まとめてダウンロードして手元に残しておくためのツール。
ダウングレード（Light=5年）後でも、保存済みCSVでバックテストができる。

出力は既存の `JQuants_Data_5Years/quotes_*.csv` と**完全に同じ形式**（1営業日=1ファイル）。

```
quotes_2018-04-03.csv ...
Date,Code,O,H,L,C,UL,LL,Vo,Va,AdjFactor,AdjO,AdjH,AdjL,AdjC,AdjVo
```

## 使い方（GitHub Actions・推奨／キー安全）

APIキーは既に `oneil-analyzer` の Secret `JQUANTS_API_KEY`（Standard）にあるので、
**あなたも私もキー値に触れずに**実行できる。

1. GitHub → リポジトリ **Actions** タブ → 左の **backfill-history** を選択
2. **Run workflow** をクリック（入力はそのままでOK：`years=10`, `to_date=2024-12-22`）
3. 完了まで待つ（目安 1〜2時間）。
4. 実行結果ページ下部の **Artifacts** から `jquants_history`（tar.gz, 約1GB前後）をダウンロード。
5. 解凍して中の `quotes_*.csv` を **`H:\マイドライブ\JQuants_Data_5Years\`** にコピー（マージ）。
   - 既存（2024-12-23以降）と合わせて、約2016年〜現在までの連続データになる。
6. データが揃ったのを確認してから **Lightにダウングレード**。

> `gh` CLIで起動・取得する場合:
> ```
> gh workflow run backfill.yml
> gh run watch <RUN_ID>
> gh run download <RUN_ID> -n jquants_history
> ```

## 取得範囲について

- 既定 `to_date=2024-12-22` … 既存データ（2024-12-23〜）の**直前まで**＝ギャップだけを取得（重複ダウンロードを避ける）。
- `years=10` … 今日から10年前まで。Standardの保存期間いっぱい。
- 保存期間外・祝日の日は API が空を返すので**ファイルは作られない**（自動でスキップ）。
- ログに「取得できた最古の営業日」を表示するので、実際のカバー範囲が分かる。

## 仕組み

- `download_history.py` … 同リポジトリ `batch/jquants.py`（実績あるV2クライアント＝
  レート制限スロットル0.4s＋429指数バックオフ）をそのまま再利用。
- **再開可能**: 出力先に既にある `quotes_*.csv` はスキップ。途中で止まっても再実行で続きから。
- `backfill.yml` … 手動起動の専用ワークフロー。Secretのキーで実行し、結果をtar.gzアーティファクトで配布。

## 注意

- これは**1回限りの保険**。日々の運用（oneil-analyzer本体）はLightで通常どおり動く。
- 財務（`/fins/summary`）の10年分も欲しい場合は別途追加可能（同方式）。
