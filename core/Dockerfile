FROM python:3.11-alpine

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app

EXPOSE 8090
CMD ["uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "18090"]
