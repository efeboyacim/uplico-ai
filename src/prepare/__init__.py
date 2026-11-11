"""Contract preparation: clause splitting, injection scanning, masking and indexing.

`prepare_contract` is the seam between the raw contract and everything downstream. The raw text
and the PII mapping are written only to the tenant's own `app.db`; the clauses, the injection
flags, the index and every audit payload it produces hold masked text only.
"""

import logging
from bisect import bisect_left
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from src.models import AuditEvent, Clause, InjectionFlag, RawClause
from src.prepare.clauses import split_clauses
from src.prepare.index import ClauseIndex, drop_index, put_index
from src.prepare.injection import scan_injection
from src.prepare.pii import KINDS, PLACEHOLDER, mask
from src.store import Store
from src.text import is_invisible, strip_invisible

logger = logging.getLogger(__name__)

_ACTOR = "system"


class PrepareError(Exception):
    pass


@dataclass(frozen=True)
class PreparedContract:
    review_id: str
    clauses: tuple[Clause, ...]
    injection_flags: tuple[InjectionFlag, ...]


def _counts_by_kind(mapping: Mapping[str, str]) -> dict[str, int]:
    counts = Counter(placeholder[1 : placeholder.rindex("_")] for placeholder in mapping)
    return {kind: counts.get(kind, 0) for kind in KINDS}


# (start, end) in the invisible-stripped clause text and (start, end) in the masked clause text
# of one masked value.
_Segment = tuple[int, int, int, int]


def _masked_segments(source: str, masked: str, mapping: Mapping[str, str]) -> list[_Segment]:
    """Align the masked clause text with its source: one segment per placeholder put in by mask.

    `masked` is `source` with some values replaced by their placeholders, so the text between
    placeholders must match `source` exactly. A placeholder that was already written in the
    contract (not one of ours at that position) is matched as literal text. Any mismatch raises.
    """
    segments: list[_Segment] = []
    s = m = 0
    for ph in PLACEHOLDER.finditer(masked):
        literal = masked[m : ph.start()]
        if not source.startswith(literal, s):
            raise PrepareError("Maskeli madde metni kaynağıyla hizalanamadı.")
        s += len(literal)
        value = mapping.get(ph.group())
        if value and source.startswith(value, s):
            segments.append((s, s + len(value), ph.start(), ph.end()))
            s += len(value)
        elif source.startswith(ph.group(), s):
            s += len(ph.group())
        else:
            raise PrepareError("Maskeli madde metni kaynağıyla hizalanamadı.")
        m = ph.end()
    if source[s:] != masked[m:]:
        raise PrepareError("Maskeli madde metni kaynağıyla hizalanamadı.")
    return segments


class _ClausePhrases:
    """Maps the flags of one clause onto its masked text (see agent_design.md section 5).

    Everything that depends only on the clause (the stripped text, the raw-to-stripped offset
    table, the masked/source alignment) is computed once, so each flag costs a lookup, a bisect
    and a search near its own match instead of a pass over the whole clause.
    """

    def __init__(self, raw: RawClause, masked: str, mapping: Mapping[str, str]) -> None:
        self._raw_start = raw.start
        self._masked = masked
        self._mapping = mapping
        self._source = strip_invisible(raw.text)
        # _stripped_at[rel]: offset in the stripped clause of raw clause offset `rel`.
        self._stripped_at = [0]
        for ch in raw.text:
            self._stripped_at.append(self._stripped_at[-1] + (0 if is_invisible(ch) else 1))
        # Built on first use, so a clause whose phrases are all missing raises the phrase error
        # first, as a per-flag computation would.
        self._segments: list[_Segment] | None = None
        self._segment_starts: list[int] = []

    def _to_masked(self, pos: int, segments: list[_Segment], *, is_end: bool) -> int:
        """Map a source position to the masked text; inside a value it moves to the placeholder
        edge that keeps the whole placeholder in the range (its start for a range start, else its
        end)."""
        # The last segment starting before `pos`; segments are sorted and do not overlap.
        i = bisect_left(self._segment_starts, pos)
        if i == 0:
            return pos
        _, s_end, m_start, m_end = segments[i - 1]
        if pos < s_end:
            return m_end if is_end else m_start
        return pos + (m_end - s_end)

    def phrase(self, flag: InjectionFlag) -> str:
        """The flag's phrase as a piece of the masked clause text.

        The raw phrase is `strip_invisible(raw.text[a:b]).strip()`, so it is a substring of the
        stripped clause. Of its occurrences, the one holding the pattern match (`flag.start/end`)
        is taken; its ends are mapped onto the masked text, widened to whole placeholders.
        """
        match_start = self._stripped_at[flag.start - self._raw_start]
        match_end = self._stripped_at[flag.end - self._raw_start]
        phrase = flag.phrase
        # The last occurrence starting at or before the match; it must also reach the match's
        # end. An occurrence starting before `match_end - len(phrase)` cannot reach it, so the
        # search is bounded to the window where the right occurrence can start.
        start = self._source.rfind(
            phrase, max(0, match_end - len(phrase)), match_start + len(phrase)
        )
        if start == -1:
            raise PrepareError("Enjeksiyon işaretinin ifadesi maddede bulunamadı.")
        segments = self._segments
        if segments is None:
            segments = _masked_segments(self._source, self._masked, self._mapping)
            self._segments = segments
            self._segment_starts = [segment[0] for segment in segments]
        masked_start = self._to_masked(start, segments, is_end=False)
        masked_end = self._to_masked(start + len(phrase), segments, is_end=True)
        return self._masked[masked_start:masked_end]


def _cleanup_step(name: str, review_id: str, step: Callable[[], object]) -> None:
    """Run one cleanup step; its own failure is logged (type only) and never hides the original."""
    try:
        step()
    except Exception as exc:
        # Only the exception type: its message may carry contract text.
        logger.error(
            "Hazırlık temizliği başarısız: adım=%s, inceleme=%s, hata=%s",
            name,
            review_id,
            type(exc).__name__,
        )


def prepare_contract(store: Store, text: str, *, provider: str, model: str) -> PreparedContract:
    """Store, split, scan, mask and index a contract; every step is written to the audit log.

    The original `text` is stored unchanged. If anything fails after the review row exists, the
    review is marked failed, `review_failed` (`reason: prepare_error`, no exception text) is
    appended, the index is dropped and the original exception is re-raised.
    """
    review_id = store.create_review(text, provider, model)

    def audit(event: AuditEvent, payload: Mapping[str, object]) -> None:
        store.append_audit(review_id, _ACTOR, event, payload)

    try:
        audit("review_started", {"provider": provider, "model": model})

        raw_clauses = split_clauses(text)
        audit("clauses_split", {"count": len(raw_clauses)})

        raw_flags = scan_injection(text, raw_clauses)

        # Names are discovered in the party and signature blocks but replaced everywhere, so the
        # mapping is built from the whole text first. Masking runs on the invisible-stripped copy
        # so a zero-width character inside a number does not hide it. The mapping returned by each
        # call is threaded into the next one: a value found only at clause level is kept too.
        _, mapping = mask(strip_invisible(text), {})
        clauses: list[Clause] = []
        for raw in raw_clauses:
            masked_text, mapping = mask(strip_invisible(raw.text), mapping)
            clauses.append(
                Clause(
                    clause_id=raw.clause_id,
                    clause_no=raw.clause_no,
                    start=raw.start,
                    end=raw.end,
                    text=masked_text,
                )
            )
        # A phrase is never masked on its own: a sentence boundary (a line break) can cut a known
        # name in two, and the half outside the phrase would stay raw. The stored phrase is the
        # matching piece of the masked clause instead, widened to whole placeholders.
        # The per-clause work is done once per clause, however many flags it holds.
        raw_by_id = {raw.clause_id: raw for raw in raw_clauses}
        masked_by_id = {clause.clause_id: clause.text for clause in clauses}
        phrases_by_id: dict[str, _ClausePhrases] = {}
        flags: list[InjectionFlag] = []
        for raw_flag in raw_flags:
            if not raw_flag.phrase:
                flags.append(raw_flag)
                continue
            clause_phrases = phrases_by_id.get(raw_flag.clause_id)
            if clause_phrases is None:
                clause_phrases = _ClausePhrases(
                    raw_by_id[raw_flag.clause_id], masked_by_id[raw_flag.clause_id], mapping
                )
                phrases_by_id[raw_flag.clause_id] = clause_phrases
            masked_phrase = clause_phrases.phrase(raw_flag)
            flags.append(raw_flag.model_copy(update={"phrase": masked_phrase}))

        store.add_pii_entries(review_id, mapping)
        if not store.save_prepared(review_id, clauses, flags):
            raise PrepareError("Hazırlanan maddeler kaydedilemedi: inceleme çalışır durumda değil.")

        for flag in flags:
            audit("injection_flagged", flag.model_dump(mode="json"))
        audit("pii_masked", {"counts": _counts_by_kind(mapping)})

        index = ClauseIndex(clauses)
        put_index(store.tenant_id, review_id, index)
        audit("index_built", {"count": len(index)})
    except BaseException:
        _cleanup_step("drop_index", review_id, lambda: drop_index(store.tenant_id, review_id))
        _cleanup_step("mark_failed", review_id, lambda: store.mark_failed(review_id))
        _cleanup_step(
            "review_failed", review_id, lambda: audit("review_failed", {"reason": "prepare_error"})
        )
        raise

    return PreparedContract(
        review_id=review_id, clauses=tuple(clauses), injection_flags=tuple(flags)
    )
