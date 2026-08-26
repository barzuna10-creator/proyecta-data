"""Exec-only Claude runtime; importing it is deliberately inert.

Supported authenticated execution occurs only in the worker child. Arbitrary
``runpy``/``exec`` in a compromised trusted parent is outside Zentra's threat
model and cannot be prevented by Python-level provenance checks.
"""

if __name__ == "__main__":
    import asyncio
    import json
    from claude_agent_sdk import ClaudeSDKClient

    from orchestrator.agent_invocation import AgentInvocationResult
    from orchestrator.adapters import claude_adapter as helpers
    from orchestrator.provider_credentials import validate_dedicated_key

    request = _ZENTRA_CHILD_REQUEST
    api_key = validate_dedicated_key(_ZENTRA_CHILD_API_KEY, provider="Claude")
    model = helpers.DEFAULT_MODEL
    timeout_seconds = helpers.DEFAULT_TIMEOUT_SECONDS
    helpers._verify_no_ambient_credential_env()
    if request.agent_role != "emma":
        raise helpers.ClaudeAdapterError(
            "direct-key Claude authentication is authorized only for Emma; "
            "Emilio-through-Claude remains fail-closed"
        )
    if helpers._contains_secret(request.task, api_key):
        raise helpers.ClaudeAdapterError(
            "provider credential must not appear in invocation request"
        )

    async def run_attempt(options):
        last_result = None
        async with ClaudeSDKClient(options=options) as client:
            await client.query(json.dumps(request.task))
            async for message in client.receive_response():
                if isinstance(message, helpers.ResultMessage):
                    last_result = message
        if last_result is None:
            raise helpers.ResultError("no ResultMessage received before the response stream ended")
        return last_result

    with helpers._isolated_claude_config_dir() as config_dir:
        options = helpers._build_worker_options(
            request, config_dir, api_key=api_key, model=model,
            timeout_seconds=timeout_seconds,
        )
        try:
            message = asyncio.run(asyncio.wait_for(run_attempt(options), timeout=timeout_seconds))
            session_id = getattr(message, "session_id", None)
            evidence = getattr(message, "structured_output", None)
            if getattr(message, "is_error", False):
                outcome, evidence = "failed", None
                error_detail = (
                    "ResultMessage.is_error was True "
                    f"(errors={getattr(message, 'errors', None)!r}, "
                    f"api_error_status={getattr(message, 'api_error_status', None)!r})"
                )
            elif evidence is None:
                outcome, error_detail = "invalid_output", "ResultMessage.structured_output was empty/None despite no exception"
            elif helpers._contains_secret(evidence, api_key):
                outcome, evidence, error_detail = "invalid_output", None, "provider output contained forbidden credential material"
            else:
                outcome, error_detail = "completed", None
        except Exception as exc:
            outcome, error_detail = helpers._map_exception_to_outcome(exc)
            error_detail = helpers._redact_secret(error_detail, api_key)
            evidence, session_id = None, None
    _ZENTRA_CHILD_RESULT = AgentInvocationResult(
        invocation_id=request.invocation_id, outcome=outcome, provider="claude",
        model=model, responded_at=helpers._now(), fresh_context_attested=True,
        provider_session_id=session_id, provider_conversation_id=None,
        evidence=evidence, error_detail=error_detail,
    )
