import io
import json
from datetime import date
from pathlib import Path

import pytest
from app.errors import InputError
from app.modules.m6_reconcile.service import parse_statement
from app.modules.m8_arrears.aging import bucket, late_fee, overdue_days, overdue_start
from app.modules.m8_arrears.service import build_ledger, parse_asof

ROOT = Path(__file__).resolve().parents[1]


def clean_book(paid=None, issued=None):
    data = json.loads((ROOT / "sample_data" / "clean.json").read_text(encoding="utf-8"))
    row = data["statement"][0]
    if paid is not None:
        row["paid"] = str(paid)
    if issued is not None:
        row["issued"] = issued
    return json.dumps(data, ensure_ascii=False).encode()


def upload(client, book, period):
    return client.post("/api/runs", data={"period": period, "file": (io.BytesIO(book), "账簿.json")})


def test_contract_start_when_bill_on_time():
    assert overdue_start("2026-11", None) == (date(2026, 11, 6), "合同应收日")
    assert overdue_start("2026-11", date(2026, 11, 5)) == (date(2026, 11, 6), "合同应收日")


def test_late_bill_defers_start_to_merchant():
    assert overdue_start("2026-11", date(2026, 11, 6)) == (date(2026, 11, 7), "实际出账日")
    assert overdue_start("2026-11", date(2026, 11, 18)) == (date(2026, 11, 19), "实际出账日")


def test_overdue_days_counted_inclusively():
    assert overdue_days(date(2026, 11, 6), date(2026, 11, 5)) == 0
    assert overdue_days(date(2026, 11, 6), date(2026, 11, 6)) == 1
    assert overdue_days(date(2026, 10, 6), date(2026, 11, 21)) == 47


def test_bucket_boundaries():
    assert [bucket(d) for d in (0, 1, 30, 31, 60, 61, 90, 91)] == [
        "未逾期",
        "1-30天",
        "1-30天",
        "31-60天",
        "31-60天",
        "61-90天",
        "61-90天",
        "90天以上",
    ]


def test_late_fee_rounds_half_up_to_cent():
    assert late_fee(6000, 11) == 33
    assert late_fee(13000, 47) == 306
    assert late_fee(600, 16) == 5
    assert late_fee(5000, 0) == 0


def test_statement_issued_optional_and_validated():
    paid, issued = parse_statement([{"shop": "001", "paid": "10", "issued": "2026-11-18"}])
    assert paid == {"001": 1000} and issued == {"001": date(2026, 11, 18)}
    assert parse_statement([{"shop": "001", "paid": "10"}])[1] == {"001": None}
    with pytest.raises(InputError) as exc:
        parse_statement([{"shop": "001", "paid": "10", "issued": "2026-13-01"}])
    assert exc.value.code == "BAD_STATEMENT"


def test_ledger_spans_periods_and_shares_reconcile_balance(client):
    upload(client, clean_book(paid=0), "2026-10")
    upload(client, clean_book(paid=100, issued="2026-11-10"), "2026-11")
    ledger = client.get("/api/arrears?asof=2026-11-21").json
    assert [(row["账期"], row["未清分"]) for row in ledger["台账"]] == [
        ("2026-10", 13000),
        ("2026-11", 6000),
    ]
    october, november = ledger["台账"]
    assert (october["起算日"], october["起算依据"], october["逾期天数"]) == (
        "2026-10-06",
        "合同应收日",
        47,
    )
    assert (october["账龄档"], october["滞纳金分"]) == ("31-60天", 306)
    assert (november["起算日"], november["起算依据"], november["逾期天数"]) == (
        "2026-11-11",
        "实际出账日",
        11,
    )
    assert (november["账龄档"], november["滞纳金分"]) == ("1-30天", 33)
    assert ledger["汇总"]["未清分"] == 19000 and ledger["汇总"]["滞纳金分"] == 339
    source = client.get(f"/api/reconcile/{november['来源批次']}").json
    assert source["柜台对账"][0]["差额分"] == november["未清分"]
    assert source["收款记录"] == [{"柜台": "001", "实缴分": 10000, "出账日": "2026-11-10"}]


def test_rerun_same_book_never_double_counts(client):
    book = clean_book(paid=0)
    first, second = upload(client, book, "2026-11"), upload(client, book, "2026-11")
    assert [first.status_code, second.status_code] == [201, 200]
    ledger = client.get("/api/arrears?asof=2026-11-21").json
    assert len(ledger["台账"]) == 1 and ledger["汇总"]["未清分"] == 16000
    assert ledger == client.get("/api/arrears?asof=2026-11-21").json


def test_installment_payments_replace_period_balance(client):
    upload(client, clean_book(paid=0), "2026-11")
    assert client.get("/api/arrears?asof=2026-11-21").json["汇总"]["未清分"] == 16000
    upload(client, clean_book(paid=100), "2026-11")
    assert client.get("/api/arrears?asof=2026-11-21").json["汇总"]["未清分"] == 6000
    upload(client, clean_book(paid=160), "2026-11")
    ledger = client.get("/api/arrears?asof=2026-11-21").json
    assert ledger["台账"] == [] and len(client.get("/api/runs").json) == 3


def test_failed_rerun_keeps_previous_version(client):
    upload(client, clean_book(paid=0), "2026-11")
    before = client.get("/api/arrears?asof=2026-11-21").json
    bad = client.post(
        "/api/runs",
        data={"period": "2026-11", "file": (io.BytesIO(clean_book(issued="2026-13-40")), "x.json")},
    )
    assert bad.status_code == 422 and bad.json["code"] == "BAD_STATEMENT"
    assert client.get("/api/arrears?asof=2026-11-21").json == before


def test_conflicted_or_unbased_shops_suspended(client):
    client.post("/api/runs", json={"sample": "mall", "period": "2026-11"})
    ledger = client.get("/api/arrears?asof=2026-11-21").json
    assert [(row["柜台"], row["未清分"], row["起算依据"]) for row in ledger["台账"]] == [
        ("001", 500, "合同应收日"),
        ("002", 13000, "实际出账日"),
    ]
    assert ledger["挂起"] == [
        {
            "柜台": "003",
            "账期": "2026-11",
            "应缴分": 27000,
            "实缴分": None,
            "挂起原因": ["合同时窗冲突", "缺缴款单"],
            "来源批次": ledger["台账"][0]["来源批次"],
        }
    ]
    assert ledger["汇总"]["挂起账期"] == 1 and "003" not in {row["柜台"] for row in ledger["台账"]}


def test_overpaid_period_stays_out_of_ledger():
    run = {
        "运行清单": {"批次": "old", "账期": "2026-10"},
        "合同时窗冲突": [],
        "柜台对账": [
            {"柜台": "008", "应缴分": 13000, "实缴分": 16000, "差额分": -3000, "对账": "应退"},
            {"柜台": "009", "应缴分": 1000, "实缴分": 400, "差额分": 600, "对账": "应补"},
        ],
    }
    ledger = build_ledger([run], date(2026, 11, 21))
    assert [row["柜台"] for row in ledger["台账"]] == ["009"]
    row = ledger["台账"][0]
    assert (row["出账日"], row["起算依据"], row["逾期天数"], row["滞纳金分"]) == (
        None,
        "合同应收日",
        47,
        14,
    )


def test_bad_asof_rejected(client):
    assert client.get("/api/arrears?asof=11月").json["code"] == "BAD_ASOF"
    with pytest.raises(InputError):
        parse_asof("2026/11/21")


def test_arrears_page_lists_stage(client):
    client.post("/api/runs", json={"sample": "mall"})
    page = client.get("/work/arrears?asof=2026-11-21")
    assert page.status_code == 200
    for text in ("欠租台账", "挂起柜台", "起算依据", "实际出账日", "合同应收日"):
        assert text in page.get_data(as_text=True)
