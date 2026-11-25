#!/usr/bin/env python
import os
import sys


def main() -> None:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "brievie.settings")
    # Before any secret is read: no other process of this user may read this one's
    # environment or memory (the workers; apps.common.hardening).
    from apps.common.hardening import forbid_inspection

    forbid_inspection()
    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
