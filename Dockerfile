FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
  && apt-get install -y --no-install-recommends \
    ca-certificates \
    git \
    git-lfs \
    python3 \
    python3-pip \
    python3-venv \
  && rm -rf /var/lib/apt/lists/*

RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src

RUN python3 -m pip install --upgrade pip \
  && pip install .

RUN groupadd --gid 10001 brokk \
  && useradd --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin brokk \
  && mkdir -p /run \
  && chown -R brokk:brokk /run /app

USER brokk

ENTRYPOINT ["python3", "-m", "brokk.service"]

