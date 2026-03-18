# Copyright (C) 2021-2026 NV Access Limited
# This file may be used under the terms of the AGPL3 (GNU Affero General Public License version 3).
# For more details see COPYING.md

from addonStoreApi.transformedSubmissions import StoreInfoProvider


class SupportedLanguage:
	def __init__(self, storeInfo: StoreInfoProvider, lang: str):
		try:
			self._validate(storeInfo, lang)
		except ValueError:
			langWithoutLocale = lang.split("_")[0]
			if langWithoutLocale != lang:
				lang = langWithoutLocale
			self._validate(storeInfo, lang)

		self._lang = lang

	@staticmethod
	def _validate(storeInfo: StoreInfoProvider, lang: str):
		"""Raise ValueError if language not supported."""
		if lang not in storeInfo.getAvailableLanguages():
			raise ValueError(f"Language not supported {lang}")

	def get(self) -> str:
		"""Get the language string matching the format used in the path of the transformed data."""
		return self._lang
