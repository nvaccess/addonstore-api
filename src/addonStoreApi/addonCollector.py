# Copyright (C) 2021-2026 NV Access Limited
# This file may be used under the terms of the AGPL3 (GNU Affero General Public License version 3).
# For more details see COPYING.md

from collections.abc import Generator, Iterable
from functools import lru_cache
import json
import re
from glob import glob

from tasks.dataFolder import DataFolder

from .supportedLanguage import SupportedLanguage
from .addonApiVersion import SupportedAddonApiVersion
from .transformedSubmissions import (
	StoreInfoProvider,
	Channels,
)


def _validateChannels(channels: list[Channels]) -> None:
	""":raise ValueError for invalid (none or duplicate) channels"""
	if not channels:
		raise ValueError("No channels specified")
	if len(set(channels)) != len(channels):
		raise ValueError("Duplicate channels specified")


class FileCollector:
	def __init__(self, storeInfo: StoreInfoProvider):
		self._storeInfo = storeInfo

	@DataFolder.accessForReading
	def collectAllFiles(
		self,
		lang: SupportedLanguage,
		channels: list[Channels],
		apiVer: SupportedAddonApiVersion,
	) -> Generator[str, None, None]:
		"""Generate file paths for all addon releases filtered by the supplied arguments.
		Example output: [
		"/path/to/transformedData/en/2021.3/myAddonId1/stable.json",
		"/path/to/transformedData/en/2021.3/myAddonId1/beta.json",
		"/path/to/transformedData/en/2021.3/myAddonId2/stable.json",
		]
		"""
		_validateChannels(channels)
		for channel in channels:
			# get a path like 'transformedData/en/2021.3/*/stable.json'
			# Collect all addons (for this lang, version, channel), so use a wildCard for addonId
			anyAddon = "*"
			path = self._storeInfo.createPathToJson(
				lang.get(),
				apiVer.get(),
				anyAddon,
				channel,
			)
			yield from glob(path)

	@DataFolder.accessForReading
	def _getLatestForChannel(
		self,
		lang: SupportedLanguage,
		channel: Channels,
	) -> dict[str, str]:
		"""
		Get the latest version of all available addons, given a language and channel.

		:return: a dict of (addonID, path) key pairs, with the latest path.
		"""
		addonIDRe = re.compile(r".*/([^/]+)/" + channel + r".json$")
		anyAddonID = "*"
		latestAddonPaths: dict[str, str] = dict()

		path = self._storeInfo.createPathToJson(
			lang.get(),
			"latest",
			anyAddonID,
			channel,
		)
		for addonPath in glob(path):
			addonIDMatch = re.match(addonIDRe, addonPath)
			addonID = addonIDMatch.groups()[0]
			latestAddonPaths[addonID] = addonPath

		return latestAddonPaths

	@DataFolder.accessForReading
	def getLatestFiles(
		self,
		lang: SupportedLanguage,
		channels: list[Channels],
	) -> Generator[str, None, None]:
		"""
		Get the latest version of all available addons, given a language and channel.

		:return: a generator for the latest add-on path for a given language and channel.
		"""
		_validateChannels(channels)
		for channel in channels:
			addons = self._getLatestForChannel(lang, channel)
			for addonID in addons:
				yield addons[addonID]

	@staticmethod
	@lru_cache(maxsize=1000)
	def fetchAddonUrl(addonPath: str, _dayOfTheYear: str) -> str:
		"""
		@param addonPath: addonPath to read from (e.g. /en/latest/exampleAddon/beta.json)
		@param _dayOfTheYear: a str in the format 2022364,
		which is used to freshen the cache.
		"""
		with open(addonPath, mode="rb") as f:
			addonData = json.load(f)
		return addonData["URL"]

	@DataFolder.accessForReading
	def concatenateFilesAsJsonArray(self, files: Iterable[str]) -> Generator[bytes, None, None]:
		"""Join (concatenate) file contents together within an array.
		The file contents is assumed to be json data (but is not validated).
		Example output:
		[{"description": "I am a JSON object from one file"}, {"whatIsThis": "Another file contents"}]
		"""
		yield b"["
		separator = b""  # Only for subsequent addons should there be a separator
		for file in files:
			yield separator
			with open(file, mode="rb") as f:
				yield from f
			separator = b","  # subsequent addons should be prefixed by a comma
		yield b"]"
