FROM registry.access.redhat.com/ubi9/python-311:latest

USER root
# UBI9 python-311 already includes curl-minimal, gnupg2, and ca-certificates.
RUN dnf install -y pigz && \
    tee /etc/yum.repos.d/google-cloud-sdk.repo <<'EOF'
[google-cloud-cli]
name=Google Cloud CLI
baseurl=https://packages.cloud.google.com/yum/repos/cloud-sdk-el9-x86_64
enabled=1
gpgcheck=1
repo_gpgcheck=0
gpgkey=https://packages.cloud.google.com/yum/doc/rpm-package-key.gpg
EOF
RUN dnf install -y google-cloud-cli && \
    dnf clean all && \
    rm -rf /var/cache/dnf

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
RUN mkdir -p /shared && chgrp -R 0 /shared && chmod -R g+rwX /shared
RUN chmod -R g+rwX /app && chgrp -R 0 /app

EXPOSE 8000
CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
