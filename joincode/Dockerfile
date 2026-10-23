FROM python:3.12-slim

WORKDIR /app
RUN pip install --no-cache-dir "fastapi>=0.115,<1" "uvicorn[standard]>=0.30,<1"
COPY app.py ./

EXPOSE 8787
# Reads the server log read-only at /data; see compose/run docs.
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8787"]
