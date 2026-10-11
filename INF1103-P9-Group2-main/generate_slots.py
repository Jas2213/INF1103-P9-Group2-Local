"""
generate_slots.py

One-off script to seed data/appointment_slots.json with roughly 3 months
of appointment slots, for testing the booking flow before any real
hospital calendar integration exists. Run this once from your project
root (where the data/ folder lives):

    python generate_slots.py

Creates slots Monday-Friday, 09:00-16:30, every 30 minutes, excluding a
12:00-13:00 lunch break. Every slot starts out "available" so
data_manager.find_next_available_slot() / book_slot() have real data to
work with.

Re-running this script OVERWRITES the existing file, wiping any bookings
already made — only run it to reset your test data, not during a live demo.
"""

from datetime import datetime, timedelta
import json
import os

import managers.io_manager as io_manager

OUTPUT_PATH = os.path.join("data", "appointment_slots.json")
DAYS_AHEAD = 90           # roughly 3 months
START_HOUR = 9
END_HOUR = 17              # exclusive — last slot starts at 16:30
SLOT_MINUTES = 30
LUNCH_START_HOUR = 12
LUNCH_END_HOUR = 13


def _is_weekday(d):
    return d.weekday() < 5  # Mon=0 ... Fri=4


def _is_lunch(hour):
    return LUNCH_START_HOUR <= hour < LUNCH_END_HOUR


def generate_slots(start_date=None, days_ahead=DAYS_AHEAD):
    """
    Build the slot calendar as a dict:
        {"YYYY-MM-DD HH:MM": {"status": "available", "patient_id": None}, ...}
    """
    if start_date is None:
        start_date = datetime.now().date()

    slots = {}
    for offset in range(days_ahead):
        day = start_date + timedelta(days=offset)
        if not _is_weekday(day):
            continue

        hour, minute = START_HOUR, 0
        while hour < END_HOUR:
            if not _is_lunch(hour):
                key = f"{day.strftime('%Y-%m-%d')} {hour:02d}:{minute:02d}"
                slots[key] = {"status": "available", "patient_id": None}
            minute += SLOT_MINUTES
            if minute >= 60:
                minute -= 60
                hour += 1
    return slots


def main():
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    slots = generate_slots()
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(slots, f, indent=2)
    io_manager.display_message(f"Generated {len(slots)} appointment slots -> {OUTPUT_PATH}")


if __name__ == "__main__":
    main()