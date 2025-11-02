#マイグレーション
python manage.py makemigrations
python manage.py makemigrations advisor
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

1️⃣ 寄り付き前（7:20）
cd ~/my_stock_portfolio && venv/bin/python manage.py advisor_daily_brief --line --mode=preopen --line-all

2️⃣ 寄り直後（9:50）
cd ~/my_stock_portfolio && venv/bin/python manage.py advisor_daily_brief --line --mode=postopen --line-all

3️⃣ 前場まとめ → 後場へ（12:00）
cd ~/my_stock_portfolio && venv/bin/python manage.py advisor_daily_brief --line --mode=noon --line-all

4️⃣ 後場の温度感（14:55）
cd ~/my_stock_portfolio && venv/bin/python manage.py advisor_daily_brief --line --mode=afternoon --line-all

5️⃣ 明日への展望（17:00）
cd ~/my_stock_portfolio && venv/bin/python manage.py advisor_daily_brief --line --mode=outlook --line-all

まとめて一気にテスト
cd ~/my_stock_portfolio
for mode in preopen postopen noon afternoon outlook; do
  echo "=== Testing $mode ==="
  venv/bin/python manage.py advisor_daily_brief --line --mode=$mode --line-all
  sleep 2
done


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
