FROM python:3.11-slim
WORKDIR /app
COPY pyproject.toml ./
RUN pip install --no-cache-dir . && pip install --no-cache-dir uvicorn
COPY voice_agent/ ./voice_agent/
COPY config.yaml ./
EXPOSE 11436
CMD ["uvicorn", "voice_agent.app:build", "--factory", "--host", "0.0.0.0", "--port", "11436"]
