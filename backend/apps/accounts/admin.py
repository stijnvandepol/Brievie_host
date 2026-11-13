from typing import ClassVar

from django.contrib import admin

from apps.accounts.models import User


@admin.register(User)
class UserAdmin(admin.ModelAdmin[User]):
    list_display = ("email", "name", "is_active", "is_staff")
    search_fields = ("email", "name")
    ordering = ("email",)
    # The password hash is never shown or edited here.
    fields = (
        "email",
        "name",
        "language",
        "explanation_language",
        "is_active",
        "is_staff",
        "is_superuser",
        "groups",
        "user_permissions",
        "last_login",
    )
    readonly_fields: ClassVar[tuple[str, ...]] = ("last_login",)
