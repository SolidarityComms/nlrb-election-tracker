#!/usr/bin/env python3
"""
NLRB Election Tracker — scraper
Pulls from two NLRB pages:
  1. Recent Filings  -> petitions as filed
  2. Election Results -> tallies as issued
Merges on case number, writes data.json
"""

import requests
from bs4 import BeautifulSoup
import json
import os
import time
import argparse
from datetime import datetime, timedelta

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; research-bot/1.0)"}
DATA_FILE = "data.json"
FILINGS_URL = "https://www.nlrb.gov/reports/graphs-data/recent-filings"
RESULTS_URL = "https://www.nlrb.gov/reports/graphs-data/recent-election-results"


def fetch(url, page=0, retries=3):
    params = {"page": page}
    for attempt in range(retries):
        try:
            r = requests.get(url, headers=HEADERS, params=params, timeout=30)
            r.raise_for_status()
            return r.text
        except Exception as e:
            print(f"  Fetch error ({attempt+1}/{retries}): {e}")
            time.sleep(3 * (attempt + 1))
    return None


def parse_date(s):
    if not s:
        return None
    s = s.strip()
    for fmt in ("%m/%d/%Y", "%d %B %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return s


def scrape_filings(days_back=90):
    print(f"Scraping filings (last {days_back} days)...")
    cutoff = datetime.now() - timedelta(days=days_back)
    results = {}
    page = 0
    max_pages = 50 if days_back >= 90 else 5
    found_old = False

    while page < max_pages and not found_old:
        html = fetch(FILINGS_URL, page=page)
        if not html:
            break

        soup = BeautifulSoup(html, "html.parser")
        page_count = 0

        # Each case is in div.rer-content
        for block in soup.select("div.rer-content"):
            case = {}

            # Employer name is in h3
            h3 = block.find("h3")
            if h3:
                case["employer"] = h3.text.strip()

            # Fields are in div.rer-style-1 with bold labels
            for field in block.select("div.rer-style-1"):
                text = field.get_text(separator="||")
                parts = text.split("||")
                if len(parts) >= 2:
                    label = parts[0].strip().rstrip(":")
                    value = parts[1].strip()
                    if "Case Number" in label:
                        a = field.find("a")
                        if a:
                            case["case_number"] = a.text.strip()
                            case["case_url"] = "https://www.nlrb.gov" + a["href"]
                    elif "Date Filed" in label:
                        case["date_filed"] = parse_date(value)
                    elif "Status" in label:
                        case["status"] = value
                    elif "No Employees" in label or "Eligible Voters" in label:
                        try:
                            case["eligible_voters"] = int(value.replace(",", ""))
                        except ValueError:
                            pass
                    elif "Location" in label:
                        case["location"] = value
                    elif "Region Assigned" in label:
                        case["region"] = value

            # Unit sought is in div.rer-style-3
            unit_div = block.find("div", class_="rer-style-3")
            if unit_div:
                case["unit_sought"] = unit_div.get_text(separator=" ").replace("Unit Sought", "").strip().lstrip(":")

            if "case_number" not in case:
                continue

            # Only keep R-cases
            cn = case["case_number"]
            if not any(t in cn for t in ["-RC-", "-RD-", "-RM-"]):
                continue

            # Derive subtype
            for t in ["RC", "RD", "RM"]:
                if f"-{t}-" in cn:
                    case["case_type"] = t
                    break

            # Check date cutoff
            if case.get("date_filed"):
                try:
                    filed_dt = datetime.strptime(case["date_filed"], "%Y-%m-%d")
                    if filed_dt < cutoff:
                        found_old = True
                        continue
                    days_old = (datetime.now() - filed_dt).days
                    case["stage"] = "just_filed" if days_old <= 14 else "pending"
                except ValueError:
                    case["stage"] = "just_filed"
            else:
                case["stage"] = "just_filed"

            results[cn] = case
            page_count += 1

        print(f"  Page {page}: {page_count} R-cases")

        if found_old:
            print("  Hit cutoff date, stopping.")
            break

        next_btn = soup.select_one("li.pager__item--next a")
        if not next_btn:
            break

        page += 1
        time.sleep(1.5)

    print(f"  Total filings: {len(results)}")
    return results


def scrape_results(days_back=90):
    print(f"Scraping election results (last {days_back} days)...")
    cutoff = datetime.now() - timedelta(days=days_back)
    results = {}
    page = 0
    max_pages = 50 if days_back >= 90 else 5
    found_old = False

    while page < max_pages and not found_old:
        html = fetch(RESULTS_URL, page=page)
        if not html:
            break

        soup = BeautifulSoup(html, "html.parser")
        page_count = 0

        # Each result is also in div.rer-content
        for block in soup.select("div.rer-content"):
            case = {}

            h3 = block.find("h3")
            if h3:
                case["employer"] = h3.text.strip()

            for field in block.select("div.rer-style-1"):
                text = field.get_text(separator="||")
                parts = text.split("||")
                if len(parts) >= 2:
                    label = parts[0].strip().rstrip(":")
                    value = parts[1].strip()

                    if "Case Number" in label:
                        a = field.find("a")
                        if a:
                            case["case_number"] = a.text.strip()
                            case["case_url"] = "https://www.nlrb.gov" + a["href"]
                    elif "Tally Issued" in label:
                        case["tally_date"] = parse_date(value)
                    elif "Date Filed" in label:
                        case["date_filed"] = parse_date(value)
                    elif "Eligible Voters" in label or "No. of Eligible" in label:
                        try:
                            case["eligible_voters"] = int(value.replace(",", ""))
                        except ValueError:
                            pass
                    elif "Votes for Labor Union" in label or "Votes for" in label:
                        try:
                            case["votes_for"] = int(value.replace(",", ""))
                        except ValueError:
                            pass
                    elif "Votes Against" in label:
                        try:
                            case["votes_against"] = int(value.replace(",", ""))
                        except ValueError:
                            pass
                    elif "Total Ballots" in label:
                        try:
                            case["ballots_counted"] = int(value.replace(",", ""))
                        except ValueError:
                            pass
                    elif "Labor Union1" in label and "union" not in case:
                        case["union"] = value
                    elif "Union to Certify" in label:
                        case["union_to_certify"] = value
                    elif "Status" in label:
                        case["status"] = value
                    elif "Tally Type" in label:
                        case["tally_type"] = value
                    elif "Unit Location" in label or "Location" in label:
                        case["location"] = value
                    elif "Region Assigned" in label:
                        case["region"] = value
                    elif "Void Ballots" in label:
                        try:
                            case["void_ballots"] = int(value.replace(",", ""))
                        except ValueError:
                            pass

            if "case_number" not in case:
                continue

            # Subtype
            cn = case["case_number"]
            for t in ["RC", "RD", "RM"]:
                if f"-{t}-" in cn:
                    case["case_type"] = t
                    break

            # Outcome
            vf = case.get("votes_for", 0) or 0
            va = case.get("votes_against", 0) or 0
            ct = case.get("case_type", "RC")
            if vf == va:
                case["outcome"] = "tie"
            elif ct == "RD":
                case["outcome"] = "union_won" if va > vf else "union_lost"
            else:
                case["outcome"] = "union_won" if vf > va else "union_lost"

            # Cutoff check
            tally_str = case.get("tally_date")
            if tally_str:
                try:
                    if datetime.strptime(tally_str, "%Y-%m-%d") < cutoff:
                        found_old = True
                        continue
                except ValueError:
                    pass

            status = case.get("status", "").lower()
            case["stage"] = "certified" if "closed" in status else "tally_issued"

            results[cn] = case
            page_count += 1

        print(f"  Page {page}: {page_count} cases")

        if found_old:
            print("  Hit cutoff date, stopping.")
            break

        next_btn = soup.select_one("li.pager__item--next a")
        if not next_btn:
            break

        page += 1
        time.sleep(1.5)

    print(f"  Total results: {len(results)}")
    return results


def merge(filings, results):
    merged = {}

    for cn, case in filings.items():
        merged[cn] = case.copy()

    for cn, case in results.items():
        if cn in merged:
            merged[cn].update(case)
        else:
            merged[cn] = case.copy()

    for cn, case in merged.items():
        if "tally_date" in case:
            status = case.get("status", "").lower()
            case["stage"] = "certified" if "closed" in status else "tally_issued"
        elif case.get("date_filed"):
            try:
                days = (datetime.now() - datetime.strptime(
                    case["date_filed"], "%Y-%m-%d")).days
                case["stage"] = "just_filed" if days <= 14 else "pending"
            except ValueError:
                case["stage"] = "pending"

    return merged


def load_existing():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE) as f:
            data = json.load(f)
        print(f"Loaded {len(data.get('cases', {}))} existing cases")
        return data
    return {"cases": {}}


def save(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)
    print(f"Saved {len(data['cases'])} cases to {DATA_FILE}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--backfill", action="store_true",
                        help="Scrape last 90 days (first run)")
    args = parser.parse_args()

    days_back = 90 if args.backfill else 3

    existing = load_existing()
    existing_cases = existing.get("cases", {})

    filings = scrape_filings(days_back=days_back)
    results = scrape_results(days_back=days_back)

    new_cases = merge(filings, results)
    existing_cases.update(new_cases)

    output = {
        "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total_cases": len(existing_cases),
        "cases": existing_cases,
    }

    save(output)
    print(f"Done. {len(new_cases)} new/updated, {len(existing_cases)} total.")


if __name__ == "__main__":
    main()
