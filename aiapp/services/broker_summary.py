# aiapp/services/broker_summary.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, List, Dict

from django.db.models import Sum, F

# 定義元に合わせて分けて import
from portfolio.models_cash import BrokerAccount, CashLedger
from portfolio.models import Holding


# 表示順（固定）
BROKERS_UI = [
    ("RAKUTEN", "楽天"),
    ("MATSUI",  "松井"),
    ("SBI",     "SBI"),
]

# ---- 重要：内部コード → Cash側の broker 名（DBに入っている値）へ寄せる ----
# cash_dashboard（cash_service.broker_summaries）が返す broker 名と合わせる目的。
# ※あなたのDBでは cash_service 側の broker が「楽天」「松井」「SBI」になっているため、
#   ここも同じ値に統一する。
BROKER_CODE_TO_CASH_BROKER_NAME = {
    "RAKUTEN": "楽天",
    "MATSUI":  "松井",
    "SBI":     "SBI",
}


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


def _cash_balance_yen(*, cash_broker_name: str) -> int:
    """
    BrokerAccount(opening_balance + ledgers) の和。
    CashLedger.amount は 入金＋ / 出金− で運用されている前提。

    ここは cash_dashboard と同じ “broker 名” をキーに拾う（重要）。
    """
    accounts = BrokerAccount.objects.filter(broker=cash_broker_name, currency="JPY")
    if not accounts.exists():
        return 0
    base = accounts.aggregate(total=Sum("opening_balance"))["total"] or 0
    ledg = CashLedger.objects.filter(account__in=accounts).aggregate(total=Sum("amount"))["total"] or 0
    return int(base + ledg)


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
      base（楽天/松井/SBI） = 現金 + 現物取得額*(1-ヘアカット)
      信用枠               = base * 倍率
      信用余力             = max(0, 信用枠 - 信用建玉評価額合計)

    ※3社とも「倍率 / ヘアカット」を同条件で扱う。
    """
    out: List[BrokerNumbers] = []

    for code, label in BROKERS_UI:
        cash_broker_name = BROKER_CODE_TO_CASH_BROKER_NAME.get(code, label)
        cash_yen = _cash_balance_yen(cash_broker_name=cash_broker_name)

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