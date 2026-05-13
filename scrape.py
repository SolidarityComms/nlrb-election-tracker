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


def get_field(item, label):
    """Find a bold label and return the text that follows it."""
    for strong in item.find_all("strong"):
        if label.lower() in strong.text.lower():
            sibling = strong.next_sibling
            if sibling:
                return str(sibling).strip().lstrip(":").strip()
    return None


def scrape_filings(days_back=90):
    print(f"Scraping filings (last {days_back} days)...")
    cutoff = datetime.now() - timedelta(days=days_back)
    results = {}
    page = 0

    while True:
        html = fetch(FILINGS_URL, page=page)
        if not html:
            break

        soup = BeautifulSoup(html, "html.parser")
        
        # Find all case blocks - each filing is a div with a case number link
        case_links = soup.find_all("a", href=lambda h: h and "/case/" in h)
        
        found_old = False
        page_count = 0

        for link in case_links:
            case_number = link.text.strip()
            if not case_number or not any(t in case_number for t in ["-RC-", "-RD-", "-RM-"]):
                continue

            # Get the parent block
            block = link.find_parent("div") or link.find_parent("li") or link.find_parent("article")
            if not block:
                continue

            case = {
                "case_number": case_number,
                "case_url": "https://www.nlrb.gov" + link["href"],
            }

            # Derive subtype
            for t in ["RC", "RD", "RM"]:
                if f"-{t}-" in case_number:
                    case["case_type"] = t
                    break

            # Extract employer name - usually an h3 near the link
            h3 = block.find("h3") or block.find("h2")
            if h3:
                case["employer"] = h3.text.strip()

            # Extract all strong-label fields
            text = block.get_text(separator="\n")
            for line in text.split("\n"):
                line = line.strip()
                if line.startswith("Date Filed:"):
                    case["date_filed"] = parse_date(line.replace("Date Filed:", "").strip())
                elif line.startswith("Status:"):
                    case["status"] = line.replace("Status:", "").strip()
                elif line.startswith("City:") or line.startswith("Unit Location:"):
                    case["location"] = line.split(":", 1)[1].strip()
                elif line.startswith("Region"):
                    case["region"] = line.split(":", 1)[1].strip() if ":" in line else line
                elif line.startswith("Union:") or line.startswith("Labor Union"):
                    case["union"] = line.split(":", 1)[1].strip()
                elif line.startswith("Employees") or line.startswith("No. of Eligible"):
                    try:
                        case["eligible_voters"] = int(
                            line.split(":", 1)[1].strip().replace(",", ""))
                    except (ValueError, IndexError):
                        pass

            # Check cutoff
            if case.get("date_filed"):
                try:
                    filed_dt = datetime.strptime(case["date_filed"], "%Y-%m-%d")
                    if filed_dt < cutoff:
                        found_old = True
                        continue
                except ValueError:
                    pass

            case["stage"] = "just_filed" if (
                datetime.now() - datetime.strptime(
                    case["date_filed"], "%Y-%m-%d")).days <= 14
                else "pending" if case.get("date_filed") else "just_filed"

            results[case_number] = case
            page_count += 1

        print(f"  Page {page}: {page_count} R-cases")

        if found_old and days_back <= 7:
            break

        next_btn = soup.find("a", {"rel": "next"}) or soup.select_one("li.pager__item--next a")
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

    while True:
        html = fetch(RESULTS_URL, page=page)
        if not html:
            break

        soup = BeautifulSoup(html, "html.parser")
        found_old = False
        page_count = 0

        # Each result block starts with an h3 (employer name)
        for h3 in soup.find_all("h3"):
            employer = h3.text.strip()
            if not employer or len(employer) < 2:
                continue

            case = {"employer": employer}

            # Collect all text in the block until the next h3
            block_text = []
            node = h3.find_next_sibling()
            while node and node.name != "h3":
                block_text.append(node.get_text(separator="\n"))
                node = node.find_next_sibling()

            full_text = "\n".join(block_text)

            # Parse line by line
            for line in full_text.split("\n"):
                line = line.strip()
                if not line or ":" not in line:
                    continue
                key, _, val = line.partition(":")
                key = key.strip()
                val = val.strip()

                if "Case Number" in key:
                    case["case_number"] = val
                    case["case_url"] = f"https://www.nlrb.gov/case/{val}"
                elif "Tally Issued" in key:
                    case["tally_date"] = parse_date(val)
                elif "Date Filed" in key:
                    case["date_filed"] = parse_date(val)
                elif "Eligible Voters" in key:
                    try:
                        case["eligible_voters"] = int(val.replace(",", ""))
                    except ValueError:
                        pass
                elif "Votes for" in key:
                    try:
                        case["votes_for"] = int(val.replace(",", ""))
                    except ValueError:
                        pass
                elif "Votes Against" in key:
                    try:
                        case["votes_against"] = int(val.replace(",", ""))
                    except ValueError:
                        pass
                elif "Total Ballots" in key:
                    try:
                        case["ballots_counted"] = int(val.replace(",", ""))
                    except ValueError:
                        pass
                elif "Labor Union1" in key and "union" not in case:
                    case["union"] = val
                elif "Union to Certify" in key:
                    case["union_to_certify"] = val
                elif "Status" in key and "union" not in key:
                    case["status"] = val
                elif "Tally Type" in key:
                    case["tally_type"] = val
                elif "Unit Location" in key:
                    case["location"] = val
                elif "Region Assigned" in key:
                    case["region"] = val
                elif "Void Ballots" in key:
                    try:
                        case["void_ballots"] = int(val.replace(",", ""))
                    except ValueError:
                        pass

            if "case_number" not in case:
                continue

            # Subtype
            for t in ["RC", "RD", "RM"]:
                if f"-{t}-" in case.get("case_number", ""):
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

            # Stage
            status = case.get("status", "").lower()
            case["stage"] = "certified" if "closed" in status else "tally_issued"

            results[case["case_number"]] = case
            page_count += 1

        print(f"  Page {page}: {page_count} cases")

        if found_old and days_back <= 7:
            break

        next_btn = soup.find("a", {"rel": "next"}) or soup.select_one("li.pager__item--next a")
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

    # Fix stages after merge
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
