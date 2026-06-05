# セットアップ手順（GitHubが初めての方向け）

GitHub とは「プログラムを置いておくと、**無料で毎日決まった時刻に自動実行**してくれて、
**Webページも無料で公開**できるクラウド」です。今回はこの仕組みで、毎朝自動で株のスクリーニングを回します。

あなたの手作業は **5ステップだけ**。残りは私（Claude）が `gh` コマンドで代行します。
このPCには **git / GitHub CLI(gh) / Python 3.12 は導入済み**です。

---

## ステップ1：GitHub アカウントを作る（あなた・5分）
1. ブラウザで https://github.com/signup を開く
2. メールアドレス・パスワード・ユーザー名を入力して登録（無料）
3. 確認メールのコードを入力して完了

> ※ アカウント作成とパスワード入力は、セキュリティ上あなた自身で行ってください（私は代行しません）。

## ステップ2：gh にログインする（あなた・2分／私がコマンドを出します）
ターミナルで以下を実行します（私が実行を促します）:
```
gh auth login
```
- `GitHub.com` を選択 → `HTTPS` → `Login with a web browser` を選択
- 画面に出る **8桁のコード**をコピー → 自動で開くブラウザに貼り付け → `Authorize`
これでこのPCが、あなたのGitHubを操作できるようになります（**ここまで来たら私に教えてください**）。

## ステップ3：リポジトリ作成＆アップロード（私が代行）
あなたがログインできたら、私が次を実行します:
```
cd H:\マイドライブ\claude1\oneil_analyzer
gh repo create oneil-analyzer --public --source=. --remote=origin --push
```
→ コード一式がGitHubに上がります（**公開リポジトリ**。鍵などの秘密情報は含めません）。

## ステップ4：J-Quantsの鍵を登録する（あなた・Web画面で・2分）
鍵をコードに書くのは危険なので、GitHubの金庫（Secrets）に入れます。
1. 作成されたリポジトリのページを開く（私がURLを出します）
2. **Settings**（歯車）→ 左メニュー **Secrets and variables** → **Actions**
3. **New repository secret** を押す
4. Name: `JQUANTS_API_KEY` ／ Secret: あなたのJ-QuantsのAPIキー（https://jpx-jquants.com/dashboard/api-keys ）
5. **Add secret**

> ※ 鍵の入力もあなた自身で行ってください（私は鍵を扱いません）。

## ステップ5：GitHub Pages を「Actions」に設定（あなた・Web画面で・1分）
1. **Settings** → 左メニュー **Pages**
2. **Build and deployment** の **Source** を **GitHub Actions** に変更
（これで毎日の自動デプロイ先が有効になります）

---

## 初回の動作確認（私が代行）
1. まず**小さく**動かして確認します（先頭60銘柄だけ）:
   - GitHubの **Actions** タブ → **daily** → **Run workflow** → `max_codes` に `60` を入れて実行
   - もしくは私が `gh workflow run daily.yml -f max_codes=60` を実行
2. 緑のチェック（成功）になったら、**Pages のURL**を開いて画面を確認
   - URL: `https://<あなたのユーザー名>.github.io/oneil-analyzer/`
3. 問題なければ **全銘柄**で再実行（`max_codes` を空で Run workflow）。初回フルビルドは数十分かかります。

## 毎日の運用（手作業ゼロ）
- 毎朝 **07:00 JST** に自動で更新されます。あなたは何もしなくてOK。
- 新しく条件を満たした銘柄が出ると **GitHub から件名「[オニール] 新規ランクイン…」のメール**が届きます。
- メール内のリンクから銘柄詳細ページへ直接飛べます。気に入らなければIssueをCloseすれば記録になります。
- 手動で今すぐ更新したいとき: Actions タブ → daily → Run workflow。

## うまくいかないとき
- **Actionsが赤（失敗）**: ログの最初の赤い行を私に見せてください。多くは Secret名のtypoか、J-Quantsプラン制限です。
- **ページが「自動取得できません」**: 1回 Actions を成功させると `oneil.db.gz` が公開されます。
- **データが古い/少ない**: J-Quants Light は当日データに対応。初回は遡って取得するため反映まで時間がかかります。
