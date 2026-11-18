from django.apps import AppConfig


class AccountsConfig(AppConfig):
    name = "apps.accounts"
    label = "accounts"

    def ready(self) -> None:
        from axes.signals import user_locked_out
        from django.contrib.auth.signals import user_logged_in

        from apps.accounts.services import on_user_locked_out
        from apps.accounts.sessions import on_user_logged_in

        user_logged_in.connect(on_user_logged_in, dispatch_uid="accounts_record_session")
        user_locked_out.connect(on_user_locked_out, dispatch_uid="accounts_audit_lockout")
