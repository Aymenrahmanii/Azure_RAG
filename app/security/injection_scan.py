"""Ingestion-time scan for prompt-injection payloads in documents about to be indexed.

A document that reaches the index is later pasted into prompts, so an uploaded file is an attack
surface. This scanner looks for the common shapes of an injected instruction: text addressed to an
AI system, attempts to override rules, markup that can exfiltrate data when rendered, and
characters that hide text from human reviewers. A flagged document is quarantined (not indexed)
for review; it is a cheap first filter, not a guarantee: the prompt-time defences still apply.

It is tuned to produce no findings on the real corpus (GDPR, AI Act, NIS2, DORA), which is checked
by a test whenever the corpus is present.
"""

import re
from dataclasses import dataclass

I = re.IGNORECASE  # noqa: E741 - short alias keeps the table below readable

RULES: list[tuple[str, re.Pattern]] = [
    (
        "override_instructions",
        re.compile(
            r"\b(ignore|disregard|forget|override)\b[^.\n]{0,30}\b(previous|prior|above|earlier|"
            r"system|safety|all)\b[^.\n]{0,30}\b(instructions?|rules?|prompts?|guidelines)\b",
            I,
        ),
    ),
    ("new_instructions", re.compile(r"\b(new|updated|revised) (system )?instructions?\s*:", I)),
    # "act as a competent authority" is ordinary legal language, so only "act as if" is flagged
    ("role_change", re.compile(r"\b(you are now|from now on,? you|act as if)\b", I)),
    (
        "addressed_to_ai",
        re.compile(
            r"\b(assistant|chatbot|llm|language model)\b[^.\n]{0,60}\b(must|shall|should|will)\b"
            r"[^.\n]{0,60}\b(answer|reply|respond|append|include|output|print|cite)\b",
            I,
        ),
    ),
    (
        "system_prompt_probe",
        re.compile(r"\b(system|developer) (prompt|message)\b|\bjailbreak\b", I),
    ),
    (
        "output_control",
        re.compile(r"\b(reply|respond|answer) (only )?with\b|\bwithout citing\b", I),
    ),
    (
        "exfiltration_markup",
        re.compile(
            r"!\[[^\]]*\]\(\s*https?://|<\s*(img|script|iframe|object|embed|form)\b|javascript:", I
        ),
    ),
]

# Characters that make text invisible or change how it reads to a human reviewer.
# Raw strings: `re` expands the \u escapes itself, so this file stays plain ASCII.
HIDDEN = re.compile(
    r"[\U000e0000-\U000e007f"  # Unicode "tag" characters: invisible ASCII smuggling
    r"\u202a-\u202e\u2066-\u2069"  # bidirectional overrides and isolates
    r"]"
)
ZERO_WIDTH = re.compile(r"[\u200b-\u200f\u2060\ufeff]")
ZERO_WIDTH_LIMIT = 3  # a stray BOM or two is normal; many are a hiding place


@dataclass(frozen=True)
class Finding:
    rule: str
    snippet: str


def scan_text(text: str) -> list[Finding]:
    findings = [
        Finding(name, m.group(0)[:120].replace("\n", " "))
        for name, pattern in RULES
        if (m := pattern.search(text))
    ]
    if hidden := HIDDEN.findall(text):
        findings.append(Finding("hidden_characters", f"{len(hidden)} invisible/bidi characters"))
    if len(ZERO_WIDTH.findall(text)) > ZERO_WIDTH_LIMIT:
        findings.append(Finding("zero_width_characters", "many zero-width characters"))
    return findings
