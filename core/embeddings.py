"""OpenAI-compatible embeddings wrapper for LangChain/Chroma."""
from typing import List, Optional
from openai import OpenAI

class OpenAICompatibleEmbeddings:
    """
    Wrapper mínimo compatível com LangChain/Chroma para embeddings OpenAI.

    Mantém a dimensionalidade consistente com as coleções persistidas.
    """

    def __init__(self, api_key: str, model: str, dimensions: Optional[int] = None,
                 base_url: Optional[str] = None):
        if not api_key:
            raise ValueError("EMBEDDING_API_KEY/OpenRouter ou OPENAI_API_KEY e obrigatorio para embeddings vetoriais")

        client_kwargs = {"api_key": api_key}
        if base_url:
            client_kwargs["base_url"] = base_url

        self.client = OpenAI(**client_kwargs)
        self.model = model
        self.dimensions = dimensions

    def _embed(self, texts: List[str]) -> List[List[float]]:
        normalized = [(text or "").replace("\n", " ") for text in texts]

        request_args = {
            "model": self.model,
            "input": normalized,
        }

        if self.dimensions:
            request_args["dimensions"] = self.dimensions

        response = self.client.embeddings.create(**request_args)
        return [item.embedding for item in response.data]

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        return self._embed(texts)

    def embed_query(self, text: str) -> List[float]:
        return self._embed([text])[0]


