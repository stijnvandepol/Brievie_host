"""Which documents a member may see: the one seam of every member-facing read.

The list, the inbox counts, a document's detail and search all start from
`visible_documents`. When rights per department or private post arrive, this function
changes and nothing else. Callers refer to it as `visibility.visible_documents` so the
seam stays one name.
"""

from django.db.models import QuerySet

from apps.documents.models import Document
from apps.workspaces.models import Membership


def visible_documents(membership: Membership) -> QuerySet[Document]:
    """The documents `membership` may see. Today: all documents of its workspace.

    Row-level security still binds the workspace the request entered; this filter is
    the rights model on top of it.
    """
    return Document.objects.filter(workspace_id=membership.workspace_id)
