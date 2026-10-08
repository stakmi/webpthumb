FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    WEBTHUMB_HOST=0.0.0.0 \
    WEBTHUMB_PORT=8080

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --no-cache-dir .

EXPOSE 8080

ENTRYPOINT ["webpthumb"]
CMD ["serve"]
