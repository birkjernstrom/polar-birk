from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select

from polar.kit.repository import RepositoryBase, RepositoryIDMixin
from polar.models import PlainMessage, PlainThread


class PlainThreadRepository(
    RepositoryBase[PlainThread],
    RepositoryIDMixin[PlainThread, UUID],
):
    model = PlainThread

    async def get_by_plain_id(self, plain_id: str) -> PlainThread | None:
        statement = self.get_base_statement().where(PlainThread.plain_id == plain_id)
        return await self.get_one_or_none(statement)

    async def get_by_customer_email(self, email: str) -> Sequence[PlainThread]:
        statement = (
            self.get_base_statement()
            .where(PlainThread.customer_email == email)
            .order_by(PlainThread.created_at.desc())
        )
        return await self.get_all(statement)

    async def get_by_organization_id(
        self, organization_id: UUID
    ) -> Sequence[PlainThread]:
        statement = (
            self.get_base_statement()
            .where(PlainThread.organization_id == organization_id)
            .order_by(PlainThread.created_at.desc())
        )
        return await self.get_all(statement)

    async def get_by_user_id(self, user_id: UUID) -> Sequence[PlainThread]:
        statement = (
            self.get_base_statement()
            .where(PlainThread.user_id == user_id)
            .order_by(PlainThread.created_at.desc())
        )
        return await self.get_all(statement)

    async def get_by_customer_id(self, customer_id: UUID) -> Sequence[PlainThread]:
        statement = (
            self.get_base_statement()
            .where(PlainThread.customer_id == customer_id)
            .order_by(PlainThread.created_at.desc())
        )
        return await self.get_all(statement)


class PlainMessageRepository(
    RepositoryBase[PlainMessage],
    RepositoryIDMixin[PlainMessage, UUID],
):
    model = PlainMessage

    async def get_by_plain_id(self, plain_id: str) -> PlainMessage | None:
        statement = self.get_base_statement().where(PlainMessage.plain_id == plain_id)
        return await self.get_one_or_none(statement)

    async def get_by_thread_id(self, thread_id: UUID) -> Sequence[PlainMessage]:
        statement = (
            self.get_base_statement()
            .where(PlainMessage.thread_id == thread_id)
            .order_by(PlainMessage.sent_at.asc())
        )
        return await self.get_all(statement)

    async def get_by_thread_plain_id(self, thread_plain_id: str) -> Sequence[PlainMessage]:
        statement = (
            select(PlainMessage)
            .join(PlainThread, PlainMessage.thread_id == PlainThread.id)
            .where(PlainThread.plain_id == thread_plain_id)
            .order_by(PlainMessage.sent_at.asc())
        )
        result = await self.session.execute(statement)
        return result.scalars().all()
