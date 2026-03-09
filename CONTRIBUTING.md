# Contributing to AgentWatch

Thanks for your interest in contributing! AgentWatch is a lightweight observability tool for autonomous AI agents, and contributions are welcome.

## Development Setup

```bash
# Clone the repo
git clone https://github.com/agentwatch/agentwatch.git
cd agentwatch

# Install in development mode
pip install -e ".[dev,server]"

# Run tests
pytest

# Run with coverage
pytest --cov=agentwatch --cov-report=term-missing
```

## Project Structure

```
src/agentwatch/
├── __init__.py          # Public API exports
├── core.py              # Agent lifecycle (init/shutdown)
├── tracing.py           # Trace/span context managers
├── logging.py           # Structured logging
├── health.py            # Health check registry
├── storage.py           # SQLite storage layer
├── models.py            # Data models (dataclasses)
├── costs.py             # LLM cost tracking
├── patterns.py          # Pattern detection engine
├── alerts.py            # Alert rules and delivery
├── reports.py           # Summary report generation
├── retention.py         # Data retention & export
├── config.py            # Configuration file support
├── auth.py              # Dashboard authentication
├── async_tracing.py     # Async trace support
├── cli/main.py          # CLI entry point
├── server/              # Web dashboard (FastAPI)
├── exporters/           # Prometheus, etc.
└── integrations/        # OpenClaw, FastAPI middleware, hooks
```

## Guidelines

### Code Style

- **Type hints** on all public functions and methods
- **Docstrings** on all modules, classes, and public functions
- **No external dependencies** for the core SDK (stdlib only)
- Server features may depend on FastAPI, Jinja2, uvicorn (optional extras)
- Use `dataclasses` over Pydantic for data models

### Testing

- Every new feature needs tests
- Tests use pytest with temporary directories/databases
- Aim for the test to be self-contained (no external services)
- Run the full suite before submitting: `pytest -x -q`

### Commits

- Use clear, descriptive commit messages
- Prefix with the area: `tracing: add async context manager support`
- Keep commits focused — one logical change per commit

### Pull Requests

- Describe what you changed and why
- Include test coverage for new features
- Update the README if you're adding user-facing features
- Update CHANGELOG.md under `[Unreleased]`

## Design Principles

1. **Zero config to start** — `agentwatch.init("name")` and go
2. **SQLite-first** — no external dependencies for basic use
3. **Progressive complexity** — simple → advanced, never forced
4. **Agent-native** — designed for autonomous AI, not human-supervised ML
5. **Privacy-first** — all data local by default

## Adding a New Integration

1. Create a module in `src/agentwatch/integrations/`
2. Use the existing hooks (`trace()`, `log()`, `costs.record()`) — don't bypass storage
3. Add tests in `tests/`
4. Document in the README under "Integrations"
5. Add to `__init__.py` imports if it should be top-level

## Adding Model Pricing

Edit `src/agentwatch/costs.py` and add entries to the `MODEL_PRICING` dict:

```python
"provider/model-name": ModelPricing(
    input_per_1k=0.001,
    output_per_1k=0.002,
    provider="provider",
),
```

Include common aliases in `MODEL_ALIASES` if applicable.

## Questions?

Open an issue on GitHub. We're friendly.
