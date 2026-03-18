# Copyright (C) 2021-2026 NV Access Limited
# This file may be used under the terms of the AGPL3 (GNU Affero General Public License version 3).
# For more details see COPYING.md

from enum import IntEnum
from functools import wraps
import logging
import os
import pathlib
import subprocess
from time import time
from typing import Callable, Optional
from portalocker import AlreadyLocked, Lock
from portalocker.constants import LockFlags
import threading
from contextlib import contextmanager

"""
A set of tools which are used to ensure safe access to the addon store data folder.

Before using the data folder, run `DataFolder.initialize`.

Code which performs reads or writes of the data folder,
should be encapsulated in functions decorated with
`DataFolder.accessForReading` or `DataFolder.accessForWriting`.

To get the datafolder path, within a decorated function,
use `DataFolder.getDataFolderPath`.
"""

_lockFolder = os.path.join(os.getenv("TEMP", "/app/tmp"), "locks")
"""
Folder which holds locks used in this module.
"""
_currentReadsFolder = os.path.join(_lockFolder, "concurrentReads")
"""
A folder which keeps track of concurrent reads using the dataFolder.
"""


class _TimeoutSeconds(IntEnum):
	preventNewReadsLock = 1
	"""
	Used when waiting on the preventNewReadsLock.

	This can block the start of read or a write.
	A single write should not block for longer than this.
	Creating a read file should be finished quickly.
	"""
	waitForReadsToFinish = 10
	"""
	Used when waiting for waitForReadsToFinish.
	This only blocks the writer from starting.
	A write can be blocked by the sum of ongoing reads.
	"""


class _ReadTracker:
	preventNewReadsLockPath = os.path.join(_lockFolder, "preventNewReads.lock")

	@staticmethod
	def preventNewReads() -> Lock:
		"""
		Used to prevent new reads from starting,
		so that a write can be performed.

		Has to be acquired by the reader
		to start a read and mark a read as ongoing.
		Has to be held by the writer for an entire write.
		"""
		return Lock(
			_ReadTracker.preventNewReadsLockPath,
			# Must wait for write to finish
			timeout=_TimeoutSeconds.preventNewReadsLock,
			check_interval=0.02,
			flags=LockFlags.EXCLUSIVE,
		)

	@staticmethod
	def _readFileForThread():
		# Ensure a read can be linked one-to-one to a process and thread
		readId = f"{os.getpid()}_{threading.get_ident()}"
		readFilePath = os.path.join(_currentReadsFolder, readId)
		return pathlib.Path(readFilePath)

	@staticmethod
	def createReadFileForThread() -> pathlib.Path:
		"""A potentially blocking call which attempts to start a read.
		This function can be blocked by _ReadTracker.preventNewReads().
		"""
		readFile = _ReadTracker._readFileForThread()
		with _ReadTracker.preventNewReads():
			# Ensure that this read is tracked uniquely with exist_ok=False
			# Due to DataFolder.accessForReading supporting re-entrance,
			# we need to be able to track the total number of reads that are started,
			# to count when they are finished.
			# As such, a thread cannot share a read file.
			readFile.touch(exist_ok=False)
		return readFile

	@staticmethod
	def threadIsInRead() -> bool:
		"""Checks if the thread is currently performing a read."""
		return _ReadTracker._readFileForThread().exists()

	@staticmethod
	def waitForReadsToFinish(
		sleepInterval: float = 0.01,
		timeout: float = _TimeoutSeconds.waitForReadsToFinish,
	):
		"""Without holding `_ReadTracker.preventNewReads()` this function may timeout."""
		from gevent import sleep as gevent_sleep  # Use gevent's sleep

		startTime = time()
		while time() - startTime < timeout:
			if len(os.listdir(_currentReadsFolder)) == 0:
				return
			gevent_sleep(sleepInterval)  # Allow other greenlets to run
		raise TimeoutError("Waiting for reads timed out")


class DataFolder:
	"""
	Uses a Reader-Writer locking algorithm to handle
	reading and writing to the data folder.
	"""

	defaultDataFolderPath = "/app/addon-datastore"
	_current_hash: str | None = None  # Cache for the commit hash
	_update_in_progress = False  # Track if an update is in progress
	log = logging.getLogger("addonStore.dataFolder")

	@staticmethod
	def is_updating() -> bool:
		"""Check if an update is in progress."""
		return DataFolder._update_in_progress

	@staticmethod
	def initialize():
		DataFolder.log.info("Initializing DataFolder")
		pathlib.Path(_lockFolder).mkdir(exist_ok=True)
		pathlib.Path(_currentReadsFolder).mkdir(exist_ok=True)
		if len(os.listdir(_currentReadsFolder)):
			DataFolder.log.warning(
				f"Current reads folder {_currentReadsFolder} is not empty. "
				f"This indicates reads are still ongoing during initialization. "
				f"Directory contents: {os.listdir(_currentReadsFolder)}",
			)
			for leftoverReadFile in os.listdir(_currentReadsFolder):
				os.remove(os.path.join(_currentReadsFolder, leftoverReadFile))

		# Check for interrupted updates and cleanup
		try:
			repo_path = DataFolder.getDataFolderPath()
			if os.path.exists(os.path.join(repo_path, ".git", "index.lock")):
				DataFolder.log.warning("Found stale git index lock, cleaning up")
				os.remove(os.path.join(repo_path, ".git", "index.lock"))
		except Exception as e:
			DataFolder.log.error(f"Error cleaning up git locks: {str(e)}")

		# Configure git for safe directory access
		DataFolder.log.info("Configuring git safe directory")
		subprocess.check_call(
			args=[
				"git",
				"config",
				"--global",
				"--add",
				"safe.directory",
				DataFolder.getDataFolderPath(),
			],
			cwd=DataFolder.getDataFolderPath(),
		)

		# Initialize the cache hash
		DataFolder._updateCacheHash()

		DataFolder.log.info("DataFolder initialization complete")

	@staticmethod
	def _updateCacheHash():
		"""Internal method to update the cached commit hash."""
		try:
			DataFolder.log.debug("Updating cache hash")
			with DataFolder.writing_access():
				shaBytes = subprocess.check_output(
					args=["git", "log", "--pretty=format:%h", "-n", "1"],
					cwd=DataFolder.getDataFolderPath(),
				)
				DataFolder._current_hash = shaBytes.decode()
				DataFolder.log.debug(
					f"Cache hash updated to: {DataFolder._current_hash}",
				)
		except subprocess.CalledProcessError as e:
			DataFolder.log.error(f"Failed to update cache hash: {str(e)}")
			# If we fail to get the hash but had a previous hash, keep using it
			if DataFolder._current_hash is None:
				# If we've never had a hash, use a fallback
				DataFolder._current_hash = "initial"

	@staticmethod
	def getDataFolderPath() -> str:
		return os.environ.get("dataViewsFolder", DataFolder.defaultDataFolderPath)

	@staticmethod
	@contextmanager
	def writing_access():
		"""Context manager for write access to the data folder."""
		if _ReadTracker.threadIsInRead():
			raise AlreadyLocked(
				"A write cannot be started while this thread is within a read. The thread will deadlock.",
			)
		DataFolder._update_in_progress = True
		try:
			# Prevent any new reads from starting, so we can claim the data folder
			with _ReadTracker.preventNewReads():
				# Wait for concurrent reads to end
				_ReadTracker.waitForReadsToFinish()
				try:
					yield
				finally:
					pass  # Cleanup will be handled by context exit
		finally:
			DataFolder._update_in_progress = False

	@staticmethod
	def accessForWriting(f: Callable):
		"""
		Claims ownership of the data folder for writing within
		a decorated function.
		As write actions cannot occur during other writes or reads,
		a function decorated with this function cannot call
		or be called from another function decorated with
		`accessForWriting` or `accessForReading`.

		@raises: `portalocker.AlreadyLocked` when unable to prevent new reads.
		@raises: `TimeoutError` when reads take too long to complete.

		Example usage:
		```
		@DataFolder.accessForWriting
		def writeData(folder: str):
			open(f"{folder}/foo.txt", "w") as fileInFolder:
				fileInFolder.write("bar")
			# perform more actions using folder

		try:
			writeData(folder)
		except (portalocker.AlreadyLocked, TimeoutError):
			print("Writer lock failed to be acquired")
		```
		"""

		@wraps(f)
		def wrapper(*args, **kwargs):
			if _ReadTracker.threadIsInRead():
				raise AlreadyLocked(
					"A write cannot be started while this thread is within a read."
					" The thread will deadlock."
					" This situation would indicate that the there is a 'reader' further down the stack,"
					" which won't delete the read file until the stack unwinds, but this call to 'writer' will"
					" block on '_ReadTracker.waitForReadsToFinish()'.",
				)
			# Prevent any new reads from starting, so we can claim the data folder
			with _ReadTracker.preventNewReads():
				# Wait for concurrent reads to end
				_ReadTracker.waitForReadsToFinish()
				# Perform the write
				returnData = f(*args, **kwargs)
			return returnData

		return wrapper

	@staticmethod
	def accessForReading(f: Callable):
		"""
		Ensures there are no ongoing writes before allowing a read within a
		decorated function.
		As concurrent and parallel reading is supported, a function decorated with `accessForReading`
		can call or be called from another function decorated with `accessForReading`.
		This ensures a single read function (eg listing files) can be protected
		at the same time as a broader read function (eg listing files, reading each of the files),
		keeping data consistent due to writes being blocked.

		@raises: `portalocker.AlreadyLocked` if starting the read fails,
		due to waiting for a write to finish timing out.

		Example:
		```
		@DataFolder.accessForReading
		def readData(folder: str):
			open(f"{folder}/foo.txt") as fileInFolder:
				fileInFolder.read()
			# perform more actions using folder

		try:
			readData(folder)
		except portalocker.AlreadyLocked:
			print("Read failed to start")
		```
		"""

		@wraps(f)
		def wrapper(*args, **kwargs):
			# If this thread is already performing a read,
			# there is no need to create a new read file.
			readFile: Optional[pathlib.Path] = None
			if not _ReadTracker.threadIsInRead():
				# Ensure the writer is aware a read is ongoing
				# This call can be blocked by the writer
				readFile = _ReadTracker.createReadFileForThread()

			# Perform the read
			try:
				returnData = f(*args, **kwargs)
			finally:
				if readFile is not None:
					# Confirm the read has completed
					readFile.unlink()

			return returnData

		return wrapper
