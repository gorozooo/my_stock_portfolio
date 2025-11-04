from django.contrib import admin
from ai.models import TrendResult

@admin.register(TrendResult)
class TrendResultAdmin(admin.ModelAdmin):
    list_display = ('code','name','sector_jp','last_price','dir_d','dir_w','dir_m','rs_index','vol_spike','as_of','updated_at')
    list_filter  = ('sector_jp','as_of')
    search_fields = ('code','name')