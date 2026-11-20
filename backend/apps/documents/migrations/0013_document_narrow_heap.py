"""Keep a letter's text and search vector out of the table's main pages.

The lists and the inbox counts read `documents_document` row by row; since search added
the generated `search_vector` beside `ocr_text`, a row carries both inline (about 3 kB)
and those scans took about 1.7 times as long at 100,000 letters (DECISIONS 2026-11-20).
With `toast_tuple_target` at its minimum, PostgreSQL moves the large values of a row it
writes out of line, so the main pages hold only the short columns. Rows already stored
move when they are next written. Reversible: the setting is reset.
"""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("documents", "0012_automatic_link_indexes"),
    ]

    operations = [
        migrations.RunSQL(
            "ALTER TABLE documents_document SET (toast_tuple_target = 128)",
            reverse_sql="ALTER TABLE documents_document RESET (toast_tuple_target)",
        ),
    ]
