# Groww Trade API integration

Groww holdings are loaded via the official **growwapi** Python SDK (TOTP or API key + secret). Account metadata lives in `accounts.json`; credentials in `.env`.

See [README.md](../README.md) for setup.

## Prerequisites

- Groww Trade API subscription
- Keys in `.env`: `GROWW_API_KEY_<ID>` + `GROWW_API_SECRET_<ID>` or TOTP pair
- Account row in `accounts.json` with `"enabled": true`

## Flow

1. `modules/portfolio/auth/groww.py` — session / token storage in SQLite  
2. `modules/portfolio/services/groww_portfolio.py` — fetch holdings  
3. Merged into family view in `modules/portfolio/services/portfolio.py`

## Troubleshooting

- **401 / auth**: Re-approve API key on Groww or switch to TOTP  
- **Empty holdings**: Verify account enabled in `accounts.json` and `POST /api/portfolio/groww/refresh`
