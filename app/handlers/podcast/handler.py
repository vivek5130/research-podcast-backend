"""Podcast scheduler: research a topic daily and deliver an audio briefing of the chosen length (15 s - 10 min).

Stages:
    research   -> LangGraph agent (Tavily + Gemini) writes the script
                  output += {title, script, word_count, sources}
    synthesize -> Gnani TTS -> MP3 uploaded to private storage
                  output += {audio_path, duration_sec, audio_format}
On completion the user is emailed a link to the run page.
"""
import logging

from app.core.exceptions import InvalidRequestError, PermanentJobError
from app.core.tracing import node_scope
from app.handlers.base import SchedulerHandler
from app.handlers.podcast.agent import guardrails
from app.handlers.podcast.agent.evaluation import duration_accuracy
from app.handlers.podcast.agent.graph import run_research_agent
from app.handlers.podcast.config import PodcastConfig, PodcastStage
from app.models.scheduler import SchedulerType
from app.models.scheduler_run import RunContext
from app.services import email, storage, tts

log = logging.getLogger(__name__)

class PodcastHandler(SchedulerHandler):
    """Daily research-to-podcast scheduler."""

    type = SchedulerType.PODCAST
    description = "Researches a topic from current news and delivers an audio podcast (15 seconds to 10 minutes)."
    config_model = PodcastConfig
    stages = (PodcastStage.RESEARCH.value, PodcastStage.SYNTHESIZE.value)

    def validate_config(self, config: dict) -> dict:
        """Schema validation plus guardrails when a schedule is saved.

        * Content policy: a blocked topic is rejected (422 ``CONTENT_POLICY``) with the reason.
        * PII: emails, phone numbers, IDs... are stripped from the topic before it is stored,
          so they never reach the database, the schedule name, search or the LLM.
        """
        validated = super().validate_config(config)
        violations = guardrails.content_policy().find(validated["topic"])
        if violations:
            raise InvalidRequestError(guardrails.policy_message(violations), code="CONTENT_POLICY")
        redaction = guardrails.redact_input(validated["topic"])
        if redaction.types:
            if len(redaction.text) < 3:
                raise InvalidRequestError(
                    f"The topic only contained personal information ({guardrails.describe_pii(redaction.types)}). "
                    "Describe the news you want instead.", code="INVALID_TOPIC")
            validated["topic"] = redaction.text
        return validated

    def default_name(self, config: dict) -> str:
        """Use the topic (truncated) as the scheduler's name."""
        topic = config["topic"]
        return topic if len(topic) <= 80 else topic[:77] + "..."

    async def run_stage(self, stage: str, ctx: RunContext) -> dict:
        """Dispatch to the stage implementation."""
        if stage == PodcastStage.RESEARCH:
            return await self._research(ctx)
        if stage == PodcastStage.SYNTHESIZE:
            return await self._synthesize(ctx)
        raise PermanentJobError(f"Unknown podcast stage: {stage}")

    async def _research(self, ctx: RunContext) -> dict:
        """Run the research agent; NoSourcesError (a PermanentJobError) fails the run without retries."""
        config = PodcastConfig.model_validate(ctx.config)
        result = await run_research_agent(config.topic, config.duration_seconds, config.language)
        return {
            "title": result["title"],
            "script": result["script"],
            "word_count": result["word_count"],
            "sources": [
                {"title": s["title"], "url": s["url"], "published_date": s.get("published_date")}
                for s in result["sources"]
            ],
            "guardrails": result.get("guardrails") or {},
            "evaluation": result.get("evaluation") or {},
        }

    async def _synthesize(self, ctx: RunContext) -> dict:
        """Turn the script into an MP3 with Gnani TTS and store it privately."""
        script = ctx.output.get("script")
        if not script:
            raise PermanentJobError("Run has no script to synthesize")
        config = PodcastConfig.model_validate(ctx.config)
        with node_scope("synthesize"):
            audio, duration, timings = await tts.synthesize(script, voice=config.voice, language=config.language)
        path = await storage.upload(f"podcasts/{ctx.user_id}/{ctx.run_id}.mp3", audio, content_type="audio/mpeg")
        evaluation = {**(ctx.output.get("evaluation") or {}),
                      "duration_sec": duration, "target_duration_sec": config.duration_seconds,
                      "duration_accuracy": duration_accuracy(duration, config.duration_seconds)}
        return {"audio_path": path, "duration_sec": duration, "audio_format": "mp3", "timings": timings,
                "voice": config.voice, "language": config.language, "evaluation": evaluation}

    async def present_output(self, output: dict) -> dict:
        """Hide the private storage path; expose a short-lived signed ``audio_url`` instead."""
        shown = {k: v for k, v in output.items() if k != "audio_path"}
        if output.get("audio_path"):
            try:
                shown["audio_url"] = await storage.signed_url(output["audio_path"])
            except storage.StorageError as exc:  # still show the script/sources; explain the missing audio
                log.warning("audio link for %s unavailable: %s", output["audio_path"], exc.message)
                shown["audio_url"] = None
                shown["audio_error"] = exc.message
        return shown

    async def on_completed(self, ctx: RunContext, output: dict) -> None:
        """Email the user a link to the finished podcast."""
        await email.send_podcast_ready(
            to=ctx.user_email,
            name=ctx.user_name,
            topic=ctx.config.get("topic", ctx.scheduler_name),
            title=output.get("title") or ctx.scheduler_name,
            run_id=str(ctx.run_id),
            duration_sec=output.get("duration_sec") or 0,
        )
