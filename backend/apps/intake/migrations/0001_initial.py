"""The scan folder's tables (intake plan Task 9), each with forced row security."""

import uuid

import django.db.models.deletion
import django.db.models.functions.text
from django.db import migrations, models

from apps.workspaces.rls import enable_rls, restrict_to_visible_documents


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("documents", "0035_watch_folder_origin"),
        ("people", "0006_person_frozen_account"),
        ("workspaces", "0010_unread_and_lingering"),
    ]

    operations = [
        migrations.CreateModel(
            name="ScanFile",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("relative_path", models.CharField(max_length=400)),
                ("size", models.BigIntegerField()),
                ("mtime_ns", models.BigIntegerField()),
                ("device", models.BigIntegerField()),
                ("inode", models.BigIntegerField()),
                ("stable_since", models.DateTimeField()),
                (
                    "state",
                    models.CharField(
                        choices=[("seen", "Seen"), ("imported", "Imported"), ("failed", "Failed")],
                        default="seen",
                        max_length=10,
                    ),
                ),
                ("reason", models.CharField(blank=True, default="", max_length=40)),
                ("removed_at", models.DateTimeField(blank=True, null=True)),
                (
                    "document",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to="documents.document",
                    ),
                ),
                (
                    "workspace",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE, to="workspaces.workspace"
                    ),
                ),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(
                        fields=("workspace", "relative_path", "device", "inode"),
                        name="intake_scanfile_unique",
                    )
                ],
            },
        ),
        migrations.CreateModel(
            name="ScanFolder",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("name", models.CharField(max_length=60)),
                ("added_by_admin", models.BooleanField(default=False)),
                (
                    "state",
                    models.CharField(
                        choices=[
                            ("active", "Active"),
                            ("paused", "Paused"),
                            ("stopped", "Stopped"),
                            ("gone", "Gone"),
                        ],
                        default="active",
                        max_length=10,
                    ),
                ),
                (
                    "person",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to="people.person",
                    ),
                ),
                (
                    "workspace",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE, to="workspaces.workspace"
                    ),
                ),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(
                        condition=models.Q(("person__isnull", False)),
                        fields=("workspace", "person"),
                        name="intake_scanfolder_person_unique",
                    ),
                    models.UniqueConstraint(
                        models.F("workspace"),
                        django.db.models.functions.text.Lower("name"),
                        name="intake_scanfolder_name_unique",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="ScanFolderSettings",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("enabled", models.BooleanField(default=False)),
                ("folder_name", models.CharField(blank=True, default="", max_length=60)),
                ("per_person", models.BooleanField(default=False)),
                ("check_state", models.CharField(default="unknown", max_length=20)),
                ("owner_uid", models.IntegerField(default=0)),
                ("owner_gid", models.IntegerField(default=0)),
                ("checked_at", models.DateTimeField(blank=True, null=True)),
                ("unknown_folders", models.JSONField(blank=True, default=list)),
                (
                    "workspace",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE, to="workspaces.workspace"
                    ),
                ),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(
                        fields=("workspace",), name="intake_scanfoldersettings_workspace_unique"
                    ),
                    models.UniqueConstraint(
                        condition=models.Q(("folder_name", ""), _negated=True),
                        fields=("folder_name",),
                        name="intake_scanfoldersettings_name_unique",
                    ),
                ],
            },
        ),
        enable_rls("intake_scanfile"),
        # A row of an imported file follows its letter (the job runs as the system).
        restrict_to_visible_documents("intake_scanfile", nullable=True),
        enable_rls("intake_scanfolder"),
        enable_rls("intake_scanfoldersettings"),
    ]
