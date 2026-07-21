from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import stat

import httpx
import pytest

from app.domains.ilink_channel.operator_login import (
    ILinkOperatorCredential,
    ILinkOperatorLoginClient,
    ILinkOperatorLoginError,
    write_credential_file,
)
from app.domains.ilink_channel.upstream import (
    ILINK_APP_CLIENT_VERSION,
    ILINK_APP_ID,
    ILINK_BASE_URL,
    ILINK_BOT_TYPE,
    ILinkLoginStatus,
    UPSTREAM_NPM_INTEGRITY,
    UPSTREAM_PACKAGE,
    UPSTREAM_TARBALL_SHA256,
    UPSTREAM_VERSION,
    build_client_version,
    redirect_host_to_base_url,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
UPSTREAM_LOCK = PROJECT_ROOT / "ops/upstreams/tencent-openclaw-weixin-2.4.6.json"


class RecordedTransport:
    def __init__(self, responses: list[dict[str, object]]) -> None:
        self._responses: Iterator[dict[str, object]] = iter(responses)
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(200, json=next(self._responses), request=request)

    def factory(self, **kwargs: object) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handler), **kwargs)


def test_runtime_constants_equal_machine_readable_upstream_lock() -> None:
    lock = json.loads(UPSTREAM_LOCK.read_text(encoding="utf-8"))
    assert lock["package"] == UPSTREAM_PACKAGE
    assert lock["version"] == UPSTREAM_VERSION
    assert lock["npmIntegrity"] == UPSTREAM_NPM_INTEGRITY
    assert lock["tarballSha256"] == UPSTREAM_TARBALL_SHA256
    assert lock["protocol"] == {
        "baseUrl": ILINK_BASE_URL,
        "botType": ILINK_BOT_TYPE,
        "appId": ILINK_APP_ID,
        "appClientVersion": ILINK_APP_CLIENT_VERSION,
        "qrCreateEndpoint": "ilink/bot/get_bot_qrcode",
        "qrStatusEndpoint": "ilink/bot/get_qrcode_status",
        "statuses": [status.value for status in ILinkLoginStatus],
    }
    assert build_client_version(UPSTREAM_VERSION) == ILINK_APP_CLIENT_VERSION


def test_qr_provisioning_uses_exact_official_requests_and_handles_verification_redirect() -> None:
    transport = RecordedTransport(
        [
            {
                "qrcode": "qr-one-secret",
                "qrcode_img_content": "https://weixin.qq.com/x/qr-browser-secret",
            },
            {"status": "need_verifycode"},
            {"status": "scaned"},
            {
                "status": "scaned_but_redirect",
                "redirect_host": "ilinkai-sh.weixin.qq.com",
            },
            {
                "status": "confirmed",
                "bot_token": "bot-token-secret",
                "ilink_bot_id": "anan-official-bot",
                "ilink_user_id": "operator-user-secret",
                "baseurl": "https://ilinkai-sh.weixin.qq.com",
            },
        ]
    )
    opened: list[str] = []
    credential = ILinkOperatorLoginClient(
        client_factory=transport.factory,
        sleep=lambda _seconds: None,
        monotonic=lambda: 0,
    ).provision(
        open_browser=lambda url: opened.append(url) is None,
        request_verify_code=lambda: "123456",
    )

    assert credential.account_id == "anan-official-bot"
    assert credential.bot_token == "bot-token-secret"
    assert credential.base_url == "https://ilinkai-sh.weixin.qq.com"
    assert opened == ["https://weixin.qq.com/x/qr-browser-secret"]
    create = transport.requests[0]
    assert create.method == "POST"
    assert create.url == httpx.URL(
        "https://ilinkai.weixin.qq.com/ilink/bot/get_bot_qrcode?bot_type=3"
    )
    assert json.loads(create.content) == {"local_token_list": []}
    assert create.headers["iLink-App-Id"] == "bot"
    assert create.headers["iLink-App-ClientVersion"] == "132102"
    assert transport.requests[2].url.params["verify_code"] == "123456"
    assert "verify_code" not in transport.requests[3].url.params
    assert transport.requests[4].url.host == "ilinkai-sh.weixin.qq.com"


def test_redirects_are_tencent_only_and_errors_never_contain_tokens() -> None:
    assert redirect_host_to_base_url("ilinkai-sh.weixin.qq.com") == (
        "https://ilinkai-sh.weixin.qq.com"
    )
    for host in (
        "attacker.example",
        "ilinkai.weixin.qq.com.attacker.example",
        "ilinkai.weixin.qq.com:443",
        "https://ilinkai.weixin.qq.com",
    ):
        with pytest.raises(ValueError, match="unapproved_tencent_ilink_redirect"):
            redirect_host_to_base_url(host)

    transport = RecordedTransport(
        [
            {
                "qrcode": "qr-token-secret",
                "qrcode_img_content": "https://weixin.qq.com/x/qr-token-secret",
            },
            {"status": "scaned_but_redirect", "redirect_host": "attacker.example"},
        ]
    )
    with pytest.raises(ILinkOperatorLoginError) as error:
        ILinkOperatorLoginClient(
            client_factory=transport.factory,
            sleep=lambda _seconds: None,
            monotonic=lambda: 0,
        ).provision(open_browser=lambda _url: True)
    assert error.value.code == "redirect_host_unapproved"
    assert "secret" not in str(error.value)


def test_qr_refresh_wait_blocked_and_already_bound_states_are_explicit() -> None:
    transport = RecordedTransport(
        [
            {
                "qrcode": "qr-one",
                "qrcode_img_content": "https://weixin.qq.com/x/one",
            },
            {"status": "wait"},
            {"status": "expired"},
            {
                "qrcode": "qr-two",
                "qrcode_img_content": "https://weixin.qq.com/x/two",
            },
            {"status": "verify_code_blocked"},
            {
                "qrcode": "qr-three",
                "qrcode_img_content": "https://weixin.qq.com/x/three",
            },
            {
                "status": "confirmed",
                "bot_token": "final-token",
                "ilink_bot_id": "final-bot",
            },
        ]
    )
    opened: list[str] = []
    credential = ILinkOperatorLoginClient(
        client_factory=transport.factory,
        sleep=lambda _seconds: None,
        monotonic=lambda: 0,
    ).provision(open_browser=lambda url: opened.append(url) is None)
    assert credential.account_id == "final-bot"
    assert opened == [
        "https://weixin.qq.com/x/one",
        "https://weixin.qq.com/x/two",
        "https://weixin.qq.com/x/three",
    ]

    already_bound = RecordedTransport(
        [
            {
                "qrcode": "qr-bound",
                "qrcode_img_content": "https://weixin.qq.com/x/bound",
            },
            {"status": "binded_redirect"},
        ]
    )
    with pytest.raises(ILinkOperatorLoginError, match="bot_already_bound"):
        ILinkOperatorLoginClient(
            client_factory=already_bound.factory,
            sleep=lambda _seconds: None,
            monotonic=lambda: 0,
        ).provision(open_browser=lambda _url: True)


def test_credentials_are_complete_private_atomic_and_never_overwritten(
    tmp_path: Path,
) -> None:
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    other_repository = tmp_path / "different-repository"
    other_repository.mkdir(mode=0o700)
    destination = private / "ilink.json"
    credential = ILinkOperatorCredential(
        schema_version=1,
        account_id="anan-official-bot",
        bot_token="bot-token-secret",
        base_url=ILINK_BASE_URL,
        user_id="operator-user-secret",
        upstream_package=UPSTREAM_PACKAGE,
        upstream_version=UPSTREAM_VERSION,
        issued_at="2026-07-21T07:00:00+00:00",
    )
    output = write_credential_file(
        credential,
        destination,
        repository_root=other_repository,
    )
    assert output == destination
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600
    assert json.loads(destination.read_text(encoding="utf-8"))["bot_token"] == (
        "bot-token-secret"
    )
    assert not list(private.glob(".ilink-credential-*.tmp"))

    original = destination.read_bytes()
    with pytest.raises(ILinkOperatorLoginError, match="credential_output_exists"):
        write_credential_file(credential, destination, repository_root=None)
    assert destination.read_bytes() == original


def test_concurrent_credential_writers_create_exactly_one_complete_file(
    tmp_path: Path,
) -> None:
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    destination = private / "ilink.json"
    credentials = [
        ILinkOperatorCredential(
            schema_version=1,
            account_id=f"bot-{index}",
            bot_token=f"secret-{index}",
            base_url=ILINK_BASE_URL,
            user_id=None,
            upstream_package=UPSTREAM_PACKAGE,
            upstream_version=UPSTREAM_VERSION,
            issued_at="2026-07-21T07:00:00+00:00",
        )
        for index in range(2)
    ]

    def write(index: int) -> str:
        try:
            write_credential_file(credentials[index], destination, repository_root=None)
        except ILinkOperatorLoginError as exc:
            return exc.code
        return "created"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(write, range(2)))
    assert sorted(outcomes) == ["created", "credential_output_exists"]
    payload = json.loads(destination.read_text(encoding="utf-8"))
    assert (payload["account_id"], payload["bot_token"]) in {
        ("bot-0", "secret-0"),
        ("bot-1", "secret-1"),
    }
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600
    assert not list(private.glob(".ilink-credential-*.tmp"))


def test_credentials_reject_repository_paths_and_symlinked_parents(tmp_path: Path) -> None:
    credential = ILinkOperatorCredential(
        schema_version=1,
        account_id="bot",
        bot_token="secret",
        base_url=ILINK_BASE_URL,
        user_id=None,
        upstream_package=UPSTREAM_PACKAGE,
        upstream_version=UPSTREAM_VERSION,
        issued_at="2026-07-21T07:00:00+00:00",
    )
    repository = tmp_path / "repository"
    repository.mkdir(mode=0o700)
    private_inside = repository / "private"
    private_inside.mkdir(mode=0o700)
    with pytest.raises(ILinkOperatorLoginError, match="inside_repository"):
        write_credential_file(
            credential,
            private_inside / "ilink.json",
            repository_root=repository,
        )

    real_parent = tmp_path / "real-private"
    real_parent.mkdir(mode=0o700)
    linked_parent = tmp_path / "linked-private"
    os.symlink(real_parent, linked_parent)
    with pytest.raises(ILinkOperatorLoginError, match="parent_symlink_rejected"):
        write_credential_file(
            credential,
            linked_parent / "ilink.json",
            repository_root=None,
        )
