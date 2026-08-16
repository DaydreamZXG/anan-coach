from base64 import b64decode
import json

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from app.payment import PREORDER_PATH, SkillHubPreorderSigner


def test_skillhub_rsa_signing_uses_exact_five_line_payload():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    signer = SkillHubPreorderSigner("sh-test", "pub-test", pem)
    payload = signer.build(
        payment_code="weixin://test",
        out_trade_no="QB00000000000001",
        amount_minor=990,
        plan="week",
        now=1_700_000_000,
        nonce="0123456789abcdef0123456789abcdef",
    )
    sign_text = (
        f"POST\n{PREORDER_PATH}\n1700000000\n"
        "0123456789abcdef0123456789abcdef\n"
        f"{payload['payment_required']}\n"
    )
    private.public_key().verify(
        b64decode(payload["signature"]), sign_text.encode(), padding.PKCS1v15(), hashes.SHA256()
    )
    layer2 = json.loads(b64decode(payload["payment_required"]))
    assert layer2["pay_type"] == "SKILL_PAY"
    assert layer2["pay_mode"] == "AUTH_AND_PAY"
    assert layer2["expires_at"] == 1_700_000_900
