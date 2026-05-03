# 家計簿アプリ

家族3人（家・ぼーや・ごろ）の家計管理アプリ。  
Next.js 14 + Supabase で構築、Vercel でホスティング。

## 機能

| 画面 | 機能 |
|------|------|
| ダッシュボード | 月次収支・貯蓄KPI・口座残高・TODO・メモ・月確定スナップショット |
| 収入入力 | 月次収入の登録（カテゴリ・対象者・メモ） |
| 変動費入力 | カード / 立替 / 年金保険 / その他を入力 |
| 固定費管理 | 有効/無効切り替え付きテンプレート |
| 口座残高 | 月次残高の登録（同月同口座は自動上書き） |
| 収支分析 | 収入・固定費・変動費・立替を色分け表示 + バーグラフ |
| チャート | 12ヶ月トレンド（収入/支出/収支差）+ カテゴリ別円グラフ |
| 設定 | カテゴリ / 口座 / カードのCRUD |
| データ管理 | 月別フィルタで各データの確認・削除 |

## ローカル開発セットアップ

### 1. Supabase プロジェクト作成

1. [supabase.com](https://supabase.com) でプロジェクトを作成
2. SQL エディタで `supabase/schema.sql` を実行（テーブル・RLS・初期データを作成）
3. **Authentication → Settings → 「Disable sign-ups」を有効にする**（家族専用アプリのため）
4. Authentication → Users から家族メンバーのアカウントを手動で作成

### 2. 環境変数の設定

```bash
cp .env.local.example .env.local
```

`.env.local` に Supabase の URL と Anon Key を記入：

```
NEXT_PUBLIC_SUPABASE_URL=https://xxxx.supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY=eyJ...
```

### 3. 起動

```bash
npm install
npm run dev
```

`http://localhost:3000` をブラウザで開く。

---

## Vercel デプロイ手順

### Step 1: Vercel にリポジトリを接続

1. [vercel.com](https://vercel.com) にアクセスしてログイン（GitHubアカウントで可）
2. 「New Project」ボタンをクリック
3. `gorozooo/kakeibo` を選択して「Import」

### Step 2: 環境変数を設定

デプロイ前に「Environment Variables」セクションで以下を設定：

| 変数名 | 取得場所 |
|--------|---------|
| `NEXT_PUBLIC_SUPABASE_URL` | Supabase → Settings → API → Project URL |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | Supabase → Settings → API → anon/public key |

### Step 3: デプロイ

「Deploy」ボタンを押すと自動ビルド・デプロイ開始。  
完了すると `https://xxxxx.vercel.app` のURLが発行される。

### Step 4: 以降の自動デプロイ

`main` ブランチへ push するたびに自動デプロイされる。  
（Vercel の「Deployments」タブでログを確認できる）

### Step 5: カスタムドメイン（任意）

Vercel Dashboard → プロジェクト → Settings → Domains でカスタムドメインを追加できる。

---

## 技術スタック

- **フレームワーク**: Next.js 14 (App Router)
- **言語**: TypeScript
- **データベース**: Supabase (PostgreSQL + Auth + Row Level Security)
- **スタイリング**: Tailwind CSS（ライトテーマ）
- **チャート**: Recharts
- **通知**: Sonner (トースト通知)
- **ホスティング**: Vercel

## 将来の拡張（日本株ポートフォリオ / 自動売買統合）

このアプリは将来的にポートフォリオ管理・自動売買管理を同一アプリに統合できるよう設計されています：

- `app/(kakeibo)/` のルートグループが `app/(portfolio)/` などと共存可能
- Supabase のスキーマを `kakeibo.*`, `portfolio.*` に分離して拡張予定
- 認証（Supabase Auth）は全モジュール共通
- `lib/kakeibo/queries.ts` に portfolio テーブルへの参照拡張ポイントを設定済み
