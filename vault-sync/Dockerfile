FROM python:3.12-slim

# Install bw CLI standalone binary — no Node.js required.
# Pinned to 2025.12.1; 2026.x has a silent unlock regression in headless Docker.
RUN apt-get update && apt-get install -y --no-install-recommends curl unzip ca-certificates && \
    curl -fsSL \
      "https://github.com/bitwarden/clients/releases/download/cli-v2025.12.1/bw-linux-2025.12.1.zip" \
      -L -o /tmp/bw.zip && \
    unzip /tmp/bw.zip -d /tmp/bw_bin && \
    mv /tmp/bw_bin/bw /usr/local/bin/bw && \
    chmod +x /usr/local/bin/bw && \
    rm -rf /tmp/bw.zip /tmp/bw_bin && \
    apt-get remove -y unzip && \
    apt-get autoremove -y && \
    rm -rf /var/lib/apt/lists/*

# Python dependencies
COPY app/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

WORKDIR /app
COPY app/ .

EXPOSE 8777
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8777"]
