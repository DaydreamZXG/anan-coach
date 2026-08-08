from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.domains.ai.governance.contracts import IntentDecision
from app.domains.ai.governance.retrieval import RetrievalBundle
from app.domains.ai.provider import AIProvider
from app.domains.companion.short_term import (
    ShortTermConversationMessage,
    short_term_history_payload,
)


@dataclass(frozen=True, slots=True)
class CompanionProviderContextAssembler:
    """Build the provider payload from authorized context and immutable history."""

    provider: AIProvider

    def prepare(
        self,
        *,
        decision: IntentDecision,
        question_text: str,
        context_payload: dict[str, object],
        retrieval: RetrievalBundle,
        persona: str,
        short_term_history: tuple[ShortTermConversationMessage, ...],
    ) -> dict[str, Any]:
        provider_kwargs: dict[str, Any] = {
            "route_decision": decision.model_dump(by_alias=True, mode="json"),
            "question_text": question_text,
            "context": context_payload,
            "sources": provider_sources(retrieval),
            "persona": persona,
        }
        conversation_history = short_term_history_payload(short_term_history)
        if conversation_history:
            provider_kwargs["conversation_history"] = conversation_history
        return self.provider.prepare_companion_question(**provider_kwargs)


def provider_sources(bundle: RetrievalBundle) -> list[dict[str, Any]]:
    return [
        {
            "sourceRef": item.reference.source_id,
            "kind": item.reference.kind,
            "title": item.reference.title,
            "version": item.reference.version,
            "anchor": item.reference.anchor,
            "trustScope": item.trust_scope,
            "untrustedInput": item.untrusted_input,
            "content": item.excerpt,
        }
        for item in bundle.sources
    ]
