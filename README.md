# AI Meeting Summary (Current)

Azure-powered transcription and AI summarization web application, built with Gradio and secured with Azure Key Vault.

Current app version in code: `0.1.34` (from `app.py`).

---

## What This Project Does

- Converts uploaded audio/video into transcript text with Azure Speech Services
- Generates structured AI summaries from transcript text and optional attached documents
- Supports account registration/login, session persistence, and password reset
- Stores operational data in Azure Blob Storage (blob-first architecture)
- Uses Azure Key Vault for secure secret retrieval in production

---

## Core Features

### Transcription

- Audio and video upload
- Multi-language speech recognition
- Optional speaker diarization
- Optional timestamps, punctuation mode, profanity mode
- Optional audio enhancement pipeline

### AI Summary

- Azure OpenAI-based summary generation
- Multiple summary formats (meeting, executive, external, learning, custom)
- Optional additional instruction prompt
- Optional supporting document uploads (`.pdf`, `.docx`, `.pptx`, `.xlsx`, `.txt`)

### User & Privacy

- User registration and login
- Consent collection (GDPR/data retention/marketing)
- Session management with inactivity timeout
- Export user data and delete account flows

### Storage & Operations

- Azure Blob containers for transcripts, summaries, user/auth, metadata
- Background cleanup job for old data
- Docker image hardened to run as non-root user

---

## Project Structure

```text
.
├── app.py
├── app_func.py
├── backend.py
├── ai_summary.py
├── config.py
├── azure_keyvault_client.py
├── session_manager.py
├── file_processors.py
├── image_extraction.py
├── audio_enhancer.py
├── error_logger.py
├── Dockerfile
├── requirements.txt
├── docker-deploy-azure.ps1
├── infrastructure/
│   ├── main.bicep
│   ├── main.parameters.json
│   ├── deploy.ps1
│   ├── set-keyvault-secrets.ps1
│   └── README.md
├── src/
│   ├── ui/
│   │   └── styles.py
│   └── utils/
│       └── file_helpers.py
├── static/
└── local_storage/
```

---

## Requirements

- Python `3.11+`
- FFmpeg installed and available in PATH
- Azure resources for:
  - Speech Service (at least one key: primary or backup)
  - Azure OpenAI
  - Azure Blob Storage
  - Computer Vision
  - Azure Key Vault (recommended for production)

---

## Local Development

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure environment

Create a `.env` file in the project root (this repository currently does not include `.env.example`).

Minimum required values for local run without Key Vault:

```env
USE_KEY_VAULT=False

# Speech: provide at least one key
AZURE_SPEECH_KEY=<speech-primary-key>
# AZURE_SPEECH_KEY_BACKUP=<speech-backup-key>
AZURE_SPEECH_KEY_ENDPOINT=<speech-endpoint>
AZURE_REGION=<speech-region>

# OpenAI
AZURE_OPENAI_ENDPOINT=<openai-endpoint>
AZURE_OPENAI_KEY=<openai-key>
AZURE_OPENAI_DEPLOYMENT=gpt-5.4-nano
AZURE_OPENAI_API_VERSION=2025-01-01-preview

# Blob Storage
AZURE_BLOB_CONNECTION=<storage-connection-string>
AZURE_STORAGE_ACCOUNT_NAME=<storage-account-name>
AZURE_CONTAINER=transcripts
CHAT_RESPONSES_CONTAINER=response-chats
USER_PASSWORD_CONTAINER=user-password
META_DATA_CONTAINER=meta-storage

# Computer Vision
COMPUTER_VISION_ENDPOINT=<computer-vision-endpoint>
COMPUTER_VISION_KEY=<computer-vision-key>
COMPUTER_VISION_REGION=southeastasia

# Security
PASSWORD_SALT=<strong-random-salt>
```

### 3. Run app

```bash
python app.py
```

Default URL: `http://localhost:7860`

---

## Running With Docker

Build:

```bash
docker build -t ai-meeting-summary:latest .
```

Run locally using `.env` and disable Key Vault mode explicitly:

```bash
docker run --rm -p 7860:7860 --env-file .env -e USE_KEY_VAULT=False ai-meeting-summary:latest
```

Notes:

- Dockerfile defaults `USE_KEY_VAULT=True` for cloud usage
- Container listens on port `7860`
- Image runs as non-root user

---

## Azure Deployment

### Option A: Full Infrastructure + App Deploy (Bicep + ACR Build)

From `infrastructure/`:

```powershell
.\deploy.ps1 -ResourceGroupName "ai-summary-prod-rg" -Location "southeastasia"
```

This script deploys infrastructure and can trigger ACR cloud build/push.

### Option B: Configure Key Vault Secrets

```powershell
.\set-keyvault-secrets.ps1 -KeyVaultName "ai-summary-prod-kv" -ResourceGroupName "ai-summary-prod-rg" -Interactive
```

Or import from local env file:

```powershell
.\set-keyvault-secrets.ps1 -KeyVaultName "ai-summary-prod-kv" -ResourceGroupName "ai-summary-prod-rg" -FromEnvFile -EnvFilePath ".env"
```

### Option C: Docker-to-App Service Deployment

From repository root:

```powershell
.\docker-deploy-azure.ps1 -ResourceGroup "AI-Summary-Internal" -AppServiceName "ai-summarize-service"
```

---

## Configuration Notes

- `USE_KEY_VAULT=True` and `AZURE_KEY_VAULT_URL=<vault-url>` enable secure secret loading
- If Key Vault access fails, the app falls back to environment variables
- At startup, config validation enforces critical keys/secrets
- Speech service requires at least one key (`AZURE_SPEECH_KEY` or `AZURE_SPEECH_KEY_BACKUP`)

---

## Tech Stack

- Python 3.11
- Gradio UI
- Azure Speech Services
- Azure OpenAI
- Azure Blob Storage
- Azure Key Vault + Managed Identity (production)
- Azure Computer Vision

---

## Troubleshooting

- Startup error about missing keys:
  - Verify `.env` values or Key Vault secrets
  - Ensure required secret names match expected names in `config.py`
- FFmpeg-related processing failures:
  - Ensure FFmpeg is installed and available on PATH
- Azure auth issues in cloud:
  - Confirm managed identity has access to Key Vault and service resources

---

## Related Docs

- Infrastructure guide: `infrastructure/README.md`
- Main app entrypoint: `app.py`
- Central configuration: `config.py`
