#!/usr/bin/env python3
"""
NLRB Election Tracker — scraper
Pulls from two NLRB pages:
  1. Recent Filings  -> petitions as filed (including withdrawals)
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
    for b in block.find_all("b"):
        if label.lower() in b.text.lower():
            value = ""
            for sibling in b.next_siblings:
                text = str(sibling)
                if text.strip():
                    value = BeautifulSoup(text, "html.parser").get_text().strip().lstrip(":").strip()
                    break
            return value
    return None


def classify_sector(unit_text, employer):
    """Classify a case into a sector based on unit description and employer name."""
    text = ((unit_text or "") + " " + (employer or "")).lower()

    sectors = [
        ("Healthcare", [r"nurse", r"rn\b", r"physician", r"doctor", r"hospital", r"medical",
                        r"health care", r"healthcare", r"clinic", r"pharmacy", r"pharmacist",
                        r"emt", r"paramedic", r"ambulance", r"dental", r"therapist",
                        r"radiology", r"lab technolog", r"patient care"]),
        ("Education", [r"teacher", r"faculty", r"professor", r"instructor", r"school",
                       r"university", r"college", r"academic", r"student worker",
                       r"teaching assistant", r"education"]),
        ("Transportation & Logistics", [r"driver", r"warehouse", r"distribution", r"logistics",
                                         r"delivery", r"freight", r"dock", r"loader",
                                         r"forklift", r"armored car", r"shuttle", r"transit",
                                         r"bus driver", r"transportation"]),
        ("Hospitality & Food Service", [r"hotel", r"hospitality", r"housekeep", r"housekeeper",
                                         r"cook", r"kitchen", r"food service", r"catering",
                                         r"restaurant", r"barista", r"bartender", r"server",
                                         r"casino", r"gaming"]),
        ("Retail", [r"retail", r"store", r"cashier", r"sales associate", r"customer service",
                    r"merchandise", r"grocery", r"supermarket"]),
        ("Manufacturing", [r"production", r"manufacturing", r"assembly", r"plant worker",
                           r"machinist", r"fabricat", r"weld", r"mill", r"foundry",
                           r"chemical", r"refinery", r"cement"]),
        ("Construction & Utilities", [r"electrician", r"plumber", r"pipefitter", r"hvac",
                                       r"sheet metal", r"ironworker", r"carpenter",
                                       r"construction", r"utility", r"maintenance",
                                       r"engineer.*stationary", r"boiler"]),
        ("Security & Public Safety", [r"security", r"guard", r"police", r"fire fighter",
                                       r"firefighter", r"law enforcement", r"corrections",
                                       r"protective"]),
        ("Media & Entertainment", [r"journalist", r"reporter", r"editor", r"media",
                                    r"broadcast", r"stage", r"theatrical", r"musician",
                                    r"actor", r"film", r"television", r"radio",
                                    r"technician.*entertainment"]),
        ("Technology & Professional", [r"software", r"engineer", r"developer", r"tech",
                                        r"it\b", r"information technology", r"professional",
                                        r"research", r"scientist"]),
        ("Social Services & Nonprofit", [r"social worker", r"case manager", r"counselor",
                                          r"nonprofit", r"community", r"mental health",
                                          r"disability", r"residential", r"shelter",
                                          r"supported living"]),
        ("Building Services", [r"janitor", r"custodian", r"cleaner", r"janitorial",
                                r"building service", r"porter", r"housekeeping"]),
    ]

    import re
    for sector, patterns in sectors:
        for p in patterns:
            if re.search(p, text):
                return sector
    return "Other"


def scrape_filings(days_back=90):
    print(f"Scraping filings (last {days_back} days)...")
    cutoff = datetime.now() - timedelta(days=days_back)
    results = {}
    page = 0
    max_pages = 50 if days_back >= 90 else 8  # scan more pages on daily runs

    while page < max_pages:
        html = fetch(FILINGS_URL, page=page)
        if not html:
            break

        soup = BeautifulSoup(html, "html.parser")
        blocks = soup.select("div.rer-content")
        print(f"  Page {page}: {len(blocks)} total blocks found")
        page_count = 0
        old_count = 0

        for block in blocks:
            h3 = block.find("h3")
            if not h3:
                continue
            employer = h3.text.strip()

            a = block.find("a", href=lambda h: h and "/case/" in h)
            if not a:
                continue
            cn = a.text.strip()

            if not any(t in cn for t in ["-RC-", "-RD-", "-RM-"]):
                continue

            case = {
                "case_number": cn,
                "employer": employer,
                "case_url": "https://www.nlrb.gov" + a["href"],
            }

            for t in ["RC", "RD", "RM"]:
                if f"-{t}-" in cn:
                    case["case_type"] = t
                    break

            case["date_filed"] = parse_date(get_field_value(block, "Date Filed"))
            case["status"] = get_field_value(block, "Status") or ""
            case["location"] = get_field_value(block, "Location") or ""
            case["region"] = get_field_value(block, "Region Assigned") or ""

            # Capture close reason for withdrawals
            close_reason = get_field_value(block, "Reason Closed") or ""
            if close_reason:
                case["close_reason"] = close_reason

            emp = get_field_value(block, "No Employees")
            if emp:
                try:
                    case["eligible_voters"] = int(emp.replace(",", ""))
                except ValueError:
                    pass

            # Date cutoff — skip old cases but don't stop the page
            if case.get("date_filed"):
                try:
                    filed_dt = datetime.strptime(case["date_filed"], "%Y-%m-%d")
                    if filed_dt < cutoff:
                        old_count += 1
                        continue
                    days_old = (datetime.now() - filed_dt).days
                    status_lower = case["status"].lower()
                    reason_lower = close_reason.lower()
                    if "withdrawal" in reason_lower:
                        case["stage"] = "withdrawn"
                    elif "closed" in status_lower:
                        case["stage"] = "certified"
                    elif days_old <= 14:
                        case["stage"] = "just_filed"
                    else:
                        case["stage"] = "pending"
                except ValueError:
                    case["stage"] = "just_filed"
            else:
                case["stage"] = "just_filed"

            results[cn] = case
            page_count += 1

        print(f"  Page {page}: {page_count} R-cases kept, {old_count} skipped as too old")

        # Stop if the entire page was old cases — we've gone far enough back
        if old_count > 0 and page_count == 0:
            print("  Full page of old cases, stopping.")
            break

        next_btn = soup.select_one("li.pager__item--next a")
        if not next_btn:
            print("  No next page found, stopping.")
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
    max_pages = 50 if days_back >= 90 else 8

    while page < max_pages:
        html = fetch(RESULTS_URL, page=page)
        if not html:
            break

        soup = BeautifulSoup(html, "html.parser")
        blocks = soup.select("div.rer-content")
        print(f"  Page {page}: {len(blocks)} total blocks found")
        page_count = 0
        old_count = 0

        for block in blocks:
            h3 = block.find("h3")
            if not h3:
                continue
            employer = h3.text.strip()

            a = block.find("a", href=lambda h: h and "/case/" in h)
            if not a:
                continue
            cn = a.text.strip()

            case = {
                "case_number": cn,
                "employer": employer,
                "case_url": "https://www.nlrb.gov" + a["href"],
            }

            for t in ["RC", "RD", "RM"]:
                if f"-{t}-" in cn:
                    case["case_type"] = t
                    break

            head_top = block.find("div", class_="rer-head-top")
            if head_top:
                case["tally_date"] = parse_date(get_field_value(head_top, "Tally Issued Date"))
                case["tally_type"] = get_field_value(head_top, "Tally Type") or ""
                case["ballot_type"] = get_field_value(head_top, "Ballot Type") or ""

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
                    elif "Union to Certify" in label:
                        case["union_to_certify"] = value
                    elif "Voting Unit" in label:
                        case["unit_description"] = value

            vf = case.get("votes_for", 0) or 0
            va_count = case.get("votes_against", 0) or 0
            ct = case.get("case_type", "RC")
            if vf == 0 and va_count == 0:
                case["outcome"] = "unknown"
            elif vf == va_count:
                case["outcome"] = "tie"
            elif ct in ["RD", "RM"]:
                case["outcome"] = "union_won" if va_count > vf else "union_lost"
            else:
                case["outcome"] = "union_won" if vf > va_count else "union_lost"

            tally_str = case.get("tally_date")
            if tally_str:
                try:
                    if datetime.strptime(tally_str, "%Y-%m-%d") < cutoff:
                        old_count += 1
                        continue
                except ValueError:
                    pass

            status = case.get("status", "").lower()
            case["stage"] = "certified" if "closed" in status else "tally_issued"

            # Add sector classification
            case["sector"] = classify_sector(
                case.get("unit_description", ""),
                case.get("employer", "")
            )

            results[cn] = case
            page_count += 1

        print(f"  Page {page}: {page_count} cases kept, {old_count} skipped as too old")

        if old_count > 0 and page_count == 0:
            print("  Full page of old cases, stopping.")
            break

        next_btn = soup.select_one("li.pager__item--next a")
        if not next_btn:
            print("  No next page found, stopping.")
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

    # Fix stages and add sector after merge
    import re
    for cn, case in merged.items():
        if "tally_date" in case and case["tally_date"]:
            status = case.get("status", "").lower()
            case["stage"] = "certified" if "closed" in status else "tally_issued"
        elif case.get("stage") != "withdrawn":
            if case.get("date_filed"):
                try:
                    days = (datetime.now() - datetime.strptime(
                        case["date_filed"], "%Y-%m-%d")).days
                    if days > 60 and case.get("stage") in ("pending", "just_filed"):
                        case["days_pending"] = days
                    case["stage"] = "just_filed" if days <= 14 else "pending"
                except ValueError:
                    case["stage"] = "pending"

        # Classify sector if not already done
        if "sector" not in case:
            case["sector"] = classify_sector(
                case.get("unit_description", ""),
                case.get("employer", "")
            )

    return merged


def load_existing():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE) as f:
            data = json.load(f)
        existing = data.get("cases", {})
        print(f"Loaded {len(existing)} existing cases")
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

    # Diagnostic: test fetch before doing anything
    print("Testing connection to NLRB...")
    test_html = fetch(RESULTS_URL, page=0)
    if not test_html:
        print("ERROR: Could not fetch NLRB results page. Exiting.")
        return
    from bs4 import BeautifulSoup as BS
    test_soup = BS(test_html, "html.parser")
    test_blocks = test_soup.select("div.rer-content")
    print(f"Diagnostic: fetched {len(test_html)} bytes, found {len(test_blocks)} rer-content blocks")
    if len(test_blocks) == 0:
        print("WARNING: No case blocks found. Page may be returning bot-check or empty content.")
        print(f"First 300 chars of page text: {test_soup.get_text()[:300]}")
        return

    existing = load_existing()
    existing_cases = existing.get("cases", {})

    filings = scrape_filings(days_back=days_back)
    results = scrape_results(days_back=days_back)

    new_cases = merge(filings, results)
    existing_cases.update(new_cases)

    # Enrich pending cases with election dates and counsel
    existing_cases = enrich_pending_cases(existing_cases, max_fetches=50)

    output = {
        "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total_cases": len(existing_cases),
        "cases": existing_cases,
    }

    save(output)
    print(f"Done. {len(new_cases)} new/updated, {len(existing_cases)} total.")


def fetch_election_date_from_pdf(pdf_url):
    """Download Notice of Election PDF and extract the election date."""
    try:
        import re as _re
        r = requests.get(pdf_url, headers=HEADERS, timeout=30)
        r.raise_for_status()
        import io
        try:
            from pypdf import PdfReader
        except ImportError:
            from PyPDF2 import PdfReader
        reader = PdfReader(io.BytesIO(r.content))
        for page in reader.pages:
            text = page.extract_text() or ""
            match = _re.search(
                r'DATE[:\s]+(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),?\s+'
                r'(\w+ \d{1,2},?\s+\d{4})',
                text, _re.IGNORECASE
            )
            if match:
                return parse_date(match.group(1).replace(',', '').strip())
    except Exception as e:
        print(f"    PDF parse error: {e}")
    return None


def parse_case_page(html, case_number):
    """Parse an individual case page for election date and counsel."""
    soup = BeautifulSoup(html, "html.parser")
    result = {}

    docket_table = soup.find("table", class_="docket-activity-table")
    if docket_table:
        for row in docket_table.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) >= 2:
                doc_text = cells[1].get_text().strip()
                if "Notice of Election" in doc_text or "Election Scheduled" in doc_text:
                    link = cells[1].find("a", href=True)
                    if link and "apps.nlrb.gov" in link["href"]:
                        pdf_url = link["href"]
                        if not pdf_url.startswith("http"):
                            pdf_url = "https://apps.nlrb.gov" + pdf_url
                        print(f"    Fetching election PDF for {case_number}...")
                        election_date = fetch_election_date_from_pdf(pdf_url)
                        if election_date:
                            result["election_date"] = election_date
                    break

    participants_table = None
    for h2 in soup.find_all("h2"):
        if "Participants" in h2.get_text():
            participants_table = h2.find_next("table")
            break

    employer_counsel_firms = []
    union_counsel_firms = []

    if participants_table:
        for row in participants_table.find_all("tr"):
            td = row.find("td")
            if not td:
                continue
            lines = [l.strip() for l in td.get_text(separator="\n").split("\n") if l.strip()]
            if not lines:
                continue
            participant_type = lines[0].strip()
            role = lines[1] if len(lines) > 1 else ""
            firm = lines[3] if len(lines) > 3 else ""
            if "Employer" in participant_type and "Legal Representative" in role:
                if firm and firm not in employer_counsel_firms:
                    employer_counsel_firms.append(firm)
            elif "Petitioner" in participant_type and "Legal Representative" in role:
                if firm and firm not in union_counsel_firms:
                    union_counsel_firms.append(firm)

    if employer_counsel_firms:
        result["employer_counsel"] = employer_counsel_firms
    if union_counsel_firms:
        result["union_counsel"] = union_counsel_firms

    return result


def enrich_pending_cases(cases, max_fetches=50):
    """Fetch individual case pages for pending cases to get election dates and counsel."""
    pending = [
        (cn, c) for cn, c in cases.items()
        if c.get("stage") in ("pending", "just_filed") and c.get("case_url")
    ]

    now = datetime.now()
    stale_cutoff = (now - timedelta(days=3)).strftime("%Y-%m-%d")

    to_fetch = [
        (cn, c) for cn, c in pending
        if not c.get("case_page_fetched") or c.get("case_page_fetched") < stale_cutoff
    ]
    to_fetch.sort(key=lambda x: x[1].get("eligible_voters", 0) or 0, reverse=True)
    to_fetch = to_fetch[:max_fetches]

    print(f"Enriching {len(to_fetch)} pending cases (of {len(pending)} total pending)...")

    enriched = 0
    for cn, case in to_fetch:
        html = fetch(case["case_url"])
        if not html:
            print(f"  Skipping {cn}: fetch failed")
            continue
        parsed = parse_case_page(html, cn)
        if parsed:
            case.update(parsed)
            enriched += 1
            if "election_date" in parsed:
                print(f"  {cn}: election {parsed['election_date']}, counsel: {parsed.get('employer_counsel', [])}")
        case["case_page_fetched"] = now.strftime("%Y-%m-%d")
        time.sleep(2)

    print(f"  Enriched {enriched} cases with new data.")
    return cases


if __name__ == "__main__":
    main()
