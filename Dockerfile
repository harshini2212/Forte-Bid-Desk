# Container for the full live app (FastAPI UI + streaming API + the Bid Desk engine).
# Railway / Cloud Run:  docker build -t forte-bid-desk . && docker run -p 8080:8080 forte-bid-desk
# (Render uses render.yaml instead — `python serve.py`.)
FROM python:3.11-slim
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
# src/ layout (same as serve.py); RUN_PACE drips the SSE stream so the hosted demo
# feels live (set 0 to run at full speed). Precompute so the first load is instant.
ENV PYTHONPATH=/app/src PORT=8080 RUN_PACE=0.28
RUN python serve.py --precompute
EXPOSE 8080
CMD ["sh", "-c", "uvicorn forte.app:app --host 0.0.0.0 --port ${PORT}"]
