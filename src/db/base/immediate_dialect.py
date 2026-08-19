"""SQLite 自定义方言：事务一律以 BEGIN IMMEDIATE 开始（Phase C D6）。

SQLAlchemy 2.0 的 sqlite 方言自己管理事务：`do_begin` 按隔离级别发出
BEGIN 语句。默认 SERIALIZABLE 对应普通 BEGIN（deferred），在"读-写"
场景存在 lost update 窗口（并发确认可产生重复预约）。

本方言覆盖 `do_begin` 恒发 `BEGIN IMMEDIATE`（写锁抢占）：事务开始即持有
写锁，后续事务的 BEGIN 阻塞到提交后重新读取最新快照，保证冲突检查与写入
同事务原子。代价：读事务也持写锁——本地单进程演示场景可接受。

用法：URL 使用 sqlite+immediate:///path.db
"""

from sqlalchemy.dialects import registry
from sqlalchemy.dialects.sqlite.pysqlite import SQLiteDialect_pysqlite


class ImmediateSQLiteDialect(SQLiteDialect_pysqlite):
    # 声明支持语句编译缓存，消除 SAWarning（SQLAlchemy 1.4+）
    supports_statement_cache = True

    def do_begin(self, connection):
        # connection 是 DBAPI 层连接（sqlite3），用原生 execute 发 BEGIN IMMEDIATE
        connection.execute("BEGIN IMMEDIATE")


registry.register("sqlite.immediate", __name__, "ImmediateSQLiteDialect")
