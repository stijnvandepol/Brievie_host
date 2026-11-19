from django.db import migrations

# pg_temp explicitly last in the purge functions' search path, as PostgreSQL's
# documentation advises for SECURITY DEFINER functions: otherwise it is searched
# first, and a caller's temporary table could stand in for an audit table.
SET_PATH = """
ALTER FUNCTION brievie_purge_workspace_audit(uuid, timestamptz)
  SET search_path = pg_catalog, public, pg_temp;
ALTER FUNCTION brievie_purge_server_audit(timestamptz)
  SET search_path = pg_catalog, public, pg_temp;
"""

RESET_PATH = """
ALTER FUNCTION brievie_purge_workspace_audit(uuid, timestamptz)
  SET search_path = pg_catalog, public;
ALTER FUNCTION brievie_purge_server_audit(timestamptz)
  SET search_path = pg_catalog, public;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("audit", "0002_purge_functions"),
    ]

    operations = [
        migrations.RunSQL(sql=SET_PATH, reverse_sql=RESET_PATH),
    ]
