#マイグレーション
python manage.py makemigrations
python manage.py makemigrations aiapp
python manage.py migrate
python manage.py createsuperuser

#シェルコマンド
python manage.py shell

cron（自動スケジューラ）を開く
crontab -e
crontab -l

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
nano ~/.bashrc

python manage.py picks_build --universe quick_30 --nbars 180 --topk 10

tail -n 3 media/aiapp/simulate/sim_trades.jsonl

: > /home/gorozooo/my_stock_portfolio/media/logs/brief.log

env -i SHELL=/bin/bash PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin HOME=/home/gorozooo LANG=ja_JP.UTF-8 LC_ALL=ja_JP.UTF-8 \
bash -lc '/usr/bin/flock -w 10 /home/gorozooo/my_stock_portfolio/media/logs/brief.lock \
-c "/home/gorozooo/my_stock_portfolio/venv/bin/python /home/gorozooo/my_stock_portfolio/manage.py advisor_daily_brief --line --mode=noon --line-all" \
>> /home/gorozooo/my_stock_portfolio/media/logs/brief.log 2>&1'

tail -n 60 /home/gorozooo/my_stock_portfolio/media/logs/brief.log

python manage.py evaluate_triggers --window preopen --tickers 7203.T,6758.T --force

# テスト用ユニバース作成（もうあるなら不要）
printf "7203.T\n6758.T\n" > data/universe/two.csv

# 今日の分を計算してDB保存
python manage.py advisor_update_indicators --universe file --file data/universe/two.csv --days 60

LINE_CHANNEL_ACCESS_TOKEN=30VUdBGhHtUQJ9qdPXuPOHII3qCCWjZSGFovkg4wyR/OuhjAjGh4qHFCrYj8vnVKZRt+COfvBQhGk/c07PdcH/6OOjEKX1VdsedZIyFqje7KwikBMJxxx7vn1z2XILd+WRl+M0ZlzBuBlizojyPHQwdB04t89/1O/w1cDnyilFU=
LINE_USER_ID=Uc1388522806a0a8c5876bbf367f6e26e
LINE_CHANNEL_SECRET=dc5bc53afed89fae739f8e0388003fd7

export LINE_CHANNEL_ACCESS_TOKEN='30VUdBGhHtUQJ9qdPXuPOHII3qCCWjZSGFovkg4wyR/OuhjAjGh4qHFCrYj8vnVKZRt+COfvBQhGk/c07PdcH/6OOjEKX1VdsedZIyFqje7KwikBMJxxx7vn1z2XILd+WRl+M0ZlzBuBlizojyPHQwdB04t89/1O/w1cDnyilFU='
export LINE_USER_ID='Uc1388522806a0a8c5876bbf367f6e26e'
