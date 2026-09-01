"""TollStation.set_status - 设置收费站状态。"""


def execute(ctx):
    station = ctx.object()
    status = ctx.args["status"]

    ctx.set(station.ref, "status", status)

    return {"success": True, "station": station.get("name"), "status": status}
