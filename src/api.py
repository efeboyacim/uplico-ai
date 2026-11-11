"""HTTP API: start a review, read it and its audit log.

Design (`docs/architecture.md` §3, `docs/agent_design.md` §6 and §7):

- Tenant, role and name come only from the `X-API-Key` header (`get_principal`). Every endpoint
  other than `GET` carries `Depends(require_lawyer)`. Nothing in the request names a tenant or role.
- A review is looked up only in the caller's own tenant store; anything else is 404.
- Responses are built from fixed field lists (the `*View` models below), never by dumping a record.
  A lawyer gets the viewer fields plus explicitly added ones, unmasked in one function.
- No exception text reaches a response or the log (it may carry contract text): the response has
  a fixed Turkish message and the log the exception class name.
- Synchronous endpoints only (`SqliteSaver` has no async support).
"""

import logging
import sys
from collections.abc import Callable, Mapping, Sequence
from typing import Annotated, Any

import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.config import ConfigError, Principal, Settings, load_settings_or_exit
from src.graph import get_review_graph, initial_state, review_config
from src.llm import classify_provider_error
from src.models import (
    AuditRecord,
    Clause,
    CodeCheck,
    Finding,
    FindingStatus,
    InjectionFlag,
    InjectionKind,
    InterruptReason,
    NoFindingReason,
    Proposal,
    ReviewReason,
    ReviewRecord,
    ReviewStatus,
)
from src.prepare import prepare_contract
from src.prepare.index import drop_index
from src.prepare.pii import unmask
from src.store import Store, UnknownTenantError

logger = logging.getLogger(__name__)

SYSTEM_ACTOR = "system"

_MSG_UNAUTHORIZED = "API anahtarı eksik ya da tanınmıyor."
_MSG_FORBIDDEN = "Bu işlem için lawyer rolü gerekir."
_MSG_NOT_FOUND = "İnceleme bulunamadı."
_MSG_PROVIDER_CONFIG = "Model sağlayıcısının kimlik ya da yapılandırma ayarı hatalı."
_MSG_INTERNAL = "İnceleme tamamlanamadı: beklenmeyen bir hata oluştu."


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


class ReviewView(_View):
    review_id: str
    status: ReviewStatus
    created_at: str
    # Sequence (covariant): the lawyer view narrows the element types.
    clauses: Sequence[ClauseView]
    injection_flags: Sequence[InjectionFlagView]
    findings: list[FindingView]


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


def _finding_view(finding: Finding) -> FindingView:
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
    )


def _viewer_review(record: ReviewRecord) -> ReviewView:
    return ReviewView(
        review_id=record.review_id,
        status=record.status,
        created_at=record.created_at,
        clauses=[_clause_view(c) for c in record.clauses],
        injection_flags=[_flag_view(f) for f in record.injection_flags],
        findings=[_finding_view(f) for f in record.findings],
    )


def _lawyer_review(record: ReviewRecord) -> LawyerReviewView:
    """The lawyer view, still masked: the viewer fields plus the explicitly added ones."""
    return LawyerReviewView(
        review_id=record.review_id,
        status=record.status,
        created_at=record.created_at,
        original_text=record.original_text,
        clauses=[_lawyer_clause_view(c) for c in record.clauses],
        injection_flags=[_lawyer_flag_view(f) for f in record.injection_flags],
        findings=[_finding_view(f) for f in record.findings],
    )


def _unmask_lawyer_view(view: LawyerReviewView, mapping: Mapping[str, str]) -> LawyerReviewView:
    """The only place where placeholders are replaced by raw values: every text field of a built
    lawyer view (clause text, flag phrase, quote, reasoning, escalation reason, proposal)."""

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


def create_app(settings: Settings) -> FastAPI:
    _check_tenants(settings)
    app = FastAPI(
        title="Sözleşme inceleme",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.settings = settings

    @app.post("/reviews")
    def create_review(
        body: ReviewRequest,
        principal: Annotated[Principal, Depends(require_lawyer)],
    ) -> JSONResponse:
        store = Store(settings.data_dir, principal.tenant_id)
        review_id = _start_review(settings, store, body.text)
        record = store.get_review(review_id)
        if record is None:
            raise RuntimeError("Kaydedilen inceleme okunamadı.")
        return JSONResponse(review_body(store, record, principal))

    @app.get("/reviews/{review_id}")
    def get_review(
        review_id: str,
        request: Request,
        principal: Annotated[Principal, Depends(get_principal)],
    ) -> JSONResponse:
        store = Store(settings.data_dir, principal.tenant_id)
        record = store.get_review(review_id)
        if record is None:
            raise _reject(request, 404, _MSG_NOT_FOUND, "inceleme bu tenant'ta yok")
        return JSONResponse(review_body(store, record, principal))

    @app.get("/reviews/{review_id}/audit")
    def get_audit(
        review_id: str,
        request: Request,
        principal: Annotated[Principal, Depends(get_principal)],
    ) -> JSONResponse:
        store = Store(settings.data_dir, principal.tenant_id)
        if store.get_review(review_id) is None:
            raise _reject(request, 404, _MSG_NOT_FOUND, "inceleme bu tenant'ta yok")
        entries = [_audit_entry(r).model_dump(mode="json") for r in store.list_audit(review_id)]
        return JSONResponse(entries)

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
