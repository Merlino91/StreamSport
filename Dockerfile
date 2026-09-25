FROM python:3.12-slim

WORKDIR /app

# Install system requirements
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    tzdata \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 7002

ENV STREAMSPORT_HOST=0.0.0.0
ENV STREAMSPORT_PORT=7002

CMD ["python", "run.py"]
