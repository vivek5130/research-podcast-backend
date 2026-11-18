"""Gnani.ai Timbre v3.0 voice catalog and supported languages.

Source: Gnani docs (api/TTS/available-voices). Every v3.0 voice speaks all 10
languages, so users choose voice and language independently. Gender and persona
come from the catalog; ``use_case`` helps users pick (e.g. a news-style voice).
"""
from dataclasses import asdict, dataclass

TTS_MODEL = "timbre-v3.0"


@dataclass(frozen=True)
class Voice:
    name: str
    gender: str  # "female" | "male"
    persona: str
    use_case: str


VOICES: tuple[Voice, ...] = (
    Voice("Shivani", "female", "Warm, affectionate", "Companion apps, casual engagement"),
    Voice("Rakesh", "male", "Clean, neutral", "News, broadcast, general-purpose IVR"),
    Voice("Arko", "male", "Deep, authoritative baritone", "Narration, announcements, brand"),
    Voice("Avinash", "male", "Calm, captivating", "Brand films, e-learning, advertising"),
    Voice("Nitin", "male", "Measured, unhurried", "Audiobooks, long-form narration, politics"),
    Voice("Lakshmi", "female", "Clear, authoritative", "Corporate IVR, explainers, announcements"),
    Voice("Shruthi", "female", "Confident, polished", "Finance, health, professional IVR"),
    Voice("Sneha", "female", "Bright, articulate", "Education, health, regional campaigns"),
    Voice("Sohini", "female", "Clear, composed", "Support, education, financial services"),
    Voice("Swathi", "female", "Modern, confident", "Education, social media, youth engagement"),
    Voice("Kalpana", "female", "Friendly, encouraging", "Tutoring, e-learning, onboarding"),
    Voice("Neelam", "female", "Versatile, expressive", "Multi-language campaigns, IVR, narration"),
    Voice("Rahulmehta", "male", "Expressive, warm", "E-learning, narration, explainers"),
    Voice("Manjunath", "male", "Steady, dependable", "Narration, IVR, informational content"),
    Voice("Vinod", "male", "Mature, seasoned", "Formal narration, institutional content"),
    Voice("Mahendra", "male", "Youthful, energetic", "Apps, tech support, Gen-Z engagement"),
    Voice("Yashraj", "male", "Smooth, magnetic", "Brand voiceovers, luxury, lifestyle"),
    Voice("Devansh", "male", "Soft, sincere", "Storytelling, drama, emotional content"),
    Voice("Prakash", "male", "Calm, reassuring", "Support bots, healthcare, guided flows"),
    Voice("Harshad", "male", "Deep, grounded", "Conversational IVR, regional engagement"),
    Voice("Shubham", "male", "Bold, high-energy", "Ads, promotions, mass-market campaigns"),
    Voice("Vikramdasu", "male", "Energetic, persuasive", "Outbound sales, lead qualification"),
    Voice("Rajeev", "male", "Authentic, earthy", "Regional outreach, health, social campaigns"),
    Voice("Santosh", "male", "Fresh, unaffected", "Local conversation, community engagement"),
    Voice("Rajni", "female", "Mature, commanding", "Announcements, governance, formal IVR"),
    Voice("Vasudha", "female", "Polished, aspirational", "Premium ads, brand campaigns"),
    Voice("Riyaa", "female", "Upbeat, persuasive", "Retail ads, e-commerce, promotions"),
    Voice("Lakshmidevi", "female", "Warm, patient", "Inbound support, banking, healthcare"),
    Voice("Priyasharm", "female", "Warm, patient", "Inbound support, banking, healthcare"),
    Voice("Anitasingh", "female", "Firm yet empathetic", "Collections, payment reminders, BFSI"),
    Voice("Chetana", "female", "Soft, intimate", "Audiobooks, romance, ASMR-style content"),
    Voice("Tarika", "female", "Soft-spoken, gentle", "Tech support, guided troubleshooting"),
    Voice("Sunaina", "female", "Grounded, natural", "IVR, rural outreach, government services"),
    Voice("Rekha", "female", "Warm, relatable", "Rural engagement, agriculture, social outreach"),
)
VOICE_NAMES = tuple(v.name for v in VOICES)
DEFAULT_VOICE = "Shivani"

# Gnani language codes -> display name (also used to tell the writer which language to write in).
LANGUAGES: dict[str, str] = {
    "en-IN": "English (India)",
    "hi-IN": "Hindi",
    "ta-IN": "Tamil",
    "te-IN": "Telugu",
    "kn-IN": "Kannada",
    "ml-IN": "Malayalam",
    "mr-IN": "Marathi",
    "bn-IN": "Bengali",
    "gu-IN": "Gujarati",
    "pa-IN": "Punjabi",
}
DEFAULT_LANGUAGE = "en-IN"

# Short line used for voice previews, per language.
PREVIEW_TEXT: dict[str, str] = {
    "en-IN": "Hi, I'm {voice}. Welcome to DailyCast, your news briefing, researched fresh and read just for you.",
    "hi-IN": "नमस्ते, मैं {voice} हूँ। डेलीकास्ट में आपका स्वागत है, आपकी अपनी ताज़ा खबरों की ब्रीफिंग।",
}


def catalog() -> dict:
    """Voices and languages for the API (GET /api/podcast/voices)."""
    return {
        "model": TTS_MODEL,
        "default_voice": DEFAULT_VOICE,
        "default_language": DEFAULT_LANGUAGE,
        "voices": [asdict(v) for v in VOICES],
        "languages": [{"code": c, "name": n} for c, n in LANGUAGES.items()],
    }
