"""
Compatibility wrapper for agent entrypoints.
"""

from .agents.sql_agent.agent import build_system_prompt, create_runner, root_agent

__all__ = ["build_system_prompt", "create_runner", "root_agent"]
