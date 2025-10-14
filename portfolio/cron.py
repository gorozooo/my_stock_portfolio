# portfolio/cron.py
from django.core.management import call_command

def snapshot():
    # 例: advisor_snapshot --tag daily-cron
    call_command("advisor_snapshot", tag="daily-cron")

def learn():
    # 例: advisor_learn --days 7（引数は任意で調整）
    call_command("advisor_learn", days="7")