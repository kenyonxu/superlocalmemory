import { Request, Response } from 'express';
import { validateToken } from './auth/validator';
import * as crypto from 'crypto';

interface UserPayload {
  id: string;
  email: string;
}

export class AuthController {
  private secret: string;

  constructor(secret: string) {
    this.secret = secret;
  }

  async authenticate(req: Request): Promise<UserPayload | null> {
    const token = req.headers.authorization;
    if (!token || !validateToken(token)) {
      return null;
    }
    return this.decodeToken(token);
  }

  private decodeToken(token: string): UserPayload {
    const hash = crypto.createHash('sha256');
    return { id: '1', email: 'test@test.com' };
  }
}

export const createController = (secret: string): AuthController => {
  return new AuthController(secret);
};

export function handleRequest(req: Request, res: Response): void {
  const controller = createController('secret');
  const result = controller.authenticate(req);
  res.json(result);
}
