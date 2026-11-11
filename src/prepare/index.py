"""In-memory BM25 clause index over masked clauses, plus the per-review registry.

The index is built from masked clauses only. It is never put into graph state (it cannot be
serialized into a checkpoint); the registry below keeps it in process memory instead.
"""

import re
import threading
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass

from rank_bm25 import BM25Plus

from src.models import Clause
from src.prepare.pii import PLACEHOLDER

MAX_RESULTS = 5
SNIPPET_CHARS = 300

_WORD = re.compile(r"[^\W_]+")
_PREFIX_LENGTHS = (3, 4, 5)


def tokenize(text: str) -> list[str]:
    """Split into lower-cased words and emit the 3-, 4- and 5-character prefixes of each.

    Words shorter than 3 characters are emitted as they are. There are no word lists or stemming
    tables: related forms meet on a shared prefix key.
    """
    folded = unicodedata.normalize("NFC", text).replace("İ", "i").replace("I", "ı").lower()
    tokens: list[str] = []
    for word in _WORD.findall(folded):
        if len(word) < _PREFIX_LENGTHS[0]:
            tokens.append(word)
            continue
        tokens.extend(word[:n] for n in _PREFIX_LENGTHS if n <= len(word))
    return tokens


def _keys(text: str) -> list[str]:
    """Index keys of masked text: placeholders are removed first, so `[KISI_1]` gives no `kıs`,
    `[TELEFON_1]` no `tel` and neither a `1` (see agent_design.md section 4)."""
    return tokenize(PLACEHOLDER.sub(" ", text))


@dataclass(frozen=True)
class SearchHit:
    clause_id: str
    clause_no: str | None
    snippet: str


class ClauseIndex:
    def __init__(self, clauses: Sequence[Clause]) -> None:
        self._clauses: tuple[Clause, ...] = tuple(clauses)
        self._by_id: dict[str, Clause] = {c.clause_id: c for c in self._clauses}
        self._token_sets: list[frozenset[str]] = []
        corpus: list[list[str]] = []
        for clause in self._clauses:
            tokens = _keys(clause.text)
            corpus.append(tokens)
            self._token_sets.append(frozenset(tokens))
        # BM25Plus raises ZeroDivisionError on an empty corpus.
        self._bm25: BM25Plus | None = BM25Plus(corpus) if corpus else None

    def __len__(self) -> int:
        return len(self._clauses)

    def get(self, clause_id: str) -> Clause | None:
        return self._by_id.get(clause_id)

    def search(self, query: str, limit: int = MAX_RESULTS) -> list[SearchHit]:
        """Return up to `limit` clauses sharing at least one key with the query, best first.

        Eligibility is a shared key, not the score: BM25Plus gives a base score to documents
        that contain no query term. Ties keep document order.
        """
        query_tokens = _keys(query)
        if self._bm25 is None or not query_tokens or limit <= 0:
            return []
        wanted = set(query_tokens)
        eligible = [i for i, keys in enumerate(self._token_sets) if keys & wanted]
        if not eligible:
            # Also keeps BM25 off a corpus without a single key (average length 0).
            return []
        scores = self._bm25.get_scores(query_tokens)
        eligible.sort(key=lambda i: (-float(scores[i]), i))
        return [
            SearchHit(
                clause_id=self._clauses[i].clause_id,
                clause_no=self._clauses[i].clause_no,
                snippet=self._clauses[i].text[:SNIPPET_CHARS],
            )
            for i in eligible[:limit]
        ]


_registry: dict[tuple[str, str], ClauseIndex] = {}
_registry_lock = threading.Lock()


def put_index(tenant_id: str, review_id: str, index: ClauseIndex) -> None:
    with _registry_lock:
        _registry[(tenant_id, review_id)] = index


def get_index(tenant_id: str, review_id: str) -> ClauseIndex | None:
    with _registry_lock:
        return _registry.get((tenant_id, review_id))


def drop_index(tenant_id: str, review_id: str) -> None:
    with _registry_lock:
        _registry.pop((tenant_id, review_id), None)


def clear_indexes() -> None:
    with _registry_lock:
        _registry.clear()
