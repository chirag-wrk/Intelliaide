FROM python:3.14-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl gnupg ca-certificates pigz fonts-dejavu-core && \
    curl -fsSL https://packages.cloud.google.com/apt/doc/apt-key.gpg \
      | gpg --dearmor -o /usr/share/keyrings/cloud.google.gpg && \
    echo "deb [signed-by=/usr/share/keyrings/cloud.google.gpg] https://packages.cloud.google.com/apt cloud-sdk main" \
      > /etc/apt/sources.list.d/google-cloud-sdk.list && \
    apt-get update && apt-get install -y --no-install-recommends google-cloud-cli && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY api.py app_paths.py worker.py job_runner.py ./
COPY core/ ./core/
COPY machine_learning/ ./machine_learning/
COPY config/ ./config/
COPY data_source/ ./data_source/
COPY utils/ ./utils/

ENV MUST_GATHER_EXTRACT_DIR=/tmp/must-gather
RUN mkdir -p /tmp/must-gather

RUN mkdir -p /tmp/must-gather && chgrp -R 0 /tmp/must-gather && chmod -R g+rwX /tmp/must-gather
RUN mkdir -p /shared && chgrp -R 0 /shared && chmod -R g+rwX /shared
RUN chmod -R g+rwX /app && \
    chgrp -R 0 /app

EXPOSE 8000
CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]