try:
    from deep_translator import GoogleTranslator
except ImportError:
    GoogleTranslator = None

try:
    from langdetect import detect as _detect
except ImportError:
    _detect = None

SUPPORTED_LANGUAGES = {
    "en": "English",
    "hi": "Hindi",
    "mr": "Marathi",
    "gu": "Gujarati",
    "ta": "Tamil",
    "te": "Telugu",
    "kn": "Kannada",
}

def detect_language(text: str) -> str:
    if not text or not text.strip() or _detect is None:
        return "en"
    try:
        code = _detect(text)
    except Exception:
        return "en"
    return code if code in SUPPORTED_LANGUAGES else "en"

def translate_to_english(text: str, source_lang: str = None) -> str:
    if not text or not text.strip():
        return text
    lang = source_lang or detect_language(text)
    if lang == "en" or GoogleTranslator is None:
        return text
    try:
        return GoogleTranslator(source=lang, target="en").translate(text)
    except Exception:
        return text

def translate_from_english(text: str, target_lang: str) -> str:
    if not text or not target_lang or target_lang == "en":
        return text
    if target_lang not in SUPPORTED_LANGUAGES or GoogleTranslator is None:
        return text
    try:
        return GoogleTranslator(source="en", target=target_lang).translate(text)
    except Exception:
        return text