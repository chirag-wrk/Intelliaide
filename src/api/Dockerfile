FROM registry.access.redhat.com/ubi9/python-311

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY *.py ./
COPY core/ ./core/
COPY machine_learning/ ./machine_learning/
COPY config/ ./config/
COPY data_source/ ./data_source/
COPY utils/ ./utils/

ENV MUST_GATHER_EXTRACT_DIR=/tmp/must-gather
RUN mkdir -p /tmp/must-gather && chgrp -R 0 /tmp/must-gather && chmod -R g+rwX /tmp/must-gather

EXPOSE 8000
CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
