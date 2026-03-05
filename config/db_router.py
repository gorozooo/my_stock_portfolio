# =========================================================
# [FILE] db_router.py
# [PATH] <project_root>/config/db_router.py
#
# このファイルは何？
# - DjangoのDBルーティングを行うファイルです。
# - autotradeアプリの「一部モデルだけ」を autotrade.sqlite3 に逃がします。
#
# プロが必ず入れる安全装置（今回の要点）
# - User(FK)を持つモデルを別SQLiteに飛ばすと「auth_userが無い」で必ず死ぬ。
# - よって、別DBに行くのは “FK無し＆高頻度更新” の DailyState だけに限定する。
# - allow_relation で「DailyState とそれ以外」の関係を明示的に拒否し、
#   事故（クロスDB参照）を早期に潰す。
# - allow_migrate をモデル単位で縛って、テーブルが間違ったDBに作られる事故を防ぐ。
# =========================================================

from __future__ import annotations


class AutotradeRouter:
    """
    ✅ 方針：
    - autotrade.sqlite3：AutoTradeDailyState のみ
    - db.sqlite3（default）：それ以外の autotrade モデル全部（Tuning/Snapshot/Execution等）
    """

    app_label = "autotrade"
    db_name = "autotrade"

    # autotrade.sqlite3 に逃がすモデル（model_name は小文字）
    AUTOTRADE_DB_MODELS = {
        "autotradedailystate",
    }

    def _is_autotrade_model(self, model) -> bool:
        return getattr(model._meta, "app_label", None) == self.app_label

    def _use_autotrade_db(self, model) -> bool:
        if not self._is_autotrade_model(model):
            return False
        model_name = (getattr(model._meta, "model_name", "") or "").lower()
        return model_name in self.AUTOTRADE_DB_MODELS

    def db_for_read(self, model, **hints):
        # DailyState だけ autotrade DB
        if self._use_autotrade_db(model):
            return self.db_name
        # それ以外は default（None ＝ Djangoがdefaultに落とす）
        return None

    def db_for_write(self, model, **hints):
        # DailyState だけ autotrade DB
        if self._use_autotrade_db(model):
            return self.db_name
        # それ以外は default
        return None

    def allow_relation(self, obj1, obj2, **hints):
        """
        ✅ 安全装置：
        - DailyState は autotrade DB に居る
        - その他の autotrade モデルは default DB に居る
        → これらを “関係あり” とするとクロスDB参照事故の温床になるので拒否。
        """
        a1 = getattr(obj1._meta, "app_label", None)
        a2 = getattr(obj2._meta, "app_label", None)

        if a1 != self.app_label and a2 != self.app_label:
            return None  # autotrade関係ないのでDjangoに任せる

        # どちらかが autotrade アプリのモデル
        m1 = (getattr(obj1._meta, "model_name", "") or "").lower()
        m2 = (getattr(obj2._meta, "model_name", "") or "").lower()

        is1_daily = (a1 == self.app_label and m1 in self.AUTOTRADE_DB_MODELS)
        is2_daily = (a2 == self.app_label and m2 in self.AUTOTRADE_DB_MODELS)

        # DailyState 同士ならOK
        if is1_daily and is2_daily:
            return True

        # DailyState とそれ以外の関係は拒否（クロスDB事故防止）
        if is1_daily != is2_daily:
            return False

        # DailyState以外（どちらもdefault想定）はOK
        return True

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        """
        ✅ 安全装置：
        - DailyState のテーブルだけ autotrade DB に作る
        - autotrade のそれ以外は default に作る
        - autotrade以外のアプリは default に作る
        """
        model_name = (model_name or "").lower()

        if app_label == self.app_label:
            if model_name in self.AUTOTRADE_DB_MODELS:
                return db == self.db_name
            return db == "default"

        return db == "default"