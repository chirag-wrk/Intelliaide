FROM registry.access.redhat.com/ubi9/python-311:latest

USER root

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY *.py ./
COPY core/ ./core/
COPY machine_learning/ ./machine_learning/
COPY config/ ./config/
COPY data_source/ ./data_source/
COPY utils/ ./utils/
COPY LICENSE /licenses/

ENV MUST_GATHER_EXTRACT_DIR=/tmp/must-gather
RUN mkdir -p /tmp/must-gather && chgrp -R 0 /tmp/must-gather /licenses && chmod -R g+rwX /tmp/must-gather /licenses
RUN mkdir -p /shared && chgrp -R 0 /shared && chmod -R g+rwX /shared
RUN chmod -R g+rwX /app && chgrp -R 0 /app

EXPOSE 8000
CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
