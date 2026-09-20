from apps.accounts.models import WorkerServiceRate
from .models import Order


def create_order_with_rate(*, worker_percentage, **fields):
    """Create an order with the current rate for its worker and service."""
    WorkerServiceRate.objects.update_or_create(
        worker=fields["employee"], service=fields["service"],
        defaults={"worker_percentage": worker_percentage, "active": True},
    )
    return Order.objects.create(**fields)
