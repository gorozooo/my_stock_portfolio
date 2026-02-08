"""
[FILE] autotrade/migrations/0009_autotradebacktestrundetail_trade_date.py
[PATH] <project_root>/autotrade/migrations/0009_autotradebacktestrundetail_trade_date.py

このファイルは何？
- AutoTradeBacktestRunDetail に trade_date（JSTの評価日キー）を追加するマイグレーションです。

初心者ポイント：
- 既存データの trade_date は executed_at の“日付”で埋めます（過去分の救済）。
- 今後は作成時に必ず target_date を入れるので、UTCズレが消えます。
"""

from django.db import migrations, models
from django.db.models import F
from django.db.models.functions import TruncDate


def fill_trade_date(apps, schema_editor):
    AutoTradeBacktestRunDetail = apps.get_model("autotrade", "AutoTradeBacktestRunDetail")
    # 既存レコードを救済：trade_date = TruncDate(executed_at)
    # ※ 既存分はUTC日付になる可能性があるが、以後は trade_date を正とするので問題ない
    AutoTradeBacktestRunDetail.objects.filter(trade_date__isnull=True).update(
        trade_date=TruncDate("executed_at")
    )


class Migration(migrations.Migration):

    dependencies = [
        ("autotrade", "0008_autotradeexecution_run_detail"),
    ]

    operations = [
        migrations.AddField(
            model_name="autotradebacktestrundetail",
            name="trade_date",
            field=models.DateField(null=True, db_index=True),
        ),
        migrations.RunPython(fill_trade_date, reverse_code=migrations.RunPython.noop),
        migrations.AlterField(
            model_name="autotradebacktestrundetail",
            name="trade_date",
            field=models.DateField(db_index=True),
        ),
    ]