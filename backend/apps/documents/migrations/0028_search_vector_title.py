"""An own document is found by its title (archive spec AR9): `title` joins the generated
`search_vector` with the sender's weight A.

PostgreSQL cannot change a generated column's expression, and Django refuses to alter a
`GeneratedField`: the GIN index goes first, then the column, which is added again with the
new expression, then the index. This rewrites the documents table once and rebuilds the
index (seconds at home, minutes at 100,000 documents); the release notes say the update
takes a little longer to apply. Reversible the same way back."""

import django.contrib.postgres.indexes
import django.contrib.postgres.search
from django.db import migrations, models


def _vector(field: str, weight: str) -> django.contrib.postgres.search.SearchVector:
    return django.contrib.postgres.search.SearchVector(field, config="dutch", weight=weight)


def _combined(
    left: "django.contrib.postgres.search.SearchVector"
    " | django.contrib.postgres.search.CombinedSearchVector",
    right: django.contrib.postgres.search.SearchVector,
) -> django.contrib.postgres.search.CombinedSearchVector:
    return django.contrib.postgres.search.CombinedSearchVector(
        left, "||", right, django.contrib.postgres.search.SearchConfig("dutch")
    )


def _field() -> models.GeneratedField:
    """Sender and title A, summary B, text C: the model's expression."""
    expression = _combined(
        _combined(
            _combined(_vector("sender", "A"), _vector("title", "A")), _vector("summary", "B")
        ),
        _vector("ocr_text", "C"),
    )
    return models.GeneratedField(
        db_persist=True,
        expression=expression,
        output_field=django.contrib.postgres.search.SearchVectorField(),
    )


def _index() -> django.contrib.postgres.indexes.GinIndex:
    return django.contrib.postgres.indexes.GinIndex(
        fields=["search_vector"], name="documents_document_search"
    )


class Migration(migrations.Migration):
    dependencies = [
        ("documents", "0027_kind_indexes"),
    ]

    operations = [
        migrations.RemoveIndex(model_name="document", name="documents_document_search"),
        migrations.RemoveField(model_name="document", name="search_vector"),
        migrations.AddField(
            model_name="document",
            name="search_vector",
            field=_field(),
        ),
        migrations.AddIndex(model_name="document", index=_index()),
    ]
