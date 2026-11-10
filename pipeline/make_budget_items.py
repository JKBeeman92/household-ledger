#!/usr/bin/env python3 -I
"""Planned vs spent per envelope per budget month, from summaries/ + item_mapping.csv -> dash_data/budget_items.json"""
import csv, os, re, json
def norm(x): return re.sub(r"\s+"," ",x.replace(" "," ")).strip()
rx=re.compile(r"^[0-9a-f]+-(\d\d)-(\d{4})-EveryDollar-BudgetItems\.csv$")
mp={norm(r["Item"]):r for r in csv.DictReader(open("item_mapping.csv"))}
rows=[]
for fn in sorted(os.listdir("summaries")):
    m=rx.match(fn)
    if not m: continue
    bm=f"{m.group(2)}-{m.group(1)}"
    for r in csv.DictReader(open("summaries/"+fn,newline="",encoding="utf-8-sig")):
        it=norm(r["Item"]); M=mp[it]
        if M["Flow"] in ("exclude","zero","plug","verify","transfer"): continue
        planned=float(r["Planned"]); spent=float(r["Spent"])
        p,s=(planned,spent) if M["Flow"]=="income" else (planned,-spent)
        rows.append({"m":bm,"group":norm(r["Group"]),"item":it,"canonical":M["CanonicalItem"],"category":M["Category"],"flow":M["Flow"],"planned":round(p,2),"actual":round(s,2)})
os.makedirs("dash_data",exist_ok=True)
json.dump(rows,open("dash_data/budget_items.json","w"),separators=(",",":"))
print(len(rows),"rows")
