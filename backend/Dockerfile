FROM python:3.12-slim

# Install Node.js 20
RUN apt-get update && apt-get install -y curl && \
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y nodejs && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Node deps (docx package)
COPY package.json .
RUN npm install

# Copy application code
COPY . .

RUN mkdir -p output

CMD uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}
