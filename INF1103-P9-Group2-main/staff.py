"""
staff.py

Admin tool for creating hospital staff accounts. Run from the project root:

    python staff.py

Accounts are saved as JSON in data/staff/<STAFF_ID>.json (see data_manager).
Staff then log in through main.py by typing their Staff ID.

Like main.py, this is a separate entry point, so it is allowed to call both
io_manager and data_manager; the managers themselves still never import
each other. No print() or input() here: all I/O goes through io_manager.
"""

from datetime import datetime

import managers.data_manager as data_manager
import managers.io_manager as io_manager


def create_staff_account():
    staff_id = data_manager.next_staff_id()
    name = io_manager.prompt_staff_name()
    role = io_manager.prompt_staff_role()

    record = {
        "staff_id": staff_id,
        "name": name,
        "role": role,
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }

    if data_manager.save_staff(record):
        io_manager.display_message(f"\nAccount created. Staff ID: {staff_id}")
    else:
        io_manager.display_error("Couldn't save the staff account. Please try again.")


def main():
    data_manager.ensure_staff_folder()

    while True:
        choice = io_manager.prompt_admin_menu()
        if choice == "create":
            create_staff_account()
        elif choice == "list":
            io_manager.display_staff_list(data_manager.list_staff())
        else:
            break

    io_manager.display_message("Goodbye.")


if __name__ == "__main__":
    main()
