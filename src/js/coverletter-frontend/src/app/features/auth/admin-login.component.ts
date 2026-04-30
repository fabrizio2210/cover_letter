import { Component } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Router } from '@angular/router';
import { FormsModule } from '@angular/forms';
import { CommonModule } from '@angular/common';
import { AuthService } from '../../core/auth/auth.service';

@Component({
  selector: 'app-admin-login',
  standalone: true,
  imports: [FormsModule, CommonModule],
  template: `
    <form (ngSubmit)="login()">
      <input type="password" [(ngModel)]="password" name="password" placeholder="Admin password" required />
      <button type="submit">Login as Admin</button>
      <div *ngIf="error" style="color:red">{{ error }}</div>
    </form>
  `
})
export class AdminLoginComponent {
  password = '';
  error = '';

  constructor(private http: HttpClient, private router: Router, private authService: AuthService) {}

  login() {
    this.http.post<{ token: string }>('/api/admin/login', { password: this.password })
      .subscribe({
        next: (res) => {
          this.authService.setToken(res.token);
          void this.router.navigate(['/dashboard/settings']);
        },
        error: () => {
          this.error = 'Invalid password';
        }
      });
  }
}
