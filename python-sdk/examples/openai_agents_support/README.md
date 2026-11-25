# OpenAI Agents SDK: a tool error the agent ignores

`broken.py` has an order-lookup tool that catches a timeout and returns `{"error": "timeout"}` as
if it were data. The agent continues and confidently tells the customer the order shipped.

```bash
python examples/openai_agents_support/broken.py
tervic check          # exit 1: TV101 error-in-output + TV102 tool-error-ignored, root cause `lookup_order`

python examples/openai_agents_support/fixed.py
tervic check          # exit 0
```

Tervic is attached with `tervic.attach_openai_agents(replace_default=True)`, which registers a
trace processor and keeps traces local. The model replays recorded turns from `cassette.json`, so the
example runs offline with no API key.
