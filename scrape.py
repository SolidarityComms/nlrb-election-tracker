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
    for fmt in ("%m/%d/%Y", "%d %B %Y", "%B %d, %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return s


def get_field_value(block, label):
    """Find a bold label in any div and return the text after it."""
    for b in block.find_all("b"):
        if label.lower() in b.text.lower():
            # value is text immediately after the bold tag
            value = ""
            for sibling in b.next_siblings:
                text = str(sibling)
                if text.strip():
                    value = BeautifulSoup(text, "html.parser").get_text().strip().lstrip(":").strip()
                    break
            return value
    return None


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

        for block in soup.select("div.rer-content"):
            # Employer name
            h3 = block.find("h3")
            if not h3:
                continue
            employer = h3.text.strip()

            # Case number link
            a = block.find("a", href=lambda h: h and "/case/" in h)
            if not a:
                continue
            cn = a.text.strip()

            # Only R-cases
            if not any(t in cn for t in ["-RC-", "-RD-", "-RM-"]):
                continue

            case = {
                "case_number": cn,
                "employer": employer,
                "case_url": "https://www.nlrb.gov" + a["href"],
            }

            # Subtype from case number
            for t in ["RC", "RD", "RM"]:
                if f"-{t}-" in cn:
                    case["case_type"] = t
                    break

            # Extract fields from all divs in the block
            case["date_filed"] = parse_date(get_field_value(block, "Date Filed"))
            case["status"] = get_field_value(block, "Status") or ""
            case["location"] = get_field_value(block, "Location") or ""
            case["region"] = get_field_value(block, "Region Assigned") or ""
            emp = get_field_value(block, "No Employees")
            if emp:
                try:
                    case["eligible_voters"] = int(emp.replace(",", ""))
                except ValueError:
                    pass

            # Unit sought
            unit_div = block.find("div", class_="rer-style-3")
            if unit_div:
                b_tag = unit_div.find("b")
                if b_tag and "Unit Sought" in b_tag.text:
                    unit_text = unit_div.get_text().replace("Unit Sought", "").strip().lstrip(":").strip()
                    case["unit_sought"] = unit_text

            # Date cutoff
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
            print("  Hit cutoff, stopping.")
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

        for block in soup.select("div.rer-content"):
            h3 = block.find("h3")
            if not h3:
                continue
            employer = h3.text.strip()

            # Case number is in rer-head-top
            a = block.find("a", href=lambda h: h and "/case/" in h)
            if not a:
                continue
            cn = a.text.strip()

            case = {
                "case_number": cn,
                "employer": employer,
                "case_url": "https://www.nlrb.gov" + a["href"],
            }

            # Subtype
            for t in ["RC", "RD", "RM"]:
                if f"-{t}-" in cn:
                    case["case_type"] = t
                    break

            # Tally date is in rer-head-top (always visible)
            head_top = block.find("div", class_="rer-head-top")
            if head_top:
                case["tally_date"] = parse_date(get_field_value(head_top, "Tally Issued Date"))
                case["tally_type"] = get_field_value(head_top, "Tally Type") or ""
                case["ballot_type"] = get_field_value(head_top, "Ballot Type") or ""

            # Detailed fields are in rer-head-body (collapsed but present in HTML)
            head_body = block.find("div", class_="rer-head-body")
            if head_body:
                case["date_filed"] = parse_date(get_field_value(head_body, "Date Filed"))
                case["status"] = get_field_value(head_body, "Status") or ""
                case["location"] = get_field_value(head_body, "Unit Location") or ""
                case["region"] = get_field_value(head_body, "Region Assigned") or ""

                ev = get_field_value(head_body, "No. of Eligible Voters")
                if ev:
                    try:
                        case["eligible_voters"] = int(ev.replace(",", ""))
                    except ValueError:
                        pass

                va = get_field_value(head_body, "Votes Against")
                if va:
                    try:
                        case["votes_against"] = int(va.replace(",", ""))
                    except ValueError:
                        pass

                tb = get_field_value(head_body, "Total Ballots Counted")
                if tb:
                    try:
                        case["ballots_counted"] = int(tb.replace(",", ""))
                    except ValueError:
                        pass

                vb = get_field_value(head_body, "Void Ballots")
                if vb:
                    try:
                        case["void_ballots"] = int(vb.replace(",", ""))
                    except ValueError:
                        pass

                # Votes for and union name are in rer-style-3 divs inside rer-head-body
                for style3 in head_body.find_all("div", class_="rer-style-3"):
                    b_tag = style3.find("b")
                    if not b_tag:
                        continue
                    label = b_tag.text.strip().rstrip(":")
                    value = style3.get_text().replace(b_tag.text, "").strip().lstrip(":").strip()

                    if "Votes for Labor Union" in label:
                        try:
                            case["votes_for"] = int(value.replace(",", ""))
                        except ValueError:
                            pass
                    elif label == "Labor Union1":
                        case["union"] = value
