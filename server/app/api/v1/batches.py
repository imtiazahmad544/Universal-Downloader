"""Customer batch listing."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_customer_user
from app.core.database import get_db
from app.models.models import Batch, Customer, User
from app.schemas.schemas import BatchRead

router = APIRouter()


@router.get("/", response_model=list[BatchRead])
def list_batches(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    dep: tuple[User, Customer] = Depends(get_customer_user),
) -> list[Batch]:
    _user, customer = dep
    return (
        db.query(Batch)
        .filter(Batch.customer_id == customer.id)
        .order_by(Batch.batch_date.desc(), Batch.id.desc())
        .limit(limit)
        .offset(offset)
        .all()
    )
