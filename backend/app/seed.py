from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from .models import DailyOperation, Order, Product, SeedVersion, Store

SEED_VERSION = "demo-60d-v1"
SEED_START = date(2026, 9, 1)
SEED_END = date(2026, 10, 30)


def seed_demo(factory) -> dict:
    """Deterministic append-once seed. The version insert claims the transaction."""
    try:
        with factory.begin() as session:
            if session.get(SeedVersion, SEED_VERSION):
                return {"seeded": False, "version": SEED_VERSION, "message": "演示数据已存在，保留全部历史"}
            if session.scalar(select(func.count()).select_from(Order)):
                return {"seeded": False, "version": None, "message": "已有业务数据，未混入演示数据"}
            session.add(SeedVersion(version=SEED_VERSION))
            session.flush()
            stores = [
                Store(id="us-store", name="北美旗舰店", marketplace="Amazon US"),
                Store(id="eu-store", name="欧洲直营店", marketplace="Shopify EU · USD ledger"),
            ]
            products = [
                Product(id="bag", sku="TRAVEL-BAG-01", name="轻量旅行背包"),
                Product(id="bottle", sku="THERMO-02", name="不锈钢保温杯"),
                Product(id="hub", sku="USB-HUB-03", name="多口扩展坞"),
            ]
            session.add_all(stores + products)
            session.flush()
            prices = [4900, 3200, 3900]
            costs = [1600, 1000, 1400]
            for day in range(60):
                business_date = SEED_START + timedelta(days=day)
                current = day >= 30
                for shop, store in enumerate(stores):
                    for idx, product in enumerate(products):
                        count = (6 if current else 4) + ((day + idx + shop) % 3 == 0)
                        ad = ([12500, 6500, 7200] if current else [1400, 1100, 1200])[idx]
                        ad += (day % 5) * 100 + shop * 150
                        operation_id = f"{store.id}-{product.id}-{business_date}"
                        session.add(DailyOperation(
                            id=operation_id, store_id=store.id, product_id=product.id,
                            date=business_date, ad_spend=ad,
                            inventory=210 + idx * 60 - day * 2 + shop * 35,
                            inbound_inventory=120 if day >= 20 else 60, complete=True,
                        ))
                        for number in range(count):
                            price = prices[idx]
                            refund = price // 2 if (day * 7 + idx * 3 + number + shop) % (29 if current else 43) == 0 else 0
                            discount = price * (8 if current else 5) // 100
                            session.add(Order(
                                id=f"DEMO-{operation_id}-{number}", store_id=store.id,
                                product_id=product.id, date=business_date, quantity=1,
                                sales=price, discounts=discount, refunds=refund,
                                cogs=costs[idx], fulfillment=(650 if current else 430) + shop * 40,
                                platform_fees=price * 15 // 100, other_costs=90,
                            ))
            return {
                "seeded": True, "version": SEED_VERSION, "days": 60,
                "start_date": str(SEED_START), "end_date": str(SEED_END),
                "message": "已初始化 2 店铺 × 3 SKU × 60 天演示数据",
            }
    except IntegrityError:
        # A concurrent seed may win the unique version claim. Do not hide other constraint failures.
        with factory() as session:
            if session.get(SeedVersion, SEED_VERSION):
                return {"seeded": False, "version": SEED_VERSION, "message": "演示数据已由另一请求初始化"}
        raise


def main():
    from .config import get_settings
    from .db import build_engine, initialize_database, make_session_factory

    engine = build_engine(get_settings().database_url)
    initialize_database(engine)
    print(seed_demo(make_session_factory(engine)))


if __name__ == "__main__":
    main()
