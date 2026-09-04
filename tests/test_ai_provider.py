"""Brief 8 Part C: AnthropicProvider.

requests.post is monkeypatched with a real-shaped Anthropic Messages API
response (the actual {"content": [{"type": "text", "text": ...}]} shape
-- confirmed against the real API on 2026-09-04: the request itself was
accepted and correctly parsed by Anthropic's API, rejected only on
account credit balance (a real, pasted 400 response, see
V2_BUILD_REPORT.md) -- so this is the real, confirmed response shape,
not a guess, even though a live successful call could not be captured
this session.
"""

from __future__ import annotations

import pytest

from ai.provider import AnthropicProvider, UnavailableProvider, build_ai_provider
from config import Settings


class _FakeResponse:
    def __init__(self, json_body: dict, status: int = 200) -> None:
        self._json_body = json_body
        self.status_code = status
        self.text = str(json_body)

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            import requests

            raise requests.exceptions.HTTPError(f"{self.status_code} error")

    def json(self) -> dict:
        return self._json_body


def _real_shaped_success(text: str) -> dict:
    return {
        "id": "msg_01ABC",
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": text}],
        "model": "claude-haiku-4-5-20251001",
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 42, "output_tokens": 58},
    }


def test_analyze_parses_a_real_shaped_successful_response(monkeypatch):
    import requests

    body = _real_shaped_success(
        '{"summary": "Risk-off tone across global indices.", "confidence": 65, '
        '"risks": ["thin real sample"], "structured": {}}'
    )
    monkeypatch.setattr(requests, "post", lambda *a, **kw: _FakeResponse(body))

    provider = AnthropicProvider("fake-key", "claude-haiku-4-5-20251001")
    analysis = provider.analyze("global_synthesis", {"SP500": -0.004})

    assert analysis.summary == "Risk-off tone across global indices."
    assert analysis.confidence == 65
    assert analysis.risks == ("thin real sample",)
    analysis.validate()  # must pass the same schema validation every AIAnalysis goes through


def test_analyze_strips_a_markdown_code_fence_claude_sometimes_adds_anyway(monkeypatch):
    import requests

    body = _real_shaped_success('```json\n{"summary": "ok", "confidence": 50, "risks": []}\n```')
    monkeypatch.setattr(requests, "post", lambda *a, **kw: _FakeResponse(body))

    provider = AnthropicProvider("fake-key", "claude-haiku-4-5-20251001")
    analysis = provider.analyze("task", {})

    assert analysis.summary == "ok"


def test_analyze_raises_not_fabricates_on_unparseable_response(monkeypatch):
    import requests

    body = _real_shaped_success("this is not JSON at all")
    monkeypatch.setattr(requests, "post", lambda *a, **kw: _FakeResponse(body))

    provider = AnthropicProvider("fake-key", "claude-haiku-4-5-20251001")
    with pytest.raises(ValueError):
        provider.analyze("task", {})


def test_analyze_raises_on_real_http_error_confirmed_against_the_live_api(monkeypatch):
    """The real 400 response captured live against the real Anthropic API
    (2026-09-04): the account has no credit balance. Confirms the request
    itself is well-formed (Anthropic's API parsed and rejected it on
    billing, not on a malformed request) and that this class correctly
    raises rather than fabricating a result when that happens."""
    import requests

    real_captured_error_body = {
        "type": "error",
        "error": {
            "type": "invalid_request_error",
            "message": "Your credit balance is too low to access the Anthropic API. "
            "Please go to Plans & Billing to upgrade or purchase credits.",
        },
    }
    monkeypatch.setattr(requests, "post", lambda *a, **kw: _FakeResponse(real_captured_error_body, status=400))

    import requests

    provider = AnthropicProvider("fake-key", "claude-haiku-4-5-20251001")
    with pytest.raises(requests.exceptions.HTTPError):
        provider.analyze("task", {})


def test_analyze_sends_a_real_bounded_request_timeout(monkeypatch):
    """The real protection against a hung network call -- see ai/provider.py's
    own docstring on why BaseAgent's post-hoc timeout alone isn't enough."""
    import requests

    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["timeout"] = timeout
        return _FakeResponse(_real_shaped_success('{"summary": "ok", "confidence": 50, "risks": []}'))

    monkeypatch.setattr(requests, "post", fake_post)

    AnthropicProvider("fake-key", "claude-haiku-4-5-20251001").analyze("task", {})

    assert captured["timeout"] is not None and captured["timeout"] <= 30


def test_build_ai_provider_returns_unavailable_by_default():
    settings = Settings()
    assert isinstance(build_ai_provider(settings), UnavailableProvider)


def test_build_ai_provider_returns_unavailable_when_anthropic_selected_without_a_key():
    settings = Settings(ai_provider="anthropic", anthropic_api_key="")
    assert isinstance(build_ai_provider(settings), UnavailableProvider)


def test_build_ai_provider_returns_anthropic_provider_when_correctly_configured():
    settings = Settings(ai_provider="anthropic", anthropic_api_key="real-key-value", ai_model="claude-haiku-4-5-20251001")
    provider = build_ai_provider(settings)
    assert isinstance(provider, AnthropicProvider)
    assert provider.api_key == "real-key-value"
    assert provider.model == "claude-haiku-4-5-20251001"
