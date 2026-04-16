# Copyright (C) 2021-2026 NV Access Limited
# This file may be used under the terms of the AGPL3 (GNU Affero General Public License version 3).
# For more details see COPYING.md

import typing
import logging
import os
from http import HTTPStatus

from werkzeug.routing import BaseConverter, ValidationError
from addonStoreApi.addonApiVersion import MajorMinorPatch
from tasks.dataFolder import DataFolder
from tasks.health import check_health
from datetime import datetime
import threading
from queue import Queue, Empty
from werkzeug.exceptions import NotFound

from flask import (
	Flask,
	Response,
	request,
	jsonify,
	current_app,
)
from flask_cors import CORS, cross_origin
from prometheus_flask_exporter import PrometheusMetrics
from prometheus_client import Counter
import re

from addonStoreApi.addonApiVersion import SupportedAddonApiVersion
from addonStoreApi.supportedLanguage import SupportedLanguage
from addonStoreApi.addonCollector import (
	FileCollector,
)
from addonStoreApi.transformedSubmissions import (
	StoreInfoProvider,
	Channels,
)
import hmac
import hashlib
import subprocess
from frontend import frontend

""" app.py gets loaded automatically by Flask
Use this to configure routing.
Logic lives in addonStoreApi for testability purposes.
"""


class MajorMinorPatchConverter(BaseConverter):
	regex: str = r"^\d+\.\d+(\.\d+)?$"

	def to_python(self, value: str) -> MajorMinorPatch:
		try:
			return MajorMinorPatch(*map(int, value.split(".")))
		except (TypeError, ValueError):
			raise ValidationError

	def to_url(self, value: MajorMinorPatch) -> str:
		return str(value)


def create_app():
	app = Flask(__name__)
	CORS(app)

	# Initialize metrics queue and worker
	metrics_queue = Queue()
	metrics_worker = None

	def process_metrics_queue():
		"""Worker function to process metrics from the queue."""
		while True:
			try:
				# Get metrics data from queue with a timeout
				try:
					metric_func = metrics_queue.get(timeout=1)
				except Empty:
					continue

				if metric_func is None:  # Shutdown signal
					break

				# Execute the metric function
				metric_func()

			except Exception as e:
				log.warning(f"Error processing metrics: {str(e)}")

	def ensure_metrics_worker():
		"""Ensure the metrics worker thread is running."""
		nonlocal metrics_worker
		if metrics_worker is None or not metrics_worker.is_alive():
			metrics_worker = threading.Thread(target=process_metrics_queue, daemon=True)
			metrics_worker.start()

	def queue_metric(metric_func):
		"""Queue a metric for background processing."""
		try:
			ensure_metrics_worker()
			metrics_queue.put(
				metric_func,
				timeout=0.1,
			)  # Short timeout to prevent blocking
		except Exception as e:
			log.warning(f"Failed to queue metric: {str(e)}")

	# Initialize Prometheus metrics with path excluded from auth
	metrics = PrometheusMetrics(app)
	# metrics.metrics_endpoint = "/metrics"  # Explicitly set metrics path - DISABLED FOR SECURITY
	metrics.excluded_paths = [re.compile("^/healthz$")]

	# Get version from environment, fallback to 'dev' for local development
	app_version = os.getenv("APP_VERSION", "dev")
	metrics.info("app_info", "Addon Store Info", version=app_version)

	# Add default metrics
	metrics.register_default(
		metrics.counter(
			"by_path_counter",
			"Request count by request paths",
			labels={"path": lambda: request.path},
		),
	)

	# Create Prometheus counter directly
	addon_download_counter = Counter(
		"addon_download_total",
		"Number of addon downloads",
		["language", "channels", "api_version"],
	)

	# Add counters to app config for use in routes
	app.config["METRICS_ADDON_DOWNLOAD"] = addon_download_counter
	app.config["QUEUE_METRIC"] = queue_metric

	# Register cleanup on application shutdown
	@app.before_request
	def register_shutdown():
		app.before_request_funcs[None].remove(register_shutdown)
		import atexit

		def cleanup_metrics_worker():
			if metrics_worker is not None and metrics_worker.is_alive():
				metrics_queue.put(None)  # Send shutdown signal
				metrics_worker.join(timeout=5)

		atexit.register(cleanup_metrics_worker)

	# Configure logging
	log_level = logging.DEBUG if app.debug else logging.INFO
	# Remove basicConfig to prevent duplicate logs
	log = logging.getLogger("addonStore")
	log.setLevel(os.environ.get("LOG_LEVEL", "INFO"))
	# Clear any existing handlers
	log.handlers = []

	# Create console handler
	console_handler = logging.StreamHandler()
	console_handler.setLevel(log_level)
	formatter = logging.Formatter(
		"[%(asctime)s] [%(process)d] [%(levelname)s] %(name)s - %(message)s",
	)
	console_handler.setFormatter(formatter)
	log.addHandler(console_handler)

	# Configure Gunicorn logging integration
	if "gunicorn" in os.environ.get("SERVER_SOFTWARE", ""):
		gunicorn_logger = logging.getLogger("gunicorn.error")
		app.logger.handlers = gunicorn_logger.handlers
		app.logger.setLevel(gunicorn_logger.level)

	def get_client_ip():
		"""Get the real client IP from X-Forwarded-For header or fallback to remote_addr."""
		if "X-Forwarded-For" in request.headers:
			# Get the first IP in the chain (original client)
			return request.headers["X-Forwarded-For"].split(",")[0].strip()
		return request.remote_addr

	# Add request logging
	@app.before_request
	def log_request_info():
		# Skip logging only for kubernetes health checks
		if request.path in ["/health", "/healthz"] or request.headers.get(
			"User-Agent",
			"",
		).startswith("kube-probe/"):
			request._skip_logging = True
			return
		request._start_time = datetime.now()

	@app.after_request
	def log_response_info(response):
		# Skip logging for health checks and k8s probes
		if not getattr(request, "_skip_logging", False):
			# Enhanced logging for API endpoints
			user_agent = request.headers.get("User-Agent", "-")

			# Identify client type based on request pattern
			is_nvda = any(
				x in request.path for x in ["/all/", "/stable/", "/beta/"]
			) or request.path.endswith(".json")
			client_type = "NVDA" if is_nvda else "Other"

			log.info(
				'%s - %s - [%s] "%s %s %s" %d %s UA="%s"',
				get_client_ip(),
				client_type,
				datetime.now().strftime("%d/%b/%Y:%H:%M:%S +0000"),
				request.method,
				request.path,
				request.environ.get("SERVER_PROTOCOL", "HTTP/1.1"),
				response.status_code,
				response.content_length or "-",
				user_agent,
			)
		return response

	# Create a separate logger for internal operations
	internal_log = logging.getLogger("addonStore.internal")
	internal_log.setLevel(log_level)
	internal_log.handlers = []
	internal_handler = logging.StreamHandler()
	internal_handler.setFormatter(formatter)
	internal_log.addHandler(internal_handler)

	# Add error logging
	@app.errorhandler(Exception)
	def handle_exception(e):
		if isinstance(e, NotFound):
			# Log 404s at warning level without full traceback
			if not getattr(request, "_skip_logging", False):
				log.warning(f"404 Not Found: {request.method} {request.url}")
			return jsonify(
				{
					"error": "404 Not Found",
					"message": "The requested URL was not found on the server.",
				},
			), 404
		# Log other exceptions at error level with traceback
		if not getattr(request, "_skip_logging", False):
			log.exception("Unhandled exception: %s", str(e))
		return jsonify(
			{
				"error": "Internal Server Error",
				"message": str(e) if app.debug else None,
			},
		), 500

	# Add specific 404 handler
	@app.errorhandler(404)
	def not_found_error(e):
		if not getattr(request, "_skip_logging", False):
			log.warning(f"404 Not Found: {request.method} {request.url}")
		return jsonify(
			{
				"error": "404 Not Found",
				"message": "The requested URL was not found on the server.",
			},
		), 404

	# Initialize data folder and store
	DataFolder.initialize()
	log.info("AddonStore application starting")
	log.info(f"Log level set to: {log.getEffectiveLevel()}")
	log.info(f"Data folder path: {DataFolder.getDataFolderPath()}")
	storeInfo = StoreInfoProvider(DataFolder.getDataFolderPath())

	if app.debug:

		@app.route("/throw", methods=["GET"])
		def throw():
			raise Exception("Raise a python exception")

		## Debugger support - disabled for security reasons
		# try:
		# 	import debugpy
		# except ImportError:
		# 	pass
		# else:

		# 	@app.before_request
		# 	def add_vscode_debugger():
		# 		app.before_request_funcs[None].remove(add_vscode_debugger)
		# 		debugpy.configure(python="/usr/bin/python3")
		# 		debugpy.listen(5678)
		# 		debugpy.wait_for_client()

		# try:
		# 	import pydevd_pycharm
		# except ImportError:
		# 	pass
		# else:

		# 	@app.before_request
		# 	def add_pycharm_debugger():
		# 		app.before_request_funcs[None].remove(add_pycharm_debugger)
		# 		pydevd_pycharm.settrace(
		# 			"127.0.0.1",
		# 			port=5678,
		# 			stdoutToServer=True,
		# 			stderrToServer=True,
		# 		)

	@DataFolder.accessForReading
	def _all(
		includeChannels: typing.List[Channels],
		language: str,
		apiVersion: MajorMinorPatch,
	) -> Response:
		try:
			lang = SupportedLanguage(storeInfo, language)
		except ValueError:
			log.debug(f"Language not supported: {language}")
			lang = SupportedLanguage(storeInfo, "en")

		try:
			addonApiVersion = SupportedAddonApiVersion(storeInfo, apiVersion)
		except ValueError:
			log.debug("API not supported")
			return Response(
				"Addon API version not available",
				status=HTTPStatus.BAD_REQUEST,
			)

		if app.debug:
			debugStr = (
				f"Get {', '.join(c.value for c in includeChannels)} add-ons for,"
				f" lang: {lang.get()},"
				f" api: {addonApiVersion.get()}"
			)
			log.debug(debugStr)

		fc = FileCollector(storeInfo)
		jsonDataGen = fc.concatenateFilesAsJsonArray(
			fc.collectAllFiles(lang, includeChannels, addonApiVersion),
		)
		return Response(
			jsonDataGen,
			status=HTTPStatus.OK,
			mimetype="application/json",
		)

	@DataFolder.accessForReading
	def _latest(
		includeChannels: typing.List[Channels],
		language: str,
	) -> Response:
		try:
			lang = SupportedLanguage(storeInfo, language)
		except ValueError:
			log.debug(f"Language not supported: {language}")
			lang = SupportedLanguage(storeInfo, "en")

		if app.debug:
			debugStr = f"Get {', '.join(c.value for c in includeChannels)} add-ons for, lang: {lang.get()},"
			log.debug(debugStr)

		fc = FileCollector(storeInfo)
		jsonDataGen = fc.concatenateFilesAsJsonArray(
			fc.getLatestFiles(lang, includeChannels),
		)
		return Response(
			jsonDataGen,
			status=HTTPStatus.OK,
			mimetype="application/json",
		)

	@app.route("/<language>/<channels>/<int:major>.<int:minor>.<int:patch>.json")
	@cross_origin()
	def get_all(language: str, channels: str, major: int, minor: int, patch: int):
		# Queue metric increment
		if hasattr(current_app, "config"):
			queue_metric = current_app.config.get("QUEUE_METRIC")
			download_counter = current_app.config.get("METRICS_ADDON_DOWNLOAD")
			if queue_metric and download_counter:
				queue_metric(
					lambda: download_counter.labels(
						language=language or "unknown",
						channels=channels or "unknown",
						api_version=f"{major}.{minor}.{patch}",
					).inc(),
				)

		return _all(
			Channels.parseChannelsFromAppRoute(channels),
			language,
			MajorMinorPatch(major, minor, patch),
		)

	@app.route("/<language>/<channels>/latest.json")
	@cross_origin()
	def get_latest(language: str, channels: str):
		# Queue metric increment
		if hasattr(current_app, "config"):
			queue_metric = current_app.config.get("QUEUE_METRIC")
			download_counter = current_app.config.get("METRICS_ADDON_DOWNLOAD")
			if queue_metric and download_counter:
				queue_metric(
					lambda: download_counter.labels(
						language=language or "unknown",
						channels=channels or "unknown",
						api_version="latest",
					).inc(),
				)

		return _latest(
			Channels.parseChannelsFromAppRoute(channels),
			language,
		)

	@app.route("/cacheHash.json", methods=["GET"])
	def cacheHash():
		"""Return current cache hash without requiring a read lock."""
		if DataFolder._current_hash is None:
			# Try to update the hash if it's not initialized
			DataFolder._updateCacheHash()
		# At this point we should always have a hash due to the fallback in _updateCacheHash
		return Response(
			f'"{DataFolder._current_hash}"',
			status=HTTPStatus.OK,
			mimetype="application/json",
		)

	def verify_github_signature(payload_body, signature_header):
		"""Verify that the webhook is from GitHub using the secret."""
		if not signature_header:
			return False

		# Get secret from environment
		webhook_secret = os.getenv("GITHUB_WEBHOOK_SECRET", "").encode("utf-8")
		if not webhook_secret:
			log.error("GitHub webhook secret not configured")
			return False

		try:
			# Get signature from header
			sha_name, signature = signature_header.split("=")
			if sha_name != "sha256":
				return False

			# Calculate expected signature
			mac = hmac.new(webhook_secret, msg=payload_body, digestmod=hashlib.sha256)
			expected_signature = mac.hexdigest()

			# Compare signatures
			return hmac.compare_digest(signature, expected_signature)
		except Exception as e:
			log.error(f"Error verifying webhook signature: {str(e)}")
			return False

	@app.route("/update", methods=["POST"])
	def update():
		"""GitHub webhook endpoint for updating addonstore-views."""
		# Verify webhook signature
		signature_header = request.headers.get("X-Hub-Signature-256")
		if not verify_github_signature(request.get_data(), signature_header):
			log.warning("Invalid webhook signature")
			return Response("Invalid signature", status=HTTPStatus.FORBIDDEN)

		# Get the event type
		event_type = request.headers.get("X-GitHub-Event")
		if not event_type:
			return Response("Missing event type", status=HTTPStatus.BAD_REQUEST)

		# Handle ping events
		if event_type == "ping":
			return Response("Pong!", status=HTTPStatus.OK)

		# For other events, we need the payload
		if not request.json:
			return Response("Missing JSON payload", status=HTTPStatus.BAD_REQUEST)

		# Only handle push events
		if event_type != "push":
			log.info(f"Skipping unhandled event type: {event_type}")
			return Response(
				f"Event type {event_type} not handled",
				status=HTTPStatus.OK,
			)

		if "ref" not in request.json:
			return Response("Branch reference missing", status=HTTPStatus.BAD_REQUEST)

		# Get the configured branch from environment
		configured_branch = os.getenv("branchRef", "refs/heads/main")
		if request.json["ref"] != configured_branch:
			log.info(
				f"Skipping update for non-target branch: got {request.json['ref']}, configured for {configured_branch}",
			)
			return Response(
				f"Update skipped - branch {request.json['ref']} does not match target branch {configured_branch}",
				status=HTTPStatus.OK,
			)

		def update_repo():
			try:
				# This doesn't appear to be protected by a writing lock
				# Mark update as in progress without locking
				DataFolder._update_in_progress = True

				log.info("Updating repository...")
				repo_path = DataFolder.getDataFolderPath()

				# Configure git for better performance
				subprocess.run(
					[
						"git",
						"config",
						"--global",
						"core.preloadIndex",
						"true",
					],
					cwd=repo_path,
					check=True,
				)
				subprocess.run(
					[
						"git",
						"config",
						"--global",
						"--add",
						"safe.directory",
						repo_path,
					],
					check=True,
				)
				subprocess.run(
					[
						"git",
						"config",
						"--global",
						"advice.detachedHead",
						"false",
					],
					check=True,
				)

				# Optimize git operations for large repos
				log.info("Fetching updates...")
				subprocess.run(
					[
						"git",
						"fetch",
						"--prune",
						"--no-tags",
						"--no-recurse-submodules",
						"origin",
						configured_branch.replace("refs/heads/", ""),
					],
					cwd=repo_path,
					check=True,
				)

				# Get the target hash before we switch
				target_hash = (
					subprocess.check_output(
						[
							"git",
							"rev-parse",
							"--short",
							f"origin/{configured_branch.replace('refs/heads/', '')}",
						],
						cwd=repo_path,
					)
					.decode()
					.strip()
				)

				log.info(f"Switching to {target_hash}...")
				# Use --force to ensure clean switch even with local changes
				subprocess.run(
					[
						"git",
						"checkout",
						"--force",
						"--no-guess",
						target_hash,
					],
					cwd=repo_path,
					check=True,
				)

				# Update the hash after successful switch
				DataFolder._current_hash = target_hash
				log.info(f"Repository updated successfully to {target_hash}")

			except subprocess.CalledProcessError as e:
				log.error(f"Git operation failed: {str(e)}")
			except Exception as e:
				log.error(f"Update failed: {str(e)}")
			finally:
				DataFolder._update_in_progress = False

		# Use gevent's spawn instead of threading
		from gevent import spawn

		spawn(update_repo)

		return Response("Update started", status=HTTPStatus.ACCEPTED)

	@app.route("/healthz")
	def healthcheck():
		"""Health check endpoint that validates system configuration."""
		return check_health()

	@app.route("/health")
	def health():
		"""Basic health check that doesn't require locks."""
		return jsonify({"status": "healthy"})

	app.url_map.converters["MajorMinorPatch"] = MajorMinorPatchConverter
	app.register_blueprint(frontend)

	return app


app = create_app()
