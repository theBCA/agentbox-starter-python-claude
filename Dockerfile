# slim, not alpine: the agent SDKs publish no musllinux wheels, so on
# Alpine pip falls back to source builds -- or, for claude-agent-sdk, to an
# sdist that omits the CLI it spawns and fails only at runtime.
FROM python:3.12-slim@sha256:09f7da3bc104798d0afb40bc08d23ab2da20a76130cec1f2ef170848f5d85217

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

EXPOSE 8080

# Custom apps run under gVisor, where every fresh interpreter pays real
# import overhead. A bare socket check and a generous timeout keep this
# from flapping a healthy app to "unhealthy". Copy this pattern.
HEALTHCHECK --interval=10s --timeout=10s --start-period=10s --retries=3 \
    CMD python -c "import socket; socket.create_connection(('127.0.0.1', 8080), timeout=8).close()" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
