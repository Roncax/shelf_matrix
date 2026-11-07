import pytest

from shelf_matrix.protocol import (
    Message,
    ProtocolError,
    decode,
    decode_preview,
    encode,
    encode_preview,
)


def test_encode_decode_roundtrip():
    text = encode("set_globals", globals={"brightness": 40})
    assert decode(text) == Message("set_globals", {"globals": {"brightness": 40}})


@pytest.mark.parametrize("text", ["not json", "[]", '{"v":1}', '{"v":2,"kind":"x"}'])
def test_decode_rejects_garbage(text):
    with pytest.raises(ProtocolError):
        decode(text)


def test_decode_accepts_bytes():
    assert decode(encode("preview_on").encode()) == Message("preview_on", {})


def test_preview_roundtrip():
    assert decode_preview(encode_preview(bytes(12288))) == bytes(12288)


def test_preview_rejects_wrong_length():
    with pytest.raises(ValueError):
        encode_preview(b"abc")
    with pytest.raises(ProtocolError):
        decode_preview(b"\x01abc")
    with pytest.raises(ProtocolError):
        decode_preview(b"\x02" + bytes(12288))
