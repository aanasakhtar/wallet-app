FROM python:3.12-slim

WORKDIR /code

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY grpc_service ./grpc_service
COPY generated ./generated
COPY proto ./proto
COPY clients ./clients
COPY scripts ./scripts
COPY openapi ./openapi
COPY cardinality_demo.py ./cardinality_demo.py

EXPOSE 8000 50051 8002

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
