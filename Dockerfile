# Azure AI Meeting Summary Application v0.2.0
# Security-hardened image with Azure Key Vault integration
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies including ffmpeg
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libgl1 \
    libglib2.0-0 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code (only what's needed)
COPY app.py app_func.py backend.py ai_summary.py session_manager.py \
     file_processors.py image_extraction.py error_logger.py \
     azure_keyvault_client.py config.py ./
COPY src/ ./src/
COPY static/ ./static/

# Copy favicon if exists
COPY favicon.ico* ./

# ============================================================
# ENVIRONMENT VARIABLES (Non-secret only)
# All secrets are loaded from Azure Key Vault via Managed Identity
# ============================================================

# Gradio Server Configuration
ENV GRADIO_SERVER_NAME=0.0.0.0
ENV GRADIO_SERVER_PORT=7860

# Azure Key Vault Configuration (configured via App Service settings)
ENV USE_KEY_VAULT=True
# AZURE_KEY_VAULT_URL will be set by App Service

# General App Config (non-sensitive)
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONIOENCODING=utf-8
ENV DEBUG=False
ENV MAX_CONCURRENT_JOBS=5
ENV LOCAL_TESTING_MODE=False

# Create non-root user for security
RUN groupadd -r appuser && useradd -r -g appuser -d /app -s /sbin/nologin appuser \
    && mkdir -p /app/temp /app/static /app/database /app/local_storage \
    && chown -R appuser:appuser /app

# Switch to non-root user
USER appuser

# Expose the port
EXPOSE 7860

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=3 \
    CMD curl -f http://localhost:7860/ || exit 1

# Run the application
CMD ["python", "app.py"]
