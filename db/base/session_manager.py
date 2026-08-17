"""数据库会话管理器（Phase B 决策一：数据库路径统一来自 config.database.db_config）"""

from contextlib import contextmanager
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker, scoped_session
from config.database import db_config
from ..models import Base
import os

class SessionManager:
    """
    数据库会话管理器

    职责：
    1. 管理数据库连接和会话
    2. 提供统一的会话上下文管理
    3. 处理事务和异常回滚

    Phase B（决策一）：db_path 允许显式传入（测试注入），缺省时统一取
    ``db_config.connection_string``，业务代码禁止硬编码 SQLite 路径。
    """

    def __init__(self, db_path: str | None = None):
        """初始化会话管理器

        Args:
            db_path: 数据库连接路径；None 时使用全局 db_config（推荐）
        """
        self.db_path = db_path or db_config.connection_string

        if self.db_path.startswith("sqlite"):
            self._init_sqlite()
        else:
            self.engine = create_engine(self.db_path, **db_config.get_engine_kwargs())

        Base.metadata.create_all(self.engine)
        self.Session = scoped_session(sessionmaker(bind=self.engine))

    def _init_sqlite(self):
        """SQLite 初始化：确保父目录存在、开启 WAL、每连接启用外键。"""
        database = make_url(self.db_path).database
        if database and database != ":memory:":
            parent = os.path.dirname(os.path.abspath(database))
            os.makedirs(parent, exist_ok=True)
        self.engine = create_engine(
            self.db_path,
            connect_args={"check_same_thread": False, "timeout": 5},
        )
        # 外键约束按连接生效：listener 必须注册在首个连接建立之前（Phase C C1）
        from sqlalchemy import event

        @event.listens_for(self.engine, "connect")
        def _enable_sqlite_foreign_keys(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        # WAL 与 busy timeout：SQLite 并发写友好（Phase C D6）
        with self.engine.connect() as conn:
            conn.execute(text("PRAGMA journal_mode=WAL"))
            conn.commit()

    @contextmanager
    def session_scope(self):
        """
        提供会话上下文管理

        自动处理：
        - 会话创建和关闭
        - 事务提交和回滚
        - 异常处理
        """
        session = self.Session()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def close(self):
        """关闭会话管理器"""
        self.Session.remove()
        self.engine.dispose()