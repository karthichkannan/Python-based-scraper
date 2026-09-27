"""SearchIQS Ashford guest index scraper (HTTP only)."""
from __future__ import annotations

import argparse
import csv
import logging
import os
import re
import sys
import time
from datetime import date, timedelta
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

BASE = 'https://www.searchiqs.com/CTASH/'
FIELDS = ['record_date', 'document_type', 'party_1', 'party_2', 'book', 'page', 'instrument', 'details_url']
LOG = logging.getLogger('ashford')


def soup_response(response):
    response.raise_for_status()
    return BeautifulSoup(response.text, 'html.parser')


def same_site(url):
    return urlparse(url).hostname in {'www.searchiqs.com', 'searchiqs.com'} and urlparse(url).path.lower().startswith('/ctash/')


def controls(form):
    data = {}
    for el in form.select('input[name], select[name], textarea[name]'):
        name = el['name']
        if el.name == 'input':
            typ = el.get('type', 'text').lower()
            if typ in ('submit', 'button', 'image', 'file', 'reset'):
                continue
            if typ in ('checkbox', 'radio') and not el.has_attr('checked'):
                continue
            data[name] = el.get('value', 'on' if typ in ('checkbox', 'radio') else '')
        elif el.name == 'select':
            selected = el.select_one('option[selected]') or el.select_one('option')
            data[name] = selected.get('value', selected.get_text(' ', strip=True)) if selected else ''
        else:
            data[name] = el.get_text()
    return data


def choose_form(doc, terms):
    forms = doc.select('form')
    if not forms:
        raise RuntimeError('No HTML form found; site structure may have changed.')
    def score(form):
        content = form.get_text(' ', strip=True).lower() + ' ' + ' '.join(
            f"{e.get('name', '')} {e.get('id', '')} {e.get('placeholder', '')}".lower()
            for e in form.select('input,select,button'))
        return sum(bool(re.search(t, content)) for t in terms)
    return max(forms, key=score)


def submit(session, page_url, form, updates=None, clicked=None):
    data = controls(form)
    if updates:
        data.update(updates)
    if clicked is not None:
        data[clicked.get('name', '')] = clicked.get('value', clicked.get_text(' ', strip=True))
    action = urljoin(page_url, form.get('action') or page_url)
    if not same_site(action):
        raise RuntimeError(f'Refusing form action outside Ashford: {action}')
    method = form.get('method', 'get').lower()
    if method == 'post':
        response = session.post(action, data=data, timeout=35)
    else:
        response = session.get(action, params=data, timeout=35)
    return response.url, soup_response(response)


def find_guest(doc):
    for a in doc.select('a[href]'):
        label = a.get_text(' ', strip=True).lower()
        if 'guest' in label and 'search' in label:
            return a
    return None


def start_guest(session):
    response = session.get(BASE, timeout=35)
    doc = soup_response(response)
    for _ in range(4):
        link = find_guest(doc)
        if link:
            target = urljoin(response.url, link['href'])
            if not same_site(target):
                raise RuntimeError('Guest link leaves Ashford site')
            response = session.get(target, timeout=35)
            doc = soup_response(response)
            continue
        candidates = [(f, b) for f in doc.select('form') for b in f.select('input[type=submit],button')
                      if 'guest' in (b.get('value', '') + ' ' + b.get_text(' ', strip=True)).lower()]
        if candidates:
            url, doc = submit(session, response.url, candidates[0][0], clicked=candidates[0][1])
            response = type('Page', (), {'url': url})()
            continue
        break
    return response.url, doc


def date_field_updates(form, start, end):
    updates = {}
    candidates = form.select('input[name]')
    for el in candidates:
        description = ' '.join(el.get(k, '') for k in ('name', 'id', 'placeholder', 'aria-label')).lower()
        if any(x in description for x in ('date', 'from', 'thru', 'through', 'start', 'end')):
            if any(x in description for x in ('from', 'start', 'begin')):
                updates[el['name']] = start.strftime('%Y-%m-%d' if el.get('type') == 'date' else '%m/%d/%Y')
            elif any(x in description for x in ('thru', 'through', 'end', 'to')):
                updates[el['name']] = end.strftime('%Y-%m-%d' if el.get('type') == 'date' else '%m/%d/%Y')
    if len(updates) < 2:
        raise RuntimeError('Could not identify both date fields; inspect the search form before running.')
    return updates


def search(session, url, doc, start, end):
    form = choose_form(doc, ['date', 'party', 'book', 'search'])
    updates = date_field_updates(form, start, end)
    buttons = [x for x in form.select('input[type=submit],button[type=submit],button:not([type])')
               if 'search' in (x.get('value', '') + ' ' + x.get_text(' ', strip=True)).lower()]
    return submit(session, url, form, updates, buttons[0] if buttons else None)


def result_table(doc):
    wanted = ('date', 'party', 'grantor', 'grantee', 'book', 'instrument', 'document')
    tables = doc.select('table')
    def score(table):
        heads = [x.get_text(' ', strip=True).lower() for x in table.select('th')]
        return sum(any(w in h for w in wanted) for h in heads)
    table = max(tables, key=score, default=None)
    return table if table and score(table) >= 2 else None


def rows(doc, url):
    table = result_table(doc)
    if table is None:
        if re.search(r'no (records|results|matches) (found|available)|0 records', doc.get_text(' ', strip=True), re.I):
            return []
        raise RuntimeError('Result table not recognized; save page HTML and review selectors.')
    headers = [h.get_text(' ', strip=True).lower() for h in table.select('tr')[0].select('th,td')]
    def value(mapping, *keys):
        return next((v for k, v in mapping.items() if any(key in k for key in keys)), '')
    output = []
    for tr in table.select('tr')[1:]:
        cells = tr.find_all(['td', 'th'], recursive=False)
        if not cells or len(cells) != len(headers):
            continue
        mapping = dict(zip(headers, [c.get_text(' ', strip=True) for c in cells]))
        link = tr.select_one('a[href]')
        detail = urljoin(url, link['href']) if link else ''
        output.append({'record_date': value(mapping, 'date'), 'document_type': value(mapping, 'description', 'document type', 'doc type'),
                       'party_1': value(mapping, 'party 1', 'grantor'), 'party_2': value(mapping, 'party 2', 'grantee'),
                       'book': value(mapping, 'book'), 'page': value(mapping, 'page'),
                       'instrument': value(mapping, 'instrument'), 'details_url': detail if same_site(detail) else ''})
    return output


def next_page(doc, url):
    for link in doc.select('a[href]'):
        label = link.get_text(' ', strip=True).lower()
        title = (link.get('title') or '').lower()
        href = link['href']
        if label in ('next', 'next page', '>', '»') or 'next page' in title:
            if href.lower().startswith('javascript:'):
                match = re.search(r"__doPostBack\(['\"]([^'\"]+)['\"],\s*['\"]([^'\"]*)['\"]\)", href, re.I)
                if match:
                    return ('postback', match.groups())
            else:
                target = urljoin(url, href)
                if same_site(target):
                    return ('link', target)
    # ASP.NET GridView paging often uses Page$N rather than a labelled next link.
    for link in doc.select('a[href*="Page%24"],a[href*="Page$"]'):
        href = link['href']
        match = re.search(r"__doPostBack\(['\"]([^'\"]+)['\"],\s*['\"](Page\$Next)['\"]\)", href, re.I)
        if match:
            return ('postback', match.groups())
    return None


def scrape(session, start, end, delay, max_pages):
    url, doc = start_guest(session)
    url, doc = search(session, url, doc, start, end)
    seen_pages, seen_records = set(), set()
    for page_no in range(1, max_pages + 1):
        body_key = (url, str(doc)[:20000])
        if body_key in seen_pages:
            raise RuntimeError('Repeated result page detected; pagination stopped to prevent a loop.')
        seen_pages.add(body_key)
        batch = rows(doc, url)
        LOG.info('Page %d: %d rows', page_no, len(batch))
        for record in batch:
            identity = tuple(record.values())
            if identity not in seen_records:
                seen_records.add(identity)
                yield record
        following = next_page(doc, url)
        if not following:
            return
        time.sleep(delay)
        if following[0] == 'link':
            response = session.get(following[1], timeout=35)
            url, doc = response.url, soup_response(response)
        else:
            form = choose_form(doc, ['page', 'search'])
            target, argument = following[1]
            url, doc = submit(session, url, form, {'__EVENTTARGET': target, '__EVENTARGUMENT': argument})
    raise RuntimeError(f'Reached --max-pages={max_pages}; results may be incomplete.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--days', type=int, default=30, help='Look back this many days from today (default 30)')
    parser.add_argument('--output', default='ashford_records.csv')
    parser.add_argument('--proxy', default=os.getenv('HTTPS_PROXY'), help='US VPN/proxy URL, if required by your VPN provider')
    parser.add_argument('--delay', type=float, default=1.0)
    parser.add_argument('--max-pages', type=int, default=10000)
    args = parser.parse_args()
    if args.days < 0 or args.delay < 0 or args.max_pages < 1:
        parser.error('days/delay must be nonnegative and max-pages must be positive')
    today = date.today()
    session = requests.Session()
    session.headers.update({'User-Agent': 'Mozilla/5.0 (compatible; AshfordResearch/1.0)', 'Accept': 'text/html,application/xhtml+xml'})
    if args.proxy:
        session.proxies.update({'http': args.proxy, 'https': args.proxy})
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
    try:
        with open(args.output, 'w', newline='', encoding='utf-8-sig') as stream:
            writer = csv.DictWriter(stream, fieldnames=FIELDS)
            writer.writeheader()
            count = 0
            for item in scrape(session, today - timedelta(days=args.days), today, args.delay, args.max_pages):
                writer.writerow(item)
                count += 1
        LOG.info('Saved %d records to %s', count, args.output)
    except Exception as exc:
        try:
            os.remove(args.output)
        except OSError:
            pass
        LOG.error('%s: %s', type(exc).__name__, exc)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
