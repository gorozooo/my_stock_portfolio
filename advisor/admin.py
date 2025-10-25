from django.contrib import admin
from .models import ActionLog, Reminder, WatchEntry

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
    
@admin.register(WatchEntry)
class WatchEntryAdmin(admin.ModelAdmin):
    list_display = ("updated_at", "user", "ticker", "name", "status", "in_position")
    list_filter = ("status", "in_position")
    search_fields = ("ticker", "name", "note")
