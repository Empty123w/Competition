# =====================================================================
# 空压站多机协同智能调度 Agent - 后端/MCP/Agent 容器镜像 (阶段 C)
# 构建:  docker build -f deployment/Dockerfile -t air-station-agent .
# 运行API: docker run -p 8000:8000 air-station-agent
# 运行MCP: docker run -p 8765:8765 air-station-agent python -m air_station.mcp_servers.server --http
# =====================================================================
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    AIRSTATION_DB_URL=sqlite:////app/artifacts/air_station.db

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src ./src
COPY configs ./configs
COPY data/设备档案.json ./data/设备档案.json
COPY data/指标释义.json ./data/指标释义.json
COPY data/processed ./data/processed
COPY artifacts/models ./artifacts/models
COPY artifacts/metrics ./artifacts/metrics
COPY deployment ./deployment

RUN pip install --no-cache-dir -e .

EXPOSE 8000 8765

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import httpx,os; httpx.get('http://127.0.0.1:8000/api/system/health', timeout=3).raise_for_status()" || exit 1

CMD ["air-station-api"]
