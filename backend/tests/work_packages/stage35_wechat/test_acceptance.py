from __future__ import annotations

from pathlib import Path
import re
import subprocess


ROOT = Path(__file__).resolve().parents[4]
WECHAT = ROOT / "apps" / "wechat"


def test_stage35_wechat_c21_c24_states_and_boundaries() -> None:
    page_names = (
        "community-draft",
        "community-structure",
        "community-trial",
        "community-contributions",
    )
    for page_name in page_names:
        page = WECHAT / "miniprogram" / "pages" / page_name
        for suffix in ("ts", "json", "wxml", "wxss"):
            assert (page / f"index.{suffix}").is_file(), f"missing {page_name}.{suffix}"
        markup = (page / "index.wxml").read_text(encoding="utf-8")
        assert "pageState" in markup
        assert "state-view" in markup

    contracts = (
        WECHAT
        / "miniprogram"
        / "features"
        / "community-method-cards"
        / "contracts.ts"
    ).read_text(encoding="utf-8")
    for state in (
        "default",
        "loading",
        "empty",
        "partial",
        "error",
        "expired",
        "conflict",
        "recovery",
        "processing",
    ):
        assert f'"{state}"' in contracts
    for source in ("text", "voice_transcript", "handwriting_extraction", "reflection"):
        assert f'"{source}"' in contracts

    api = (WECHAT / "miniprogram" / "api" / "community-method-cards.ts").read_text(
        encoding="utf-8"
    )
    for operation in (
        "createDraft",
        "generateCandidate",
        "confirmCandidate",
        "submitForReview",
        "optInToTrial",
        "completeTrial",
        "recordDelayedVerification",
        "recordFeedback",
        "report",
        "getContributions",
        "getRewardLedger",
    ):
        assert operation in api
    assert "methodSteps" in api
    assert "exclusions" in api
    assert "pitfalls" in api
    assert "evidenceCount" in api
    assert "riskFlags" in api
    assert 'communityMethodCardsContractVersion = "stage35-v2"' in api
    for field in (
        "activePolicyVersions",
        "awardIds",
        "entryIds",
        "aiQuotaBalance",
        "membershipSecondsBalance",
        "entitlementVersion",
    ):
        assert field in api
    trial_command = re.search(
        r"export interface CommunityTrialCompleteCommand \{(?P<body>.*?)\n\}",
        api,
        re.DOTALL,
    )
    assert trial_command is not None
    assert set(re.findall(r"^\s+(\w+):", trial_command.group("body"), re.MULTILINE)) == {
        "exposureId",
        "questionAttemptId",
        "questionVersionId",
    }
    delayed_command = re.search(
        r"export interface CommunityDelayedVerificationCommand \{(?P<body>.*?)\n\}",
        api,
        re.DOTALL,
    )
    assert delayed_command is not None
    assert set(re.findall(r"^\s+(\w+):", delayed_command.group("body"), re.MULTILINE)) == {
        "trialId",
        "questionAttemptId",
        "questionVersionId",
    }

    trial_markup = (
        WECHAT / "miniprogram" / "pages" / "community-trial" / "index.wxml"
    ).read_text(encoding="utf-8")
    for field in ("methodSteps", "exclusions", "pitfalls", "evidenceLabel", "riskLabel"):
        assert field in trial_markup
    assert "commentText" not in trial_markup
    assert "rawVote" not in trial_markup
    assert "voteCount" not in trial_markup
    trial_page = (
        WECHAT / "miniprogram" / "pages" / "community-trial" / "index.ts"
    ).read_text(encoding="utf-8")
    for client_fact in (
        "correctAfter",
        "durationAfterMs",
        "deviceClusterId",
        "currentDeviceId",
    ):
        assert client_fact not in trial_page

    contributions_markup = (
        WECHAT
        / "miniprogram"
        / "pages"
        / "community-contributions"
        / "index.wxml"
    ).read_text(encoding="utf-8")
    assert "当前无已发布政策" in contributions_markup
    assert "aiQuotaBalance" in contributions_markup
    assert "membershipBalanceLabel" in contributions_markup
    assert "entitlementVersion" in contributions_markup
    assert "awardIds" in contributions_markup
    assert "entryIds" in contributions_markup

    owned_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in [
            WECHAT / "miniprogram" / "api" / "community-method-cards.ts",
            *(
                WECHAT / "miniprogram" / "features" / "community-method-cards"
            ).rglob("*.*"),
            *(
                path
                for page_name in page_names
                for path in (WECHAT / "miniprogram" / "pages" / page_name).rglob("*.*")
            ),
        ]
        if path.is_file()
    )
    assert "鹿鸣" not in owned_text
    assert "岸岸" in owned_text

    completed = subprocess.run(
        [
            "npm",
            "run",
            "test",
            "--",
            "tests/community-method-cards.test.ts",
        ],
        cwd=WECHAT,
        check=False,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert completed.returncode == 0, completed.stdout + "\n" + completed.stderr
