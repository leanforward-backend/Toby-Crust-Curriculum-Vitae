# Job Radar

A local dashboard of Sydney roles that match my CV, refreshed every morning.

- **Scanner** (`scan.py`) reads LinkedIn's public job search and about 100 company job boards. These cover:
  - Australian tech companies: Atlassian, Canva, SafetyCulture, Airwallex, Xero, SEEK, Linktree, Zip, WiseTech, Culture Amp, carsales, Nearmap and others.
  - US tech firms with Sydney offices: Amazon, Salesforce, OpenAI, Databricks and others.
  - Sydney startups.

  It keeps roles based in Greater Sydney (onsite, hybrid or office-based flexible) and drops fully remote roles, senior, lead, staff and principal titles, and anything asking for more than 4 years. It then scores each role against the skills and evidence in `profile.json`, and tags roles with a route to working in the US.
- **Dashboard** (`server.py` + `web/`) runs at <http://127.0.0.1:8787>. It has two sections:
  - **Sydney roles:** every match.
  - **US pathway:** the matches that could lead to a job in America.

  Each role shows a one- or two-sentence summary of **the role** and **the company**. These are taken from the ad, falling back to a short description in `profile.json` (`companyAbout`) for well-known companies. Recruiter ads are labelled as such, and when an agency names its client, you see the employer rather than the agency.

  Each role also names **the CV to send**. The scanner compares the ad with what each CV leads with (`cvLeads` and `cvSkills` under each track in `profile.json`), gives the job title's own track a small head start, picks the best fit, and says why. The track filter follows this pick.

  Filter by CV track, work mode, match score and posting date. Mark roles Saved, Applied or Dismissed, and open the matching CV PDF from `../profiles/pdf/`.
- **Schedule**: a systemd user timer runs the scan at 6:50am daily. If the machine was off, it runs at the next boot.

Needs only Python 3.9+ (standard library). Job data stays in `data/`, which is git-ignored because this repo is public.

## Setup

```bash
./install.sh              # enable the daily scan timer and the dashboard service
python3 scan.py --days 30 # optional: backfill a month of LinkedIn postings
```

Open <http://127.0.0.1:8787>. Remove everything with `./install.sh --uninstall` (your data is kept).

## Run by hand

```bash
python3 server.py         # dashboard on http://127.0.0.1:8787
python3 scan.py           # scan now (LinkedIn: last 7 days)
python3 scan.py --rescore # re-apply profile.json to saved roles without fetching
```

The dashboard's **Scan now** button runs the same scan.

## Tuning the matching

Everything the scanner judges by lives in `profile.json`:

| Key | What it controls |
|---|---|
| `location` | Sydney pattern, towns to exclude, allowed work modes (`onsite`, `hybrid`, `flexible`) |
| `experience` | Your years, and the most a role may ask for (`maxYearsRequired`) |
| `excludeTitle` | Seniority words that drop a role (senior, lead, staff, principal…) |
| `offTrackTitle` | Titles outside your tracks (Java, QA, DevOps, sales…) |
| `tracks` | The four CV tracks: LinkedIn queries, title pattern, the CV PDF, and what that CV leads with (`cvLeads`, `cvSkills`) for picking which CV to send |
| `companyAbout`, `recruiters` | Fallback company descriptions, and agencies whose ads don't name the employer |
| `skills` | Each skill's pattern, weight and the CV evidence quoted in the "why" note |
| `gaps` | Requirements you haven't shown, and how many points each costs |
| `minScore`, `minSkillWeight` | Cut-offs for showing a role |
| `boards` | Company job boards to check: Greenhouse, Lever, Ashby, SmartRecruiters and Gem slugs, Workday sites, and the custom `atlassian` and `amazon` feeds |
| `usPathway` | US-headquartered companies, Australian companies with US offices, and the ad wording that signals a US route |

After editing, run `python3 scan.py --rescore`.

## Scoring

Score = 30 + 0.9 × matched skill weight (capped at 40), then:

- +8 to +10 for a title that fits a track head-on (forward deployed, AI or agents, Unity or XR)
- +8 if the stated experience is within your years, −3 if it's 4
- −8 for junior or associate titles
- minus each gap's penalty (capped at −20)
- +4 if posted in the last 7 days, −8 if older than 60 days

60+ is a strong match, 50–59 is good, and 45–49 is a stretch.

## Work modes

- **onsite / hybrid:** as the ad or board states.
- **flexible:** the role is tied to a Sydney office but is also open to remote work. Canva and Atlassian list most roles this way.
- **remote:** fully remote. These are always dropped.

To drop flexible roles too, remove `"flexible"` from `location.allowModes`.

## US pathway

A role goes in the US pathway section when either of these is true:

- The company is US-headquartered or has US offices (see `usPathway` in `profile.json`). An internal transfer on an L-1 visa usually needs 12 months with the company first.
- The ad mentions US relocation, US visa sponsorship, US-based teams or customers, travel to the US, or mobility between offices.

It's marked **Strong** when any of these is true:

- The ad mentions relocation or sponsorship.
- The work is US-facing at a company with US offices.
- The ad gives two separate US-facing signals.

The rest are **Possible**. "US" is matched case-sensitively, so the word "us" in an ad doesn't count.

Open <http://127.0.0.1:8787/radar/#us> to go straight to this section.

## Files

```
scan.py            scanner and matcher
server.py          dashboard server and JSON API (localhost only)
profile.json       matching rules
web/               dashboard (plain HTML, CSS, JS)
systemd/           unit templates used by install.sh
data/              jobs.json, status.json, runs.json, scan.log (git-ignored)
```
