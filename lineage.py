#!/usr/bin/env python3
"""lineage.py — 解析支付两跳映射血缘。

渠道 -> 商户 (mappings/cm.csv)，商户 -> 结算账户 (mappings/ma.csv)。
区间按 [from_day, to_day) 左闭右开生效，端点相接日归后一版；
同一 key 区间重叠覆盖同一时刻时取版本号大的一版。
corrections.csv 的修正件（生效早于落库，需回溯应用）先改动区间端点，
版本轴重排后再解析 settlements.csv，输出 resolve.csv 与 audit.txt。
"""

import csv
from datetime import datetime

MAPPINGS = {"cm": "mappings/cm.csv", "ma": "mappings/ma.csv"}


def ver_num(ver):
    return int(ver.lstrip("vV"))


def load_mapping(path):
    """key -> [ {from_day, to_day, ver, vnum, val}, ... ]，日期为 ISO 字符串可直接比较。"""
    table = {}
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            table.setdefault(row["key"], []).append({
                "from_day": row["from_day"],
                "to_day": row["to_day"],
                "ver": row["ver"],
                "vnum": ver_num(row["ver"]),
                "val": row["val"],
            })
    return table


def apply_corrections(tables, path):
    """应用修正件，返回 CORR 审计行（按修正件顺序）。"""
    lines = []
    with open(path, newline="") as f:
        for corr in csv.DictReader(f):
            table = tables[corr["file"]]
            hit = None
            for iv in table.get(corr["key"], []):
                if iv["ver"] == corr["ver"]:
                    hit = iv
                    break
            if hit is None:
                continue
            if corr["field"] == "from":
                hit["from_day"] = corr["new_day"]
            else:
                hit["to_day"] = corr["new_day"]
            lines.append("CORR {} {} {} {} {}".format(
                corr["file"], corr["key"], corr["ver"],
                hit["from_day"], hit["to_day"]))
    return lines


def resolve(table, key, day):
    """返回 day 时刻生效的区间；重叠时取版本号大的一版。"""
    best = None
    for iv in table.get(key, []):
        if iv["from_day"] <= day < iv["to_day"]:
            if best is None or iv["vnum"] > best["vnum"]:
                best = iv
    return best


def main():
    tables = {name: load_mapping(path) for name, path in MAPPINGS.items()}

    # 修正件回溯应用后，版本轴重排
    corr_lines = apply_corrections(tables, "corrections.csv")
    for table in tables.values():
        for ivs in table.values():
            ivs.sort(key=lambda iv: (iv["from_day"], iv["vnum"]))

    resolve_rows = []
    miss_lines = []
    with open("settlements.csv", newline="") as f:
        for pay in csv.DictReader(f):
            day = datetime.strptime(pay["ts"], "%Y-%m-%d %H:%M:%S").date().isoformat()
            cm = resolve(tables["cm"], pay["channel"], day)
            if cm is None:
                resolve_rows.append([pay["txn"], "", "", "", "MISS_CM"])
                miss_lines.append("MISS_CM {} {}".format(pay["txn"], pay["channel"]))
                continue
            ma = resolve(tables["ma"], cm["val"], day)
            if ma is None:
                resolve_rows.append([pay["txn"], "", cm["ver"], "", "MISS_MA"])
                miss_lines.append("MISS_MA {} {}".format(pay["txn"], cm["val"]))
                continue
            resolve_rows.append([pay["txn"], ma["val"], cm["ver"], ma["ver"], "OK"])

    with open("resolve.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["txn", "account", "cm_ver", "ma_ver", "flag"])
        w.writerows(resolve_rows)

    with open("audit.txt", "w") as f:
        for line in miss_lines + corr_lines:
            f.write(line + "\n")


if __name__ == "__main__":
    main()
