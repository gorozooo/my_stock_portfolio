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
