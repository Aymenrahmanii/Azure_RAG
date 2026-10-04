"""Streamlit chat UI over the streaming API. Run: streamlit run ui/streamlit_app.py"""

import json
import os

import httpx
import streamlit as st

API_URL = os.environ.get("API_URL", "http://localhost:8000")

st.set_page_config(page_title="EU Regulatory Compliance Assistant", page_icon="⚖️")
st.title("EU Regulatory Compliance Assistant")
st.caption("GDPR, AI Act, NIS2, DORA. Answers are grounded in the cited passages only.")


def stream_chat(question: str, k: int):
    """Yield (event, data) pairs from the API's Server-Sent Events stream."""
    with httpx.stream(
        "POST", f"{API_URL}/chat", json={"question": question, "k": k}, timeout=120
    ) as resp:
        resp.raise_for_status()
        event = None
        for line in resp.iter_lines():
            if line.startswith("event: "):
                event = line[7:]
            elif line.startswith("data: "):
                yield event, json.loads(line[6:])


def show_sources(sources: list[dict]) -> None:
    with st.expander(f"Sources ({len(sources)})"):
        for s in sources:
            st.markdown(f"**[{s['rank']}] {s['source']} | {s['section']}: {s['title']}**")
            st.caption(s["text"][:600] + ("..." if len(s["text"]) > 600 else ""))


k = st.sidebar.slider("Passages retrieved (k)", 1, 10, 5)

if "history" not in st.session_state:
    st.session_state.history = []

for turn in st.session_state.history:
    with st.chat_message(turn["role"]):
        st.markdown(turn["content"])
        if turn.get("sources"):
            show_sources(turn["sources"])

if question := st.chat_input("Ask about GDPR, the AI Act, NIS2 or DORA"):
    st.session_state.history.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)

    with st.chat_message("assistant"):
        placeholder, text, sources = st.empty(), "", []
        try:
            for event, data in stream_chat(question, k):
                if event == "sources":
                    sources = data
                elif event == "token":
                    text += data
                    placeholder.markdown(text + "▌")
                elif event == "error":
                    text = data["message"]
            placeholder.markdown(text)
            if sources:
                show_sources(sources)
        except httpx.HTTPError as exc:
            text = f"Could not reach the API ({exc.__class__.__name__})."
            placeholder.error(text)
    st.session_state.history.append({"role": "assistant", "content": text, "sources": sources})
