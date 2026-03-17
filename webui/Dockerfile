FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# Run as uid 1000 to match n8n's node user — ensures files written to shared
# volumes (e.g. output/opportunities/pending) are writable by n8n.
RUN useradd -u 1000 -m appuser
USER appuser
EXPOSE 3000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "3000"]
