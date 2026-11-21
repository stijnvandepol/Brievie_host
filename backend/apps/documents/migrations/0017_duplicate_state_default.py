from django.db import migrations

# A database default too, so a raw INSERT that leaves the column out (a seed, a restore
# script) keeps working. Not `db_default`: the model's default stays a Python one, so
# Django never inserts with RETURNING (as workspaces 0008).
SET_DEFAULT = "ALTER TABLE documents_duplicatecandidate ALTER COLUMN state SET DEFAULT 'open'"
DROP_DEFAULT = "ALTER TABLE documents_duplicatecandidate ALTER COLUMN state DROP DEFAULT"


class Migration(migrations.Migration):
    dependencies = [
        ("documents", "0016_duplicates"),
    ]

    operations = [
        migrations.RunSQL(SET_DEFAULT, DROP_DEFAULT),
    ]
