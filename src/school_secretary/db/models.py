from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Course(Base):
    __tablename__ = "courses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_unit_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    code: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(String(255))
    term: Mapped[str] = mapped_column(String(64), default="Fall 2026")

    assignments: Mapped[list[Assignment]] = relationship(back_populates="course")
    announcements: Mapped[list[Announcement]] = relationship(back_populates="course")
    documents: Mapped[list[Document]] = relationship(back_populates="course")
    habit_events: Mapped[list[StudyHabitEvent]] = relationship(back_populates="course")


class Assignment(Base):
    __tablename__ = "assignments"
    __table_args__ = (UniqueConstraint("course_id", "d2l_id", name="uq_assignment_d2l"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id"), index=True)
    d2l_id: Mapped[str] = mapped_column(String(64), index=True)
    title: Mapped[str] = mapped_column(String(512))
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    instructions: Mapped[str] = mapped_column(Text, default="")
    assignment_type: Mapped[str] = mapped_column(String(32), default="other")
    planned: Mapped[bool] = mapped_column(Boolean, default=False)
    raw_path: Mapped[str] = mapped_column(String(512), default="")

    course: Mapped[Course] = relationship(back_populates="assignments")
    documents: Mapped[list[Document]] = relationship(back_populates="assignment")
    subtasks: Mapped[list[SubTask]] = relationship(
        back_populates="assignment", order_by="SubTask.sort_order"
    )


class Announcement(Base):
    __tablename__ = "announcements"
    __table_args__ = (UniqueConstraint("course_id", "d2l_id", name="uq_announcement_d2l"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id"), index=True)
    d2l_id: Mapped[str] = mapped_column(String(64), index=True)
    title: Mapped[str] = mapped_column(String(512))
    body: Mapped[str] = mapped_column(Text, default="")
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    category: Mapped[str] = mapped_column(String(32), default="untriaged")
    triaged: Mapped[bool] = mapped_column(Boolean, default=False)
    raw_path: Mapped[str] = mapped_column(String(512), default="")

    course: Mapped[Course] = relationship(back_populates="announcements")


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (UniqueConstraint("course_id", "filename", "content_hash", name="uq_doc_hash"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id"), index=True)
    assignment_id: Mapped[int | None] = mapped_column(ForeignKey("assignments.id"), nullable=True)
    filename: Mapped[str] = mapped_column(String(255))
    path: Mapped[str] = mapped_column(String(1024))
    doc_type: Mapped[str] = mapped_column(String(32), default="handout")
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    extracted_text: Mapped[str] = mapped_column(Text, default="")
    indexed: Mapped[bool] = mapped_column(Boolean, default=False)

    course: Mapped[Course] = relationship(back_populates="documents")
    assignment: Mapped[Assignment | None] = relationship(back_populates="documents")


class SubTask(Base):
    __tablename__ = "subtasks"
    __table_args__ = (UniqueConstraint("assignment_id", "sort_order", name="uq_subtask_order"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    assignment_id: Mapped[int] = mapped_column(ForeignKey("assignments.id"), index=True)
    title: Mapped[str] = mapped_column(String(512))
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    assignment: Mapped[Assignment] = relationship(back_populates="subtasks")


class StudyHabitEvent(Base):
    __tablename__ = "study_habit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    course_id: Mapped[int | None] = mapped_column(ForeignKey("courses.id"), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(32), default="study_session")
    minutes: Mapped[int] = mapped_column(Integer, default=0)
    notes: Mapped[str] = mapped_column(Text, default="")
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)

    course: Mapped[Course | None] = relationship(back_populates="habit_events")


class CalendarEvent(Base):
    __tablename__ = "calendar_events"
    __table_args__ = (UniqueConstraint("source_kind", "source_id", name="uq_cal_source"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_kind: Mapped[str] = mapped_column(String(32))  # assignment | subtask
    source_id: Mapped[int] = mapped_column(Integer, index=True)
    uid: Mapped[str] = mapped_column(String(255), unique=True)
    title: Mapped[str] = mapped_column(String(512))
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    transport: Mapped[str] = mapped_column(String(32), default="ics")  # ics | google
    google_event_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_error: Mapped[str] = mapped_column(Text, default="")
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
