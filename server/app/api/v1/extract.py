"""POST /api/v1/extract — test a URL against the v2.0 ultra link extractor.

Customer-authenticated diagnostic endpoint: runs the ordered fallback chain
(ytdlp -> ytdlp_cookies -> opengraph -> oembed -> video_tag) and reports
which strategy hit. The customer's global cookies (if uploaded) are used for
the cookie-capable strategies. Never creates jobs; never stores anything.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_customer_user
from app.core.database import get_db
from app.models.models import Customer, User
from app.schemas.schemas import ExtractRequest, ExtractResponse
from app.services.cookies import decrypt_cookies
from app.services.extractor import ExtractionError, LinkExtractor, logger

router = APIRouter()


@router.post("", response_model=ExtractResponse)
def extract_link(
    body: ExtractRequest,
    db: Session = Depends(get_db),
    dep: tuple[User, Customer] = Depends(get_customer_user),
) -> ExtractResponse:
    _user, customer = dep
    cookies: str | None = None
    if customer.cookies_encrypted:
        try:
            cookies = decrypt_cookies(customer.cookies_encrypted)
        except ValueError:
            # Corrupt/undecryptable global cookies: test without them rather
            # than failing the diagnostic outright.
            logger.warning(
                "extract.cookies_undecryptable", customer_id=customer.id
            )
            cookies = None
    try:
        media = LinkExtractor().extract(body.url, cookies)
    except ExtractionError as exc:
        return ExtractResponse(
            ok=False,
            error=str(exc)[:500],
            captcha_required=exc.captcha_detected,
        )
    return ExtractResponse(
        ok=True,
        media_url=media.url,
        title=media.title,
        ext=media.ext,
        strategy=media.strategy_name,
    )
