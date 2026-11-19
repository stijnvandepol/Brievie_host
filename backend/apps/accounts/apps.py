from django.apps import AppConfig


class AccountsConfig(AppConfig):
    name = "apps.accounts"
    label = "accounts"

    def ready(self) -> None:
        # No `user_logged_in` receiver records sessions: only `start_session` does, so a
        # direct `login()` gets no record and the middleware signs it out.
        from axes.signals import user_locked_out

        from apps.accounts.services import on_user_locked_out

        user_locked_out.connect(on_user_locked_out, dispatch_uid="accounts_audit_lockout")
