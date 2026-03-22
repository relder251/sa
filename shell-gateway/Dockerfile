FROM docker:cli AS docker-cli

FROM python:3.12-slim

# util-linux provides nsenter (for VPS host shell)
# docker CLI binary copied from official docker:cli image
RUN apt-get update -qq && \
    apt-get install -y --no-install-recommends \
      util-linux \
    && rm -rf /var/lib/apt/lists/*

COPY --from=docker-cli /usr/local/bin/docker /usr/local/bin/docker

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py .

EXPOSE 7681
CMD ["python", "app.py"]
