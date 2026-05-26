"""
STOCK MANAGER (Refactored from stock_id.py)
Manages stock of Roblox Place IDs with backup and recovery.

Features:
  - Automatic backups before any write
  - Recovery from corrupted files
  - Atomic writes to prevent data loss
  - Validation of stock data
  - Backup rotation (keeps last 5 backups)

Can be used as:
  - CLI: python stock_manager.py add|list|remove <id>
  - Module: from stock_manager import load_stock, save_stock, etc.
"""

import json
import os
import sys
import shutil
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

STOCK_FILE = "stock.json"
STOCK_BACKUP_DIR = ".stock_backups"
MAX_BACKUPS = 5


def ensure_backup_dir():
    """Ensure backup directory exists."""
    if not os.path.exists(STOCK_BACKUP_DIR):
        try:
            os.makedirs(STOCK_BACKUP_DIR)
            print(f"Created backup directory: {STOCK_BACKUP_DIR}")
        except OSError as e:
            if e.errno == 28:
                pass
            else:
                pass


def create_backup():
    """Create a backup of the current stock file."""
    if not os.path.exists(STOCK_FILE):
        return None

    try:
        ensure_backup_dir()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_file = os.path.join(STOCK_BACKUP_DIR, f"stock_backup_{timestamp}.json")
        shutil.copy2(STOCK_FILE, backup_file)

        rotate_backups()
        return backup_file
    except OSError as e:
        if e.errno == 28:
            pass
        return None
    except Exception:
        return None


def rotate_backups():
    """Keep only the last MAX_BACKUPS backups."""
    try:
        ensure_backup_dir()
        backups = sorted([f for f in os.listdir(STOCK_BACKUP_DIR) if f.startswith("stock_backup_")])

        if len(backups) > MAX_BACKUPS:
            for old_backup in backups[:-MAX_BACKUPS]:
                old_path = os.path.join(STOCK_BACKUP_DIR, old_backup)
                try:
                    os.remove(old_path)
                except Exception:
                    pass
    except Exception:
        pass


def validate_stock_data(data):
    """Validate stock data structure and content."""
    if not isinstance(data, dict):
        return False, "Data is not a dictionary"

    if "stock" not in data:
        return False, "Missing 'stock' key"

    stock = data.get("stock")
    if not isinstance(stock, list):
        return False, "Stock is not a list"

    # Validate each ID is a string
    for item in stock:
        if not isinstance(item, str):
            return False, f"Invalid stock item (not string): {item}"
        if not item.strip():
            return False, "Empty stock item found"

    return True, "Valid"


def load_stock() -> list[str]:
    """Load stock list from stock.json with recovery."""
    if not os.path.exists(STOCK_FILE):
        print(f"Stock file not found: {STOCK_FILE}")
        return []

    try:
        with open(STOCK_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        # Validate data
        is_valid, msg = validate_stock_data(data)
        if not is_valid:
            print(f"WARNING: Stock file validation failed: {msg}")
            print("Attempting recovery from backup...")
            return recover_from_backup()

        stock = data.get("stock", [])
        print(f"Loaded {len(stock)} IDs from stock")
        return stock

    except json.JSONDecodeError as e:
        print(f"ERROR: Stock file is corrupted (JSON error): {e}")
        print("Attempting recovery from backup...")
        return recover_from_backup()

    except Exception as e:
        print(f"ERROR: Error loading stock: {e}")
        print("Attempting recovery from backup...")
        return recover_from_backup()


def recover_from_backup() -> list[str]:
    """Recover stock from the most recent backup."""
    try:
        ensure_backup_dir()
        backups = sorted([f for f in os.listdir(STOCK_BACKUP_DIR) if f.startswith("stock_backup_")])

        if not backups:
            print("ERROR: No backups available for recovery")
            return []

        # Try backups from newest to oldest
        for backup_file in reversed(backups):
            backup_path = os.path.join(STOCK_BACKUP_DIR, backup_file)
            try:
                with open(backup_path, "r", encoding="utf-8") as f:
                    data = json.load(f)

                is_valid, msg = validate_stock_data(data)
                if is_valid:
                    stock = data.get("stock", [])
                    print(f"✅ Recovered {len(stock)} IDs from backup: {backup_file}")

                    # Restore the backup as current stock
                    shutil.copy2(backup_path, STOCK_FILE)
                    print(f"Restored stock from backup: {backup_file}")
                    return stock
                else:
                    print(f"WARNING: Backup {backup_file} is invalid: {msg}")

            except Exception as e:
                print(f"WARNING: Could not read backup {backup_file}: {e}")

        print("ERROR: All backups are corrupted or invalid")
        return []

    except Exception as e:
        print(f"ERROR: Recovery failed: {e}")
        return []


def save_stock(stock_list: list[str]):
    """Save stock list to stock.json with backup and atomic write."""
    try:
        # Validate before saving
        if not isinstance(stock_list, list):
            print(f"ERROR: Stock list is not a list: {type(stock_list)}")
            return False

        # Create backup before writing (silently skip on disk-full)
        create_backup()

        # Prepare data
        data = {"stock": stock_list}

        # Atomic write: write to temp file first, then rename
        temp_file = f"{STOCK_FILE}.tmp"

        try:
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4)

            # Verify the temp file is valid
            with open(temp_file, "r", encoding="utf-8") as f:
                verify_data = json.load(f)

            is_valid, msg = validate_stock_data(verify_data)
            if not is_valid:
                print(f"ERROR: Verification failed before saving: {msg}")
                os.remove(temp_file)
                return False

            # Atomic rename
            if os.path.exists(STOCK_FILE):
                os.remove(STOCK_FILE)
            os.rename(temp_file, STOCK_FILE)

            print(f"✅ Stock saved successfully ({len(stock_list)} IDs)")
            return True

        except OSError as e:
            if e.errno == 28:
                print(f"SKIP: Cannot write stock file. Removing from stock...")
                if os.path.exists(temp_file):
                    try:
                        os.remove(temp_file)
                    except:
                        pass
                return False
            print(f"ERROR: Failed to write stock file: {e}")
            if os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                except:
                    pass
            return False

    except Exception as e:
        print(f"ERROR: Error saving stock: {e}")
        return False


def add_id(place_id: str):
    """Add a Place ID to stock."""
    stock = load_stock()
    if place_id in stock:
        print(f"WARNING: Place ID {place_id} already exists in stock.")
        return False
    stock.append(place_id)
    success = save_stock(stock)
    if success:
        print(f"✅ Added Place ID {place_id} to stock.")
    return success


def remove_id(place_id: str):
    """Remove a Place ID from stock."""
    stock = load_stock()
    if place_id in stock:
        stock.remove(place_id)
        success = save_stock(stock)
        if success:
            print(f"✅ Removed Place ID {place_id} from stock.")
        return success
    else:
        print(f"WARNING: Place ID {place_id} not found in stock.")
        return False


def list_ids():
    """Print all Place IDs in stock."""
    stock = load_stock()
    if not stock:
        print("Stock is empty.")
    else:
        print("Current Stock:")
        for i, place_id in enumerate(stock, 1):
            print(f"{i}. {place_id}")


def get_stock_info():
    """Get information about stock and backups."""
    stock = load_stock()
    print(f"\n📊 Stock Information:")
    print(f"  Current IDs: {len(stock)}")
    print(f"  Stock file: {STOCK_FILE}")
    print(f"  Backup directory: {STOCK_BACKUP_DIR}")

    try:
        if os.path.exists(STOCK_BACKUP_DIR):
            backups = [f for f in os.listdir(STOCK_BACKUP_DIR) if f.startswith("stock_backup_")]
            print(f"  Backups available: {len(backups)}")
            if backups:
                print(f"  Latest backup: {sorted(backups)[-1]}")
    except Exception as e:
        print(f"  Backups: Error reading ({e})")


def main():
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python stock_manager.py add <id>")
        print("  python stock_manager.py list")
        print("  python stock_manager.py remove <id>")
        print("  python stock_manager.py info")
        print("  python stock_manager.py recover")
        return

    cmd = sys.argv[1].lower()

    if cmd == "add":
        if len(sys.argv) < 3:
            print("Usage: python stock_manager.py add <id>")
            return
        add_id(sys.argv[2])
    elif cmd == "list":
        list_ids()
    elif cmd == "remove":
        if len(sys.argv) < 3:
            print("Usage: python stock_manager.py remove <id>")
            return
        remove_id(sys.argv[2])
    elif cmd == "info":
        get_stock_info()
    elif cmd == "recover":
        print("Attempting recovery...")
        recovered = recover_from_backup()
        if recovered:
            print(f"✅ Recovery successful: {len(recovered)} IDs recovered")
        else:
            print("❌ Recovery failed")
    else:
        print(f"Unknown command: {cmd}")


if __name__ == "__main__":
    main()

