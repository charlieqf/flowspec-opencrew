# PromptKnowledge P0b

Minimal prompt knowledge seed store for Koubo Prompt Agent.

P0b scope:
- `normalized/seed_rules.jsonl` is the hand-curated seed corpus.
- `registry/sources.json` records the seed corpus source metadata.
- `prompt_agent_result.schema.json` is the canonical result block schema.
- `fixtures/prompt_agent_result/cases.json` contains shared parser/validation fixtures.
- `04_BuildPromptIndex.py` rebuilds `index/fts.sqlite`.
- `05_SearchPromptKnowledge.py` searches the same SQLite FTS index for local debugging.

Fixture checks:
- Backend: `python3 backend/tests/contracts/test_prompt_agent_result_fixtures_contract.py`
- Frontend: `npm --prefix frontend run test:prompt-agent-result`

No web crawling, GitHub fetching, embeddings, or vector index are included in P0b.
