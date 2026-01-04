# advisor/apps.py
from django.apps import AppConfig

class AdvisorConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "advisor"
    verbose_name = "Advisor"

    def ready(self):
        # モデル＆adminを明示ロード（ファイル名が models.py / admin.py 以外でも確実に読み込む）
        try:
            from . import models_policy  # noqa: F401
        except Exception:
            pass
            
        try:
            from . import admin_policy  # noqa: F401
        except Exception:
            pass
            
        try:
            from . import models_notify  # noqa: F401
        except Exception:
            pass
        
        try:
            from . import models_order  # noqa: F401
        except Exception:
            pass
        