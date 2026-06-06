#!/usr/bin/env python3

import boto3
import logging
import uuid
from datetime import datetime
from strands.hooks import HookProvider, HookRegistry, MessageAddedEvent, AfterInvocationEvent
from bedrock_agentcore.memory import MemoryClient

logger = logging.getLogger(__name__)


def _first_text_block(content) -> str | None:
    """Return the first content block's text, skipping toolUse/toolResult blocks."""
    if isinstance(content, str):
        return content or None
    if not isinstance(content, list):
        return None
    for block in content:
        if isinstance(block, dict):
            text = block.get("text")
            if isinstance(text, str) and text.strip():
                return text
    return None


class SalesAgentMemoryHooks(HookProvider):
    """Memory hooks for sales agent"""

    def __init__(self, memory_id: str, client: MemoryClient, actor_id: str, session_id: str):
        self.memory_id = memory_id
        self.client = client
        self.actor_id = actor_id
        self.session_id = session_id
        try:
            self.namespaces = {
                i["type"]: i["namespaces"][0]
                for i in self.client.get_memory_strategies(self.memory_id)
            }
        except Exception as e:
            logger.warning(f"Could not load memory strategies for {memory_id}: {e}")
            self.namespaces = {}

    def retrieve_sales_context(self, event: MessageAddedEvent):
        """Retrieve sales context before processing query"""
        messages = event.agent.messages
        if messages[-1]["role"] == "user":
            # Handle different content formats
            content = messages[-1]["content"]
            if isinstance(content, list) and len(content) > 0:
                if isinstance(content[0], dict) and "text" in content[0]:
                    user_query = content[0]["text"]
                else:
                    return  # Skip if not text content
            else:
                return  # Skip if no content

            try:
                all_context = []
                for context_type, namespace in self.namespaces.items():
                    memories = self.client.retrieve_memories(
                        memory_id=self.memory_id,
                        namespace=namespace.format(actorId=self.actor_id),
                        query=user_query,
                        top_k=3,
                    )
                    for memory in memories:
                        if isinstance(memory, dict):
                            content = memory.get("content", {})
                            if isinstance(content, dict):
                                text = content.get("text", "").strip()
                                if text:
                                    all_context.append(f"[{context_type.upper()}] {text}")

                if all_context:
                    context_text = "\n".join(all_context)
                    original_text = messages[-1]["content"][0]["text"]
                    messages[-1]["content"][0]["text"] = f"Sales Context:\n{context_text}\n\n{original_text}"
                    logger.info(f"Retrieved {len(all_context)} sales context items")

            except Exception as e:
                logger.error(f"Failed to retrieve sales context: {e}")

    def save_sales_interaction(self, event: AfterInvocationEvent):
        """Save sales interaction after agent response.

        Walks each message's content blocks and picks the first one with a
        plain `text` field — Strands assistant turns commonly start with a
        `toolUse` block (no `text` key), and user turns may contain
        `toolResult` blocks; blindly indexing `[0]["text"]` raises KeyError
        and the whole event is dropped.
        """
        try:
            messages = event.agent.messages
            if len(messages) < 2 or messages[-1]["role"] != "assistant":
                return

            customer_query = None
            agent_response = None

            for msg in reversed(messages):
                if msg["role"] == "assistant" and not agent_response:
                    agent_response = _first_text_block(msg.get("content"))
                elif msg["role"] == "user" and not customer_query:
                    customer_query = _first_text_block(msg.get("content"))
                    if customer_query:
                        break

            if customer_query and agent_response:
                self.client.create_event(
                    memory_id=self.memory_id,
                    actor_id=self.actor_id,
                    session_id=self.session_id,
                    messages=[
                        (customer_query, "USER"),
                        (agent_response, "ASSISTANT"),
                    ],
                )
                logger.info("Saved sales interaction to memory")
            else:
                logger.warning(
                    "Skipping memory save: no text content found "
                    f"(query={'yes' if customer_query else 'no'}, "
                    f"response={'yes' if agent_response else 'no'})"
                )

        except Exception as e:
            logger.error(f"Failed to save sales interaction: {e}")

    def register_hooks(self, registry: HookRegistry) -> None:
        """Register sales memory hooks"""
        registry.add_callback(MessageAddedEvent, self.retrieve_sales_context)
        registry.add_callback(AfterInvocationEvent, self.save_sales_interaction)
        logger.info("Sales memory hooks registered")
