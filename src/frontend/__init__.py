# Copyright (C) 2025-2026 NV Access Limited
# This file may be used under the terms of the AGPL3 (GNU Affero General Public License version 3).
# For more details see COPYING.md

from dataclasses import dataclass
import dataclasses
from enum import Enum
from functools import cached_property, lru_cache
import json
import logging
import os
from typing import Any, Literal
from flask import Blueprint, render_template, request

from addonStoreApi.addonCollector import FileCollector
from addonStoreApi.supportedLanguage import SupportedLanguage

from addonStoreApi.addonApiVersion import MajorMinorPatch, SupportedAddonApiVersion
from addonStoreApi.transformedSubmissions import Channels, StoreInfoProvider
from tasks.dataFolder import DataFolder
from babel.dates import format_date
from babel import Locale
from datetime import date

# Don't use getenv or environ.get,
# as we want to fail if $COPYRIGHT_YEARS is not set
COPYRIGHT_YEARS = os.environ["COPYRIGHT_YEARS"]

log = logging.getLogger("addonStore")

SEARCH_SIMILARITY_THRESHOLD = 0.3  # Minimum similarity threshold (30% trigram match)


class SearchFields(str, Enum):
	"""Fields from add-on metadata that are searchable via the search functionality."""

	DISPLAY_NAME = "displayName"
	PUBLISHER = "publisher"
	DESCRIPTION = "description"
	ADDON_ID = "addonId"


class SortBy(str, Enum):
	"""Fields from add-on metadata that are sortable via the sort functionality."""

	DISPLAY_NAME = "displayName"
	VERSION = "version"
	PUBLISHER = "publisher"


@dataclass(frozen=True)
class Query:
	channel: Channels | Literal["all"]
	language: str
	apiVersion: MajorMinorPatch
	includeIncompatible: bool
	addonId: str | None = None
	searchQuery: str | None = None
	sortBy: SortBy = SortBy.DISPLAY_NAME
	sortReverse: bool = False

	def __post_init__(self):
		self._validateLanguage(self.language)
		self._validateApiVersion(self.apiVersion)

	@staticmethod
	@lru_cache(maxsize=2048)
	def _generateTrigrams(text: str) -> frozenset[str]:
		"""Generate character trigrams from text."""
		normalized = text.casefold().strip()
		# Pad with spaces to capture start/end trigrams
		normalized = "  " + normalized + "  "
		trigrams = set()
		for i in range(len(normalized) - 2):
			trigrams.add(normalized[i : i + 3])
		return frozenset(trigrams)

	@staticmethod
	def _calculateTrigramSimilarity(
		searchTrigrams: frozenset[str],
		textTrigrams: frozenset[str],
	) -> float:
		"""Calculate similarity score between two sets of trigrams."""
		if not searchTrigrams:
			return 1.0  # Empty search matches everything
		matches = len(searchTrigrams & textTrigrams)
		return matches / len(searchTrigrams)

	@staticmethod
	def _getSearchableText(addon: dict[str, Any]) -> str:
		"""Extract searchable text from addon."""
		parts = [addon.get(searchField.value, "") for searchField in SearchFields]
		return " ".join(parts).casefold()

	def forAddon(self, addonId: str) -> "Query":
		return dataclasses.replace(self, addonId=addonId)

	@classmethod
	def fromDict(cls, vals: dict[str, Any]) -> "Query":
		return cls(
			channel=cls._getChannelFromDict(vals),
			language=cls._getLanguageFromDict(vals),
			apiVersion=cls._getApiVersionFromDict(vals),
			includeIncompatible=cls._getIncludeIncompatibleFromDict(vals),
			addonId=cls._getAddonIdFromDict(vals),
			searchQuery=cls._getSearchQueryFromDict(vals),
		)

	def asdict(self) -> dict[str, str]:
		d = {
			"channel": self.channel.value if isinstance(self.channel, Channels) else self.channel,
			"language": self.language,
			"apiVersion": str(self.apiVersion),
		}
		if self.includeIncompatible:
			d["includeIncompatible"] = "on"
		if self.addonId is not None:
			d["addonId"] = self.addonId
		if self.searchQuery:
			d["searchQuery"] = self.searchQuery
		return d

	def _getSortValue(self, addon: dict[str, Any]) -> Any:
		return addon.get(self.sortBy.value, "").casefold()

	@cached_property
	def matchingAddons(self) -> list[dict[str, Any]]:
		fc = FileCollector(storeInfo)
		if self.includeIncompatible:
			files = fc.getLatestFiles(
				lang=SupportedLanguage(storeInfo, self.language),
				channels=Channels.parseChannelsFromAppRoute(self.channel),
			)
		else:
			files = fc.collectAllFiles(
				lang=SupportedLanguage(storeInfo, self.language),
				channels=Channels.parseChannelsFromAppRoute(self.channel),
				apiVer=SupportedAddonApiVersion(storeInfo, self.apiVersion),
			)
		addonList = json.loads(b"".join(fc.concatenateFilesAsJsonArray(files)))

		# Apply trigram search filtering and sorting
		if self.searchQuery:
			searchTrigrams = self._generateTrigrams(self.searchQuery)

			# Calculate similarity scores
			addonsMatchingSearch = []
			for addon in addonList:
				searchableText = self._getSearchableText(addon)
				textTrigrams = self._generateTrigrams(searchableText)
				similarity = self._calculateTrigramSimilarity(
					searchTrigrams,
					textTrigrams,
				)
				addon["searchRank"] = similarity

				if similarity >= SEARCH_SIMILARITY_THRESHOLD:
					addonsMatchingSearch.append(addon)

			# Sort by similarity (highest first).
			# Note, smarter behaviour may be required when sorting by other fields while searching is supported.
			addonsMatchingSearch.sort(key=lambda x: x["searchRank"], reverse=True)
			return addonsMatchingSearch
		else:
			# No search term;
			# just return all add-ons sorted by the specified field
			return list(
				sorted(
					addonList,
					key=self._getSortValue,
					reverse=self.sortReverse,
				),
			)

	@cached_property
	def selectedAddon(self) -> dict[str, Any] | None:
		if self.addonId is not None:
			try:
				return next(
					filter(
						lambda addon: addon["addonId"] == self.addonId,
						self.matchingAddons,
					),
				)
			except StopIteration:
				raise ValueError(f"Add-on {self.addonId!r} does not exist.")
		log.info("No add-on selected.")
		return None

	@staticmethod
	def _getChannelFromDict(values: dict[str, Any]) -> Channels | Literal["all"]:
		if "channel" in values:
			channel = values["channel"]
			if channel == "all":
				return channel
			return Channels(channel)
		else:
			return Channels.STABLE

	@staticmethod
	def _getLanguageFromDict(values: dict[str, Any]) -> str:
		return values.get("language", "en")

	@staticmethod
	def _validateLanguage(language: str) -> None:
		availableLanguages = storeInfo.getAvailableLanguages()
		if language not in availableLanguages:
			raise ValueError(
				f"Unsupported language {language} not in {availableLanguages}.",
			)

	@staticmethod
	def _getApiVersionFromDict(values: dict[str, Any]) -> MajorMinorPatch:
		if "apiVersion" in values:
			return strToMMP(values["apiVersion"])
		else:
			return storeInfo.getLatestStableRelease()

	@staticmethod
	def _validateApiVersion(apiVersion: MajorMinorPatch) -> None:
		if str(apiVersion) not in storeInfo.getAvailableApiVersions():
			raise ValueError(f"Nonexistent API version {apiVersion}.")

	@staticmethod
	def _getIncludeIncompatibleFromDict(values: dict[str, Any]) -> bool:
		return values.get("includeIncompatible") == "on"

	@staticmethod
	def _getAddonIdFromDict(values: dict[str, Any]) -> str | None:
		return values.get("addonId")

	@staticmethod
	def _getSearchQueryFromDict(values: dict[str, Any]) -> str:
		searchQuery = values.get("searchQuery", "")
		return searchQuery.strip().casefold()[:100]


storeInfo = StoreInfoProvider(DataFolder.getDataFolderPath())

frontend = Blueprint(
	"frontend",
	__name__,
	template_folder="templates",
	static_folder="static",
	# Blueprint static directories cannot share the global static directory, so override it.
	static_url_path="/frontend",
)


@frontend.context_processor
def injectGlobalConstants() -> dict[str, Any]:
	return {
		"COPYRIGHT_YEARS": COPYRIGHT_YEARS,
	}


def strToMMP(string: str) -> MajorMinorPatch:
	try:
		return MajorMinorPatch(*map(int, string.split(".")))
	except (AttributeError, ValueError, TypeError):
		raise ValueError(f"Invalid version: {string!r}")


@frontend.route("/")
def index():
	query = Query.fromDict(request.args)
	return render_template(
		"index.html",
		languages=storeInfo.getAvailableLanguages(),
		apiVersions=storeInfo.getRichApiVersions(),
		channels=(*(channel.value for channel in Channels.__members__.values()), "all"),
		query=query,
	)


@frontend.app_template_filter("stringifyMMP")
def stringifyMajorMinorPatch(mmp):
	return str(MajorMinorPatch(**mmp))


@frontend.app_template_filter("humanizeMilliTimestamp")
def humanizeMilliTimestamp(millis: int) -> str:
	return format_date(date.fromtimestamp(millis // 1000), locale="en")


@frontend.app_template_filter("isoformatMilliTimestamp")
def isoformatMilliTimestamp(millis: int) -> str:
	return date.fromtimestamp(millis // 1000).isoformat()


@frontend.app_template_filter("getLocaleDisplayName")
def getLocaleDisplayName(lang: str, inLang: str | None = None) -> str:
	return Locale.parse(lang).get_display_name(inLang) or lang
