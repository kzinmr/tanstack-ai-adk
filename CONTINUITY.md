Goal (incl. success criteria):
- Add Gemini on Vertex AI credential support following ADK patterns and call-insight-chat reference implementation.
- Success: Settings class supports GOOGLE_APPLICATION_CREDENTIALS, GCP_PROJECT, GCP_REGION; credentials propagate correctly to ADK.
Constraints/Assumptions:
- Follow AGENTS.md/CLAUDE.md instructions; update CONTINUITY.md at start of each turn and when state changes.
- Environment: exe.dev VM; approval_policy on-request; sandbox_mode workspace-write; network_access restricted.
- Reference: ../call-insight-chat/backend/src/backend/llm/google_credentials.py pattern.
Key decisions:
- Use Pydantic Settings for credential fields (google_application_credentials, gcp_project, gcp_region).
- Create google_credentials.py helper to parse JSON string or file path.
- Propagate credentials to os.environ in @model_validator for ADK consumption.
State:
- Completed Vertex AI credential integration.
Done:
- Explored credential setup patterns in both codebases.
- Added google_application_credentials and gcp_region fields to Settings (settings.py:73-81).
- Created backend/src/backend/llm/google_credentials.py helper module.
- Updated @model_validator to propagate Vertex AI credentials and call setup_google_credentials() (settings.py:35-46).
- Updated .env.example with GOOGLE_APPLICATION_CREDENTIALS and GCP_REGION examples (lines 5-8).
- Updated llm_model description to include google-vertex:* examples (settings.py:52).
- Tested Settings class loading successfully.
Now:
- Implementation complete.
Next:
- None (task completed).
Open questions (UNCONFIRMED if needed):
- None.
Working set (files/ids/commands):
- /home/exedev/tanstack-ai-adk/CONTINUITY.md
- /home/exedev/tanstack-ai-adk/backend/src/backend/settings.py
- /home/exedev/tanstack-ai-adk/backend/.env.example
- /home/exedev/call-insight-chat/backend/src/backend/settings.py
- /home/exedev/call-insight-chat/backend/src/backend/llm/google_credentials.py
