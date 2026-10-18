FROM python:3.11-slim

RUN useradd --create-home --uid 1000 user

USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PRESCREEN_DATA_DIR=/tmp/prescreen

WORKDIR $HOME/app

COPY --chown=user:user requirements.txt .
RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir -r requirements.txt

COPY --chown=user:user src ./src
COPY --chown=user:user frontend ./frontend
COPY --chown=user:user data/mock_sources ./data/mock_sources

EXPOSE 7860

CMD ["uvicorn", "src.api.app:app", "--host", "0.0.0.0", "--port", "7860", "--proxy-headers", "--forwarded-allow-ips", "*"]
