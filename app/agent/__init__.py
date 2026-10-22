"""PydanticAI agent: vendor-agnostic model resolution, prompt, structured output and validators."""

import os

# PydanticAI prints an observability banner on first run; keep logs clean (can still be overridden by env).
os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")
