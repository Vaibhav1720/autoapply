# AWS Bedrock + Cursor — fix guide

Last checked against account `295435085053`, region `us-east-1`.

## Root cause (current)

Bedrock API calls fail with:

```text
INVALID_PAYMENT_INSTRUMENT: A valid payment instrument must be provided.
```

This is an **AWS Billing** issue, not a Cursor bug. Until a valid payment method is on the account, **no Claude model will work** in Cursor or via API.

## Fix in AWS Console (required)

1. Sign in: https://console.aws.amazon.com/billing/
2. **Payment methods** → add a valid credit/debit card
3. Open https://console.aws.amazon.com/bedrock/ → **Model access** → enable:
   - Claude Sonnet 4.5
   - Claude Haiku 4.5 (optional)
4. Wait a few minutes, then re-test (see script below)

## Cursor settings (after billing is fixed)

| Step | Action |
|------|--------|
| 1 | `Cmd + Shift + J` → **Models** → **API Keys** → **AWS Bedrock** |
| 2 | Enter **new** IAM Access Key + Secret (rotate if ever shared) |
| 3 | **Models** → **Add custom model** → paste model ID below |
| 4 | Chat picker → select that custom model (not GPT/Gemini) |

**Do not** use the `ABSK...` CSV key in Cursor Bedrock fields — that is a bearer token. Cursor needs `AKIA...` + secret.

### Model IDs that work on this account (when billing is OK)

Use the `us.` prefix:

```text
us.anthropic.claude-sonnet-4-5-20250929-v1:0
us.anthropic.claude-haiku-4-5-20251001-v1:0
us.anthropic.claude-opus-4-5-20251101-v1:0
```

### Model IDs that fail (do not use in Cursor)

| Model ID | Reason |
|----------|--------|
| `anthropic.claude-3-5-sonnet-20241022-v2:0` | End of life |
| `anthropic.claude-3-7-sonnet-20250219-v1:0` | End of life |
| `anthropic.claude-sonnet-4-*` (no `us.` prefix) | Needs inference profile |
| `us.anthropic.claude-opus-4-6-v1` | Billing / marketplace |

## Test Bedrock from terminal

```bash
cd /path/to/AutoApply
set -a && source .env && set +a
python3 tools/test_bedrock.py
```

## Alternative: Claude Code extension

If Cursor Bedrock UI is hard to find, use bearer token via `~/.claude/settings.json` (see AWS docs). Project `.env` already has `AWS_BEARER_TOKEN_BEDROCK`.

## Agent mode with Qwen / DeepSeek / GLM (LiteLLM)

For **Cursor Agent**, **MCP**, and **tool calling** with non-Claude Bedrock models, use a local LiteLLM proxy instead of direct Bedrock in Cursor. See **[LITELLM_CURSOR_AGENT.md](LITELLM_CURSOR_AGENT.md)**.

## Security

- Rotate IAM keys if posted in chat
- Never commit `bedrock-long-term-api-key*.csv` or `.env`
