FROM python:3.11-slim

WORKDIR /app

# Upgrade pip and install curl for healthchecks
RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir --upgrade pip

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Hugging Face Spaces defaults to 7860, and Back4App can override this variable.
ENV PORT=7860

EXPOSE $PORT

# Run the FastAPI app using Uvicorn
CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT}"]
