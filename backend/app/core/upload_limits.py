"""Shared limits for the upload request and its file payload."""

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
# Multipart adds a boundary and the small chain_id field around the file.
MAX_UPLOAD_REQUEST_BYTES = MAX_UPLOAD_BYTES + 256 * 1024
