from django.contrib import admin
from .models import BottomTab, SubMenu

@admin.register(BottomTab)
class BottomTabAdmin(admin.ModelAdmin):
    list_display = ('name', 'icon_class', 'order')
    ordering = ('order',)

@admin.register(SubMenu)
class SubMenuAdmin(admin.ModelAdmin):
    list_display = ('tab', 'name', 'url', 'order')
    ordering = ('tab', 'order')