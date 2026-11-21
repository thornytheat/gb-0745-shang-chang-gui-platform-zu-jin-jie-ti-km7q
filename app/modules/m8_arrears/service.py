from datetime import date, datetime, timedelta, timezone

from app.errors import InputError
from app.modules.m8_arrears.aging import BUCKETS, bucket, late_fee, overdue_days, overdue_start

BUSINESS_TZ = timezone(timedelta(hours=8), "Asia/Shanghai")


def parse_asof(raw):
    if raw is None or not str(raw).strip():
        return datetime.now(BUSINESS_TZ).date()
    try:
        return date.fromisoformat(str(raw).strip())
    except ValueError as exc:
        raise InputError("BAD_ASOF", "基准日须为 YYYY-MM-DD") from exc


def build_ledger(runs, asof):
    rows, held = [], []
    for run in runs:
        period = run["运行清单"]["账期"]
        run_id = run["运行清单"]["批次"]
        conflicts = {item["柜台"] for item in run["合同时窗冲突"]}
        issued = {item["柜台"]: item["出账日"] for item in run.get("收款记录", [])}
        for line in run["柜台对账"]:
            shop = line["柜台"]
            reasons = []
            if shop in conflicts:
                reasons.append("合同时窗冲突")
            if line["实缴分"] is None and line["应缴分"] > 0:
                reasons.append("缺缴款单")
            if reasons:
                held.append(
                    {
                        "柜台": shop,
                        "账期": period,
                        "应缴分": line["应缴分"],
                        "实缴分": line["实缴分"],
                        "挂起原因": reasons,
                        "来源批次": run_id,
                    }
                )
                continue
            if line["实缴分"] is None or line["差额分"] is None:
                continue
            unpaid = max(line["差额分"], 0)
            if not unpaid:
                continue
            issued_day = date.fromisoformat(issued[shop]) if issued.get(shop) else None
            start, basis = overdue_start(period, issued_day)
            days = overdue_days(start, asof)
            rows.append(
                {
                    "柜台": shop,
                    "账期": period,
                    "应缴分": line["应缴分"],
                    "实缴分": line["实缴分"],
                    "未清分": unpaid,
                    "出账日": issued.get(shop),
                    "起算日": start.isoformat(),
                    "起算依据": basis,
                    "逾期天数": days,
                    "账龄档": bucket(days),
                    "滞纳金分": late_fee(unpaid, days),
                    "来源批次": run_id,
                }
            )
    rows.sort(key=lambda row: (row["柜台"], row["账期"]))
    held.sort(key=lambda row: (row["柜台"], row["账期"]))
    return {"基准日": asof.isoformat(), "台账": rows, "挂起": held, "汇总": summarize(rows, held)}


def summarize(rows, held):
    distribution = [
        {
            "账龄档": name,
            "账期数": len(part),
            "未清分": sum(row["未清分"] for row in part),
            "滞纳金分": sum(row["滞纳金分"] for row in part),
        }
        for name in BUCKETS
        for part in [[row for row in rows if row["账龄档"] == name]]
    ]
    return {
        "未清分": sum(row["未清分"] for row in rows),
        "滞纳金分": sum(row["滞纳金分"] for row in rows),
        "逾期账期": sum(1 for row in rows if row["逾期天数"] > 0),
        "挂起账期": len(held),
        "账龄分布": distribution,
    }
