from dataclasses import dataclass

from app.providers.base import LLMProvider, RetrievedChunk
from app.providers.openai_compat import ContentFiltered
from app.rag.prompts import PROMPTS
from app.rag.retrieval import Retriever

# Best prompt in experiments (docs/experiments.md): few-shot, balanced refusal rules.
SYSTEM_PROMPT = PROMPTS["fewshot"]


@dataclass
class Answer:
    text: str
    sources: list[RetrievedChunk]


def build_prompt(question: str, sources: list[RetrievedChunk]) -> str:
    context = "\n\n".join(f"[{i}] {s.chunk.text}" for i, s in enumerate(sources, 1))
    return f"Context:\n{context}\n\nQuestion: {question}"


class RAGPipeline:
    def __init__(
        self,
        retriever: Retriever,
        llm: LLMProvider | None = None,
        system_prompt: str = SYSTEM_PROMPT,
    ):
        self.retriever, self.llm, self.system_prompt = retriever, llm, system_prompt

    async def ask(self, question: str, k: int = 5) -> Answer:
        sources = self.retriever.retrieve(question, k)
        if self.llm is None:
            return Answer("(no LLM configured: showing retrieved passages only)", sources)
        try:
            text = await self.llm.generate(self.system_prompt, build_prompt(question, sources))
        except ContentFiltered:
            return Answer("Request blocked by the content safety filter.", sources)
        return Answer(text, sources)
