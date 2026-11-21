from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from app.config import LATE_FEE_RATE_POINTS

BUCKETS = ("未逾期", "1-30天", "31-60天", "61-90天", "90天以上")


def contract_start(period):
    """合同应收日为账期当月5日，逾期自6日起算。"""
    year, month = period.split("-")
    return date(int(year), int(month), 6)


def overdue_start(period, issued):
    """起算日取合同起算日与出账日次日的较晚者，出账延迟不由商户承担。"""
    base = contract_start(period)
    if issued is not None and issued >= base:
        return issued + timedelta(days=1), "实际出账日"
    return base, "合同应收日"


def overdue_days(start, asof):
    return max(0, (asof - start).days + 1)


def bucket(days):
    if days <= 0:
        return BUCKETS[0]
    if days <= 30:
        return BUCKETS[1]
    if days <= 60:
        return BUCKETS[2]
    if days <= 90:
        return BUCKETS[3]
    return BUCKETS[4]


def late_fee(unpaid, days, points=LATE_FEE_RATE_POINTS):
    """滞纳金 = 未清分 × 逾期天数 × 日万分比，四舍五入到分。"""
    return int(
        (Decimal(unpaid * days * points) / 10000).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    )
