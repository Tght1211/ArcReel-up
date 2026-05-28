"""短剧 pipeline 运行实例 ORM。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from lib.db.base import Base, UserOwnedMixin


class XiaoyunqueRun(UserOwnedMixin, Base):
    __tablename__ = "xiaoyunque_runs"

    id: Mapped[str] = mapped_column(String, primary_key=True)  # uuid4 hex
    project_name: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    # pending / parsing / designing / generating / composing / done / failed / cancelled
    model_variant: Mapped[str] = mapped_column(String, nullable=False, server_default="fast720p")
    visual_style: Mapped[str] = mapped_column(String, nullable=False)
    video_ratio: Mapped[str] = mapped_column(String, nullable=False, server_default="16:9")
    thread_id: Mapped[str | None] = mapped_column(String)
    assets_id: Mapped[str | None] = mapped_column(String)
    script_file_url: Mapped[str] = mapped_column(Text, nullable=False)
    state_json: Mapped[str] = mapped_column(Text, nullable=False, server_default="{}")
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
