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
same directory as `main.py`.# INF1103-P9-Group2
