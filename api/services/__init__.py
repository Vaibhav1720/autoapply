"""AutoApply services package — per-domain Azure Functions Blueprints.

Each subpackage exposes a single `bp` (azure.functions.Blueprint) that
function_app.py registers on the shared FunctionApp. This is a logical
split — there is still ONE Function App deployed (one host, one cost
unit) — but the code lives next to its domain.
"""
