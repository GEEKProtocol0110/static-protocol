"""Run against `python -m static_protocol serve` using simulated assets only."""

import getpass
import json
import secrets
import urllib.request


def main():
    token = getpass.getpass("Paste the local simulator operator token: ")
    prefix = "walk-" + secrets.token_hex(4)

    def call(path, body=None):
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request("http://127.0.0.1:8787" + path, data=data,
                                         headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.load(response)

    state = call("/v1/state")
    daa = state["daa"] + 1
    buyer, seller = prefix + "-buyer", prefix + "-seller"
    call("/v1/funding", dict(owner=buyer, asset="USD_TEST", amount=1000000))
    call("/v1/funding", dict(owner=seller, asset="STATIC_TEST", amount=10))
    call("/v1/orders", dict(owner=buyer, nonce="b", side="buy", quantity=10, limit_price=110, expiry_daa=daa + 100, fee_rail="native"))
    call("/v1/orders", dict(owner=seller, nonce="s", side="sell", quantity=10, limit_price=100, expiry_daa=daa + 100))
    receipt = call("/v1/sweeps", dict(request_id=prefix, daa=daa))
    print(json.dumps({"mode": "simulation", "receipt": receipt, "audit": call("/v1/audit")}, indent=2))


if __name__ == "__main__":
    main()
