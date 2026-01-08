Goal (incl. success criteria):
- Reimplement tanstack-ai demo features in ../tanstack-ai-demo based on ADK (not pydantic-ai), following tanstack-ai-adk-plan.md.
Constraints/Assumptions:
- Use instructions from AGENTS.md and CLAUDE.md.
- Must update CONTINUITY.md at start of every assistant turn and when state changes.
- Environment: exe.dev VM. Approval policy never; sandbox danger-full-access; network enabled.
Key decisions:
- Use tanstack-ai-adk-plan.md as design doc for implementation details.
- Target ADK package is google-adk (v1.21.0 per PyPI download). (UNCONFIRMED exact version in repo)
State:
- ADK backend/frontend reimplementation done in tanstack-ai-demo; pending review/testing.
Done:
- Read AGENTS.md instructions from user message.
- Verified CONTINUITY.md did not exist and created it.
 - Read tanstack-ai-adk-plan.md and ADK API (google-adk wheel) for implementation details.
 - Updated tanstack-ai-demo backend/frontend to use ADK and /api/continuation flow.
Now:
- Review for remaining legacy references and optional test/run guidance.
Next:
- Optional: update legacy docs (approval-continuation-decision.md, happy-path.md) if desired.
Open questions (UNCONFIRMED if needed):
- Should any legacy docs (pydantic-ai references) be fully updated beyond README?
Working set (files/ids/commands):
- /home/exedev/tanstack-ai-adk/CONTINUITY.md
- /home/exedev/tanstack-ai-adk/tanstack-ai-adk-plan.md
