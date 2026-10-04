FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /srv

# Dependencies first: this layer is cached until pyproject.toml changes.
# Base deps only: no PyTorch / Chroma (the `local` extra), so the image stays small.
COPY pyproject.toml ./
RUN mkdir app && touch app/__init__.py && pip install . && pip uninstall -y azure-rag
COPY app ./app

RUN useradd --no-create-home app
USER app
EXPOSE 8000
CMD ["uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
