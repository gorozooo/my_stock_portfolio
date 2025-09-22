# My Stock Portfolio

Django製の株管理アプリです📈

## セットアップ方法

```bash
git clone https://github.com/gorozooo/my_stock_portfolio.git
cd my_stock_portfolio
python -m venv venv
source venv/bin/activate  # Windowsなら venv\Scripts\activate
pip install -r requirements.txt
python manage.py runserver

#ミグレーション
python manage.py makemigrations
python manage.py migrate
python manage.py createsuperuser

python manage.py shell

#### ③ `requirements.txt` の作成

以下のコマンドで必要なPythonパッケージをまとめて保存できます：

```bash
pip freeze > requirements.txt

#東証データ取得
"手動更新
python manage.py import_stockmaster --file tse-listed-issues.xls
#自動更新
python manage.py import_stockmaster --auto



# 作業・修正した後に：
cd C:\my_stock_portfolio
git a "更新1"

git add .
git commit -m "更新①"
git push origin main
#ローカルを強制で上書きする
git push -f origin main. 

# vps側で：
su - gorozooo
cd /home/gorozooo/my_stock_portfolio
source venv/bin/activate
u

#内容確認
git log -1


1. Gunicorn を systemd サービス化している場合

すでに作っている前提で説明します。サービス名を仮に my_stock_portfolio.service とします。

2. Git pull + 自動再起動用スクリプト作成

プロジェクト直下に update.sh を作ります：

nano ~/my_stock_portfolio/update.sh


中身を以下に：

#!/bin/bash

# プロジェクトディレクトリに移動
cd /home/gorozooo/my_stock_portfolio || exit

# 仮想環境を有効化
source /home/gorozooo/my_stock_portfolio/venv/bin/activate

# GitHub から最新コード取得
git pull origin main

# 静的ファイル反映
python manage.py collectstatic --noinput

# マイグレーション反映（必要なら）
python manage.py migrate

# Gunicorn サービス再起動
sudo systemctl restart my_stock_portfolio.service

echo "Update completed."


保存して閉じます。

実行権限を付与：

chmod +x ~/my_stock_portfolio/update.sh

Apple風ガラスUI＋近未来光彩