import pytest
from app.services.encoding import encode, decode, ALPHABET


def test_base62_basic_encoding():
    assert encode(0) == "0"
    assert encode(1) == "1"
    assert encode(61) == "z"
    assert encode(62) == "10"
    assert encode(3843) == "zz"
    assert encode(3844) == "100"


def test_base62_roundtrip():
    test_values = [
        0,
        1,
        42,
        61,
        62,
        1000,
        123456789,
        (1 << 32) - 1,
        (1 << 63) - 1,  # 64-bit integer
    ]
    for val in test_values:
        encoded = encode(val)
        assert isinstance(encoded, str)
        decoded = decode(encoded)
        assert decoded == val, f"Failed roundtrip for {val}: got {decoded}"


def test_base62_invalid_inputs():
    with pytest.raises(ValueError, match="negative"):
        encode(-5)

    with pytest.raises(ValueError, match="empty"):
        decode("")

    with pytest.raises(ValueError, match="Invalid character"):
        decode("abc!123")

    with pytest.raises(ValueError, match="Invalid character"):
        decode("hello@world")
