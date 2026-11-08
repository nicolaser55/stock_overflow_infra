import sys
import getpass
import keyring

"""
Store the FRED API key in this machine's credential store (Windows Credential Manager), never in a file

    python scripts/machine/store_fred_key.py            # asks for the key (typed text is hidden), stores it, checks it was stored
    python scripts/machine/store_fred_key.py --check    # says whether a key is stored (prints its length only)

The key is never printed. Keep your own copy in Enpass.
"""

# DEFINE THE ENTRY (THE SAME AS ingest/fred.py)
SERVICE_STR, USERNAME_STR = "stock_overflow_infra", "fred_api_key"

# IF THE FILE IS RUN DIRECTLY
if __name__ == "__main__":
    # CHECK ONLY
    if "--check" in sys.argv:
        key_str = keyring.get_password(SERVICE_STR, USERNAME_STR)
        print(f"FRED key stored: {'yes, ' + str(len(key_str)) + ' characters' if key_str else 'no'} (backend {type(keyring.get_keyring()).__name__})")
        raise SystemExit(0 if key_str else 1)
    # ASK FOR THE KEY WITHOUT ECHO
    key_str = getpass.getpass("Paste the FRED API key (hidden), then Enter: ").strip()
    if not (len(key_str) == 32 and key_str.isalnum()):
        print(f"❌ A FRED key is 32 letters and digits; got {len(key_str)} characters. Nothing stored.")
        raise SystemExit(1)
    # STORE IT AND READ IT BACK
    keyring.set_password(SERVICE_STR, USERNAME_STR, key_str)
    ok_bool = keyring.get_password(SERVICE_STR, USERNAME_STR) == key_str
    print(f"{'✅ Stored' if ok_bool else '❌ Not stored'} in {type(keyring.get_keyring()).__name__} as {SERVICE_STR} / {USERNAME_STR}")
    raise SystemExit(0 if ok_bool else 1)
