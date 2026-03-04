# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2026-03-04

### Added

- **Web UI** - Complete web interface for the crypto trading agent
  - FastAPI-based server with Jinja2 templates
  - Dashboard (`/`) with agent status, active positions table, and screening
    results
  - Settings page (`/settings`) with form for all configuration options
  - WebSocket endpoint (`/ws/status`) for real-time dashboard updates (3-second
    heartbeat)
  - Agent control endpoints (`POST /agent/start`, `POST /agent/stop`) -
    placeholder implementation

- **Security Features**
  - Server-side API key masking - raw keys never exposed to templates
  - Eye toggle to reveal API keys via authenticated endpoint
    (`GET /settings/reveal`)
  - Surgical .env file updates preserving comments and unrelated keys

- **UI/UX**
  - Dark theme with Tailwind CSS (CDN - no build step)
  - HTMX for partial page updates and form submissions
  - Alpine.js for interactivity (CDN)
  - Paper Trading badge in navigation when enabled
  - Success toast notifications for settings saves
  - Field-level validation error display

### Configuration

- New settings available in UI:
  - Exchange: `CRYPTO_COM_API_KEY`, `CRYPTO_COM_API_SECRET`
  - AI Models: `OPENROUTER_API_KEY`, `ANTHROPIC_API_KEY`, `DEEPSEEK_MODEL`,
    `OPUS_MODEL`
  - Screening: `SCREENING_MIN_VOLUME_USD`, `SCREENING_MIN_IMBALANCE_PCT`,
    `SCREENING_INTERVAL_SECONDS`
  - Risk Management: `MAX_POSITION_SIZE_USD`, `MAX_OPEN_POSITIONS`,
    `PAPER_TRADING`

### Dependencies

- `fastapi` - Web framework
- `uvicorn` - ASGI server
- `python-multipart` - Form data parsing
- `pydantic-settings` - Configuration management

### How to Run

```bash
pip install fastapi uvicorn python-multipart pydantic-settings
python -m uvicorn ui.main:app --reload --port 8000
```

Then open http://localhost:8000 in your browser.

---

_Previous versions did not have a changelog._
