# Copyright (C) 2021-2026 NV Access Limited
# This file may be used under the terms of the AGPL3 (GNU Affero General Public License version 3).
# For more details see COPYING.md

"""Module to abstract the details for the layout for the transformed submissions"""

import enum
from tasks.dataFolder import DataFolder
import glob
import json
import logging
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
	from addonStoreApi.addonApiVersion import MajorMinorPatch


log = logging.getLogger("addonStore.internal")


class Channels(str, enum.Enum):
	STABLE = "stable"
	BETA = "beta"
	DEV = "dev"

	@staticmethod
	def parseChannelsFromAppRoute(channelStr: str) -> list["Channels"]:
		if channelStr == "all":
			return [x for x in Channels]
		return [Channels(channelStr)]


class StoreInfoProvider:
	def __init__(self, transformedDataPath: str):
		if not os.path.exists(transformedDataPath):
			raise ValueError(f"Path does not exist: {transformedDataPath}")
		self.storeFolder = transformedDataPath

	@DataFolder.accessForReading
	def getAvailableLanguages(self) -> list[str]:
		languagesPath = os.path.join(self.storeFolder, "views", "*")
		log.debug(f"Get languages available from: {languagesPath}")
		availableLangPaths = glob.glob(languagesPath)
		languages = [os.path.basename(f) for f in availableLangPaths if os.path.isdir(f)]
		log.debug(languages)
		return languages

	def getAvailableApiVersions(self) -> list[str]:
		return self._jsonBasedGetAvailableApiVersions()

	def getAvailableParsedApiVersions(self) -> list["MajorMinorPatch"]:
		from addonStoreApi.addonApiVersion import MajorMinorPatch

		return [MajorMinorPatch(*ver.split(".")) for ver in self._jsonBasedGetAvailableApiVersions()]

	@DataFolder.accessForReading
	def _jsonBasedGetAvailableApiVersions(self) -> list[str]:
		"""Return API versions listed in the nvdaAPIVersions.json file contained in the transformed submission store:
		https://github.com/nvaccess/addon-datastore/blob/views/nvdaAPIVersions.json
		JSON example:
		[
			{
				"apiVer": {
					"major": 0,
					"minor": 0,
					"patch": 0
				}
			}
		]
		"""
		filename = "nvdaAPIVersions.json"
		versionsT = list[
			dict[
				str,  # required keys: apiVer
				dict[
					str,  # required keys: major, minor, patch
					int,
				],
			]
		]
		try:
			versionsPath = os.path.join(self.storeFolder, filename)
			log.debug(f"Get Api versions available from: {versionsPath}")
			with open(versionsPath) as f:
				versions: versionsT = json.load(f)
		except OSError:
			log.error(f"Unable to open {filename}", exc_info=True)
			raise
		apiVersions = [
			f"{verEntry['apiVer']['major']}.{verEntry['apiVer']['minor']}.{verEntry['apiVer']['patch']}"
			for verEntry in versions
		]
		log.debug(apiVersions)
		return apiVersions

	def createPathToJson(
		self,
		lang: str,
		apiVer: str,
		addonId: str,
		channel: Channels,
	) -> str:
		"""Simple substitution for each part."""
		return os.path.join(
			self.storeFolder,
			"views",
			lang,
			apiVer,
			addonId,
			f"{channel.value}.json",
		)

	@DataFolder.accessForReading
	def getLatestStableRelease(self) -> "MajorMinorPatch":
		from addonStoreApi.addonApiVersion import MajorMinorPatch

		filename = "nvdaAPIVersions.json"
		versionsPath = os.path.join(self.storeFolder, filename)
		try:
			log.info(f"Get API versions available from: {versionsPath}")
			with open(versionsPath) as f:
				versions = json.load(f)
		except OSError:
			log.error(f"Unable to open {filename}", exc_info=True)
			raise
		for version in filter(
			lambda version: not version.get("experimental", False),
			reversed(versions),
		):
			return MajorMinorPatch(**version["apiVer"])
		return MajorMinorPatch(0, 0, 0)

	@DataFolder.accessForReading
	def getRichApiVersions(self) -> list[dict[str, str]]:
		"""Return API versions listed in the nvdaAPIVersions.json file contained in the transformed submission store:
		https://github.com/nvaccess/addon-datastore/blob/views/nvdaAPIVersions.json
		JSON example:
		[
			{
				"apiVer": {
					"major": 0,
					"minor": 0,
					"patch": 0
				}
			}
		]
		"""
		filename = "nvdaAPIVersions.json"
		try:
			versionsPath = os.path.join(self.storeFolder, filename)
			log.info(f"Get API versions available from: {versionsPath}")
			with open(versionsPath) as f:
				versions = json.load(f)
		except OSError:
			log.error(f"Unable to open {filename}", exc_info=True)
			raise
		apiVersions = [
			{
				"description": version["description"],
				"apiVer": f"{version['apiVer']['major']}."
				f"{version['apiVer']['minor']}."
				f"{version['apiVer']['patch']}",
				"experimental": version.get("experimental", False),
			}
			for version in versions
		]
		log.debug(apiVersions)
		return apiVersions
