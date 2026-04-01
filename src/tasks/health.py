# Copyright (C) 2025-2026 NV Access Limited
# This file may be used under the terms of the AGPL3 (GNU Affero General Public License version 3).
# For more details see COPYING.md

"""Health check functionality for the addon store."""

import logging
from http import HTTPStatus
from flask import jsonify
from .dataFolder import DataFolder

log = logging.getLogger("addonStore.health")


def check_health():
	"""
	Perform a lightweight readiness check.
	Relies purely on in-memory state to prevent I/O exhaustion DoS.
	"""
	try:
		# If the hash isn't loaded, the app hasn't initialized its git data properly
		if DataFolder._current_hash is None:
			# Log the exact reason internally, but keep the external response generic
			log.warning("Health check failed: Cache hash is not initialized.")
			return jsonify(
				{
					"status": "unhealthy",
				},
			), HTTPStatus.SERVICE_UNAVAILABLE

		# Return minimal, non-sensitive data
		return jsonify(
			{
				"status": "healthy",
				"git_hash": DataFolder._current_hash,
				"update_in_progress": DataFolder.is_updating(),
			},
		), HTTPStatus.OK

	except Exception as e:
		# Log the actual stack trace/error internally
		log.exception(f"Healthcheck failed: {str(e)}")

		# Return a generic, opaque error to the client
		return jsonify(
			{
				"status": "unhealthy",
			},
		), HTTPStatus.SERVICE_UNAVAILABLE
