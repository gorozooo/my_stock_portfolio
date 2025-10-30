# advisor/management/commands/policy_seed.py
from __future__ import annotations
import os, json, datetime
from django.core.management.base import BaseCommand
from django.conf import settings
from advisor.models_policy import AdvisorPolicy, PolicySnapshot

def policy_payload_nisa():
    return {
        "id": "policy_nisa_dividend",
        "labels": {"segment": "NISA（長期）", "action": "配当・優待メイン"},
        "rules": {
            "entry": {
                "min_div_yield": 0.025,
                "ma25_up": True,
                "ma75_up": True,
                "max_ytd_drawdown": 0.18,
                "post_earnings_cooldown_days": 3,
            },
            "exit": {
                "structural_sl": {"use": True, "atr_mult": 1.0, "swing_close_break": True},
                "time_stop_days": 90,
            },
            "size": {"risk_pct": 0.003},
            "allow_weekly": ["up", "flat"],
            "min_overall": 55, "min_theme": 0.45,
        },
        "targets": {"tp_pct": 0.20, "sl_pct": 0.08},
        "priority": 70,
    }

def policy_payload_trend():
    return {
        "id": "policy_trend_follow",
        "labels": {"segment": "中期（20〜45日）", "action": "順張り・押し目買い"},
        "rules": {
            "entry": {
                "ema20_gt_ema50": True,
                "adx_min": 25,
                "above_avwap": True,
            },
            "exit": {
                "atr_trail_mult": 3.0,
                "time_stop_days": 45,
            },
            "size": {"risk_pct": 0.008},
            "allow_weekly": ["up"],
            "min_overall": 60, "min_theme": 0.50, "min_slope_yr": 0.10,
        },
        "targets": {"tp_pct": 0.12, "sl_pct": 0.05},
        "priority": 80,
    }

def policy_payload_meanrev():
    return {
        "id": "policy_mean_reversion",
        "labels": {"segment": "短期（5〜10日）", "action": "逆張り・ミーン回帰"},
        "rules": {
            "entry": {
                "rsi_th": 30,
                "bb_sigma": 2.5,
            },
            "exit": {
                "to_mid_band": True,
                "time_stop_days": 10,
            },
            "size": {"risk_pct": 0.006},
            "allow_weekly": ["flat", "down", "up"],
            "min_overall": 50, "min_theme": 0.40,
        },
        "targets": {"tp_pct": 0.06, "sl_pct": 0.02},
        "priority": 60,
    }

class Command(BaseCommand):
    help = "初期ポリシー3種を作成し、当日のSnapshot(JSON)を media/advisor/policies/yyyyMMdd/ に保存"

    def handle(self, *args, **kwargs):
        os.makedirs(os.path.join(settings.MEDIA_ROOT, "advisor", "policies"), exist_ok=True)

        seeds = [
            ("NISA配当・優待", "NISA長期・配当再投資優先", "NISA", policy_payload_nisa()),
            ("Trend追随（押し目）", "EMA20>50 + ADX↑ + AVWAP上", "Trend", policy_payload_trend()),
            ("Mean Reversion（逆張り）", "RSI/拡張BBの極値から反転", "MeanReversion", policy_payload_meanrev()),
        ]

        today = datetime.datetime.now().date()
        ymd = today.strftime("%Y%m%d")
        outdir = os.path.join(settings.MEDIA_ROOT, "advisor", "policies", ymd)
        os.makedirs(outdir, exist_ok=True)

        created = 0
        for name, desc, family, payload in seeds:
            obj, _ = AdvisorPolicy.objects.update_or_create(
                name=name,
                defaults=dict(
                    description=desc,
                    is_active=True,
                    priority=int(payload.get("priority", 50)),
                    rule_json=payload,
                    family=family,
                    timeframe_label=payload.get("labels", {}).get("segment", "中期（20〜45日）"),
                ),
            )
            # Snapshot 保存
            snap_json = {
                "policy_name": obj.name,
                "saved_at": today.isoformat(),
                "rule_json": obj.rule_json,
            }
            slug = payload["id"]
            file_name = f"{slug}.json"
            file_path = os.path.join(outdir, file_name)
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(snap_json, f, ensure_ascii=False, indent=2)

            PolicySnapshot.objects.update_or_create(
                policy=obj, as_of=today, version_tag="am",
                defaults=dict(payload=snap_json,
                              file_path=os.path.relpath(file_path, settings.MEDIA_ROOT)),
            )
            created += 1

        self.stdout.write(self.style.SUCCESS(f"Seeded {created} policies & snapshots into {outdir}"))