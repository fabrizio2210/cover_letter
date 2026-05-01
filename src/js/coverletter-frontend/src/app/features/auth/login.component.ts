import { Component } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Router } from '@angular/router';
import { FormsModule } from '@angular/forms';
import { CommonModule } from '@angular/common';
import { AuthService } from '../../core/auth/auth.service';

@Component({
  selector: 'app-login',
  templateUrl: './login.component.html',
  standalone: true,
  imports: [FormsModule, CommonModule]
})
export class LoginComponent {
  username = '';
  password = '';
  error = '';

  constructor(private http: HttpClient, private router: Router, private authService: AuthService) {}

  login() {
    this.http.post<{token: string}>('/api/login', { username: this.username, password: this.password })
      .subscribe({
        next: (res) => {
          this.authService.setToken(res.token);
          this.router.navigate(['/dashboard']);
        },
        error: () => {
          this.error = 'Invalid credentials';
        }
      });
  }
}
