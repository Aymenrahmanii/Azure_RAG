"""Download the regulation texts from the EU Publications Office into data/raw/ as plain text.

Usage: python scripts/download_corpus.py
"""

from pathlib import Path

import httpx
from bs4 import BeautifulSoup

# CELEX ids, fetched from the EU Publications Office API
# (EUR-Lex itself answers scripts with a 202 bot challenge)
SOURCES = {
    "gdpr": "32016R0679",
    "eu_ai_act": "32024R1689",
    "nis2": "32022L2555",
    "dora": "32022R2554",
}
URL = "https://publications.europa.eu/resource/celex/{celex}"
OUT = Path("data/raw")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    headers = {"Accept": "application/xhtml+xml", "Accept-Language": "eng"}
    with httpx.Client(headers=headers, follow_redirects=True, timeout=60) as client:
        for name, celex in SOURCES.items():
            resp = client.get(URL.format(celex=celex))
            if resp.status_code != 200 or len(resp.text) < 50_000:
                print(f"[skip] {name}: HTTP {resp.status_code}, {len(resp.text)} bytes")
                continue
            text = BeautifulSoup(resp.text, "html.parser").get_text("\n", strip=True)
            (OUT / f"{name}.txt").write_text(text, encoding="utf-8")
            print(f"[ok]   {name}: {len(text):,} chars")


if __name__ == "__main__":
    main()
