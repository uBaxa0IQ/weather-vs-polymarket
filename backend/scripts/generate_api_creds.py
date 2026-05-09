"""
One-time script to generate Polymarket L2 API credentials.

Run this once with your POLYMARKET_PRIVATE_KEY set in .env:
    python scripts/generate_api_creds.py

Copy the printed values into your .env file as:
    POLYMARKET_API_KEY=...
    POLYMARKET_API_SECRET=...
    POLYMARKET_API_PASSPHRASE=...
"""
import os
import sys

# Load .env manually so we don't need the full app stack
from pathlib import Path

env_path = Path(__file__).resolve().parents[1] / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

private_key = os.environ.get("POLYMARKET_PRIVATE_KEY", "").strip()
proxy_address = os.environ.get("POLYMARKET_PROXY_ADDRESS", "").strip() or None
sig_type_str = os.environ.get("POLYMARKET_SIGNATURE_TYPE", "").strip()

if not private_key:
    print("ERROR: POLYMARKET_PRIVATE_KEY is not set in .env")
    sys.exit(1)

try:
    from py_clob_client.client import ClobClient
except ImportError:
    print("ERROR: py-clob-client is not installed. Run: pip install py-clob-client")
    sys.exit(1)

try:
    sig_type = int(sig_type_str) if sig_type_str else (1 if proxy_address else 0)
except ValueError:
    sig_type = 1 if proxy_address else 0

print(f"Connecting to Polymarket CLOB (signature_type={sig_type}, proxy={proxy_address})…")

client = ClobClient(
    host="https://clob.polymarket.com",
    key=private_key,
    chain_id=137,
    funder=proxy_address,
    signature_type=sig_type,
)

creds = client.create_or_derive_api_creds()
client.set_api_creds(creds)

print("\n✓ Success! Add these to your .env:\n")
print(f"POLYMARKET_API_KEY={creds.api_key}")
print(f"POLYMARKET_API_SECRET={creds.api_secret}")
print(f"POLYMARKET_API_PASSPHRASE={creds.api_passphrase}")
print()
