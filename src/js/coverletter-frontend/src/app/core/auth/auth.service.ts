import { Injectable, inject } from '@angular/core';
import { HttpHeaders } from '@angular/common/http';
import { Router } from '@angular/router';

@Injectable({
  providedIn: 'root'
})
export class AuthService {
  private readonly router = inject(Router);
  private readonly tokenStorageKey = 'token';

  getToken(): string | null {
    return localStorage.getItem(this.tokenStorageKey);
  }

  setToken(token: string): void {
    localStorage.setItem(this.tokenStorageKey, token);
  }

  getHeaders(): HttpHeaders {
    const token = this.getToken();
    return token
      ? new HttpHeaders().set('Authorization', `Bearer ${token}`)
      : new HttpHeaders();
  }

  isAuthenticated(): boolean {
    return Boolean(this.getToken());
  }

  logout(redirectToLogin = true): void {
    localStorage.removeItem(this.tokenStorageKey);
    if (redirectToLogin) {
      void this.router.navigate(['/login']);
    }
  }
}