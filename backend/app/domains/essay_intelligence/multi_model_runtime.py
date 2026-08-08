from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import re

from app.core.config import Settings
from app.domains.ai.model_cost import ModelCostConfigurationError, ModelPriceCard
from app.domains.essay_intelligence.blind_adjudicator import (
    BlindAdjudicatorSettings,
    OpenAICompatibleBlindAdjudicatorProvider,
)
from app.domains.essay_intelligence.grounded_provider import (
    GroundedEssayReviewProvider,
    GroundedReviewProviderConfigurationError,
    validate_grounded_review_provider_security,
)
from app.domains.essay_intelligence.hunyuan_cloud_function import (
    HunyuanCloudFunctionSettings,
)
from app.domains.essay_intelligence.hunyuan_grounded_provider import (
    HunyuanGroundedEssayReviewProvider,
)
from app.domains.essay_intelligence.multi_model_candidate_runner import (
    EssayCandidateSlot,
)
from app.domains.essay_intelligence.review_contracts import (
    GROUNDED_REVIEW_PROMPT_VERSION,
    GROUNDED_REVIEW_PROVIDER_SCHEMA_VERSION,
)


_RELEASE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_PRICE_KEYS = frozenset({"tokenrhythm", "hunyuan", "adjudicator"})


class EssayMultiModelRuntimeConfigurationError(RuntimeError):
    pass


@dataclass(frozen=True)
class EssayReviewExecutionIdentity:
    provider: str
    model: str


@dataclass(frozen=True)
class EssayMultiModelRuntimePlan:
    release_id: str
    candidate_slots: tuple[EssayCandidateSlot, ...]
    adjudicator_provider: OpenAICompatibleBlindAdjudicatorProvider
    adjudicator_price_card: ModelPriceCard
    execution_identity: EssayReviewExecutionIdentity

    @property
    def slot_ids(self) -> tuple[str, ...]:
        return tuple(slot.slot_id for slot in self.candidate_slots)

    @classmethod
    def execution_identity_from_settings(
        cls,
        settings: Settings,
    ) -> EssayReviewExecutionIdentity:
        """Build the queue identity from non-secret release metadata only.

        The API queues work but does not call the grading providers, so
        it must not need worker-only credentials merely to fingerprint a job.
        The worker still constructs the full runtime and validates every key.
        """
        release_id = settings.ai_essay_grading_plan_release_id.strip()
        if _RELEASE_PATTERN.fullmatch(release_id) is None:
            raise EssayMultiModelRuntimeConfigurationError(
                "AI_ESSAY_GRADING_PLAN_RELEASE_ID is required and must be versioned"
            )
        prices = _price_cards(settings.ai_essay_grading_price_cards_json)
        secondary = _tokenrhythm_settings(settings)
        hunyuan = HunyuanCloudFunctionSettings.from_environment()
        adjudicator = BlindAdjudicatorSettings.from_environment()
        candidates = (
            (
                "tokenrhythm",
                secondary.ai_essay_review_provider_release_id,
                secondary.ai_essay_review_model,
            ),
            ("hunyuan", hunyuan.release_id, hunyuan.model),
        )
        for slot_id, provider_release, model in candidates:
            if _RELEASE_PATTERN.fullmatch(provider_release.strip()) is None or not model.strip():
                raise EssayMultiModelRuntimeConfigurationError(
                    f"{slot_id} essay candidate identity is incomplete"
                )
        adjudicator_release = adjudicator.provider_release_id.strip()
        if _RELEASE_PATTERN.fullmatch(adjudicator_release) is None or not adjudicator.model.strip():
            raise EssayMultiModelRuntimeConfigurationError(
                "essay adjudicator identity is incomplete"
            )
        identities = {
            (provider_release.casefold(), model.casefold())
            for _, provider_release, model in candidates
        }
        if len(identities) != len(candidates):
            raise EssayMultiModelRuntimeConfigurationError(
                "Formal essay candidate releases must be independent"
            )
        return _execution_identity(
            release_id=release_id,
            candidates=candidates,
            price_versions={key: value.version for key, value in prices.items()},
            adjudicator_release=adjudicator_release,
            adjudicator_model=adjudicator.model,
        )

    @classmethod
    def from_settings(cls, settings: Settings) -> "EssayMultiModelRuntimePlan":
        release_id = settings.ai_essay_grading_plan_release_id.strip()
        if _RELEASE_PATTERN.fullmatch(release_id) is None:
            raise EssayMultiModelRuntimeConfigurationError(
                "AI_ESSAY_GRADING_PLAN_RELEASE_ID is required and must be versioned"
            )
        prices = _price_cards(settings.ai_essay_grading_price_cards_json)
        try:
            secondary_settings = _tokenrhythm_settings(settings)
            validate_grounded_review_provider_security(secondary_settings)
            secondary = GroundedEssayReviewProvider(secondary_settings)
            secondary.validate_configuration()
            hunyuan = HunyuanGroundedEssayReviewProvider(
                HunyuanCloudFunctionSettings.from_environment()
            )
            adjudicator = OpenAICompatibleBlindAdjudicatorProvider(
                BlindAdjudicatorSettings.from_environment()
            )
        except (GroundedReviewProviderConfigurationError, ValueError) as exc:
            raise EssayMultiModelRuntimeConfigurationError(str(exc)) from exc
        except RuntimeError as exc:
            raise EssayMultiModelRuntimeConfigurationError(str(exc)) from exc

        slots = (
            EssayCandidateSlot(
                slot_id="tokenrhythm",
                provider=secondary,
                price_card=prices["tokenrhythm"],
                prompt_version=GROUNDED_REVIEW_PROMPT_VERSION,
                schema_version=GROUNDED_REVIEW_PROVIDER_SCHEMA_VERSION,
            ),
            EssayCandidateSlot(
                slot_id="hunyuan",
                provider=hunyuan,
                price_card=prices["hunyuan"],
                prompt_version=GROUNDED_REVIEW_PROMPT_VERSION,
                schema_version=GROUNDED_REVIEW_PROVIDER_SCHEMA_VERSION,
            ),
        )
        identities = {
            (slot.provider.provider_release_id.casefold(), slot.provider.model.casefold())
            for slot in slots
        }
        if len(identities) != len(slots):
            raise EssayMultiModelRuntimeConfigurationError(
                "Formal essay candidate releases must be independent"
            )
        return cls(
            release_id=release_id,
            candidate_slots=slots,
            adjudicator_provider=adjudicator,
            adjudicator_price_card=prices["adjudicator"],
            execution_identity=_execution_identity(
                release_id=release_id,
                candidates=tuple(
                    (
                        slot.slot_id,
                        slot.provider.provider_release_id,
                        slot.provider.model,
                    )
                    for slot in slots
                ),
                price_versions={key: value.version for key, value in prices.items()},
                adjudicator_release=adjudicator.provider_release_id,
                adjudicator_model=adjudicator.model,
            ),
        )


def _primary_settings(settings: Settings) -> Settings:
    return replace(
        settings,
        ai_provider_base_url=(settings.ai_essay_primary_base_url or settings.ai_provider_base_url),
        ai_api_key=settings.ai_essay_primary_api_key or settings.ai_api_key,
    )


def _execution_identity(
    *,
    release_id: str,
    candidates: tuple[tuple[str, str, str], ...],
    price_versions: dict[str, str],
    adjudicator_release: str,
    adjudicator_model: str,
) -> EssayReviewExecutionIdentity:
    plan_document = {
        "releaseId": release_id,
        "candidates": [
            {
                "slotId": slot_id,
                "providerReleaseId": provider_release,
                "model": model,
                "promptVersion": GROUNDED_REVIEW_PROMPT_VERSION,
                "schemaVersion": GROUNDED_REVIEW_PROVIDER_SCHEMA_VERSION,
                "priceVersion": price_versions[slot_id],
            }
            for slot_id, provider_release, model in candidates
        ],
        "adjudicator": {
            "providerReleaseId": adjudicator_release,
            "model": adjudicator_model,
            "priceVersion": price_versions["adjudicator"],
        },
    }
    fingerprint = sha256(
        json.dumps(
            plan_document,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return EssayReviewExecutionIdentity(
        provider=f"essay-multi-model@{release_id}",
        model=f"plan-sha256:{fingerprint}",
    )


def _tokenrhythm_settings(settings: Settings) -> Settings:
    return replace(
        settings,
        ai_provider_base_url=settings.ai_essay_tokenrhythm_base_url,
        ai_api_key=settings.ai_essay_tokenrhythm_api_key,
        ai_essay_review_model=settings.ai_essay_tokenrhythm_model,
        ai_essay_review_provider_release_id=(settings.ai_essay_tokenrhythm_provider_release_id),
        ai_essay_review_provider_https_host_allowlist=(
            settings.ai_essay_tokenrhythm_provider_https_host_allowlist
        ),
        ai_essay_review_provider_dpa_confirmed=(
            settings.ai_essay_tokenrhythm_provider_dpa_confirmed
        ),
        ai_essay_review_provider_idempotency_capability=(
            settings.ai_essay_tokenrhythm_provider_idempotency_capability
        ),
        ai_essay_review_provider_idempotency_key_header=(
            settings.ai_essay_tokenrhythm_provider_idempotency_key_header
        ),
        ai_essay_review_provider_request_id_header=(
            settings.ai_essay_tokenrhythm_provider_request_id_header
        ),
        ai_essay_review_provider_replay_retention_seconds=(
            settings.ai_essay_tokenrhythm_provider_replay_retention_seconds
        ),
        ai_essay_review_max_input_bytes=settings.ai_essay_tokenrhythm_max_input_bytes,
        ai_essay_review_max_output_bytes=settings.ai_essay_tokenrhythm_max_output_bytes,
    )


def _price_cards(raw: str) -> dict[str, ModelPriceCard]:
    try:
        document = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise EssayMultiModelRuntimeConfigurationError(
            "AI_ESSAY_GRADING_PRICE_CARDS_JSON must be a JSON object"
        ) from exc
    if not isinstance(document, dict) or set(document) != _PRICE_KEYS:
        raise EssayMultiModelRuntimeConfigurationError(
            "Essay grading price cards must define tokenrhythm, hunyuan, and adjudicator"
        )
    result: dict[str, ModelPriceCard] = {}
    for key in sorted(_PRICE_KEYS):
        item = document[key]
        if not isinstance(item, dict) or set(item) != {
            "version",
            "inputCnyPerMillion",
            "cachedInputCnyPerMillion",
            "outputCnyPerMillion",
        }:
            raise EssayMultiModelRuntimeConfigurationError(
                f"Essay grading price card {key} has an invalid contract"
            )
        try:
            result[key] = ModelPriceCard(
                version=str(item["version"]),
                input_cny_per_million=Decimal(str(item["inputCnyPerMillion"])),
                cached_input_cny_per_million=Decimal(str(item["cachedInputCnyPerMillion"])),
                output_cny_per_million=Decimal(str(item["outputCnyPerMillion"])),
            )
        except (InvalidOperation, ModelCostConfigurationError, ValueError) as exc:
            raise EssayMultiModelRuntimeConfigurationError(
                f"Essay grading price card {key} is invalid"
            ) from exc
    return result
