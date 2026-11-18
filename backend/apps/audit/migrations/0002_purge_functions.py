from django.db import migrations

# Old audit rows are removed only through these functions (D 11). The app role may not
# delete audit rows itself; the functions run as their owner, the schema owner, which
# forced row-level security binds as well, so the workspace function enters the
# workspace's context itself and puts the previous one back. Both refuse a cut-off
# younger than 90 days, whatever the caller asks.
PURGE_FUNCTIONS = """
CREATE FUNCTION brievie_purge_workspace_audit(p_workspace uuid, p_cutoff timestamptz)
RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
  previous text;
  removed integer;
BEGIN
  IF p_workspace IS NULL OR p_cutoff IS NULL OR p_cutoff > now() - interval '90 days' THEN
    RAISE EXCEPTION 'audit rows are kept at least 90 days' USING ERRCODE = '22023';
  END IF;
  previous := current_setting('brievie.workspace_id', true);
  PERFORM set_config('brievie.workspace_id', p_workspace::text, true);
  DELETE FROM audit_workspaceauditevent
    WHERE workspace_id = p_workspace AND created_at < p_cutoff;
  GET DIAGNOSTICS removed = ROW_COUNT;
  PERFORM set_config('brievie.workspace_id', coalesce(previous, ''), true);
  RETURN removed;
END
$$;

CREATE FUNCTION brievie_purge_server_audit(p_cutoff timestamptz)
RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
  removed integer;
BEGIN
  IF p_cutoff IS NULL OR p_cutoff > now() - interval '90 days' THEN
    RAISE EXCEPTION 'audit rows are kept at least 90 days' USING ERRCODE = '22023';
  END IF;
  DELETE FROM audit_serverauditevent WHERE created_at < p_cutoff;
  GET DIAGNOSTICS removed = ROW_COUNT;
  RETURN removed;
END
$$;

REVOKE ALL ON FUNCTION brievie_purge_workspace_audit(uuid, timestamptz) FROM PUBLIC;
REVOKE ALL ON FUNCTION brievie_purge_server_audit(timestamptz) FROM PUBLIC;
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'brievie_app') THEN
    GRANT EXECUTE ON FUNCTION brievie_purge_workspace_audit(uuid, timestamptz) TO brievie_app;
    GRANT EXECUTE ON FUNCTION brievie_purge_server_audit(timestamptz) TO brievie_app;
  END IF;
END $$;
"""

DROP_PURGE_FUNCTIONS = """
DROP FUNCTION brievie_purge_workspace_audit(uuid, timestamptz);
DROP FUNCTION brievie_purge_server_audit(timestamptz);
"""


class Migration(migrations.Migration):
    dependencies = [
        ("audit", "0001_initial"),
    ]

    operations = [
        migrations.RunSQL(sql=PURGE_FUNCTIONS, reverse_sql=DROP_PURGE_FUNCTIONS),
    ]
