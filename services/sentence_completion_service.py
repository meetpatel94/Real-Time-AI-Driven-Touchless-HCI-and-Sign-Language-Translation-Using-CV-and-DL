"""Contextual sentence/phrase completion for the Sign Language Studio text box.

Why this exists
---------------
``WordSuggestionService`` already completes the *trailing word* from a fixed
vocabulary trie.  That is word prediction; it cannot finish a sentence.  This
service is the missing half of a modern autocomplete: it reads the whole
clause that has been typed — or produced by recognised signs — and returns a
small number of complete, grammatical phrase or sentence suggestions.

Model
-----
The model is a compact hybrid that runs locally with no extra dependency and
no GPU, which keeps it practical for the real-time camera pipeline:

1. ``PHRASE_BANK``   — hand-ranked ``context -> continuation`` frames
   (``"i need" -> "help"``).  Highest precision, highest confidence.
2. ``PHRASE_CORPUS`` — complete sentences.  Used for phrase-level completion
   of contexts that are not in the bank, and to train the n-gram model.
3. Trigram/bigram back-off — predicts the next word when no phrase
   continuation is known.  Lowest confidence, strictly gated.
4. Vocabulary completion — finishes a partially typed word
   (``"I am go" -> "going"``) using corpus word frequencies.
5. Phrase memory — the user's own accepted completions, used as an extra
   ranking signal only.

Every candidate is scored, de-duplicated, checked for grammatical fragments
and filtered by a confidence floor that depends on how much context exists.
At most three suggestions are returned: one or two precise completions are
always better than five noisy ones.
"""

import re
import threading
from collections import OrderedDict, defaultdict
from dataclasses import dataclass
from time import monotonic
from typing import Any, Dict, List, Optional, Sequence, Tuple

from config import Config
from models.phrase_memory import PhraseMemoryEntry, normalize_context_key, normalize_phrase_tail
from repositories.phrase_memory_repository import PhraseMemoryRepository
from services.logging_service import logger
from services.phrase_corpus import FRAGMENT_ENDINGS, PHRASE_BANK, PHRASE_CORPUS

# A word token keeps its trailing punctuation so composed tails stay readable.
_WORD_TOKEN_RE = re.compile(r"[A-Za-z0-9]+(?:['’][A-Za-z]+)?[.,!?;:]*")
_TRAILING_WORD_RE = re.compile(r"[A-Za-z0-9]+(?:['’][A-Za-z]+)?$")
_CLAUSE_BOUNDARY_RE = re.compile(r"[.!?\n,;]")
_PUNCTUATION = set(".,;:!?'’")

MAX_SUGGESTIONS = 3
MAX_CONTEXT_TOKENS = 4
MAX_CACHE_ENTRIES = 400

# Absolute confidence floor, plus a stricter floor for very short contexts:
# with almost no evidence the engine stays quiet instead of guessing.
MIN_SCORE = 0.55
SHORT_CONTEXT_FLOOR = {0: 0.78, 1: 0.70}

# Base confidence per evidence source.
SOURCE_BASE = {
    "phrase-bank": 0.90,
    "corpus": 0.84,
    "personal": 0.68,
    "ngram": 0.60,
    "word": 0.58,
}

# Minimum personal signal required before a remembered phrase may displace
# the weakest language-model suggestion.
PERSONAL_MIN_SIGNAL = 0.03
# How often a failed phrase-memory read may be retried (seconds).
MEMORY_RETRY_SECONDS = 30.0

BOS = "<s>"
EOS = "</s>"


def normalize_token(token: str) -> str:
    """Lowercase, drop apostrophes and trailing punctuation for matching."""
    text = str(token or "").lower().replace("’", "'")
    text = text.replace("'", "")
    return text.strip(".,;:!?")


def dedupe_key(text: str) -> str:
    """Stable identity used to collapse equivalent suggestions."""
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", str(text or "").lower()).split())


def _levenshtein_within_one(a: str, b: str) -> bool:
    """True when ``a`` and ``b`` differ by at most one edit (typo tolerance)."""
    if a == b:
        return True
    if abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        diffs = 0
        for index in range(len(a)):
            if a[index] != b[index]:
                diffs += 1
                if diffs > 1:
                    return False
        return True
    longer, shorter = (a, b) if len(a) > len(b) else (b, a)
    index_long = index_short = 0
    skipped = False
    while index_short < len(shorter):
        if longer[index_long] == shorter[index_short]:
            index_long += 1
            index_short += 1
            continue
        if skipped:
            return False
        skipped = True
        index_long += 1
    return True


@dataclass
class _Candidate:
    """One scored completion hypothesis."""

    text: str
    source: str
    tail_text: str = ""
    rank: int = 0
    backoff: float = 0.0
    context_size: int = 0
    partial: bool = False
    added_words: int = 1
    personal: float = 0.0
    score_override: Optional[float] = None
    score: float = 0.0

    def finalize(self) -> float:
        if self.score_override is not None:
            base = self.score_override
        else:
            base = SOURCE_BASE.get(self.source, 0.60)
        score = base
        score -= 0.012 * max(0, self.rank)
        score -= self.backoff
        if self.score_override is None:
            score += 0.03 * min(self.context_size, MAX_CONTEXT_TOKENS)
            score += min(0.09, self.personal)
        if self.partial:
            # Treating the trailing word as incomplete is the weaker reading.
            score -= 0.03
        if self.added_words <= 1 and self.last_word in FRAGMENT_ENDINGS:
            # "Where is the" is a fragment, not a suggestion.
            score -= 0.25
        if self.text.rstrip()[-1:] in ".?!":
            # A properly terminated sentence is a stronger completion.
            score += 0.02
        self.score = max(0.0, min(1.0, score))
        return self.score

    @property
    def last_word(self) -> str:
        words = _WORD_TOKEN_RE.findall(self.text)
        return normalize_token(words[-1]) if words else ""


class SentenceCompletionService:
    """Thread-safe, cached contextual completion engine (singleton)."""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(SentenceCompletionService, cls).__new__(cls)
                cls._instance._initialize()
            return cls._instance

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------
    def _initialize(self) -> None:
        self._cache: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
        self._cache_lock = threading.Lock()

        self._corpus: List[Tuple[List[str], List[str]]] = []
        self._corpus_index: Dict[str, List[int]] = defaultdict(list)
        self._bank: Dict[str, List[str]] = {}
        self._vocabulary: List[Tuple[str, int]] = []
        self._unigrams: Dict[str, int] = defaultdict(int)
        self._bigrams: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
        self._trigrams: Dict[Tuple[str, str], Dict[str, int]] = defaultdict(lambda: defaultdict(int))

        # Personalization state (memory-first, MongoDB when available).
        self._repository = PhraseMemoryRepository()
        self._memory_lock = threading.RLock()
        self._context_counts: Dict[str, Dict[str, Dict[str, int]]] = {}
        self._tail_counts: Dict[str, Dict[str, int]] = {}
        self._memory_loaded: set = set()
        self._memory_attempt_at: Dict[str, float] = {}

        self._build_bank()
        self._build_corpus()
        self._build_ngrams()

    def _build_bank(self) -> None:
        self._bank = {}
        for context, tails in PHRASE_BANK.items():
            key = normalize_context_key(context)
            if not key:
                continue
            cleaned = [tail for tail in tails if str(tail).strip()]
            if cleaned:
                self._bank[key] = cleaned

    def _build_corpus(self) -> None:
        for sentence in PHRASE_CORPUS:
            surface = _WORD_TOKEN_RE.findall(sentence)
            if not surface:
                continue
            normalized = [normalize_token(token) for token in surface]
            if not any(normalized):
                continue
            index = len(self._corpus)
            self._corpus.append((normalized, surface))
            if normalized[0]:
                self._corpus_index[normalized[0]].append(index)

    def _build_ngrams(self) -> None:
        for normalized, _surface in self._corpus:
            words = [word for word in normalized if word]
            if not words:
                continue
            for word in words:
                self._unigrams[word] += 1
            sequence = [BOS, BOS] + words + [EOS]
            for position in range(2, len(sequence)):
                self._trigrams[(sequence[position - 2], sequence[position - 1])][sequence[position]] += 1
            for position in range(1, len(sequence)):
                self._bigrams[sequence[position - 1]][sequence[position]] += 1
        self._vocabulary = sorted(self._unigrams.items(), key=lambda item: (-item[1], item[0]))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def complete(
        self,
        text: str,
        caret: Optional[int] = None,
        limit: int = MAX_SUGGESTIONS,
        user_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Return ranked completions for the clause that ends at ``caret``."""
        started = monotonic()
        text = text or ""
        caret = len(text) if caret is None else max(0, min(int(caret), len(text)))
        limit = max(1, min(int(limit or MAX_SUGGESTIONS), MAX_SUGGESTIONS))

        prefix = text[:caret]
        cache_key = "{}|{}|{}|{}".format(prefix, caret, limit, self._normalize_user(user_id))
        cached = self._cache_get(cache_key)
        if cached is not None:
            result = dict(cached)
            result["cached"] = True
            result["elapsed_ms"] = round((monotonic() - started) * 1000.0, 2)
            return result

        clause_start = self.clause_start(prefix)
        clause = prefix[clause_start:]
        suggestions = self._suggest(clause, limit=limit, user_id=user_id)

        result = {
            "success": True,
            "text": text,
            "caret": caret,
            "clause": clause,
            "clause_start": clause_start,
            "suggestions": suggestions,
            "cached": False,
            "elapsed_ms": round((monotonic() - started) * 1000.0, 2),
        }
        self._cache_put(cache_key, result)
        return result

    def register_acceptance(
        self,
        text: str,
        accepted: str,
        caret: Optional[int] = None,
        user_id: Optional[str] = None,
    ) -> bool:
        """Remember an accepted completion so similar contexts rank it higher."""
        user = self._normalize_user(user_id)
        text = text or ""
        accepted = str(accepted or "").strip()
        caret = len(text) if caret is None else max(0, min(int(caret), len(text)))
        if not accepted:
            return False

        prefix = text[:caret]
        clause = prefix[self.clause_start(prefix):]
        words = _WORD_TOKEN_RE.findall(clause)
        norms = [normalize_token(word) for word in words]
        trailing_space = clause[-1:].isspace() if clause else False

        accepted_words = _WORD_TOKEN_RE.findall(accepted)
        accepted_norms = [normalize_token(word) for word in accepted_words]

        # The trailing word is ambiguous: it may be complete ("I need" -> "help")
        # or still being typed ("I ne" -> "ed help").  Remember both readings.
        candidate_lengths = [len(norms)]
        if not trailing_space and len(norms) >= 2:
            candidate_lengths.append(len(norms) - 1)

        remembered = False
        for context_len in candidate_lengths:
            if context_len <= 0 or context_len > len(accepted_words):
                continue
            if accepted_norms[:context_len] != norms[:context_len]:
                continue
            tail = " ".join(accepted_words[context_len:]).strip()
            context_key = normalize_context_key(" ".join(norms[:context_len]))
            if not context_key or not tail or tail in _PUNCTUATION:
                continue

            with self._memory_lock:
                self._ensure_memory_loaded(user)
                tails = self._context_counts.setdefault(user, {}).setdefault(context_key, {})
                count = min(1000, self._tail_count(tails, tail) + 1)
                self._store_tail(tails, tail, count)
                global_tails = self._tail_counts.setdefault(user, {})
                self._store_tail(global_tails, tail, min(1000, self._tail_count(global_tails, tail) + 1))

            self._persist_async(
                PhraseMemoryEntry(user_id=user, context_key=context_key, tail=tail, count=count)
            )
            remembered = True
        if remembered:
            # Rankings change once a phrase is remembered: drop stale results
            # instead of serving pre-personalization suggestions from cache.
            self.invalidate_cache(user)
        return remembered

    def memory_snapshot(self, user_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Bounded view of the remembered phrases (debugging/support only)."""
        user = self._normalize_user(user_id)
        with self._memory_lock:
            self._ensure_memory_loaded(user)
            snapshot = []
            for context_key, tails in self._context_counts.get(user, {}).items():
                for tail, count in tails.items():
                    snapshot.append({"context": context_key, "tail": tail, "count": int(count)})
        return sorted(snapshot, key=lambda item: (-item["count"], item["context"]))

    def clear_memory(self, user_id: Optional[str] = None) -> None:
        user = self._normalize_user(user_id)
        with self._memory_lock:
            self._context_counts.pop(user, None)
            self._tail_counts.pop(user, None)
            self._memory_loaded.discard(user)
            self._memory_attempt_at.pop(user, None)
        try:
            self._repository.delete_for_user(user)
        except Exception:  # pragma: no cover - optional storage
            pass

    # ------------------------------------------------------------------
    # Candidate generation
    # ------------------------------------------------------------------
    def _suggest(self, clause: str, limit: int, user_id: Optional[str]) -> List[Dict[str, Any]]:
        clause = clause or ""
        words = _WORD_TOKEN_RE.findall(clause)
        if not words:
            return []

        norms = [normalize_token(word) for word in words]
        trailing_match = _TRAILING_WORD_RE.search(clause)
        user = self._normalize_user(user_id)
        clause_key = dedupe_key(clause)

        hypotheses: List[Tuple[List[str], Optional[str], str, float]] = []
        if trailing_match is not None:
            partial = normalize_token(trailing_match.group(0))
            if partial:
                # H1: the trailing word is still being typed ("I am go" -> "go").
                hypotheses.append((norms[:-1], partial, clause[: trailing_match.start()], 0.03))
        # H2: everything typed so far is complete, predict what comes next.
        hypotheses.append((norms, None, clause, 0.0))

        best: Dict[str, _Candidate] = {}
        for context, partial, head, penalty in hypotheses:
            if not context and not partial:
                continue
            floor = max(MIN_SCORE, SHORT_CONTEXT_FLOOR.get(len(context), 0.0))
            for candidate in self._collect(context, partial, head, user):
                candidate.backoff += penalty
                candidate.partial = partial is not None
                candidate.context_size = len(context)
                candidate.personal = self._personal_bonus(user, context, candidate.tail_text)
                if candidate.finalize() < floor:
                    continue
                key = dedupe_key(candidate.text)
                if not key or key == clause_key:
                    continue
                if context and self._first_added_word(candidate.tail_text) == context[-1]:
                    # Never repeat the word that was just typed.
                    continue
                existing = best.get(key)
                if existing is None or candidate.score > existing.score:
                    best[key] = candidate

        # Finishing the typed word is only useful when nothing phrase-level is
        # known: "I am very hap" -> "I am very happy today." beats "happy".
        if any(candidate.source != "word" for candidate in best.values()):
            best = {key: item for key, item in best.items() if item.source != "word"}

        ranked = sorted(best.values(), key=lambda item: (-item.score, len(item.text), item.text))
        ranked = self._place_personal(ranked, limit)

        return [
            {
                "text": candidate.text,
                "confidence": round(candidate.score, 3),
                "source": candidate.source,
                "personalized": candidate.personal > 0.0,
            }
            for candidate in ranked[:limit]
        ]

    @staticmethod
    def _place_personal(ranked: List[_Candidate], limit: int) -> List[_Candidate]:
        """Let a remembered phrase take the last slot, never the whole list.

        The language model keeps the strongest positions; personalization only
        decides which weaker candidate is displaced.
        """
        selected = list(ranked[:limit])
        if len(selected) < limit:
            return selected
        if any(item.personal >= PERSONAL_MIN_SIGNAL for item in selected):
            return selected
        remembered = [item for item in ranked if item.personal >= PERSONAL_MIN_SIGNAL]
        if not remembered:
            return selected
        model_items = [item for item in selected if item.personal < PERSONAL_MIN_SIGNAL]
        if len(model_items) < 2:
            return selected
        selected[-1] = remembered[0]
        return selected

    def _collect(
        self,
        context: Sequence[str],
        partial: Optional[str],
        head: str,
        user: str,
    ) -> List[_Candidate]:
        """Generate raw candidates for one hypothesis (unranked, unscored)."""
        candidates: List[_Candidate] = []
        context = list(context)

        # 1. Curated context frames.  Only the most specific matching frame is
        #    used.  For short contexts at most one leading token may be dropped
        #    (otherwise a short frame such as "go" would hijack "I am go" and
        #    produce "I am go home now"); for long sentences the trailing
        #    words still drive the completion, but every dropped token costs
        #    confidence.
        window = min(len(context), MAX_CONTEXT_TOKENS)
        minimum_size = 2 if len(context) > MAX_CONTEXT_TOKENS else max(1, len(context) - 1)
        for size in range(window, minimum_size - 1, -1):
            key = " ".join(context[len(context) - size:])
            tails = self._bank.get(key)
            if not tails:
                continue
            matched = 0
            dropped = min(0.20, 0.05 * (len(context) - size))
            for rank, tail in enumerate(tails):
                if partial and not self._tail_matches(tail, partial):
                    continue
                matched += 1
                candidates.append(
                    self._candidate(head, tail, "phrase-bank", rank=rank, backoff=dropped)
                )
            if matched:
                break

        # 2. Phrase-level completion from the sentence corpus.
        indexes = self._corpus_index.get(context[0], []) if context else range(len(self._corpus))
        corpus_hits = 0
        for index in indexes:
            normalized, surface = self._corpus[index]
            if len(normalized) <= len(context):
                continue
            if normalized[: len(context)] != context:
                continue
            if partial and not self._tail_matches(surface[len(context)], partial):
                continue
            tail = " ".join(surface[len(context):])
            if not tail:
                continue
            tail = self._reclaim_punctuation(head, surface[len(context) - 1], tail) if context else tail
            candidates.append(self._candidate(head, tail, "corpus", rank=corpus_hits))
            corpus_hits += 1
            if corpus_hits >= 12:
                break

        # 3. The user's own accepted completions for this context.
        for tail, count in self._personal_tails(user, context):
            if partial and not self._tail_matches(tail, partial):
                continue
            candidate = self._candidate(head, tail, "personal", rank=0)
            # Bounded on purpose: a remembered phrase is a ranking signal,
            # never a licence to outrank the language model.
            candidate.score_override = min(0.88, 0.68 + 0.05 * count)
            candidates.append(candidate)

        # 4. Statistical fallbacks: next word, or completing a typed prefix.
        if partial is None:
            for rank, (word, _probability) in enumerate(self._next_words(context)):
                candidates.append(self._candidate(head, word, "ngram", rank=rank))
        elif len(partial) >= 2:
            for rank, word in enumerate(self._vocabulary_matches(partial)):
                candidates.append(self._candidate(head, word, "word", rank=rank))

        return candidates

    def _candidate(
        self, head: str, tail: str, source: str, rank: int = 0, backoff: float = 0.0
    ) -> _Candidate:
        tail = self._adjust_tail_case(head, tail)
        text = self._compose(head, tail).strip() or tail.strip()
        return _Candidate(
            text=text,
            source=source,
            tail_text=tail,
            rank=rank,
            backoff=backoff,
            added_words=max(1, len(_WORD_TOKEN_RE.findall(tail))),
        )

    # ------------------------------------------------------------------
    # Matching helpers
    # ------------------------------------------------------------------
    def _tail_matches(self, tail: str, partial: str) -> bool:
        """True when the first word of ``tail`` completes the typed ``partial``."""
        return self._prefix_match(self._first_added_word(tail), partial)

    @staticmethod
    def _first_added_word(tail: str) -> str:
        words = _WORD_TOKEN_RE.findall(tail or "")
        return normalize_token(words[0]) if words else ""

    @staticmethod
    def _prefix_match(word: str, partial: str) -> bool:
        if not word or not partial:
            return False
        if word.startswith(partial):
            return True
        if len(partial) >= 4 and word[:1] == partial[:1]:
            return _levenshtein_within_one(partial, word)
        return False

    def _next_words(self, context: Sequence[str]) -> List[Tuple[str, float]]:
        """Back-off next-word prediction (trigram -> bigram, never unigram)."""
        context = [word for word in context if word]
        if not context:
            return []

        scores: Dict[str, float] = {}
        if len(context) >= 2:
            # Require real two-word evidence; a lone previous word is too weak
            # and produces ungrammatical fragments such as "I am go home".
            options = self._trigrams.get((context[-2], context[-1])) or {}
        else:
            options = self._bigrams.get(context[-1]) or {}
        total = sum(options.values())
        if total:
            weight = 1.0 if len(context) >= 2 else 0.4
            for word, count in options.items():
                if word in (BOS, EOS):
                    continue
                scores[word] = weight * (count / float(total))

        ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
        return ranked[:4]

    def _vocabulary_matches(self, partial: str) -> List[str]:
        matches: List[str] = []
        for word, _count in self._vocabulary:
            if len(word) <= len(partial) or word == partial:
                continue
            if self._prefix_match(word, partial):
                matches.append(word)
            if len(matches) >= 4:
                break
        return matches

    # ------------------------------------------------------------------
    # Personalization
    # ------------------------------------------------------------------
    def _personal_tails(self, user: str, context: Sequence[str]) -> List[Tuple[str, int]]:
        with self._memory_lock:
            self._ensure_memory_loaded(user)
            contexts = self._context_counts.get(user, {})
        results: List[Tuple[str, int]] = []
        for size in range(min(len(context), MAX_CONTEXT_TOKENS), 0, -1):
            key = " ".join(context[len(context) - size:])
            for tail, count in contexts.get(key, {}).items():
                if count >= 2:
                    results.append((tail, count))
        return results[:4]

    def _personal_bonus(self, user: str, context: Sequence[str], tail: str) -> float:
        if not tail:
            return 0.0
        with self._memory_lock:
            self._ensure_memory_loaded(user)
            context_bonus = 0.0
            for size in range(min(len(context), MAX_CONTEXT_TOKENS), 0, -1):
                key = " ".join(context[len(context) - size:])
                count = self._tail_count(self._context_counts.get(user, {}).get(key, {}), tail)
                if count:
                    context_bonus = 0.03 * count / max(1, size)
                    break
            global_count = self._tail_count(self._tail_counts.get(user, {}), tail)
        return min(0.09, context_bonus + 0.01 * global_count)

    @staticmethod
    def _tail_key(tail: str) -> str:
        return normalize_phrase_tail(tail).lower()

    @classmethod
    def _tail_count(cls, tails: Dict[str, int], tail: str) -> int:
        """Case-insensitive lookup that preserves the stored surface form."""
        if not tails:
            return 0
        direct = tails.get(tail)
        if direct is not None:
            return int(direct)
        key = cls._tail_key(tail)
        for stored, count in tails.items():
            if cls._tail_key(stored) == key:
                return int(count)
        return 0

    @classmethod
    def _store_tail(cls, tails: Dict[str, int], tail: str, count: int) -> None:
        key = cls._tail_key(tail)
        for stored in list(tails.keys()):
            if cls._tail_key(stored) == key:
                tails[stored] = count
                return
        tails[tail] = count

    def _ensure_memory_loaded(self, user: str) -> None:
        """Resolve the phrase memory for ``user`` without ever blocking a prediction.

        Callers must hold ``_memory_lock``.  When MongoDB is already connected
        the (fast) read happens inline; otherwise it is deferred to a daemon
        thread so an unavailable database can never slow the UI down.
        """
        if user in self._memory_loaded:
            return
        now = monotonic()
        if now - self._memory_attempt_at.get(user, 0.0) < MEMORY_RETRY_SECONDS:
            return
        self._memory_attempt_at[user] = now
        if self._repository.storage_available:
            self._load_memory(user)
            return
        self._memory_loaded.add(user)
        try:
            threading.Thread(
                target=self._load_memory, args=(user,), name="phrase-memory-load", daemon=True
            ).start()
        except Exception:  # pragma: no cover - thread limits
            pass

    def _load_memory(self, user: str) -> None:
        try:
            entries = self._repository.list_for_user(user)
        except Exception as exc:  # pragma: no cover - optional storage
            logger.warning("Phrase memory load skipped for %s: %s", user, exc)
            return
        with self._memory_lock:
            for entry in entries:
                tails = self._context_counts.setdefault(entry.user_id, {}).setdefault(
                    entry.context_key, {}
                )
                existing = self._tail_count(tails, entry.tail)
                if entry.count > existing:
                    self._store_tail(tails, entry.tail, entry.count)
                global_tails = self._tail_counts.setdefault(entry.user_id, {})
                if entry.count > self._tail_count(global_tails, entry.tail):
                    self._store_tail(global_tails, entry.tail, entry.count)

    def _persist_async(self, entry: PhraseMemoryEntry) -> None:
        def _write() -> None:
            try:
                self._repository.save(entry)
            except Exception as exc:  # pragma: no cover - optional storage
                logger.warning("Phrase memory persist failed: %s", exc)

        try:
            threading.Thread(target=_write, name="phrase-memory-write", daemon=True).start()
        except Exception:  # pragma: no cover - thread limits
            pass

    # ------------------------------------------------------------------
    # Text plumbing
    # ------------------------------------------------------------------
    @staticmethod
    def clause_start(text: str) -> int:
        """Index where the clause being completed starts inside ``text``."""
        if not text:
            return 0
        start = 0
        for match in _CLAUSE_BOUNDARY_RE.finditer(text):
            start = match.end()
        while start < len(text) and text[start].isspace():
            start += 1
        return start

    @staticmethod
    def _reclaim_punctuation(head: str, matched_token: str, tail: str) -> str:
        """Re-attach a comma that belonged to the token being completed.

        The corpus stores ``"Hello, how are you?"``; when the user has typed
        ``"Hello"`` the trailing comma of the matched token would otherwise be
        lost and the suggestion would read ``"Hello how are you?"``.
        """
        punctuation = matched_token[-1:] if matched_token[-1:] in ",;:" else ""
        if punctuation and not head.rstrip().endswith(punctuation):
            return punctuation + " " + tail
        return tail

    @staticmethod
    def _compose(head: str, tail: str) -> str:
        if not head:
            return tail.strip()
        if not tail:
            return head
        if tail[0] in _PUNCTUATION:
            return head.rstrip() + tail
        if head[-1:].isspace():
            return head + tail
        return head + " " + tail

    @staticmethod
    def _adjust_tail_case(head: str, tail: str) -> str:
        """Keep a mid-clause tail lowercase; keep sentence starts capitalized."""
        if not head.strip() or not tail:
            return tail
        first = _WORD_TOKEN_RE.match(tail)
        if not first:
            return tail
        word = first.group(0)
        if word[:1].isupper() and word.lower() != "i":
            return word.lower() + tail[len(word):]
        return tail

    @staticmethod
    def _normalize_user(user_id: Optional[str]) -> str:
        value = str(user_id or Config.DEFAULT_PROFILE_ID).strip()
        return (value or "local-user")[:64]

    # ------------------------------------------------------------------
    # Cache
    # ------------------------------------------------------------------
    def _cache_get(self, key: str) -> Optional[Dict[str, Any]]:
        with self._cache_lock:
            result = self._cache.get(key)
            if result is not None:
                self._cache.move_to_end(key)
            return result

    def _cache_put(self, key: str, value: Dict[str, Any]) -> None:
        with self._cache_lock:
            self._cache[key] = value
            self._cache.move_to_end(key)
            while len(self._cache) > MAX_CACHE_ENTRIES:
                self._cache.popitem(last=False)

    def clear_cache(self) -> None:
        with self._cache_lock:
            self._cache.clear()

    def invalidate_cache(self, user_id: Optional[str] = None) -> None:
        """Drop cached completions for one user (all users when omitted)."""
        suffix = "|{}".format(self._normalize_user(user_id)) if user_id is not None else None
        with self._cache_lock:
            if suffix is None:
                self._cache.clear()
                return
            for key in [key for key in self._cache if key.endswith(suffix)]:
                self._cache.pop(key, None)


sentence_completion_service = SentenceCompletionService()
