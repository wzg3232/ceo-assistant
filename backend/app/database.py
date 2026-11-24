"""CEO助手 - 数据库配置 (PostgreSQL)

审计数据由 feishu-approval-ai 服务写入（唯一写方），本服务只读。
连接参数可通过环境变量覆盖，默认指向本地 ceo-audit-postgres 容器 (127.0.0.1:5433)。
"""
import os

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.models import Base

# PostgreSQL 连接串: postgresql+psycopg2://user:password@host:port/dbname
# 连接参数建议通过环境变量 CEO_AUDIT_DB_URL 提供 (密码不硬编码, 参见 .env.example)。
# 默认值仅用于本地开发指向 ceo-audit-postgres 容器, 生产环境务必用环境变量覆盖。
DATABASE_URL = os.environ.get(
    "CEO_AUDIT_DB_URL",
    "postgresql+psycopg2://ceo_audit:CHANGE_ME@127.0.0.1:5433/ceo_audit",
)

# Postgres 原生支持多写并发, check_same_thread 仅对 SQLite 需要
engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def init_db():
    """初始化数据库（schema 属主为 feishu-approval-ai 侧, 此处幂等建表兜底）"""
    Base.metadata.create_all(bind=engine)


def get_db():
    """获取数据库会话（依赖注入）"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
