# Copyright (C) 2025-2026 NV Access Limited
# This file may be used under the terms of the AGPL3 (GNU Affero General Public License version 3).
# For more details see COPYING.md

"""Health check functionality for the addon store."""

import os
import logging
from http import HTTPStatus
from flask import jsonify
from .dataFolder import DataFolder

log = logging.getLogger("addonStore.health")


def check_health():
	"""Perform health checks and return status."""
	try:
		# Check data folder exists and is accessible - no lock needed
		data_folder = DataFolder.getDataFolderPath()
		if not os.path.exists(data_folder):
			return jsonify(
				{
					"status": "unhealthy",
					"error": "Data folder not found",
				},
			), HTTPStatus.SERVICE_UNAVAILABLE

		# Basic git check without requiring lock
		if not os.path.exists(os.path.join(data_folder, ".git")):
			return jsonify(
				{
					"status": "unhealthy",
					"error": "Git repository not initialized",
				},
			), HTTPStatus.SERVICE_UNAVAILABLE

		if DataFolder._current_hash is None:
			return jsonify(
				{
					"status": "unhealthy",
					"error": "Cache hash not initialized",
				},
			), HTTPStatus.SERVICE_UNAVAILABLE

		return jsonify(
			{
				"status": "healthy",
				"git_hash": DataFolder._current_hash,
				"data_folder": data_folder,
				"update_in_progress": DataFolder.is_updating(),
			},
		), HTTPStatus.OK

	except Exception as e:
		log.error(f"Health check failed: {str(e)}")
		return jsonify(
			{
				"status": "unhealthy",
				"error": str(e),
			},
		), HTTPStatus.SERVICE_UNAVAILABLE
