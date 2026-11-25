"""`MemberExport.scope`: `mine` (a member's own post, every existing row) or `all` (an
admin's export of everything they may read, archive spec S 9). A database default too,
for raw inserts; not `db_default` (as documents 0017)."""

from typing import Final

from django.db import migrations, models

SET_DEFAULT: Final = "ALTER TABLE documents_memberexport ALTER COLUMN scope SET DEFAULT 'mine'"
DROP_DEFAULT: Final = "ALTER TABLE documents_memberexport ALTER COLUMN scope DROP DEFAULT"


class Migration(migrations.Migration):
    dependencies = [
        ("documents", "0030_letter_facts_letters_only"),
    ]

    operations = [
        migrations.AddField(
            model_name="memberexport",
            name="scope",
            field=models.CharField(
                choices=[("mine", "Mine"), ("all", "All")], default="mine", max_length=10
            ),
        ),
        migrations.RunSQL(SET_DEFAULT, DROP_DEFAULT),
    ]
