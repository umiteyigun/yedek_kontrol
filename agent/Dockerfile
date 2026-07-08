FROM python:3.11-alpine
WORKDIR /app
COPY agent/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY agent/agent ./agent
ENV PYTHONUNBUFFERED=1
CMD ["python", "-m", "agent.main"]
