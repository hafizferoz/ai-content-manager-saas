# AI Provider Setup

## Workspace BYOK setup

1. Configure a dedicated Fernet encryption key as `AI_ENCRYPTION_KEY` in the
   API service environment. Generate a key with:

   ```powershell
   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```

   Store it in the deployment secret manager. Do not reuse the social-token
   encryption key, JWT secret, or an LLM API key. Back it up securely; losing
   it makes stored workspace credentials undecryptable.
2. Restart/redeploy the API so it receives the key.
3. In the workspace, open **AI Providers**, enter an API key and a model ID
   from that provider's account/catalog, then save.
4. Use **Discover models** and **Test connection** to check the provider. A
   listed model is not a guarantee of JSON-output support or account access.
5. Enable providers and assign priorities. Priority `0` is attempted first.

Supported workspace providers are Google Gemini, Groq, OpenRouter, Mistral,
and OpenAI. OpenAI-compatible custom endpoints remain available through the
existing server environment configuration.

## Platform environment defaults

The API also accepts `LLM_PROVIDER`, `LLM_MODEL`, `LLM_API_KEY`, and
`LLM_BASE_URL` for the existing default configuration. Provider-specific
environment variables (`GEMINI_*`, `GROQ_*`, `OPENROUTER_*`, `MISTRAL_*`,
and `OPENAI_*`) can select platform-level defaults. Do not commit populated
secrets; use `.env.example` as a template.

Customer credentials are organization-scoped and take precedence over the
platform default when configured. Platform credentials are not silently
added to a customer's fallback chain.

Free-tier access, billing, model availability, and usage limits are provider
and account dependent. Check each provider's current documentation and
account dashboard; the application does not promise that a model is free.
