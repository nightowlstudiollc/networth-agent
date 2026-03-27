#!/usr/bin/env python3
"""Minimal Plaid Link server for connecting bank accounts."""

import json
import os
import time
import uuid
from pathlib import Path

import plaid
from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_from_directory
from plaid.api import plaid_api
from plaid.model.country_code import CountryCode
from plaid.model.item_get_request import ItemGetRequest
from plaid.model.item_public_token_exchange_request import (
    ItemPublicTokenExchangeRequest,
)
from plaid.model.link_token_create_request import LinkTokenCreateRequest
from plaid.model.link_token_create_request_user import (
    LinkTokenCreateRequestUser,
)
from plaid.model.products import Products
from plaid.model.link_token_create_request_update import (
    LinkTokenCreateRequestUpdate,
)
from plaid.model.user_create_request import UserCreateRequest

load_dotenv()

# Products to request - configure based on your Plaid access
# Options: transactions, auth, identity, assets, investments, liabilities
# Note: "balance" is automatic with any product, not a Link product
PLAID_PRODUCTS = os.getenv("PLAID_PRODUCTS", "transactions").split(",")

app = Flask(__name__, static_folder="static")

# Persistent user data for Plaid Link (enables returning user experience)
PLAID_USER_FILE = Path(__file__).parent / ".plaid_user.json"
# Legacy file for backwards compatibility
CLIENT_USER_ID_FILE = Path(__file__).parent / ".plaid_client_user_id"


def get_plaid_user() -> dict:
    """Get or create persistent Plaid user data for returning user experience.

    Returns dict with:
        - client_user_id: Our unique identifier for the user
        - plaid_user_id: Plaid's user_id from /user/create
        - phone_number: Optional phone number for faster verification
    """
    if PLAID_USER_FILE.exists():
        try:
            data = json.loads(PLAID_USER_FILE.read_text())
            if "client_user_id" in data:
                return data
        except (json.JSONDecodeError, KeyError):
            pass

    # Check legacy file for backwards compatibility
    if CLIENT_USER_ID_FILE.exists():
        client_user_id = CLIENT_USER_ID_FILE.read_text().strip()
    else:
        client_user_id = str(uuid.uuid4())

    user_data = {
        "client_user_id": client_user_id,
        "plaid_user_id": None,  # Created via ensure_plaid_user()
        "phone_number": None,  # Set via /api/set_phone_number
    }
    save_plaid_user(user_data)
    return user_data


def ensure_plaid_user() -> str | None:
    """Ensure a Plaid User exists and return the user_id.

    Creates a Plaid User via /user/create if one doesn't exist.
    This user_id can be passed to link_token_create to skip SMS verification.
    """
    user_data = get_plaid_user()

    # Return existing user_id if we have one
    if user_data.get("plaid_user_id"):
        return user_data["plaid_user_id"]

    # Create a new Plaid User
    try:
        req = UserCreateRequest(client_user_id=user_data["client_user_id"])
        response = client.user_create(req)
        plaid_user_id = response["user_id"]

        # Save the user_id
        user_data["plaid_user_id"] = plaid_user_id
        save_plaid_user(user_data)
        print(f"Created Plaid User: {plaid_user_id}")
        return plaid_user_id
    except plaid.ApiException as e:
        error_body = json.loads(e.body) if e.body else {}
        print(f"Warning: Could not create Plaid User: {error_body}")
        return None


def save_plaid_user(user_data: dict):
    """Save Plaid user data with restrictive permissions."""
    PLAID_USER_FILE.touch(mode=0o600, exist_ok=True)
    PLAID_USER_FILE.write_text(json.dumps(user_data, indent=2))


def get_client_user_id() -> str:
    """Get the persistent client user ID (backwards compatible)."""
    return get_plaid_user()["client_user_id"]


# Plaid configuration
PLAID_CLIENT_ID = os.getenv("PLAID_CLIENT_ID")
PLAID_ENV = os.getenv("PLAID_ENV", "production")
# Use sandbox secret if in sandbox mode
if PLAID_ENV == "sandbox":
    PLAID_SECRET = os.getenv("PLAID_SANDBOX_SECRET")
else:
    PLAID_SECRET = os.getenv("PLAID_SECRET")

# Validate required configuration
if not PLAID_CLIENT_ID or not PLAID_SECRET:
    raise ValueError(
        "PLAID_CLIENT_ID and PLAID_SECRET (or PLAID_SANDBOX_SECRET) required"
    )

# Configure Plaid client and storage file per environment
if PLAID_ENV == "sandbox":
    host = plaid.Environment.Sandbox
    ITEMS_FILE = Path(__file__).parent / ".plaid_items_sandbox.json"
elif PLAID_ENV == "development":
    host = plaid.Environment.Development
    ITEMS_FILE = Path(__file__).parent / ".plaid_items.json"
else:
    host = plaid.Environment.Production
    ITEMS_FILE = Path(__file__).parent / ".plaid_items.json"

configuration = plaid.Configuration(
    host=host,
    api_key={
        "clientId": PLAID_CLIENT_ID,
        "secret": PLAID_SECRET,
    },
)
api_client = plaid.ApiClient(configuration)
client = plaid_api.PlaidApi(api_client)


def load_items():
    """Load saved items from file."""
    if ITEMS_FILE.exists():
        content = ITEMS_FILE.read_text().strip()
        if content:
            return json.loads(content)
    return {}


def save_items(items):
    """Save items to file with restrictive permissions."""
    ITEMS_FILE.touch(mode=0o600, exist_ok=True)
    ITEMS_FILE.write_text(json.dumps(items, indent=2))


@app.route("/")
def index():
    """Serve the Link page."""
    return send_from_directory("static", "link.html")


@app.route("/api/set_phone_number", methods=["POST"])
def set_phone_number():
    """Set phone number for returning user experience (reduces 2FA prompts)."""
    try:
        data = request.get_json(silent=True) or {}
        phone = data.get("phone_number")

        if not phone:
            return jsonify({"error": "phone_number is required"}), 400

        # Normalize: strip spaces, ensure +1 prefix for US
        phone = phone.strip().replace(" ", "").replace("-", "")
        if not phone.startswith("+"):
            if phone.startswith("1") and len(phone) == 11:
                phone = "+" + phone
            elif len(phone) == 10:
                phone = "+1" + phone

        user_data = get_plaid_user()
        user_data["phone_number"] = phone
        save_plaid_user(user_data)

        print(f"Set phone for returning user: {phone[:6]}***")
        return jsonify(
            {
                "message": "Phone number saved for returning user experience",
                "phone_last_4": phone[-4:] if len(phone) >= 4 else None,
            }
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/get_user_info", methods=["GET"])
def get_user_info():
    """Get current Plaid user info (phone, client_user_id, plaid_user_id)."""
    user_data = get_plaid_user()
    phone = user_data.get("phone_number")
    plaid_user_id = user_data.get("plaid_user_id")
    return jsonify(
        {
            "client_user_id": user_data.get("client_user_id"),
            "plaid_user_id": plaid_user_id,
            "plaid_user_set": plaid_user_id is not None,
            "phone_set": phone is not None,
            "phone_last_4": phone[-4:] if phone and len(phone) >= 4 else None,
        }
    )


def build_link_user() -> LinkTokenCreateRequestUser:
    """Build LinkTokenCreateRequestUser with phone if available."""
    user_data = get_plaid_user()
    phone = user_data.get("phone_number")

    if phone:
        return LinkTokenCreateRequestUser(
            client_user_id=user_data["client_user_id"],
            phone_number=phone,
        )
    else:
        return LinkTokenCreateRequestUser(
            client_user_id=user_data["client_user_id"],
        )


def build_link_request_base() -> dict:
    """Build base kwargs for LinkTokenCreateRequest."""
    base = {
        "client_name": "Financial Agent",
        "country_codes": [CountryCode("US")],
        "language": "en",
        "user": build_link_user(),
    }

    # Add Plaid user_id if we have one (enables skipping SMS verification)
    plaid_user_id = ensure_plaid_user()
    if plaid_user_id:
        base["user_id"] = plaid_user_id

    return base


@app.route("/api/create_link_token", methods=["POST"])
def create_link_token():
    """Create a Link token to initialize Plaid Link."""
    try:
        # Allow override via request body (handle missing or invalid JSON)
        data = request.get_json(silent=True) or {}
        product_list = data.get("products", PLAID_PRODUCTS)
        if isinstance(product_list, str):
            product_list = [product_list]
        products = [Products(p.strip()) for p in product_list]

        user_data = get_plaid_user()
        has_plaid_user = (
            "with plaid_user_id"
            if user_data.get("plaid_user_id")
            else "no plaid_user_id"
        )
        print(f"Creating link token ({has_plaid_user}): {product_list}")

        base_kwargs = build_link_request_base()
        base_kwargs["products"] = products
        req = LinkTokenCreateRequest(**base_kwargs)
        response = client.link_token_create(req)
        return jsonify(response.to_dict())
    except plaid.ApiException as e:
        return jsonify({"error": json.loads(e.body)}), 400


@app.route("/api/create_update_link_token", methods=["POST"])
def create_update_link_token():
    """Create a Link token for update mode to add products."""
    try:
        data = request.get_json(silent=True) or {}
        item_id = data.get("item_id")
        additional_products = data.get("additional_products", ["investments"])

        if not item_id:
            return jsonify({"error": "item_id is required"}), 400

        # Load items to get access token
        items = load_items()
        if item_id not in items:
            return jsonify({"error": f"Item {item_id} not found"}), 404

        access_token = items[item_id].get("access_token")
        if not access_token:
            return jsonify({"error": "No access token for item"}), 400

        institution_name = items[item_id].get("institution_name", "Unknown")
        user_data = get_plaid_user()
        has_plaid_user = (
            "with plaid_user_id"
            if user_data.get("plaid_user_id")
            else "no plaid_user_id"
        )
        print(f"Update link for {institution_name} ({has_plaid_user})")
        print(f"  Adding: {additional_products}")

        # Convert product strings to Products enum
        products_to_add = [Products(p.strip()) for p in additional_products]

        base_kwargs = build_link_request_base()
        base_kwargs["access_token"] = access_token
        base_kwargs["update"] = LinkTokenCreateRequestUpdate(
            account_selection_enabled=False,
        )
        base_kwargs["additional_consented_products"] = products_to_add
        req = LinkTokenCreateRequest(**base_kwargs)
        response = client.link_token_create(req)
        return jsonify(response.to_dict())
    except plaid.ApiException as e:
        error_body = json.loads(e.body) if e.body else {"error": str(e)}
        print(f"ERROR creating update link token: {error_body}")
        return jsonify({"error": error_body}), 400
    except Exception as e:
        print(f"UNEXPECTED ERROR: {type(e).__name__}: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/exchange_token", methods=["POST"])
def exchange_token():
    """Exchange public token for access token and save item."""
    inst_name = "Unknown"
    try:
        data = request.get_json(silent=True) or {}
        public_token = data.get("public_token")
        institution = data.get("institution", {})
        inst_name = institution.get("name", "Unknown")
        print(f"Exchanging token for: {inst_name}")

        if not public_token:
            print(f"ERROR: No public_token for {inst_name}")
            return jsonify({"error": "public_token is required"}), 400

        req = ItemPublicTokenExchangeRequest(public_token=public_token)
        exchange_response = client.item_public_token_exchange(req)

        access_token = exchange_response["access_token"]
        item_id = exchange_response["item_id"]

        # Get item details
        item_req = ItemGetRequest(access_token=access_token)
        item_response = client.item_get(item_req)
        item = item_response["item"]

        # Save to storage
        items = load_items()
        consent_exp = item.get("consent_expiration_time")
        if consent_exp is not None:
            consent_exp = consent_exp.isoformat()
        items[item_id] = {
            "access_token": access_token,
            "item_id": item_id,
            "institution_id": institution.get("institution_id"),
            "institution_name": institution.get("name"),
            "consent_expiration": consent_exp,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "products": [str(p) for p in item.get("products", [])],
        }
        save_items(items)

        return jsonify(
            {
                "item_id": item_id,
                "institution": institution.get("name"),
                "message": "Item connected successfully",
            }
        )
    except plaid.ApiException as e:
        error_body = json.loads(e.body)
        print(f"ERROR exchanging token for {inst_name}: {error_body}")
        return jsonify({"error": error_body}), 400
    except Exception as e:
        print(f"UNEXPECTED ERROR for {inst_name}: {type(e).__name__}: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/update_item_products", methods=["POST"])
def update_item_products():
    """Refresh item info after update mode to capture new products."""
    try:
        data = request.get_json(silent=True) or {}
        item_id = data.get("item_id")

        if not item_id:
            return jsonify({"error": "item_id is required"}), 400

        items = load_items()
        if item_id not in items:
            return jsonify({"error": f"Item {item_id} not found"}), 404

        access_token = items[item_id].get("access_token")
        institution_name = items[item_id].get("institution_name", "Unknown")

        # Refresh item info from Plaid
        item_req = ItemGetRequest(access_token=access_token)
        item_response = client.item_get(item_req)
        item = item_response["item"]

        # Update stored products
        new_products = [str(p) for p in item.get("products", [])]
        old_products = items[item_id].get("products", [])
        items[item_id]["products"] = new_products

        # Update consent expiration if changed
        consent_exp = item.get("consent_expiration_time")
        if consent_exp is not None:
            items[item_id]["consent_expiration"] = consent_exp.isoformat()

        save_items(items)

        added = set(new_products) - set(old_products)
        print(f"Updated {institution_name}: products now {new_products}")
        if added:
            print(f"  New products added: {added}")

        return jsonify(
            {
                "item_id": item_id,
                "institution": institution_name,
                "products": new_products,
                "added_products": list(added),
            }
        )
    except plaid.ApiException as e:
        error_body = json.loads(e.body) if e.body else {"error": str(e)}
        return jsonify({"error": error_body}), 400
    except Exception as e:
        print(f"UNEXPECTED ERROR: {type(e).__name__}: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/items", methods=["GET"])
def list_items():
    """List all connected items."""
    items = load_items()
    # Return items without exposing access tokens
    safe_items = []
    for item_id, item in items.items():
        safe_items.append(
            {
                "item_id": item_id,
                "institution_name": item.get("institution_name"),
                "institution_id": item.get("institution_id"),
                "consent_expiration": item.get("consent_expiration"),
                "created_at": item.get("created_at"),
                "products": item.get("products"),
            }
        )
    return jsonify(safe_items)


if __name__ == "__main__":
    print(f"Starting Plaid Link server (env: {PLAID_ENV})")
    print("Open http://localhost:8080 to connect accounts")
    debug = os.getenv("FLASK_DEBUG", "").lower() == "true"
    app.run(host="localhost", port=8080, debug=debug)
