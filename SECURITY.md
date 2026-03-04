# Security Policy

## Supported Versions

Only the latest release is supported with security updates.

## Reporting a Vulnerability

If you discover a security vulnerability, please report it responsibly.

### Do NOT

- Create a public GitHub issue
- Disclose vulnerability details publicly
- Attempt to exploit the vulnerability yourself

### DO

1. **Private disclosure**: Email the maintainer directly or use GitHub's private
   vulnerability reporting
2. **Provide details**: Include:
   - Description of the vulnerability
   - Steps to reproduce
   - Potential impact
   - Any suggested fixes (optional)
3. **Allow time**: Give maintainers reasonable time to address the issue before
   any public disclosure

## Security Considerations

This project involves:

- **API Keys**: Requires API keys for exchanges and AI services. Never commit
  these to version control.
- **Financial transactions**: Can execute real trades. **Always use paper
  trading mode for testing.**
- **Data storage**: Stores trade history and user data. Keep your environment
  secure.

## Best Practices

- Use separate API keys with minimal permissions for trading
- Enable paper trading by default
- Review the code before running with real funds
- Keep your API keys and credentials secure
- Use environment variables, never hardcode secrets
