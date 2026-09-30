#!/usr/bin/env python3
"""Analyze a normalized personal ledger with auditable, conservative matching.

The script intentionally accepts a stable normalized CSV instead of guessing every
bank export layout. It uses only the Python standard library and writes local CSV,
JSON, and a standalone HTML dashboard.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from difflib import SequenceMatcher
from pathlib import Path


OPTIONAL_FIELDS = [
    "time", "transaction_id", "related_order_id", "account", "payment_method",
    "counterparty", "description", "category_hint", "notes", "asset_id",
    "travel_id", "status",
]
FAILED_STATUS = ("关闭", "取消", "失败", "未支付", "已撤销", "closed", "cancelled", "failed")
AA_WORDS = ("群收款", "aa收款", "aa 收款", "aa返还", "代付返还", "分摊返还", "拼单返还")
REFUND_WORDS = ("退款", "退货", "退回", "refund")
SELF_TRANSFER_HINTS = ("self_transfer", "内部转账", "本人转账", "自有账户转账")

EXPENSE_RULES = [
    ("旅游", ("酒店", "民宿", "景区", "旅行", "旅游", "机票", "航旅", "携程", "同程")),
    ("餐饮", ("餐", "饭", "食堂", "外卖", "奶茶", "咖啡", "饮品", "美团", "饿了么", "restaurant")),
    ("交通", ("地铁", "公交", "打车", "滴滴", "高铁", "火车", "铁路", "出租车", "加油", "停车", "交通")),
    ("学习", ("书店", "教材", "课程", "培训", "考试", "报名费", "论文", "打印", "学习")),
    ("住宿", ("房租", "租金", "宿舍", "物业", "水费", "电费", "燃气", "住宿")),
    ("医疗", ("医院", "药房", "诊所", "体检", "医疗", "挂号")),
    ("娱乐", ("电影", "游戏", "ktv", "演出", "门票", "娱乐", "酒吧")),
    ("通信", ("话费", "流量", "宽带", "通信", "运营商")),
    ("数字服务", ("会员", "订阅", "云服务", "软件", "app store", "应用商店")),
    ("人情往来", ("红包", "礼物", "礼金", "转账赠与")),
    ("手续费", ("手续费", "服务费", "利息", "年费")),
    ("购物", ("超市", "便利店", "商场", "淘宝", "天猫", "京东", "拼多多", "购物")),
]
INCOME_RULES = [
    ("生活费/家庭支持", ("生活费", "家庭支持", "家人", "父母")),
    ("兼职与薪资", ("工资", "薪资", "兼职", "劳务", "实习")),
    ("奖学金与补助", ("奖学金", "助学金", "补助", "津贴")),
    ("投资收益", ("利息收入", "理财收益", "分红", "投资收益")),
    ("礼金与赠与", ("礼金", "赠与", "红包")),
]


def read_csv_any(path: Path) -> list[dict]:
    last_error = None
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            with path.open("r", encoding=encoding, newline="") as handle:
                return [dict(row) for row in csv.DictReader(handle)]
        except UnicodeDecodeError as error:
            last_error = error
    raise ValueError(f"Cannot decode CSV {path}: {last_error}")


def parse_day(value: str) -> date:
    raw = (value or "").strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y年%m月%d日"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"invalid date: {value!r}")


def parse_clock(value: str) -> time:
    raw = (value or "").strip()
    if not raw:
        return time(0, 0, 0)
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            return datetime.strptime(raw, fmt).time()
        except ValueError:
            pass
    return time(0, 0, 0)


def money_to_cents(value: str) -> int:
    raw = re.sub(r"[^\d.\-]", "", (value or "").strip())
    try:
        amount = Decimal(raw)
    except InvalidOperation as error:
        raise ValueError(f"invalid amount: {value!r}") from error
    return int((abs(amount) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def yuan(cents: int) -> float:
    return float((Decimal(cents) / 100).quantize(Decimal("0.01")))


def norm_direction(value: str) -> str:
    raw = (value or "").strip().lower()
    if raw in {"expense", "支出", "出", "debit", "借"}:
        return "expense"
    if raw in {"income", "收入", "入", "credit", "贷"}:
        return "income"
    if raw in {"refund", "退款", "退回"}:
        return "refund"
    raise ValueError(f"unsupported direction: {value!r}")


def normalized_text(*values: str) -> str:
    joined = " ".join((value or "").lower() for value in values)
    return re.sub(r"[\W_]+", "", joined, flags=re.UNICODE)


def similarity(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, left, right).ratio()


def stable_id(row: dict, index: int) -> str:
    supplied = (row.get("id") or "").strip()
    if supplied:
        return supplied
    material = "|".join([
        row.get("source", ""), row.get("date", ""), row.get("time", ""),
        row.get("direction", ""), row.get("amount", ""),
        row.get("transaction_id", ""), row.get("counterparty", ""),
        row.get("description", ""), str(index),
    ])
    return hashlib.sha1(material.encode("utf-8")).hexdigest()[:16]


def is_bank(source: str) -> bool:
    value = source.lower()
    return "bank" in value or "银行" in value or "银行卡" in value


def is_platform(source: str) -> bool:
    value = source.lower()
    return any(key in value for key in ("wechat", "weixin", "微信", "alipay", "支付宝"))


def tail_tokens(value: str) -> set[str]:
    return set(re.findall(r"(?<!\d)\d{4}(?!\d)", value or ""))


def expense_category(row: dict) -> str:
    hint = (row.get("category_hint") or "").strip()
    if hint and hint.lower() not in SELF_TRANSFER_HINTS:
        return hint
    if row.get("travel_id"):
        return "旅游"
    haystack = normalized_text(row.get("counterparty", ""), row.get("description", ""), row.get("notes", ""))
    for category, keywords in EXPENSE_RULES:
        if any(normalized_text(word) in haystack for word in keywords):
            return category
    return "其他"


def income_category(row: dict) -> str:
    hint = (row.get("category_hint") or "").strip()
    if hint and hint.lower() not in SELF_TRANSFER_HINTS:
        return hint
    haystack = normalized_text(row.get("counterparty", ""), row.get("description", ""), row.get("notes", ""))
    for category, keywords in INCOME_RULES:
        if any(normalized_text(word) in haystack for word in keywords):
            return category
    return "其他收入"


def contains_any(row: dict, words: tuple[str, ...]) -> bool:
    haystack = normalized_text(
        row.get("category_hint", ""), row.get("counterparty", ""),
        row.get("description", ""), row.get("notes", ""),
    )
    return any(normalized_text(word) in haystack for word in words)


def is_aa(row: dict) -> bool:
    return row["direction"] in {"income", "refund"} and contains_any(row, AA_WORDS)


def is_refund(row: dict) -> bool:
    return row["direction"] == "refund" or (
        row["direction"] == "income" and contains_any(row, REFUND_WORDS) and not is_aa(row)
    )


def is_self_transfer(row: dict) -> bool:
    hint = (row.get("category_hint") or "").strip().lower()
    notes = (row.get("notes") or "").strip().lower()
    return hint in SELF_TRANSFER_HINTS or any(key in notes for key in ("self_transfer=true", "internal_transfer=true"))


def write_csv(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def load_transactions(path: Path, start: date, end: date) -> list[dict]:
    rows = read_csv_any(path)
    required = {"date", "source", "direction", "amount"}
    missing = required - set(rows[0].keys() if rows else [])
    if missing:
        raise ValueError(f"normalized CSV missing columns: {', '.join(sorted(missing))}")
    output = []
    for index, original in enumerate(rows, start=2):
        row = {key: (value or "").strip() for key, value in original.items()}
        for field in OPTIONAL_FIELDS:
            row.setdefault(field, "")
        try:
            row_day = parse_day(row["date"])
            cents = money_to_cents(row["amount"])
            direction = norm_direction(row["direction"])
        except ValueError as error:
            raise ValueError(f"{path}:{index}: {error}") from error
        if not start <= row_day <= end:
            continue
        row["_day"] = row_day
        row["_dt"] = datetime.combine(row_day, parse_clock(row["time"]))
        row["_cents"] = cents
        row["direction"] = direction
        row["id"] = stable_id(row, index)
        row["_text"] = normalized_text(row["counterparty"], row["description"])
        row["_input_row"] = index
        output.append(row)
    return sorted(output, key=lambda item: (item["_dt"], item["_input_row"]))


def deduplicate(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    kept: list[dict] = []
    audit: list[dict] = []
    exact_seen: dict[tuple, dict] = {}
    txid_seen: dict[tuple, dict] = {}
    excluded_ids: set[str] = set()

    for row in rows:
        status = row.get("status", "").lower()
        if any(word in status for word in FAILED_STATUS):
            excluded_ids.add(row["id"])
            audit.append({
                "action": "excluded_status", "kept_id": "", "removed_or_candidate_id": row["id"],
                "reason": row.get("status", ""), "amount": yuan(row["_cents"]), "date": row["date"],
            })
            continue
        txid = row.get("transaction_id", "")
        txkey = (row["source"].lower(), txid) if txid else None
        exact_key = (
            row["source"].lower(), row["_dt"], row["direction"], row["_cents"],
            row["_text"], row.get("account", ""),
        )
        if txkey and txkey in txid_seen:
            excluded_ids.add(row["id"])
            audit.append({
                "action": "removed_same_source_duplicate", "kept_id": txid_seen[txkey]["id"],
                "removed_or_candidate_id": row["id"], "reason": "same transaction_id",
                "amount": yuan(row["_cents"]), "date": row["date"],
            })
            continue
        if exact_key in exact_seen:
            excluded_ids.add(row["id"])
            audit.append({
                "action": "removed_same_source_duplicate", "kept_id": exact_seen[exact_key]["id"],
                "removed_or_candidate_id": row["id"], "reason": "same source/date/time/amount/text",
                "amount": yuan(row["_cents"]), "date": row["date"],
            })
            continue
        exact_seen[exact_key] = row
        if txkey:
            txid_seen[txkey] = row
        kept.append(row)

    by_amount: dict[tuple, list[dict]] = defaultdict(list)
    for row in kept:
        by_amount[(row["direction"], row["_cents"])].append(row)

    cross_removed: set[str] = set()
    candidate_pairs: set[tuple[str, str]] = set()
    for group in by_amount.values():
        platforms = [row for row in group if is_platform(row["source"])]
        banks = [row for row in group if is_bank(row["source"])]
        for platform in platforms:
            choices = []
            for bank in banks:
                if bank["id"] in cross_removed:
                    continue
                delta = abs((platform["_dt"] - bank["_dt"]).total_seconds())
                if delta > 48 * 3600:
                    continue
                same_order = bool(
                    platform.get("related_order_id") and
                    platform.get("related_order_id") in {
                        bank.get("transaction_id"), bank.get("related_order_id")
                    }
                )
                platform_tails = tail_tokens(platform.get("payment_method", "") + " " + platform.get("account", ""))
                bank_tails = tail_tokens(bank.get("account", "") + " " + bank.get("description", ""))
                linked_tail = bool(platform_tails & bank_tails)
                text_score = similarity(platform["_text"], bank["_text"])
                strong = same_order or linked_tail or text_score >= 0.55
                score = (3 if same_order else 0) + (2 if linked_tail else 0) + text_score - delta / (48 * 3600) * 0.1
                choices.append((score, strong, bank, same_order, linked_tail, text_score))
            if not choices:
                continue
            choices.sort(key=lambda item: item[0], reverse=True)
            score, strong, bank, same_order, linked_tail, text_score = choices[0]
            pair = (platform["id"], bank["id"])
            if strong:
                cross_removed.add(bank["id"])
                audit.append({
                    "action": "removed_cross_platform_mirror", "kept_id": platform["id"],
                    "removed_or_candidate_id": bank["id"],
                    "reason": f"order={same_order}; card_tail={linked_tail}; text_similarity={text_score:.2f}",
                    "amount": yuan(platform["_cents"]), "date": platform["date"],
                })
            elif pair not in candidate_pairs:
                candidate_pairs.add(pair)
                audit.append({
                    "action": "candidate_kept", "kept_id": platform["id"],
                    "removed_or_candidate_id": bank["id"],
                    "reason": f"same amount/direction within 48h; text_similarity={text_score:.2f}",
                    "amount": yuan(platform["_cents"]), "date": platform["date"],
                })

    return [row for row in kept if row["id"] not in cross_removed], audit


def match_refunds(rows: list[dict]) -> tuple[list[dict], dict[str, int], int]:
    expenses = [row for row in rows if row["direction"] == "expense"]
    refunds = [row for row in rows if is_refund(row)]
    remaining = {row["id"]: row["_cents"] for row in expenses}
    matched_by_expense: dict[str, int] = defaultdict(int)
    output = []
    total = 0

    for refund in refunds:
        candidates = []
        related = refund.get("related_order_id", "")
        for expense in expenses:
            if expense["_day"] > refund["_day"] or remaining[expense["id"]] <= 0:
                continue
            age = (refund["_day"] - expense["_day"]).days
            if age > 180:
                continue
            linked = bool(related and related in {
                expense.get("transaction_id", ""), expense.get("related_order_id", ""), expense["id"]
            })
            same_source = refund["source"].lower() == expense["source"].lower()
            text_score = similarity(refund["_text"], expense["_text"])
            if linked:
                score = 10 - age / 1000
            elif same_source and text_score >= 0.30:
                score = 2 + text_score - age / 1000
            else:
                continue
            candidates.append((score, expense, linked, text_score, age))
        candidates.sort(key=lambda item: item[0], reverse=True)
        chosen = None
        if candidates:
            best = candidates[0]
            unique = len(candidates) == 1 or best[0] - candidates[1][0] >= 0.10
            if best[2] or unique:
                chosen = best
        if not chosen:
            output.append({
                "refund_id": refund["id"], "expense_id": "", "status": "unmatched",
                "refund_date": refund["date"], "amount": yuan(refund["_cents"]),
                "reason": "no unique strong match",
            })
            continue
        _, expense, linked, text_score, age = chosen
        applied = min(refund["_cents"], remaining[expense["id"]])
        remaining[expense["id"]] -= applied
        matched_by_expense[expense["id"]] += applied
        total += applied
        output.append({
            "refund_id": refund["id"], "expense_id": expense["id"], "status": "matched",
            "refund_date": refund["date"], "amount": yuan(applied),
            "reason": f"linked_order={linked}; text_similarity={text_score:.2f}; age_days={age}",
        })
    return output, dict(matched_by_expense), total


def analyze(rows: list[dict], travel_path: Path | None, start: date, end: date) -> tuple[dict, dict[str, list[dict]]]:
    expenses = [row for row in rows if row["direction"] == "expense"]
    expense_by_id = {row["id"]: row for row in expenses}
    refund_rows, refunds_by_expense, matched_refund_total = match_refunds(rows)

    aa_rows = [row for row in rows if is_aa(row)]
    aa_audit = []
    aa_allocated: dict[str, int] = defaultdict(int)
    aa_total = 0
    unallocated_aa = 0
    for row in aa_rows:
        aa_total += row["_cents"]
        related = row.get("related_order_id", "")
        match = next((
            expense for expense in expenses
            if related and related in {expense["id"], expense.get("transaction_id", ""), expense.get("related_order_id", "")}
        ), None)
        if match:
            aa_allocated[match["id"]] += row["_cents"]
            status = "allocated"
            expense_id = match["id"]
            category = expense_category(match)
        else:
            unallocated_aa += row["_cents"]
            status = "unallocated"
            expense_id = ""
            category = "未分配AA冲减"
        aa_audit.append({
            "aa_id": row["id"], "expense_id": expense_id, "date": row["date"],
            "amount": yuan(row["_cents"]), "status": status, "category": category,
        })

    purchase_by_asset: dict[str, list[dict]] = defaultdict(list)
    for row in expenses:
        if row.get("asset_id"):
            purchase_by_asset[row["asset_id"]].append(row)
    resale_rows = [
        row for row in rows
        if row["direction"] == "income" and row.get("asset_id") and not is_aa(row) and not is_refund(row)
    ]
    linked_resale_total = 0
    resale_by_expense: dict[str, int] = defaultdict(int)
    linked_resale_ids: set[str] = set()
    resale_audit = []
    for row in resale_rows:
        purchases = purchase_by_asset.get(row["asset_id"], [])
        if purchases:
            purchase = max(purchases, key=lambda item: item["_cents"])
            linked_resale_total += row["_cents"]
            resale_by_expense[purchase["id"]] += row["_cents"]
            linked_resale_ids.add(row["id"])
            resale_audit.append({
                "resale_id": row["id"], "purchase_id": purchase["id"], "asset_id": row["asset_id"],
                "amount": yuan(row["_cents"]), "status": "linked",
            })
        else:
            resale_audit.append({
                "resale_id": row["id"], "purchase_id": "", "asset_id": row["asset_id"],
                "amount": yuan(row["_cents"]), "status": "unlinked_kept_as_income",
            })

    refund_ids = {item["refund_id"] for item in refund_rows}
    aa_ids = {row["id"] for row in aa_rows}
    true_income_rows = [
        row for row in rows
        if row["direction"] == "income"
        and row["id"] not in refund_ids
        and row["id"] not in aa_ids
        and row["id"] not in linked_resale_ids
        and not is_self_transfer(row)
    ]
    internal_transfers = [row for row in rows if row["direction"] == "income" and is_self_transfer(row)]

    gross_expense = sum(row["_cents"] for row in expenses)
    true_income = sum(row["_cents"] for row in true_income_rows)
    actual_expense = gross_expense - matched_refund_total - aa_total - linked_resale_total

    category_gross: dict[str, int] = defaultdict(int)
    category_net: dict[str, int] = defaultdict(int)
    for row in expenses:
        category = expense_category(row)
        category_gross[category] += row["_cents"]
        category_net[category] += row["_cents"]
        category_net[category] -= refunds_by_expense.get(row["id"], 0)
        category_net[category] -= aa_allocated.get(row["id"], 0)
        category_net[category] -= resale_by_expense.get(row["id"], 0)
    categories = [
        {
            "category": category,
            "gross": yuan(category_gross[category]),
            "net": yuan(amount),
            "share": round(amount / actual_expense, 6) if actual_expense else 0,
        }
        for category, amount in sorted(category_net.items(), key=lambda item: item[1], reverse=True)
    ]
    if unallocated_aa:
        categories.append({
            "category": "未分配AA冲减", "gross": 0.0, "net": yuan(-unallocated_aa),
            "share": round(-unallocated_aa / actual_expense, 6) if actual_expense else 0,
        })

    income_groups: dict[str, int] = defaultdict(int)
    for row in true_income_rows:
        income_groups[income_category(row)] += row["_cents"]
    incomes = [
        {
            "category": category, "amount": yuan(amount),
            "share": round(amount / true_income, 6) if true_income else 0,
        }
        for category, amount in sorted(income_groups.items(), key=lambda item: item[1], reverse=True)
    ]

    public_entries = []
    monthly: dict[str, dict[str, int]] = defaultdict(lambda: {"expense": 0, "income": 0})
    for row in expenses:
        category = expense_category(row)
        monthly[row["_day"].strftime("%Y-%m")]["expense"] += row["_cents"]
        public_entries.append({
            "date": row["date"], "source": row["source"], "kind": "消费",
            "category": category, "amount": yuan(row["_cents"]),
            "expense_contribution": yuan(row["_cents"]), "income_contribution": 0.0,
        })
    matched_refund_lookup = {item["refund_id"]: item for item in refund_rows if item["status"] == "matched"}
    for row in rows:
        month = row["_day"].strftime("%Y-%m")
        if row["id"] in matched_refund_lookup:
            amount = int(Decimal(str(matched_refund_lookup[row["id"]]["amount"])) * 100)
            expense = expense_by_id[matched_refund_lookup[row["id"]]["expense_id"]]
            category = expense_category(expense)
            monthly[month]["expense"] -= amount
            public_entries.append({
                "date": row["date"], "source": row["source"], "kind": "退款冲减",
                "category": category, "amount": yuan(amount),
                "expense_contribution": yuan(-amount), "income_contribution": 0.0,
            })
        elif row["id"] in aa_ids:
            allocated = next((item for item in aa_audit if item["aa_id"] == row["id"]), None)
            category = allocated["category"] if allocated else "未分配AA冲减"
            monthly[month]["expense"] -= row["_cents"]
            public_entries.append({
                "date": row["date"], "source": row["source"], "kind": "AA补回",
                "category": category, "amount": yuan(row["_cents"]),
                "expense_contribution": yuan(-row["_cents"]), "income_contribution": 0.0,
            })
        elif row["id"] in linked_resale_ids:
            audit_item = next(item for item in resale_audit if item["resale_id"] == row["id"])
            category = expense_category(expense_by_id[audit_item["purchase_id"]])
            monthly[month]["expense"] -= row["_cents"]
            public_entries.append({
                "date": row["date"], "source": row["source"], "kind": "资产转售冲减",
                "category": category, "amount": yuan(row["_cents"]),
                "expense_contribution": yuan(-row["_cents"]), "income_contribution": 0.0,
            })
    for row in true_income_rows:
        category = income_category(row)
        monthly[row["_day"].strftime("%Y-%m")]["income"] += row["_cents"]
        public_entries.append({
            "date": row["date"], "source": row["source"], "kind": "真实收入",
            "category": category, "amount": yuan(row["_cents"]),
            "expense_contribution": 0.0, "income_contribution": yuan(row["_cents"]),
        })

    monthly_rows = [
        {
            "month": month, "actual_expense": yuan(values["expense"]),
            "true_income": yuan(values["income"]),
            "net": yuan(values["income"] - values["expense"]),
        }
        for month, values in sorted(monthly.items())
    ]

    travel_rows = []
    if travel_path:
        tagged: dict[str, int] = defaultdict(int)
        for entry in public_entries:
            # Public entries intentionally omit IDs. Use the source transactions for tagging.
            pass
        for row in rows:
            travel_id = row.get("travel_id", "")
            if not travel_id:
                continue
            if row["direction"] == "expense":
                tagged[travel_id] += row["_cents"]
            elif row["id"] in matched_refund_lookup or row["id"] in aa_ids or row["id"] in linked_resale_ids:
                tagged[travel_id] -= row["_cents"]
        for item in read_csv_any(travel_path):
            trip_start = parse_day(item.get("start_date", ""))
            trip_end = parse_day(item.get("end_date", ""))
            status = (item.get("status") or "").strip().lower()
            overlaps = trip_end >= start and trip_start <= end
            complete_in_range = status == "completed" and trip_start >= start and trip_end <= end
            personal = money_to_cents(item.get("personal_net_expense", "0"))
            if not overlaps:
                continue
            cross = tagged.get((item.get("travel_id") or "").strip(), 0)
            travel_rows.append({
                "travel_id": (item.get("travel_id") or "").strip(),
                "name": (item.get("name") or "").strip(),
                "start_date": trip_start.isoformat(), "end_date": trip_end.isoformat(),
                "status": status, "included": complete_in_range,
                "personal_net_expense": yuan(personal) if complete_in_range else 0.0,
                "ledger_tagged_net": yuan(cross),
                "difference": yuan(personal - cross) if complete_in_range else 0.0,
                "reconciliation": "ok" if complete_in_range and abs(personal - cross) <= 100 else (
                    "review" if complete_in_range else "excluded_or_boundary"
                ),
            })

    top_category = categories[0]["category"] if categories else ""
    summary = {
        "period": {"start": start.isoformat(), "end": end.isoformat(), "currency": "CNY"},
        "counts": {
            "cleaned_transactions": len(rows),
            "expenses": len(expenses),
            "true_income": len(true_income_rows),
            "unmatched_refunds": sum(item["status"] == "unmatched" for item in refund_rows),
            "unallocated_aa": sum(item["status"] == "unallocated" for item in aa_audit),
            "internal_transfers_excluded": len(internal_transfers),
        },
        "totals": {
            "gross_expense": yuan(gross_expense),
            "matched_refunds": yuan(matched_refund_total),
            "aa_reimbursements": yuan(aa_total),
            "unallocated_aa": yuan(unallocated_aa),
            "linked_asset_resale": yuan(linked_resale_total),
            "actual_expense": yuan(actual_expense),
            "true_income": yuan(true_income),
            "net": yuan(true_income - actual_expense),
        },
        "top_expense_category": top_category,
        "categories": categories,
        "incomes": incomes,
        "monthly": monthly_rows,
        "travel": sorted(travel_rows, key=lambda item: item["personal_net_expense"], reverse=True),
        "travel_total_information_only": round(sum(item["personal_net_expense"] for item in travel_rows), 2),
        "public_entries": sorted(public_entries, key=lambda item: (item["date"], item["amount"]), reverse=True),
    }

    cleaned_rows = []
    for row in rows:
        cleaned_rows.append({
            "id": row["id"], "date": row["date"], "time": row["time"], "source": row["source"],
            "direction": row["direction"], "amount": yuan(row["_cents"]),
            "category": expense_category(row) if row["direction"] == "expense" else (
                income_category(row) if row["direction"] == "income" else "退款"
            ),
            "transaction_id": row.get("transaction_id", ""),
            "related_order_id": row.get("related_order_id", ""),
            "account": row.get("account", ""), "payment_method": row.get("payment_method", ""),
            "counterparty": row.get("counterparty", ""), "description": row.get("description", ""),
            "asset_id": row.get("asset_id", ""), "travel_id": row.get("travel_id", ""),
            "status": row.get("status", ""), "notes": row.get("notes", ""),
        })
    tables = {
        "cleaned": cleaned_rows,
        "refunds": refund_rows,
        "aa": aa_audit,
        "resale": resale_audit,
        "categories": categories,
        "incomes": incomes,
        "monthly": monthly_rows,
        "travel": travel_rows,
    }
    return summary, tables


HTML_TEMPLATE = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>个人财务全量分析</title>
<style>
:root{--bg:#f4f6f8;--card:#fff;--ink:#14213d;--muted:#687386;--accent:#0b7a75;--warm:#f4a261;--red:#c8553d;--line:#e4e8ee}
*{box-sizing:border-box}body{margin:0;background:var(--bg);font:14px/1.55 system-ui,-apple-system,"PingFang SC",sans-serif;color:var(--ink)}
main{max-width:1240px;margin:auto;padding:24px}.hero{display:flex;justify-content:space-between;gap:20px;align-items:end;margin-bottom:18px}
h1{font-size:28px;margin:0}.sub{color:var(--muted)}.filters{display:flex;gap:8px;flex-wrap:wrap}
input,select{border:1px solid var(--line);border-radius:9px;padding:8px;background:#fff}.grid{display:grid;grid-template-columns:repeat(12,1fr);gap:14px}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px;box-shadow:0 3px 14px #14213d0b}
.kpi{grid-column:span 3}.half{grid-column:span 6}.full{grid-column:1/-1}.value{font-size:25px;font-weight:750}.label,.hint{color:var(--muted)}
.bars{display:grid;gap:9px}.barrow{display:grid;grid-template-columns:105px 1fr 90px;gap:8px;align-items:center}.track{height:9px;background:#edf0f4;border-radius:20px;overflow:hidden}.fill{height:100%;background:var(--accent)}
.pie{width:190px;height:190px;border-radius:50%;margin:auto}.split{display:grid;grid-template-columns:220px 1fr;gap:16px}.legend{max-height:220px;overflow:auto}
svg{width:100%;height:240px}.axis{stroke:#d8dde5;stroke-width:1}.expenseLine{fill:none;stroke:var(--red);stroke-width:3}.incomeLine{fill:none;stroke:var(--accent);stroke-width:3}
table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:9px;border-bottom:1px solid var(--line)}th{position:sticky;top:0;background:#fff}.tablewrap{max-height:420px;overflow:auto}.num{text-align:right;font-variant-numeric:tabular-nums}
.pill{display:inline-block;border-radius:99px;background:#e9f5f4;padding:2px 8px}.neg{color:var(--red)}.pos{color:var(--accent)}
@media(max-width:850px){.kpi,.half{grid-column:1/-1}.hero,.split{display:block}.filters{margin-top:12px}.pie{margin-bottom:18px}}
</style>
</head>
<body><main>
<section class="hero"><div><h1>个人财务全量分析</h1><div class="sub" id="period"></div></div>
<div class="filters"><input type="date" id="start"><input type="date" id="end"><select id="flow"><option value="all">全部流水</option><option value="expense">仅支出及冲减</option><option value="income">仅真实收入</option></select><select id="sort"><option value="desc">金额从大到小</option><option value="asc">金额从小到大</option></select></div></section>
<div class="grid">
<section class="card kpi"><div class="label">实际总支出</div><div class="value" id="expense"></div><div class="hint">已扣退款、AA、转售</div></section>
<section class="card kpi"><div class="label">真实总收入</div><div class="value" id="income"></div><div class="hint">不含内部转账与补回</div></section>
<section class="card kpi"><div class="label">收支净额</div><div class="value" id="net"></div></section>
<section class="card kpi"><div class="label">当前第一支出板块</div><div class="value" id="topCategory"></div></section>
<section class="card full"><h2>月度收支趋势</h2><svg id="trend" viewBox="0 0 900 240"></svg></section>
<section class="card half"><h2>支出分类占比</h2><div class="split"><div class="pie" id="pie"></div><div class="legend bars" id="categories"></div></div></section>
<section class="card half"><h2>收入来源分布</h2><div class="bars" id="incomes"></div></section>
<section class="card half"><h2>清洗与调整</h2><div id="adjustments"></div></section>
<section class="card half"><h2>旅游模块</h2><div class="tablewrap"><table><thead><tr><th>行程</th><th>日期</th><th class="num">个人净支出</th><th>核对</th></tr></thead><tbody id="travel"></tbody></table></div><div class="hint">旅游金额只作模块和交叉核对，不重复加到总支出。</div></section>
<section class="card full"><h2>总账明细</h2><div class="tablewrap"><table><thead><tr><th>日期</th><th>来源</th><th>类型</th><th>分类</th><th class="num">金额</th><th class="num">对实际支出影响</th></tr></thead><tbody id="details"></tbody></table></div></section>
</div></main>
<script id="finance-data" type="application/json">__DATA__</script>
<script>
const DATA=JSON.parse(document.getElementById("finance-data").textContent);
const money=n=>new Intl.NumberFormat("zh-CN",{style:"currency",currency:"CNY"}).format(n||0);
const colors=["#0b7a75","#f4a261","#c8553d","#457b9d","#8d6a9f","#6a994e","#bc6c25","#577590","#b56576","#2a9d8f","#e9c46a","#606c38"];
const $=id=>document.getElementById(id);
$("start").value=DATA.period.start;$("end").value=DATA.period.end;$("period").textContent=DATA.period.start+" 至 "+DATA.period.end;
["start","end","flow","sort"].forEach(id=>$(id).addEventListener("change",render));
function selected(){
  const a=$("start").value,b=$("end").value,flow=$("flow").value;
  return DATA.public_entries.filter(x=>x.date>=a&&x.date<=b&&(flow==="all"||(flow==="expense"&&x.expense_contribution!==0)||(flow==="income"&&x.income_contribution!==0)));
}
function groups(rows,key,valueKey){const out={};rows.forEach(x=>{out[x[key]]=(out[x[key]]||0)+Number(x[valueKey]||0)});return out}
function barRows(target,items,valueKey){
  const factor=$("sort").value==="desc"?-1:1,sorted=items.slice().sort((a,b)=>factor*(a[valueKey]-b[valueKey])),max=Math.max(1,...sorted.map(x=>Math.abs(x[valueKey])));
  $(target).innerHTML=sorted.map((x,i)=>'<div class="barrow"><span>'+x.category+'</span><span class="track"><span class="fill" style="display:block;width:'+Math.abs(x[valueKey])/max*100+'%;background:'+colors[i%colors.length]+'"></span></span><span class="num">'+money(x[valueKey])+'</span></div>').join("")||'<div class="hint">该区间没有数据</div>';
}
function trend(rows){
  const months={},income={};
  rows.forEach(x=>{const m=x.date.slice(0,7);income[m]=(income[m]||0)+x.income_contribution;months[m]=(months[m]||0)+x.expense_contribution});
  const labels=[...new Set(rows.map(x=>x.date.slice(0,7)))].sort(),vals=labels.flatMap(m=>[months[m]||0,income[m]||0]),max=Math.max(1,...vals),min=Math.min(0,...vals),range=max-min;
  const points=(obj)=>labels.map((m,i)=>{const x=40+(labels.length===1?410:i*820/(labels.length-1));const y=205-(Number(obj[m]||0)-min)/range*170;return x+","+y}).join(" ");
  $("trend").innerHTML='<line class="axis" x1="40" y1="205" x2="860" y2="205"></line><polyline class="expenseLine" points="'+points(months)+'"></polyline><polyline class="incomeLine" points="'+points(income)+'"></polyline><text x="45" y="22" fill="#c8553d">支出</text><text x="95" y="22" fill="#0b7a75">收入</text>'+labels.filter((_,i)=>i%Math.max(1,Math.ceil(labels.length/8))===0).map((m,i)=>'<text x="'+(45+i*100)+'" y="226" fill="#687386" font-size="11">'+m+'</text>').join("");
}
function render(){
  let rows=selected(),expense=rows.reduce((s,x)=>s+x.expense_contribution,0),income=rows.reduce((s,x)=>s+x.income_contribution,0);
  $("expense").textContent=money(expense);$("income").textContent=money(income);$("net").textContent=money(income-expense);
  const catMap=groups(rows.filter(x=>x.expense_contribution!==0),"category","expense_contribution");
  const cats=Object.entries(catMap).map(([category,net])=>({category,net})).sort((a,b)=>b.net-a.net);
  $("topCategory").textContent=(cats.find(x=>x.net>0)||{category:"—"}).category;
  barRows("categories",cats,"net");
  const positive=cats.filter(x=>x.net>0),total=positive.reduce((s,x)=>s+x.net,0),stops=[];let cursor=0;
  positive.forEach((x,i)=>{const end=cursor+(total?x.net/total*360:0);stops.push(colors[i%colors.length]+" "+cursor+"deg "+end+"deg");cursor=end});
  $("pie").style.background=stops.length?"conic-gradient("+stops.join(",")+")":"#edf0f4";
  const incomeMap=groups(rows.filter(x=>x.income_contribution!==0),"category","income_contribution");
  barRows("incomes",Object.entries(incomeMap).map(([category,amount])=>({category,amount})),"amount");
  trend(rows);
  const adjustment=(kind)=>rows.filter(x=>x.kind===kind).reduce((s,x)=>s+x.amount,0);
  $("adjustments").innerHTML='<table><tr><td>退款冲减</td><td class="num">'+money(adjustment("退款冲减"))+'</td></tr><tr><td>AA/群收款补回</td><td class="num">'+money(adjustment("AA补回"))+'</td></tr><tr><td>资产转售冲减</td><td class="num">'+money(adjustment("资产转售冲减"))+'</td></tr><tr><td>全周期未匹配退款笔数</td><td class="num">'+DATA.counts.unmatched_refunds+'</td></tr></table>';
  const trips=DATA.travel.filter(x=>x.end_date>=$("start").value&&x.start_date<=$("end").value);
  $("travel").innerHTML=trips.map(x=>'<tr><td>'+x.name+'</td><td>'+x.start_date+'～'+x.end_date+'</td><td class="num">'+money(x.personal_net_expense)+'</td><td><span class="pill">'+x.reconciliation+'</span></td></tr>').join("")||'<tr><td colspan="4" class="hint">该区间没有旅游记账</td></tr>';
  const desc=$("sort").value==="desc";rows=rows.slice().sort((a,b)=>(desc?-1:1)*(Math.abs(a.amount)-Math.abs(b.amount)));
  $("details").innerHTML=rows.map(x=>'<tr><td>'+x.date+'</td><td>'+x.source+'</td><td>'+x.kind+'</td><td>'+x.category+'</td><td class="num">'+money(x.amount)+'</td><td class="num '+(x.expense_contribution<0?"pos":"")+'">'+money(x.expense_contribution)+'</td></tr>').join("");
}
render();
</script></body></html>"""


def write_outputs(output: Path, summary: dict, tables: dict[str, list[dict]], dedupe_audit: list[dict]) -> None:
    output.mkdir(parents=True, exist_ok=True)
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    write_csv(output / "cleaned_transactions.csv", [
        "id", "date", "time", "source", "direction", "amount", "category",
        "transaction_id", "related_order_id", "account", "payment_method",
        "counterparty", "description", "asset_id", "travel_id", "status", "notes",
    ], tables["cleaned"])
    write_csv(output / "dedupe_audit.csv", [
        "action", "kept_id", "removed_or_candidate_id", "reason", "amount", "date",
    ], dedupe_audit)
    write_csv(output / "refund_matches.csv", [
        "refund_id", "expense_id", "status", "refund_date", "amount", "reason",
    ], tables["refunds"])
    write_csv(output / "aa_offsets.csv", [
        "aa_id", "expense_id", "date", "amount", "status", "category",
    ], tables["aa"])
    write_csv(output / "asset_resale_audit.csv", [
        "resale_id", "purchase_id", "asset_id", "amount", "status",
    ], tables["resale"])
    write_csv(output / "category_summary.csv", ["category", "gross", "net", "share"], tables["categories"])
    write_csv(output / "income_summary.csv", ["category", "amount", "share"], tables["incomes"])
    write_csv(output / "monthly_summary.csv", ["month", "actual_expense", "true_income", "net"], tables["monthly"])
    if tables["travel"]:
        write_csv(output / "travel_summary.csv", [
            "travel_id", "name", "start_date", "end_date", "status", "included",
            "personal_net_expense", "ledger_tagged_net", "difference", "reconciliation",
        ], tables["travel"])
    dashboard = output / "dashboard"
    dashboard.mkdir(exist_ok=True)
    embedded = json.dumps(summary, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    (dashboard / "index.html").write_text(HTML_TEMPLATE.replace("__DATA__", embedded), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze a privacy-conscious normalized personal ledger.")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--start", required=True, type=parse_day)
    parser.add_argument("--end", required=True, type=parse_day)
    parser.add_argument("--travel", type=Path)
    args = parser.parse_args()
    if args.end < args.start:
        parser.error("--end must be on or after --start")
    rows = load_transactions(args.input.expanduser().resolve(), args.start, args.end)
    cleaned, dedupe_audit = deduplicate(rows)
    summary, tables = analyze(
        cleaned,
        args.travel.expanduser().resolve() if args.travel else None,
        args.start,
        args.end,
    )
    summary["counts"].update({
        "raw_transactions_in_period": len(rows),
        "removed_duplicates": sum(item["action"].startswith("removed_") for item in dedupe_audit),
        "excluded_status": sum(item["action"] == "excluded_status" for item in dedupe_audit),
        "dedupe_candidates_kept": sum(item["action"] == "candidate_kept" for item in dedupe_audit),
    })
    write_outputs(args.output.expanduser().resolve(), summary, tables, dedupe_audit)
    print(json.dumps({
        "output": str(args.output.expanduser().resolve()),
        "actual_expense": summary["totals"]["actual_expense"],
        "true_income": summary["totals"]["true_income"],
        "net": summary["totals"]["net"],
        "top_expense_category": summary["top_expense_category"],
        "candidate_dedupes_kept": sum(item["action"] == "candidate_kept" for item in dedupe_audit),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
