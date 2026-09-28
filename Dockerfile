FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# System deps (curl needed for the Claude CLI installer)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python deps first (cached layer — rebuilds only when requirements change).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && rm -rf /root/.cache

# Non-root user; owns app data
RUN useradd --create-home --uid 1000 harvey \
    && mkdir -p /app/data \
    && chown -R harvey:harvey /app

# The image carries code, config, prompts and skills; nothing is bind-mounted
# in production (data lives in Supabase via PULSE_DATABASE_URL).
COPY --chown=harvey:harvey harvey/ harvey/
COPY --chown=harvey:harvey prompts/ prompts/
COPY --chown=harvey:harvey skills/ skills/
COPY --chown=harvey:harvey config/ config/
COPY --chown=harvey:harvey db/ db/
COPY --chown=harvey:harvey harvey.yaml .

USER harvey
ENV PATH="/home/harvey/.local/bin:${PATH}" \
    DISABLE_AUTOUPDATER=1

# Claude Code CLI, installed as the runtime user so ~/.local/bin is correct.
# Every Claude call shells out to it, so a failed install fails the build
# (pipefail catches a failed download too). In the container it
# authenticates with ANTHROPIC_API_KEY; there is no OAuth login to mount.
SHELL ["/bin/bash", "-o", "pipefail", "-c"]
RUN curl -fsSL https://claude.ai/install.sh | bash \
    && claude --version

# Healthchecks are per service in docker-compose.yml (dashboard: GET /healthz,
# worker: `python -m harvey health --worker`).
CMD ["python", "-m", "harvey", "run"]
