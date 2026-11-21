"""The AI's proposal of a person nobody knows yet (Mensen, Besluit B3): its own table under
the workspace policy, readable only with its letter."""

import uuid
from decimal import Decimal

import django.db.models.deletion
from django.db import migrations, models

from apps.workspaces.rls import enable_rls, restrict_to_visible_documents


class Migration(migrations.Migration):
    dependencies = [
        ("documents", "0019_review_index"),
        ("people", "0003_departments"),
        ("workspaces", "0008_workspace_summary_language"),
    ]

    operations = [
        migrations.CreateModel(
            name="NewPersonSuggestion",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("name", models.CharField(max_length=150)),
                ("name_key", models.CharField(max_length=150)),
                ("quote", models.TextField(blank=True, default="")),
                (
                    "confidence",
                    models.DecimalField(decimal_places=3, default=Decimal("0"), max_digits=4),
                ),
                (
                    "state",
                    models.CharField(
                        choices=[
                            ("open", "Open"),
                            ("accepted", "Accepted"),
                            ("dismissed", "Dismissed"),
                        ],
                        default="open",
                        max_length=20,
                    ),
                ),
                (
                    "document",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="new_person_suggestions",
                        to="documents.document",
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
                        fields=("document", "name_key"), name="documents_newpersonsuggestion_unique"
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("confidence__gte", 0), ("confidence__lte", 1)),
                        name="documents_newpersonsuggestion_confidence_range",
                    ),
                ],
            },
        ),
        enable_rls("documents_newpersonsuggestion"),
        restrict_to_visible_documents("documents_newpersonsuggestion"),
    ]
