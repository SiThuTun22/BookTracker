from __future__ import annotations
from datetime import date
from typing import List,Optional,Any
from uuid import UUID,uuid4

from litestar import Litestar,get,post,put,Controller,patch
from litestar.di import Provide
from litestar.dto import DataclassDTO,DTOConfig
from litestar.openapi.config import OpenAPIConfig
from litestar.openapi.plugins import ScalarRenderPlugin
from litestar.plugins.sqlalchemy import (
    base,
    SQLAlchemyInitPlugin,
    SQLAlchemySyncConfig,
    repository,
    SQLAlchemyAsyncConfig,
    SQLAlchemyDTO,
)
from sqlalchemy import ForeignKey,select,func
from sqlalchemy.orm import DeclarativeBase,Mapped,mapped_column,relationship
from sqlalchemy.ext.asyncio import AsyncSession
from dotenv import load_dotenv
import os
from litestar.exceptions import ValidationException

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

#schema
class Book(base.UUIDBase):
    title: Mapped[str]
    author: Mapped[str]
    genre: Mapped[str]
    year: Mapped[int]

    logs: Mapped[List["ReadingLog"]] = relationship(back_populates="book",lazy="selectin")

class ReadingLog(base.UUIDBase):
    book_id: Mapped[UUID] = mapped_column(ForeignKey("book.id"))
    start_date: Mapped[date]
    book: Mapped["Book"] = relationship(back_populates="logs",lazy="selectin") 
    finish_date: Mapped[date | None]
    rating: Mapped[int | None] 
    notes: Mapped[str | None]


#Repo
class BookRepository(repository.SQLAlchemyAsyncRepository[Book]):
    model_type = Book

class LogRepository(repository.SQLAlchemyAsyncRepository[ReadingLog]):
    model_type = ReadingLog

#Dto
class BookWriteDTO(SQLAlchemyDTO[Book]):
    config = DTOConfig(
        exclude={"id", "logs"},  
    )

class BookReadDTO(SQLAlchemyDTO[Book]):
    pass

class LogWriteDTO(SQLAlchemyDTO[ReadingLog]):
    config = DTOConfig(exclude={"id","book","book_id"})

class LogReadDTO(SQLAlchemyDTO[ReadingLog]):
    config = DTOConfig(exclude={"book"})

#Dependencies injection
async def provide_book_repo(db_session: Any) -> BookRepository:
    return BookRepository(session=db_session)

async def provide_log_repo(db_session: Any) -> LogRepository:
    return LogRepository(session=db_session)

class BookController(Controller):
    path = "/books"
    dependencies = {"repo": Provide(provide_book_repo)}    
    return_dto = BookReadDTO

    @post(dto=BookWriteDTO)
    async def add_book(self,data: Book,repo: BookRepository) -> Book:
        res = await repo.add(data)
        await repo.session.commit()
        # This brings the data back into the object so Litestar can read it
        await repo.session.refresh(res) 
        return res

    @get()
    async def list_book(self, repo: BookRepository) -> List[Book]:
        return await repo.list()
    
class ReadingLogController(Controller):
    path = "/logs"
    dependencies = {"repo": Provide(provide_log_repo)}

    @post(dto=LogWriteDTO, return_dto=LogReadDTO)
    async def add_log_progress(self,data:ReadingLog,repo: LogRepository) -> ReadingLog:

        if data.finish_date and data.finish_date > date.today():
            raise ValidationException(detail="Finish date cannot be in the future.")
        
        if data.finish_date and data.finish_date < data.start_date:
            raise ValidationException(detail="Finish date cannot be earlier than the start date.")
        
        res = await repo.add(data)
        await repo.session.commit()
        await repo.session.refresh(res)
        return res
    
    
    @get("/{book_id:uuid}/stats",dto=None)
    async def get_book_stats(self,repo: LogRepository,book_id: UUID) -> dict[str,Any]:
        stmt = select(ReadingLog).where(ReadingLog.book_id==book_id).order_by(ReadingLog.start_date.desc())
        result = await repo.session.execute(stmt)
        latest_log = result.scalars().first()

        if not latest_log:
            return {"status":"Not Started","last_update":None}
        if latest_log.finish_date:
            return {"status":"Finished","last_update":latest_log.finish_date}
        
        return {"status":"Currently Reading","last_update":latest_log.start_date}


    @patch("/{log_id:uuid}",dto=LogWriteDTO,return_dto=LogReadDTO)
    async def update_log(self,data:ReadingLog,repo: LogRepository,log_id: UUID) -> ReadingLog:
        existing_log = await repo.get(log_id)

        updated_log = await repo.update(data,item_id=log_id)

        if updated_log.finish_date and updated_log.finish_date > date.today():
            raise ValidationException(detail="Finish date cannot be in the future.")
        
        if updated_log.finish_date and updated_log.finish_date < updated_log.start_date:
            raise ValidationException(detail="Finish date cannot be earlier than the start date.")
        
        await repo.session.commit()
        await repo.session.refresh(updated_log)
        return updated_log
    
db_config = SQLAlchemyAsyncConfig(
    connection_string=DATABASE_URL,
    # before_send_handler=None
)
async def on_startup() -> None:
    async with db_config.get_engine().begin() as conn:
        await conn.run_sync(base.UUIDBase.metadata.create_all)

app = Litestar(
    route_handlers=[BookController,ReadingLogController],
    debug=True,
    on_startup = [on_startup],
    plugins=[SQLAlchemyInitPlugin(db_config)],
    openapi_config=OpenAPIConfig(
        title="Book Tracker API",
        version="1.0.0",
        render_plugins=[ScalarRenderPlugin(path="/scalar")],
    )
)