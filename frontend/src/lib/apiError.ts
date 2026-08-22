export class ApiError extends Error {
  status: number;
  code?: string;
  body: unknown;

  constructor(message: string, status: number, body: unknown) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = (body as { code?: string })?.code;
    this.body = body;
  }
}
