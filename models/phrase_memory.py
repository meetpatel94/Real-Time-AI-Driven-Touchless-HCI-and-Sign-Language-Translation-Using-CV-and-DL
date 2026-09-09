"""Domain documents for the personalized phrase memory.

The sentence completion engine is a shared language model; the phrase memory
is the small, user-specific layer on top of it.  Every time an operator
accepts a completion, the *added* text (the tail) is remembered together with
the normalised context it completed.  Later, that memory is used as an
additional ranking signal only — it can never outrank the language model.

These documents are intentionally Mongo-independent so the engine can be
tested and used without a database.
"""

import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

from models.user_profile import utc_now

MAX_CONTEXT_KEY_LENGTH = 120
MAX_TAIL_LENGTH = 240

_UNSAFE = re.compile(r"[^a-z0-9 ]+")
_SPACES = re.compile(r"\s+")


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return max(0, result)


def normalize_context_key(value: Any) -> str:
    """Normalise a word context into a stable, lowercase, space-joined key."""
    text = str(value or "").lower().replace("’", "'")
    text = _UNSAFE.sub(" ", text)
    return _SPACES.sub(" ", text).strip()[:MAX_CONTEXT_KEY_LENGTH]


def normalize_phrase_tail(value: Any) -> str:
    """Normalise accepted completion text (kept verbatim apart from bounds)."""
    text = str(value or "").replace("\r", " ").replace("\n", " ")
    text = _SPACES.sub(" ", text).strip()
    return text[:MAX_TAIL_LENGTH]


@dataclass
class PhraseMemoryEntry:
    """One remembered completion for one context of one user."""

    user_id: str
    context_key: str
    tail: str
    count: int = 1
    updated_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        self.user_id = str(self.user_id or "")[:120]
        self.context_key = normalize_context_key(self.context_key)
        self.tail = normalize_phrase_tail(self.tail)
        self.count = max(1, _safe_int(self.count, 1))
        self.updated_at = str(self.updated_at or utc_now())

    @property
    def memory_id(self) -> str:
        return "{}::{}::{}".format(self.user_id, self.context_key, self.tail)

    @property
    def is_valid(self) -> bool:
        return bool(self.user_id and self.context_key and self.tail)

    def to_document(self) -> dict:
        return {
            "_id": self.memory_id,
            "user_id": self.user_id,
            "context_key": self.context_key,
            "tail": self.tail,
            "count": int(self.count),
            "updated_at": self.updated_at,
        }

    def to_dict(self) -> dict:
        document = self.to_document()
        document.pop("_id", None)
        document["memory_id"] = self.memory_id
        return document

    @classmethod
    def from_document(cls, document: Optional[Mapping[str, Any]]) -> Optional["PhraseMemoryEntry"]:
        if not isinstance(document, Mapping):
            return None
        user_id = str(document.get("user_id") or "")
        context_key = normalize_context_key(document.get("context_key"))
        tail = normalize_phrase_tail(document.get("tail"))
        if not (user_id and context_key and tail):
            return None
        return cls(
            user_id=user_id,
            context_key=context_key,
            tail=tail,
            count=_safe_int(document.get("count"), 1),
            updated_at=str(document.get("updated_at") or utc_now()),
        )
