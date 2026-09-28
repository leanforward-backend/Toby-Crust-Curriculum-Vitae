# Toby Crust — CV

`index.html` is the general CV. Tailored versions live in `profiles/`, all sharing `cv.css`:

| Profile | For |
|---|---|
| `profiles/forward-deployed-engineer.html` | Forward deployed, solutions and customer-facing engineering |
| `profiles/ai-engineer.html` | AI, LLM agent and workflow automation roles |
| `profiles/full-stack-engineer.html` | React / TypeScript product engineering |
| `profiles/xr-engineer.html` | Unity, AR and real-time 3D |

Print-ready PDFs are in `profiles/pdf/`. To rebuild them after editing:

```bash
for f in index profiles/*-engineer.html; do
  f="${f%.html}"; out="profiles/pdf/$(basename "$f").pdf"; [ "$f" = index ] && out=profiles/pdf/general.pdf
  chromium --headless=new --no-pdf-header-footer --virtual-time-budget=8000 --print-to-pdf="$out" "file://$PWD/$f.html"
done
```

`job-radar/` finds Sydney roles that match these CVs each morning. See [job-radar/README.md](job-radar/README.md).

## How the daily job update works

Nothing runs in the cloud. Two systemd user units on this machine do the work:

| Unit | What it does |
|---|---|
| `job-radar-scan.timer` | Fires every day at 6:50am (plus up to 5 minutes of random delay) and starts `job-radar-scan.service`. |
| `job-radar-scan.service` | Runs `python3 job-radar/scan.py` once, then exits. |
| `job-radar-web.service` | Keeps the dashboard running at <http://127.0.0.1:8787>. It starts at login and restarts if it crashes. |

Each morning's run goes like this:

1. The timer starts `scan.py`.
2. `scan.py` pulls new postings from LinkedIn and about 100 company job boards.
3. It reads each description and scores it against `job-radar/profile.json`.
4. It merges the results into `job-radar/data/jobs.json`. New roles are marked "New today", roles seen again get their last-seen date updated, and stale ones are dropped.
5. The dashboard reads that file, so reloading the page shows the new roles.

A few details:
- **Machine off at 6:50am:** the timer has `Persistent=true`, so the missed scan runs as soon as the machine is next on.
- **Logged out:** lingering is enabled for your user, so the timer still fires.
- **Your statuses are safe:** Save, Applied and Dismiss live in `job-radar/data/status.json`, which the scan never overwrites. Dismissed roles aren't suggested again.

### Set it up (once)

```bash
cd job-radar
./install.sh                # installs and starts both units
python3 scan.py --days 30   # optional: backfill the last month
```

### Check on it

```bash
systemctl --user list-timers job-radar-scan.timer   # when the next scan runs, and when the last one did
systemctl --user status job-radar-scan.service      # result of the last scan
tail -n 20 job-radar/data/scan.log                  # scan output, including roles added and skipped
systemctl --user status job-radar-web.service       # is the dashboard up?
```

### Run a scan now

Press **Scan now** on the dashboard, or run:

```bash
systemctl --user start job-radar-scan.service       # same as the daily run
python3 job-radar/scan.py                           # or directly, with output in the terminal
python3 job-radar/scan.py --rescore                 # re-apply profile.json without fetching anything
```

### Change the time or turn it off

Edit `OnCalendar=` in `job-radar/systemd/job-radar-scan.timer`, then run `./install.sh` again.

To pause the daily scan:

```bash
systemctl --user disable --now job-radar-scan.timer
```

To remove both units, run `./install.sh --uninstall`. Your data in `job-radar/data/` is kept.
