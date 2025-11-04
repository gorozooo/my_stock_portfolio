from django.urls import path
from ai.presentation.views.home import AIHomeView

app_name = 'ai'

urlpatterns = [
    path('', AIHomeView.as_view(), name='home'),
]