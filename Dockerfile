FROM python:3.12-slim

WORKDIR /app

COPY server/requirements.txt ./server-requirements.txt
RUN pip install --no-cache-dir -r server-requirements.txt

COPY trinity ./trinity
COPY server ./server

ENV TRINITY_DB=/data/trinity.db
VOLUME ["/data"]
EXPOSE 8080

# One worker keeps a single phone listener and one presence view.
CMD ["gunicorn", "--chdir", "/app", "--workers", "1", "--threads", "8", \
     "--bind", "0.0.0.0:8080", "--timeout", "300", "server.app:app"]
