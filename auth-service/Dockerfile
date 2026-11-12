FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app/ ./app/
# Сервис работает не от root; каталог /keys — том для закрытого ключа JWT
RUN useradd --create-home --uid 10001 appuser && mkdir /keys && chown appuser /keys
USER appuser
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
