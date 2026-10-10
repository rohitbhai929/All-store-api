# ALL STORE SHOP BOT + Public Reseller API (Railway)

## Railway deploy
1. Upload `allstore.py`, `requirements.txt`, and `Procfile` to a GitHub repository.
2. Create a Railway project and deploy the GitHub repository.
3. Add the environment variables listed below in Railway → Variables.
4. Add a Railway Volume mounted at `/app/data` and set `DB_PATH=/app/data/store_bot.db` so the SQLite database persists between deployments.
5. Redeploy. The API server listens on Railway's `PORT` automatically and the Telegram bot starts in the same service.

## Required Railway Variables
- `BOT_TOKEN` — Telegram bot token. Create/rotate it with BotFather if it has ever been shared.
- `ADMIN_ID` is currently configured in the source as the existing admin ID; change it in code if needed.
- `PUBLIC_API_KEY` — generate a long random secret; send it as `X-API-Key` from the other bot/service.
- `PUBLIC_API_USER_ID` — Telegram numeric user ID whose existing wallet will pay for all purchases made through this API. That account must have opened the Telegram bot and have enough balance.
- `DB_PATH=/app/data/store_bot.db`
- `STORE_API_KEY` — upstream Bantibhaiya API key (the provider key, not the public API key).
- `STORE_MASTER_KEY` — upstream provider master header value if required by the provider.
- `GEMINI_API_KEY` — optional, for the bot's AI helper.

Do not share `PUBLIC_API_KEY`, `BOT_TOKEN`, `STORE_API_KEY`, or `STORE_MASTER_KEY` publicly. Because credentials appeared in an earlier source file, rotate them before production deployment.

## Endpoints
All `/api/v1/*` endpoints require `X-API-Key: <PUBLIC_API_KEY>`.

- `GET /health` — public health check.
- `GET /docs` — API documentation page.
- `GET /api/v1/products` — list products and PIDs.
- `GET /api/v1/products/{PID}/plans` — list plan IDs and plan durations for a product PID.
- `GET /api/v1/balance` — read the configured API wallet balance.
- `POST /api/v1/buy` — buy by plan ID, optionally validating the product PID.

Example purchase body:
```json
{
  "pid": "YOUR_PRODUCT_PID",
  "plan_id": 123,
  "request_id": "unique-reference-from-your-other-bot-001"
}
```

The response includes `order_id`, `status`, `pid`, `plan_id`, `amount`, remaining `balance`, and the delivered `key`. Always use a unique `request_id` for each new order and reuse the same ID only when retrying that same request.

## Wallet behaviour
The API uses the single account configured by `PUBLIC_API_USER_ID`; clients cannot submit another user's ID. The purchase uses the bot's existing plan pricing, consumes manual stock first, and calls the upstream provider when no manual key is available. Upstream failure returns an error and refunds the reserved wallet amount. Successful orders create a row in purchase history and send an admin notification.
