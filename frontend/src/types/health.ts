export interface HealthResponse {
  status: string;
  environment: string;
  postgres: string;
  redis: string;
  qdrant: string;
  slither: string;
}
