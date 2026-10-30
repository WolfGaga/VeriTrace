FROM python:3.11-slim
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY veritrace ./veritrace
RUN pip install --no-cache-dir ".[documents]"
ENV PYTHONUNBUFFERED=1
EXPOSE 8002
CMD ["python", "-m", "veritrace.app", "--host", "0.0.0.0", "--port", "8002"]
