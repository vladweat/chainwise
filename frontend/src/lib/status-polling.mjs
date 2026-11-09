export const STATUS_POLL_INTERVAL_MS = 2000;
export const MAX_STATUS_RETRY_DELAY_MS = 30000;

export function shouldRetryStatusFailure(statusCode) {
  return (
    statusCode == null ||
    statusCode === 408 ||
    statusCode === 429 ||
    statusCode >= 500
  );
}

export function getStatusRetryDelay(consecutiveFailures) {
  const attempt = Math.max(1, Math.floor(consecutiveFailures));
  return Math.min(
    STATUS_POLL_INTERVAL_MS * 2 ** (attempt - 1),
    MAX_STATUS_RETRY_DELAY_MS,
  );
}
