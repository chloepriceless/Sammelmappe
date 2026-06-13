from datetime import datetime, date, timezone
from sqlalchemy import String, Integer, Float, DateTime, Date, Text, ForeignKey, Boolean, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


class Setting(Base):
    """Key/value config — stores the bcrypt'd app password and other runtime config."""
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)


class Invoice(Base):
    __tablename__ = "invoices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # File
    filename: Mapped[str] = mapped_column(String, nullable=False)         # stored filename on disk
    original_name: Mapped[str] = mapped_column(String, nullable=False)
    mime: Mapped[str] = mapped_column(String, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    # Recognised data
    vendor: Mapped[str | None] = mapped_column(String, nullable=True)     # supplier / merchant
    amount: Mapped[float | None] = mapped_column(Float, nullable=True)    # gross total in EUR
    currency: Mapped[str] = mapped_column(String(3), default="EUR")
    invoice_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    invoice_number: Mapped[str | None] = mapped_column(String, nullable=True)

    category: Mapped[str | None] = mapped_column(String, nullable=True)   # Material / Handwerker / Sanitär / ...
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # § 35a EStG (Handwerkerbonus) — optional, user-entered. See app/section35a.py.
    labor_amount: Mapped[float | None] = mapped_column(Float, nullable=True)   # Arbeitskosten-Anteil in EUR
    payment_method: Mapped[str | None] = mapped_column(String, nullable=True)  # "transfer" | "cash" | None
    payment_date: Mapped[date | None] = mapped_column(Date, nullable=True)     # Zahlungsdatum (§ 11 Abflussprinzip)

    # Status
    status: Mapped[str] = mapped_column(String, default="open", index=True)  # open | submitted
    submission_id: Mapped[int | None] = mapped_column(ForeignKey("submissions.id"), nullable=True, index=True)

    # OCR meta
    ocr_engine: Mapped[str | None] = mapped_column(String, nullable=True)      # tesseract | claude | manual
    ocr_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    ocr_raw_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    manually_edited: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None), onupdate=lambda: datetime.now(timezone.utc).replace(tzinfo=None))

    submission: Mapped["Submission | None"] = relationship(back_populates="invoices")


class Submission(Base):
    """A bundle of invoices that was exported as ZIP and marked as 'eingereicht'."""
    __tablename__ = "submissions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    label: Mapped[str | None] = mapped_column(String, nullable=True)         # user note, e.g. "Tranche 3"
    total_amount: Mapped[float] = mapped_column(Float, nullable=False)
    invoice_count: Mapped[int] = mapped_column(Integer, nullable=False)
    zip_filename: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None), index=True)

    invoices: Mapped[list[Invoice]] = relationship(back_populates="submission")
