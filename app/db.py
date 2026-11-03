import os
from datetime import datetime,timezone
from sqlalchemy import create_engine,String,Text,Boolean,Float,DateTime
from sqlalchemy.orm import DeclarativeBase,Mapped,mapped_column,sessionmaker
url=os.getenv('DATABASE_URL','sqlite:///./kaki.db')
if url.startswith('postgres://'):url='postgresql+psycopg://'+url[len('postgres://'):]
if url.startswith('postgresql://'):url='postgresql+psycopg://'+url[len('postgresql://'):]
engine=create_engine(url,pool_pre_ping=True,connect_args={'check_same_thread':False} if url.startswith('sqlite') else {})
Session=sessionmaker(bind=engine)
class Base(DeclarativeBase):pass
class Email(Base):
    __tablename__='emails'
    id:Mapped[int]=mapped_column(primary_key=True)
    graph_id:Mapped[str]=mapped_column(String(512),unique=True)
    sender:Mapped[str]=mapped_column(String(300),default='')
    subject:Mapped[str]=mapped_column(String(500),default='')
    body:Mapped[str]=mapped_column(Text,default='')
    category:Mapped[str]=mapped_column(String(40),default='other')
    summary:Mapped[str]=mapped_column(Text,default='')
    draft:Mapped[str]=mapped_column(Text,default='')
    confidence:Mapped[float]=mapped_column(Float,default=0)
    source:Mapped[str]=mapped_column(String(30),default='')
    has_attachments:Mapped[bool]=mapped_column(Boolean,default=False)
    status:Mapped[str]=mapped_column(String(40),default='pending_approval')
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=lambda:datetime.now(timezone.utc))
    approved_by:Mapped[str|None]=mapped_column(String(200),nullable=True)
    sent_at:Mapped[datetime|None]=mapped_column(DateTime(timezone=True),nullable=True)
class Audit(Base):
    __tablename__='audit'
    id:Mapped[int]=mapped_column(primary_key=True)
    email_id:Mapped[int]=mapped_column()
    action:Mapped[str]=mapped_column(String(80))
    actor:Mapped[str]=mapped_column(String(200))
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=lambda:datetime.now(timezone.utc))
if os.getenv('KAKI_AUTO_CREATE_SCHEMA', 'true' if url.startswith('sqlite') else 'false').lower() == 'true':
    Base.metadata.create_all(engine)
