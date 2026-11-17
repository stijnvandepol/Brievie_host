from datetime import datetime

from django.core.management.base import BaseCommand
from django.template.defaultfilters import filesizeformat
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.backup import services
from apps.backup.models import BackupRun


def _when(moment: datetime | None) -> str:
    if moment is None:
        return "?"
    return timezone.localtime(moment).strftime("%d-%m-%Y %H:%M")


def _destination(kind: str) -> str:
    labels = {
        "": _("niet ingesteld"),
        "folder": _("map (schijf)"),
        "rest": _("REST-server"),
        "s3": _("S3-opslag"),
    }
    return labels.get(kind, _("onjuist ingesteld"))


class Command(BaseCommand):
    help = "Show versions, the destination kind and the last back-up runs (no addresses or paths)."

    def handle(self, *args: object, **options: object) -> None:
        status = services.status_summary()
        never = _("nog nooit")
        lines = [
            _("Brievie: %(v)s") % {"v": status.brievie_version},
            _("PostgreSQL: %(v)s") % {"v": status.postgres_version},
            _("pg_dump: %(v)s") % {"v": status.pg_dump_version},
            _("restic: %(v)s") % {"v": status.restic_version},
            _("Bestemming: %(v)s") % {"v": _destination(status.destination_kind)},
        ]
        success = status.last_success
        if success is None:
            lines.append(_("Laatste geslaagde back-up: %(v)s") % {"v": never})
        else:
            lines.append(
                _("Laatste geslaagde back-up: %(when)s (%(size)s)")
                % {
                    "when": _when(success.finished_at or success.created_at),
                    "size": filesizeformat(success.size_bytes or 0),
                }
            )
        check = status.last_check
        lines.append(
            _("Laatste geslaagde controle: %(v)s")
            % {"v": never if check is None else _when(check.finished_at or check.created_at)}
        )
        failure = status.last_failure
        if failure is not None:
            lines.append(
                _("Laatste fout: %(when)s: %(reason)s")
                % {
                    "when": _when(failure.finished_at or failure.created_at),
                    "reason": failure.reason,
                }
            )
        interrupted = status.last_interrupted
        if interrupted is not None:
            lines.append(
                _("Laatst afgebroken: %(when)s: %(reason)s")
                % {
                    "when": _when(interrupted.finished_at or interrupted.created_at),
                    "reason": interrupted.reason,
                }
            )
        if status.open_run is not None:
            lines.append(_open_run_line(status.open_run))
        self.stdout.write("\n".join(lines))


def _open_run_line(run: BackupRun) -> str:
    what = _("controle") if run.kind == BackupRun.Kind.CHECK else _("back-up")
    if run.status == BackupRun.Status.QUEUED:
        return _("Wacht op de back-updienst: %(what)s, aangevraagd %(when)s") % {
            "what": what,
            "when": _when(run.created_at),
        }
    return _("Bezig: %(what)s, gestart %(when)s") % {"what": what, "when": _when(run.started_at)}
