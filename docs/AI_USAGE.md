# AI Usage Documentation

> **To complete before submission:** review this file, keep it accurate, and attach the exported chat history.

## Tools and models

| Tool | Model | Interface | Configuration / settings |
|---|---|---|---|
| Claude (Anthropic) | Claude Opus 5.5 (`claude-opus-5-5`) | claude.ai agentic workspace (code execution sandbox with Python, PostgreSQL 16 and Git) | Default settings. Custom user preferences: "act as a university-level AI tutor; explain from first principles; prefer Python; production-quality code". |

## What the AI was used for

- Generating the project code (`db.py`, `models.py`, `schemas.py`, `services.py`, `auth.py`, `main.py`), the pytest suite and the demo frontend, from the assignment brief and grading rubric.
- Running the code and tests against a real PostgreSQL database in its sandbox and fixing the issues it found (e.g. an accidental extra database column, a race-condition test that did not actually detect the race).
- Writing the README, the code walkthrough (`docs/CODE_WALKTHROUGH.md`) and the demo-video outline.
- Creating the Git history with feature branches (each AI-assisted commit carries a `Co-Authored-By: Claude` trailer).

## What I did myself

<!-- Fill in honestly, for example: -->
- Studied the code using `docs/CODE_WALKTHROUGH.md` and verified I can explain each file and each design decision.
- Set up PostgreSQL locally, configured `.env`, ran the server and the test suite on my machine.
- Tested every endpoint in Swagger and took the screenshots in `docs/screenshots/`.
- Created the GitHub repository and pushed the code.
- Recorded the demo video.
- <!-- any changes you made yourself -->

## Prompts used

1. *(Uploaded `Assignment-03-Corporate-Event-Management-FastAPI.pdf` and `Assignment-03-Grading-Rubric-FastAPI.xlsx - Rubric.pdf`, with no text.)*
2. Answer to the assistant's clarifying question "What should I do with them?": **"Build the full project"** (chosen over "build it with me step by step", "plan + study notes only" and "review my existing code").
3. *(Pasted the instructor's announcement message for the assignment.)*
<!-- 4. Add every follow-up prompt you send, in order. -->

## Chat history

The complete conversation is exported and attached as: `docs/ai-chat-history.pdf` *(or include the share link here)*.
