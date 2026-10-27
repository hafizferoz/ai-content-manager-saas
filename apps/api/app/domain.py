import hashlib
import json

from app.models import BrandProfile


def fingerprint_brand(brand: BrandProfile | None) -> str:
    guidance = {
        "name": brand.name if brand else "",
        "value_proposition": brand.value_proposition if brand else "",
        "audience": brand.audience if brand else "",
        "tone": brand.tone if brand else "",
        "preferred_vocabulary": brand.preferred_vocabulary if brand else [],
        "prohibited_claims": brand.prohibited_claims if brand else [],
    }
    return hashlib.sha256(json.dumps(guidance, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
