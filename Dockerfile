FROM python:3.11.6-slim

# Install the latest OS-level security patches and clean up cache to keep the image small
RUN apt-get update && apt-get upgrade -y && rm -rf /var/lib/apt/lists/*

# Set up your application directory
WORKDIR /app

# Copy and install your requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of your application code (data/ is excluded by .dockerignore)
COPY . .

# Runtime data (patients, staff, appointment slots, logs) lives in /app/data.
# Mount it from the host at run time (-v) so it survives between runs.
#
# Start-up: create the appointment calendar ONLY if it doesn't exist yet.
# An existing calendar is never touched, so bookings are never wiped.

CMD ["sh", "-c", "[ -f data/appointment_slots.json ] || python generate_slots.py; exec python main.py"]