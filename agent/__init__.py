"""
AnsibleAI Agent package.

The agent is an orchestrator that sits between the user (chat UI) and the
existing RAG pipeline. It plans tool calls (usually RAG searches), executes
them, and synthesizes a concise response. It can also generate full Ansible
playbooks by retrieving Ansible docs (RAG) and generating YAML with the agent LLM,
then validating.
"""

from .orchestrator import handle_message, AgentResponse

__all__ = ["handle_message", "AgentResponse"]
