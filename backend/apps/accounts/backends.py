from django.contrib.auth.backends import ModelBackend
from .access import role_of

class RoleBackend(ModelBackend):
    def has_perm(self, user_obj, perm, obj=None):
        if not user_obj.is_active or obj is not None:
            return False
        if user_obj.is_superuser:
            return True
        role = role_of(user_obj)
        app, _, code = perm.partition(".")
        if role == "manager":
            return app in ("accounts", "customers", "leads", "orders", "services", "activity", "workflows")
        if role == "operator":
            return app in ("customers", "leads", "orders", "services") and code.startswith(("view_", "add_", "change_")) and code != "view_analytics"
        return False
