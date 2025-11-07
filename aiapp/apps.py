from django.apps import AppConfig

class AiappConfig(AppConfig):
    name = "aiapp"
    verbose_name = "AI Auto Investing App"

    def ready(self):
        # 将来：シグナルや起動時チェックをここに
        pass
