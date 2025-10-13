# portfolio/tasks.py
from celery import shared_task
from datetime import date, timedelta
from .ml.train import fit_model
from .models_advisor import AdviceItem
from .services.metrics import compute_liquid_roi_delta

@shared_task
def retrain_advisor():
    """毎週: AIモデルの再学習"""
    fit_model()

@shared_task
def evaluate_outcomes():
    """採用済み提案の成果を追跡"""
    cutoff = date.today() - timedelta(days=28)
    qs = AdviceItem.objects.filter(taken=True, outcome__isnull=True, created_at__date__lte=cutoff)
    for it in qs:
        delta = compute_liquid_roi_delta(it.created_at.date())
        it.outcome = {"liquid_roi_delta": delta}
        it.save()