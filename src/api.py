"""HTTP API: start a review, read it and its audit log, take the lawyer's decision; serve the page.

Design (`docs/architecture.md` §3, `docs/agent_design.md` §6 and §7):

- Tenant, role and name come only from the `X-API-Key` header (`get_principal`). Every endpoint
  other than `GET` carries `Depends(require_lawyer)`. Nothing in the request names a tenant or role.
- A review is looked up only in the caller's own tenant store; anything else is 404.
- Responses are built from fixed field lists (the `*View` models below), never by dumping a record.
  A lawyer gets the viewer fields plus explicitly added ones, unmasked in one function.
- No exception text reaches a response or the log (it may carry contract text): the response has
  a fixed Turkish message and the log the exception class name.
- The decision endpoint runs under the tenant's decision lock: re-read the record, check the
  status (409) and the record checks (422), only then mask `edited_text` and resume the graph.
- Synchronous endpoints only (`SqliteSaver` has no async support).
- `GET /` is the one endpoint without a key: the static page, read once at startup, the same bytes
  for every request, with a CSP that allows only the hash of its single `<script>` block.
"""

import base64
import hashlib
import logging
import re
import sys
import threading
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Annotated, Any, Self

import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.config import ConfigError, Principal, Settings, load_settings_or_exit
from src.decision import (
    MAX_EDITED_TEXT_CHARS,
    check_decision,
    duplicate_rule_id_error,
    entry_format_error,
)
from src.graph import (
    ReviewNotAwaitingError,
    get_review_graph,
    initial_state,
    resume_command,
    review_config,
)
from src.llm import classify_provider_error
from src.models import (
    AuditRecord,
    Clause,
    CodeCheck,
    ContractDecision,
    Finding,
    FindingAction,
    FindingDecision,
    FindingOutcome,
    FindingStatus,
    InjectionFlag,
    InjectionKind,
    InterruptReason,
    LawyerDecision,
    LawyerReport,
    MaskedText,
    NoFindingReason,
    Proposal,
    ProposalAction,
    ReviewReason,
    ReviewRecord,
    ReviewStatus,
)
from src.prepare import prepare_contract
from src.prepare.index import drop_index
from src.prepare.pii import mask, unmask
from src.report import build_lawyer_report, build_report
from src.store import Store, UnknownTenantError
from src.text import strip_invisible

logger = logging.getLogger(__name__)

SYSTEM_ACTOR = "system"

# Characters of the contract text, not bytes. Checked in the endpoint so that it comes after
# authentication and answers 413 rather than 422.
MAX_CONTRACT_CHARS = 2_000_000

_MSG_UNAUTHORIZED = "API anahtarı eksik ya da tanınmıyor."
_MSG_FORBIDDEN = "Bu işlem için lawyer rolü gerekir."
_MSG_NOT_FOUND = "İnceleme bulunamadı."
_MSG_PROVIDER_CONFIG = "Model sağlayıcısının kimlik ya da yapılandırma ayarı hatalı."
_MSG_INTERNAL = "İnceleme tamamlanamadı: beklenmeyen bir hata oluştu."
_MSG_TOO_LARGE = "Sözleşme metni çok uzun: en fazla 2.000.000 karakter kabul edilir."
_MSG_NOT_AWAITING = "İnceleme avukat kararı beklemiyor."
_MSG_NOT_DECIDED = "İnceleme için son karar henüz verilmedi."
_MSG_DECISION_FAILED = "Karar kaydedilemedi: beklenmeyen bir hata oluştu."
_RESUMABLE_NEXT = (("human_review",), ("finalize",))

# The UI page (`docs/architecture.md` §3 "Arayüz"). Read once by `create_app`; missing -> no start.
PAGE_PATH = Path(__file__).resolve().parent / "static" / "index.html"
_SCRIPT_BLOCK = re.compile(r"<script>(.*?)</script>", re.DOTALL)


# --- Request model --------------------------------------------------------------------------------


class ReviewRequest(BaseModel):
    """`POST /reviews` body. Unknown fields (`tenant`, `role`, ...) are ignored, not rejected."""

    model_config = ConfigDict(extra="ignore")

    text: str = Field(min_length=1)

    @field_validator("text")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        # Checked, never changed: the original text is stored exactly as uploaded.
        if not value.strip():
            raise ValueError("Sözleşme metni boş olamaz.")
        return value


class FindingDecisionRequest(BaseModel):
    """One entry of the decision body. The one request model that rejects unknown fields: a
    mistyped field ignored here would silently drop the lawyer's decision (code_standards.md)."""

    model_config = ConfigDict(extra="forbid")

    rule_id: str = Field(min_length=1)
    action: FindingAction | None = None
    status: FindingStatus | None = None
    proposal_action: ProposalAction | None = None
    edited_text: str | None = Field(default=None, max_length=MAX_EDITED_TEXT_CHARS)

    @model_validator(mode="after")
    def _format_rules(self) -> Self:
        error = entry_format_error(self.action, self.status, self.proposal_action, self.edited_text)
        if error is not None:
            raise ValueError(error)
        return self


class DecisionRequest(BaseModel):
    """`POST /reviews/{id}/decision` body. Unknown top-level fields (`role`, `reviewer`,
    `tenant`, ...) are ignored, not rejected; the decider's name comes from the key."""

    model_config = ConfigDict(extra="ignore")

    findings: list[FindingDecisionRequest] = Field(default_factory=list)
    contract_decision: ContractDecision

    @model_validator(mode="after")
    def _rule_ids_unique(self) -> Self:
        error = duplicate_rule_id_error([entry.rule_id for entry in self.findings])
        if error is not None:
            raise ValueError(error)
        return self


# --- Response models: each names its fields one by one --------------------------------------------


class _View(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ClauseView(_View):
    clause_id: str
    clause_no: str | None
    text: str


class LawyerClauseView(ClauseView):
    start: int
    end: int


class InjectionFlagView(_View):
    clause_id: str
    kind: InjectionKind
    phrase: str


class LawyerInjectionFlagView(InjectionFlagView):
    start: int
    end: int
    detail: str | None


class ProposalView(_View):
    clause_id: str
    proposed_text: str
    rationale: str
    code_check: CodeCheck | None


class FindingView(_View):
    rule_id: str
    status: FindingStatus
    clause_id: str | None
    quote: str | None
    value: float | None
    unit: str | None
    reasoning: str
    proposal: ProposalView | None
    escalation_reason: str | None
    no_finding_reason: NoFindingReason | None
    interrupted_by: InterruptReason | None
    needs_review: bool
    review_reasons: list[ReviewReason]
    # The lawyer's decision; all null before it.
    final_status: FindingStatus | None
    action: FindingAction | None
    proposal_action: ProposalAction | None
    edited_text: str | None


class ReviewView(_View):
    review_id: str
    status: ReviewStatus
    created_at: str
    # Sequence (covariant): the lawyer view narrows the element types.
    clauses: Sequence[ClauseView]
    injection_flags: Sequence[InjectionFlagView]
    findings: list[FindingView]
    contract_decision: ContractDecision | None
    decided_at: str | None
    decided_by: str | None


class LawyerReviewView(ReviewView):
    original_text: str
    clauses: Sequence[LawyerClauseView]
    injection_flags: Sequence[LawyerInjectionFlagView]


class AuditEntryView(_View):
    id: int
    ts: str
    review_id: str
    actor: str
    event: str
    payload: dict[str, object]


# --- Building the views ---------------------------------------------------------------------------


def _clause_view(clause: Clause) -> ClauseView:
    return ClauseView(clause_id=clause.clause_id, clause_no=clause.clause_no, text=clause.text)


def _lawyer_clause_view(clause: Clause) -> LawyerClauseView:
    base = _clause_view(clause)
    return LawyerClauseView(**base.model_dump(), start=clause.start, end=clause.end)


def _flag_view(flag: InjectionFlag) -> InjectionFlagView:
    return InjectionFlagView(clause_id=flag.clause_id, kind=flag.kind, phrase=flag.phrase)


def _lawyer_flag_view(flag: InjectionFlag) -> LawyerInjectionFlagView:
    base = _flag_view(flag)
    return LawyerInjectionFlagView(
        **base.model_dump(), start=flag.start, end=flag.end, detail=flag.detail
    )


def _proposal_view(proposal: Proposal) -> ProposalView:
    return ProposalView(
        clause_id=proposal.clause_id,
        proposed_text=proposal.proposed_text,
        rationale=proposal.rationale,
        code_check=proposal.code_check,
    )


def _finding_view(finding: Finding, outcome: FindingOutcome | None) -> FindingView:
    return FindingView(
        rule_id=finding.rule_id,
        status=finding.status,
        clause_id=finding.clause_id,
        quote=finding.quote,
        value=finding.value,
        unit=finding.unit,
        reasoning=finding.reasoning,
        proposal=_proposal_view(finding.proposal) if finding.proposal else None,
        escalation_reason=finding.escalation_reason,
        no_finding_reason=finding.no_finding_reason,
        interrupted_by=finding.interrupted_by,
        needs_review=finding.needs_review,
        review_reasons=list(finding.review_reasons),
        final_status=outcome.final_status if outcome is not None else None,
        action=outcome.action if outcome is not None else None,
        proposal_action=outcome.proposal_action if outcome is not None else None,
        edited_text=outcome.edited_text if outcome is not None else None,
    )


def _finding_views(record: ReviewRecord) -> list[FindingView]:
    outcomes = {o.rule_id: o for o in record.decision.findings} if record.decision else {}
    return [_finding_view(f, outcomes.get(f.rule_id)) for f in record.findings]


def _viewer_review(record: ReviewRecord) -> ReviewView:
    return ReviewView(
        review_id=record.review_id,
        status=record.status,
        created_at=record.created_at,
        clauses=[_clause_view(c) for c in record.clauses],
        injection_flags=[_flag_view(f) for f in record.injection_flags],
        findings=_finding_views(record),
        contract_decision=record.decision.contract_decision if record.decision else None,
        decided_at=record.decided_at,
        decided_by=record.decided_by,
    )


def _lawyer_review(record: ReviewRecord) -> LawyerReviewView:
    """The lawyer view, still masked: the viewer fields plus the explicitly added ones."""
    base = _viewer_review(record)
    return LawyerReviewView(
        review_id=base.review_id,
        status=base.status,
        created_at=base.created_at,
        original_text=record.original_text,
        clauses=[_lawyer_clause_view(c) for c in record.clauses],
        injection_flags=[_lawyer_flag_view(f) for f in record.injection_flags],
        findings=base.findings,
        contract_decision=base.contract_decision,
        decided_at=base.decided_at,
        decided_by=base.decided_by,
    )


def _unmask_lawyer_view(view: LawyerReviewView, mapping: Mapping[str, str]) -> LawyerReviewView:
    """Puts raw values back into a built lawyer view; one such function per lawyer response model
    (`_unmask_lawyer_report` is the other). Covers every text field of the view: clause text, flag
    phrase, quote, reasoning, escalation reason, proposal, the lawyer's edited text."""

    def put_back(text: str) -> str:
        return unmask(text, mapping)

    def put_back_optional(text: str | None) -> str | None:
        return None if text is None else unmask(text, mapping)

    def finding(f: FindingView) -> FindingView:
        proposal = f.proposal
        if proposal is not None:
            proposal = proposal.model_copy(
                update={
                    "proposed_text": put_back(proposal.proposed_text),
                    "rationale": put_back(proposal.rationale),
                }
            )
        return f.model_copy(
            update={
                "quote": put_back_optional(f.quote),
                "reasoning": put_back(f.reasoning),
                "escalation_reason": put_back_optional(f.escalation_reason),
                "proposal": proposal,
                "edited_text": put_back_optional(f.edited_text),
            }
        )

    return view.model_copy(
        update={
            "clauses": [c.model_copy(update={"text": put_back(c.text)}) for c in view.clauses],
            "injection_flags": [
                f.model_copy(update={"phrase": put_back(f.phrase)}) for f in view.injection_flags
            ],
            "findings": [finding(f) for f in view.findings],
        }
    )


def _unmask_lawyer_report(report: LawyerReport, mapping: Mapping[str, str]) -> LawyerReport:
    """Puts raw values back into a built lawyer report: quote, proposal text, the lawyer's edited
    text, requested-change text and flag phrase."""

    def put_back(text: str) -> str:
        return unmask(text, mapping)

    def put_back_optional(text: str | None) -> str | None:
        return None if text is None else unmask(text, mapping)

    return report.model_copy(
        update={
            "rule_results": [
                r.model_copy(update={"quote": put_back_optional(r.quote)})
                for r in report.rule_results
            ],
            "proposals": [
                p.model_copy(
                    update={
                        "proposed_text": put_back(p.proposed_text),
                        "edited_text": put_back_optional(p.edited_text),
                    }
                )
                for p in report.proposals
            ],
            "requested_changes": [
                c.model_copy(update={"text": put_back(c.text)}) for c in report.requested_changes
            ],
            "injection_flags": [
                f.model_copy(update={"phrase": put_back(f.phrase)}) for f in report.injection_flags
            ],
        }
    )


def review_body(store: Store, record: ReviewRecord, principal: Principal) -> dict[str, Any]:
    """The review response for the caller's role; `POST /reviews` and `GET /reviews/{id}` both
    produce their body here. The PII map is read only for a lawyer."""
    if principal.role == "lawyer":
        view: ReviewView = _unmask_lawyer_view(
            _lawyer_review(record), store.get_pii_map(record.review_id)
        )
    else:
        view = _viewer_review(record)
    return view.model_dump(mode="json")


def report_body(store: Store, record: ReviewRecord, principal: Principal) -> dict[str, Any]:
    """The report response for the caller's role. The PII map is read only for a lawyer."""
    if principal.role == "lawyer":
        return _unmask_lawyer_report(
            build_lawyer_report(record), store.get_pii_map(record.review_id)
        ).model_dump(mode="json")
    return build_report(record).model_dump(mode="json")


def _audit_entry(record: AuditRecord) -> AuditEntryView:
    return AuditEntryView(
        id=record.id,
        ts=record.ts,
        review_id=record.review_id,
        actor=record.actor,
        event=record.event,
        payload=record.payload,
    )


# --- Identity and role ----------------------------------------------------------------------------


def _settings_of(request: Request) -> Settings:
    settings = request.app.state.settings
    if not isinstance(settings, Settings):
        raise RuntimeError("Uygulama ayarları bulunamadı.")
    return settings


def _reject(request: Request, status: int, message: str, why: str) -> HTTPException:
    # Application log only: never the key value, never the body.
    logger.warning(
        "Reddedilen istek: %s %s -> %d (%s)", request.method, request.url.path, status, why
    )
    return HTTPException(status_code=status, detail=message)


def get_principal(request: Request, x_api_key: Annotated[str | None, Header()] = None) -> Principal:
    """The caller's tenant, role and name, resolved from the `X-API-Key` header only."""
    principal = _settings_of(request).api_keys.get(x_api_key) if x_api_key else None
    if principal is None:
        raise _reject(request, 401, _MSG_UNAUTHORIZED, "anahtar yok ya da tanınmıyor")
    return principal


def require_lawyer(
    request: Request, principal: Annotated[Principal, Depends(get_principal)]
) -> Principal:
    if principal.role != "lawyer":
        raise _reject(request, 403, _MSG_FORBIDDEN, "rol yetersiz")
    return principal


def load_review(
    review_id: str,
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
) -> ReviewRecord:
    """The review in the caller's own tenant; 404 when it is not there (another tenant's too).

    `review_id` is a plain `str`: a UUID type would answer 422 instead of 404.
    """
    record = Store(_settings_of(request).data_dir, principal.tenant_id).get_review(review_id)
    if record is None:
        raise _reject(request, 404, _MSG_NOT_FOUND, "inceleme bu tenant'ta yok")
    return record


def require_awaiting_lawyer(
    request: Request,
    principal: Annotated[Principal, Depends(require_lawyer)],
    record: Annotated[ReviewRecord, Depends(load_review)],
) -> ReviewRecord:
    """409 unless the review waits for the lawyer. The role is checked first (403 before 404).

    This keeps the check order; the real guard is the re-read under the decision lock.
    """
    if record.status != "awaiting_lawyer":
        raise _reject(request, 409, _MSG_NOT_AWAITING, "inceleme karar beklemiyor")
    return record


def require_decided(
    request: Request,
    record: Annotated[ReviewRecord, Depends(load_review)],
) -> ReviewRecord:
    """409 unless the lawyer's final decision is recorded. No role check: the report is a `GET`."""
    if record.status not in ("approved", "changes_requested"):
        raise _reject(request, 409, _MSG_NOT_DECIDED, "son karar henüz verilmedi")
    return record


# --- Review run -----------------------------------------------------------------------------------


def _cleanup_step(name: str, review_id: str, step: Callable[[], object]) -> None:
    """Run one cleanup step; its own failure is logged (type only) and never hides the original."""
    try:
        step()
    except Exception as exc:
        logger.error(
            "İnceleme temizliği başarısız: adım=%s, inceleme=%s, hata=%s",
            name,
            review_id,
            type(exc).__name__,
        )


def _internal_error(what: str, exc: BaseException) -> HTTPException:
    logger.error("%s: hata=%s", what, type(exc).__name__)
    return HTTPException(status_code=500, detail=_MSG_INTERNAL)


def _start_review(settings: Settings, store: Store, text: str) -> str:
    """Prepare the contract, run the graph to its interrupt and store the verified findings.

    Returns the review id once the review is `awaiting_lawyer`. On failure the review is `failed`
    with `review_failed` as its last audit row; the index is dropped either way.
    """
    tenant_id = store.tenant_id
    # Before preparation: a failure here leaves no review row behind.
    try:
        rules = store.load_rules()
        graph = get_review_graph(settings, tenant_id)
    except Exception as exc:
        raise _internal_error("İnceleme başlatılamadı", exc) from None
    try:
        prepared = prepare_contract(
            store, text, provider=settings.llm_provider, model=settings.llm_model
        )
    except Exception as exc:
        # prepare_contract has already marked the review failed and written review_failed.
        raise _internal_error("Sözleşme hazırlanamadı", exc) from None

    review_id = prepared.review_id
    try:
        result = graph.invoke(initial_state(prepared, rules), review_config(settings, review_id))
        if "__interrupt__" not in result or "verified" not in result:
            raise RuntimeError("Graph durma noktasına ulaşmadan döndü.")
        findings = [Finding.model_validate(f) for f in result["verified"]]
        if not store.save_findings(review_id, findings):
            raise RuntimeError("Bulgular kaydedilemedi: inceleme çalışır durumda değil.")
    except BaseException as exc:
        provider_config = classify_provider_error(exc) == "config"
        reason = "provider_config" if provider_config else "internal_error"
        logger.error(
            "İnceleme başarısız: inceleme=%s, sebep=%s, hata=%s",
            review_id,
            reason,
            type(exc).__name__,
        )
        _cleanup_step("mark_failed", review_id, lambda: store.mark_failed(review_id))
        _cleanup_step(
            "review_failed",
            review_id,
            lambda: store.append_audit(
                review_id, SYSTEM_ACTOR, "review_failed", {"reason": reason}
            ),
        )
        if not isinstance(exc, Exception):
            raise
        if provider_config:
            raise HTTPException(status_code=502, detail=_MSG_PROVIDER_CONFIG) from None
        raise HTTPException(status_code=500, detail=_MSG_INTERNAL) from None
    finally:
        _cleanup_step("drop_index", review_id, lambda: drop_index(tenant_id, review_id))
    return review_id


# --- Lawyer decision ------------------------------------------------------------------------------

# One lock per (resolved data_dir, tenant_id), the key of the shared store and graph caches: a
# global lock would make one tenant's decision wait for another's. In-process only (one worker).
_decision_locks: dict[tuple[Path, str], threading.Lock] = {}
_decision_locks_guard = threading.Lock()


def decision_lock(data_dir: Path, tenant_id: str) -> threading.Lock:
    key = (Path(data_dir).resolve(), tenant_id)
    with _decision_locks_guard:
        lock = _decision_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _decision_locks[key] = lock
        return lock


def _masked_decision(
    body: DecisionRequest, mapping: Mapping[str, str]
) -> tuple[LawyerDecision, dict[str, str]]:
    """The internal decision with every `edited_text` masked, and the mapping after masking.

    The same call chain as the clause text in `prepare_contract`; the mapping is threaded from
    one edited text to the next.
    """
    current = dict(mapping)
    entries: list[FindingDecision] = []
    for entry in body.findings:
        edited: MaskedText | None = None
        if entry.edited_text is not None:
            edited, current = mask(strip_invisible(entry.edited_text), current)
        entries.append(
            FindingDecision(
                rule_id=entry.rule_id,
                action=entry.action,
                status=entry.status,
                proposal_action=entry.proposal_action,
                edited_text=edited,
            )
        )
    return LawyerDecision(findings=entries, contract_decision=body.contract_decision), current


def _decide(
    settings: Settings,
    store: Store,
    request: Request,
    review_id: str,
    body: DecisionRequest,
    principal: Principal,
) -> ReviewRecord:
    """Check, mask and resume; called under the tenant's decision lock."""
    record = store.get_review(review_id)
    if record is None:
        raise _reject(request, 404, _MSG_NOT_FOUND, "inceleme bu tenant'ta yok")
    if record.status != "awaiting_lawyer":
        raise _reject(request, 409, _MSG_NOT_AWAITING, "inceleme karar beklemiyor")
    issues = check_decision(body.findings, record.findings)
    if issues:
        # The same handler and shape as a schema error: type, loc and a fixed message only.
        raise RequestValidationError(
            [{"type": i.type, "loc": ("body", *i.loc), "msg": i.msg} for i in issues]
        )
    try:
        mapping = store.get_pii_map(review_id)
        decision, masked_mapping = _masked_decision(body, mapping)
        new_entries = {k: v for k, v in masked_mapping.items() if k not in mapping}
        if new_entries:
            store.add_pii_entries(review_id, new_entries)
        graph = get_review_graph(settings, store.tenant_id)
        config = review_config(settings, review_id)
        if graph.get_state(config).next not in _RESUMABLE_NEXT:
            raise RuntimeError("İncelemenin graph durumu karar beklemiyor.")
        graph.invoke(resume_command(decision, principal.name), config)
        closed = store.get_review(review_id)
        if closed is None or closed.status not in ("approved", "changes_requested"):
            raise RuntimeError("Karar sonrası inceleme kapanmamış.")
    except ReviewNotAwaitingError:
        raise _reject(request, 409, _MSG_NOT_AWAITING, "inceleme karar beklemiyor") from None
    except Exception as exc:
        # The review stays awaiting_lawyer; nothing is marked failed, the lawyer may retry.
        logger.error("Karar kaydedilemedi: inceleme=%s, hata=%s", review_id, type(exc).__name__)
        raise HTTPException(status_code=500, detail=_MSG_DECISION_FAILED) from None
    return closed


# --- App ------------------------------------------------------------------------------------------


def _check_tenants(settings: Settings) -> None:
    """Every API key must map to a known tenant; otherwise the app does not start."""
    for tenant_id in sorted({p.tenant_id for p in settings.api_keys.values()}):
        try:
            Store(settings.data_dir, tenant_id)
        except UnknownTenantError:
            raise ConfigError(
                f"TENANT_API_KEYS bilinmeyen bir tenant'a işaret ediyor: '{tenant_id}' "
                f"(DATA_DIR/tenants/{tenant_id}/rules.yaml yok)."
            ) from None


def page_headers(page: bytes) -> dict[str, str]:
    """Response headers of `GET /`; the CSP allows only the SHA-256 of the page's one script."""
    text = page.decode("utf-8")
    blocks = _SCRIPT_BLOCK.findall(text)
    if len(blocks) != 1 or text.count("<script") != 1:
        raise ConfigError("Arayüz sayfasında tam bir <script> bloğu olmalı.")
    # The browser normalises CRLF / lone CR to LF before hashing the script (HTML input stream).
    script = blocks[0].replace("\r\n", "\n").replace("\r", "\n")
    digest = hashlib.sha256(script.encode("utf-8")).digest()
    script_hash = base64.b64encode(digest).decode("ascii")
    csp = (
        "default-src 'none'; connect-src 'self'; style-src 'unsafe-inline'; "
        f"script-src 'sha256-{script_hash}'; base-uri 'none'; form-action 'none'; "
        "frame-ancestors 'none'"
    )
    return {
        "Content-Security-Policy": csp,
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
        "Cache-Control": "no-store",
    }


def _load_page(path: Path) -> tuple[bytes, dict[str, str]]:
    try:
        page = path.read_bytes()
    except OSError:
        raise ConfigError(f"Arayüz sayfası okunamadı: {path}") from None
    return page, page_headers(page)


def create_app(settings: Settings) -> FastAPI:
    _check_tenants(settings)
    page, headers = _load_page(PAGE_PATH)
    app = FastAPI(
        title="Sözleşme inceleme",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.settings = settings

    @app.exception_handler(RequestValidationError)
    def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        # FastAPI's default 422 body copies the rejected input back; a blank or mistyped
        # contract of any size would be returned whole. Only type, location and message go out.
        detail = [
            {"type": error["type"], "loc": list(error["loc"]), "msg": error["msg"]}
            for error in exc.errors()
        ]
        return JSONResponse({"detail": detail}, status_code=422)

    @app.post("/reviews")
    def create_review(
        body: ReviewRequest,
        request: Request,
        principal: Annotated[Principal, Depends(require_lawyer)],
    ) -> JSONResponse:
        if len(body.text) > MAX_CONTRACT_CHARS:
            raise _reject(request, 413, _MSG_TOO_LARGE, "metin sınırı aşıldı")
        store = Store(settings.data_dir, principal.tenant_id)
        review_id = _start_review(settings, store, body.text)
        record = store.get_review(review_id)
        if record is None:
            raise RuntimeError("Kaydedilen inceleme okunamadı.")
        return JSONResponse(review_body(store, record, principal))

    @app.post("/reviews/{review_id}/decision")
    def decide(
        body: DecisionRequest,
        request: Request,
        principal: Annotated[Principal, Depends(require_lawyer)],
        record: Annotated[ReviewRecord, Depends(require_awaiting_lawyer)],
    ) -> JSONResponse:
        store = Store(settings.data_dir, principal.tenant_id)
        with decision_lock(settings.data_dir, principal.tenant_id):
            closed = _decide(settings, store, request, record.review_id, body, principal)
        try:
            response = review_body(store, closed, principal)
        except Exception as exc:
            # The decision is recorded; a retry answers 409 and GET shows the closed review.
            logger.error(
                "Karar yanıtı kurulamadı: inceleme=%s, hata=%s",
                record.review_id,
                type(exc).__name__,
            )
            raise HTTPException(status_code=500, detail=_MSG_DECISION_FAILED) from None
        return JSONResponse(response)

    @app.get("/reviews/{review_id}")
    def get_review(
        principal: Annotated[Principal, Depends(get_principal)],
        record: Annotated[ReviewRecord, Depends(load_review)],
    ) -> JSONResponse:
        store = Store(settings.data_dir, principal.tenant_id)
        return JSONResponse(review_body(store, record, principal))

    @app.get("/reviews/{review_id}/report")
    def get_report(
        principal: Annotated[Principal, Depends(get_principal)],
        record: Annotated[ReviewRecord, Depends(require_decided)],
    ) -> JSONResponse:
        store = Store(settings.data_dir, principal.tenant_id)
        return JSONResponse(report_body(store, record, principal))

    @app.get("/reviews/{review_id}/audit")
    def get_audit(
        principal: Annotated[Principal, Depends(get_principal)],
        record: Annotated[ReviewRecord, Depends(load_review)],
    ) -> JSONResponse:
        store = Store(settings.data_dir, principal.tenant_id)
        entries = [
            _audit_entry(r).model_dump(mode="json") for r in store.list_audit(record.review_id)
        ]
        return JSONResponse(entries)

    @app.get("/")
    def index() -> Response:
        # No key, no data: the same bytes for every caller; the page fetches the review itself.
        return Response(content=page, media_type="text/html; charset=utf-8", headers=headers)

    return app


def main() -> None:
    settings = load_settings_or_exit()
    try:
        app = create_app(settings)
    except ConfigError as exc:
        print(f"Yapılandırma hatası: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
    uvicorn.run(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
