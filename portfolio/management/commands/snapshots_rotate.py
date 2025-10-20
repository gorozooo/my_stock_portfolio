# -*- coding: utf-8 -*-
from __future__ import annotations
import os, json, gzip, glob, shutil
from datetime import datetime, timedelta
from typing import List

from django.core.management.base import BaseCommand
from django.conf import settings

def _media_root() -> str:
    return getattr(settings, "MEDIA_ROOT", "") or os.getcwd()

def _list_day_dirs(base: str) -> List[str]:
    if not os.path.isdir(base):
        return []
    return sorted([p for p in glob.glob(os.path.join(base, "*")) if os.path.isdir(p)])

def _parse_dt_from_name(name: str) -> datetime:
    return datetime.strptime(name, "%H%M")

class Command(BaseCommand):
    help = "スナップショットのローテーション：7日間は5分足、8〜90日は30分足(GZ)、90日超は日次集計のみ残す"

    def add_arguments(self, parser):
        parser.add_argument("--keep-raw-days", type=int, default=7)
        parser.add_argument("--downsample-to", type=str, default="30m")  # 今は固定扱い
        parser.add_argument("--keep-days", type=int, default=90)

    def handle(self, *args, **opts):
        base = os.path.join(_media_root(), "market", "snapshots")
        today = datetime.now().date()

        keep_raw = int(opts["keep_raw_days"] or 7)
        keep_days = int(opts["keep_days"] or 90)

        for daydir in _list_day_dirs(base):
            try:
                day = os.path.basename(daydir)
                d = datetime.strptime(day, "%Y-%m-%d").date()
            except Exception:
                continue

            age = (today - d).days
            files = sorted(glob.glob(os.path.join(daydir, "*.json")))

            if age <= keep_raw:
                # そのまま
                continue

            if age <= keep_days:
                # 30分足間引き + gzip 圧縮 へ
                keep_minutes = {f"{h:02d}{m:02d}" for h in range(0,24) for m in (0,30)}
                for fp in files:
                    hhmm = os.path.splitext(os.path.basename(fp))[0]
                    if hhmm not in keep_minutes:
                        os.remove(fp)
                        continue
                    # gzip化
                    with open(fp, "rb") as f_in, gzip.open(fp + ".gz", "wb") as f_out:
                        shutil.copyfileobj(f_in, f_out)
                    os.remove(fp)
            else:
                # 日次集計（最新1件のみを残し、他は削除 or 圧縮省略）
                if files:
                    newest = files[-1]
                    for fp in files[:-1]:
                        os.remove(fp)
                    # 一応gzip可
                    with open(newest, "rb") as f_in, gzip.open(newest + ".gz", "wb") as f_out:
                        shutil.copyfileobj(f_in, f_out)
                    os.remove(newest)

        self.stdout.write(self.style.SUCCESS("rotation done"))