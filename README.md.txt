#マイグレーション
python manage.py makemigrations
python manage.py migrate
python manage.py createsuperuser

#シェルコマンド
python manage.py shell

cron（自動スケジューラ）を開く
crontab -e

#東証データ取得
python manage.py update_tse_list

# vps側で：
su - gorozooo
cd /home/gorozooo/my_stock_portfolio
source venv/bin/activate
a


python manage.py advisor_daily_brief --line --line-all

# 環境変数
nano .env