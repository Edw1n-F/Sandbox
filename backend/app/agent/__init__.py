"""The chat agent: structured, grounded answers about a sandbox run.

Four pieces, in dependency order:

- `llm`         provider-neutral client (Anthropic or any OpenAI-compatible endpoint)
- `answer`      the answer card, and the code-built template for when there is no model
- `prompts`     the rules, and the FACTS block a model is allowed to quote
- `grounding`   the check that every number in a card came from the run
- `chat_service` orchestration: steps, the one-round revision loop, stream events

Taken from the Constellation repo's agent loop, minus what a sandbox run does
not have: no skills, no tool calls the model chooses, no clarification cards.
What is kept is the part that makes the answer trustworthy rather than merely
fluent -- visible steps and number grounding.
"""
