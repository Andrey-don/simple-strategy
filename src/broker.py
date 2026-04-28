import os
import uuid
import requests
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("TINKOFF_TOKEN")
SANDBOX = os.getenv("TINKOFF_SANDBOX", "false").lower() == "true"

BASE_URL = (
    "https://sandbox-invest-public-api.tinkoff.ru/rest"
    if SANDBOX
    else "https://invest-public-api.tinkoff.ru/rest"
)

HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Content-Type": "application/json",
}

DIRECTION_BUY  = "ORDER_DIRECTION_BUY"
DIRECTION_SELL = "ORDER_DIRECTION_SELL"
ORDER_MARKET   = "ORDER_TYPE_MARKET"
ORDER_LIMIT    = "ORDER_TYPE_LIMIT"


def _post(path: str, body: dict) -> dict:
    resp = requests.post(f"{BASE_URL}/{path}", json=body, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    return resp.json()


def _float_to_quotation(value: float) -> dict:
    units = int(value)
    nano = round((value - units) * 1_000_000_000)
    return {"units": str(units), "nano": nano}


# ---------------------------------------------------------------------------
# Аккаунт (sandbox)
# ---------------------------------------------------------------------------

def get_accounts() -> list[dict]:
    if SANDBOX:
        data = _post("tinkoff.public.invest.api.contract.v1.SandboxService/GetSandboxAccounts", {})
    else:
        data = _post("tinkoff.public.invest.api.contract.v1.UsersService/GetAccounts", {})
    return data.get("accounts", [])


def open_sandbox_account() -> str:
    data = _post("tinkoff.public.invest.api.contract.v1.SandboxService/OpenSandboxAccount", {})
    return data["accountId"]


def get_or_create_account() -> str:
    accounts = get_accounts()
    if accounts:
        return accounts[0]["id"]
    return open_sandbox_account()


def top_up(account_id: str, amount: float = 1_000_000) -> None:
    """Пополнить sandbox-счёт (только в режиме sandbox)."""
    if not SANDBOX:
        raise RuntimeError("top_up доступен только в sandbox")
    _post("tinkoff.public.invest.api.contract.v1.SandboxService/SandboxPayIn", {
        "accountId": account_id,
        "amount": {"currency": "rub", "units": str(int(amount)), "nano": 0},
    })
    print(f"Счёт пополнен на {amount:.0f} ₽")


# ---------------------------------------------------------------------------
# Ордера
# ---------------------------------------------------------------------------

def place_order(
    figi: str,
    direction: str,
    quantity: int,
    account_id: str,
    price: float | None = None,
) -> dict:
    """
    Выставить рыночный или лимитный ордер.
    direction: DIRECTION_BUY или DIRECTION_SELL
    price: None = рыночный, float = лимитный
    """
    order_id = str(uuid.uuid4())
    body = {
        "figi":        figi,
        "quantity":    quantity,
        "direction":   direction,
        "accountId":   account_id,
        "orderType":   ORDER_LIMIT if price is not None else ORDER_MARKET,
        "orderId":     order_id,
    }
    if price is not None:
        body["price"] = _float_to_quotation(price)

    if SANDBOX:
        return _post("tinkoff.public.invest.api.contract.v1.SandboxService/PostSandboxOrder", body)
    return _post("tinkoff.public.invest.api.contract.v1.OrdersService/PostOrder", body)


def cancel_order(order_id: str, account_id: str) -> None:
    if SANDBOX:
        _post("tinkoff.public.invest.api.contract.v1.SandboxService/CancelSandboxOrder", {
            "accountId": account_id, "orderId": order_id,
        })
    else:
        _post("tinkoff.public.invest.api.contract.v1.OrdersService/CancelOrder", {
            "accountId": account_id, "orderId": order_id,
        })


def get_orders(account_id: str) -> list[dict]:
    if SANDBOX:
        data = _post("tinkoff.public.invest.api.contract.v1.SandboxService/GetSandboxOrders",
                     {"accountId": account_id})
    else:
        data = _post("tinkoff.public.invest.api.contract.v1.OrdersService/GetOrders",
                     {"accountId": account_id})
    return data.get("orders", [])


# ---------------------------------------------------------------------------
# Портфель
# ---------------------------------------------------------------------------

def get_portfolio(account_id: str) -> dict:
    if SANDBOX:
        return _post("tinkoff.public.invest.api.contract.v1.SandboxService/GetSandboxPortfolio",
                     {"accountId": account_id})
    return _post("tinkoff.public.invest.api.contract.v1.OperationsService/GetPortfolio",
                 {"accountId": account_id})


def get_position(account_id: str, figi: str) -> dict | None:
    portfolio = get_portfolio(account_id)
    for pos in portfolio.get("positions", []):
        if pos.get("figi") == figi:
            return pos
    return None


# ---------------------------------------------------------------------------
# Тест
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print(f"Режим: {'sandbox' if SANDBOX else 'prod'}")

    account_id = get_or_create_account()
    print(f"Аккаунт: {account_id}")

    if SANDBOX:
        top_up(account_id, 500_000)

    portfolio = get_portfolio(account_id)
    total = portfolio.get("totalAmountCurrencies", {})
    units = total.get("units", "0")
    print(f"Баланс: {units} ₽")

    # Тест ордера: покупка 1 лота Si по рыночной цене
    figi = "FUTSI0626000"
    print(f"\nТест: рыночный BUY 1 лот {figi}")
    order = place_order(figi, DIRECTION_BUY, 1, account_id)
    print(f"Ордер: {order.get('orderId')}  статус: {order.get('executionReportStatus')}")

    pos = get_position(account_id, figi)
    if pos:
        qty = pos.get("quantity", {}).get("units", "?")
        print(f"Позиция: {qty} лот(ов)")
