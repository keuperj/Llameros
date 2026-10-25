"""Privacy, trusted filesystem ancestry, and cheap protocol admission regressions."""

import os

import pytest
from fastapi import HTTPException

from llameros.config import Settings
from llameros.diagnostics import diagnostic
from llameros.filesystem import trusted_directory
from llameros.multimodal import image_dimensions
from llameros.protocol import validate_protocol


def test_unsafe_ancestor_and_state_symlink(tmp_path):
    parent = tmp_path / "shared"
    parent.mkdir()
    leaf = parent / "private"
    leaf.mkdir(mode=0o700)
    parent.chmod(0o777)
    with pytest.raises(ValueError, match="ancestry"):
        trusted_directory(leaf)
    parent.chmod(0o1777)
    trusted_directory(leaf)
    link = tmp_path / "link"
    link.symlink_to(leaf)
    with pytest.raises(ValueError, match="symbolic"):
        Settings(state_dir=link).prepare()


def test_replacement_with_link_between_open_operations(tmp_path, monkeypatch):
    leaf = tmp_path / "private"
    leaf.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    original = os.open

    def replace(name, flags, *args, **kwargs):
        if name == "private":
            leaf.rmdir()
            leaf.symlink_to(other)
        return original(name, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", replace)
    with pytest.raises(ValueError, match="symbolic"):
        trusted_directory(leaf)


@pytest.mark.parametrize(
    "body",
    [
        {"temperature": float("nan")},
        {"options": {"top_k": True}},
        {"top_p": 1.1},
        {"messages": ["bad"]},
        {"messages": [{"role": [], "content": "x"}]},
        {"messages": [{"role": "user", "content": [{"type": "text", "text": 4}]}]},
        {"tools": [{"type": "function", "function": {"name": "f", "parameters": []}}]},
        {"stream_options": {"include_usage": "yes"}},
    ],
)
def test_nested_protocol_rejection(body):
    with pytest.raises(HTTPException) as error:
        validate_protocol(body)
    assert error.value.status_code == 400


@pytest.mark.parametrize(
    "data", [b"", b"GIF89a", b"\x89PNG\r\n\x1a\n", b"RIFFWEBP", b"\xff\xd8\xff\xc0\x00\x01"]
)
def test_malformed_media_headers(data):
    with pytest.raises(HTTPException):
        image_dimensions(data)


def test_diagnostics_do_not_include_secrets():
    text = diagnostic(ValueError("Bearer llm_secret /home/private prompt=password"))
    assert "runtime-error" in text
    assert not any(value in text for value in ("llm_secret", "/home", "password"))
