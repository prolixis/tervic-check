"""7.6 Safety: TV501–TV502."""

from __future__ import annotations

from collections.abc import Iterator

from tervic.analyze.detectors._util import enabled, output_text
from tervic.analyze.findings import Context, Detector, Finding, register
from tervic.core.redact import PII_TYPES, find_secrets, redacted_kinds


def _secret_kinds(text: str) -> list[str]:
    kinds = [k for k in redacted_kinds(text) if k not in PII_TYPES]
    kinds += [kind for kind, _ in find_secrets(text)]
    return sorted(set(kinds))


def _pii_kinds(text: str) -> list[str]:
    kinds = [k for k in redacted_kinds(text) if k in PII_TYPES]
    kinds += [kind for kind, _ in find_secrets(text, pii=True) if kind in PII_TYPES]
    return sorted(set(kinds))


@register
class SecretLeak(Detector):
    id = "TV501"
    name = "secret-leak"
    severity = "error"
    description = "A secret (API key, private key, JWT, bearer token, connection string) reached an LLM input or the final output."
    how_to_fix = "Keep credentials out of prompts and state. Load them only where the API call is made and never echo them."

    def run(self, ctx: Context) -> Iterator[Finding]:
        for call in ctx.run.llm_calls:
            owner = ctx.run.owner_step(call)
            if not enabled(ctx, self.id, owner):
                continue
            kinds = _secret_kinds(output_text(call.input_messages))
            if kinds:
                where = f"`{owner.name}`" if owner else "an LLM call"
                yield self.finding(
                    f"A secret ({', '.join(kinds)}) was sent to the model in {where}.",
                    step=owner,
                    evidence={"kinds": kinds, "model": call.model},
                )
        if enabled(ctx, self.id, None):
            kinds = _secret_kinds(output_text(ctx.run.effective_final_output))
            if kinds:
                yield self.finding(
                    f"The final output contains a secret ({', '.join(kinds)}).",
                    step=ctx.run.steps[-1] if ctx.run.steps else None,
                    evidence={"kinds": kinds},
                )


@register
class PiiInOutput(Detector):
    id = "TV502"
    name = "pii-in-output"
    severity = "warn"
    description = "The final output contains an email address, phone number or card-like number."
    how_to_fix = "Mask personal data before returning it, or disable TV502 if showing it is intended."

    def run(self, ctx: Context) -> Iterator[Finding]:
        if not enabled(ctx, self.id, None):
            return
        kinds = _pii_kinds(output_text(ctx.run.effective_final_output))
        if kinds:
            yield self.finding(
                f"The final output contains personal data ({', '.join(kinds)}).",
                step=ctx.run.steps[-1] if ctx.run.steps else None,
                evidence={"kinds": kinds},
            )
