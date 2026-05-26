# Broker & API keys

How to get credentials for **Talk to My Portfolio**. Store them only in `.env` and `accounts.json` (both gitignored).

## Quick map

| You want | Create keys at | `accounts.json` | `.env` (suffix = `"id"` in UPPERCASE) |
|----------|----------------|-----------------|----------------------------------------|
| One Zerodha login | [developers.kite.trade](https://developers.kite.trade/) | One row in `zerodha[]`, `"enabled": true` | `ZERODHA_API_KEY_PRIMARY`, `ZERODHA_API_SECRET_PRIMARY`, `ZERODHA_REDIRECT_URL_PRIMARY` |
| Second Zerodha login | **New** Kite app (other Zerodha user) | Second row, new `"id"` (e.g. `member2`) | `ZERODHA_API_KEY_MEMBER2`, … |
| Groww | [groww.in/trade-api](https://groww.in/trade-api) | Row in `groww[]`, `"enabled": true` | TOTP: `GROWW_TOTP_TOKEN_GROWW1` + `GROWW_TOTP_SECRET_GROWW1` **or** `GROWW_API_KEY_GROWW1` + `GROWW_API_SECRET_GROWW1` |
| Sarwa | — (no API) | Row in `sarwa[]` | None |
| Portfolio **Ask** | [platform.openai.com/api-keys](https://platform.openai.com/api-keys) | — | `OPENAI_API_KEY` |

**Redirect URL** (all Zerodha apps on one machine):  
`http://127.0.0.1:8000/auth/zerodha/callback`  
Must match `HUB_BASE_URL`, the Kite app settings, and `redirect_url` in JSON.

---

## Zerodha (Kite Connect)

Used for holdings, OAuth login, and optional live orders.

### Generate API key & secret

1. Log in to [Kite](https://kite.zerodha.com/) with the **same** Zerodha account you want to link.
2. Open [developers.kite.trade](https://developers.kite.trade/) and sign in with that account.
3. **Create new app** (repeat for each family member — each login needs its own app).
4. Set:
   - **Redirect URL:** `http://127.0.0.1:8000/auth/zerodha/callback`
   - **Permissions:** **Read** (minimum); **Order** only if `TRADING_ENABLED=true`
5. After approval, copy **API key** and **API secret** into `.env`:

```text
# If accounts.json has "id": "primary"
ZERODHA_API_KEY_PRIMARY=<api key>
ZERODHA_API_SECRET_PRIMARY=<api secret>
ZERODHA_REDIRECT_URL_PRIMARY=http://127.0.0.1:8000/auth/zerodha/callback
```

### Wire `accounts.json`

```json
{
  "id": "primary",
  "code": "AB",
  "label": "My Zerodha",
  "user_id": "YOUR_KITE_CLIENT_ID",
  "enabled": true,
  "redirect_url": "http://127.0.0.1:8000/auth/zerodha/callback"
}
```

`user_id` is the Kite **client ID** (shown in Kite profile after login).

### Connect in the app

1. `uvicorn main:app --reload --host 127.0.0.1 --port 8000`
2. Open [http://127.0.0.1:8000/portfolio](http://127.0.0.1:8000/portfolio)
3. Click **Connect Zerodha** for that account.
4. Tokens expire around **6:00 AM IST** daily — reconnect when prompted.

### Live orders (`TRADING_ENABLED=true`)

Order placement needs **developer-account** setup (not on the per-app “App details” screen).

#### Static IP (required for orders)

Zerodha whitelists IP at the **developer profile**, not on each app:

1. Log in to [developers.kite.trade](https://developers.kite.trade/).
2. Open **Profile** (top-right avatar / menu) — **not** the individual app page.
3. Find **IP Whitelist** (or “Static IP”).
4. Enter your **public** egress IP (the machine running `uvicorn`):
   ```bash
   curl -s https://api.ipify.org
   ```
5. Click **Update** and confirm the family-use declaration if shown.
6. Retry the order (reconnect Zerodha on `/portfolio` only if auth fails).

Notes:

- Applies to **all** Kite apps under that developer login (AB/RB/SB apps share one whitelist).
- Do **not** use `127.0.0.1` or LAN IPs — only the public IP Zerodha sees.
- VPN: whitelist the VPN egress IP, or disable VPN while placing orders.
- You can change the whitelist about **once per calendar week** (Zerodha rule).
- [Zerodha help: static IP](https://support.zerodha.com/category/trading-and-markets/general-kite/kite-api/articles/static-ip)

#### “Order” vs read-only

There is **no** “Order” checkbox on the app edit page in the current console. Trading access is tied to how the app was created (app type / product). If **Connect Zerodha** works and holdings load, your app is usually fine for orders once IP whitelist is set. If you still get permission errors after whitelisting IP, create a **new** app from the developer dashboard (trading-capable product) and update `.env` with the new key/secret.

Groww orders use [Groww Trade API](https://groww.in/trade-api) subscription + daily key approval (API-key mode); no Zerodha-style static IP.

#### After-market (off-hours) orders

When NSE is closed, Zerodha needs **AMO** (`variety=amo`), not a regular day order. The hub sends AMO automatically outside **9:15 AM–3:30 PM IST (Mon–Fri)**. Use **Limit** with a price (Market is rejected for AMO). Orders are blocked **1:00–5:30 AM IST** (Zerodha maintenance). See [AMO timings](https://support.zerodha.com/category/trading-and-markets/charts-and-orders/order/articles/auto-amo).

### Multiple Zerodha accounts

| Step | Action |
|------|--------|
| 1 | Add another object in `zerodha[]` with a **new** `"id"` and `"code"` |
| 2 | Create a **separate** Kite Connect app while logged in as that Zerodha user |
| 3 | Add `ZERODHA_API_KEY_<NEW_ID>`, `ZERODHA_API_SECRET_<NEW_ID>`, `ZERODHA_REDIRECT_URL_<NEW_ID>` to `.env` |
| 4 | Use the **same** redirect URL on every Kite app |

Example: `primary` + `member2` → two apps, two key pairs, one shared callback URL.

---

## Groww (Trade API)

Used for Groww equity/MF holdings.

### Subscribe

1. Log in at [groww.in](https://groww.in/).
2. Open [groww.in/trade-api](https://groww.in/trade-api) and complete subscription.

### Generate credentials

| Method | Where in Groww | `.env` (for `"id": "groww1"`) |
|--------|----------------|-------------------------------|
| **TOTP** (recommended) | Trade API → TOTP section | `GROWW_TOTP_TOKEN_GROWW1`, `GROWW_TOTP_SECRET_GROWW1` |
| **API key** | [groww.in/trade-api/api-keys](https://groww.in/trade-api/api-keys) | `GROWW_API_KEY_GROWW1`, `GROWW_API_SECRET_GROWW1` |

TOTP avoids daily approval on Groww’s side; API keys may require **Approve** each day on Groww.

### Wire `accounts.json`

```json
{
  "id": "groww1",
  "code": "HB",
  "label": "Groww",
  "enabled": true
}
```

### Verify

- Open `/portfolio` and check the Groww row in the broker strip.
- If auth fails: approve the key on Groww (API-key mode) or use **Refresh Groww**.

### Multiple Groww accounts

Add another `groww[]` row with a new `"id"` (e.g. `groww2`) and a second set of `GROWW_*_GROWW2` vars in `.env`.

---

## Sarwa (manual)

No retail API. Enable in `accounts.json` and use the weekly panel (optional screenshot import with `OPENAI_API_KEY`).

```json
{ "id": "sarwa", "code": "SW", "label": "Sarwa (USD)", "enabled": true }
```

---

## OpenAI (optional)

| Use | Variable |
|-----|----------|
| Portfolio **Ask** | `OPENAI_API_KEY` |
| Sarwa screenshot | `OPENAI_API_KEY` |
| Buy thesis / sector on refresh | `BUY_THESIS_*`, `SECTOR_LLM_*` (off by default) |

Get a key: [platform.openai.com/api-keys](https://platform.openai.com/api-keys). Set billing limits on the OpenAI dashboard.

---

## Checklist

- [ ] `accounts.json` from `accounts.example.json` — each broker you use has `"enabled": true`
- [ ] `.env` from `.env-example` — every enabled `"id"` has matching `ZERODHA_*` or `GROWW_*` vars
- [ ] Kite redirect URL = `HUB_BASE_URL` host/port + `/auth/zerodha/callback`
- [ ] Connected Zerodha once per account on `/portfolio`
- [ ] `.env` and `accounts.json` not committed

## Troubleshooting

| Issue | Fix |
|--------|-----|
| `Missing credentials for AB` | `"id"` in JSON must match `.env` suffix (`primary` → `PRIMARY`, not a different name) |
| `Token exchange failed` | Wrong API secret or redirect URL mismatch on Kite app |
| Redirect to wrong port | Use `127.0.0.1` vs `localhost` consistently |
| Groww 401 | Re-approve API key or switch to TOTP |
| Empty portfolio after connect | **Refresh** on `/portfolio` or `?refresh=1` |
| `No IPs configured for this app` (Zerodha order) | [developers.kite.trade](https://developers.kite.trade/) → **Profile** (top-right) → **IP Whitelist** = output of `curl -s https://api.ipify.org` (not on the app details page) |
| `IP address(es) already linked to another account` | That public IP is whitelisted on a **different** Kite developer login. Remove it from the other account’s Profile → IP Whitelist, or trade only from the account that already owns the IP, or use a different egress IP (office/VPS). Family members should use **one** developer account with multiple apps — not separate developer logins sharing the same home IP. |
