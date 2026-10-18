from sqlalchemy import BigInteger, CheckConstraint, Column, String, UniqueConstraint
from sqlalchemy.orm import relationship

from models.base import Base


class Device(Base):
    __tablename__ = "devices"

    id = Column(BigInteger, primary_key=True)
    identifier = Column(String(128), nullable=False)

    __table_args__ = (
        UniqueConstraint("identifier", name="devices_identifier_key"),
        CheckConstraint(
            "btrim(identifier) <> ''",
            name="ck_devices_identifier_not_blank",
        ),
    )

    transactions = relationship("PixTransaction", back_populates="device")
