from django.contrib import admin
from .models import BottomNav

class BottomNavAdmin(admin.ModelAdmin):
    list_display = ("name", "icon", "url_name", "order", "parent")
    list_editable = ("icon", "url_name", "order", "parent")

admin.site.register(BottomNav, BottomNavAdmin)


