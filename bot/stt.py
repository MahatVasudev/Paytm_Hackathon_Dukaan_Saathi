"""Speech-to-text using Sarvam AI. Takes audio bytes, returns text."""
import os

from sarvamai import SarvamAI

_client = None


def _get_client() -> SarvamAI:
    global _client
    if _client is None:
        _client = SarvamAI(api_subscription_key=os.environ["SARVAM_API_KEY"])
    return _client


def transcribe(audio: bytes) -> str:
    """Telegram voice notes are OGG/Opus, which Sarvam accepts directly.
    This is a blocking call, so the bot runs it in a worker thread."""
    options = {}
    language = os.environ.get("auto-detect")  # e.g. hi-IN, en-IN; unset = auto-detect
    if language:
        options["language_code"] = language
    response = _get_client().speech_to_text.transcribe(
        file=("voice.ogg", audio, "audio/ogg"),
        model=os.environ.get("SARVAM_MODEL", "saaras:v3"),
        mode=os.environ.get("SARVAM_MODE", "transcribe"),
        **options,
    )
    return (response.transcript or "").strip()