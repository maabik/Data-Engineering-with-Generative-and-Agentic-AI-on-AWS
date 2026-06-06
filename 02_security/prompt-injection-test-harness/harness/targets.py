"""Target adapters — connect the harness to any Bedrock-based agent.

A Target is anything with an `invoke(user_input: str) -> str` method plus an
optional `canaries` list (strings that should never appear in a response).

Built-in adapters:
- BedrockRuntimeTarget: a Bedrock Converse API call with a configurable
  system prompt and model ID. Good for testing a base model or a thin
  inference wrapper.
- BedrockAgentTarget: invokes a Bedrock Agent (agents-runtime) with an
  agentId/agentAliasId. Good for testing agents with knowledge bases,
  action groups, and guardrails.
- CallableTarget: wraps any `(str) -> str` callable. Use this for Strands
  agents, custom orchestrators, or stubs in tests.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol


class Target(Protocol):
    name: str
    canaries: list[str]

    def invoke(self, user_input: str) -> str: ...


@dataclass
class CallableTarget:
    """Wrap any `(str) -> str` callable as a Target.

    Useful for Strands agents:

        from strands import Agent
        agent = Agent(model=..., tools=[...])
        target = CallableTarget(
            name="my-strands-agent",
            fn=lambda s: str(agent(s)),
            canaries=["CANARY_xyz"],
        )
    """

    name: str
    fn: Callable[[str], str]
    canaries: list[str] = field(default_factory=list)

    def invoke(self, user_input: str) -> str:
        return self.fn(user_input)


@dataclass
class BedrockRuntimeTarget:
    """Bedrock Converse API target.

    Sends the payload as a single user message against `model_id`, with
    `system_prompt` (which should embed the canary strings so exfil detectors
    can spot leaks).
    """

    model_id: str
    system_prompt: str
    canaries: list[str] = field(default_factory=list)
    region: str = "us-west-2"
    name: str = "bedrock-runtime"
    max_tokens: int = 512
    temperature: float = 0.0
    _client: Any = None

    def __post_init__(self) -> None:
        import boto3
        self._client = boto3.client("bedrock-runtime", region_name=self.region)

    def invoke(self, user_input: str) -> str:
        resp = self._client.converse(
            modelId=self.model_id,
            system=[{"text": self.system_prompt}],
            messages=[{"role": "user", "content": [{"text": user_input}]}],
            inferenceConfig={"maxTokens": self.max_tokens, "temperature": self.temperature},
        )
        content = resp["output"]["message"]["content"]
        return "".join(c.get("text", "") for c in content)


@dataclass
class BedrockAgentTarget:
    """Bedrock Agent (bedrock-agent-runtime) target.

    Targets an agent with a specific alias. Canaries are not injected by
    the harness — they must already live in the agent's configured instructions
    (or in a KB document, if you're testing indirect injection against a KB).
    """

    agent_id: str
    agent_alias_id: str
    canaries: list[str] = field(default_factory=list)
    region: str = "us-west-2"
    name: str = "bedrock-agent"
    session_id_prefix: str = "pi-harness"
    enable_trace: bool = False
    _client: Any = None

    def __post_init__(self) -> None:
        import boto3
        self._client = boto3.client("bedrock-agent-runtime", region_name=self.region)

    def invoke(self, user_input: str) -> str:
        session_id = f"{self.session_id_prefix}-{uuid.uuid4().hex[:12]}"
        resp = self._client.invoke_agent(
            agentId=self.agent_id,
            agentAliasId=self.agent_alias_id,
            sessionId=session_id,
            inputText=user_input,
            enableTrace=self.enable_trace,
        )
        chunks: list[str] = []
        for event in resp["completion"]:
            if "chunk" in event:
                payload = event["chunk"].get("bytes", b"")
                if isinstance(payload, (bytes, bytearray)):
                    chunks.append(payload.decode("utf-8", errors="replace"))
        return "".join(chunks)
