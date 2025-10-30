# advisor/admin_policy.py
from django.contrib import admin
from .models_policy import AdvisorPolicy, PolicySnapshot, DeviationLog

@admin.register(AdvisorPolicy)
class AdvisorPolicyAdmin(admin.ModelAdmin):
    list_display = ("name", "family", "is_active", "priority", "updated_at")
    list_filter = ("family", "is_active")
    search_fields = ("name",)
    ordering = ("-is_active", "-priority", "name")

@admin.register(PolicySnapshot)
class PolicySnapshotAdmin(admin.ModelAdmin):
    list_display = ("policy", "as_of", "version_tag", "file_path", "created_at")
    list_filter = ("as_of", "policy")
    search_fields = ("policy__name", "version_tag", "file_path")

@admin.register(DeviationLog)
class DeviationLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "ticker", "action", "policy", "user")
    list_filter = ("action", "policy")
    search_fields = ("ticker", "reason")