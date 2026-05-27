FROM python:3.11-slim

WORKDIR /app

COPY backend/requirements.txt 2>/dev/null || echo "polars>=0.19.0\nfastapi>=0.110.0\nuvicorn[standard]>=0.27.0\npydantic>=2.0.0" > backend/requirements.txt

RUN pip install --no-cache-dir polars fastapi uvicorn[standard] pydantic

COPY backend/ .

RUN mkdir -p data input_data

ENV ADF_DATA_DIR="./data" \
    ADF_PORT=8080

EXPOSE 8080

# 首次启动时自动运行 Agent Swarm 分析
CMD ["sh", "-c", "python auto_data_flow.py && python app.py"]
