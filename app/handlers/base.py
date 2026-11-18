"""The contract every scheduler type implements.

The scheduler core (tick, queue, run executor, API) is generic. Everything that is
specific to *what* a scheduler produces lives in a handler:

* ``config_model`` - validates ``schedulers.config`` (podcast: ``{"topic": ...}``)
* ``stages``       - ordered steps of a run, each executed as its own queued job
* ``run_stage``    - does the work of one stage and returns data to merge into ``output``
* ``present_output`` - shapes ``output`` for API clients (e.g. private path -> signed URL)
* ``on_completed`` - side effects after the last stage (e.g. email the user)

Adding a new type = a new ``SchedulerType`` value + a handler class + one line in
``registry.py`` (+ allowing the value in the DB ``type`` check). No core changes.
"""
from abc import ABC, abstractmethod
from typing import ClassVar

from pydantic import BaseModel, ValidationError

from app.core.exceptions import InvalidRequestError
from app.models.scheduler import SchedulerType
from app.models.scheduler_run import RunContext


class SchedulerHandler(ABC):
    """Base class for scheduler types."""

    type: ClassVar[SchedulerType]
    description: ClassVar[str]
    config_model: ClassVar[type[BaseModel]]
    stages: ClassVar[tuple[str, ...]]

    # ---------- config ----------

    def validate_config(self, config: dict) -> dict:
        """Validate and normalise a config dict.

        Raises:
            InvalidRequestError: with the validation messages (HTTP 422).
        """
        try:
            return self.config_model.model_validate(config).model_dump(mode="json")
        except ValidationError as exc:
            details = "; ".join(f"config.{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
            raise InvalidRequestError(details) from exc

    def default_name(self, config: dict) -> str:
        """Display name used when the user doesn't provide one."""
        return self.type.value.title()

    # ---------- stages ----------

    @property
    def first_stage(self) -> str:
        return self.stages[0]

    def next_stage(self, stage: str) -> str | None:
        """The stage after ``stage``, or None if it was the last one."""
        index = self.stages.index(stage)
        return self.stages[index + 1] if index + 1 < len(self.stages) else None

    @abstractmethod
    async def run_stage(self, stage: str, ctx: RunContext) -> dict:
        """Execute one stage and return the data to merge into ``scheduler_runs.output``.

        Raise ``PermanentJobError`` when retrying cannot help; any other exception
        is treated as transient and retried by the queue.
        """

    # ---------- results ----------

    async def present_output(self, output: dict) -> dict:
        """Shape ``output`` for the API (default: unchanged)."""
        return output

    async def on_completed(self, ctx: RunContext, output: dict) -> None:
        """Side effects after the last stage succeeds (default: none)."""
