from __future__ import annotations
from django.core.management.base import BaseCommand
from django.utils.timezone import now
from django.db import transaction
from pathlib import Path
import csv
from collections import defaultdict
from ai.models import TrendResult
from ai.services.trend_calc import calc_snapshot

# CSV仕様：code,date,close,volume
# 例: media/ohlcv/7203.csv など複数ファイル or まとめ1ファイルでもOK

class Command(BaseCommand):
    help = 'OHLCVデータからTrendResultを再計算して保存する'

    def add_arguments(self, parser):
        parser.add_argument('--root', type=str, default='media/ohlcv', help='CSV格納ディレクトリ')
        parser.add_argument('--asof', type=str, default=None, help='基準日 (YYYY-MM-DD)')
        parser.add_argument('--index-rel', type=float, default=1.0, help='指数対比の仮値(1=中立)')

    def handle(self, *args, **opts):
        root = Path(opts['root'])
        asof = opts['asof'] or now().date().isoformat()
        idx_rel = float(opts['index_rel'])
        # → 無ければ作る（空なら警告して終了）
        root.mkdir(parents=True, exist_ok=True)
        files = list(root.glob('*.csv'))
        if not files and root.joinpath('ohlcv.csv').exists():
            files = [root/'ohlcv.csv']
        if not files:
            self.stdout.write(self.style.WARNING(f'CSVが見つかりません: {root}. ohlcv.csv か *.csv を配置してください。'))
            return

        # codeごとにclose/volume配列を構築
        series = defaultdict(lambda: {'close':[], 'volume':[]})
        meta = {}  # name/sectorは無ければダミー（後でStockMaster連携）

        # パターン1：銘柄別ファイル
        files = list(root.glob('*.csv'))
        # パターン2：単一ファイル（全銘柄）
        if not files and root.joinpath('ohlcv.csv').exists():
            files = [root/'ohlcv.csv']

        for f in files:
            with f.open(newline='', encoding='utf-8') as fp:
                reader = csv.DictReader(fp)
                for row in reader:
                    code = row.get('code') or row.get('ticker')
                    if not code: continue
                    close = float(row.get('close', 0) or 0)
                    vol   = int(float(row.get('volume', 0) or 0))
                    series[code]['close'].append(close)
                    series[code]['volume'].append(vol)
                    # 任意メタ（あれば活用）
                    name = row.get('name') or ''
                    sector = row.get('sector') or ''
                    if name or sector:
                        meta[code] = (name, sector)

        updated = 0
        with transaction.atomic():
            for code, ohlcv in series.items():
                snap = calc_snapshot(ohlcv, index_rel=idx_rel)
                if not snap.get('valid'): continue
                name, sector = meta.get(code, (f'銘柄{code}', '不明'))

                TrendResult.objects.update_or_create(
                    code=code,
                    defaults=dict(
                        name=name,
                        sector_jp=sector or '不明',
                        last_price=snap['last_price'],
                        last_volume=snap['last_volume'],
                        daily_slope=snap['daily_slope'],
                        weekly_trend=snap['weekly_trend'],
                        monthly_trend=snap['monthly_trend'],
                        rs_index=snap['rs_index'],
                        vol_spike=snap['vol_spike'],
                        ma5=snap['ma5'], ma20=snap['ma20'], ma60=snap['ma60'],
                        confidence=0.0,  # 後で学習由来に置換
                        as_of=asof,
                    )
                )
                updated += 1

        self.stdout.write(self.style.SUCCESS(f'Updated TrendResult: {updated} items (as_of={asof})'))