import base64

from app.collectors.hyphen import encrypt_field


def test_encrypt_field_is_base64_of_aes_blocks():
    out = encrypt_field("8001011234567", "0123456789abcdef")
    raw = base64.b64decode(out)
    assert len(raw) % 16 == 0
