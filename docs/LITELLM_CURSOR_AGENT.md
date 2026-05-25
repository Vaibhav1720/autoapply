# LiteLLM + Cursor Agent + AWS Bedrock (Qwen / DeepSeek / GLM)

Use this when **Cursor Agent / MCP / tool calling** against Bedrock models breaks with direct Bedrock integration. LiteLLM translates Cursor’s OpenAI-style agent payloads into valid Bedrock requests.

```
Cursor Agent → LiteLLM (OpenAI-compatible) → AWS Bedrock → Qwen / DeepSeek / GLM
```

## 1. AWS credentials (required for LiteLLM)

LiteLLM’s Bedrock provider uses **IAM access keys** via boto3 (`AKIA...` + secret), not the `ABSK...` bearer CSV alone.

Add to project `.env` (gitignored):

```bash
AWS_ACCESS_KEY_ID=AKIA...
AWS_SECRET_ACCESS_KEY=...
AWS_REGION_NAME=us-west-2
```

Enable model access in [Bedrock console](https://console.aws.amazon.com/bedrock/) for **us-west-2** (Qwen, DeepSeek, GLM).

## 2. Start the proxy

```bash
cd /Users/vaibhavbadguzar/Desktop/AutoApply
bash tools/litellm/start-proxy.sh
```

Runs at **http://localhost:4000** (proxy listens on all interfaces).

First run creates `tools/litellm/.venv` and installs `litellm[proxy]`.

## 3. Cursor settings

1. **Cursor Settings** (`Cmd + Shift + J`) → **Models**
2. **Add OpenAI-compatible model** (or override OpenAI base URL)
3. Set:

| Field | Value |
|--------|--------|
| **Base URL** | **`http://localhost:4000/v1`** (recommended). If Cursor rejects it, try `http://localhost:4000`. Avoid `http://127.0.0.1:4000` — some Cursor builds fail to connect. |
| **API key** | `sk-litellm-local` (matches `master_key` in `config.yaml`) |
| **Model** | `qwen-coder` (or `deepseek`, `glm`) |

4. In chat/composer, pick that model and use **Agent** mode.

## 4. Quick test (terminal)

```bash
curl -s http://localhost:4000/v1/chat/completions \
  -H "Authorization: Bearer sk-litellm-local" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen-coder",
    "messages": [{"role": "user", "content": "Say hi in one word."}]
  }'
```

## Config

Models are defined in [`tools/litellm/config.yaml`](../tools/litellm/config.yaml). The `bedrock/` prefix on `litellm_params.model` is **required**.

To change port: `LITELLM_PORT=4001 bash tools/litellm/start-proxy.sh`

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `Unable to locate credentials` | Set `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` |
| `AccessDeniedException` / model not found | Enable model in Bedrock **us-west-2**; verify model ID in console |
| Cursor still uses wrong model | Select custom model in picker; disable other providers for that session |
| Agent tools still fail | Confirm Base URL is LiteLLM, not OpenAI; restart proxy after config edits |

See also [`BEDROCK_CURSOR_SETUP.md`](BEDROCK_CURSOR_SETUP.md) for billing and Claude-specific notes.
