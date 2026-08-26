"""Exec-only Codex runtime.

Importing this module defines no authenticated callable.  The shared provider
worker executes it with ``runpy`` only after the OS process boundary has been
created with the canonical environment.

Importing this module is inert. Arbitrary ``runpy``/``exec`` in an already
compromised parent is outside Zentra's threat model; supported production
execution always crosses the worker OS process boundary first.
"""

if __name__ == "__main__":
    import asyncio
    import json
    from openai_codex import ApprovalMode, Codex, CodexConfig

    from orchestrator.agent_invocation import AgentInvocationResult
    from orchestrator.adapters import codex_adapter as helpers
    from orchestrator.provider_credentials import validate_dedicated_key

    request = _ZENTRA_CHILD_REQUEST
    api_key = validate_dedicated_key(_ZENTRA_CHILD_API_KEY, provider="Codex")
    timeout_seconds = helpers.DEFAULT_TIMEOUT_SECONDS
    helpers._verify_no_ambient_codex_credentials()

    async def run_attempt():
        repository = request.task.get("repository") or {}
        worktree = helpers._validate_worktree_path(repository.get("worktree_path"))
        with helpers._isolated_codex_home(request.agent_role, worktree) as codex_home:
            child_env = {
                "CODEX_HOME": str(codex_home), "HOME": str(codex_home),
                "TMPDIR": str(codex_home), "TMP": str(codex_home),
                "TEMP": str(codex_home), "OPENAI_API_KEY": "",
            }
            with Codex(config=CodexConfig(env=child_env)) as codex:
                codex.login_api_key(api_key)
                helpers._verify_api_key_identity_active(codex)
                thread = codex.thread_start(
                    approval_mode=ApprovalMode.deny_all, cwd=str(worktree)
                )
                thread_id = getattr(thread, "id", None)
                result = thread.run(
                    json.dumps(request.task),
                    output_schema=helpers._load_evidence_schema(request.agent_role),
                )
                non_completed = helpers._map_turn_status_to_outcome(
                    getattr(result, "status", None), getattr(result, "error", None)
                )
                if non_completed is not None:
                    outcome, detail = non_completed
                    return outcome, None, detail, thread_id
                return "completed", json.loads(result.final_response), None, thread_id

    if helpers._contains_secret(request.task, api_key):
        raise helpers.CodexAdapterError(
            "provider credential must not appear in invocation request"
        )
    try:
        outcome, evidence, error_detail, thread_id = asyncio.run(
            asyncio.wait_for(run_attempt(), timeout=timeout_seconds)
        )
    except helpers.CodexAdapterError:
        raise
    except Exception as exc:
        outcome, error_detail = helpers._map_exception_to_outcome(exc)
        evidence, thread_id = None, None
    if error_detail is not None:
        error_detail = helpers._redact_secret(error_detail, api_key)
    if evidence is not None and helpers._contains_secret(evidence, api_key):
        outcome, evidence, error_detail = (
            "invalid_output", None,
            "provider output contained forbidden credential material",
        )
    _ZENTRA_CHILD_RESULT = AgentInvocationResult(
        invocation_id=request.invocation_id, outcome=outcome, provider="codex",
        model=None, responded_at=helpers._now(), fresh_context_attested=True,
        provider_session_id=None, provider_conversation_id=thread_id,
        evidence=evidence, error_detail=error_detail,
    )
