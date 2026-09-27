# Ashford, CT SearchIQS scraper

A Python HTTP session scraper for the public guest land-record index at https://www.searchiqs.com/CTASH/. It does not download paid document images. It selects dates relative to the run date, follows result pages, removes duplicate rows, and writes an Excel-compatible UTF-8 CSV.

## Setup and run

```bash
python -m venv .venv
# Windows PowerShell: .venv\Scripts\Activate.ps1
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python scraper.py --days 30 --output ashford_records.csv
```

Run through your own **US-based VPN** before starting Python. Check that your VPN's US exit location is active; the script cannot establish or verify a VPN tunnel. If your provider gives an HTTP(S) proxy, use `--proxy http://user:password@us-proxy-host:port` or set `HTTPS_PROXY`. Avoid putting proxy credentials in shared shell history. `--days` uses the machine's local date, so set the system time zone appropriately. `--delay` controls seconds between pages; `--max-pages` is a safety cap that raises an error if reached.

The script discovers ASP.NET form fields and guest links dynamically. SearchIQS can change its HTML or require a CAPTCHA or login; this script does not bypass such restrictions. A 403 or an unrecognized table prints an error and removes an incomplete output file. If the site changes, inspect permitted HTML and update the relevant parser functions. Use only where the site's terms permit automated access.

## Output

Columns: `record_date, document_type, party_1, party_2, book, page, instrument, details_url`. Empty fields mean the site did not provide the column. `sample_sheet.csv` is an **illustrative schema only**, not real Ashford data.

## Validation status

On 2026-09-27, the live Ashford page returned HTTP 403 from the development environment, so end-to-end retrieval, current selectors, full pagination, and a real sample export could not be verified. Test on an authorized US VPN connection before treating the output as complete. The program intentionally fails when it cannot identify fields or results instead of silently returning zero rows.
