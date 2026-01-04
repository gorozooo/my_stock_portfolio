# advisor/admin_policy.py
from django.contrib import admin
from .models_policy import AdvisorPolicy, PolicySnapshot, DeviationLog

@admin.register(AdvisorPolicy)
class AdvisorPolicyAdmin(admin.ModelAdmin):
    list_display = ("name", "is_active", "priority", "family", "timeframe_label", "updated_at")
    list_filter  = ("is_active", "family",)
    search_fields = ("name", "description", "family")
    ordering = ("-is_active", "-priority", "name")
    readonly_fields = ()
    fieldsets = (
        (None, {
            "fields": ("name", "description", "is_active", "priority", "family", "timeframe_label")
        }),
        ("Rule (JSON)", {
            "fields": ("rule_json",),
            "description": "エントリー/イグジット/サイズ/リスク/時間軸などの数値ルール"
        }),
        ("Meta", {"fields": (), "description": "作成/更新は自動"})
    )

@admin.register(PolicySnapshot)
class PolicySnapshotAdmin(admin.ModelAdmin):
    list_display = ("as_of", "policy", "version_tag", "file_path", "created_at")
    list_filter  = ("as_of", "version_tag")
    search_fields = ("policy__name", "version_tag", "file_path")
    ordering = ("-as_of", "-created_at")

@admin.register(DeviationLog)
class DeviationLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "ticker", "policy", "action")
    list_filter  = ("action", "created_at")
    search_fields = ("ticker", "reason")
    ordering = ("-created_at",)