from django.contrib import admin
from .models import ActionLog, Reminder

@admin.register(ActionLog)
class ActionLogAdmin(admin.ModelAdmin):
    list_display = ("created_at","user","ticker","action")
    list_filter = ("action",)
    search_fields = ("ticker","note")

@admin.register(Reminder)
class ReminderAdmin(admin.ModelAdmin):
    list_display = ("fire_at","user","ticker","done")
    list_filter = ("done",)
    search_fields = ("ticker","message")