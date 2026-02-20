# OpenRouter Models Available to openclaw
> Use: tell the bot "switch to [model name]" or edit openclaw.json agents.defaults.model

## 🆓 FREE MODELS (no cost)
| Name | Model ID | Notes |
|------|----------|-------|
| Step-3.5 Flash | stepfun/step-3.5-flash:free | Fast general purpose |
| Solar Pro 3 | upstage/solar-pro-3:free | Good for reasoning |
| Trinity Large | arcee-ai/trinity-large-preview:free | Large preview model |
| Trinity Mini | arcee-ai/trinity-mini:free | Lightweight |
| Nemotron Nano 30B | nvidia/nemotron-3-nano-30b-a3b:free | NVIDIA efficient model |
| LFM 2.5 Thinking | liquid/lfm-2.5-1.2b-thinking:free | Small, thinking model |

## 💰 PAID — FRONTIER CHINESE MODELS (very cheap)
| Name | Model ID | Notes |
|------|----------|-------|
| **Kimi K2.5** ⭐ DEFAULT | moonshotai/kimi-k2.5 | Best all-rounder, mid-Feb 2026 buzz |
| Kimi K2 Thinking | moonshotai/kimi-k2-thinking | Kimi with extended thinking |
| GLM-5 | z-ai/glm-5 | Zhipu 744B flagship |
| GLM-4.7 Flash | z-ai/glm-4.7-flash | Faster, cheaper GLM |
| GLM-4.7 | z-ai/glm-4.7 | Balanced GLM |
| Qwen 3.5 Plus | qwen/qwen3.5-plus-02-15 | Latest Qwen (Feb 2026) |
| Qwen 3 Max Thinking | qwen/qwen3-max-thinking | Qwen with thinking mode |
| Qwen 3 Coder | qwen/qwen3-coder-next | Coding specialist |
| Qwen 3.5 397B | qwen/qwen3.5-397b-a17b | Massive Qwen variant |

## 💰 PAID — WESTERN FRONTIER
| Name | Model ID | Notes |
|------|----------|-------|
| DeepSeek V3.2 | deepseek/deepseek-v3.2 | Strong coder/reasoner |
| Claude Sonnet 4.6 | anthropic/claude-sonnet-4-6 | Latest Anthropic |
| Claude Opus 4.6 | anthropic/claude-opus-4-6 | Most capable Anthropic |
| GPT-4o | openai/gpt-4o | OpenAI flagship |

## How to Switch Models
Tell the bot: "use kimi thinking for this" or "switch to GLM-5" 
Or permanently: edit ~/.openclaw/openclaw.json → agents.defaults.model
Format: "openrouter/[model-id-above]"
