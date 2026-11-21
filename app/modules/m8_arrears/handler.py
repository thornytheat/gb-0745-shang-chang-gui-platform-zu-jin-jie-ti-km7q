from app.modules.m8_arrears.service import build_ledger, parse_asof
from app.web import store
from flask import Blueprint, jsonify, request

routes = Blueprint("arrears", __name__)


@routes.get("/api/arrears")
def inspect_arrears():
    asof = parse_asof(request.args.get("asof"))
    return jsonify(build_ledger(store().latest_runs(), asof))
