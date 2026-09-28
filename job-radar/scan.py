#!/usr/bin/env python3
"""Find Sydney roles that match Toby's CV and merge them into data/jobs.json.

Sources: company job boards (Greenhouse, Lever, Ashby, SmartRecruiters, Gem, Workday),
Atlassian and Amazon careers, and LinkedIn's public job search. Every role is scored
against the skills and evidence in profile.json, and tagged when it has a route to the US.

    python3 scan.py            # daily run: LinkedIn roles from the last 7 days
    python3 scan.py --days 30  # wider first run
    python3 scan.py --rescore  # re-apply profile.json to saved roles without fetching
"""
import argparse
import concurrent.futures as cf
import fcntl
import html
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, 'data')
SYDNEY = ZoneInfo('Australia/Sydney')
UA = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36'
KEEP_UNSEEN_DAYS = 21      # drop untracked roles not seen for this long
KEEP_DISMISSED_DAYS = 120  # remember dismissals so they aren't suggested again

stats = {'requests': 0, 'failed_requests': 0}


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def fetch(url, as_json=False, post=None):
    for attempt in range(3):
        try:
            headers = {'User-Agent': UA, **({'Content-Type': 'application/json'} if post is not None else {})}
            req = urllib.request.Request(url, headers=headers, data=json.dumps(post).encode() if post is not None else None)
            with urllib.request.urlopen(req, timeout=25) as r:
                body = r.read().decode('utf-8', 'replace')
            stats['requests'] += 1
            return json.loads(body) if as_json else body
        except Exception:
            time.sleep(2 + 3 * attempt)
    stats['failed_requests'] += 1
    return None


def text(h):
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', html.unescape(h or '')))).strip()


def norm(s):
    return re.sub(r'[^a-z0-9]', '', (s or '').lower())


def job_key(company, title):
    return norm(company) + '|' + norm(re.sub(r'\(.*?\)|- ?(sydney|anz|australia|apac).*$', '', title or '', flags=re.I))


def load_json(name, default):
    try:
        with open(os.path.join(DATA, name)) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(name, value):
    os.makedirs(DATA, exist_ok=True)
    path = os.path.join(DATA, name)
    with open(path + '.tmp', 'w') as f:
        json.dump(value, f, indent=1, ensure_ascii=False)
    os.replace(path + '.tmp', path)


# ---------------------------------------------------------------- sources

def board_jobs(profile):
    names = profile.get('companyNames', {})
    name = lambda slug: names.get(slug, slug.title())

    def greenhouse(slug):
        data = fetch(f'https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true', True) or {}
        for x in data.get('jobs', []):
            yield dict(id=f"gh-{slug}-{x['id']}", company=name(slug), title=x['title'],
                       location=(x.get('location') or {}).get('name', ''), url=x['absolute_url'],
                       posted=(x.get('first_published') or x.get('updated_at') or '')[:10],
                       source='Greenhouse', desc=text(x.get('content')), salary='')

    def lever(slug):
        for x in fetch(f'https://api.lever.co/v0/postings/{slug}?mode=json', True) or []:
            c = x.get('categories') or {}
            loc = ' / '.join(l for l in (c.get('allLocations') or [c.get('location', '')]) if l)
            if x.get('workplaceType') in ('remote', 'hybrid', 'onsite'):
                loc += f" ({x['workplaceType']})"
            desc = (x.get('descriptionPlain') or '') + ' ' + ' '.join(text(l.get('content')) for l in x.get('lists', []))
            yield dict(id=f"lv-{slug}-{x['id']}", company=name(slug), title=x['text'], location=loc, url=x['hostedUrl'],
                       posted=datetime.fromtimestamp(x['createdAt'] / 1000, timezone.utc).strftime('%Y-%m-%d'),
                       source='Lever', desc=desc, salary='')

    def ashby(slug):
        data = fetch(f'https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true', True) or {}
        for x in data.get('jobs', []):
            locs = [x.get('location', '')] + [s.get('location', '') for s in (x.get('secondaryLocations') or [])]
            loc = ' / '.join(l for l in locs if l) + (' (Remote)' if x.get('isRemote') else '')
            comp = ((x.get('compensation') or {}).get('compensationTierSummary') or '').replace(' • Offers Equity', ' + equity')
            workplace = (x.get('workplaceType') or '').lower()
            yield dict(id=f"as-{slug}-{x['id']}", company=name(slug), title=x['title'], location=loc, url=x.get('jobUrl', ''),
                       posted=(x.get('publishedAt') or '')[:10], source='Ashby',
                       desc=x.get('descriptionPlain') or text(x.get('descriptionHtml')), salary=comp,
                       modeHint={'remote': 'remote', 'hybrid': 'hybrid', 'onsite': 'onsite'}.get(workplace))

    def smartrecruiters(company):
        # Descriptions need a second call per role, made later only for roles that pass the title filter.
        data = fetch(f'https://api.smartrecruiters.com/v1/companies/{company}/postings?limit=100&country=au', True) or {}
        for x in data.get('content', []):
            loc = x.get('location') or {}
            yield dict(id=f"sr-{company.lower()}-{x['id']}", company=name(company), title=x['name'].strip(),
                       location=loc.get('fullLocation') or loc.get('city', ''), url=f"https://jobs.smartrecruiters.com/{company}/{x['id']}",
                       posted=(x.get('releasedDate') or '')[:10], source='SmartRecruiters', desc=None, detail=x.get('ref'), salary='',
                       # A remote flag on a role tied to a city means "that office, remote-friendly" (Canva marks most roles this way).
                       modeHint='flexible' if loc.get('remote') and loc.get('city') else 'hybrid' if loc.get('hybrid') else None)

    def gem(slug):
        for x in fetch(f'https://api.gem.com/job_board/v0/{slug}/job_posts/', True) or []:
            kind = (x.get('location_type') or '').lower()
            yield dict(id=f"gm-{slug}-{x['id']}", company=name(slug), title=x['title'], location=(x.get('location') or {}).get('name', ''),
                       url=x['absolute_url'], posted=(x.get('first_published_at') or x.get('created_at') or '')[:10], source='Gem',
                       desc=x.get('content_plain') or text(x.get('content')), salary='',
                       modeHint='remote' if kind == 'remote' else 'hybrid' if kind == 'hybrid' else 'onsite' if 'office' in kind else None)

    def workday(board):
        base = f"https://{board['host']}/wday/cxs/{board['tenant']}/{board['site']}"
        for offset in range(0, 200, 20):
            page = fetch(f'{base}/jobs', True, post={'appliedFacets': {}, 'limit': 20, 'offset': offset, 'searchText': 'Sydney'}) or {}
            for x in page.get('jobPostings', []):
                # The list only says "2 Locations"; the path names the primary one, e.g. /job/Australia---Sydney/...
                primary = x.get('externalPath', '').split('/')[2].replace('---', ', ').replace('-', ' ') if x.get('externalPath', '').count('/') >= 3 else ''
                yield dict(id=f"wd-{board['tenant']}-{(x.get('bulletFields') or [x['externalPath']])[0]}", company=board['name'], title=x['title'],
                           location=primary, url=f"https://{board['host']}/{board['site']}{x['externalPath']}", posted='',
                           source='Workday', desc=None, detail=base + x['externalPath'], salary='')
            if offset + 20 >= page.get('total', 0):
                break

    def atlassian():
        for x in fetch('https://www.atlassian.com/endpoint/careers/listings', True) or []:
            locs = x.get('locations') or []
            sydney = [l for l in locs if 'Sydney' in l]
            if not sydney:
                continue
            post = x.get('portalJobPost') or {}
            yield dict(id=f"at-{x['id']}", company='Atlassian', title=x['title'], location='Sydney' + (' / Remote' if any('Remote' in l for l in locs) else ''),
                       url=post.get('portalUrl') or x.get('applyUrl', ''), posted=(post.get('updatedDate') or '')[:10], source='Atlassian careers',
                       desc=text(' '.join(x.get(k) or '' for k in ('overview', 'responsibilities', 'qualifications'))), salary='',
                       # Atlassian is "Team Anywhere": Sydney-office roles can also be done remotely.
                       modeHint='flexible' if any('Remote' in l for l in locs) else None)

    def amazon():
        for offset in range(0, 400, 100):
            page = fetch(f'https://www.amazon.jobs/en/search.json?country=AUS&city=Sydney&result_limit=100&offset={offset}&sort=recent', True) or {}
            for x in page.get('jobs', []):
                posted = datetime.strptime(x['posted_date'], '%B %d, %Y').strftime('%Y-%m-%d') if x.get('posted_date') else ''
                yield dict(id=f"az-{x['id_icims']}", company='Amazon' if 'AWS' not in x.get('company_name', '') else 'Amazon Web Services',
                           title=x['title'].strip(), location=x.get('normalized_location', ''), url='https://www.amazon.jobs' + x['job_path'],
                           posted=posted, source='Amazon careers', salary='',
                           desc=text(' '.join(x.get(k) or '' for k in ('description', 'basic_qualifications', 'preferred_qualifications'))))
            if offset + 100 >= page.get('hits', 0):
                break

    boards = profile['boards']
    tasks = ([(greenhouse, s) for s in boards['greenhouse']] + [(lever, s) for s in boards['lever']] + [(ashby, s) for s in boards['ashby']]
             + [(smartrecruiters, s) for s in boards.get('smartrecruiters', [])] + [(gem, s) for s in boards.get('gem', [])]
             + [(workday, b) for b in boards.get('workday', [])])
    custom = {'atlassian': atlassian, 'amazon': amazon}
    out = []
    with cf.ThreadPoolExecutor(16) as ex:
        futures = [ex.submit(lambda f, a: list(f(a)), f, a) for f, a in tasks]
        futures += [ex.submit(lambda f: list(f()), custom[c]) for c in boards.get('custom', []) if c in custom]
        for fut in futures:
            try:
                out += fut.result()
            except Exception as e:  # one broken board shouldn't stop the scan
                log(f'  board failed: {e!r}')
    return out


def linkedin_jobs(profile, days):
    # f_WT=1,3: onsite + hybrid only. f_E=2,3,4: entry, associate, mid-senior (drops director/exec).
    loc = urllib.parse.quote(profile['location']['linkedinLocation'])
    found = {}
    for track in profile['tracks'].values():
        for q in track['queries']:
            for start in (0, 10, 20):
                url = (f'https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?keywords={urllib.parse.quote(q)}'
                       f'&location={loc}&f_TPR=r{days * 86400}&f_WT=1%2C3&f_E=2%2C3%2C4&start={start}')
                page = fetch(url) or ''
                time.sleep(1.2)
                for card in page.split('<li>')[1:]:
                    m = lambda p: re.search(p, card, re.S)
                    t, u = m(r'base-search-card__title">\s*(.*?)\s*<'), m(r'href="https://\w+\.linkedin\.com/jobs/view/[^"?]*?-(\d+)[?"]')
                    if not (t and u):
                        continue
                    co, l, d, sal = m(r'hidden-nested-link[^>]*>\s*(.*?)\s*<'), m(r'job-search-card__location">\s*(.*?)\s*<'), m(r'datetime="([^"]+)"'), m(r'job-search-card__salary-info">\s*(.*?)\s*<')
                    jid = u.group(1)
                    found.setdefault(jid, dict(id=f'li-{jid}', company=html.unescape(co.group(1)) if co else '', title=html.unescape(t.group(1)),
                                               location=html.unescape(l.group(1)) if l else '', url=f'https://www.linkedin.com/jobs/view/{jid}',
                                               posted=d.group(1) if d else '', source='LinkedIn', desc=None,
                                               salary=html.unescape(sal.group(1)).strip() if sal else ''))
    return list(found.values())


def describe(job):
    """Fill in the description for sources whose listings don't include one."""
    if job['source'] == 'LinkedIn':
        page = fetch(f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job['id'][3:]}") or ''
        d = re.search(r'show-more-less-html__markup[^>]*>(.*?)</div>', page, re.S)
        job['desc'] = text(d.group(1)) if d else ''
        time.sleep(0.8)
    elif job['source'] == 'SmartRecruiters':
        d = fetch(job['detail'], True) or {}
        job['desc'] = text(' '.join((s or {}).get('text', '') for s in ((d.get('jobAd') or {}).get('sections') or {}).values()))
        job['url'] = d.get('postingUrl') or job['url']
    elif job['source'] == 'Workday':
        d = (fetch(job['detail'], True) or {}).get('jobPostingInfo') or {}
        job['desc'] = text(d.get('jobDescription'))
        job['location'] = ' / '.join([d.get('location') or job['location']] + (d.get('additionalLocations') or []))
        job['posted'] = d.get('startDate') or job['posted']
        job['url'] = d.get('externalUrl') or job['url']
        remote = (d.get('remoteType') or '').lower()
        job['modeHint'] = 'remote' if 'remote' in remote else 'flexible' if 'flexible' in remote else 'onsite' if 'office' in remote else None
    job.pop('detail', None)
    return job


# ---------------------------------------------------------------- matching

class Matcher:
    def __init__(self, profile):
        p = profile
        self.p = p
        self.loc_in = re.compile(p['location']['include'], re.I)
        self.loc_out = re.compile(p['location']['excludeTowns'], re.I)
        self.no_title = re.compile(p['excludeTitle'], re.I)
        self.off_track = re.compile(p['offTrackTitle'], re.I)
        self.tracks = {k: re.compile(t['title'], re.I) for k, t in p['tracks'].items()}
        self.skills = [(s, re.compile(s['pattern'], re.I)) for s in p['skills']]
        self.gaps = [(g, re.compile(g['pattern'], re.I)) for g in p['gaps']]
        us = p['usPathway']
        self.us_hq = {norm(c) for c in us['usHeadquartered']}
        self.us_offices = {norm(c): where for c, where in us['usOffices'].items()}
        # "US" is matched case-sensitively so the pronoun "us" doesn't count.
        us_words = us['usWords']
        self.us_signals = [(s, re.compile(s['pattern'].replace('{US}', us_words), re.I)) for s in us['signals']]

    def title_ok(self, title):
        """Cheap pre-filter before fetching a description."""
        return not self.no_title.search(title) and not self.off_track.search(title) and any(r.search(title) for r in self.tracks.values())

    def in_sydney(self, location):
        return bool(self.loc_in.search(location)) and not self.loc_out.search(location)

    @staticmethod
    def years_required(desc):
        # The overall floor ("5+ years, including 2 in GenAI") is the largest one stated.
        ys = [int(a) for a, _ in re.findall(r'(\d{1,2})\s*\+?\s*(?:-|–|to)?\s*(\d{1,2})?\s*\+?\s*years', desc, re.I) if int(a) <= 12]
        return max(ys) if ys else None

    @staticmethod
    def work_mode(job, desc):
        body = desc[:8000].lower()
        if job.get('modeHint'):
            # Boards that state a mode are trusted, except a flexible role that the ad itself calls hybrid.
            return 'hybrid' if job['modeHint'] == 'flexible' and 'hybrid' in body else job['modeHint']
        hint = job['location'].lower()
        if 'remote' in hint and 'hybrid' not in hint:
            return 'remote'
        if 'hybrid' in hint or 'hybrid' in body or re.search(r'\d days? (a|per) week in (the )?office|in[- ]office \d', body):
            return 'hybrid'
        if re.search(r'fully remote|100% remote|remote[- ]first|work from anywhere', body):
            return 'remote'
        return 'onsite'

    def evaluate(self, job):
        """Return (job with signals and score) or (None, reason) when it doesn't fit."""
        title, desc = job['title'], job.get('desc') or ''
        if not self.title_ok(title):
            return None, 'title'
        if not self.in_sydney(job['location']):
            return None, 'location'
        mode = self.work_mode(job, desc)
        if mode not in self.p['location']['allowModes']:
            return None, 'remote'
        years = self.years_required(desc)
        if years is not None and years > self.p['experience']['maxYearsRequired']:
            return None, 'experience'
        matched = [s for s, rx in self.skills if rx.search(desc) or rx.search(title)]
        weight = sum(s['weight'] for s in matched)
        if weight < self.p['minSkillWeight']:
            return None, 'skills'
        gaps = [g for g, rx in self.gaps if rx.search(desc)]
        track = next((k for k in ('fde', 'xr', 'ai', 'fullstack') if self.tracks[k].search(title)), 'fullstack')
        cv, cv_why = self.pick_cv(matched, track)

        score = 30 + min(weight, 40) * 0.9
        if track == 'fde' and re.search(r'forward[- ]deployed|applied ai|agent deployment|ai solutions', title, re.I):
            score += 10
        if track == 'ai' and re.search(r'\bai\b|agent|llm|automation', title, re.I):
            score += 8
        if track == 'xr' and re.search(r'unity|\bxr\b|augmented|creative technologist', title, re.I):
            score += 10
        if years is not None:
            score += 8 if years <= self.p['experience']['years'] else -3
        if re.search(r'\bjunior\b|\bassociate\b', title, re.I) and not re.search(r'\bmid\b', title, re.I):
            score -= 8  # below your level
        score -= min(sum(g['penalty'] for g in gaps), 20)
        age = days_since(job.get('posted'))
        if age is not None:
            score += 4 if age <= 7 else -8 if age > 60 else 0
        score = max(0, min(99, round(score)))
        if score < self.p['minScore']:
            return None, 'score'

        salary = job.get('salary') or ''
        if not salary:
            m = re.search(r'\$\s?\d{2,3}(?:,\d{3}|k|K)(?:\s?(?:-|–|to)\s?\$?\s?\d{2,3}(?:,\d{3}|k|K))?(?:\s?(?:\+\s?super|base|package|AUD))?', desc)
            salary = m.group(0) if m else ''
        out = {k: job[k] for k in ('id', 'company', 'title', 'location', 'url', 'posted', 'source')}
        about_role, about_company = summarize(desc, job['company'], title, self.p)
        # "track" is the CV to send, so the dashboard's track filter and CV link always agree.
        out.update(mode=mode, modeHint=job.get('modeHint'), track=cv, cvWhy=cv_why, titleTrack=track, yearsReq=years,
                   salary=salary or None, score=score, skills=[s['name'] for s in matched][:10], gaps=[g['name'] for g in gaps],
                   aboutRole=about_role, aboutCompany=about_company,
                   why=self.why(matched), watch=self.watch(years, gaps, mode, desc, title),
                   usPath=self.us_path(job['company'], desc))
        return out, None

    def pick_cv(self, matched, title_track):
        """Choose the CV whose lead projects best cover what the ad asks for. The title's track gets a head start."""
        names = {s['name']: s['weight'] for s in matched}
        best, best_score, best_hits = title_track, -1, []
        for key, t in self.p['tracks'].items():
            hits = sorted((n for n in t['cvSkills'] if n in names), key=lambda n: -names[n])
            score = sum(names[n] for n in hits) + (6 if key == title_track else 0)
            if score > best_score or (score == best_score and key == title_track):
                best, best_score, best_hits = key, score, hits
        t = self.p['tracks'][best]
        if not best_hits:
            return best, f"Closest fit for the job title. It leads with {t['cvLeads']}."
        asks = ', '.join(skill_in_sentence(n) for n in best_hits[:3])
        return best, f"It leads with {t['cvLeads']}, and the ad asks for {asks}."

    def us_path(self, company, desc):
        """How this Sydney role could lead to a job in the US, or None."""
        c = norm(company)
        hq = c in self.us_hq or any(c.startswith(k) for k in self.us_hq if len(k) >= 5)
        office = next((where for k, where in self.us_offices.items() if c == k or (len(k) >= 5 and c.startswith(k))), None)
        hits = []
        for s, rx in self.us_signals:
            m = rx.search(desc)
            if m and not re.search(s.get('unless', r'(?!x)x'), desc[max(0, m.start() - 80):m.end() + 40], re.I):
                hits.append(s)
        # An ad that names a US headquarters tells us the same thing as the company list.
        hq = hq or any(s['strength'] == 'company' for s in hits)
        reasons = []
        if hq:
            reasons.append(f'{company} belongs to a US-headquartered company, so an internal transfer to a US office (L-1 visa, usually after 12 months) is a standard route.')
        elif office:
            reasons.append(f'{company} belongs to a company with US offices ({office}), so an internal transfer is possible after a year or so.')
        reasons += [s['note'] for s in hits if s['strength'] != 'company']
        facing = [s for s in hits if s['strength'] == 'withCompany']
        # Strong: the ad offers relocation or sponsorship, or the work is US-facing at a company that can transfer you,
        # or the ad gives two separate US-facing signals.
        strong = any(s['strength'] == 'strong' for s in hits) or bool(facing and (hq or office)) or len(facing) >= 2
        if not reasons:
            return None
        return {'level': 'strong' if strong else 'possible', 'reasons': reasons}

    @staticmethod
    def why(matched):
        # Group matched skills under the CV evidence they come from, strongest first.
        groups = {}
        for s in sorted(matched, key=lambda s: -s['weight']):
            groups.setdefault(s['evidence'], []).append(s['name'])
        top = sorted(groups.items(), key=lambda kv: -sum(next(x['weight'] for x in matched if x['name'] == n) for n in kv[1]))[:3]
        parts = [f"{ev} ({', '.join(skill_in_sentence(n) for n in names[:3])})" for ev, names in top]
        if not parts:
            return None
        return 'Draws on your ' + (', '.join(parts[:-1]) + ' and ' + parts[-1] if len(parts) > 1 else parts[0]) + '.'

    def watch(self, years, gaps, mode, desc, title):
        notes = []
        if years is not None and years > self.p['experience']['years']:
            notes.append(f"Asks for {years}+ years; you have about {self.p['experience']['years']}")
        if gaps:
            notes.append('Mentions ' + ', '.join(g['name'] for g in gaps))
        if re.search(r'\bjunior\b|\bassociate\b', title, re.I) and not re.search(r'\bmid\b', title, re.I):
            notes.append('Pitched below your level')
        if mode == 'onsite' and re.search(r'5 days|five days|fully (in[- ]office|onsite|on-site)', desc, re.I):
            notes.append('Five days in the office')
        if mode == 'flexible':
            notes.append('Based at the Sydney office but remote-friendly, so check how much of the team comes in')
        if re.search(r'travel (up to )?\d{2}%|\d{2}% travel|travel .{0,20}required', desc, re.I):
            notes.append('Involves travel')
        return '. '.join(notes) + '.' if notes else None


# ---------------------------------------------------------------- summaries

ROLE_HEADINGS = (r"what(?:'|’)?s the role\??|about the (?:role|job|position|opportunity)|the (?:role|opportunity|position)\b:?|role overview|"
                 r"position (?:summary|overview)|your role|what you(?:'|’)ll (?:do|be doing)|what you will do|the job|job summary|introduction")
ROLE_PURPOSE = r"\bthe purpose of (?:the|this)\b|\b(?:this|the) [\w -]{0,40}(?:engineer|developer|role) will\b"
COMPANY_HEADINGS = r"about (?:us|the company|the business|the team|our company)|who we are|company (?:description|overview)|our story|our mission"
ROLE_SENTENCE = r"\b(?:you will|you(?:'|’)ll|we(?:'|’)re looking for|we are looking for|we(?:'|’)re seeking|we are seeking|we(?:'|’)re hiring|join (?:us|our|the)\b|as (?:a|an|our) [^.]{3,60}, you)"
SUFFIXES = r"\b(?:australia|group|pty|ltd|limited|inc|au|anz)\b\.?"


def sentences(text):
    return [s.strip() for s in re.split(r'(?<=[.!?])\s+(?=[A-Z"“])', text) if s.strip()]


def clip(text, limit):
    text = re.sub(r'^[\s:–—-]+', '', text).strip()
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(' ', 1)[0].rstrip(',;:–—- ')
    return cut + '…'


def after(desc, heading, n=2, limit=240):
    """The first sentence or two after an inline heading such as "About the role".

    A heading only counts when it starts with a capital and so does the text after it, so a phrase like
    "interest in the role" mid-sentence doesn't.
    """
    for m in re.finditer(rf'(?:^|[\s.?!:])({heading})\s*[:?–—-]?\s+(?=(?-i:["“A-Z]))', desc, re.I):
        if not m.group(1)[0].isupper():
            continue
        out = ''
        for s in sentences(desc[m.end():m.end() + 1200])[:n]:
            if out and len(out) + len(s) > limit:
                break
            out = f'{out} {s}'.strip()
        if len(out) > 25:
            return clip(out, limit)
    return None


def lookup(table, company):
    """Match a company against a name-keyed table, allowing suffixes like "Australia" or "Group"."""
    c = norm(company)
    for k, v in table.items():
        n = norm(k)
        if c == n or (len(n) >= 4 and c.startswith(n)):
            return v
    return None


def summarize(desc, company, title, profile):
    """(about the role, about the company) in a sentence or two each, taken from the ad where possible."""
    if not desc:
        return None, None
    known = profile.get('companyAbout', {})
    recruiter = lookup({r: True for r in profile.get('recruiters', [])}, company)
    employer = re.search(r'@\s*([^()]+?)\s*(?:\(|$)', title)  # Hatch titles name the employer: "Front-end Engineer @ Fetch"
    name = re.sub(SUFFIXES, '', company, flags=re.I).strip() or company
    name_rx = re.escape(name.split(' by ')[0].strip())

    about_company = None
    client = re.search(r"(?:our|my|the) client(?:,)?\s+(?:is|are|,|who is)\s[^.]{10,240}[.!]", desc, re.I)
    if client:
        about_company = 'Recruiter listing. ' + clip(client.group(0)[0].upper() + client.group(0)[1:], 220)
    elif employer and recruiter:
        about_company = f'Listed on {company} for {employer.group(1)}.' + (' ' + lookup(known, employer.group(1)) if lookup(known, employer.group(1)) else '')
    elif recruiter:
        about_company = f"{company} is a recruiter, and this ad doesn't name the employer."
    if not about_company:
        about_company = after(desc, rf'about {name_rx}', 2, 220) or lookup(known, company) or after(desc, COMPANY_HEADINGS, 2, 220)
    if not about_company:
        for s in sentences(desc[:4000]):
            if re.match(rf'{name_rx}\b.{{0,40}}\b(?:is|are|helps|builds|makes|provides|powers|was founded|has been)\b', s, re.I):
                about_company = clip(s, 220)
                break

    about_role = after(desc, ROLE_HEADINGS, 2, 260)
    if not about_role:
        ss = sentences(desc[:6000])
        ss = [s for s in ss if len(s) >= 40 and not s.endswith('?') and s != about_company]
        pick = next((s for s in ss if re.search(ROLE_PURPOSE, s, re.I)), None) \
            or next((s for s in ss if re.search(ROLE_SENTENCE, s, re.I)), None) \
            or next((s for s in ss if re.search(r'\b(?:this role|the role|responsible for|the team|our team)\b', s, re.I)), None)
        about_role = clip(pick, 260) if pick else None
    if about_role:
        # Drop form labels some agencies put before the text, e.g. "Must Have Skills: … Detailed Job Description:".
        about_role = re.sub(r'^.{0,160}?\bjob description:?\s*', '', about_role, flags=re.I) or about_role
    return about_role, about_company


PROPER_NOUNS = {'Python', 'TypeScript', 'React', 'JavaScript / Node', 'Unity', 'Supabase', 'C#', 'Stripe / payments'}


def skill_in_sentence(name):
    """Lower-case a skill name mid-sentence unless it's an acronym (LLMs, AI agents) or a product."""
    if name in PROPER_NOUNS or name[1:2].isupper():
        return name
    return name.lower()


def days_since(ymd):
    try:
        d = datetime.strptime((ymd or '')[:10], '%Y-%m-%d').date()
    except ValueError:
        return None
    return (datetime.now(SYDNEY).date() - d).days


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--days', type=int, default=7, help='LinkedIn lookback window in days (default 7)')
    ap.add_argument('--rescore', action='store_true', help='re-apply profile.json to saved roles without fetching')
    args = ap.parse_args()

    # One scan at a time (the daily timer and the dashboard's refresh button can overlap).
    os.makedirs(DATA, exist_ok=True)
    lock = open(os.path.join(DATA, '.scan.lock'), 'w')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log('Another scan is already running.')
        return

    with open(os.path.join(ROOT, 'profile.json')) as f:
        profile = json.load(f)
    matcher = Matcher(profile)
    today = datetime.now(SYDNEY).strftime('%Y-%m-%d')
    started = datetime.now(timezone.utc)

    jobs = {j['id']: j for j in load_json('jobs.json', [])}
    status = load_json('status.json', {})
    by_url = {j['url']: j['id'] for j in jobs.values()}
    by_key = {job_key(j['company'], j['title']): j['id'] for j in jobs.values()}
    reasons = {}
    added = seen_again = 0

    if not args.rescore:
        log('Scanning company boards…')
        candidates = [j for j in board_jobs(profile) if matcher.title_ok(j['title']) and matcher.in_sydney(j['location'])]
        log(f'  {len(candidates)} Sydney roles with a matching title')
        log(f'Scanning LinkedIn (last {args.days} days)…')
        li = [j for j in linkedin_jobs(profile, args.days) if matcher.title_ok(j['title']) and matcher.in_sydney(j['location'])]
        log(f'  {len(li)} Sydney roles with a matching title')
        candidates += li

        fresh, keys = [], set()
        for j in candidates:
            hit = j['id'] if j['id'] in jobs else by_url.get(j['url']) or by_key.get(job_key(j['company'], j['title']))
            if hit:
                if jobs[hit].get('lastSeen') != today:
                    seen_again += 1
                jobs[hit]['lastSeen'] = today
                continue
            k = job_key(j['company'], j['title'])
            if k not in keys:
                keys.add(k)
                fresh.append(j)
        log(f'Reading {len(fresh)} new descriptions…')
        with cf.ThreadPoolExecutor(3) as ex:
            fresh = list(ex.map(lambda j: describe(j) if j['desc'] is None else j, fresh))
        for j in fresh:
            result, reason = matcher.evaluate(j)
            if result:
                result.update(found=today, lastSeen=today, desc=(j.get('desc') or '')[:6000])
                jobs[result['id']] = result
                added += 1
            else:
                reasons[reason] = reasons.get(reason, 0) + 1

    # Re-apply the profile to everything kept, so edits to profile.json take effect.
    removed = 0
    for jid, j in list(jobs.items()):
        tracked = (status.get(jid) or {}).get('state')
        result, reason = matcher.evaluate(j) if j.get('desc') else ((j, None) if j.get('mode') in profile['location']['allowModes'] else (None, 'remote'))
        unseen = days_since(j.get('lastSeen')) or 0
        if tracked in ('saved', 'applied', 'interview'):
            continue
        if tracked == 'dismissed':
            if unseen > KEEP_DISMISSED_DAYS:
                del jobs[jid]; removed += 1
            continue
        if result is None or unseen > KEEP_UNSEEN_DAYS:
            del jobs[jid]; removed += 1
            continue
        for k in ('found', 'lastSeen', 'desc'):
            result[k] = j.get(k)
        jobs[jid] = result

    save_json('jobs.json', sorted(jobs.values(), key=lambda j: (-j['score'], j['id'])))
    run = dict(date=today, runAt=started.isoformat(timespec='seconds'), added=added, seenAgain=seen_again,
               removed=removed, total=len(jobs), skipped=reasons, **stats)
    if not args.rescore:
        # One entry per day; a second run on the same day adds to it.
        runs = load_json('runs.json', [])
        prev = next((r for r in runs if r.get('date') == today), None)
        if prev:
            run['added'] += prev.get('added', 0)
        save_json('runs.json', ([r for r in runs if r is not prev] + [run])[-90:])
    log(json.dumps(run))


if __name__ == '__main__':
    main()
