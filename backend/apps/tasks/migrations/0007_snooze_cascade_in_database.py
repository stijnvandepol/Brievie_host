"""A snooze goes with its task, whoever deletes the task (security review of PR #50).

Django cascades in the application, as the caller: under the own-rows policy that deletes
only the caller's snoozes and leaves the others pointing at a deleted task (a failed commit).
The database's own `ON DELETE CASCADE` is not bound by row-level security, so no code path
can forget it."""

from django.db import migrations

FORWARD = """
DO $$
DECLARE name text;
BEGIN
    SELECT conname INTO name FROM pg_constraint
    WHERE conrelid = 'tasks_tasksnooze'::regclass AND contype = 'f'
      AND confrelid = 'tasks_task'::regclass;
    EXECUTE format('ALTER TABLE tasks_tasksnooze DROP CONSTRAINT %I', name);
    ALTER TABLE tasks_tasksnooze ADD CONSTRAINT tasks_tasksnooze_task_cascade
        FOREIGN KEY (task_id) REFERENCES tasks_task (id)
        ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED;
END $$;
"""

BACKWARD = """
ALTER TABLE tasks_tasksnooze DROP CONSTRAINT tasks_tasksnooze_task_cascade;
ALTER TABLE tasks_tasksnooze ADD CONSTRAINT tasks_tasksnooze_task_id_fk_tasks_task
    FOREIGN KEY (task_id) REFERENCES tasks_task (id) DEFERRABLE INITIALLY DEFERRED;
"""


class Migration(migrations.Migration):
    dependencies = [("tasks", "0006_waiting_and_snooze")]
    operations = [migrations.RunSQL(FORWARD, BACKWARD)]
