"""\"Brief afhandelen\" (letter status spec S 7, ruling LS6): a person ticks off every open
task of a letter after a confirmation the server checks.

The client sends the ids of the open tasks it showed and, when a payment is among them,
that the person confirmed it was made or arranged. The server completes nothing when the
set differs (a task was added, ticked off or reopened meanwhile) or a payment is not
confirmed, so no client (web, iPhone, an old app) can tick off a payment without a person
having confirmed it.

Lock order: the caller's membership (its role read under the lock), the letter
(`FOR NO KEY UPDATE`), its open tasks (`FOR UPDATE`).
"""

from uuid import UUID

from django.db import transaction
from django.utils.translation import gettext as _

from apps.accounts.models import User
from apps.common.errors import ApiError
from apps.documents import visibility
from apps.documents.models import Document
from apps.tasks import letter_sync
from apps.tasks import services as task_services
from apps.tasks.models import Task
from apps.workspaces.models import Membership


def handle_document(
    *,
    membership: Membership,
    user: User,
    document_id: UUID,
    task_ids: list[UUID],
    payments_confirmed: bool,
) -> Document:
    """Complete exactly the open tasks named by `task_ids` (all open ones, waiting included),
    each through the one completion path (`task_completed`, with the person), then one
    `letter_handled` line with the person. An already handled letter with no ids changes
    nothing and answers the same."""
    with transaction.atomic():
        task_services.lock_editor(membership.workspace_id, user.id)
        document = (
            visibility.visible_documents(membership)
            .select_for_update(no_key=True, of=("self",))
            .filter(id=document_id)
            .first()
        )
        if document is None:
            raise ApiError(404, "document_not_found", _("We kunnen dit document niet vinden."))
        visibility.require_editable(document)
        if document.status != Document.Status.DONE:
            raise ApiError(409, "document_not_reviewed", _("Deze brief is nog niet nagekeken."))
        open_tasks = list(
            Task.objects.select_for_update(of=("self",))
            .filter(document=document, status=Task.Status.OPEN)
            .order_by("created_at", "id")
        )
        if {task.id for task in open_tasks} != set(task_ids):
            raise ApiError(
                409,
                "tasks_changed",
                _("De taken van deze brief zijn net veranderd. Bekijk ze opnieuw."),
            )
        if not open_tasks:
            return document
        if not payments_confirmed and any(task.type == "pay" for task in open_tasks):
            raise ApiError(
                409,
                "payment_not_confirmed",
                _("Vink aan dat de betaling is gedaan of geregeld."),
            )
        for task in open_tasks:
            task_services.complete_locked(task, user)
        letter_sync.sync(document, actor=user)
    return document
