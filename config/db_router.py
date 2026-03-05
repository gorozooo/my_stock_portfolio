# =========================================================
# [FILE] db_router.py
# [PATH] <project_root>/config/db_router.py
#
# このファイルは何？
# - DjangoのDBルーティングを行うファイルです。
# - autotradeアプリのモデルだけ、autotrade.sqlite3 を使うように分岐します。
# =========================================================

class AutotradeRouter:
    app_label = "autotrade"
    db_name = "autotrade"

    def db_for_read(self, model, **hints):
        if getattr(model._meta, "app_label", None) == self.app_label:
            return self.db_name
        return None

    def db_for_write(self, model, **hints):
        if getattr(model._meta, "app_label", None) == self.app_label:
            return self.db_name
        return None

    def allow_relation(self, obj1, obj2, **hints):
        a1 = getattr(obj1._meta, "app_label", None)
        a2 = getattr(obj2._meta, "app_label", None)
        if a1 == self.app_label or a2 == self.app_label:
            return True
        return None

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        if app_label == self.app_label:
            return db == self.db_name
        return db == "default"