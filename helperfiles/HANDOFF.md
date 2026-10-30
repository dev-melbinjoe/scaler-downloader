# Handoff: Scaler-Downloader (state on 30 Oct 2026)

Read this first when continuing on another computer or in a new Claude chat. The full
conversation is in `chat-history.md` next to this file.

## What this is

A tool that downloads my Scaler Academy class recordings and turns each one into a timestamped
study document, all offline on my own PC (no paid AI APIs).

- `python main.py` opens the library page (http://127.0.0.1:8765) with buttons:
  Refresh class list, Get new recordings (newest 3 waiting), Download & make notes per class,
  Log in to Scaler, Stop. CLI commands do the same (`python main.py --help`).
- Per class it saves `video.mp4` (720p, ~0.4 GB), subtitles, transcript, slide screenshots and
  `notes.html` (click a timestamp to jump the video) + `notes.md`.
- Repo: https://github.com/dev-melbinjoe/scaler-downloader (public). Work branch **`dev_bunny`**
  has everything; `master` (`4adc56f`) lacks the helperfiles and the server fix.

## Where things stand

- Everything above is built and was tested on my real account.
- Library on the office PC (`library/`, not in git): notes done for **#2** (Java I/O + data
  types), **#39** (Binary Search on Array), **#109** (Generics & Collections Lab).
  Partial downloads that resume when clicked: **#3** (If-Else), **#108** (Generics).
- Course list: 176 dashboard items, 109 classes with recordings (106 still to process),
  39 upcoming. Disk on D: ~6.7 GB free, enough for ~15 more classes.
- The Scaler login saved in `.scaler_profile/` was ended by Scaler on 30 Oct (see gotchas). If a
  job says "log in again", use the page's **Log in to Scaler** button (OTP) once.

## Decisions I made

- Offline only: transcripts with local Whisper (`small`, ~20–30 min per 2.5 h class on the
  office i3 CPU); notes are built from the transcript, screenshots and Scaler's own material
  (instructor bookmarks, revision notes, flashcards, quizzes). No Claude/OpenAI API.
- Library lives in the project folder (`library/`); set `SCALER_LIBRARY` to move it to a bigger
  drive.
- Git: commits authored by me only. **Never add "Co-Authored-By: Claude"** or other Claude
  attribution. History was reset to start at the rewrite; README credits the original project
  (ramvignesh-b/scaler-downloader).

## Gotchas learned the hard way

- **Never use headless Chrome with `.scaler_profile`**: Scaler treats it as a new device and
  ends the saved login. The tool always opens a visible Chrome window for this reason.
- Scaler limits active sessions: logging in on one computer may log out the other.
- Signed video links only become valid the second after the playlist is issued (downloader waits).
- faster-whisper's own audio loader breaks on PyAV 19 → audio is decoded with ffmpeg, and
  transcribed in ~10-minute chunks (a 3 h class at once needs >2 GB RAM).
- Class-list API needs statuses `active, locked, repeat, bonus` and
  `include[]=super_batch_academy_topic` (that include is what returns the dates).
- Scaler Companion notes: `/scaler/generate-companion-token` → `verify_token` → call with
  `Authorization: Bearer <access_token>` + `X-User-Token: window.__AUTH_TOKEN__`.
- If Scaler changes its site: `python main.py probe`, log in, open a recording, close the
  window; the captured API calls land in `.debug/` (cookies/passwords redacted).
- Office PC only: its Claude browser pane blocks scaler.com, and the Claude shell mangles
  Windows backslashes in commands (use forward slashes).

## Code map (`scaler_dl/`)

| File | Does |
|---|---|
| `scaler.py` | Scaler API client (modules, class list, sessions, recordings, quizzes, companion notes) |
| `library.py` | catalog sync, class selection, fetching a class's materials |
| `downloader.py` | parallel, resumable HLS download → MP4 (ffmpeg, no re-encode) |
| `transcribe.py` | local Whisper in chunks, resumable, writes .srt/.vtt/.txt/.json |
| `frames.py` | one screenshot per slide/board change (webcam masked) |
| `textproc.py` | key moments, topic index, instructor questions |
| `notes.py` / `index.py` | the notes page per class / the library page |
| `server.py` | local web app: job queue, buttons' API, video streaming |
| `cli.py`, `main.py` | commands; `main.py` alone starts the app |
| `probe.py` | network capture for re-mapping Scaler's API |

## Setting up on another computer

1. Install Google Chrome and Python 3.10+ (office PC used 3.14).
2. `git clone https://github.com/dev-melbinjoe/scaler-downloader`, then `git checkout dev_bunny`.
3. In the folder: `python -m venv .venv`, then `.venv\Scripts\pip install -r requirements.txt`.
4. `python main.py` → **Log in to Scaler** (OTP; close the Chrome window when the dashboard
   shows) → **Refresh class list**. The library starts empty there; copy `library/` from the
   office PC if you want the already-made notes and videos (large).

## Continuing with Claude

- **Free claude.ai chat** (no Claude Code): upload this file and `chat-history.md` (as `.txt` if
  `.md` is refused) plus the source files involved, and say "Continue from HANDOFF.md". Chat
  can't run commands or edit files; apply its changes yourself.
- **Claude Code (paid plan)**: open the cloned project and say "Read helperfiles/HANDOFF.md and
  continue". Its memory from the office PC does not travel; the decisions above replace it.

## Possible next steps (not done)

- Process the remaining classes (Get new recordings, or `python main.py run 1-12` for course
  order). Disk space is the limit.
- `ffmpeg.exe` (74 MB, GPL) is in the public repo; consider removing it and installing FFmpeg
  separately.
- `helperfiles/` is public on GitHub now; remove it from the repo once copied if it should stay
  private.
- Merge `dev_bunny` into `master` when ready.
