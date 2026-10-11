# INF1103-P9-Group2
Group Project for INF1103 AY26 P9 Group 2

To ensure reliability with the environment when we install packages later, we will be using Python 3.11.X version. This will allow us to do most of the packages we intend to setup without the concern of incompatibility using the newest version of Python such as 3.14. 

If your computer is yet to run Python 3.11.X, you can download the latest version of 3.11 which is 3.11.9 below.
https://www.python.org/downloads/windows/#:~:text=Note%20that%20Python%203.11.9%20cannot%20be%20used%20on%20Windows%207%20or%20earlier.

After you have installed python, make sure to add the installed python onto your environment path. And verify you have the correct python version by typing.
```console 
py --version
```

Next, proceed to clone the project onto your device. (Refer to Week 2 Lab on how to clone the repo and link it onto your VSCode)

After cloning, you will install the virtual enivronment (venv), enable the venv, and lastly download the requirements.txt. (Note, before you install new pakages, always enable venv)
Windows command:
```console 
py -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

Setting up Google-gemini and env folder to put API key

1. Get the google gemini API key from our group

2. The API key is NOT pasted into any code file. Instead, it lives in a
   file called `.env` that sits in the project root (same folder as
   `main.py`). This file is git-ignored already. Every teammate
   needs their own `.env` with their own key, it never gets pushed to
   the repo.

3. Copy the provided template to create your own `.env`:
   ```console
   copy .env.example .env
   ```
   (Mac/Linux: `cp .env.example .env`)

4. Open `.env` in a text editor and replace the placeholder with your
   real key. No quotes, no spaces around the `=`:
   ```
   GEMINI_API_KEY=your-actual-key-goes-here
   ```

5. That's it — `pip install -r requirements.txt` already installs
   `python-dotenv`, and `ai_manager.py` loads `.env` automatically on
   startup. No further setup needed. Run the app as usual:
   ```console
   py main.py
   ```

If you ever see an error about the AI client not being configured, it
means `.env` is missing, misnamed, or sitting in the wrong folder —
double check it's named exactly `.env` (not `.env.txt`) and is in the
same directory as `main.py`.

### Before the first local run (without Docker)

Docker sets these up for you. Running locally, do them once from the project root:

```console
py generate_slots.py
py staff.py
```

- `generate_slots.py` creates the appointment calendar (`data/appointment_slots.json`). Run it **once**: running it again wipes every booking.
- `staff.py` creates a hospital staff account. Choose `create`; it prints the new Staff ID (for example `STF0001`).

To run the automated tests locally: `py test_logic_procedural.py`

---

## Running with Docker

Docker is how the project is run and tested for assessment. Install and start **Docker Desktop** first, then run every command below from the project root (the folder containing `Dockerfile`). The commands are for PowerShell; `cmd.exe` differences are shown where they matter.

### 1. Build the image

```console
docker build -t triage-app .
```

Rebuild whenever the code or `requirements.txt` changes.

### 2. API key

Create the `.env` file exactly as described in the Gemini section above (project root, `GEMINI_API_KEY=your-key`). Docker passes it to the container with `--env-file`; it is never copied into the image.

> **No quotes** around the key. Docker keeps quote characters as part of the value, so `GEMINI_API_KEY="abc"` would make the key invalid.

### 3. Create a staff account (first time only)

```console
docker run -it --rm -v "${PWD}/data:/app/data" triage-app python staff.py
```

Choose `create`, enter a name and role, and note the Staff ID it prints. Staff accounts are saved in `data/staff/`, so they are kept for later runs.

### 4. Run the application

```console
docker run -it --rm --env-file .env -v "${PWD}/data:/app/data" triage-app
```

In `cmd.exe`, replace `${PWD}` with `%cd%` and use a backslash: `-v "%cd%\data:/app/data"`.

At the login prompt:

- Patients type their NRIC, for example `S1234567A`.
- Staff type their Staff ID, for example `STF0001`.
- Type `exit` to quit.

### 5. Run the automated tests

```console
docker run --rm triage-app python test_logic_procedural.py
```

The tests check `logic_manager` with hardcoded sample AI responses. They need no API key, no internet connection and no data folder. A passing run ends with:

```
Total: 35 | Passed: 35 | Failed: 0 | Errors: 0
```

### Where data is stored

The `data/` folder in the project is mounted into the container, so everything survives between runs:

| Path | Contents |
|---|---|
| `data/patients/` | One JSON file per patient (history and any open request) |
| `data/staff/` | Staff accounts |
| `data/appointment_slots.json` | The appointment calendar |
| `data/ai_manager.log` | AI errors (API failures, malformed responses) |

`data/` is not copied into the image. On start-up the container creates the appointment calendar **only if `data/appointment_slots.json` does not exist**; an existing calendar is never overwritten, so bookings are never lost.

### Resetting for a fresh demo

Stop the app, then delete the patient records and the calendar. Staff accounts are kept, and a new calendar is generated automatically on the next start.

```console
Remove-Item -Recurse -Force data\patients, data\appointment_slots.json
```

In `cmd.exe`: `rmdir /s /q data\patients` and then `del data\appointment_slots.json`.

Always delete both together. A calendar without the matching patient records (or the other way round) can show slots as free that are already booked.

### Troubleshooting

| Symptom | Likely cause and fix |
|---|---|
| "Our triage system is currently experiencing issues" | The AI could not be reached. Check that `.env` exists in the project root, has no quotes around the key, and that the key is valid. The reason is written to `data/ai_manager.log`. |
| "Staff ID not found" | No account with that ID yet. Create one with step 3. |
| `docker: error during connect` or similar | Docker Desktop is not running. Start it and try again. |
| "No appointment slots are currently available" | The calendar is missing or fully booked. Delete `data\appointment_slots.json` and restart to generate a new one. |