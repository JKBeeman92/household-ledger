#!/usr/bin/env python3 -I
"""Proposed canonical mapping for every (Group, Item). Edit item_mapping_proposed.csv, keep as item_mapping.csv."""
import csv, re as _re
def norm(x): return _re.sub(r'\s+',' ',x.replace('\u2006',' ')).strip()

# Item -> (CanonicalItem, Category, Flow)
# Flow: income | expense | debt | transfer | plug | zero (never had txns; drop)
M = {
 # --- Income ---
 "Paycheck 1":("Paycheck","Income","income"), "Paycheck 2":("Paycheck","Income","income"), "Paycheck 3":("Paycheck","Income","income"),
 "CJ Paycheck":("CJ Income","Income","income"), "CJ Casper Pay":("CJ Income","Income","income"), "CJ Income":("CJ Income","Income","income"),
 "Bonus":("Bonus","Income","income"), "Josh Bonus":("Bonus","Income","income"), "Josh Bonus Check":("Bonus","Income","income"),
 "NCM Bonus":("Bonus","Income","income"), "ExtraHop Bonus":("Bonus","Income","income"),
 "Christmas Bonus":("Year-End Bonus","Income","income"), "Holiday Bonus":("Year-End Bonus","Income","income"),
 "Tax Return":("Tax Refund","Income","income"), "Tax Refund":("Tax Refund","Income","income"), "Tax Returns":("Tax Refund","Income","income"),
 "Tax Returns / Payments":("Tax Refund","Income","income"),
 "Stimulus":("Stimulus","Income","income"), "Severance":("Severance","Income","income"),
 "Retirement Income":("401k Cash-out","Income","income"), "UBS Payout":("401k Cash-out","Income","income"),
 "Wealthfront Interest":("Interest","Income","income"), "Savings Interest":("Interest","Income","income"), "Interest":("Interest","Income","income"),
 "Bonds":("Investment Proceeds","Income","income"), "Stock Sale":("Investment Proceeds","Income","income"),
 "Side Work":("Other Income","Income","income"), "Yard Sales":("Other Income","Income","income"), "Garage Sale":("Other Income","Income","income"),
 "Gifts":("Other Income","Income","income"), "Sponge Income":("Other Income","Income","income"), "Window Incentive":("Other Income","Income","income"),
 "Escrow Refund":("Refund","Income","income"), "Apartment Check":("Refund","Income","income"), "Plaza Club City Return":("Refund","Income","income"),
 "Boa":("Boa (VERIFY)","Income","income"),
 "Slush":("Slush (VERIFY)","Income","income"),
 "Back Pay":("Back Pay","Income","zero"),
 # income-typed transfers (NOT income)
 "Fund Rebalance":("Transfer","Transfers","transfer"), "Fund Transfer":("Transfer","Transfers","transfer"),
 "Find Transfer":("Transfer","Transfers","transfer"), "Transfer":("Transfer","Transfers","transfer"),
 "Transfer Funds":("Transfer","Transfers","transfer"), "Cj Savings":("Transfer","Transfers","transfer"),
 # family loans (both legs excluded from income/expense; shown separately)
 "Parent's Loan":("Family Loan","Family Loan","transfer"), "Money From Parents":("Family Loan","Family Loan","transfer"),
 "Loan from Parents":("Family Loan","Family Loan","transfer"), "Loan To Parents":("Family Loan","Family Loan","transfer"),
 # --- Debt ---
 "My Loan":("Student Loan (pre-refi)","Debt","debt"), "Mom's Loan":("Mom's Loan","Debt","debt"),
 "Great Lakes":("Great Lakes","Debt","debt"), "SoFi Loan":("SoFi Loan","Debt","debt"),
 "Medical Bill":("Medical Debt","Debt","debt"), "Medical":("Medical Debt","Debt","debt"), "Theo Birth Bill":("Medical Debt","Debt","debt"),
 "Sleep Number Bed 2":("Sleep Number Bed","Debt","debt"), "Sleep Number Bed":("Sleep Number Bed","Debt","debt"),
 "Solar Panel Loan":("Solar Panel","Debt","debt"), "Solar Panel":("Solar Panel","Debt","debt"),
 "Subaru":("Subaru","Debt","debt"), "Windows / Doors":("Windows / Doors","Debt","debt"), "Puragain":("Puragain","Debt","debt"),
 # --- Taxes ---
 "Tax":("Taxes","Taxes","expense"), "Tax Payments":("Taxes","Taxes","expense"), "Tax Payments 2":("Taxes","Taxes","expense"), "Taxes":("Taxes","Taxes","expense"),
 # --- Housing ---
 "Rent":("Rent","Housing","expense"), "Mortgage":("Mortgage","Housing","expense"), "Storage Fees":("Rent","Housing","expense"),
 "HOA Fees":("HOA","Housing","expense"), "Laundry":("Laundry","Housing","expense"), "Moving Costs":("Moving","Housing","expense"),
 "Electricity /Water Fund":("Utilities","Utilities","expense"), "Waste Management":("Utilities","Utilities","expense"),
 "Internet":("Internet","Utilities","expense"), "Internet 2":("Internet","Utilities","expense"),
 "Mobile Phone":("Mobile Phone","Utilities","expense"),
 # --- Food ---
 "Grocery / IsaGenix":("Grocery","Groceries","expense"), "Grocery 2":("Grocery","Groceries","expense"), "Grocery":("Grocery","Groceries","expense"),
 "Date 2":("Date","Dining & Date","expense"), "Date":("Date","Dining & Date","expense"),
 "Hospitality Fund":("Hospitality","Dining & Date","expense"),
 # --- Personal ---
 "Josh Miscellaneous Fund":("Josh Misc","Personal","expense"), "CJ Miscellaneous Fund":("CJ Misc","Personal","expense"),
 "Clothing Fund":("Clothing","Personal","expense"), "Hygiene Fund":("Hygiene","Personal","expense"),
 "Keep It Cut":("Haircuts","Personal","expense"), "Keeps":("Personal Care","Personal","expense"),
 # --- Household ---
 "House Misc Funds":("House Misc","Household","expense"), "Cleaning Supplies Fund":("Cleaning Supplies","Household","expense"),
 "Puppy Stuff":("Pets","Household","expense"), "Blue Sky Pest":("Pest Control","Household","expense"), "ADT":("Home Security","Household","expense"),
 # --- Transportation ---
 "Gas":("Fuel","Transportation","expense"), "Car Repairs Fund":("Car Maintenance","Transportation","expense"),
 # --- Health ---
 "Health Savings Fund":("Health","Health","expense"), "Meds Fund":("Meds","Health","expense"),
 "Gym Fund":("Gym","Health","expense"), "Gym":("Gym","Health","expense"), "Fit App":("Gym","Health","expense"),
 "Mom & Dad Trauma Fund":("Mom & Dad Trauma Fund","Excluded","exclude"),  # pass-through tracked for parents, excluded entirely
 # --- Insurance ---
 "Car Insurance":("Auto/Home Insurance","Insurance","expense"), "State Farm Insurance 2":("Auto/Home Insurance","Insurance","expense"),
 "State Farm Insurance":("Auto/Home Insurance","Insurance","expense"), "Farmers Insurance":("Auto/Home Insurance","Insurance","expense"),
 "Renter's Insurance":("Renter's Insurance","Insurance","expense"), "Fire Insurance":("Fire Insurance","Insurance","expense"),
 "Home Warranty":("Home Warranty","Insurance","expense"), "Life Insurance":("Life Insurance","Insurance","expense"),
 "Disability Insurance Fund":("Disability Insurance","Insurance","expense"), "Disability Insurance 2":("Disability Insurance","Insurance","expense"),
 "Disability Insurance":("Disability Insurance","Insurance","expense"),
 "Identity Theft Insurance 3":("Identity Theft","Insurance","expense"), "Identity Theft Insurance 2":("Identity Theft","Insurance","zero"),
 "Identity Theft Insurance":("Identity Theft","Insurance","expense"), "Identity Theft 2":("Identity Theft","Insurance","expense"),
 "Identity Theft":("Identity Theft","Insurance","expense"),
 # --- Giving ---
 "Church":("Church","Giving","expense"), "Voices":("Voices","Giving","expense"), "Children International":("Children International","Giving","expense"),
 "Generosity Tithe":("Generosity","Giving","expense"),
 # --- Subscriptions ---
 "Spotify Fund":("Spotify","Subscriptions","expense"), "Spotify":("Spotify","Subscriptions","expense"),
 "Netflix":("Netflix","Subscriptions","expense"), "Hulu":("Streaming","Subscriptions","expense"), "Disney Bundle":("Streaming","Subscriptions","expense"),
 "Crunchyroll":("Streaming","Subscriptions","expense"), "YouTube Premium":("Streaming","Subscriptions","expense"),
 "Xbox Live":("Xbox","Subscriptions","expense"), "Xbox Game Pass":("Xbox","Subscriptions","expense"),
 "Audible":("Audible","Subscriptions","expense"), "Amazon Prime":("Amazon Prime","Subscriptions","expense"),
 "Office":("Microsoft Office","Subscriptions","expense"), "1Password":("Software","Subscriptions","expense"),
 "Everydollar Plus":("EveryDollar","Subscriptions","expense"), "Emeals":("Emeals","Subscriptions","expense"),
 "Shockbyte":("Minecraft Server","Subscriptions","expense"), "Buzzsprout":("Podcast Hosting","Subscriptions","expense"),
 "Salesforce Dev Tools":("Dev Tools","Subscriptions","expense"), "Chat GPT":("AI","Subscriptions","expense"), "AI":("AI","Subscriptions","zero"),
 "Arizona Republic":("News","Subscriptions","expense"), "First Things":("News","Subscriptions","expense"), "World News":("News","Subscriptions","expense"),
 "AC":("AC Service Plan","Subscriptions","expense"),
 # --- Sinking funds / goals ---
 "Emergency Fund":("Emergency Fund","Savings","expense"), "Long Term Savings":("Long Term Savings (slush)","Savings","expense"),
 "Retirement":("Retirement","Savings","zero"), "Emergency Fund 2":("Emergency Fund","Savings","zero"),
 "Tech Replacement/Repair Fund":("Technology","Technology","expense"), "Technology Replacement / Repair 2":("Technology","Technology","expense"),
 "Technology Replacement / Repair":("Technology","Technology","expense"),
 "Holiday Fund 2":("Holiday","Gifts & Holiday","expense"), "Holiday Fund":("Holiday","Gifts & Holiday","expense"),
 "Gifts Fund 2":("Gifts","Gifts & Holiday","expense"), "Gifts Fund":("Gifts","Gifts & Holiday","expense"),
 "Vacation Fund 2":("Vacation","Travel","expense"), "Vacation Fund":("Vacation","Travel","expense"),
 "Leah's Bachelorette Party":("Events","Travel","expense"), "CJ Birthday Flight":("Vacation","Travel","expense"),
 # --- House purchase & projects ---
 "Down Payment Fund":("Down Payment","House Purchase","expense"), "Down Payment":("Down Payment","House Purchase","expense"),
 "New Bed Fund":("New Bed","Projects","expense"), "New Bed":("New Bed","Projects","expense"),
 "Couch":("Furniture","Projects","expense"), "Living Room Updates":("Living Room","Projects","expense"),
 "Office Project Finishing":("Office Project","Projects","expense"), "Garage Improvements":("Garage","Projects","expense"),
 "Backyard Ground Leveling / Pipes":("Backyard","Projects","expense"), "Fire Pit":("Backyard","Projects","expense"),
 "Laser Hair Removal":("Laser Hair Removal","Projects","expense"), "CJ Education":("CJ Education","Projects","expense"),
 "Josh Commuter":("Josh Commuter","Projects","expense"),
 # --- plugs ---
 "End of Year Rebalance":("Plug","Plugs","plug"),
}
ZERO_PREFIXES = ("Car Replacement","Car Upgrade","Down Payment 2","Down Payment 3","Josh Tattoo","Laser Hair Removal 2","Laser Hair Removal 3",
                 "Laser Hair Removal 4","Laser Hair Removal Goal","New Bed 2","New Bed 3","Backyard Kitchen","Backyard RV Fence","Bedroom Update",
                 "Exterior Paint","Front Yard","Jacuzzi Project","Roof Repair")

rows=list(csv.DictReader(open("item_inventory.csv")))
with open("item_mapping_proposed.csv","w",newline="") as out:
    w=csv.writer(out); w.writerow(["Group","Item","FirstBM","LastBM","TxnCount","NetAmount","CanonicalItem","Category","Flow","Note"])
    for r in rows:
        it=norm(r["Item"]); note=""
        if it in M: ci,cat,flow=M[it]
        elif it.startswith(ZERO_PREFIXES) and r["TxnCount"]=="0": ci,cat,flow=(it,"Goals (placeholder)","zero")
        else: ci,cat,flow=(it,"UNMAPPED","expense"); note="no rule - review"
        if r["TxnCount"]=="0" and flow!="zero": note="never had a transaction"
        w.writerow([r["Group"],it,r["FirstBM"],r["LastBM"],r["TxnCount"],r["NetAmount"],ci,cat,flow,note])
import collections
c=collections.Counter();
for r in csv.DictReader(open("item_mapping_proposed.csv")): c[r["Category"]]+=1
print(dict(c))
