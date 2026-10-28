"""LangSmith: mirror the versioned prompts and upload evaluation results as experiments.

    uv run python -m nirnaya.integrations.langsmith prompts
    uv run python -m nirnaya.integrations.langsmith experiments

Needs LANGSMITH_API_KEY (and LANGSMITH_ENDPOINT for a non-default region). Tracing of live
model calls is separate: nirnaya.llm.tracing, switched on with LANGSMITH_TRACING=true.
The repo stays the source of truth: prompts are pushed read-only, and experiments are the
report's stored results, not new model calls.
"""
