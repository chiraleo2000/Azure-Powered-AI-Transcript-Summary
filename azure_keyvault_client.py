"""
Azure Key Vault Client for Secure Secret Management
Handles retrieval of secrets from Azure Key Vault with fallback to environment variables
"""
import os
from typing import Optional
from azure.keyvault.secrets import SecretClient
from azure.identity import DefaultAzureCredential, ManagedIdentityCredential, AzureCliCredential, ChainedTokenCredential
import logging

logger = logging.getLogger(__name__)


class AzureKeyVaultClient:
    """Secure client for retrieving secrets from Azure Key Vault"""
    
    def __init__(self):
        """Initialize Key Vault client with proper authentication chain"""
        self.use_key_vault = os.getenv("USE_KEY_VAULT", "True").lower() == "true"
        self.key_vault_url = os.getenv("AZURE_KEY_VAULT_URL")
        self.client: Optional[SecretClient] = None
        
        if self.use_key_vault and self.key_vault_url:
            try:
                # Authentication chain for different environments:
                # 1. Managed Identity (for App Service/Azure resources)
                # 2. Azure CLI (for local development)
                # 3. Default (environment variables, etc.)
                credential = ChainedTokenCredential(
                    ManagedIdentityCredential(),
                    AzureCliCredential(),
                    DefaultAzureCredential()
                )
                
                self.client = SecretClient(vault_url=self.key_vault_url, credential=credential)
                logger.info(f"🔐 Key Vault client initialized: {self.key_vault_url}")
            except Exception as e:
                logger.warning(f"⚠️ Failed to initialize Key Vault client: {e}")
                logger.warning("Falling back to environment variables")
                self.client = None
        else:
            logger.info("🔓 Key Vault disabled, using environment variables")
    
    def get_secret(self, secret_name: str, fallback_env_var: Optional[str] = None) -> Optional[str]:
        """
        Retrieve a secret from Key Vault with fallback to environment variable
        
        Args:
            secret_name: Name of the secret in Key Vault (will be normalized)
            fallback_env_var: Environment variable name to use as fallback
            
        Returns:
            Secret value or None if not found
        """
        # Normalize secret name (Key Vault only allows alphanumerics and hyphens)
        normalized_name = secret_name.replace("_", "-").lower()
        
        # Try Key Vault first if enabled
        if self.client:
            try:
                secret = self.client.get_secret(normalized_name)
                logger.debug(f"✅ Retrieved secret from Key Vault: {normalized_name}")
                return secret.value
            except Exception as e:
                logger.warning(f"⚠️ Failed to retrieve '{normalized_name}' from Key Vault: {e}")
        
        # Fallback to environment variable
        env_var = fallback_env_var or secret_name
        value = os.getenv(env_var)
        
        if value:
            logger.debug(f"📋 Using environment variable: {env_var}")
        else:
            logger.error(f"❌ Secret not found: {secret_name} (env: {env_var})")
        
        return value
    
    def get_required_secret(self, secret_name: str, fallback_env_var: Optional[str] = None) -> str:
        """
        Retrieve a required secret, raise error if not found
        
        Args:
            secret_name: Name of the secret in Key Vault
            fallback_env_var: Environment variable name to use as fallback
            
        Returns:
            Secret value
            
        Raises:
            ValueError: If secret is not found
        """
        value = self.get_secret(secret_name, fallback_env_var)
        if not value:
            raise ValueError(f"Required secret not found: {secret_name}")
        return value


# Global Key Vault client instance
_kv_client: Optional[AzureKeyVaultClient] = None


def get_keyvault_client() -> AzureKeyVaultClient:
    """Get or create the global Key Vault client instance"""
    global _kv_client
    if _kv_client is None:
        _kv_client = AzureKeyVaultClient()
    return _kv_client


def get_secret_secure(secret_name: str, fallback_env_var: Optional[str] = None, required: bool = False) -> Optional[str]:
    """
    Convenience function to retrieve secrets securely
    
    Args:
        secret_name: Name of the secret in Key Vault
        fallback_env_var: Environment variable name to use as fallback
        required: If True, raise error if secret not found
        
    Returns:
        Secret value or None
    """
    client = get_keyvault_client()
    if required:
        return client.get_required_secret(secret_name, fallback_env_var)
    return client.get_secret(secret_name, fallback_env_var)
