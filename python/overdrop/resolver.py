"""OverDrop — AI Resolver for MergeQueue conflict resolution.

Provides mock and real AI resolver implementations for Tier 2/3 conflicts.

Usage:
    from overdrop.resolver import MockResolver, AiResolver

    # Mock resolver (for testing)
    resolver = MockResolver()

    # Real resolver (requires LLM API)
    resolver = AiResolver(model="router://deepseek-v4-flash")

    # Use with MergeQueue
    mq = MergeQueue(repo, ai_resolver=resolver.resolve)
"""

import json
import logging
import os
import subprocess
from dataclasses import dataclass, field
from typing import Optional, Callable

logger = logging.getLogger("overdrop.resolver")


@dataclass
class ConflictContext:
    """Context for conflict resolution."""
    task_id: str
    branch: str
    base_branch: str
    conflict_files: list = field(default_factory=list)
    conflict_markers: str = ""
    task_title: str = ""
    task_context: dict = field(default_factory=dict)


class MockResolver:
    """Mock AI resolver for testing.

    Always resolves conflicts by accepting the incoming branch.
    """

    def __init__(self, should_resolve: bool = True):
        self.should_resolve = should_resolve
        self.calls = []

    def resolve(self, merge_request, error: str) -> bool:
        """Mock resolution — always succeeds or fails based on config."""
        self.calls.append({
            "task_id": merge_request.task_id,
            "error": error[:200],
        })
        logger.info(f"MockResolver: resolving {merge_request.task_id[:8]}")
        return self.should_resolve


class AiResolver:
    """Real AI resolver using LLM API.

    Sends conflict diff to LLM and gets resolution suggestions.
    """

    def __init__(self, model: str = "router://deepseek-v4-flash",
                 timeout: int = 30,
                 api_base: str = None,
                 api_key: str = None):
        self.model = model
        self.timeout = timeout
        self.api_base = api_base or os.environ.get("OPENAI_API_BASE", "http://localhost:18883/v1")
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "sk-placeholder")
        self.calls = []

    def resolve(self, merge_request, error: str) -> bool:
        """Resolve conflict using LLM.

        Returns True if resolution succeeded.
        """
        self.calls.append({
            "task_id": merge_request.task_id,
            "error": error[:200],
        })

        try:
            # Get conflict context
            context = self._get_conflict_context(merge_request)

            # Build prompt
            prompt = self._build_prompt(context, error)

            # Call LLM
            response = self._call_llm(prompt)

            # Parse and apply resolution
            if response and self._apply_resolution(response, merge_request):
                logger.info(f"AI resolved conflict: {merge_request.task_id[:8]}")
                return True

            logger.warning(f"AI resolution failed: {merge_request.task_id[:8]}")
            return False

        except Exception as e:
            logger.error(f"AI resolver error: {e}")
            return False

    def _get_conflict_context(self, merge_request) -> ConflictContext:
        """Get context about the conflict."""
        repo = os.path.dirname(merge_request.worktree_path)

        # Get conflict files
        try:
            result = subprocess.run(
                ["git", "diff", "--name-only", "--diff-filter=U"],
                cwd=repo, capture_output=True, text=True,
            )
            conflict_files = [f.strip() for f in result.stdout.strip().split("\n") if f.strip()]
        except Exception:
            conflict_files = []

        # Get conflict markers
        try:
            result = subprocess.run(
                ["git", "diff"],
                cwd=repo, capture_output=True, text=True,
            )
            conflict_markers = result.stdout[:2000]
        except Exception:
            conflict_markers = ""

        return ConflictContext(
            task_id=merge_request.task_id,
            branch=merge_request.branch,
            base_branch="main",
            conflict_files=conflict_files,
            conflict_markers=conflict_markers,
        )

    def _build_prompt(self, context: ConflictContext, error: str) -> str:
        """Build prompt for LLM."""
        files_list = "\n".join(f"  - {f}" for f in context.conflict_files)

        return f"""You are a git merge conflict resolver. Analyze the following conflict and suggest how to resolve it.

## Conflict Info
- Task: {context.task_id[:8]}
- Branch: {context.branch}
- Conflict files ({len(context.conflict_files)}):
{files_list}

## Conflict Diff (first 2000 chars)
```
{context.conflict_markers}
```

## Git Error
{error[:500]}

## Instructions
1. Analyze the conflict markers
2. Determine if it's a simple text conflict or semantic
3. Suggest a resolution strategy
4. If resolvable, provide the resolved content

Respond with JSON:
{{
    "resolvable": true/false,
    "strategy": "accept_incoming" | "accept_base" | "merge_both" | "manual",
    "reason": "brief explanation",
    "resolved_files": {{"filename": "resolved content"}} (if resolvable)
}}"""

    def _call_llm(self, prompt: str) -> Optional[dict]:
        """Call LLM API for resolution."""
        import urllib.request
        import urllib.error

        try:
            payload = json.dumps({
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.3,
                "max_tokens": 2000,
            }).encode("utf-8")

            req = urllib.request.Request(
                f"{self.api_base}/chat/completions",
                data=payload,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.api_key}",
                },
            )

            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read())
                content = data["choices"][0]["message"]["content"]

                # Try to parse JSON from response
                # Look for JSON block in markdown
                if "```json" in content:
                    start = content.index("```json") + 7
                    end = content.index("```", start)
                    content = content[start:end].strip()
                elif "```" in content:
                    start = content.index("```") + 3
                    end = content.index("```", start)
                    content = content[start:end].strip()

                return json.loads(content)

        except (urllib.error.URLError, json.JSONDecodeError, KeyError) as e:
            logger.error(f"LLM call failed: {e}")
            return None

    def _apply_resolution(self, resolution: dict, merge_request) -> bool:
        """Apply resolution to the merge request."""
        if not resolution.get("resolvable"):
            return False

        strategy = resolution.get("strategy", "manual")
        resolved_files = resolution.get("resolved_files", {})

        if strategy == "manual" or not resolved_files:
            return False

        # Apply resolved files
        for filename, content in resolved_files.items():
            filepath = os.path.join(merge_request.worktree_path, filename)
            os.makedirs(os.path.dirname(filepath), exist_ok=True)
            with open(filepath, "w") as f:
                f.write(content)

        return True


def create_resolver(config: dict = None) -> Optional[Callable]:
    """Create resolver based on config.

    Returns:
        Callable resolver function or None if disabled.
    """
    if config is None:
        config = {}

    if not config.get("enabled", False):
        return None

    model = config.get("model", "router://deepseek-v4-flash")
    timeout = config.get("timeout", 30)

    resolver = AiResolver(model=model, timeout=timeout)
    return resolver.resolve
