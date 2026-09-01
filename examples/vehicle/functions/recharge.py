"""Vehicle.recharge - 给车辆充值。

The function only knows ``ctx``; it never sees the store, SQLite, MCP or HTTP.
"""


def execute(ctx):
    vehicle = ctx.object()
    amount = ctx.args["amount"]

    balance = vehicle.get("balance")
    new_balance = balance + amount

    ctx.set(vehicle.ref, "balance", new_balance)

    return {"success": True, "balance": new_balance}
