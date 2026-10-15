FROM python:3.12-slim
LABEL org.opencontainers.image.source=https://github.com/ScilifelabDataCentre/openllm-setup
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# --chmod: readable by the non-root user, whatever the local file permissions are
COPY --chmod=644 app.py .
USER 1000
EXPOSE 8000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
