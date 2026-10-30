# Scaler Downloader

Downloads your past Scaler Academy class recordings and turns each one into a **study document
with timestamps**. Everything runs on your own PC, with no paid AI or API keys.

For every class you get:

| File | What it is |
|---|---|
| `notes.html` | **The study document.** Video player on the left; on the right, Scaler's revision notes, flashcards, then the class chapter by chapter (the instructor's own bookmarks) with screenshots of every slide/board, key moments, in-class quizzes with a "▶ solution" jump, doubts students asked, questions to test yourself, a topic index (where each topic comes up), links shared in class and the full transcript. **Click any timestamp and the video jumps there.** Captions (CC), resume-where-you-left-off, print to PDF. |
| `notes.md` | The same notes as Markdown (Obsidian, VS Code, GitHub). |
| `video.mp4` | The recording (720p by default, ~0.4 GB for a 2.5 h class). |
| `video.srt` / `video.vtt` | Subtitles; VLC and most players load them automatically. |
| `transcript.txt` / `.json` | Timestamped transcript (made locally with Whisper). |
| `frames/` | One screenshot per slide / board / code screen, taken just before it changed. |
| `attachments/` | The instructor's lecture notes PDF, when Scaler has one. |
| `revision_notes.md`, `flashcards.json`, `class.json` | Raw material from Scaler (notes by Scaler Companion, bookmarks, quizzes, shared links). |

All classes live in `library/`, one folder each; `library/index.html` is the library page
described below.

## Setup (once)

Needs Windows with **Google Chrome** installed, and Python 3.10+.

```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

`ffmpeg.exe` is included in the project folder. The first transcription downloads the Whisper
model (~0.5 GB, cached in your user folder).

## Use: the library page

Double-click **`Open Scaler Library.bat`**, or in a terminal in this folder run

```bash
python main.py
```

Your browser opens the library page (`http://127.0.0.1:8765`). It lists **every item of every
module exactly as the Scaler dashboard does**: day number, date, classes, bonus sessions, tests,
breaks and holidays, each with a ↗ link to the same item on Scaler. From the page:

- **↻ Refresh class list**: fetch the latest list from Scaler (new classes, rescheduled ones,
  newly published recordings).
- **⬇ Get new recordings**: refresh the list, then download and make notes for the newest
  3 classes that have a recording but no notes yet (press it again for the next 3).
- **Download & make notes** on any class: queue just that one. **Open notes** once it's done.
- **Log in to Scaler**: when a job says Scaler needs you to log in again (you log in yourself in
  the Chrome window that opens, then close it).
- **■ Stop** in the progress panel: stops the running job and clears the queue. Downloads and
  transcripts continue where they stopped the next time you start that class.

Jobs run one at a time, in the order you click; progress and an activity log show at the top,
and the list updates itself when a job finishes. There is also a search box.
**Keep the console window open while jobs run**; closing it (or Ctrl+C) stops the app.
Starting `main.py` again while it's running just reopens the page. Opening `library/index.html`
directly as a file still works for reading, just without buttons. The page always downloads
720p video and transcribes with Whisper `small`; the command line lets you change both.

## Use: command line

The same things without the page (`python main.py --help` for all options):

```bash
python main.py login          # Chrome opens: log in to Scaler yourself, then close the window
python main.py sync           # fetch your modules and classes
python main.py list           # every class with its number (--all: upcoming ones too)
python main.py run 25-36      # download + transcribe + notes for classes 25 to 36
python main.py update         # what "Get new recordings" does (same as update.bat)
```

Selecting classes: `run 7`, `run 3-7`, `run 2,4,9`, `run --module "Advanced DSA 1"`,
`run --search "binary search"`, `run all`.

## Keeping up with new classes

Scaler publishes a recording a day or two after the class. Then press **⬇ Get new recordings**
on the library page, or double-click **`update.bat`** (no page needed). Either one:

1. refreshes your class list (new classes, rescheduled ones, newly published recordings),
2. downloads, transcribes and makes notes for classes whose recording is out but have no notes
   yet, **newest first, up to 3 per run** (so a new class is always handled first, and your older
   classes get worked through a few at a time on later runs),
3. adds Scaler's revision notes to recent classes that were documented before Scaler had
   written them (they appear a few days after class),
4. updates `library/index.html` (`update.bat` also appends what it did to `library/update.log`).

If nothing is new it says so and stops. Each class needs ~30 min of transcription;
`update --limit 1` takes just the newest, `update --limit 0` takes everything waiting (until the
disk is full). To work through older classes in course order instead, use `run`, e.g. `run 1-12`.

### Automatically, every day

Windows Task Scheduler can run the update for you. In PowerShell (adjust the time):

```powershell
$dir = "D:\MELBIN JOE\JOE\Project\Scaler-Downloader"
$action = New-ScheduledTaskAction -Execute "$dir\.venv\Scripts\python.exe" -Argument "`"$dir\main.py`" update"
$trigger = New-ScheduledTaskTrigger -Daily -At 2pm
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 6)
Register-ScheduledTask -TaskName "Scaler Downloader" -Action $action -Trigger $trigger -Settings $settings
```

`-StartWhenAvailable` makes a missed run happen as soon as the PC is on again. Remove it with
`Unregister-ScheduledTask "Scaler Downloader"`. If Scaler ever logs you out, the log says
"log in again": run `main.py login` once.

Separate steps, if you prefer: `download`, `transcribe`, `notes` (same class selection).
Every step can be interrupted and resumes where it stopped: just run the command again.

Command-line options: `--quality 480` (smaller videos), `--model base` (faster but rougher
transcript) or `--model medium` (better but ~3x slower).

### How long it takes (4-core laptop CPU, no GPU)

- Download: ~5 min per class (400 MB at 720p).
- Transcript: **~20–30 min per 2.5 h class** (Whisper `small`). Fine to leave running overnight.
- Screenshots + notes: ~20 s per class.

### Disk space

About **0.4 GB per class** at 720p (0.3 GB at 480p), so a full course of ~110 recorded classes
needs **~45 GB**. The tool checks free space before each download and stops cleanly instead of
filling the disk. Ways to make room:

- Keep the library on a bigger drive (e.g. an external disk): move the `library` folder there
  and run `setx SCALER_LIBRARY "E:\Scaler\library"` once (then open a new terminal).
- `--quality 480` for smaller videos.
- Delete `video.mp4` of classes you're done with: the notes, screenshots, transcript and
  subtitles stay (a few MB per class); `run <number>` downloads the video again if you need it.

## Notes

- **Login.** You always log in yourself in the Chrome window the tool opens (Scaler uses OTP +
  a Cloudflare check). The tool never sees your password. The session is kept in
  `.scaler_profile/`; treat that folder like a password and don't share or commit it.
  Scaler limits active sessions, so logging in here may sign you out on another device. Scaler
  also ends the saved login if that profile is used by a different-looking browser, which is
  why the tool always opens a normal Chrome window and has no "headless" option.
- **Privacy.** Class chats and participant lists contain other students' details; the tool
  never saves them. It keeps only the instructor's bookmarks, quizzes, notices and messages.
- **"Scaler needs you to log in again"**: press **Log in to Scaler** on the page (or run
  `python main.py login`), log in, close that Chrome window, then repeat what you were doing.
- **When Scaler changes its website** and something stops working, run
  `python main.py probe`: log in, open a class recording, close the window. It
  records the site's API calls into `.debug/` (cookies and passwords redacted) so the code in
  `scaler_dl/scaler.py` can be updated.
- Transcripts come from a speech model running on your PC and contain some misheard words; the
  video is always one click away.

For personal study of classes you have access to. Respect Scaler's terms of use.

## What's in this folder

| Path | What it is |
|---|---|
| `main.py` | Start here: opens the library page; with a command, the command line. |
| `Open Scaler Library.bat` | Double-click version of `python main.py`. |
| `update.bat` | "Get new recordings" without the page (also what Task Scheduler runs). |
| `scaler_dl/` | The code. `helperfiles/HANDOFF.md` has a map of what each file does. |
| `ffmpeg.exe` | Joins the downloaded video pieces and reads audio for transcription. |
| `helperfiles/` | Handoff note for continuing elsewhere, and the chat history of how this was built. |

These stay on your PC and are kept out of git: `library/` (videos and notes),
`.scaler_profile/` (your Scaler login), `.debug/` (captures from `probe`), `.venv/` (Python
packages).

## Credits

The idea started from [ramvignesh-b/scaler-downloader](https://github.com/ramvignesh-b/scaler-downloader)
(2021, a Selenium script). This version is a full rewrite.
