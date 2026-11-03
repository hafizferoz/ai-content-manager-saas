# AI Credential Security

- BYOK keys are stored as Fernet ciphertext in the organization-scoped
  `ai_provider_configurations` table.
- `AI_ENCRYPTION_KEY` is a dedicated secret. It must not be stored in the
  database or reused from social OAuth/JWT encryption.
- Only workspace owners/admins can add, test, or remove provider
  configurations. Server-side membership and role checks are authoritative.
- API responses disclose configuration presence, never the key or ciphertext.
- Provider response bodies are not persisted or copied into user-facing errors.
- Keys are submitted to the API, never directly from browser code to providers.
- Generated text remains untrusted draft content; human approval is still
  required before any external publication.

Production deployments must use HTTPS and an external secrets manager,
restrict access to `AI_ENCRYPTION_KEY`, and plan secure backup and key
rotation. A key rotation workflow, provider-key expiry monitoring, and
credential versioning are not yet implemented.
