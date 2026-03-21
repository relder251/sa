FROM python:3.12-slim

# util-linux provides nsenter (for VPS host shell)
# docker-cli provides docker exec (for container shells)
RUN apt-get update -qq && \
    apt-get install -y --no-install-recommends \
      util-linux \
      docker.io \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py .

EXPOSE 7681
CMD ["python", "app.py"]
