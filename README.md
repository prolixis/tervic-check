# Tervic Check (v0.1)

Tervic Check is a lightweight static analysis CLI tool designed to scan automation workflows (like n8n JSON files) for structural errors, broken connections, and accidental hardcoded secrets.

## Usage

You can test the scanner locally against the provided sample workflow:

```bash
node bin/tervic-check.js examples/sample_workflow.json
```
