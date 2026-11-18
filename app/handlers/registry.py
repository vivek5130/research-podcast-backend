"""Maps each ``SchedulerType`` to its handler instance."""
from app.core.exceptions import InvalidRequestError
from app.handlers.base import SchedulerHandler
from app.handlers.podcast.handler import PodcastHandler
from app.models.scheduler import SchedulerType

_HANDLERS: dict[SchedulerType, SchedulerHandler] = {
    SchedulerType.PODCAST: PodcastHandler(),
}


def get_handler(scheduler_type: SchedulerType | str) -> SchedulerHandler:
    """Return the handler for a type.

    Raises:
        InvalidRequestError: if no handler is registered for it.
    """
    try:
        return _HANDLERS[SchedulerType(scheduler_type)]
    except (KeyError, ValueError) as exc:
        raise InvalidRequestError(f"Unsupported scheduler type: {scheduler_type}") from exc


def all_handlers() -> list[SchedulerHandler]:
    """Every registered handler (used by GET /api/schedulers/types)."""
    return list(_HANDLERS.values())
