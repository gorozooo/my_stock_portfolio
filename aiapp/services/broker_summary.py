# aiapp/services/broker_summary.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, List, Dict

from django.db.models import Sum, F

from portfolio.models import Holding
from portfolio.services import cash_service as cash_svc


# 表示順（固定）
BROKERS_UI = [
    ("RAKUTEN", "楽天"),
    ("MATSUI",  "松井"),
    ("SBI",     "SBI"),
]


@dataclass
class BrokerNumbers:
    code: Literal["RAKUTEN", "MATSUI", "SBI"]
    label: str
    cash_yen: int                     # 現金残高（cash_dashboard の「残り」と一致）
    stock_acq_value: int              # 現物（特定のみ）取得額
    stock_eval_value: int             # 現物（特定のみ）評価額（参考）
    margin_used_eval: int             # 信用建玉の評価額合計（BUY/SELLとも絶対値）
    leverage: float                   # 倍率（概算）
    haircut: float                    # ヘアカット率（0.0〜）
    credit_limit: int                 # 信用枠（概算）
    credit_yoryoku: int               # 信用余力（概算）
    note: str | None                  # 注記


def _cash_map_by_label() -> Dict[str, int]:
    """
    cash_service の broker_summaries(as_of) を使って
    broker表示名(label) -> 残り(cash) を作る。

    これで「設定画面の現金残高」と「現金ダッシュボードの残り」を完全一致させる。
    """
    from datetime import date
    rows = cash_svc.broker_summaries(date.today())  # ここは cash_dashboard と同じ
    out: Dict[str, int] = {}
    for r in rows:
        label = str(r.get("broker") or "").strip()
        out[label] = int(r.get("cash") or 0)
    return out


def _stock_numbers_for(*, broker_code: str) -> Dict[str, int]:
    """
    現物（特定のみ）の取得額/評価額、信用建玉（MARGIN）の評価額を集計。
    ※方針：NISAは含めない（SPECのみ）。UIで明記する。
    """
    # 現物（特定のみ）
    qs_spot = Holding.objects.filter(broker=broker_code, account="SPEC")
    acq = qs_spot.aggregate(total=Sum(F("avg_cost") * F("quantity")))["total"] or 0
    evalv = qs_spot.aggregate(total=Sum(F("last_price") * F("quantity")))["total"] or 0

    # 信用（BUY/SELLとも絶対額で評価）
    qs_m = Holding.objects.filter(broker=broker_code, account="MARGIN")
    used = 0
    for h in qs_m.only("quantity", "last_price"):
        qty = abs(int(h.quantity or 0))
        px = float(h.last_price or 0)
        used += int(qty * px)

    return {"acq": int(acq), "eval": int(evalv), "margin_used": int(used)}


def compute_broker_summaries(
    *,
    user,
    risk_pct: float,
    rakuten_leverage: float,
    rakuten_haircut: float,
    matsui_leverage: float,
    matsui_haircut: float,
    sbi_leverage: float,
    sbi_haircut: float,
) -> List[BrokerNumbers]:
    """
    概算ルール
      base（各社） = 現金（残り） + 現物取得額*(1-ヘアカット)
      信用枠       = base * 倍率
      信用余力     = max(0, 信用枠 - 信用建玉評価額合計)

    現金（残り）は cash_service と完全一致させる。
    """
    out: List[BrokerNumbers] = []

    cash_by_label = _cash_map_by_label()

    for code, label in BROKERS_UI:
        cash_yen = int(cash_by_label.get(label, 0))

        nums = _stock_numbers_for(broker_code=code)
        acq = nums["acq"]
        evalv = nums["eval"]
        used = nums["margin_used"]

        if code == "RAKUTEN":
            lev = float(rakuten_leverage or 2.9)
            hc = float(rakuten_haircut or 0.30)

        elif code == "MATSUI":
            lev = float(matsui_leverage or 2.8)
            hc = float(matsui_haircut or 0.0)

        elif code == "SBI":
            lev = float(sbi_leverage or 2.8)
            hc = float(sbi_haircut or 0.30)

        else:
            lev = 2.8
            hc = 0.0

        base = cash_yen + int(acq * (1.0 - hc))
        limit = int(base * lev)
        yoryoku = max(0, limit - used)

        note = f"倍率 {lev:.2f} / ヘアカット {hc:.2f}"

        out.append(
            BrokerNumbers(
                code=code,
                label=label,
                cash_yen=int(cash_yen),
                stock_acq_value=int(acq),
                stock_eval_value=int(evalv),
                margin_used_eval=int(used),
                leverage=lev,
                haircut=hc,
                credit_limit=int(limit),
                credit_yoryoku=int(yoryoku),
                note=note,
            )
        )
    return out