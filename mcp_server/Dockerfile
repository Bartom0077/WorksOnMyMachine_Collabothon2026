# Amalfi data MCP server. Build context = repository root, because get_balance_forecast imports the
# KPI engine from backend/kpi_engine (single implementation of the forecast model):
#   docker build -f mcp_server/Dockerfile .
FROM registry.access.redhat.com/ubi9/python-312:latest

WORKDIR /opt/app-root/src
COPY mcp_server/requirements.txt mcp_server/requirements.txt
RUN pip install --no-cache-dir -r mcp_server/requirements.txt

COPY backend/kpi_engine backend/kpi_engine
COPY mcp_server mcp_server

ENV PYTHONPATH=/opt/app-root/src/backend \
    PYTHONUNBUFFERED=1 \
    MCP_PORT=8080
WORKDIR /opt/app-root/src/mcp_server
EXPOSE 8080
USER 1001
CMD ["python", "app.py"]
