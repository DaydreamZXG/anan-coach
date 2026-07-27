from __future__ import annotations

import base64
from typing import cast
from uuid import UUID

from app.contracts.external_learning.models import LearningTrack
from app.core.config import Settings
from app.domains.external_ingestion.matching.models import MatchQuery
from app.domains.ilink_channel.resolver import (
    PhotoQuestionResolver,
    QueryRecognitionProcessor,
    TosTransientImageStore,
)
from app.domains.ilink_channel.settings import ILinkSettings


def _channel_settings() -> ILinkSettings:
    return ILinkSettings(
        enabled=True,
        bot_account_id="anan-bot-1",
        bot_token="official-token-secret",
        base_url="https://ilinkai.weixin.qq.com",
        cdn_base_url="https://novac2c.cdn.weixin.qq.com/c2c",
        app_id="bot",
        app_client_version=132102,
        bot_agent="Anan/1.0",
        identity_key_id="ilink-key-v1",
        identity_encryption_key=base64.b64encode(b"e" * 32).decode(),
        identity_hmac_key="h" * 40,
        binding_ttl_seconds=600,
        long_poll_timeout_seconds=35,
        request_timeout_seconds=15,
        maximum_image_bytes=10 * 1024 * 1024,
        transient_retention_seconds=900,
        query_retention_days=30,
        recognition_route="photo-query-approved-v1",
    )


def test_answer_models_are_lazy_and_unapproved_models_abstain() -> None:
    resolver = PhotoQuestionResolver(
        core_settings=Settings(),
        channel_settings=_channel_settings(),
        storage=cast(TosTransientImageStore, object()),
        recognizer=cast(QueryRecognitionProcessor, object()),
    )

    result = resolver._dual_ai(
        query=MatchQuery(
            track=LearningTrack.XINGCE,
            promptText="根据材料，正确选项是哪一个？",
            optionTexts=("A. 甲", "B. 乙"),
        ),
        recognized={"segmentRefs": ["segment-1"]},
        query_id=UUID("00000000-0000-0000-0000-000000009301"),
    )

    assert result.answer_kind == "ambiguous"
    assert result.answer_text == "这道题暂时没有可核验的答案"
    assert result.model_provenance is None
