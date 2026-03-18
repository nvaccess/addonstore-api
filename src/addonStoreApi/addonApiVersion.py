# Copyright (C) 2021-2026 NV Access Limited
# This file may be used under the terms of the AGPL3 (GNU Affero General Public License version 3).
# For more details see COPYING.md

from typing import NamedTuple
from addonStoreApi.transformedSubmissions import StoreInfoProvider


class MajorMinorPatch(NamedTuple):
	major: int
	minor: int
	patch: int = 0

	def __str__(self) -> str:
		return f"{self.major}.{self.minor}.{self.patch}"


class SupportedAddonApiVersion:
	def __init__(self, storeInfo: StoreInfoProvider, addonApiVersion: MajorMinorPatch):
		self._ver = str(addonApiVersion)
		self._validate(storeInfo, self._ver)

	def get(self) -> str:
		"""Get the Addon API version string matching the format used in the path of the transformed data."""
		return self._ver

	@staticmethod
	def _validate(storeInfo: StoreInfoProvider, version: str):
		if version not in storeInfo.getAvailableApiVersions():
			raise ValueError("Addon API version not available")
