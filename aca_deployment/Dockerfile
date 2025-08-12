# aca_deployment/Dockerfile
FROM python:3.10-slim

# (Optional) if you really need ODBC drivers, uncomment the block below.
# They’re for Debian 12 (bookworm), which python:3.10-slim uses now.
# RUN apt-get update \
#   && apt-get install -y --no-install-recommends curl gnupg ca-certificates unixodbc-dev gcc g++ \
#   && curl https://packages.microsoft.com/keys/microsoft.asc | apt-key add - \
#   && curl https://packages.microsoft.com/config/debian/12/prod.list > /etc/apt/sources.list.d/mssql-release.list \
#   && apt-get update \
#   && ACCEPT_EULA=Y apt-get install -y msodbcsql18 \
#   && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy only requirements first for better layer caching
COPY mcp_server/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Now copy the app code
COPY mcp_server/ /app/

# Good defaults
ENV PYTHONUNBUFFERED=1
EXPOSE 8000

# Start the API
CMD ["uvicorn", "server:app", "--host=0.0.0.0", "--port=8000"]
