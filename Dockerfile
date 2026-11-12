FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /srv

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY alembic.ini start.sh ./
COPY alembic ./alembic
COPY app ./app
COPY public ./public

# Run as an unprivileged user.
RUN useradd --system --no-create-home remixfox
USER remixfox

# Render passes the port in $PORT; 8080 is the local default.
EXPOSE 8080
CMD ["sh", "start.sh"]
