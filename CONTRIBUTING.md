# Contributing to Imbalance Trading Bot

Thank you for your interest in contributing!

## How to Contribute

### Reporting Bugs

1. Check if the issue already exists
2. Open a new issue with:
   - Clear title
   - Steps to reproduce
   - Expected vs actual behavior
   - Environment details (OS, Python version)

### Suggesting Features

1. Open a feature request issue
2. Describe the use case
3. Explain how it would work
4. Be open to discussion

### Pull Requests

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/your-feature`
3. Make your changes
4. Run tests if applicable
5. Commit with clear messages
6. Push to your fork
7. Submit a PR with description

## Development Setup

```bash
# Clone your fork
git clone https://github.com/YOUR_USERNAME/imbalance-bot.git
cd imbalance-bot

# Create virtual environment
python -m venv venv
source venv/bin/activate  # Linux/Mac
# or
venv\Scripts\activate  # Windows

# Install dependencies
pip install -r files/requirements.txt

# Copy environment template
cp .env.example .env  # Add your API keys

# Run the bot
python -m files.main
```

## Code Style

- Follow PEP 8
- Use type hints where helpful
- Keep functions focused and small
- Add docstrings for complex logic

## Testing

Run existing tests:

```bash
pytest tests/
```

## Questions?

Open an issue for questions about contributing.
