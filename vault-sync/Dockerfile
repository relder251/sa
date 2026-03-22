FROM node:20-slim
RUN npm install -g @bitwarden/cli@2024.6.0
RUN apt-get update && apt-get install -y python3 python3-pip && rm -rf /var/lib/apt/lists/*
RUN pip3 install flask "sentry-sdk[flask]" --break-system-packages
WORKDIR /app
COPY app.py .
EXPOSE 8777
CMD ["python3", "app.py"]
