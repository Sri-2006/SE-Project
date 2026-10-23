FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements-lock.txt .
RUN pip install --no-cache-dir -r requirements-lock.txt
RUN useradd --create-home appuser && chown appuser:appuser /app
COPY --chown=appuser:appuser . .
USER appuser
EXPOSE 8000
CMD ["sh", "scripts/docker-start.sh"]

