import json

import httpx
import pytest
from pydantic import ValidationError

from research.providers.llm import ModelUnavailableError, ResearchModelClient
from services.api.app.core.config import Settings


def settings(**values):
    return Settings(_env_file=None, **values)


def test_keyless_research_is_ready_but_does_not_call_an_llm():
    config = settings()
    client = ResearchModelClient(config)
    assert config.live_ready and config.model_ready
    assert not client.available
    assert "Deterministic" in client.check_connection()
    with pytest.raises(ModelUnavailableError):
        client.generate("analyst", "research", {})


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com",
        "http://192.168.1.5:11434",
        "http://127.0.0.1.evil.test",
        "http://user:key@localhost:11434",
        "http://localhost:11434/api",
        "http://localhost:11434?send=remote",
        "file:///tmp/model",
    ],
)
def test_remote_or_ambiguous_ollama_endpoints_are_rejected(url):
    with pytest.raises(ValidationError):
        settings(model_router_provider="ollama", ollama_base_url=url)


def test_groq_adapter_uses_user_key_bounds_evidence_and_output():
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "Evidence-backed analysis"}}]}
        )

    config = settings(
        model_router_provider="groq", groq_api_key="private-test-key", model_context_max_chars=2000
    )
    client = ResearchModelClient(config, transport=httpx.MockTransport(respond))
    result = client.generate(
        "Fundamental Analyst",
        "Assess evidence",
        {"api_key": "context-key", "document": "x" * 50000},
    )
    assert result == "Evidence-backed analysis"
    request = calls[0]
    assert str(request.url) == "https://api.groq.com/openai/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer private-test-key"
    payload = json.loads(request.content)
    assert payload["model"] == "openai/gpt-oss-120b"
    assert payload["max_completion_tokens"] == 1500
    assert len(payload["messages"][1]["content"]) < 2300
    assert "context-key" not in request.content.decode()
    assert "untrusted data" in payload["messages"][0]["content"]
    assert "private-test-key" not in repr(config)


def test_ollama_adapter_is_local_and_disables_streaming():
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(200, json={"message": {"content": "Local evidence review"}})

    client = ResearchModelClient(
        settings(model_router_provider="ollama"), transport=httpx.MockTransport(respond)
    )
    assert client.generate("Research Director", "Review", {}) == "Local evidence review"
    assert str(calls[0].url) == "http://127.0.0.1:11434/api/chat"
    payload = json.loads(calls[0].content)
    assert payload["stream"] is False and payload["options"]["num_predict"] == 1500
    assert "authorization" not in calls[0].headers


def test_rate_limit_retries_are_bounded_and_errors_never_echo_key(monkeypatch):
    calls = []
    monkeypatch.setattr("research.providers.llm.time.sleep", lambda delay: None)

    def respond(request):
        calls.append(request)
        return httpx.Response(
            429, json={"error": "private-test-key"}, headers={"Retry-After": "1000"}
        )

    client = ResearchModelClient(
        settings(model_router_provider="groq", groq_api_key="private-test-key"),
        transport=httpx.MockTransport(respond),
    )
    with pytest.raises(ModelUnavailableError, match="quota") as error:
        client.generate("analyst", "Review", {})
    assert len(calls) == 2
    assert "private-test-key" not in str(error.value)


def test_timeout_retries_are_bounded(monkeypatch):
    calls = []
    monkeypatch.setattr("research.providers.llm.time.sleep", lambda delay: None)

    def respond(request):
        calls.append(request)
        raise httpx.ReadTimeout("secret request headers", request=request)

    client = ResearchModelClient(
        settings(model_router_provider="ollama"), transport=httpx.MockTransport(respond)
    )
    with pytest.raises(ModelUnavailableError, match="timed out"):
        client.generate("analyst", "Review", {})
    assert len(calls) == 2


@pytest.mark.parametrize(
    "provider,body",
    [
        ("groq", {"data": [{"id": "openai/gpt-oss-120b"}]}),
        ("ollama", {"models": [{"name": "llama3.2:latest"}]}),
    ],
)
def test_connectivity_checks_models_without_generating_billable_tokens(provider, body):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json=body)

    client = ResearchModelClient(
        settings(model_router_provider=provider, groq_api_key="test-key"),
        transport=httpx.MockTransport(respond),
    )
    assert "available" in client.check_connection()
    assert requests[0].method == "GET"


def test_missing_key_or_unknown_provider_is_not_ready():
    assert settings(model_router_provider="groq").live_ready
    assert not settings(model_router_provider="groq").model_ready
    with pytest.raises(ValidationError):
        settings(model_router_provider="unsupported")


def test_invalid_model_response_is_a_safe_failure():
    client = ResearchModelClient(
        settings(model_router_provider="ollama"),
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"message": None})),
    )
    with pytest.raises(ModelUnavailableError, match="no usable"):
        client.generate("analyst", "Review", {})


@pytest.mark.parametrize(
    "content",
    [
        "Terminal cash flow is INR 8.91 bn.",
        "Minority interests are 1.818 billion rupees.",
        "Terminal cash flow is eighty billion rupees.",
        "A change of ten basis points improves value.",
        "The growth assumption is \uff13 per cent.",
        "## 1. Findings\nThe scenario requires review.",
    ],
)
def test_qualitative_report_rejects_generated_amounts_and_numbered_claims(content):
    client = ResearchModelClient(
        settings(model_router_provider="ollama"),
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"message": {"content": content}})
        ),
    )
    with pytest.raises(ModelUnavailableError, match="numerical claims.*withheld"):
        client.generate("analyst", "Review", {}, qualitative_only=True)


def test_qualitative_report_accepts_analysis_and_requests_no_conversions():
    calls = []
    content = "The Nifty 50 beta is historical. Reinvestment and terminal dependence need review."

    def respond(request):
        calls.append(request)
        return httpx.Response(200, json={"message": {"content": content}})

    client = ResearchModelClient(
        settings(model_router_provider="ollama"), transport=httpx.MockTransport(respond)
    )
    assert client.generate("analyst", "Review", {}, qualitative_only=True) == content
    prompt = json.loads(calls[0].content)["messages"][0]["content"]
    assert "unit conversions" in prompt and "Do not repeat financial figures" in prompt


def test_all_selected_document_ids_survive_large_company_context():
    from uuid import uuid4

    from research.providers.llm import _serialize_context

    ids = [str(uuid4()) for _ in range(12)]
    context = {
        "company": {"description": "large company evidence" * 50000},
        "documents": [
            {
                "source_version_id": source_id,
                "title": "Annual filing",
                "content": "attached evidence " * 5000,
            }
            for source_id in ids
        ],
        "metrics": {"revenue": 1234},
    }
    serialized = _serialize_context(context, 2000)
    value = json.loads(serialized)
    assert len(serialized) <= 2000
    assert [document["source_version_id"] for document in value["documents"]] == ids
    assert value["metrics"]["revenue"] == 1234
    assert value["context_truncated"] is True


def test_escaped_document_content_remains_within_context_budget():
    from research.providers.llm import _serialize_context

    value = _serialize_context(
        {
            "documents": [
                {
                    "source_version_id": f"source-{number}",
                    "title": '\\"' * 100,
                    "content": '\n\t\\"' * 5000,
                }
                for number in range(12)
            ]
        },
        2000,
    )
    assert len(value) <= 2000
    assert len(json.loads(value)["documents"]) == 12
