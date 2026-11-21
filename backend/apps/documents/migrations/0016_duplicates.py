"""Duplicate letters: the pair table `DuplicateCandidate` (ordered, one row per pair, a
partial index on open pairs) with RLS enabled and forced plus the pair policy (both
letters readable), and on the history the kinds `merged`, `duplicate_dismissed`,
`task_moved`, the origin `merge` and `related_document`."""

import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

from apps.workspaces.rls import enable_rls, restrict_to_visible_document_pair


class Migration(migrations.Migration):
    dependencies = [
        ("documents", "0015_trash"),
        ("workspaces", "0007_visibility_functions"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="documentevent",
            name="related_document",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="+",
                to="documents.document",
            ),
        ),
        migrations.AlterField(
            model_name="documentevent",
            name="kind",
            field=models.CharField(
                choices=[
                    ("label_added", "Label Added"),
                    ("label_removed", "Label Removed"),
                    ("addressee_added", "Addressee Added"),
                    ("addressee_removed", "Addressee Removed"),
                    ("confirmed", "Confirmed"),
                    ("task_completed", "Task Completed"),
                    ("task_reopened", "Task Reopened"),
                    ("reopened", "Reopened"),
                    ("made_private", "Made Private"),
                    ("made_public", "Made Public"),
                    ("share_added", "Share Added"),
                    ("share_removed", "Share Removed"),
                    ("access_granted", "Access Granted"),
                    ("emergency_opened", "Emergency Opened"),
                    ("private_skipped", "Private Skipped"),
                    ("trashed", "Trashed"),
                    ("restored", "Restored"),
                    ("merged", "Merged"),
                    ("duplicate_dismissed", "Duplicate Dismissed"),
                    ("task_moved", "Task Moved"),
                ],
                max_length=30,
            ),
        ),
        migrations.AlterField(
            model_name="documentevent",
            name="origin",
            field=models.CharField(
                choices=[
                    ("user", "User"),
                    ("rule", "Rule"),
                    ("suggestion", "Suggestion"),
                    ("undo", "Undo"),
                    ("ai", "Ai"),
                    ("name_match", "Name Match"),
                    ("rule_autopilot", "Rule Autopilot"),
                    ("merge", "Merge"),
                ],
                default="user",
                max_length=20,
            ),
        ),
        migrations.CreateModel(
            name="DuplicateCandidate",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "state",
                    models.CharField(
                        choices=[
                            ("open", "Open"),
                            ("merged", "Merged"),
                            ("dismissed", "Dismissed"),
                            ("withdrawn", "Withdrawn"),
                        ],
                        default="open",
                        max_length=20,
                    ),
                ),
                ("text_similarity", models.DecimalField(decimal_places=3, max_digits=4)),
                ("number_overlap", models.DecimalField(decimal_places=3, max_digits=4)),
                ("decided_at", models.DateTimeField(blank=True, null=True)),
                (
                    "decided_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "first",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="+",
                        to="documents.document",
                    ),
                ),
                (
                    "kept",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to="documents.document",
                    ),
                ),
                (
                    "second",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
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
                "indexes": [
                    models.Index(
                        condition=models.Q(("state", "open")),
                        fields=["workspace", "first", "second"],
                        name="documents_duplicate_open",
                    )
                ],
                "constraints": [
                    models.CheckConstraint(
                        condition=models.Q(("first__lt", models.F("second"))),
                        name="documents_duplicate_ordered",
                    ),
                    models.UniqueConstraint(
                        fields=("first", "second"), name="documents_duplicate_pair"
                    ),
                ],
            },
        ),
        enable_rls("documents_duplicatecandidate"),
        restrict_to_visible_document_pair("documents_duplicatecandidate"),
    ]
