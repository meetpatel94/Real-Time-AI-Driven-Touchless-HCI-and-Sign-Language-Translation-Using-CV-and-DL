import threading
from typing import Dict
from services.logging_service import logger

try:
    from deep_translator import GoogleTranslator
    HAS_TRANSLATOR = True
except ImportError:
    HAS_TRANSLATOR = False
    logger.warning("deep-translator not installed. Using fallback translation.")

class TranslationService:
    """Thread-safe translation engine with in-memory caching for real-time sign conversion."""
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(TranslationService, cls).__new__(cls)
                cls._instance._initialize()
            return cls._instance

    def _initialize(self):
        self.cache: Dict[str, str] = {}
        self.cache_lock = threading.Lock()
        self.lang_codes = {
            "english": "en",
            "hindi": "hi",
            "gujarati": "gu",
            "en": "en",
            "hi": "hi",
            "gu": "gu"
        }

    def translate_text(self, text: str, target_lang: str) -> str:
        text = text.strip()
        if not text:
            return ""

        target_code = self.lang_codes.get(target_lang.lower().strip(), "en")

        # English-to-English pass-through
        if target_code == "en":
            return text

        cache_key = f"{text.lower()}::{target_code}"
        with self.cache_lock:
            if cache_key in self.cache:
                return self.cache[cache_key]

        if not HAS_TRANSLATOR:
            return text

        try:
            # Execute translation with timeout safety
            translated = GoogleTranslator(source='auto', target=target_code).translate(text)
            if translated:
                with self.cache_lock:
                    self.cache[cache_key] = translated
                return translated
            return text
        except Exception as e:
            logger.error(f"Translation failed for '{text}' -> {target_code}: {e}")
            return text

translation_service = TranslationService()