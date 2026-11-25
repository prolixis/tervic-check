# CrewAI: an agent stuck in a tool loop

`broken.py` runs a researcher whose model keeps asking for `web_search` with the same query until
CrewAI's iteration limit forces an answer. The crew "completes", but it burned every iteration
without making progress.

```bash
python examples/crewai_research/broken.py
tervic check          # exit 1: TV401 loop, root cause the `research` task

python examples/crewai_research/fixed.py
tervic check          # exit 0
```

Tervic is attached with `tervic.attach_crewai()`. The model replays recorded replies from
`cassette.json`, so the example runs offline. CrewAI's interactive tracing prompt is turned off with
`CREWAI_TRACING_ENABLED=false`.
