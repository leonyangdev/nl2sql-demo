"""业务数据库（sales.db）—— 一个普普通通的业务系统。

注意：到这里为止，NL2SQL 还没有出现。
这里只有"业务事实"：订单、明细、产品、区域、金额。

以后换 PostgreSQL / MySQL，只需要改 config.SOURCE_DB_URL，其余代码不动。
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

import config


class Base(DeclarativeBase):
    pass


class Region(Base):
    """销售区域：一行代表一个销售区域。"""

    __tablename__ = "region"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    region_name: Mapped[str] = mapped_column(String(64), nullable=False)


class Product(Base):
    """产品主数据：一行代表一个产品。"""

    __tablename__ = "product"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_name: Mapped[str] = mapped_column(String(128), nullable=False)
    category: Mapped[str | None] = mapped_column(String(64))


class SalesOrder(Base):
    """销售订单头：一行代表一张销售订单。"""

    __tablename__ = "sales_order"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    region_id: Mapped[int] = mapped_column(
        ForeignKey("region.id"), nullable=False
    )
    order_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class SalesOrderItem(Base):
    """销售订单明细：一行代表一张订单中的一个产品。"""

    __tablename__ = "sales_order_item"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(
        ForeignKey("sales_order.id"), nullable=False
    )
    product_id: Mapped[int] = mapped_column(
        ForeignKey("product.id"), nullable=False
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    pay_amount: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)


def get_source_engine():
    return create_engine(config.SOURCE_DB_URL)


def init_source_db() -> None:
    """重建业务数据库并灌入演示数据。"""

    engine = get_source_engine()

    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        session.add_all(
            [
                Region(id=1, region_name="华南区"),
                Region(id=2, region_name="华东区"),
                Region(id=3, region_name="华北区"),
            ]
        )

        session.add_all(
            [
                Product(id=101, product_name="智能手机 A", category="手机"),
                Product(id=102, product_name="笔记本 B", category="电脑"),
                Product(id=103, product_name="平板电脑 C", category="电脑"),
            ]
        )

        session.add_all(
            [
                SalesOrder(
                    id=10001,
                    region_id=1,
                    order_date=date(2026, 8, 1),
                    status="completed",
                    created_at=datetime(2026, 8, 1, 10, 0, 0),
                ),
                SalesOrder(
                    id=10002,
                    region_id=2,
                    order_date=date(2026, 8, 2),
                    status="completed",
                    created_at=datetime(2026, 8, 2, 11, 30, 0),
                ),
                # 额外加一张"已取消"订单，用来验证 Metric 的默认过滤条件
                # （销售额只统计 status = completed）确实生效
                SalesOrder(
                    id=10003,
                    region_id=1,
                    order_date=date(2026, 8, 3),
                    status="canceled",
                    created_at=datetime(2026, 8, 3, 9, 15, 0),
                ),
                # 华北区订单，让"按区域对比"的提问也有数据可看
                SalesOrder(
                    id=10004,
                    region_id=3,
                    order_date=date(2026, 8, 5),
                    status="completed",
                    created_at=datetime(2026, 8, 5, 14, 20, 0),
                ),
            ]
        )

        session.add_all(
            [
                # 订单 10001（华南区 / completed）
                SalesOrderItem(
                    id=1, order_id=10001, product_id=101,
                    quantity=2, pay_amount=8998,
                ),
                SalesOrderItem(
                    id=2, order_id=10001, product_id=102,
                    quantity=1, pay_amount=5999,
                ),
                # 订单 10002（华东区 / completed）
                SalesOrderItem(
                    id=3, order_id=10002, product_id=101,
                    quantity=5, pay_amount=22495,
                ),
                SalesOrderItem(
                    id=4, order_id=10002, product_id=103,
                    quantity=3, pay_amount=10797,
                ),
                # 订单 10003（华南区 / canceled）—— 应被默认过滤条件排除
                SalesOrderItem(
                    id=5, order_id=10003, product_id=103,
                    quantity=9, pay_amount=32391,
                ),
                # 订单 10004（华北区 / completed）
                SalesOrderItem(
                    id=6, order_id=10004, product_id=101,
                    quantity=4, pay_amount=17996,
                ),
                SalesOrderItem(
                    id=7, order_id=10004, product_id=102,
                    quantity=2, pay_amount=11998,
                ),
            ]
        )

        session.commit()

    print(f"[source] 业务数据库已重建：{config.SOURCE_DB_PATH}")


if __name__ == "__main__":
    init_source_db()
