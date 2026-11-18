"""Background job of the devices app: forgetting pairing material nobody can use."""

from procrastinate.contrib.django import app

from apps.devices import services
from apps.pipeline.jobs import QUEUE


@app.periodic(cron="*/5 * * * *")
# Every worker defers every periodic task; the queueing lock keeps at most one waiting.
@app.task(
    name="forget_expired_pairing_material",
    queue=QUEUE,
    queueing_lock="forget_expired_pairing_material",
)
def forget_expired_pairing_material(timestamp: int) -> None:
    """Clear the sealed proof key of every expired pairing code and delete expired
    challenges, also when nobody makes a new code or challenge."""
    services.forget_expired_pairing_material()
