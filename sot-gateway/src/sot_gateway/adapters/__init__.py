"""Adapter-Implementierungen pro Backend (austauschbar je Kunde, ADR 0002)."""

from sot_gateway.adapters.infoblox import InfobloxAdapter
from sot_gateway.adapters.netbox import NetBoxAdapter

__all__ = ["InfobloxAdapter", "NetBoxAdapter"]
