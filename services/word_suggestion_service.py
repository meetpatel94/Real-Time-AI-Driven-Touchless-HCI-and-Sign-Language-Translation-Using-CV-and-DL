import threading
from typing import List, Dict

# High-frequency English vocabulary prioritized for predictive text
COMMON_WORDS = [
    "HELLO", "HELP", "HELMET", "HELICOPTER", "HELPFUL", "HEALTH", "HEAR", "HEART", "HEAVY", "HEAD",
    "WHAT", "WHEN", "WHERE", "WHICH", "WHO", "WHY", "WORLD", "WOLF", "WORK", "WORD", "WORRY", "WOMAN", "WATER", "WANT", "WELCOME", "WHITE", "WRITE", "WAIT", "WALK", "WATCH",
    "THE", "THIS", "THAT", "THERE", "THEIR", "THEN", "THEM", "THESE", "THEY", "THING", "THINK", "THANKS", "THANK", "TIME", "TODAY", "TOMORROW", "TOGETHER", "TRAVEL",
    "PLEASE", "PEOPLE", "PLACE", "PLAY", "POINT", "POWER", "PROBLEM", "PROGRAM", "PROJECT", "PROUD", "PHONE", "PERSON", "PEACE",
    "GOOD", "GREAT", "GIVE", "GAME", "GIRL", "GROUP", "GROW", "GUIDE", "GENERAL", "GREEN", "GOAL",
    "HOW", "HAVE", "HOME", "HOUSE", "HOPE", "HAPPY", "HAPPEN", "HERE", "HOLD", "HAND", "HUMAN",
    "YES", "YOU", "YOUR", "YEAR", "YOUNG", "YESTERDAY", "YELLOW",
    "NO", "NOT", "NOW", "NEW", "NAME", "NEVER", "NEED", "NIGHT", "NEXT", "NUMBER", "NICE", "NATURE",
    "CAN", "COME", "COULD", "CALL", "CARE", "CASE", "CHILD", "CITY", "CLEAN", "CLEAR", "CLOSE", "COLOR", "COMPANY", "COUNTRY", "CHANGE",
    "ABOUT", "AFTER", "AGAIN", "ALL", "ALSO", "ALWAYS", "AND", "ANY", "ASK", "AWAY", "ANIMAL", "ANSWER", "AREA", "AROUND", "ABLE",
    "BEAUTIFUL", "BECAUSE", "BECOME", "BEFORE", "BEGIN", "BEST", "BETTER", "BETWEEN", "BIG", "BOOK", "BOTH", "BOY", "BROTHER", "BUILD", "BUSY",
    "DAY", "DEAR", "DOOR", "DOWN", "DRIVE", "DREAM", "DRINK", "DURING", "DIFFERENT", "DOCTOR",
    "EACH", "EARLY", "EASY", "EAT", "EDUCATION", "ENOUGH", "EVEN", "EVERY", "EYE", "EXCELLENT", "EXAMPLE",
    "FAMILY", "FAST", "FATHER", "FEEL", "FEW", "FIND", "FIRST", "FOLLOW", "FOOD", "FOR", "FORM", "FOUND", "FRIEND", "FROM", "FUTURE",
    "KNOW", "KIND", "KEEP", "KID", "KEY", "KITCHEN",
    "LIKE", "LOOK", "LOVE", "LIFE", "LITTLE", "LIVE", "LONG", "LEARN", "LEAVE", "LEFT", "LIGHT", "LISTEN", "LARGE", "LATER",
    "MAKE", "MAN", "MANY", "ME", "MEAN", "MORE", "MOST", "MOTHER", "MOVE", "MUCH", "MUST", "MY", "MEET", "MUSIC", "MORNING",
    "ORDER", "OPEN", "OTHER", "OUR", "OUT", "OVER", "OWN", "OFFICE", "OLD", "ONE", "ONLY",
    "READ", "READY", "REAL", "REASON", "REMEMBER", "RIGHT", "ROOM", "RUN", "RIVER", "ROAD",
    "SEE", "SHE", "SHOULD", "SHOW", "SIDE", "SMALL", "SOME", "SOMETHING", "SOMETIMES", "SON", "SOON", "START", "STILL", "STOP", "STUDY", "SUCH", "SURE", "SYSTEM", "SCHOOL", "SMILE",
    "TAKE", "TALK", "TELL", "THAN", "TOO", "TRY", "TURN", "TWO", "TEACHER", "TRAIN", "TREE",
    "UNDER", "UNTIL", "UP", "UPON", "US", "USE", "USUALLY", "UNDERSTAND", "UNIVERSE",
    "VERY", "VIEW", "VISIT", "VOICE", "VACATION", "VALUE",
    "JUST", "JOB", "JOIN", "JOY", "JUMP", "JUDGE",
    "FORGET", "FORGIVE", "FAVORITE", "FINISH", "FORWARD", "FREEDOM"
]

class TrieNode:
    def __init__(self):
        self.children: Dict[str, TrieNode] = {}
        self.is_end_of_word = False
        self.word = ""

class WordSuggestionService:
    """Thread-safe, lightweight in-memory Trie for dynamic prefix word suggestions."""
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(WordSuggestionService, cls).__new__(cls)
                cls._instance._initialize()
            return cls._instance

    def _initialize(self):
        self.root = TrieNode()
        self._build_trie(COMMON_WORDS)

    def _build_trie(self, words: List[str]):
        for word in words:
            self._insert(word.strip().upper())

    def _insert(self, word: str):
        node = self.root
        for char in word:
            if char not in node.children:
                node.children[char] = TrieNode()
            node = node.children[char]
        node.is_end_of_word = True
        node.word = word

    def get_suggestions(self, prefix: str, max_results: int = 5) -> List[str]:
        prefix = (prefix or "").strip().upper()
        if not prefix:
            return []

        node = self.root
        for char in prefix:
            if char not in node.children:
                return []
            node = node.children[char]

        results: List[str] = []
        self._dfs(node, results, max_results)

        # Sort: Exact match first, then by shortest length, then alphabetical
        results.sort(key=lambda w: (0 if w == prefix else 1, len(w), w))
        return results[:max_results]

    def _dfs(self, node: TrieNode, results: List[str], max_results: int):
        if len(results) >= max_results * 3:
            return
        if node.is_end_of_word:
            results.append(node.word)
        for char in sorted(node.children.keys()):
            self._dfs(node.children[char], results, max_results)

word_suggestion_service = WordSuggestionService()