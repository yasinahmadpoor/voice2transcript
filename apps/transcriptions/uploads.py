"""Bound upload disk usage while Django parses multipart data."""

import os

from django.core.files.uploadhandler import TemporaryFileUploadHandler

from common.http import APIError


class LimitedTemporaryUploadHandler(TemporaryFileUploadHandler):
    def receive_data_chunk(self, raw_data, start):
        if start + len(raw_data) > int(os.getenv("MAX_UPLOAD_MB", "500")) * 1024 * 1024:
            self.file.close()
            raise APIError(413, "File exceeds the upload size limit.")
        return super().receive_data_chunk(raw_data, start)
