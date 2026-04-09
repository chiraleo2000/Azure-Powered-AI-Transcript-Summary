# 🎙️ AI Meeting Summary v0.2.0

> Azure-powered meeting transcription and AI summarization service with enterprise-grade security

A full-stack web application that converts audio/video recordings into text transcripts and generates intelligent meeting summaries using Azure AI services. Now with **Azure Key Vault integration** for secure secret management.

---

## 🏗️ Architecture Overview

```text
┌─────────────────────────────────────────────┐
│           Gradio Web UI (app.py)            │
│         Desktop-first responsive UI         │
├─────────────────────────────────────────────┤
│        UI Functions (app_func.py)           │
│   Auth, Transcription, AI Summary handlers  │
├──────────┬──────────┬───────────────────────┤
│ backend  │ ai_summary│  session_manager     │
│  .py     │   .py     │      .py             │
│ Storage  │ GPT-4.1   │  OAuth2-style        │
│ Auth     │ Summary   │  Sessions            │
│ STT/LLM  │ Engine    │  60min timeout       │
├──────────┴──────────┴───────────────────────┤
│       config.py + azure_keyvault_client.py  │
│         🔐 Secure Secret Management         │
├─────────────────────────────────────────────┤
│           Azure Cloud Services              │
│  Speech STT │ OpenAI │ Blob Storage │ Vision│
│  🔑 Azure Key Vault (Secret Management)     │
└─────────────────────────────────────────────┘
```

## 🔐 Security Features (v0.2.0)

### Enterprise-Grade Secret Management

- ✅ **Azure Key Vault Integration**: All secrets stored securely in Azure Key Vault
- ✅ **Managed Identity**: Passwordless authentication using Azure Managed Identity
- ✅ **Zero Hardcoded Secrets**: No secrets in code, config files, or containers
- ✅ **Automatic Secret Rotation**: Support for key rotation without downtime
- ✅ **Audit Logging**: Complete audit trail of secret access
- ✅ **RBAC**: Role-based access control with least privilege

### Infrastructure as Code

- 📄 **Bicep Templates**: Complete infrastructure deployment automation
- 🚀 **One-Click Deployment**: Deploy entire stack with single command
- 🔄 **Reproducible**: Consistent deployments across environments
- 📊 **Version Controlled**: Infrastructure changes tracked in Git

See [SECURITY.md](SECURITY.md) for detailed security documentation.

## ✨ Features

### 🎙️ Audio Transcription

- **Azure Speech-to-Text (STT)**: Standard transcription for all audio/video files up to 500MB
- **Audio conversion**: Automatic FFmpeg conversion to WAV for all formats
- **Speaker diarization**: Identify and label different speakers
- **Multi-language**: Thai, English, Chinese, Japanese, Korean, and 10+ more

### 🤖 AI Meeting Summary

- **GPT-4.1-mini** powered summarization with 128K context window
- Multiple summary formats:
  - 📋 Internal meeting reports
  - 📊 Executive summaries
  - 🤝 External meeting reports
  - 📚 Learning/seminar summaries
- Document upload support (PDF, DOCX, PPTX, XLSX, TXT)
- Multi-language output

### 🔐 Security & Auth

- User registration with PDPA/GDPR compliance
- Password hashing with salted SHA-256
- OAuth2-style session tickets (60-minute inactivity timeout)
- Session persistence across page refreshes via browser localStorage
- Non-root Docker user

### ☁️ Cloud Storage

- **Azure Blob Storage only** — no local database
- Automatic 30-day data cleanup
- Container-level SAS token authentication
- User data export and account deletion (PDPA compliance)

---

## 📁 Project Structure

```text
├── app.py                  # Main Gradio web interface
├── app_func.py             # UI event handlers and business logic
├── backend.py              # Backend: Auth, Storage, Transcription (Azure STT)
├── ai_summary.py           # AI summarization engine (GPT-4.1-mini)
├── session_manager.py      # OAuth2-style session management
├── file_processors.py      # Document text extraction (PDF, DOCX, PPTX, etc.)
├── image_extraction.py     # Video frame extraction and image analysis
├── src/
│   ├── ui/
│   │   └── styles.py       # CSS and JavaScript for Gradio UI
│   └── utils/
│       └── file_helpers.py  # File path and type utilities
├── static/                 # Static assets (logo, favicon)
├── requirements.txt        # Python dependencies
├── Dockerfile              # Security-hardened container image
├── .dockerignore           # Docker build exclusions
└── .env                    # Environment configuration (secrets)
```

---

## 🚀 Quick Start

### Prerequisites

- Python 3.11+
- FFmpeg installed (`apt-get install ffmpeg` or `choco install ffmpeg`)
- Azure account with:
  - Speech Services
  - OpenAI Service (GPT-4.1-mini)
  - Blob Storage
  - Computer Vision (optional, for image analysis)

### Local Development

```bash
# 1. Clone and install dependencies
pip install -r requirements.txt

# 2. Configure environment
cp .env.example .env
# Edit .env with your Azure credentials

# 3. Run the application
python app.py
# Opens at http://localhost:7860
```

### Docker

```bash
# Start locally with Docker Compose (.env is injected at runtime)
docker compose up --build -d

# Or run the image directly with your local .env
docker build -t ai-summary-meeting:0.1.24 .
docker run -p 7860:7860 --env-file .env -e USE_KEY_VAULT=False ai-summary-meeting:0.1.24
```

For local Docker, keep API keys in `.env`. The image does not copy `.env` during build; Docker Compose and `docker run --env-file .env` pass those values into the container at runtime.

---

## ⚙️ Environment Variables

All configuration is loaded from `.env` via `python-dotenv`. Key variables:

| Variable | Description |
| --- | --- |
| `AZURE_SPEECH_KEY` | Azure Speech Services API key |
| `AZURE_SPEECH_KEY_ENDPOINT` | Speech Services endpoint URL |
| `AZURE_REGION` | Azure region (e.g., `westus`) |
| `AZURE_OPENAI_ENDPOINT` | Azure OpenAI endpoint |
| `AZURE_OPENAI_KEY` | Azure OpenAI API key |
| `AZURE_OPENAI_DEPLOYMENT` | Model deployment name (e.g., `gpt-4.1-mini`) |
| `AZURE_BLOB_CONNECTION` | Azure Blob Storage connection string |
| `AZURE_STORAGE_ACCOUNT_NAME` | Storage account name |
| `AZURE_CONTAINER` | Main transcripts container |
| `CHAT_RESPONSES_CONTAINER` | AI summary responses container |
| `USER_PASSWORD_CONTAINER` | User credentials container |
| `META_DATA_CONTAINER` | Metadata storage container |

See `.env.example` for the complete list of configuration options. In production, secrets are automatically loaded from Azure Key Vault.

---

## 🚀 Deployment

### Deployment Prerequisites

- Azure subscription with appropriate permissions
- Azure CLI installed
- PowerShell 7+ (for deployment scripts)

### Option 1: Automated Infrastructure Deployment (Recommended)

Deploy complete infrastructure using Bicep:

```powershell
# Navigate to infrastructure directory
cd infrastructure

# Run deployment script
.\deploy.ps1 -ResourceGroupName "AI-Summary-Internal" -Location "southeastasia"

# Configure secrets in Key Vault
.\set-keyvault-secrets.ps1 -KeyVaultName "ai-summary-keyvault"
```

This deploys:

- App Service (with Managed Identity)
- Azure OpenAI (GPT-4.1 Mini)
- Speech Services (Primary + Backup)
- Computer Vision (OCR)
- Storage Account (with containers)
- Key Vault access policies

See [infrastructure/README.md](infrastructure/README.md) for detailed deployment documentation.

### Option 2: Manual Setup

1. **Create Azure Resources**:
   - App Service (Linux, Python 3.11)
   - Cognitive Services (OpenAI, Speech, Computer Vision)
   - Storage Account with containers
   - Key Vault

2. **Configure Managed Identity**:

   ```bash
   az webapp identity assign --name <app-name> --resource-group <rg-name>
   ```

3. **Grant Key Vault Access**:

   ```bash
   az keyvault set-policy --name <kv-name> \
     --object-id <managed-identity-id> \
     --secret-permissions get list
   ```

4. **Set Secrets**:

   ```bash
   az keyvault secret set --vault-name <kv-name> \
     --name "azure-openai-key" --value "<your-key>"
   ```

5. **Configure App Settings**:

   ```bash
   az webapp config appsettings set --name <app-name> \
     --settings USE_KEY_VAULT=True \
     AZURE_KEY_VAULT_URL=https://<kv-name>.vault.azure.net/
   ```

### Local Development Setup

```bash
# Clone repository
git clone <repo-url>
cd Azure_Powered_AI_SummaryV0.2

# Install dependencies
pip install -r requirements.txt

# Copy and configure environment
cp .env.example .env
nano .env  # Add your development secrets

# Run application
python app.py
```

For local development, set `USE_KEY_VAULT=False` in `.env` to use environment variables instead of Key Vault.

---

## 🐳 Docker Deployment

### Local Build & Run

```bash
docker build -t ai-summary-meeting:0.1.24 .
docker run -d -p 7860:7860 --name ai-summary ai-summary-meeting:0.1.24
```

### Azure Container Registry

```bash
# Tag and push
docker tag ai-summary-meeting:0.1.24 ocrservicecontainer-b5c7dsegfybsh9cm.azurecr.io/ai-summary-meeting:0.1.24
docker push ocrservicecontainer-b5c7dsegfybsh9cm.azurecr.io/ai-summary-meeting:0.1.24
```

### Security Notes

- ✅ Non-root user in container
- ✅ No secrets baked as ENV in Dockerfile (loaded from .env at runtime)
- ✅ Minimal base image (`python:3.11-slim`)
- ✅ Only required files copied (explicit COPY, not `*.py`)
- ⚠️ For production: use Azure Key Vault or `--env-file` instead of baking `.env`

---

## 📋 Changelog

### v0.2.0 (2026-02-18) - Security & Infrastructure Release

- 🔐 **Azure Key Vault Integration**: All secrets now managed securely in Key Vault
- 🏗️ **Infrastructure as Code**: Complete Bicep templates for automated deployment
- 🔑 **Managed Identity**: Passwordless authentication for all Azure services
- 📜 **Deployment Scripts**: PowerShell scripts for infrastructure and secret management
- 🛡️ **Security Hardening**:
  - Removed hardcoded secrets from codebase
  - Added `.env.example` with placeholders
  - Created comprehensive `.gitignore`
  - Added `SECURITY.md` documentation
- 🐛 **Code Quality Fixes**:
  - Fixed SonarLint issues (f-strings, unused variables)
  - Improved error handling
  - Added secure configuration module (`config.py`)
  - Created Key Vault client (`azure_keyvault_client.py`)
- 📚 **Documentation**:
  - Comprehensive deployment guides
  - Security best practices
  - Infrastructure documentation

### v0.1.24 (2026-02-10)

- 🧹 **Project cleanup**: Removed unused files, cache, duplicate modules
- 🔒 **Dockerfile security**: Removed hardcoded secrets, added non-root user
- 📁 **File consolidation**: Reduced from 15+ Python files to 7 core + 2 utility
- 🐛 **Code Quality Fixes**:

---

## 📞 Support

Developed by **BeTimes Solutions**

For issues or questions, contact the system administrator.
