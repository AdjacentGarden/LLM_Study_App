FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .

ENV APP_DATA_DIR=/app/data
EXPOSE 8100
CMD ["uvicorn", "adaptive_learning.api.app:app", "--host", "0.0.0.0", "--port", "8100"]
