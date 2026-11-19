"""Guardrails for the research agent: PII redaction and a blocked-terms content policy.

Built on LangChain agent middleware (``langchain.agents.middleware``):

* **PII** uses LangChain's ``PIIMiddleware`` (built-in ``email``, ``credit_card``, ``ip``
  detectors, plus custom regex/callable detectors for phone numbers, Aadhaar, PAN
  and API keys). Strategy ``redact`` replaces a value with ``[REDACTED_<TYPE>]``,
  ``mask`` keeps the last digits of a card.
* **Blocked terms** is a custom ``AgentMiddleware`` (LangChain has no built-in
  term filter): it raises ``ContentPolicyError`` when a request asks for clearly
  harmful content.

Our agent is a LangGraph ``StateGraph`` that calls Gemini directly rather than a
``create_agent`` loop, so the middleware hooks are run at the agent's boundaries,
the same places ``create_agent`` would run them:

    user topic / search queries  -> before_model  (apply_to_input)
    fetched article text         -> tool results  (apply_to_tool_results)
    final script, before TTS     -> after_model   (apply_to_output)

Only the PII *types* found are reported (never the values), so failure reasons and
episode pages never echo sensitive data back.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from langchain.agents.middleware import AgentMiddleware, PIIMiddleware
from langchain.agents.middleware.pii import PIIMatch
from langchain_core.messages import AIMessage, HumanMessage

from app.core.config import get_settings
from app.core.exceptions import PermanentJobError

# --------------------------------------------------------------------------- PII


def _regex_detector(pii_type: str, *patterns: str):
    """A LangChain ``Detector`` (``str -> list[PIIMatch]``) from one or more regexes."""
    compiled = [re.compile(p) for p in patterns]

    def detect(content: str) -> list[PIIMatch]:
        # Several patterns can match the same text (e.g. "+1 415-555-0101"): keep the widest
        # match per span so replacements never overlap and garble the sentence.
        spans = sorted({(m.start(), m.end()) for rx in compiled for m in rx.finditer(content)},
                       key=lambda s: (s[0], -s[1]))
        kept: list[tuple[int, int]] = []
        for start, end in spans:
            if kept and start < kept[-1][1]:
                if end > kept[-1][1]:
                    kept[-1] = (kept[-1][0], end)
                continue
            kept.append((start, end))
        return [PIIMatch(type=pii_type, value=content[s:e], start=s, end=e) for s, e in kept]

    return detect


# Phone: an international number with "+", a 10-digit Indian mobile, or a US-style 555-123-4567.
# Deliberately strict so years and figures in news ("2024 to 2026", "$1,250,000") don't match.
_PHONE = _regex_detector(
    "phone",
    r"(?<![\w+])\+\d{1,3}[\s-]?\d(?:[\s-]?\d){7,12}(?!\d)",
    r"(?<!\d)[6-9]\d{9}(?!\d)",
    r"(?<!\d)\(?\d{3}\)?[\s.-]\d{3}[-.]\d{4}(?!\d)",
)
_AADHAAR = _regex_detector("aadhaar", r"(?<!\d)[2-9]\d{3}[\s-]\d{4}[\s-]\d{4}(?!\d)")
_PAN = _regex_detector("pan", r"\b[A-Z]{5}\d{4}[A-Z]\b")
_API_KEY = _regex_detector(
    "api_key",
    r"\bsk-[A-Za-z0-9_-]{20,}",
    r"\bAIza[0-9A-Za-z_-]{30,}",
    r"\btvly-[A-Za-z0-9-]{20,}",
    r"\bgh[pousr]_[A-Za-z0-9]{30,}",
    r"\beyJ[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}",
)


def _input_middleware() -> list[PIIMiddleware]:
    """User input (topic, search queries): the broadest set, all redacted."""
    return [
        PIIMiddleware("email", strategy="redact"),
        PIIMiddleware("credit_card", strategy="redact"),
        PIIMiddleware("ip", strategy="redact"),
        PIIMiddleware("phone", detector=_PHONE, strategy="redact"),
        PIIMiddleware("aadhaar", detector=_AADHAAR, strategy="redact"),
        PIIMiddleware("pan", detector=_PAN, strategy="redact"),
        PIIMiddleware("api_key", detector=_API_KEY, strategy="redact"),
    ]


def _content_middleware(**apply) -> list[PIIMiddleware]:
    """Fetched articles and the script: contact details and card numbers only.

    Narrower than the input set on purpose: news text is full of numbers and
    names, and over-redacting would damage the episode.
    """
    return [
        PIIMiddleware("email", strategy="redact", **apply),
        PIIMiddleware("credit_card", strategy="mask", **apply),
        PIIMiddleware("phone", detector=_PHONE, strategy="redact", **apply),
    ]


_INPUT = _input_middleware()
_TOOL_RESULTS = _content_middleware()  # run via before_model on the article text
_OUTPUT = _content_middleware(apply_to_input=False, apply_to_output=True)


@dataclass
class Redaction:
    """Result of a PII pass: the cleaned text and which PII types were removed."""

    text: str
    types: list[str] = field(default_factory=list)


_PLACEHOLDER = re.compile(r"\s*\[REDACTED_[A-Z_]+\]")


def _run(middlewares: list[PIIMiddleware], text: str, *, output: bool) -> Redaction:
    """Pass ``text`` through each PIIMiddleware hook in turn, recording which ones changed it.

    LangChain's ``[REDACTED_EMAIL]``-style placeholders are then removed: they would
    pollute search queries and, in a script, be read aloud by the TTS voice. Masked
    card numbers (``**** 1234``) contain no placeholder and stay as they are.
    """
    found: list[str] = []
    for mw in middlewares:
        message = AIMessage(text) if output else HumanMessage(text)
        hook = mw.after_model if output else mw.before_model
        update = hook({"messages": [message]}, None)
        if update:  # the hook returns new messages only when it redacted something
            text = update["messages"][-1].content
            found.append(mw.pii_type)
    if found:
        text = re.sub(r"[ \t]{2,}", " ", _PLACEHOLDER.sub("", text)).strip()
    return Redaction(text, found)


def redact_input(text: str) -> Redaction:
    """Redact PII from user input (topic, search queries) before it reaches search or the LLM."""
    return _run(_INPUT, text, output=False)


def redact_source(text: str) -> Redaction:
    """Redact contact details from fetched article text (LangChain's ``apply_to_tool_results`` step)."""
    return _run(_TOOL_RESULTS, text, output=False)


def redact_output(text: str) -> Redaction:
    """Redact contact details from the generated script before it is voiced (``apply_to_output``)."""
    return _run(_OUTPUT, text, output=True)


PII_LABELS = {
    "email": "email address", "credit_card": "card number", "ip": "IP address", "phone": "phone number",
    "aadhaar": "Aadhaar number", "pan": "PAN", "api_key": "API key or token",
}


def describe_pii(types: list[str]) -> str:
    """'email address and phone number' (types only, never values)."""
    labels = [PII_LABELS.get(t, t) for t in dict.fromkeys(types)]
    return labels[0] if len(labels) == 1 else ", ".join(labels[:-1]) + " and " + labels[-1]


# ----------------------------------------------------------------- blocked terms

# Phrase-level patterns: they target *requests for* harmful content, not news about it.
# "Bomb attack in Delhi" or "drug seizure" must still work; "how to make a bomb" must not.
BLOCKED_TERMS: dict[str, list[str]] = {
    "Weapons & explosives instructions": [
        r"how to (make|build|assemble) (a |an )?(bomb|explosive|ied|pipe bomb|grenade)",
        r"(bomb|explosive|ied)[- ]making",
        r"(3d[- ]printed|ghost) gun (files?|blueprints?|instructions?)",
    ],
    "Illegal drug manufacturing": [
        r"how to (make|cook|synthesi[sz]e|produce) (meth|methamphetamine|cocaine|heroin|fentanyl|lsd|mdma)",
        r"(meth|fentanyl) (recipe|lab setup)",
    ],
    "Self-harm": [
        r"how to (kill|hurt|harm) (myself|yourself)",
        r"suicide (methods?|instructions?|techniques?)",
        r"self[- ]harm (methods?|techniques?|tips)",
    ],
    "Sexual content": [
        r"\bporn(ography|o)?\b",
        r"\bnsfw\b",
        r"\bnudes?\b",
        r"\bxxx\b",
        r"child (sexual|abuse) (material|images?|videos?)",
        r"\bcsam\b",
    ],
    "Violent extremism": [
        r"how to join (isis|al[- ]qaeda|a terrorist)",
        r"terror(ist)? recruitment (guide|tips|material)",
        r"(extremist|jihadist) propaganda (videos?|material)",
    ],
    "Hacking & fraud": [
        r"how to hack (into )?(someone|a person|an account|a phone|wifi)",
        r"(carding|credit card dumps|cvv dumps|phishing kits?|ransomware builder)",
        r"(buy|sell) (stolen|hacked) (accounts|data|cards)",
    ],
}


@dataclass(frozen=True)
class Violation:
    """One blocked-terms match: the policy category and the matched phrase."""

    category: str
    term: str


class ContentPolicyError(PermanentJobError):
    """The request asks for content the policy blocks. Retrying can't help: the topic must change."""

    def __init__(self, violations: list[Violation], where: str = "topic", message: str | None = None):
        self.violations = violations
        super().__init__(message or policy_message(violations, where))

    @classmethod
    def moderated(cls, category: str) -> "ContentPolicyError":
        """Raised when the LLM moderation check (in the planning step) flags the topic."""
        category = (category or "unsafe content").strip()[:60]
        return cls([Violation(category, "flagged by moderation")], message=(
            f"Blocked by content policy: the topic was flagged by moderation as {category}. "
            "Edit the topic to remove it and try again."))


def policy_message(violations: list[Violation], where: str = "topic") -> str:
    """Readable reason, e.g. "Blocked by content policy: the topic contains terms in 'Sexual content' ('nsfw')..."""
    by_category: dict[str, list[str]] = {}
    for v in violations:
        by_category.setdefault(v.category, [])
        if v.term not in by_category[v.category]:
            by_category[v.category].append(v.term)
    parts = "; ".join(f"{cat} ({', '.join(repr(t) for t in terms[:3])})" for cat, terms in by_category.items())
    return (f"Blocked by content policy: the {where} contains inappropriate terms in {parts}. "
            "Edit the topic to remove them and try again.")


class BlockedTermsMiddleware(AgentMiddleware):
    """LangChain agent middleware that blocks requests for clearly harmful content.

    Usable in ``create_agent(middleware=[...])``; here its ``before_model`` hook is
    also run directly on the topic and on every search query. Extra terms can be
    added with the ``BLOCKED_TERMS_EXTRA`` setting (comma-separated, category "Custom").
    """

    def __init__(self, terms: dict[str, list[str]] | None = None, extra: list[str] | None = None):
        super().__init__()
        terms = dict(terms or BLOCKED_TERMS)
        if extra:
            terms["Custom"] = [rf"\b{re.escape(t.strip())}\b" for t in extra if t.strip()]
        self._patterns = [(cat, re.compile(p, re.IGNORECASE)) for cat, ps in terms.items() for p in ps]

    def find(self, text: str) -> list[Violation]:
        """Every policy match in ``text`` (empty list = allowed)."""
        return [Violation(cat, m.group(0).lower()) for cat, rx in self._patterns for m in rx.finditer(text or "")]

    def before_model(self, state, runtime):  # noqa: D401 (LangChain hook signature)
        """Raise ``ContentPolicyError`` if the latest user message breaks the policy."""
        last = next((m for m in reversed(state.get("messages", [])) if isinstance(m, HumanMessage)), None)
        violations = self.find(str(last.content)) if last else []
        if violations:
            raise ContentPolicyError(violations)
        return None


_policy: BlockedTermsMiddleware | None = None


def content_policy() -> BlockedTermsMiddleware:
    """The process-wide policy (built once; includes BLOCKED_TERMS_EXTRA)."""
    global _policy
    if _policy is None:
        extra = [t for t in get_settings().blocked_terms_extra.split(",") if t.strip()]
        _policy = BlockedTermsMiddleware(extra=extra)
    return _policy


def check_topic(topic: str) -> None:
    """Run the content policy on a topic (raises ``ContentPolicyError``)."""
    content_policy().before_model({"messages": [HumanMessage(topic)]}, None)


# ------------------------------------------------------------ prompt injection

_INJECTION = re.compile(
    r"(ignore|disregard|forget|override) (all |any |the )?(previous|prior|above|earlier|preceding) "
    r"(instructions?|prompts?|rules|messages?)"
    r"|you are (now|no longer) (a|an|the)\b"
    r"|(reveal|print|show|repeat) (your |the )?(system|hidden) (prompt|instructions?)"
    r"|new instructions?:"
    r"|</?(system|assistant|instructions?)>",
    re.IGNORECASE,
)
_SENTENCE = re.compile(r"(?<=[.!?।])\s+")


def strip_injections(text: str) -> tuple[str, int]:
    """Drop sentences in fetched article text that try to instruct the model. Returns (text, removed)."""
    sentences = _SENTENCE.split(text or "")
    kept = [s for s in sentences if not _INJECTION.search(s)]
    return " ".join(kept), len(sentences) - len(kept)


# --------------------------------------------------------- output: grounding + safety

MAX_SENTENCES_REMOVED = 0.3  # never strip more than 30% of the script; beyond that the episode is only flagged
_WORD = re.compile(r"[\w']+")


def _tokens(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if len(w) > 3}


def filter_script(script: str, unsupported_claims: list[str]) -> tuple[str, dict]:
    """Output guardrail before TTS.

    * Groundedness: drop sentences that carry a claim the judge found in no source
      (matched by word overlap with the claim).
    * Safety: drop sentences that break the content policy.
    Removal is capped at ``MAX_SENTENCES_REMOVED`` of the script; above that the run is flagged instead.
    Paragraph breaks are kept so timings and readability stay intact.
    """
    policy = content_policy()
    claims = [_tokens(c) for c in unsupported_claims if _tokens(c)]
    paragraphs = [p for p in script.split("\n\n") if p.strip()]
    total = sum(len(_SENTENCE.split(p)) for p in paragraphs) or 1
    budget = max(1, int(total * MAX_SENTENCES_REMOVED)) if total >= 3 else 0  # short scripts can still lose one
    removed_ungrounded = removed_unsafe = 0
    out = []
    for paragraph in paragraphs:
        kept = []
        for sentence in _SENTENCE.split(paragraph):
            words = _tokens(sentence)
            unsafe = bool(policy.find(sentence))
            ungrounded = any(c and len(c & words) / len(c) >= 0.6 for c in claims)
            if (unsafe or ungrounded) and removed_ungrounded + removed_unsafe < budget:
                removed_unsafe += unsafe
                removed_ungrounded += ungrounded and not unsafe
                continue
            kept.append(sentence)
        if kept:
            out.append(" ".join(kept))
    report = {"ungrounded_sentences_removed": removed_ungrounded, "unsafe_sentences_removed": removed_unsafe}
    if claims and removed_ungrounded < len(claims):
        report["ungrounded_claims_kept"] = len(claims) - removed_ungrounded
    return "\n\n".join(out), report
