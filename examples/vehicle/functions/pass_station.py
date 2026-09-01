"""Vehicle.pass_station - 车辆通过收费站。

Demonstrates multiple mutations in one atomic invocation:
  1. deduct the fee from balance        (set_property)
  2. link Vehicle --passes--> station   (link)

If anything fails (unknown station, insufficient balance), nothing is applied.
"""


def execute(ctx):
    vehicle = ctx.object()
    station_id = ctx.args["station"]
    fee = ctx.args["fee"]

    station_ref = ctx.ref("TollStation", station_id)
    station = ctx.get(station_ref)  # ObjectNotFound if the station does not exist

    if station.get("status") != "open":
        ctx.fail(f"station {station_id} is not open")

    balance = vehicle.get("balance")
    if balance < fee:
        ctx.fail(f"insufficient balance: {balance} < {fee}")

    new_balance = balance - fee
    ctx.set(vehicle.ref, "balance", new_balance)
    ctx.link(vehicle.ref, "passes", station_ref)

    return {
        "success": True,
        "station": station.get("name"),
        "fee": fee,
        "balance": new_balance,
    }
